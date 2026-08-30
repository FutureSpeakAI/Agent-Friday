# Google Housekeeper — Tasks, Calendar and Gmail write access

**Status:** spec, awaiting Stephen's answers to Q-H1..Q-H6. **HK-1 and HK-4 are LANDED** (commit `342df97`, a parallel session, 2026-08-26) — see the reconciliation notes inside those work orders; do not rebuild them. HK-2/HK-3/HK-5 can start immediately; HK-7/HK-8 need answers.
**Date:** 2026-08-26 (amended same day after `342df97` landed; re-checked 2026-08-29 against v5.7.0)
**Re-check 2026-08-29:** the blocking fact in §0 is **re-verified a third time** — both connected accounts still hold only `tasks.readonly` and only `gmail.readonly`, so nothing writes yet and the re-consent is still outstanding. **Q-H1..Q-H6 remain open and unanswered**; nothing in this document has been decided on Stephen's behalf.
**Written by:** Fable (spec) for a Sonnet build session
**Prompted by:** Google Tasks can be read but never completed, so the to-do list only grows. Stephen then asked for the full role: add/remove/edit events and to-dos; compose, send and delete email — "my Google accounts' housekeeper."

This spec was written from a four-way code audit (Tasks/Calendar paths, Gmail paths, OAuth scopes, governance/receipts). Every claim below carries a file:line. Where the audit contradicted the request's assumptions, the code won.

---

## 0. The plain answer on the task bug — now FIXED in code, blocked on re-consent

Confirmed, and it was deeper than a missing tool: the only Tasks code was two `.list()` calls and the only scope ever requested was `tasks.readonly`, so even a hand-written write would 403. Completed tasks are also *invisible* to Friday (`showCompleted=False`), which is why the list only ever grew from her side.

**A parallel session shipped the fix as commit `342df97`** while this spec was being written: `complete_task`, `create_task`, `update_task`, `delete_task` against Tasks API v1, with `account_id` + `tasklist_id` required on every write (no fan-out, no inference), `list_tasks` now returning each task's `tasklist_id`, `tasks.readonly` replaced by the read-write `tasks` scope in `GOOGLE_MULTI_SCOPES`, `delete_task` in `_ALWAYS_CONFIRM`, and tests on both the service and tool layers. HK-1/HK-4 below are marked landed with a short list of deltas from this spec's design.

> **⛔ NOTHING WRITES UNTIL RE-CONSENT — verified twice, independently.** Stephen believed the connector had already been reconnected with read-write scope. It has not: two sessions checked `~/.friday/google_accounts/accounts.json` from different directions on 2026-08-26 and both found **every connected account holds only `tasks.readonly`**. The code is merged and correct, and every task write will return a per-account 403 until each account is re-consented — once per account (Personal, Work) — via the **multi-account `+ Add Account` flow** (Settings → Connectors → Google → Add Account, the button wired to `/api/google/accounts/connect`). Re-adding the same address is idempotent and upgrades the grant in place. Do **not** use the Connectors "Reconnect" button or `friday_google_connect.py` — those request fewer scopes and silently downgrade (§1). A server restart is also required before the reconnect so the new scope constant is what the consent flow requests.

**Do the re-consent once, not three times:** since it is now unavoidable for Tasks, the Gmail write scopes (HK-6/HK-9) should be added to `GOOGLE_MULTI_SCOPES` *before* Stephen reconnects, so one consent event covers Tasks + Gmail together (Calendar already has write scope and needs nothing). See §2 and Q-H2.

---

## 1. What the code actually says (facts the spec is built on)

**Calendar writes already exist.** `create_calendar_event`, `update_calendar_event`, `annotate_calendar_events` are live tools at Ring 2 (`agent.py:3506-3508`, `agent.py:3837-3839`), backed by `calendar_write.py`. There is no delete. Both connected accounts already hold the full `auth/calendar` read-write scope (`google_accounts.py:59,66`), so **all calendar work in this spec needs zero re-consent**.

**But calendar writes are silently primary-account-only.** `calendar_write._service()` (`calendar_write.py:86-98`) resolves to `primary_credentials()` → `accounts[0]`, always `calendarId="primary"`. Worse, the preflight and the executor check *different accounts*: `write_ready()` (`calendar_write.py:56-59`) passes if *any* account holds a write scope, then `_service()` executes against the *first* account. Since account 0 is Personal, every calendar write Friday has ever made landed on the Personal calendar regardless of intent. This is exactly the wrong-account hazard the housekeeper design must kill, and it is live today (HK-2).

**Gmail has zero write capability, and two decoys that pretend otherwise.**
- The registered `draft_email` tool is a prose stub: it never reads its `body` argument and returns a string telling the model drafting isn't available (`agent.py:2061-2072`) — while the system prompt still advertises it (`model_router.py:1984,2017`).
- The `gmail_draft` flow destination writes a JSON file to `~/.friday/flow-queue/` and its "confirm" endpoint flips a `status` field in that file (`misc_engine.py:371-387`, `routes/workflows.py:211-232`). No Gmail API call exists on that path. Nothing is ever sent or drafted.
- The retired gmail-mcp server (`enabled: false` since 2026-08-13) *declares* `send_email`/`modify_email` tools whose dispatcher has no case for them — they return "Unknown tool" (gmail-mcp-multi `src/tools/index.ts:74,125` vs the switch at `:162-259`). Do not resurrect it; build native.

**Friday cannot read an email body.** The only Gmail tool, `search_email`, returns headers + snippet (`format="metadata"`, `google_accounts.py:501-503`). The one full-body read is an HTTP route with no tool wrapper (`routes/messages.py:153-165`) — and it authenticates as the primary account only, so opening a Work message 404s or falls back to cache silently (`routes/messages.py:188-190`). Any "reply to this email" capability built today would draft from a 200-char snippet — fabrication by construction. Body read (HK-5) is therefore a hard prerequisite of compose/reply, not a nice-to-have.

**Ring 2 is not a safety gate for sends.** Ring 2 means "session is authenticated" (`agent.py:4929-4938`) — the same bar as `search_web`. Two additional facts make the current governance insufficient for irreversible outward acts:
- Background tasks satisfy Ring 2 *and* bypass the ask-first confirmation gate entirely (`agent.py:5079-5080`, set at `agent.py:2377`). A scheduled "tidy" job today would execute any Ring-2 write with no confirmation and no approval card.
- The confirmation gate is dead on both voice paths — it requires a `session_id` that neither voice path supplies, so it silently allows (`agent.py:5340` vs `routes/voice.py:1363` and `voice_engine.py:574-582`). Separately, the ten native voice-live tools bypass `_execute_tool` altogether: no ring check, no decision-BOM entry, no receipt (`voice_engine.py:442-562`).

**The right approval primitive already exists, unwired.** `approvals.gate_action()` with `POLICY_TABLE_DEFAULTS` already gating `external_message`/`irreversible`/`outward` kinds (`approvals.py:102-108,359-394`), fail-closed classification, idempotency, expiry-never-approves, and decision hooks for post-approval resume. There is a working precedent of a tool handler gating itself through it: the Google OAuth URL open (`agent.py:1537-1562` with resume hook `agent.py:1468-1483`). Two gaps: **no UI renders `/api/approvals`** (zero hits in `index.html`), and `GET /api/approvals` + `GET /api/approvals/<id>` are missing `@login_required` (`routes/goals.py:183,194`) while card bodies carry payloads. HK-7 closes both.

**The file-grant system (WO-17) is the pattern to mirror, structurally.** Its guarantees are architectural, not instructional: no model-callable creation path (enforced by a registry-shape test, `tests/unit/test_file_grants.py:57-65`), consent screens rendered from the system's own scan rather than model text (`routes/control.py:104-108`), effect delivered only through an out-of-band feeder no caller can flag (`file_grants.py:414-430`), HMAC ledger that fails toward tightening (`file_grants.py:170-189`). "A spoken yes can never create a grant" (`file_grants.py:22-27`). The send-email design below copies each of those properties.

**The trust model here should be read against what the product already permits.** Friday's existing Vibe Code feature launches `claude --dangerously-skip-permissions "<task>"` in a detached terminal under `~/Projects` (`services/code_engine.py:56` at v5.7.0, registry at `core/__init__.py:594`). That is standing authority to run an unsandboxed agent with permission prompts disabled — strictly broader than anything in this spec. This is not an argument for loosening the Gmail design (an email leaving under Stephen's name is a different failure class from a local agent run he launched deliberately), but the eventual autonomy policy (§5, Q-H4) should reconcile the two rather than pretend the housekeeper verbs are the outer edge of what Friday can do.

> **Correction (2026-08-29).** An earlier revision of the paragraph above stated that
> "persistence and boot-time reconciliation were added alongside `342df97`". **That is
> false**, and it cost real time twice: two separate sessions read it as evidence that
> the work had shipped. `342df97` is Google Tasks write access (see §0, HK-1, HK-4) and
> touches neither `code_engine.py` nor `core/__init__.py`.
>
> **No shipped code persists the vibe-terminal registry or reconciles it at boot.**
> `VIBE_TERMINALS` is in-memory only, so a Friday restart orphans any running
> `claude --dangerously-skip-permissions` window: nothing tracks it, nothing can stop it
> from the UI. That makes the standing-authority point in this section worse, not better.
>
> An implementation of both does exist, but it is **uncommitted, and present in no tag
> and no release** — `git log --all -S adopt_or_reap_vibe_terminals` finds the name only
> in the docs commit `c60172a`, which merely *proposes* it. It is preserved on the local,
> unpushed branch `wip/vibe-terminal-persistence`, and it is not shippable as written:
> its reap path would force-kill every running vibe terminal on the first boot after it
> lands, because nothing has a persisted record yet.
>
> The general lesson, since this document caused the error: confirm a claim that code
> landed with `git log --all -S <symbol>`, not by reading a spec that asserts it.


**Scope lists are triplicated and two of the three are traps.** The live list is `GOOGLE_MULTI_SCOPES` (`google_accounts.py:66-67`). `calendar_engine.GOOGLE_SCOPES` (`calendar_engine.py:84-99`) and `scripts/friday_google_connect.py:51-56` (plus its byte-identical packaging duplicate) request *fewer* scopes — reconnecting through Settings → Connectors or the connect script would silently **downgrade** a grant. `calendar_write.py:63-65` currently directs users to exactly that downgrading path. HK-11 unifies.

---

## 2. Re-consent: what it costs and when to pay it

- **Mechanism:** `POST /api/google/accounts/connect` → consent screen → callback → `upsert_account` overwrites the recorded scope list idempotently (`google_accounts.py:180-223`). Once per account. No server restart needed for the consent itself; a restart *is* needed for the code change (scope constants load at boot).
- **Calendar:** no re-consent at all — full write scope already granted on both accounts.
- **Tasks + Gmail:** one re-consent per account, after the code merges and the server restarts at some natural point. The Tasks scope change is already merged (`342df97`) and both accounts verifiably still hold only `tasks.readonly` (§0) — the reconnect is now *required*, not optional. **Sequencing rule:** land the Gmail scope additions (HK-6, and HK-9's `gmail.modify` if Q-H2 says both) in `GOOGLE_MULTI_SCOPES` before Stephen performs that reconnect, so he consents once, not per-feature. The Gmail *tools* can ship later; only the scope constant has to beat the reconnect.
- **It probably costs nothing extra.** The active OAuth client is a *Web* client, and the in-repo setup guide describes External + Test users, i.e. **Testing** publishing status (`routes/google.py:216-217`) — under which Google expires refresh tokens in ~7 days. Both accounts' records were freshly created the very morning of this spec, consistent with a weekly re-auth cadence already existing. If that's right, the new scopes simply ride along on next week's re-consent. The reassuring code comment claiming desktop-client tokens don't expire (`calendar_engine.py:252-255`) does not apply to the client actually in use. **Q-H1 asks Stephen to confirm publishing status in the GCP console.**
- **Verification class doesn't change.** Friday already holds `gmail.readonly`, which sits in Google's restricted class; adding `gmail.compose`/`gmail.send`/`gmail.modify` changes what the consent screen lists, not whether verification is needed. In Testing with test users, restricted scopes work for personal use without verification. (Sonnet: confirm each scope's current sensitive/restricted classification in the console when HK-6/HK-8 land; do not trust this paragraph over the console.)
- **Tomorrow's install:** nothing in this spec touches live credentials until Stephen chooses to re-consent, and nothing requires a restart tonight. Merge freely; activate later. Do not rebuild the installer for this.

---

## 3. Design positions (asserted in the request, tested against the code)

1. **Never permanently delete.** Upheld, with one refinement: don't *rely* on Google-side trash semantics for undo. Every destructive verb writes a full before-image into its receipt, and undo = re-create from the receipt. Gmail delete is `messages().trash` only — `messages().delete` is never called and a test asserts its absence. Calendar delete keeps the event snapshot (Google's bin holds deleted events ~30 days as a second net). Tasks: prefer `complete` over delete; delete stores the full task object for re-insert.
2. **Send is its own tier.** Upheld and sharpened: Ring 2 demonstrably cannot carry it (§1), so send gates through `approvals.gate_action(kind="external_message")` *inside the handler*, with structural refusals for voice and background contexts. Draft-by-default has a bonus path here: a Gmail draft created via API appears in Stephen's own Gmail Drafts folder, where *he* can press send — a genuinely zero-trust intermediate stage that works before HK-8 ships at all.
3. **Account explicit on every write.** Upheld — and the audit showed the violation is already live for calendar (§1). Every write tool in this spec takes a **required** `account_id`; there is no default. Reads stay merged and badged. The model gets account IDs from the read tools' badges (`google_accounts.py:750-752`).
4. **Receipts + audit on every write.** Upheld. Every write records through `tool_receipts` (already automatic once dispatch goes through `_execute_tool`, `agent.py:5274`), lands in the signed decision BOM (`agent.py:4959-4984`), and returns an undo description in its result. `completion_receipts.py` gets claim patterns for the new verbs so "I sent it / completed it / deleted it" is checked against a real receipt (`completion_receipts.py:28-38`).
5. **Voice is the dangerous surface.** Upheld, with a concrete split (HK-10): low-stakes reversible verbs (`complete_task`) reach voice only via `_VOICE_SHARED_TOOLS` so they pass the full `_execute_tool` chain; send/delete/trash verbs are absent from both voice derivations, and — mirroring file grants — a spoken yes can never satisfy a send approval. Voice can *create* the pending approval and tell Stephen it's waiting; approval happens in the UI. The dead-on-voice confirmation gate is fixed as part of HK-10 because it currently lets `write_file` run unconfirmed from voice — a pre-existing hole this spec refuses to widen.
6. **Legible refusals.** Partially achievable now: a governance DENY string becomes the tool result and Friday paraphrases it (`agent.py:5258-5260`; see `docs/audits/which-gate-refused.md`). HK-7 adds the missing UI rendering of `deny` status (today no UI renders it; voice collapses it to "error", `routes/voice.py:541-542`). Every new refusal string in this spec names its gate in square brackets (`[SCOPE]`, `[APPROVAL PENDING]`, `[BACKGROUND REFUSED]`) so the paraphrase carries the gate name even before the UI lands.
7. **Do not build on `dynamic_rings.py`.** Its `check_and_consume` has zero production callers; it logs and returns but nothing consults it. `SELF.md:522-529` overclaims. All gating in this spec uses the enforced chain (`_execute_tool` pre-hooks) and `approvals.py`.

---

## 4. Work orders, in leverage order

Ship each independently. "AC" = acceptance criteria. Test patterns to reuse: hermetic home + `FakeCreds` with settable scopes (`tests/api/test_google_accounts.py:22-54`), monkeypatched `googleapiclient.discovery.build` (`:251-253`), OAuth flow without network (`tests/api/test_google_oauth_pkce.py:43-63`).

### HK-1 — `complete_task` ✅ LANDED as `342df97` (reconciled)
Shipped design matches this spec's core positions: `TASKS_RW` replaces `TASKS_READ` in `GOOGLE_MULTI_SCOPES`, `complete_task(account_id, tasklist_id, task_id)` with all three required and explicitly "never guessed" in the tool description, `list_tasks` returns `tasklist_id` per task, Ring 2, per-write audit events, tests at both the service and tool layers. Writes live in `google_accounts.py` beside the reads rather than a separate `tasks_write.py` — accepted; it matches the existing multi-account plumbing, and HK-6's `gmail_write.py` may follow either pattern.

**Deltas from this spec, worth a small follow-up (none blocks anything):**
1. **No recorded-scope preflight.** The shipped code lets the missing scope surface as Google's live 403 text — consistent with the documented house style for reads (`google_accounts.py:50-57`), but the spec's `write_ready(account_id)`-style check (per the `calendar_write.py:40-43` pattern) would say the *remedy*: "this account holds `tasks.readonly`; re-consent via `+ Add Account`." That message matters most right now, when **every** write 403s until the reconnect (§0). Recommend adding it.
2. **No `completion_receipts` claim pattern** for completed/created/deleted tasks — "I checked it off" is currently unverified prose. Recommend adding alongside HK-8's patterns.
3. **No `show_completed` option on `list_tasks`** — completed tasks remain invisible, so a completion can't be verified by re-reading, and un-completing needs the id remembered from before. Minor; fold into any later touch.

### HK-2 — Calendar writes become account-explicit (fixes a live defect)
**Files:** `calendar_write.py`, `agent.py` (three tool schemas + handlers).
1. `write_ready(account_id)` and `_service(account_id)` take a required account; the "any account passes preflight, first account executes" split (`calendar_write.py:56-59` vs `:86-98`) dies.
2. `annotate/create/update_calendar_event` schemas gain **required** `account_id` (`agent.py:361-383`). `find_calendar_events` gains optional `account_id` and starts returning event ids badged per account.
3. Fix the reconnect copy at `calendar_write.py:63-65,81-83` — it currently points at the scope-downgrading Connectors path; point it at `+ Add Account`.
4. Write the first tests for `calendar_write.py` (it has zero — `KNOWN_ISSUES.md:765-766`).
**AC:** a write with account_id=Work builds its service from Work's credentials (stubbed `build` asserts which token); omitting `account_id` is a schema error, not a default; two-account fixture where only Work holds write scope → Personal-targeted write refuses with `[SCOPE]`, Work-targeted write proceeds.
**Blast radius if wrong:** an event created/edited on the wrong calendar — annoying, reversible, and *less* likely than today's behaviour, which gets the account wrong by design whenever Stephen means Work.
**Flags:** no re-consent. Model needs restarting server to pick up schema changes — defer with the rest.

### HK-3 — `delete_calendar_event`
**Files:** `calendar_write.py`, `agent.py`, tests.
`delete_calendar_event(account_id, event_id, confirm_summary)` — the model must pass the event's summary string as `confirm_summary`, and the tool refuses unless it matches the live event's summary (a cheap targeting check that catches stale/hallucinated ids). Before deleting: fetch the full event, embed it in the receipt as `before`, then `events().delete`. Result states the undo path ("re-create from receipt; Google bin also holds it ~30 days"). Ring 2. Not exposed to voice (HK-10).
**AC:** mismatched `confirm_summary` refuses without deleting; receipt contains a re-insertable event body; a round-trip test re-creates from the receipt snapshot and asserts field equality (stubbed service).
**Blast radius if wrong:** a meeting vanishes from a calendar other people may share — recoverable from receipt/bin, but socially visible. This is why it takes the targeting check and stays off voice.

### HK-4 — Tasks add / edit / delete ✅ LANDED as `342df97` (reconciled)
Shipped: `create_task` (tasklist defaults to the `@default` API alias — a good call this spec didn't have), `update_task` (patch of title/notes/due/status; `update_task`'s description steers "just mark it done" to `complete_task`, as specced), `delete_task` with all ids required and membership in `_ALWAYS_CONFIRM`.

**Deltas from this spec, worth a small follow-up:**
1. **`delete_task` keeps no before-image.** It returns `{"deleted": true, id}` and is described as "cannot be undone." This spec called for fetching the task and embedding the snapshot in the result so undo = re-create from receipt — one `tasks().get` before the delete. Recommend adding; it converts the only irreversible task verb into a receipt-recoverable one.
2. **The `_ALWAYS_CONFIRM` protection on `delete_task` is thinner than it reads.** Per §1, the confirmation gate silently allows on both voice paths (no `session_id`) and is bypassed for background tasks — so today `delete_task` from local voice or a scheduled job would execute *unconfirmed*. Not a defect in `342df97` (it inherited the gate as-is, same as `write_file`); it raises HK-10's priority. Until HK-10 lands, the honest statement is: delete confirmation holds in text chat only.
3. Nit: `update_task` passes `notes` through unstripped, so `notes: ""` blanks the field — the one field the handler allows clearing without ceremony. Harmless for tasks; do not replicate the asymmetry in HK-6.

### HK-5 — `read_email`: body read (prerequisite for all Gmail composition)
**Files:** `google_accounts.py` (new `thread_for_account()` using `_extract_gmail_body`, cf. `calendar_engine.py:676`), `agent.py`, tests. Optionally repoint `routes/messages.py:153-190` at the same helper to fix the Work-message 404/silent-cache-fallback while there.
`read_email(account_id, message_id_or_thread_id)` → `threads().get(format="full")`, body extracted, size-capped. No new scope (`gmail.readonly` covers bodies). Ring 2. Egress: body content flows to cloud models as ordinary private content through the existing field-wise gate — deliberate, documented posture (`egress_gate.py:232-235`); do **not** register mail as public text.
**AC:** Work-account message read builds from Work credentials; body of a multipart message extracts; oversized bodies truncate with a marker; the tool result passing through `seal_outbound` with a never-send string in the body blocks the cloud call (existing gate behaviour, asserted for this path).
**Blast radius if wrong:** none outward — read-only. The risk it *removes* is Friday summarizing/replying from snippets she never read.

### HK-6 — Gmail drafts: compose and reply without send
**Files:** new `services/gmail_write.py` (same `write_ready(account_id)` shape), `agent.py`, `google_accounts.py` scope list, tests.
1. Scope: add `https://www.googleapis.com/auth/gmail.compose` to `GOOGLE_MULTI_SCOPES` (drafts + send capability at API level; send stays tool-less until HK-8).
2. `create_email_draft(account_id, to, subject, body, reply_to_message_id?)` → `users().drafts().create`, building the RFC-2822 message (reply threads via `In-Reply-To`/`References` + `threadId`). Result: draft id + "it is in your Gmail Drafts folder — nothing has been sent." Ring 2.
3. Replace the `draft_email` stub (`agent.py:2061-2072`) with the real handler; keep the tool name if migration is simpler, but the schema gains required `account_id`.
4. Outbound-content floor: before the API call, run subject+body through the egress never-send floor (`egress_gate._gate_text_span`'s floor, `egress_gate.py:704-726`, factored so it's callable for non-model egress). An email leaving for Google's servers is egress even when no cloud LLM is involved. A hit refuses with `[NEVER-SEND]`.
**AC:** draft lands via stubbed service under the right account with correct threading headers; never-send content in the body refuses before any API call; the stub's advertised-but-dead behaviour is gone from the system prompt copy (`model_router.py:1984,2017`).
**Blast radius if wrong:** a bad draft sits unsent in Drafts. By construction the worst case is embarrassment-in-private.
**Flags:** re-consent ×2 to activate — and per §2's sequencing rule, **land this scope constant (and HK-9's, per Q-H2) before Stephen performs the now-mandatory Tasks reconnect**, even if the draft tools themselves ship later; the constant is one line and beats a second consent ceremony. **This is the recommended stopping point for week one** — Friday composes, Stephen sends from Gmail, trust accrues with zero send risk.

### HK-7 — Approvals become real: UI, auth, deny visibility
**Files:** `routes/goals.py`, `index.html` **and** `ui_parts/app.html` (both, per the UI source-of-truth rule — `docs`: index.html is canonical, app.html is a strict subset that must receive the same edit), `routes/voice.py`.
1. Security fix first: add `@login_required` to `GET /api/approvals` and `GET /api/approvals/<id>` (`routes/goals.py:183,194`). Card payloads are sensitive.
2. Minimal approvals panel: list pending cards (kind, description, payload summary, created), Approve/Reject buttons hitting `/api/approvals/<id>/decide`. The wiki propose/apply UI is the in-repo precedent (`index.html:8166`). The notification action `{"workspace":"system","tab":"approvals"}` (`approvals.py:481-495`) must land somewhere real.
3. Render `deny` distinctly in the process orb instead of collapsing to error (`routes/voice.py:541-542`; no UI currently renders `status:"deny"`). A silent ring denial cost Stephen a day once; refusals become visible here.
**AC:** unauthenticated GET returns 401; a `gate_action`-created card appears, Approve fires the registered decision hook (test via the OAuth-open precedent's hook shape, `agent.py:1468-1483`); a governance deny shows as "denied" not "error" in the orb.
**Blast radius if wrong:** an approval button that mis-fires — mitigated by idempotent `decide()` and Law-1 blocked cards being immovable (`approvals.py:319,416-417`).

### HK-8 — `send_email` (requires HK-5, HK-6, HK-7; policy answers Q-H3/Q-H4)
**Files:** `gmail_write.py`, `agent.py`, `completion_receipts.py`, tests.
Design, mirroring the file-grant structure point by point:
1. The tool never sends directly. `send_email(account_id, draft_id)` — it takes an **existing draft**, not free text, so what's approved is byte-identical to what was composed and reviewable in Gmail itself. Handler calls `approvals.gate_action(kind="external_message", force_gate=True)` with a payload the *system* builds by re-fetching the draft from Gmail (recipients, subject, first N chars) — the approval card is rendered from Google's copy of the draft, never from model text (the `file_grants.scan_path` property, `routes/control.py:104-108`).
2. Execution happens in the decision hook after UI approval (the `_resume_google_oauth_open` pattern, `agent.py:1468-1483`): `users().drafts().send(draft_id)`. Tool's immediate return is `[APPROVAL PENDING] — approve in the Approvals panel`.
3. Structural refusals, each tested:
   - `session_ctx.get("is_background_task")` or `scheduled` → `[BACKGROUND REFUSED]` — scheduled jobs can never send (until an earned-autonomy policy exists; Q-H4).
   - Not present in either voice derivation (`_VOICE_LIVE_TOOLS`, `_VOICE_SHARED_TOOLS`) — and a registry-shape test in the spirit of `tests/unit/test_file_grants.py:57-65` asserts no send-capable name is voice-visible. A spoken yes cannot approve: approval lives behind `@login_required` HTTP only.
   - Never-send floor over the fetched draft content at approval-card-build time (belt to HK-6's braces).
4. `completion_receipts`: register the "sent/emailed" claim pattern so "I sent it" without a successful send receipt is flagged (`completion_receipts.py:28-38`). The sentinel list already contains `[CONFIRMATION REQUIRED]`; add the new bracketed refusals.
5. Update the two now-false claims: the OAuth approval copy "Friday never requests the gmail.send scope — it cannot send email on your behalf" (`agent.py:1543-1548`) and the test pinning it (`tests/unit/test_google_oauth_gate.py:80`). Replace with the true sentence: sends exist, are draft-bound, and require per-send UI approval.
6. Scope: `gmail.compose` from HK-6 already covers `drafts().send`; if the narrower posture of `gmail.send`-only is preferred later, that's a scope swap, not a redesign.
**AC:** send without approval leaves no API call; approval fires exactly one send (idempotency per `approvals.py` subject key); background context refuses; voice registries contain no send verb (shape test); "I sent it" with no receipt trips the completion-receipt check.
**Blast radius if wrong:** an email to real people from Stephen's name — the one non-recoverable act in this spec. Every layer above exists because of this line. Even then: draft-bound sending means the wrong email is one *he reviewed as a draft*, approved from a card built from Google's copy, on an explicitly named account.

### HK-9 — Archive / trash / label (`gmail.modify`)
**Files:** `gmail_write.py`, `agent.py`, scope list, tests.
`archive_email` (remove `INBOX` label), `trash_email` (`messages().trash` — 30-day recoverable; `messages().delete` is never called and a grep-shaped test asserts the string is absent from `src/`), `label_email` (add/remove labels). All Ring 2, required `account_id`, receipts include the label-set before. `untrash_email` for symmetry. Batch forms take explicit message-id lists — never queries — so "archive everything matching X" requires a read first and the receipt names every id.
**AC:** trash/untrash round-trip on stubbed service; archive only touches `INBOX`; no `.delete(` on messages anywhere in `src/`; batch refuses non-list input.
**Blast radius if wrong:** mail mis-filed — recoverable from Trash/labels per receipt. Note `gmail.modify` is the restricted-class scope; consent screen wording changes (§2).

### HK-10 — Voice surface policy + the dead confirmation gate
**Files:** `voice_engine.py`, `routes/voice.py`, `agent.py`.
1. Add `complete_task` (only) to `_VOICE_SHARED_TOOLS` (`voice_engine.py:292-299`) for cloud voice — shared tools route through `_execute_tool` and get full governance; native `_VOICE_LIVE_TOOLS` entries bypass it (`voice_engine.py:456-562`) and must never carry a Google write. Note that since `342df97`, *local* voice already has all four task tools (it runs the full registry), including `delete_task`.
2. Fix the gate — **priority raised by `342df97`**: both voice paths pass a real `session_id` into `session_ctx` and call `prepare_confirmation_ctx` equivalent, so `_hook_confirmation_gate` (`agent.py:5333-5355`) stops silently allowing. This closes the pre-existing hole where `write_file` (in `_ALWAYS_CONFIRM`, `agent.py:5025`) executes unconfirmed from voice — a set that now also contains `delete_task`, an irreversible Google write. Voice "confirmation" that consists of a model-set `confirmed: true` arg (`voice_engine.py:227,449-453`) is not enforcement and gains no new powers.
3. Voice behaviour for gated verbs: Friday may *stage* (create draft, create approval card) and must say the approval is waiting in the panel. Read-back-then-confirm was considered and rejected for sends: nothing verifies the model heard yes (the `_needs_confirm` mechanism trusts a model-asserted boolean), which is precisely the property file grants refused to accept.
**AC:** `complete_task` from local voice produces a decision-BOM entry and a tool receipt; `write_file` from voice now asks; no send/delete/trash name resolves in either voice derivation.

### HK-11 — Hygiene: kill the decoys, unify the scope lists
**Files:** as listed.
1. Delete the fake gmail-draft flow (`misc_engine.py:371-387` registration at `:436`, `routes/workflows.py:116-125,211-232`, the `dest:'gmail_draft'` UI affordance) or repoint it at HK-6's real drafts. A "confirm" button that edits a JSON status field is worse than no button.
2. Delete `/api/email/draft` placeholder (`routes/core_routes.py:570-573`) and the `/api/calendar` placeholder (`routes/calendar.py:276-279`).
3. Fix or delete the Quick Add insert at `routes/calendar.py:239-261` — it gates on the *code constant* rather than the token grant and swallows failure into a silent local-only fallback; route it through `calendar_write` post-HK-2.
4. Unify scopes: `calendar_engine.GOOGLE_SCOPES`, `friday_google_connect.py` (and its `packaging/windows/staging/payload/scripts/` duplicate) either import from `google_accounts` or are retired; every "reconnect" string in the app points at `+ Add Account`. The false comment about desktop-client token longevity (`calendar_engine.py:252-255`) is corrected.
5. Sweep the "read-only" copy: `connectors.py:79-84,332,502-504`, `routes/google.py:49,159-161`, `friday_google_connect.py:237`.
6. The orphaned `calendar-enrich-*.json` queue (`misc_engine.py:446-483`) returns `ok: True` and promises a sync that has no consumer — make `POST /api/calendar/enrich` either do the patch via `calendar_write` or say honestly that it stored a note.
7. `behavioral_monitor.py:75` names a `read_email` tool that will now *exist* (HK-5) — verify the sensitivity-read policy there does what its author intended once the phantom becomes real.

---

## 5. The housekeeper role itself — deferred, with preconditions

"Housekeeper" as Stephen means it — tidying unprompted — is a scheduling-layer claim on trust, not a set of verbs, and it gets its own spec later. What must be true first:

1. HK-1..HK-9 shipped and the receipts UI (HK-7) in daily use — verification before trust.
2. The background-task bypass (`agent.py:5079-5080`) replaced with an explicit per-verb allowlist: today `is_background_task` skips confirmation wholesale, which is the exact mechanism a scheduled tidy job would abuse.
3. First housekeeper mode is **propose-only**: a scheduled job produces a tidy *report* (tasks that look done, mail that looks archivable, calendar orphans) as an approval card batch; Stephen approves the batch; execution is item-by-item with receipts. The wiki propose/apply flow is the in-repo precedent. Autonomy beyond that is earned per-verb, per Stephen's explicit say-so, starting with the reversible ones.
4. Send never becomes autonomous by default; Q-H4 is where Stephen defines any narrow exceptions.

---

## 6. Questions for Stephen

- **Q-H1:** Confirm the OAuth client's publishing status in the GCP console (Testing vs Production). If Testing, you're already re-consenting weekly and the new scopes are free riders; if something else, tell the build session, because §2's timing argument changes.
- **Q-H2:** Scope breadth for Gmail — sharpened by `342df97`: a re-consent is now *mandatory* for Tasks regardless, so the question is what rides along on it. Recommendation upgraded to **take both** (`gmail.compose` + `gmail.modify`) in that single reconnect: the marginal consent cost is zero, the tools stay unbuilt until their work orders land, and it avoids a third ceremony when HK-9 arrives. Say no only if you want the consent screen to under-promise until the send design has history.
- **Q-H3:** Send approval UX: is per-send approval in the panel acceptable indefinitely, or do you want a "trusted recipients" list (e.g. sends to yourself or a designated trusted contact auto-approve) once HK-8 has history? Policy call, not code.
- **Q-H4:** Earned autonomy for the housekeeper: which verbs, if any, may ever run scheduled without a card? (Spec's position: completions yes eventually, archives maybe, sends never.)
- **Q-H5:** Calendar deletes: comfortable with delete-with-receipt as specced, or do you want delete to require the same approval card as send until it has history?
- **Q-H6:** May the build session retire the gmail-mcp config entry entirely (it advertises unimplemented send tools if ever re-enabled), or do you want it kept dormant?

---

## 7. Explicitly out of scope / in flight elsewhere

File grants, local file search and PDF extraction (WO-14/WO-17, shipped 2026-08-25) — HK borrows their patterns, changes nothing in them. The 5.6.1 installer verification and the second-machine install *[dated 2026-08-26; both have since happened, and the product is now v5.7.0]* — nothing here restarts the server, edits `index.html` (HK-7's UI work happens in the build session, after the install), or rebuilds the installer. The plaintext GitHub PAT found in `~/.friday/mcp_servers.json` during this audit was spun off as its own task, not part of HK. *[RESOLVED 2026-08-29: v5.6.4 stopped connector tokens being stored and returned in plaintext, and no plaintext PAT is present in that file today.]*
