# Architecture: subsystems in detail

> Last checked against the code on 2026-10-17 (origin/main). Paths are
> relative to `src/agent_friday/` unless they start at the repository root.
> The short map (processes, a chat turn, the hook table, where things live) is
> in [ARCHITECTURE.md](../../ARCHITECTURE.md). Diagrams are in
> [diagram.md](diagram.md). When this page and the code disagree, the code is
> right; please fix the page.

Each section names the modules that own the behaviour and how data moves
between them. Anything that is partly built, off by default or running in
shadow (computed and logged, changing nothing) says so.

Contents:

1. [Routing mode and cloud consent](#1-routing-mode-and-cloud-consent)
2. [The egress gate and the vault gate](#2-the-egress-gate-and-the-vault-gate)
3. [The never-send floor and raw-record holds](#3-the-never-send-floor-and-raw-record-holds)
4. [Approvals, cards and receipts](#4-approvals-cards-and-receipts)
5. [Scheduler](#5-scheduler)
6. [Memory card and the frozen prompt head](#6-memory-card-and-the-frozen-prompt-head)
7. [Conversation search](#7-conversation-search)
8. [Knowledge graph, embedder and vault key](#8-knowledge-graph-embedder-and-vault-key)
9. [Laya: what is live and what is trial](#9-laya-what-is-live-and-what-is-trial)
10. [Seats and model routing](#10-seats-and-model-routing)
11. [Connections](#11-connections)
12. [First run](#12-first-run)
13. [Workspaces and Laya contracts](#13-workspaces-and-laya-contracts)
14. [Task visibility](#14-task-visibility)
15. [Release and gates](#15-release-and-gates)
16. [Held features](#16-held-features)

---

## 1. Routing mode and cloud consent

Three settings, each with one owner, decide where a turn may go. All three
live in `settings.json` under `model_routing`.

| Setting | Owner | Default |
|---|---|---|
| `model_routing.mode` | `routing/model_router.py` reads it; mode list in `services/cloud_intent.py` (`MODES`) | `cloud_only` (`DEFAULT_SETTINGS`, `core/__init__.py`) |
| `model_routing.cloud_consent` | `privacy/cloud_consent.py` | `{"answered": False, "choice": None, ...}` |
| `model_routing.vault_local_only` | `privacy/vault_policy.py` | `True` |

**Mode.** The valid modes are `local_only`, `local_preferred`,
`cloud_preferred` and `cloud_only` (`cloud_intent.MODES`; a unit test,
`tests/unit/test_mode_lists_agree.py`, keeps other lists in step). The old
`smart` mode is read as `local_preferred` and rewritten on disk once.
`cloud_preferred` is hidden in the UI. Out of the box the mode is `cloud_only`;
the first-run privacy answer rewrites it (below). Friday can run fully local
when the owner chooses **Only on this PC** and has a local model; it does not
do so by default.

**Consent.** `cloud_consent.resolve()` returns a `ConsentStatus` and never
raises. The recorded choice is one of `local_private`, `cloud_guarded` or
`cloud_unrestricted`. Unanswered, unreadable or broken consent all resolve to
"gated", which keeps every safeguard on (the same as `cloud_guarded`). Only
`cloud_unrestricted` widens the egress gate's tier policy (section 2).
Choosing Cloud only is consent too (owner ruling 2026-10-17): when
`model_routing.mode` and `mode_chosen` are both `cloud_only` and
`mode_chosen_via` is `settings` (the Settings > Models control, clicked on
Friday's own page: `screen_click.screen_session`) or `setup` (the terminal
wizard, or setup completion clicked on the page), `resolve()` answers
`cloud_unrestricted` with source `cloud_only_choice`, unless a recorded answer
is later (a tie goes to the recorded answer). `core._save_settings` writes
`mode_chosen_via` only on an owner routing change and strips it from client
writes; the setup chat's posture answer writes `posture` (its recorded consent
decides), so its implied mode never counts on its own. The privacy summary
states what `resolve()` decides, the same answer the gate acts on.
`record_consent()` is the only writer: it re-measures the hardware, refuses
`local_private` on a machine that cannot run a local model, and writes through
an internal settings path. Every generic settings write strips this key, so
the UI cannot set consent by posting settings.

**Local-only records.** `vault_policy.resolve()` decides one flag,
`vault_local_only`. On (the default, and the answer on any error), vault tiers
are stripped from cloud prompts and vault-touching turns are routed to a local
model. The egress gate is separate and does not depend on this flag.

**Posture mapping.** The setup chat's privacy step is applied by
`services/setup_steps.apply_posture()`:

| Answer | Consent | Routing mode | Scheduled cloud allowed |
|---|---|---|---|
| Only on this PC (`local`) | `local_private` | `local_only` | no |
| Cloud for public things (`public`) | `cloud_guarded` | `local_only` if the PC can run a local model, else `cloud_only` | yes |
| Cloud for everything (`everything`) | `cloud_unrestricted` | `cloud_only` | yes |

It calls `cloud_consent.record_consent`, then
`setup_complete.save_routing_answer`, then `scheduled_cloud.save`. A rejected
consent (local chosen on an incapable PC) becomes `PostureUnavailable`.

**Every system prompt states the settings.**
`services/privacy_policy_summary.summary()` renders a block headed
`== PRIVACY POLICY ==`, capped at about 120 tokens: mode, consent, local-only
records on or off, the size of the never-send list, spend caps, one line on
what that means, and the rules ("web/news queries are not private by topic",
"Inform once; owner decides", "A refusal against these settings is a bug").
It is added in one place, `services/model_router._build_context_prompt`, which
`_get_friday_system_prompt` calls, so chat, agent tasks, scheduled runs, news,
channels, local voice and the cloud voice prompt (`routes/voice.py`, `ws_live`)
all carry it. The local voice front adds it separately
(`routes/voice.py`, `_front_privacy_block`).

## 2. The egress gate and the vault gate

There is no egress module under `governance/` or `privacy/`; the gate is
`services/egress_gate.py`.

**Where it runs.** Every cloud model call goes through
`services/model_router._seal_or_block()` (used by `_call_claude`, the
OpenAI-compatible path, `services/agent.py` and `services/compaction.py`). In
order it runs:

1. `spend_guard.check()`: the hard spending cap.
2. `prompt_cache.check_call_size()`: a hard size ceiling (it raises; it does
   not trim).
3. `egress_gate.seal_outbound()`.
4. A hold from the classifier while it starts (`PrivacyCheckStarting`).
5. `gate_operational()`: if the gate's start-up self-test failed, every cloud
   send is refused.

Any exception blocks the send. Other callers use `seal_outbound` or
`gate_text` directly (channels, federated compute, media, TTS and search
paths). Local providers pass through; a model tagged `:cloud` or `-cloud`
always counts as cloud.

**What `seal_outbound` does to a payload** (system, messages, tools, prompt,
context):

1. Identifier scrub (`core._scrub_pii`: SSNs, Luhn-checked card numbers,
   phones, emails, addresses, watch-list entries). Skipped under
   `cloud_unrestricted`. A scrub that fails blocks the send.
2. The never-send floor (section 3). A hit raises `NeverSendBlocked`: the
   whole call stops; nothing is redacted and sent.
3. Under `cloud_unrestricted` the text goes as is after the floor.
4. Otherwise each field is classified (`sensitivity_classifier`): public text
   passes, private text gets a placeholder, sensitive text is removed;
   multi-paragraph text is withheld span by span.

Images and documents are not tier-gated; they are recorded
(`record_binary_egress`). Decisions are logged to
`~/.friday/vault/egress-log.jsonl`.

**The money card.** Under `cloud_guarded`, email paragraphs about money that
the gate would withhold from a cloud seat raise a `cloud_release` card
(`services/cloud_release.py`) showing the exact text, with **Send** and
**Keep on this PC**. A Send lasts 30 minutes. No card when the seat is local.
Never-send material and hard identifiers are never offered.

**The vault gate.** `privacy/vault_access.py` classifies content into three
tiers: `PUBLIC` (any model), `PRIVATE` (local; cloud gets a placeholder) and
`SENSITIVE` (local; cloud gets nothing). `VaultAccessControl` gates vault
content while prompts are assembled, and `check_action()`, called by both
agent loops in `services/agent.py`, checks tool inputs before a tool runs
(writing `access-log.jsonl`). Under `cloud_unrestricted`
only `_consent_floor()` still refuses a tool input: never-send entries, hard
identifiers (card, SSN, routing numbers, API keys) and anything shaped like a
credential. Failures refuse (`floor_unavailable`, `never_send`). One
exception: the classifier's own default tier is `PUBLIC`.

When the vault gate refuses a step, `services/seat_handoff.denial_result`
turns it into one `seat_handoff` card per conversation offering to run the
work on the local model (section 4).

**Related guards.**
- `services/cloud_mode_policy.cloud_allowed()`: in a local mode, cloud use
  needs a recorded intent; media generation is refused under `local_only`.
- `services/local_only_guard.py`: a thread-local "this run is local-only"
  scope. `refuse_if_active()` raises `CloudRefused` at the cloud transports
  themselves. An unknown provider counts as cloud.

## 3. The never-send floor and raw-record holds

`services/judgment_gate.never_send_hits(text)` is the floor. It combines:

- the owner's never-send list, `never_send_tokens()`, read from
  `~/.friday/privacy_shield.json` (key `never_send`), matched case-insensitively
  on word boundaries;
- vault document markers (`vault/legal`, `vault/finances`, `vault/family`);
- paragraphs from files marked deny;
- raw-record hits (below).

It runs inside the egress gate before the unrestricted-consent check and
before any trusted or public exemption, so it applies to every cloud
destination in every mode. If the check cannot run, the payload is held. The
only exception is a file-grant override that covers the exact paragraphs; a
held raw record is never covered.

Card numbers and bank or account numbers ARE part of the floor by default:
`services/never_send_defaults.py` adds the entries `[card numbers]` and
`[bank and account numbers]` to every never-send list once per install
(migration from scheduler start, Activity notice NS1, a removed entry is
never re-added), and `never_send_hits` matches them by pattern (a card network's prefix and
length plus the card checksum, IBAN checksum, numbers written after an account, routing or
sort-code label). The owner removes either on the Locks page
(`POST /api/locks/never-send/remove`, screen click only). SSNs and
credentials (`hard_identifier_hits`) are not part of the floor. In prose they
are masked by the identifier scrub, which is skipped under unrestricted
consent. They are refused in tool inputs by the
vault gate's floor in every mode, and a key pasted into chat is withheld
before the turn starts (`services/pasted_secrets.py`, applied to
`/api/chat`, `/api/chat/stream` and `/api/chat/send`).

The optional judgment layer in the same module (`judgment_gate.enabled`,
off by default) can only rescue spans the rules already withheld; if it fails,
the rules decide unchanged.

**Raw-record holds.** `services/raw_records.kind_of(path)` recognises mail
archives (`.mbox .mbx .eml .emlx .msg .pst .ost`), finance exports
(`.ofx .qfx .qif .qbo`), and PDFs or spreadsheets named like statements or
account exports. When one is read (`file_grants.on_file_read`),
`judgment_gate.hold_raw_record()` stores SHA-256 hashes of its lines and
sentences, never the text, in `~/.friday/privacy/raw_record_holds.bin`
(through `credential_store`). Any later payload containing a held unit is
blocked by the floor. If the store cannot be read, written or is full, the
check fails closed and stays closed across restarts.

A yes for one file is a file grant pinned to the file's content hash
(`file_grants.create_file_grant`, `~/.friday/privacy/file_grants.jsonl`),
which releases that file's hold. A changed file is held again. Folder and
glob grants never cover raw records. The card that asks is shown at most once
a day per file version (`ask_for_raw_source`), and never under `local_only`.

The **Locks** page (`services/locks.py`, `routes/locks.py`) is a read-only
view of these holds and the other protections; "Start the list fresh" raises a
screen-only card that calls `judgment_gate.reset_raw_holds`.

Known limit, stated in the code and the Locks page: a model that builds a
path from pieces can read around the read-time guard; the floor still catches
held units in what is sent.

## 4. Approvals, cards and receipts

**Records.** `services/approvals.py` keeps cards in `~/.friday/approvals.json`
(atomic writes under one lock). Statuses: `pending`, `auto_approved`,
`approved`, `denied`, `expired`, `blocked`. Creation is idempotent per subject
and kind. Off-the-record cards stay in memory only. Some kinds are
screen-only (`SCREEN_ONLY_KINDS`: file grants, library changes, Laya
threshold and wipe, raw-hold reset): only `owner:ui` can approve them.

**One decision wins.** `decide_with_outcome()` acts only while the card is
still `pending` and returns `won=True` to exactly one caller. Execution is
claimed once (`claim_for_execution`) and completion recorded once
(`mark_used`). `POST /api/approvals/<id>/decide` (in `routes/goals.py`)
returns `won` and `already_decided`.

**From tool call to card.** In `services/agent.py`:

- `_hook_governance` (hook priority 1) calls `governance/action_gate.authorize`.
  `action_gate.classify` sorts tools into `internal`, `observe`, `outward` or
  `forbidden` (unknown tools count as outward). `_decide` returns `allow`,
  `confirm`, `card` or `deny`, after checking the cLaws HMAC pin, send
  pre-approvals, `SELF_GATED` tools and scoped grants for unattended work.
  Taint from `services/taint.py` (a sensitive argument that came from read
  content) can force a card (`tainted_action`) or deny.
- `_hook_confirmation_gate` (priority 10) handles `confirm`. Every
  confirmation is a `tool_confirm` card from the first ask, and the chat also
  shows the question. A repeated ask goes to the card instead of asking again.
- `services/approval_executor.py` is the decision hook for `tool_confirm` and
  `tainted_action`: on approve it claims the card, records an external
  receipt, runs `_execute_tool` with the approved card, marks it used and
  posts the result back into the conversation.

**Answering in chat.** A whole "yes" typed on the owner's own page approves
the card it answers, by compare-and-set (`approvals.claim_chat_confirmation`,
reached through `reflex_chat.approval_turn` or
`agent.prepare_confirmation_ctx`). A trusted surface means: local request,
the page's session token, typed (not voice), no attachment, one short line. A
bare "no" declines only the card of the newest waiting question
(`_deny_awaiting_card`). A yes from anywhere else does not decide a card. If
the card was already decided elsewhere the turn says so.

**Where cards appear.** All read one feed (`services/approval_feed.py`, SSE at
`/api/approvals/events`; in the page `fridayApprovalFeed`, one tab holds the
stream and relays to others):

- in the chat, `ChatApprovalCards` (`index.html`), filtered by conversation;
- the Activity panel's **Needs you** tab (`static/friday_experience.js`,
  `services/activity_lane.py`, `/api/lanes/needs-you`);
- the popup on every page (`ApprovalPopups`);
- **System > Approvals** (`StandaloneApprovals`).

`static/friday_responsive_topbar.js` keeps the Activity button in the top bar
while anything waits. `services/approval_batches.py` groups a few
low-risk kinds into one decision; it is a view, not a separate gate.

**Other card kinds.**
- `cloud_release`: money paragraphs (section 2).
- `seat_handoff` (`services/seat_handoff.py`): the `switch_seat` tool only
  raises a card. On approve it switches the conversation's seat or hands one
  task to the local model as a pinned background task. A cloud-tagged model
  is still refused by the vault gate.
- Pre-approved sends: `governance/send_grants.py`. Grants cover `send_email`
  only, scoped to named recipients or an `@domain`, one scheduled job, or a
  skill. Free-mail domains, public suffixes and bare TLDs are refused. Rows
  are HMAC-signed with the governance key; unsigned rows are ignored. A card
  is still raised when a recipient is not covered, the work read outside
  content (`governance/outside_content.py`), the request came by voice without
  `voice_may_use` (off by default), or the text does not screen as public.
  Editing a granted job voids the grant until re-approved. `revoke` also takes
  back queued sends. Unattended pre-approved sends wait for an undo window
  (`unattended_send_undo_s`, default 120 s, 10-600 s; `owner_choices.py`);
  a send the owner approved in person waits 10 s (`gmail_send.UNDO_SECONDS`).
- Sends that are not tool calls use `action_gate.authorize_external` (needs an
  approved, unused card) or `record_external`.

**Receipts.**
- `governance/action_gate` writes a signed receipt for each decision to
  `~/.friday/decision-bom.jsonl` (HMAC-SHA256 under the governance key from
  `proof_of_integrity.get_governance_key`). If the receipt cannot be written,
  outward actions are denied.
- `services/tool_receipts.py`: a per-turn record of tools that actually ran.
  `unbacked_claims` adds a correction note to a reply (`routes/chat.py`).
- `services/completion_receipts.py`: a claim that something was done needs a
  receipt from that turn. `services/model_router.validate_toolcall_integrity`
  enforces it: a corrective retry, then a retry without tools, then rewriting
  the false sentence, then an honest failure.
- `services/turn_receipt.py`: a read-only per-turn summary (models, egress
  counts, governance decisions; names and sizes only) at
  `GET /api/turns/<id>/receipt`.

## 5. Scheduler

`services/scheduler.py` waits 10 s after start, then ticks every 60 s over
`~/.friday/schedules.json`; history is in `schedule_runs.jsonl`. Each due job
runs on its own thread. Task kinds are `builtin`, `agent_prompt` and
`workflow`. `FRIDAY_TESTING=1` stops the loop.

**When.** Triggers are `interval` (also "every few hours"), `once`,
`idle_daily` (once a day while the owner is away) and the calendar triggers in
`services/schedule_math.py`: `daily`, `weekly` (a list of weekdays, so "every
weekday" and "certain days" are weekly) and `monthly` (day 1-31, or -1 for the
last day; a missing day runs on the month's last day). The math works on local
wall-clock time: a time skipped by a DST change runs at the first real moment
after the gap; a repeated time runs once. Missed slots follow
`MISSED_POLICIES` (`run_once`, the default, or `skip`).

**Pinned models and held runs.** A job may pin a model. `pinned_models()` /
`_pin_routes()` never move a pinned job to another model; if the pinned model
is not available the run is `held`. The owner's default for held runs is the
setting `schedules.held_default` (`retry`: run once when the model is back,
the default; `wait`: wait for the next scheduled time), overridable per job
(`on_held`). Other held states: `connector_unavailable`, `blocked`,
`budget_cap`.

**Local by default.** Built-in agent schedules listed in
`LOCAL_ONLY_BY_DEFAULT` (daily creation, morning and evening news, afternoon
briefing, weekly digest, weekly editorial, hourly heartbeat) are seeded with
`local_only: True`; an owner's explicit `false` is kept. Inside a local-only
run `local_only_guard` refuses any cloud call. `services/scheduled_cloud.py`
holds the separate yes/no for scheduled cloud work (default not answered, not
allowed; set by the posture answer), with its own heartbeat cadence and
daytime window.

**Built-in routines** are registered in `_register_default_builtin_tasks()`;
examples: Calendar reminders (every 2 minutes, silent), goal milestones,
approval expiry sweep, knowledge-graph reindex, memory dreaming, learning from
corrections (`idle_daily`). The hourly heartbeat is an `agent_prompt` schedule
(`sch_heartbeat`), not a builtin.

**Quiet before setup.** `_notify_run` sends no notice while
`_setup_finished()` is false (it uses `core._is_existing_install()`); runs are
still recorded. There is no separate `setup_complete` check in the scheduler.

**Email jobs.** `services/email_jobs.py`: new jobs save Gmail drafts unless the
job is switched to send. `migrate()` runs once from `start_scheduler`, marks
existing jobs (and workflows) that were made to send with `email_send: true`
so they keep sending (each send still asks unless pre-approved), and posts one
Activity notice, "Your email jobs keep sending". State:
`email_send_migration.json`.

## 6. Memory card and the frozen prompt head

**The card.** `services/core_memory.py` keeps two files in
`~/.friday/core_memory/`: `ME.md` ("About you", cap 1,375 characters) and
`FRIDAY.md` ("Working with you", cap 2,200), with at most 400 characters per
line. Changes are journalled in `changes.jsonl`; a review queue holds
suggestions. Background passes may only add; changes and removals wait for
review. Text that reads as an instruction or a credential is refused.
`render_prompt()` writes both files into the prompt; the owner's lines can be
withheld from cloud readers.

**Who may write.** `services/core_memory_tools.py` (`update_core_memory`)
saves silently only when the line is backed by the owner's own words: typed,
or spoken on a call on this PC within 5 minutes. If outside content was read
in the last 6 hours (the taint ledger), or the check cannot tell, it raises a
card. `capture_household()` saves household basics typed in the app under the
same rule, using fixed patterns from `services/household_facts.py`; voice,
pasted text and minors are queued, not saved.

**Quiet review.** `services/memory_review.py` runs every 10 turns by default
(`memory_review_every_turns`, 2-100; on/off `memory_review_enabled`, default
on). It runs inside a local-only scope and is skipped if no local model is
serving. It only adds lines backed by typed words; spoken turns are queued and
off-the-record turns are never read.

**Frozen per conversation.** `services/prompt_freeze.frozen()` captures each
memory block on the conversation's first prompt build and reuses it for every
later turn, so a save during a conversation applies to the next one. The
snapshot is in process memory only (up to 256 conversations) and is dropped
when the owner edits the card (`owner_changed()`, called from
`core_memory`) or after 6 hours idle. The blocks are assembled by
`services/model_router.memory_head_blocks()` (`== CORE MEMORY ==`, fenced by
`memory_fence`, then `== USER MODEL ==` and learned heuristics), called from
`_get_friday_system_prompt` with the conversation id as the key.

## 7. Conversation search

`services/conversation_fts.py` keeps a SQLite FTS5 index at
`~/.friday/conversations/index.sqlite` (porter-stemmed table plus an optional
trigram table). One row per chat or voice turn. Off-the-record messages are
not indexed. Every text passes `pasted_secrets.scrub` and `memory_fence.strip`
first; if the redactor cannot load, nothing is indexed. `delete_message()`
removes a message from the transcript, the index, the vector memory, the
in-memory history and the memory-review buffer.

The agent tool `search_past_conversations`
(`services/conversation_search_tool.py`) gives a local model every match. A
cloud model gets only the messages whose recorded `sent_to` includes that
provider (`services/conversation_provenance.py`), plus a count of the rest.
The filter is per message and per provider.

## 8. Knowledge graph, embedder and vault key

**Knowledge graph.** `services/knowledge_graph/` (`store.py`, `indexer.py`,
`retrieval.py`, `structural_query.py`, `wiki_graph.py`, and others) builds two
tiers from the wiki: structural links with no model, and GraphRAG extraction.
Output lives in `~/.friday/knowledge-graph/` and can be rebuilt. Private and
sensitive records are split into `<artifact>.sensitive.json`, sealed with
`privacy/vault_crypto.py`; with no vault key they are not written.

**Embedder.** Friday supplies its own embeddings and never uses Chroma's
default (which would download a model). `services/minilm.py` provides one
shared all-MiniLM-L6-v2 (384 dimensions): the pinned ONNX session from
`services/laya2_encoder.py` when present, otherwise a shared model fetched by
`services/embedder_cache.py` with a visible notice. The knowledge graph hands
Chroma precomputed vectors (`indexer.py`, `kg_vectors`).

**Vault crypto.** AES-256-GCM with an Argon2id-derived key
(`privacy/vault_crypto.py`; salt in `vault/.vault_config.json`).
`privacy/vault_rekey.py` re-seals passphrase-encrypted files under the
keystore root key, round-tripping each file before replacing it.

**One passphrase, several copies.** `services/vault_passphrase.py` resolves
the passphrase from, in order: an environment variable a person set, the
Windows keychain slot, the DPAPI file
`~/.friday/security/vault-passphrase.dpapi`, then launcher-script values. When
copies disagree, `resolve()` tries each on a sample of 8 sealed files and uses
the one that opens the most (precedence breaks ties), and records the
conflict for the integrity report. Only the account's real Friday home may use
the keychain slot (`keychain_is_this_homes`); test and scratch homes do not.
Rotation refuses while Friday runs.

**Key doctor.** `scripts/vault_key_doctor.py` (repository root): a read-only
report by default; `--repair-keychain` makes every durable copy hold the
working passphrase (keeping displaced values); `--recover-kg` re-seals
knowledge-graph files under the current key; `--rotate` asks for a new
passphrase twice and backs up originals. Every write mode refuses while
Friday runs (server lock, tray mutex, or a Friday port answering).

## 9. Laya: what is live and what is trial

Laya is Friday's small local layer for routing and reflex answers. It runs on
the CPU (`services/laya_runtime.py`, `laya_runtime="auto"`). The settings
below are `DEFAULT_SETTINGS` keys in `core/__init__.py`.

**Live by default**

| Piece | Module | What it does |
|---|---|---|
| Approval replies | `services/reflex_chat.approval_turn` | A typed whole "yes" or bare "no" answers a waiting card with no model call. `reflex_approval_replies=True`. |
| Rule router for local voice | `services/laya_router.py` | Decides `tool`, `no_tool`, `ask` or `defer`. Tier 0 is sealed regular-expression rules; tier 1 is nearest-prototype matching on the MiniLM encoder (`laya2_encoder`). It never runs a tool that changes anything; such turns become `defer`. Used by local voice: `voice_tool_routing="laya"`. |
| Fixed lines | `services/laya_templates.py` | The only sentences Laya may say herself. |
| Entry point | `services/laya.py` | `ask()` runs presets on a worker thread with a deadline, can only add caution, never blocks a turn, and writes a receipt. |

**Opt-in or off by default**

| Piece | Setting and default | Notes |
|---|---|---|
| Typed chat tool routing | `chat_tool_routing=""` (unset means the model decides) | `services/chat_tool_routing.py`. When set to `laya`: on a local seat a `tool` decision runs via `_execute_tool`; on a cloud seat only a `tool` decision at 0.9 confidence or more. |
| "Open the X" reflex | `reflex_open_enabled=None` (only `True` turns it on) | `reflex_chat.open_turn`, `services/laya_resolver.py`. |
| Safe writes | `laya_safe_writes="off"` (`off`, `shadow`, `on`) | `services/laya_safe_writes.py` proposes reversible mail changes as a batch card; never runs them. Can only be raised by an owner click in Settings; other writes of the key are dropped. |
| Pilot | `laya_pilot_enabled=False` | `services/laya_pilot.py`, an A/B experiment on chat turns. |

**Shadow (computed and logged, changes nothing)**

| Piece | Setting and default | Notes |
|---|---|---|
| Trained tool head | `laya_tool_head="shadow"` | `services/laya_router_head.py`. Inert until a certified checkpoint exists under the runtime folder; in shadow it logs to `router_head_shadow.jsonl`. The module docstring says the default is `off`; the settings say `shadow`. |
| Approval scorer | `decision_backend="keyword"`, `decision_shadow="laya"` | `services/laya_backend.py` scores beside the keyword scan; the keyword verdict stands. |
| Turn shape | none | `services/reflex_turn.py` logs a turn-shape guess to `reflex_shadow.jsonl`. |
| Calibration | none | `services/laya_calibration.py`; logged, can only tighten. |
| Live-call reflex | `voice_live_reflex="shadow"` | `services/live_laya.py`, beside a cloud voice call. Its idempotency wrapper on `_execute_tool` is live. |

**Learning.** `services/laya_personal*.py` learns the owner's phrasings
(`laya_personal_learning=True`, retention 180 days); data is sealed in the
vault. `services/laya_router_learn.py` captures corrections for a future head;
words are kept only with an opt-in that is not declared in `DEFAULT_SETTINGS`
(see the audit notes). Whether typed chat feeds personal learning is not
settled in the code: `laya.route_turn` sets the personal profile only for
voice. Treat it as unverified.

**Where Laya runs in a chat turn** (`routes/chat.py`, `chat()`): the pilot
start, the approval and open reflexes (either may answer with no model), the
turn-shape shadow, then the routing decision, then `chat_tool_routing` if
chosen. `/api/chat/send` (an older endpoint) wires none of the reflexes or
`chat_tool_routing`. `services/agent.py` does not route through Laya.

There is no separate "phrase matcher" module; the phrase rules are the router's
tier 0. **Needle is gone**: no module, setting or route by that name exists;
the word appears only as local variable names and in a test fixture's list
of made-up agent names.

## 10. Seats and model routing

A **seat** is a model bound to a job (`capability_routing` in settings):
`reasoning` and `subagent` (cloud by default), `heavy_hitter`, `orchestrator`,
`sidekick_fast`, `function_manager`, `memory_manager`, `researcher` (local,
empty until chosen), creative seats, `voice`, `asr`, `tts`, `embedding` and
`local`. A seat is local or cloud by the model it names.

**Deciding.** `routing/model_router.route()` decides and executes nothing:

1. Vault check. With `vault_local_only` on, a vault request goes to a local
   model even in `cloud_only`. With no local model, `vault_cloud_fallback`
   decides: `redact` (default) sends it to the cloud with redaction; `deny`
   and `warn` refuse.
2. The chosen seat: per-turn choice, then the conversation's seat, then
   `capability_routing.reasoning` (`_chosen_seat`).
3. The mode. In `local_only` with no local seat, the turn is refused with
   `local_unavailable` and an offer to switch; it never routes to the cloud.
   In the local modes, a cloud result without a recorded cloud intent is
   turned into a local seat or a refusal.

**Calling.** `services/model_router.py` executes (`_call_claude`,
`_call_openai`, `_call_ollama`); every cloud call goes through
`_seal_or_block` (section 2). Anthropic models run the Claude tool loop in
`services/agent.py`; every OpenAI-compatible provider, local or cloud, runs
`_oai_agentic_loop`. Both use one tool registry (`CLAUDE_TOOLS`) and dispatch
through `_execute_tool`.

**Providers.** `services/provider_registry.py` holds the built-in providers
plus user descriptors in `~/.friday/providers/*.json|yaml`;
`routing/provider_descriptors.py` defines and validates the descriptor
schema and adds more built-in cloud providers.

**Seat services.** `seat_binding` (the residency plan feeds
`capability_routing`), `seat_policy` (which seats must be local),
`seat_guard` (no second runtime while a seat serves), `seat_queue`,
`seat_admission` and `seat_supervisor` (one job per local seat, FIFO),
`seat_transparency` (a seat change posts a chat line), `seat_warm` (prefix
warm-up), `model_seat_gate` (a diagnostic; it no longer gates).

**Residency.** `services/residency_arbiter.py` owns the GPU: it starts pinned
`llama-server` seats (ports from 8090), leases Ollama and ComfyUI, grants GPU
leases to voice workers and image jobs, and runs transitions one at a time.

**What actually answered.** `services/answer_truth.py` records the model,
provider and seat that answered the last chat (no text), and the Models card
compares it with what the settings predicted.

## 11. Connections

**Catalogue.** `services/connections_guide.py` is the data behind **Connect
your services**: six categories (`models`, `creative`, `search`, `accounts`,
`mcp`, `more`), a guide per service (verified link, three steps, cost note,
"What it can access"), `COMING_SOON` (no field, no link), curated remote MCP
servers, and spoken names for voice ("connect OpenRouter").
`services/setup_connections.checklist()` serves `GET /api/setup/connections`
with each item's status (`connected`, `needs_attention`, `not_connected`,
`skipped`, `external`).

**Actions.** `services/connections_setup.py` tests a connection (`pass`,
`fail`, `not_connected`, `unknown`, each with a fix line), saves Ollama, LM
Studio and OpenAI-compatible endpoints, and adds or removes MCP servers.
Refusals are fixed sentences; user input is never echoed. **I'll just use
local AI** writes `~/.friday/connections_choice.json` and changes no privacy
or routing setting. Routes: `routes/connect_services.py`
(`/api/setup/connections/...`: test, local-only, ollama, endpoint, mcp add,
remove and sign-in), local same-origin user only. Keys are posted only to
`/api/providers/<name>/key` (`routes/platform.py`).

**Key storage.** `services/credential_store.py` encrypts each key (vault key
first, then Windows DPAPI; no plaintext tier) into
`~/.friday/providers/keys/<name>.key`, with an audit log.

**Other registries.** `services/connector_registry.py` enumerates OAuth
connectors, platform adapters and channels (a different taxonomy from the
guide). MCP servers are configured in `~/.friday/mcp_servers.json` (secrets
encrypted on save) and run by `mcp_client.py` (stdio and Streamable HTTP;
the older HTTP+SSE transport is not supported). `mcp_oauth.py` handles OAuth
2.1 sign-in for MCP servers; tokens are in `~/.friday/mcp_oauth/`.

## 12. First run

1. **Installer.** The Inno Setup program
   (`packaging/windows/installer/AgentFriday.iss`) runs
   `packaging/windows/install.ps1 -Unattended`, so the installer itself asks
   no setup questions. Its last page offers **Start Agent Friday now**, which
   runs the console launcher `Agent Friday.cmd`. The desktop and Start menu
   icons run `Agent Friday (tray).cmd`; while setup is not complete the tray
   hands over to the console launcher so the wizard can ask its questions
   (`packaging/windows/lib/Shortcuts.ps1`). Run directly without
   `-Unattended`, `install.ps1` runs the terminal wizard itself.
2. **CLI.** `cli.py cmd_start` checks for an existing install; if there is
   none it holds the setup lock (`setup_lock.py`, a Windows mutex) and offers
   `friday setup`. `cmd_setup` runs `setup_wizard.py` (`--quick` for a short
   run; only unanswered parts are asked). The wizard covers consent screens,
   the vault passphrase, routing, keys (`setup_brain.py`), voice and
   connectors, then writes `setup_complete`.
3. **Consent screens in the browser.** Copy in `services/onboarding_copy.py`
   (`collects`, `vault`, `third_party`, `calls`, `updates`). The vault
   passphrase is set here; `POST /api/setup-chat/begin` stores it.
4. **Setup chat.** `services/setup_chat.py` is a state machine with no model;
   stages are `setup_chat_copy.STAGES` (welcome through finish). State is in
   `<home>/setup_chat.json`; answers are in the encrypted setup profile.
   `services/setup_steps.py` applies each answer through the module that owns
   the setting (for example `apply_posture`, section 1). Options may be marked
   recommended; nothing is preselected. Key-shaped text is refused.
5. **Setup record.** `services/setup_record.py` appends to
   `<home>/setup/ledger.jsonl`: hash-chained lines with step, decision and who
   decided (`you`, `recommended`, `default`), labels and counts only.
   `verify()` reports a break and never repairs. `GET /api/setup-chat/record`.
6. **Complete.** `services/setup_complete.complete_setup()` (from the chat's
   finish or skip, and `POST /api/setup/complete`) stores keys and the
   passphrase, a whitelisted settings delta with the routing mode and
   `setup_complete: True`, `personality.json`, and the marker
   `~/.friday/.setup_complete`. `core._is_existing_install()` (marker, the
   setting, or `personality.json`) is what the status route and the scheduler
   read; the tray, CLI and wizard check the marker.

## 13. Workspaces and Laya contracts

- **Registry.** `static/workspace_registry.js` holds the workspace list as
  strict JSON between markers; `services/workspace_registry.py` parses the
  same bytes. Workspaces behind a held feature are hidden.
- **Laya manifests.** `resources/laya_manifests/<workspace>.json` (one per
  workspace) declare rows, verbs (tool, gate class, phrasings, undo), what
  Laya must not do, and jobs. They describe; `governance/action_gate` decides.
  `tests/test_laya_workspace_contracts.py` checks manifests against the code
  and UI and keeps a coverage floor that may only rise; a manifest and the
  code it describes change in the same commit (see
  [CONTRIBUTING.md](../../CONTRIBUTING.md)).
- **Desktop targets.** `services/desktop_targets.py` turns a phrase into a
  deep link the desktop accepts (workspace, email, file, wiki page, calendar,
  contact and other kinds). It backs the `navigate_to` tool and
  `POST /api/desktop/open`.
- `routes/agent_workspaces.py` is a different thing: browser workspaces the
  agent drives, local only.

## 14. Task visibility

- **One door for background model work.** `services/visible_jobs.visible_job`
  (context manager or decorator) creates the task record, journal entry,
  activity orb and trace id, and admits the work to the Local AI queue.
  Nested jobs become steps. `scripts/check_visible_jobs.py` guards it. See
  [visible jobs](../development/visible-jobs.md).
- **Local AI queue.** `services/local_scheduler.py` orders local work (voice,
  chat, api, user job, background), batches by model, holds background work
  until the owner is idle, and runs one background job at a time.
  `queue_snapshot()` is the single list behind the top-bar chip, the tray,
  `GET /api/local-queue` (`routes/work_plan.py`) and the voice tool.
  `services/seat_queue.py` runs one job per local seat; cloud seats are
  exempt.
- **Observation.** `services/task_journal.py` keeps an append-only journal per
  task under `<home>/tasks/` (encrypted at rest by default).
  `services/task_ledger.py` keeps a long task's working ledger.
  `services/task_watchdog.assess()` reports ok, overdue, stuck or kill.
  `routes/tasks.py` serves `/api/tasks` and per-task journal, events, stop and
  resume routes.
- **What the UI shows.** `services/activity_lane.py` defines **Needs you**,
  **Activity** (every task with model, status, queue position and Stop where
  honoured; finished tasks stay 30 minutes), **Updates** and **Receipts**,
  served by `routes/lanes.py`.

## 15. Release and gates

High level only; the steps are in
[docs/development/release-process.md](../development/release-process.md).

- **CI** (`.github/workflows/tests.yml`, on every push to main and every PR):
  pytest on Windows and Ubuntu (Python 3.11 and 3.12; import smoke, unit and
  API, security and egress suites), the repository guards
  ([repository guards](../development/repository-guards.md)), Playwright app
  specs on Windows, and a package build. `codeql.yml` runs CodeQL for Python
  and JavaScript.
- **Local full runs** go through `scripts/run_suite_guarded.py`: memory and
  disk floors, one suite lock, a worker cap, and a receipt keyed by tree SHA.
- **Lanes and quick or full gates.** Maintainers integrate work in lanes and
  gate each integration tree with a quick gate (the tests that touch what
  changed plus every rule and contract test) or a full gate (the whole suite;
  forced by conftest, packaging or requirements changes). That tooling lives
  outside this repository and is not documented here. When a local run is not
  possible, a draft PR opened only to run CI (never merged) is used as the
  proof.
- **Release.** Bump the version, `BUILD_SEQUENCE` and `RELEASE_TAG` in
  `release.py`; every check green on the exact commit; push the tag. The tag
  runs `.github/workflows/installer.yml`: build, a fresh-install test, an
  upgrade matrix from earlier releases, then the release job publishes the
  installer, its SHA-256 and a signed release manifest as the latest release.
  Pushing the tag is the release decision.

## 16. Held features

`services/held_features.py`: a feature held back from this release keeps its
code, routes and data, but nothing offers it and its routes answer "Not
enabled in this release". The `federation` switch (`held_features.federation`,
default off) holds the Marketplace, the economy, peer federation, federated
compute and defederation; `trust_agents` is a second switch. Buying is refused
in every configuration.

## Storage

Everything Friday keeps is under `~/.friday` (or `FRIDAY_HOME`). The full
layout, with what is encrypted, is in the
[configuration reference](../user-guide/configuration.md#what-lives-in-friday).
