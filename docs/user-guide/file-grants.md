# File Grants — letting Friday use your documents without giving up the gate

*Added in 5.6.0. Implementation: `services/file_grants.py`, endpoints in
`routes/control.py`.*

---

## The problem this solves

Friday's egress gate is fail-closed: anything it cannot confidently classify as
public is withheld from cloud models. That is the right default, and it has an
unpleasant consequence — Friday is **least useful on exactly the work you most
want help with**. Ask her to read your own résumé and reason about it with a
frontier model and she will decline, because your résumé is full of the contact
details the gate exists to stop.

The realistic thing a person does next is worse than the thing the gate
prevented: they paste the same text into the chat box by hand. The content
crosses the wire anyway, with no registry, no audit trail, and no receipt.

**A grant inside the system beats a bypass outside it.** So file grants are a
way to say "yes, this specific document, on purpose" — and to have that decision
recorded, scoped, expiring, and revocable.

## The design, in five rules

### 1. File grants are content-pinned

Granting a file records a **SHA-256 of its contents at grant time**. If the file
later changes, the next read does not match the pin, the grant is reported
`stale`, and the content gates normally. A grant is permission for *the document
you looked at*, not for a filename that someone or something can later refill.

Because of that pin, file grants may be permanent. There is nothing to expire —
the pin already limits them.

### 2. Folder and glob grants must expire, within 30 days

A folder or a glob cannot be content-pinned: the whole point is that it covers
files that do not exist yet. So breadth is traded against time. An expiry is
**required** for these, and the 30-day ceiling is enforced in
`file_grants.py` — not merely in the UI, so an API caller cannot exceed it
either.

### 3. Deny always beats grant, at any specificity

`check_grant()` checks deny marks **before** it ever looks at a grant. A deny on
`~/Documents/tax` is not overridden by a later, narrower grant on a file inside
it. There is no precedence puzzle to reason about and no ordering that produces
a surprise: if anything denies it, it is denied.

### 4. Only you can grant. No model, on any surface, ever

There is no grant tool in `CLAUDE_TOOLS`. Not in chat, not in voice, not in
background tasks. Grants are created only through authenticated HTTP endpoints
driven by UI chrome, and the consent dialog renders findings from the
classifier's own scan of the file — never from model-supplied text. So:

- a **prompt-injected document cannot widen its own reach**, because the model
  has no call path to a grant;
- it also **cannot script its own consent screen**, because the dialog's
  contents do not come from the file's text;
- a **spoken "yes" cannot create a grant.** Voice can only tell you a pending
  chip appeared; approving it happens in the UI.

### 5. A corrupted ledger can only ever tighten

Grants live in an append-only JSONL ledger at
`~/.friday/privacy/file_grants.jsonl`, deliberately **separate from
`settings.json`** so that the factory-reset/BOM failure mode that once wiped 83
settings keys cannot touch it. Every line carries an HMAC over its event, keyed
by the app's own secret key.

A line that fails to parse or fails its HMAC is dropped and counted. Then:

- a dropped **grant** fails safe on its own — one fewer permission, normal
  gating;
- a dropped **deny** is the dangerous direction, because losing a prohibition
  silently widens what may be sent.

Rather than reason about which kind was lost, **any drop at all puts the entire
ledger into "suspenders mode":** every grant is treated as absent, every deny
mark that folded cleanly is still enforced, and a high-priority notification is
raised. Tampering with the ledger — or corrupting it by accident — can remove
permissions. It can never add one.

## How a grant actually takes effect

There is no send-time exemption flag anywhere; nothing accepts "please allow
this call". Instead a grant re-uses the span registry the news pipeline already
uses:

`on_file_read()` is called by `read_file` / `search_files` **at the moment a
file is genuinely read**. If the resolved path carries a live grant, that read's
exact paragraphs are registered as sendable, exactly as a fetched news article
is registered.

The consequence is the property worth having: **content becomes sendable only
because the real file at the granted path was really read, just now.** A model
cannot register spans, so it cannot talk its way into an exemption for text it
merely claims came from a granted file.

## Endpoints

All four paths sit under `/api/privacy/` and all require authentication.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/privacy/file-grants` | Grants, deny marks, pending re-approvals, and ledger status |
| `GET` | `/api/privacy/file-grants/scan?path=…` | Classifier findings for a candidate file — what the consent dialog shows *before* the button. Read-only; creates nothing |
| `POST` | `/api/privacy/file-grants` | Create a grant. `{path, scope: file\|folder\|glob, expiry_days}` — `expiry_days` required for folder/glob, max 30 |
| `POST` | `/api/privacy/deny-marks` | Create a deny mark. `{path, scope}` |
| `POST` | `/api/privacy/file-grants/<id>/revoke` | Revoke a grant **or** a deny mark — same action, either registry |

## Never-send material

Some content is marked never-send regardless of grants. When a scanned file
contains such matches, the consent dialog must display them and the caller must
acknowledge each one explicitly (`ack_never_send_matches`) before
`never_send_override` is honoured — and the override is **available on file
grants only**, never on a folder or glob, where you cannot have seen what you
were agreeing to.

## Auditing your own grants

The ledger is plain JSONL on your own disk, so the most direct audit needs no
API access at all:

```bash
# every grant and deny decision ever made, newest last
cat ~/.friday/privacy/file_grants.jsonl
```

For the current *effective* state — including whether the ledger has dropped
into suspenders mode — use `GET /api/privacy/file-grants` on the running server
(`http://localhost:3000` by default). Note that it is `@login_required` like the
rest of the control API, so a bare `curl` will get a 401; call it from the UI, or
with the session the app itself uses.

The ledger is append-only, so revoking does not erase history — it appends a
revocation. What Friday was allowed to send, and when you allowed it, remains
answerable after the fact.
