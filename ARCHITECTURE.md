# Architecture

This page describes how Agent Friday™ Beta 1.2 (version `1.2.0b1`) is put
together, for people who work on the code. Paths are relative to
`src/agent_friday/` unless they start at the repository root. When this page
and the code disagree, the code is right; please fix the page.

Friday is a local Flask application with a single-page UI, local model
runtimes it manages itself, and optional cloud providers. Out of the box the
routing mode is `cloud_only` (`model_routing.mode` in `DEFAULT_SETTINGS`,
`core/__init__.py`); the privacy answer in first-run setup rewrites it. Friday
can run fully local when the owner chooses **Only on this PC** and has a local
model. Every cloud model call passes the egress gate, and every tool call
passes one governance checkpoint.

More detail:

- [docs/architecture/overview.md](docs/architecture/overview.md): each
  subsystem in detail (consent, gates, approvals, scheduler, memory, search,
  vault key, Laya, routing, connections, first run, workspaces, task
  visibility, release).
- [docs/architecture/diagram.md](docs/architecture/diagram.md): Mermaid
  diagrams of the component layout and of a chat turn through the gates.
- [docs/security/threat-model.md](docs/security/threat-model.md): the
  security design.

## Processes

```
friday_tray.py  (system tray; Windows)
 ├─ push-to-transcribe keyboard hook (in the tray process)
 └─ server.py  (Flask app, 127.0.0.1:3000)
     ├─ llama-server × N      pinned model seats, one loopback port each (from 8090)
     ├─ Ollama (external)     leased seats, localhost:11434
     ├─ voice workers         python -m agent_friday.voice.worker, one per GPU voice engine
     ├─ ComfyUI (optional)    local image and video generation
     ├─ MCP servers           stdio children or Streamable HTTP, from ~/.friday/mcp_servers.json
     ├─ officecli.exe         run per document command
     └─ in-process listeners  phone ingress (127.0.0.1:3011), local-address proxy (443/80)
```

- **The tray** (`friday_tray.py`) starts the server as a child process with no
  console window, waits for `/health`, and restarts or stops it from its menu.
  It also runs the push-to-transcribe hook. The installed desktop and Start
  menu icons run `Agent Friday (tray).cmd`. While setup is not complete the
  tray hands over to the console launcher, `Agent Friday.cmd`
  (`python -m agent_friday.cli`), so the setup wizard can ask its questions.
  `friday_tray.py` and `server.py` at the repository root are thin shims over
  the package.
- **The server** (`server.py`) binds to `127.0.0.1` on `FRIDAY_PORT` (default
  3000, next free port if busy). It registers every blueprint in `routes/` by
  auto-discovery (`_discover_and_register_blueprints`), falling back to the
  `ROUTE_MODULES` manifest in a frozen build, and starts the background
  services (scheduler, notifications, connector health, credential sweep, the
  residency arbiter and others). `FRIDAY_TESTING=1` suppresses background
  threads for tests.
- **Local model runtimes** are owned by the residency arbiter
  (`services/residency_arbiter.py`). Pinned seats run as `llama-server`
  processes the arbiter spawns, health-checks and stops. Leased seats may use
  Ollama. Transitions are serial under one lock.
- **The phone ingress** (`phone/ingress.py`) is a separate WSGI application on
  its own loopback port, with its own routes only. It runs only while the
  phone is switched on.
- **The local-address proxy** (`services/local_proxy.py`) is a byte relay on
  loopback ports 443 and 80, opened only when turned on in Settings.

## A chat turn

The diagram version is in [diagram.md](docs/architecture/diagram.md#2-a-chat-turn-through-the-gates).

1. **UI.** `index.html` posts to `/api/chat` or `/api/chat/stream`; the
   stream endpoint runs the same `chat()` on a worker thread and streams
   tokens. `/api/chat/send` is an older endpoint used by some surfaces; it
   does not run the Laya reflexes. Voice uses `/ws/voice-local` or `/ws/live`.
2. **Authentication** (`core/__init__.py`). Direct loopback requests are the
   owner; a request that looks proxied must log in.
3. **Intake.** A key pasted into the message is withheld before anything keeps
   it (`services/pasted_secrets.py`, decorator in `routes/chat.py`).
4. **Reflexes** (`services/reflex_chat.py`). A typed whole "yes" or bare "no"
   on the owner's page answers a waiting card with no model call. Other Laya
   steps are opt-in or shadow ([overview §9](docs/architecture/overview.md#9-laya-what-is-live-and-what-is-trial)).
5. **Routing** (`routing/model_router.py`). Vault requests first (local while
   `vault_local_only` is on), then the chosen seat, then the routing mode
   (`local_only`, `local_preferred`, `cloud_preferred`, `cloud_only`). In
   `local_only` a turn with no local seat is refused with an offer to switch.
6. **Context.** `services/model_router._get_friday_system_prompt` builds the
   prompt. It starts with the live privacy summary
   (`services/privacy_policy_summary.py`), adds the memory card frozen for
   this conversation (`services/prompt_freeze.py`), and gates vault content
   for cloud providers (`privacy/vault_access.py`).
7. **Egress** (`services/model_router._seal_or_block`). Before any cloud call:
   the hard spending cap, a size ceiling, then
   `services/egress_gate.seal_outbound`: the never-send floor (blocks the whole
   call in every mode), then, unless consent is `cloud_unrestricted`, the
   identifier scrub and tier withholding. Any failure blocks the send. Local
   providers pass through.
8. **The agent loop.** Anthropic models run the Claude tool loop in
   `services/agent.py`; every OpenAI-compatible provider, local or cloud, runs
   `_oai_agentic_loop`. Both use one tool registry (`CLAUDE_TOOLS`).
9. **Tools** run through `_execute_tool` and the hook chain (below). A tool
   that needs the owner raises an approval card on the first ask.
10. **Response.** Claims of completed work are checked against the turn's
    receipts (`services/completion_receipts.py`); the reply returns with the
    model that answered (`services/answer_truth.py`), and the turn is recorded.

## Tool dispatch and the governance hook chain

`_execute_tool(name, input, …)` in `services/agent.py` is the only way a tool
handler runs. `tests/unit/test_every_action_is_governed.py` fails on a bypass.
Hooks are registered in `services/agent.py` (registry in
`services/tool_hooks.py`). Pre-hooks run in priority order; a deny stops the
chain and becomes the tool result the model sees. Critical hooks cannot be
disabled, and an exception in one denies the call.

| Phase | Priority | Hook | Critical | What it does |
|---|---|---|---|---|
| pre | 1 | `governance_rings` | yes | Refuses key material first (`_hook_credential_refusal`); privilege rings and subagent scope; taint (`services/taint.py`); then `governance/action_gate.authorize`: cLaws pin check, classify (`internal`, `observe`, `outward`, `forbidden`), decide (`allow`, `confirm`, `card`, `deny`), signed receipt. Fails closed for outward actions. |
| pre | 5 | `call_verifier` | no | Escalate-only: the verifier's reasons to ask become a confirmation or, with nobody in the chat, a card. |
| pre | 10 | `confirmation_gate` | yes | A `confirm` verdict becomes a `tool_confirm` card on the first ask, with the question also shown in chat; a second ask goes to the card only. |
| pre | 25 | `vault_zt` | yes | Ring-2 (network and vault tier) tools need an authenticated or background session. Defence in depth; the ring check in hook 1 already covers it. |
| pre | 30 | `sandbox_policy` | no | `FRIDAY_SANDBOX_MODE` path confinement and command allowlist. |
| pre | 40 | `rate_limiter` | no | Per-minute token buckets. |
| post | 80 | `cost_attribution` | no | Cost meter. |
| post | 85 | `taint_record` | yes | Records the result as read content. |
| post | 90 | `audit_log` | no | Audit log. |
| post | 95 | `pii_scrub` | no | Scrubs the result. |
| post | 96 | `file_grant_registration` | yes | For `read_file` only. |
| post | 99 | `call_verifier_record` | no | Records the call for the verifier. |

`services/tool_receipts.record` notes each call that ran. Approval records are
in `services/approvals.py`; decision receipts are appended to
`~/.friday/decision-bom.jsonl`, HMAC-signed with the governance key. The card
pipeline, chat yes/no, pre-approved sends and receipts are described in
[overview §4](docs/architecture/overview.md#4-approvals-cards-and-receipts).

## Privacy, in one paragraph

Mode, consent and local-only records are three settings with one owner each
(`routing/model_router.py`, `privacy/cloud_consent.py`,
`privacy/vault_policy.py`). The setup posture answer writes all of them
(`services/setup_steps.apply_posture`). The never-send floor
(`services/judgment_gate.never_send_hits`, including raw-record hash holds)
blocks a cloud call in every mode; card numbers and bank or account numbers
are on every never-send list by default (`services/never_send_defaults.py`,
matched by pattern, removable on the Locks page). Other identifiers and
credentials are masked by the identifier scrub except under unrestricted
consent, refused in tool inputs in every mode by the vault gate, and keys are
withheld at chat intake. An explicit owner choice of Cloud only (Settings >
Models clicked on the page, or setup; `model_routing.mode_chosen_via`) is
unrestricted consent; the factory default is not. Details:
[overview §1-3](docs/architecture/overview.md#1-routing-mode-and-cloud-consent).

## Secrets, the vault and the keystore

- **Keystore** (`services/keystore.py`): a random 32-byte root key in
  `~/.friday/security/keystore.json`. `services/credential_store.py` encrypts
  every credential (AES-256-GCM; vault key, then DPAPI; no plaintext tier).
- **Vault** (`privacy/`): sensitive documents, encrypted with AES-256-GCM
  under an Argon2id key from the vault passphrase (`privacy/vault_crypto.py`).
- **Vault passphrase** (`services/vault_passphrase.py`): one resolver over
  several stored copies; when they disagree it uses the one that opens the
  most files. `scripts/vault_key_doctor.py` reports and repairs.
- **Governance key** (`governance/proof_of_integrity.py`): Credential Manager,
  with a file fallback; never replaced automatically.

## Seats and residency

A **seat** is a model bound to a job (`capability_routing` in settings). The
planner (`services/model_plan.py`) sizes local models to the card; the
residency policy decides what should be resident, and the arbiter makes it
so. `services/local_seats.py` answers which installed model serves a role.
See [overview §10](docs/architecture/overview.md#10-seats-and-model-routing).

## Voice

- **Local voice** (`/ws/voice-local` in `routes/voice.py`) runs speech
  recognition, the model turn and speech on this PC. CPU engines run in the
  server process (`services/local_voice.py`); GPU engines run as
  `python -m agent_friday.voice.worker` processes (`services/voice_workers.py`)
  holding GPU leases from the arbiter. Local voice routes tools through the
  Laya rule router by default (`voice_tool_routing="laya"`).
- **Cloud voice** (`/ws/live`) streams to a cloud voice provider; its prompt
  carries the same privacy summary.
- **Push-to-transcribe** (`services/push_to_talk.py`) is a keyboard hook in the
  tray process.

## UI

`index.html` at the repository root is the served, authoritative UI (React
components compiled into the page). `ui_parts/app.html` is a hand-maintained
JSX mirror; a UI change edits both. Static assets and page scripts are in
`static/`. See [docs/development/ui-build.md](docs/development/ui-build.md).
Any workspace except Settings can open as its own tab at `/w/<id>`.

## Settings

Defaults live in `DEFAULT_SETTINGS` in `core/__init__.py`. Changed values are
stored in `settings.json` in the data folder, `~/.friday` unless
`FRIDAY_HOME` names another (`paths.py`). Keys not in `DEFAULT_SETTINGS` are
dropped on load; some keys (cloud consent, `laya_safe_writes`) cannot be set
through a generic settings write. `scripts/check_settings_readers.py` checks
that each key has a reader.

## Installer and first run

The Windows setup program is built from `packaging/windows/` (see
`packaging/windows/README.md`). The Inno Setup program runs
`install.ps1 -Unattended`; first-run questions are asked by Friday, not the
installer: the CLI wizard (`setup_wizard.py`), the consent screens, then the
setup chat (`services/setup_chat.py`), recorded in a hash-chained setup ledger
and finished by `services/setup_complete.py`. See
[overview §12](docs/architecture/overview.md#12-first-run). Releases are
ordered by `BUILD_SEQUENCE` in `release.py`
([release process](docs/development/release-process.md)).

## Tests and documentation

- `tests/unit/` is fast and needs no server. `tests/api/` uses the Flask test
  client with model calls stubbed. `tests/security/`,
  `tests/test_egress_adversarial.py` and `tests/test_judgment_gate.py` cover
  the egress boundary. Browser tests are Playwright specs under `tests/app/`.
  See [tests/README.md](tests/README.md).
- Repository guards are scripts in `scripts/`; see
  [repository guards](docs/development/repository-guards.md).
- Documentation starts at [docs/README.md](docs/README.md).

## Where things live

| Concern | Module |
|---|---|
| App object, blueprint discovery, background services | `server.py` |
| Settings, auth, shared state | `core/__init__.py` |
| Data folder resolution (`FRIDAY_HOME`) | `paths.py` |
| Agent loops, tool registry, `_execute_tool`, hooks | `services/agent.py`, `services/tool_hooks.py` |
| Per-action checkpoint, grants, receipts | `governance/action_gate.py`, `governance/send_grants.py` |
| Approval cards | `services/approvals.py`, `approval_feed.py`, `approval_executor.py` |
| Mode, consent, local-only records | `services/cloud_intent.py`, `privacy/cloud_consent.py`, `privacy/vault_policy.py` |
| Privacy summary in every prompt | `services/privacy_policy_summary.py` |
| Egress gate, never-send floor, classifier | `services/egress_gate.py`, `services/judgment_gate.py`, `services/sensitivity_classifier.py` |
| Vault gate | `privacy/vault_access.py` |
| Raw records, file grants, Locks page | `services/raw_records.py`, `services/file_grants.py`, `services/locks.py` |
| Routing and model calls | `routing/model_router.py`, `services/model_router.py` |
| Seats and residency | `services/seat_*.py`, `services/residency_arbiter.py`, `model_plan.py` |
| Laya | `services/laya*.py`, `services/reflex_chat.py`, `services/chat_tool_routing.py` |
| Scheduler | `services/scheduler.py`, `services/schedule_math.py`, `services/email_jobs.py` |
| Memory card | `services/core_memory.py`, `core_memory_tools.py`, `memory_review.py`, `prompt_freeze.py` |
| Conversation search | `services/conversation_fts.py`, `conversation_search_tool.py` |
| Knowledge graph | `services/knowledge_graph/` |
| Connections | `services/connections_guide.py`, `connections_setup.py`, `routes/connect_services.py`, `services/provider_registry.py`, `mcp_client.py` |
| First run | `cli.py`, `setup_wizard.py`, `services/setup_chat.py`, `setup_steps.py`, `setup_record.py`, `setup_complete.py` |
| Task visibility | `services/visible_jobs.py`, `local_scheduler.py`, `task_journal.py`, `activity_lane.py` |
| Voice | `services/voice_engine.py`, `voice_session.py`, `voice_workers.py`, `local_voice.py`, `push_to_talk.py`, `voice/worker.py` |
| Mail | `services/gmail_api.py`, `gmail_read.py`, `gmail_send.py`, `mail_proposals.py` |
| Phone | `phone/` |
| Held features | `services/held_features.py` |
| Windows installer | `packaging/windows/` |
