# Gmail search ignores the user's query — improvement spec

Status: implemented (88d3bba9, "the offline-cache search matches whole words;
the live search is Gmail's"). Kept as the record of the diagnosis.
Scope check: root cause is confined to `src/agent_friday/services/agent.py`,
`src/agent_friday/services/google_accounts.py`, and (for the legacy fallback)
`src/agent_friday/services/calendar_engine.py`. None of these fall under the
sensitive-subsystem list in AGENTS.md (privacy/, governance/, egress_gate.py,
sensitivity_classifier.py, credential_store.py, vault_passphrase.py), so this
is in scope for an autonomous patch once approved.

## 1. The bug, plainly

`search_email("test")` (and any other query) does not search Gmail for that
term. It fetches whatever is unread or recent — a fixed 7-day unread/recent
window per account — and then runs a crude substring check against those
~15 messages per account. Two visible symptoms:

- Messages that plainly contain "test" but fall outside the unread/7-day
  window are never returned, no matter the query.
- Messages that do *not* contain "test" as a word are returned anyway,
  because the substring check matches inside other words — "latest,"
  "testimonial," "greatest," "protest," "fastest," "contest," etc. A a retailer
  newsletter with "latest deals" in the subject line satisfied a search for
  "test."

Net effect: the tool's query argument is cosmetic. Results are effectively
"recent unread mail, loosely fuzzed," not a search.

## 2. Confirmed root cause

**`src/agent_friday/services/agent.py`, `_tool_search_email()`, ~line 1267.**
The user's query is parsed (`q = ((inp or {}).get('query') or '').strip()`)
but is never passed into the Gmail fetch call:

```python
result = ga.merged_gmail(limit_per_account=15)   # q not forwarded
```

It only resurfaces afterward as a local post-filter:

```python
ql = q.lower()
for c in cards:
    blob = " ".join(str(c.get(k) or "") for k in ("sender", "subject", "snippet")).lower()
    if not ql or ql in blob:   # raw substring, no word boundary
        hits.append(...)
```

**`src/agent_friday/services/google_accounts.py`.** Neither function in the
call chain accepts a query parameter at all:

- `merged_gmail(limit_per_account: int = 15, days: int | None = None) -> dict`
  — no `query`/`q` argument.
- `_gmail_for_creds(creds, limit: int, days: int | None = None) -> list` —
  no `query`/`q` argument. Internally it hardcodes the Gmail API query
  string itself:

```python
window = "newer_than:%dd" % _gmail_window_days(days)
for q in (f"is:unread {window}", window):
    resp = svc.users().messages().list(userId="me", q=q, maxResults=limit).execute()
```

That `q` is a local loop variable for a hardcoded unread/recency window —
it has no connection to the user's search term. It is structurally
impossible, as written, for a caller's query string to reach
`messages().list(q=...)`.

**`src/agent_friday/services/calendar_engine.py`, `_fetch_gmail_recent()`
(legacy single-account fallback).** Same pattern, independently duplicated:

```python
for q in ("is:unread newer_than:1d", "newer_than:1d"):
    resp = svc.users().messages().list(userId="me", q=q, maxResults=limit).execute()
```

Also takes no query argument, also ignores anything the caller wanted to
search for.

Two stacked defects, confirmed by reading the code (no speculation):

1. The user's query never reaches the Gmail API — every "search" is
   actually a fetch of a fixed unread/recent window.
2. The post-fetch filter that does look at the query text is a raw
   substring match with no word boundaries, applied to a very small,
   already-irrelevant candidate set (≤15 messages/account, unread-or-7-day
   only).

## 3. Proposed fix approach (prose)

- Thread a `query: str | None` parameter from `_tool_search_email()` down
  through `merged_gmail()` into `_gmail_for_creds()`, and use it to build
  the real Gmail API query string passed to `messages().list(q=...)`.
  Gmail already supports rich query syntax (`from:`, `subject:`, quoted
  phrases, `newer_than:`, boolean operators) — let the API do the
  matching instead of re-implementing a weaker version locally.
- When a query is supplied, do not silently AND it with the hardcoded
  `is:unread newer_than:Nd` window. Decide and document the new default:
  the simplest correct behavior is that a query searches the account's
  mail per Gmail's own defaults (which already biases toward recent/
  relevant mail), and the unread/recency window becomes an *additional*,
  opt-in filter — not an invisible one baked into every search. If no
  query is supplied, keep today's unread/recent-window behavior as the
  "what's new" default, since that's a different, legitimate use case
  (`query_calendar`/inbox-glance callers may rely on it).
  - Same threading is needed in `_fetch_gmail_recent()` (legacy path) if
    it remains reachable, or that path should be confirmed dead code and
    removed instead of fixed twice.
- Drop the local post-fetch substring re-filter for the live-API path
  entirely, since Gmail's own `q=` search already did the matching. Keep
  a word-boundary-safe filter (`re.search(r'\b' + re.escape(ql) + r'\b', blob, re.IGNORECASE)`)
  only as a fallback for any no-live-API / cached-results code path where
  there is no live Gmail query to lean on.
- No changes needed to privacy/governance/egress/credential code — this is
  purely a data-plumbing and query-construction bug inside the Gmail
  fetch path.

## 4. Files that would need to change

- `src/agent_friday/services/agent.py` — `_tool_search_email()`: pass the
  parsed `query` through to `merged_gmail()`; remove or fix the post-fetch
  substring filter as described above.
- `src/agent_friday/services/google_accounts.py` — `merged_gmail()` and
  `_gmail_for_creds()`: add a `query: str | None` parameter and use it to
  construct the Gmail API `q=` string instead of (or alongside) the
  hardcoded unread/window string.
- `src/agent_friday/services/calendar_engine.py` — `_fetch_gmail_recent()`:
  either add the same query threading, or confirm it's unreachable/legacy
  and remove it so the bug isn't fixed in one path and left in the other.
- Corresponding unit tests (see below), likely under `tests/unit/` and/or
  `tests/api/` wherever the existing Gmail-fetch tests live — grep for
  `merged_gmail` / `_gmail_for_creds` / `_tool_search_email` to find them.

## 5. Test plan

**Test that currently FAILS (documents the bug):**

Name (suggested): `test_search_email_passes_query_to_gmail_api`

Setup: mock the Gmail API client (`svc.users().messages().list`) used by
`_gmail_for_creds()` so the test can capture the `q=` argument it was
called with. Call `_tool_search_email({"query": "invoice"})` (or the
equivalent public entry point) through `merged_gmail()`.

Assertion (fails today): the mock's recorded `q=` argument contains the
string `"invoice"`.

Today this fails because `merged_gmail()` never accepts or forwards a
query — the mock will only ever see the hardcoded `is:unread newer_than:7d`
/ `newer_than:7d` strings, never the word "invoice." After the fix, the
query threads through and the assertion passes.

**Second test (documents defect 2 — word-boundary matching), currently
FAILS:**

Name (suggested): `test_search_email_substring_filter_respects_word_boundaries`

Setup: construct a fake message list containing one card whose subject is
`"Your latest deals from <a retailer>"` and no card containing the standalone
word `"test"`. Call the search path with query `"test"`.

Assertion (fails today): zero hits are returned for query `"test"` against
this fixture.

Today this fails because `"test" in "your latest deals from wework".lower()`
is `True` (substring of "latest"), so the newsletter card is incorrectly
returned as a hit. After the fix — Gmail-native search plus a word-boundary
fallback filter — the same fixture returns no hits, matching the assertion.

Both tests should be added to whatever suite already exercises
`google_accounts.py` / `agent.py` email tooling, and both must be run
before-and-after per AGENTS.md: failing on current `main`, passing once the
fix lands.
