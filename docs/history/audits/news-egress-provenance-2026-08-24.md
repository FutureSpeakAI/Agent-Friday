# Why voice can search the news but can't read it back — 2026-08-24

Investigation only. Nothing changed in this pass; the proposal at the end is
for Stephen to rule on.

## Short version

Stephen is right, and the codebase already agrees with him — it just doesn't
apply its own principle on this path.

The gate is judging **news article text** and finding it private, because news
is wall-to-wall people, places, deaths, illnesses and money. A provenance
mechanism that solves exactly this already exists and is already used for the
weekly Edition digest. The `search_news` *tool* doesn't participate in it, and
a second, structural bug makes the failure total rather than partial.

## 1 · What is actually happening (measured, not inferred)

The 125-character reply is `_redact_placeholder(Tier.PRIVATE)` from
`egress_gate`. It is **not** a blanket rule on tool results. It is the
sensitivity classifier judging article content.

Classifier output on realistic news text:

| news sample | tier | outcome |
|---|---|---|
| politics / trade summit | 1 | passes |
| sports result | 1 | passes |
| local obituary | **2** | replaced with placeholder |
| CDC flu guidance | **3** | **dropped entirely** |
| tech release notes | 1 | passes |

Public-health reporting from the CDC classifying Tier 3 is the sharpest
illustration: the medical keyword rules exist to keep *Stephen's* health
affairs on the machine, and a CDC press release is not that.

Reproduced end-to-end: a two-item `search_news` JSON result of 636 characters
went in, the 125-character `TIER_2 withheld` marker came out — the same
signature as the voice session's 1,672 → 125.

## 2 · Three compounding causes

**(a) The provenance registry is scoped to the Edition digest, not the tool.**
`news_engine` registers article titles as public at ingest — but only from the
*edition-building* function, and it registers the exact line shape the digest
renders (`"- title (source)"`). `search_news` uses a different path and returns
JSON. Nothing it emits was ever registered.

**(b) Snippets are never registered at all, anywhere.** Only titles are. So
even a perfectly matched paragraph split would exempt the headline and withhold
the article body — which is the part with the information in it.

**(c) JSON tool results are all-or-nothing.** `_gate_text` has a span-wise
rescue: for multi-paragraph text it withholds only the offending paragraphs.
`json.dumps` emits **one line with no separators**, so the rescue never
engages. One blob, one classification, everything replaced.

(c) generalises well beyond news: **every tool that returns JSON is gated
all-or-nothing today.** One incidental phrase nukes the entire result. That is
a bug in its own right and worth fixing regardless of how the news question is
settled.

## 3 · Provenance already exists — and it's better than a tag

`egress_gate.register_public_text(text, origin)` (§5.7). Its docstring
describes this precise failure mode: *"9 of 120 public news headlines classify
TIER_3 … Those are the legal and financial keyword rules doing their job on the
wrong material."*

Its contract is already what Stephen described, and stricter:

- provenance is established **at ingest**, by the code that fetched the bytes;
- **exact match only** — interpolating user content produces a different string
  which gates normally;
- **there is no send-time API.** Nothing accepts "treat this as news" from a
  caller. The exemption cannot be claimed by asserting it.

That last property matters for the whitelist-vs-tag question below: this is a
**content-addressed** origin tag, not a label travelling beside the data. You
cannot mislabel a payload into exemption — you would have to get your text into
a feed Friday subscribes to first, and even then only that exact text is
exempt.

Second existing user: `web_fetch._register_spans` does the same for fetched
pages, behind an SSRF guard applied to every redirect hop.

## 4 · Origin inventory (67 first-party tools)

The safety argument lives or dies here, so the middle column is the one to
scrutinise.

**Public-origin — retrieved from the open web, never Stephen's:**
`search_news`, `search_web`, `browse_web`.

These fetch through guarded paths and carry **no user credentials**, so they
cannot pull an authenticated page. That is what makes "public by construction"
true rather than hopeful.

**Private-origin — must stay gated, no change proposed:**
`search_email`, `draft_email`, `query_calendar`, `find_calendar_events`,
`create_calendar_event`, `update_calendar_event`, `annotate_calendar_events`,
`search_contacts`, `read_file`, `write_file`, `open_path`, `save_output`,
`read_doc`, `search_drive`, `read_wiki`, `search_wiki`, `correct_wiki`,
`propose_wiki_update`, `knowledge_query`, `knowledge_related`,
`knowledge_communities`, `query_trust_graph`, `get_career_pipeline`,
`screenshot`, `write_clipboard`, `list_tasks`, `list_workspace_history`,
`workflow_status`, `personality_show`, `epistemic_score`, `run_command`.

**Mixed — and this is the answer to "say so if they mix":**

`get_briefing` **does mix.** It returns a composed daily briefing document
containing news headlines *and* calendar, mail and task content in one blob.

This does not complicate the fix, it clarifies the rule: **a composite inherits
the most restrictive origin of its parts.** The briefing stays private-origin
and stays gated. Public news inside a private document does not make the
document public. Any future composite must default to private.

`navigate` / `open_url` drive the real browser rather than a guarded fetch, so
they are not in the public set — they can reach an authenticated session.
`inspect_image` / `inspect_audio` read local files. Both private-origin.

## 5 · Proposal

Two independent pieces. The first is worth doing on its own merits.

**Piece 1 — gate JSON tool results field-wise.** Parse a JSON tool result,
gate each string value, re-serialise. Restores the span-wise rescue that prose
already gets, so one tainted field stops destroying the whole result. Benefits
every JSON-returning tool, and needs no provenance decision.

**Piece 2 — extend the existing registry to the news fetch path.** Register
title **and snippet** in `news_engine._fetch_news_items`, which is the shared
ingest point where the RSS/Brave results actually arrive. Same contract as
today: ingest-side, exact-match, no send-time API. With Piece 1 in place, the
per-field text is what the registry sees, so matching works.

### Arguing the whitelist question, as asked

**A name whitelist at the gate (`if tool_name in PUBLIC_TOOLS: skip`) is
genuinely worse here, and not mainly because names drift.**

It would require the gate to trust a tool name handed to it alongside the
payload — a **send-time assertion of exemption**, which is the one thing the
existing design explicitly refuses. Today an attacker must get text into a real
feed. Under a whitelist they would only have to get a payload labelled
`search_news`. That converts a content-addressed property into a claim, and
claims can be forged by any code path that assembles a payload.

It also can't express `get_briefing`, where the same tool returns public and
private material together.

So: Stephen's instinct is right, and the mechanism he's reaching for is already
built. The work is making the tool-result path participate in it — not
inventing a parallel one, and not adding a whitelist.

### Explicitly not proposed

No weakening of anything private-origin. No PII library. Enum values unchanged
(see the companion decision memo).

### Honest caveat

The registry is bounded at 20,000 entries and snippets are capped at 2,000
characters. Heavy news use could evict entries, and an evicted snippet simply
gates normally again — recall degrades, safety does not. Worth watching rather
than solving up front.

---

# IMPLEMENTED — 2026-08-24

Both pieces landed. Stephen's "news is not private" plus the CDC-flu-at-Tier-3
finding was taken as authorization.

## Credential precondition — re-verified before relying on it

The public-origin set is the entire safety argument, so it was re-checked
rather than assumed. None of the three can reach an authenticated page:

| path | headers sent | verdict |
|---|---|---|
| RSS (`_rss_results`) | `User-Agent` only | no credentials |
| Brave (`_brave_results`) | `Accept`, `X-Subscription-Token` | Friday's own service key, not Stephen's identity |
| article body fetch | `User-Agent` only | no credentials |
| `web_fetch` (browse_web) | none | zero credential references; SSRF-guarded every hop |

No cookies, no OAuth, no `google_token`, no netrc, anywhere in these modules.

## Piece 1 — JSON descent, placed in `_gate_text` rather than the call site

`_gate_text` became a thin wrapper: run the span-wise gate (now
`_gate_text_span`), and if it withheld something from a JSON payload, descend
field-wise instead of surrendering the whole result.

**Why the wrapper and not the tool-result call site.** The callers that need
this do not share one. The voice leg gates its tool results by calling
`_gate_text` directly (`routes/voice.py:1764`), text chat arrives via
`_gate_tool_result`, workers via `gate_worker_payload`. Fixing the wrapper
fixes all three and — importantly — touched no file another session was in.
`voice.py` had a live edit seven lines from the call site; routing the fix
through the gate avoided that collision entirely.

Properties: keys are never gated (structure, not content); non-strings pass
through; `_gate_json_value` calls the span gate directly so there is no
reentrancy to reason about; a payload that passes whole still costs exactly one
classification, so the common path is not slowed.

## Piece 2 — title AND snippet registered at the fetch point

`news_engine._register_news_provenance()` wraps all three return paths of
`_fetch_news_items` (live, offline-archive, and empty-fetch-fallback). Same
contract as the digest's `_pub()` and `web_fetch._register_spans`: ingest-side,
exact-match, no send-time API.

The new part is the **snippet**. Registering titles alone exempted the headline
and withheld the body — the actual reported symptom.

## Verified end to end

The real `_tool_search_news` output through the real voice gating line:

- **before:** 636-character result → 125-character `TIER_2 withheld` marker
- **after:** 958-character result → 1,206 characters, valid JSON, all hits intact

Health (CDC), legal (court hearing) and financial ($136M round) reporting all
survive. A `search_email` control on the same path is still withheld.

## Still withheld, correctly

Vault, email, calendar and nested private content in JSON results; private
fields sitting *beside* public news in the same object (each field is judged on
its own — one public sibling does not rescue the object); `get_briefing`-shaped
composites keep their private half. A payload that merely *labels* itself
`search_news` earns nothing, and registration is exact-match, so appending
private text to a registered headline does not smuggle it out.

Test evidence: `tests/unit/test_egress_tool_result_provenance.py`. Seven tests
fail against pre-change code. The private-origin tests passed before and after —
which is the point — so `test_private_cases_are_falsifiable` neutralises the
classifier and asserts the vault text *does* leak, proving those tests can
detect a broken gate rather than asking anyone to take it on trust.

Regression: 423 passed. The only failures are the two classifier-tuning tests
left pending for Stephen, plus a pre-existing NeMo/GPU test unrelated to this
work (confirmed failing with these changes stashed).

## One adjacent gap found, pinned but NOT closed here

While checking "what is still withheld", one string diverged: `"todo: call the
accountant about the account balance"` is withheld as bare text but travels
inside `{"f": ...}`.

**It is pre-existing.** The pre-change gate leaks it identically
(`_gate_text_span` returns it unchanged), so JSON descent neither caused nor
cured it. The cause is in the classifier: JSON punctuation breaks the
possessive/personal frame matching that b69acb2 introduced, so wrapping text in
JSON *lowers* its tier — TIER_2 bare, TIER_1 wrapped.

**Why it was not closed here.** It lives in `sensitivity_classifier.py`, which
a concurrent session is actively fixing for exactly this family of bug (they
added a `vault=` mode after b69acb2 un-gated vault contacts). Closing it from
the egress side would mean abandoning the whole-value fast path and always
descending: measured **12 ms → 1,013 ms** per tool result. Taking an 85x
latency hit inside a voice turn to paper over a classifier bug is the wrong
repair at the wrong layer.

Pinned as an `xfail` in the test file so it is tracked and will flip to passing
when the classifier is fixed at the root.
