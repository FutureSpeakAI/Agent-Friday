# Architecture

> Status: current for 5.14.0. Last verified against the code: 2026-09-24.
> Paths are relative to `src/agent_friday/` unless they start at the repository
> root. When this page and the code disagree, the code is right.

Agent Friday is a local Flask application with a single-page UI, a set of
local model runtimes it manages itself, and optional cloud providers. Every
model call to the cloud passes an egress gate, and every tool call passes one
governance checkpoint. More diagrams and subsystem detail are in
[docs/architecture/overview.md](docs/architecture/overview.md); the security
design is in [docs/security/threat-model.md](docs/security/threat-model.md).

## Processes

```
friday_tray.py  (system tray; Windows)
 ├─ push-to-transcribe keyboard hook (in the tray process)
 └─ server.py  (Flask app, 127.0.0.1:3000)
     ├─ llama-server × N      pinned model seats, one loopback port each (from 8090)
     ├─ Ollama (external)     leased seats, localhost:11434
     ├─ voice workers         python -m agent_friday.voice.worker, one per GPU voice engine
     ├─ ComfyUI (optional)    local image and video generation
     ├─ MCP servers           stdio child processes from ~/.friday/mcp_servers.json
     ├─ officecli.exe         run per document command
     └─ in-process listeners  phone ingress (127.0.0.1:3011), local-address proxy (443/80)
```

- **The tray** (`friday_tray.py`) starts the server as a child process with no
  console window, waits for `/health`, and restarts or stops it from its menu.
  It also runs the system-wide push-to-transcribe hook, because the tray is the
  one Friday process that outlives the browser. The desktop shortcut runs
  `python -m agent_friday.cli`, which starts the server without the tray.
- **The server** (`server.py`) binds to `127.0.0.1` on `FRIDAY_PORT` (default
  3000, next free port if busy). It registers every blueprint in `routes/` by
  auto-discovery (`_discover_and_register_blueprints`), falling back to the
  `ROUTE_MODULES` manifest in a frozen build, and starts the background
  services: scheduler, notification and network monitor, news archiver,
  connector health, credential sweep and the residency arbiter; the phone
  service starts when its routes load. `FRIDAY_TESTING=1` suppresses the
  background threads for tests.
- **Local model runtimes** are owned by the residency arbiter
  (`services/residency_arbiter.py`). Seats the plan pins run as `llama-server`
  processes the arbiter spawns, health-checks and terminates, so nothing else
  can evict them. Leased seats may use Ollama, where eviction is acceptable.
  Transitions are serial under one lock.
- **The phone ingress** (`phone/ingress.py`) is a separate WSGI application,
  with its own routes only, served on its own loopback socket by a thread in
  the server process. It imports nothing from `core`, so no Friday route is
  reachable through it, and it runs only while the phone is switched on.
- **The local-address proxy** (`services/local_proxy.py`) is an asyncio byte
  relay on loopback ports 443 and 80, opened only when turned on in Settings.

## A chat turn

1. **UI.** `index.html` posts to `/api/chat/stream` (Server-Sent Events) or
   `/api/chat`; voice uses the `/ws/voice-local` or `/ws/live` WebSocket.
   `/api/chat/send` is a second, older endpoint used by some surfaces.
2. **Authentication** (`core/__init__.py`). Direct loopback requests are the
   owner; a request that looks proxied (any `CF-*` header, a forwarding chain
   with a non-loopback address, an unknown forwarded host) must log in.
3. **Context assembly.** The system prompt is built by
   `_get_friday_system_prompt`, which requires an explicit provider and vault
   control; vault content is tier-gated for cloud providers
   (`privacy/vault_access.py`). The action permission policy is appended last,
   and override attempts in assembled context are stripped.
4. **Routing** (`routing/model_router.py`). Vault requests are routed first
   (local, or refused, while `vault_local_only` is on). Otherwise the seat you
   chose for the conversation or globally wins; the task classifier and the
   routing mode (`cloud_only`, `smart`, `local_preferred`, `local_only`) decide
   the rest. In `local_only`, a turn with no local seat is refused with an offer
   to answer in the cloud.
5. **Egress** (`services/model_router._seal_or_block`). Before any cloud call:
   the hard spending cap, a payload size ceiling, then
   `services/egress_gate.seal_outbound`, which scrubs PII and replaces or
   withholds private and sensitive content. Any failure blocks the send. Local
   providers pass through.
6. **The agent loop.** Anthropic models run the Claude tool loop in
   `services/agent.py`; every OpenAI-compatible provider, local or cloud, runs
   the shared `_oai_agentic_loop`. Both use one tool registry (`CLAUDE_TOOLS`)
   and send the model an index of tools with schemas loaded on demand.
7. **Tools** run through `_execute_tool` (below). Results are capped, dated
   with computed weekdays, and returned to the loop.
8. **Response.** The reply streams back with the model that answered; the turn
   is recorded in the conversation store, the cost meter and, if enabled, the
   encrypted reasoning-trace ledger (`services/reasoning_trace.py`).

## Tool dispatch and the governance hook chain

`_execute_tool(name, input, …)` in `services/agent.py` is the only way a tool
handler runs. `tests/unit/test_every_action_is_governed.py` discovers every
registered tool and every direct handler call site and fails on a bypass.

Pre-hooks run in priority order; a deny stops the chain and becomes the tool
result the model sees. Hooks marked critical cannot be disabled, and an
exception in one denies the call.

| Priority | Hook | Critical | What it does |
|---|---|---|---|
| 1 | `governance_rings` | yes | Privilege rings and subagent scope (`_governance_check`); a phone-origin turn is read-only. Provenance (`services/taint.py`): a sensitive argument that came from read content forces a card. Then `governance/action_gate.authorize`: cLaws integrity against the pin, classify internal / outward / forbidden, decide (allow, chat confirmation, card, or grant), write the signed receipt. Fails closed for outward actions. |
| 10 | `confirmation_gate` | yes | The chat yes/no for outward actions in a live conversation, bound to the action's fingerprint; a repeated ask escalates to a card. |
| 25 | `vault_zt` | yes | Ring-2 tools need an authenticated or background session. |
| 30 | `sandbox_policy` | no | `FRIDAY_SANDBOX_MODE` path confinement for `write_file`, and the `strict` command allowlist. |
| 40 | `rate_limiter` | no | Per-minute token buckets for ring 2 and ring 3. |
| 100+ | skill hooks | no | May only tighten. |

After the handler returns, `tool_receipts.record` notes that the call happened,
and post-hooks run: cost attribution (80), provenance recording of the result
as read content (85, critical), audit log (90), PII scrub (95) and file-grant
registration for `read_file` (96).

Approvals are durable records in `services/approvals.py`; grants for scheduled
work live in `~/.friday/governance/grants.json`; receipts are appended to
`~/.friday/decision-bom.jsonl` and HMAC-signed with the governance key
(`governance/proof_of_integrity.get_governance_key`). Sends that are not tool
calls (an approved email, phone alerts and codes, federated compute jobs) take
the same integrity check and receipt through `action_gate.authorize_external`
or `record_external`.

## Secrets, the vault and the keystore

- **Keystore** (`services/keystore.py`): a random 32-byte root key in
  `~/.friday/security/keystore.json`, unwrapped by default (owner-only ACL) or
  wrapped with an Argon2id passphrase. `services/credential_store.py` encrypts
  every credential with it (AES-256-GCM) and still reads blobs written by the
  older vault-key and DPAPI mechanisms.
- **Vault** (`privacy/`): the user's sensitive documents (finance, health,
  legal, family, and chosen wiki sections), encrypted with AES-256-GCM under a
  key derived by Argon2id from the vault passphrase. `privacy/vault_access.py`
  classifies vault content into tiers for prompt assembly;
  `privacy/cloud_consent.py` holds the one recorded choice that can turn cloud
  safeguards off.
- **Vault passphrase** (`services/vault_passphrase.py`): one resolver; stored
  in Windows Credential Manager and a DPAPI file.
- **Governance key** (`governance/proof_of_integrity.py`): Credential Manager,
  with a file fallback; never replaced automatically.

## Seats and residency

A **seat** is a model bound to a job: `reasoning`, `subagent`, `orchestrator`,
`heavy_hitter`, `function_manager`, `memory_manager`, `researcher`,
`creative_image`, `asr`, `tts` and so on (`capability_routing` in settings).
A seat is local or cloud by the model it names. The planner
(`services/model_plan.py`) sizes local models to the card (VRAM minus a 2.5 GiB
display reserve); the residency policy decides what should be resident, and
the arbiter makes it so, granting GPU leases to voice workers and image jobs.
`services/local_seats.py` answers which installed model serves a role and
announces any substitution. "I need my machine" in Settings releases the GPU.

## Scheduler

`services/scheduler.py` ticks every 60 seconds over `~/.friday/schedules.json`.
Built-in jobs (heartbeat, briefings, the news front page, daily creation) are
local-only by default: the run is skipped, with a reason, if no local seat is
serving, and `services/local_only_guard.py` refuses a cloud call at the
transport inside a local-only run. `idle_daily` jobs wait for the owner to be
away, inside a daily window, with the GPU free. Scheduled runs are
non-interactive, so an outward action needs a grant scoped to that schedule or
waits on a card. The weekly update check is a schedule seeded from the owner's
first-run answer.

## UI

`index.html` at the repository root is the served, authoritative UI: React
components compiled into the page, with Three.js for the 3D views, served by
the Flask app. `ui_parts/app.html` is a hand-maintained JSX mirror; every UI change
edits both, and the build tool refuses to regenerate `index.html` in a way that
drops components. See [docs/development/ui-build.md](docs/development/ui-build.md).
Any workspace except Settings can open as its own tab at `/w/<id>`.

## Where things live

| Concern | Module |
|---|---|
| App object, blueprint discovery, background services | `server.py` |
| Settings, auth, locality rule, shared state | `core/__init__.py` |
| Data folder resolution (`FRIDAY_HOME`) | `paths.py` |
| Agent loops, tool registry, `_execute_tool`, hooks | `services/agent.py`, `services/tool_hooks.py` |
| Per-action checkpoint, grants, receipts | `governance/action_gate.py` |
| Provenance of arguments | `services/taint.py` |
| Approval cards | `services/approvals.py` |
| Egress gate, classifier | `services/egress_gate.py`, `services/sensitivity_classifier.py` |
| Routing | `routing/model_router.py`, `services/model_router.py` |
| Residency | `services/residency_arbiter.py`, `residency_policy.py`, `model_plan.py` |
| Voice | `services/voice_engine.py`, `voice_session.py`, `voice_workers.py`, `local_voice.py`, `push_to_talk.py` |
| Mail | `services/gmail_*.py`, `services/mail_proposals.py` |
| Phone | `phone/` |
| Documents | `services/office_engine.py` |
| Local address | `services/local_address.py`, `local_ca.py`, `local_proxy.py` |
| Reasoning traces | `services/reasoning_trace.py`, `routes/traces.py` |
| Update check | `services/update_check.py` |
| CLI | `cli.py` (`friday`), `setup_wizard.py` |
| Windows installer | `packaging/windows/` |
