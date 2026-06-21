# Agent Friday — Architecture Inventory

> Competitive-analysis reference · source-verified from the codebase · vs. OpenClaw / Hermes Agent
> Generated from: `server.py`, `SELF.md`, and 13 subsystem/UI files.

## Headline Totals

| Metric | Count |
|---|---|
| **API endpoints** | **178** (177 Flask routes + 1 WebSocket `/ws/live`) |
| **Core subsystem modules** | **11** Python subsystems (+ external integrations) |
| **UI workspaces** | **18** (`*WS` React components) |
| **Holographic scenes** | **13** (`EVOLUTION_PATH`, one per "day" of maturity) |

---

## What Friday Is (from SELF.md)

Positions itself as **"Agent Friday," built by FutureSpeak.AI** — explicitly *not a chatbot* but a
**"sovereign personal AI"**: *"Jarvis meets Hunter S. Thompson's editor."* A Flask app on Anthropic
Claude (cloud) + optional Ollama (local), Three.js holographic UI, governed by **Asimov's cLaws**.
Core values: **privacy by default, local-first data sovereignty, editorial independence, AI amplifying
(not replacing) human agency.** Non-negotiable rule: **vault content is readable by local models only** —
cloud never receives TIER_2/TIER_3 data. Framed as the seed of a **federated agent Internet** meant to
displace Oracle/Microsoft/IBM/Salesforce/OpenAI.

---

## Subsystem-by-Subsystem

**1. `server.py` (13,272 lines) — Flask backbone.** Hosts all 178 endpoints, the Claude tool-using agent
loop (`_call_claude_agent()`, `max_iters=999`, per-agent "process orbs," mid-run steering via
`/api/agent/steer`), the Gemini Live WebSocket voice/vision bridge (`/ws/live`), SSE log streaming, and
permission-gated pyautogui computer control with a kill switch. *Notable:* **loopback-trust auth** —
localhost requests auto-authenticate; login only appears for remote (tunnel) clients; auth disabled
entirely when `FRIDAY_PASSWORD` is empty. All cognition modules import defensively so a missing subsystem
never blocks boot.

**2. `model_router.py` — Provider routing.** `ModelRouter.route()` decides local (Ollama) vs cloud
(Anthropic) per request by mode (`cloud_only` default, `local_preferred`, `smart`), task type
(`TaskType`), and vault detection. *Unique:* **vault detection overrides routing mode** — even in
`cloud_only`, a vault-touching request is force-routed local or refused. `CostTracker` frames local
routing as "estimated savings," unifying privacy and cost.

**3. `vault_access.py` — Policy gatekeeper.** `VaultAccessControl` classifies content into `Tier`
(PUBLIC/PRIVATE/SENSITIVE) and enforces local-only vault access; `gate_content()` gives cloud TIER_1 raw,
TIER_2 redacted-placeholder, TIER_3 nothing. `check_action()` does **per-tool-call zero-trust
authorization**, appending SHA-256-hashed (not plaintext) decisions to `access-log.jsonl`. *Unique:*
`regate_on_provider_change()` re-redacts mid-task if the provider switches.

**4. `epistemic_engine.py` — Self-grading feedback loop.** Scores every turn on four dimensions
(information gain, pushback, Socratic ratio, independence-fostering) via pure regex heuristics (no LLM
call), persists rolling averages, and injects self-improvement guidance back into the next system prompt.
*Unique:* **anti-sycophancy** — rewards disagreement/Socratic questioning, *penalizes doing the task for
the user*.

**5. `context_pruner.py` — "RAG over your own chat."** `ContextPruner` uses sentence-transformer
embeddings (`all-MiniLM-L6-v2`) to keep the most semantically relevant past turns (+ recent + system) for
a single call instead of naive oldest-first truncation. Full history stays intact. Lazy model load +
content-hash embedding cache keep it cheap.

**6. `context_compressor.py` — Headroom integration.** `ContextCompressor` wraps the Headroom library to
compress turn *contents* (tool output, JSON, code) before the Anthropic call, claiming 60–95% token
reduction. Runs after the pruner. *Notable:* **fail-open at every layer** — broken imports cached, even a
Windows cp1252 arrow-character `UnicodeEncodeError` is caught so compression can't crash a chat.

**7. `cognitive_memory.py` — Tamper-evident memory.** `CognitiveMemory` is an append-only store where
every write is SHA-256 hash-chained in `memory_ledger.jsonl`. `verify_chain()`,
`memory_rollback(timestamp)`, `memory_quarantine(source)`. *Unique:* **never hard-deletes** — rollback
moves files to `_rollback/`, quarantine excludes-but-keeps; blockchain-style tamper-evidence, zero deps.

**8. `dynamic_rings.py` — Single-call privilege rings.** OS-style Rings 0–3 (READ → OS control). Every
task starts at Ring 0; tools request one-shot elevation via `governance_elevate()` and **auto-drop to
Ring 0 after the call runs**. Ring 3 requires explicit user confirmation. Decisions HMAC-signed into
`privilege-log.jsonl`. *Unique:* privilege never silently persists across calls — stronger than sticky sudo.

**9. `proof_of_integrity.py` — AI Bill of Integrity.** `IntegrityEngine` signs/verifies an
`AgentIntegrityManifest` attesting cLaws, model config, tool inventory, vault status, epistemic health,
and memory-chain health. *Unique:* **dual signatures** — HMAC-SHA256 over cLaws (local governance) +
Ed25519 keypair (cross-agent federation). Graceful degradation if PyNaCl absent.

**10. `voice_personality.py` — Mood-adaptive voice.** `VoicePersonality.build_system_instruction()`
composes a Gemini Live system instruction from six mood profiles (curious, creative, protective, focused,
social, reflective) + an "affective dialog" block reading real-time user stress/excitement/fatigue from
voice. Two-layer: static mood baseline + dynamic sentiment adaptation. *(Minor flaw: runtime default mood
`"idle"` has no profile, silently falls back to default style.)*

**11. `notifications_engine.py` — Proactive notification queue.** Thread-safe single-file JSON queue
(`~/.friday/notifications.json`, RLock, 200-cap). Priority levels critical/high/medium/low with colors,
`dedupe_key`, and **deep-link `target` descriptors** routing to specific workspaces/threads/events.
*Unique:* **proactive chat injection** — a notification can become an unprompted assistant message.

---

## UI Layer

**17 Workspaces** (`wsMap`, grouped Life / Work / System):
`HomeWS · MessagesWS · CalendarWS · FamilyWS · HealthWS · FinanceWS` (Life) ·
`CareerWS · FuturespeakWS · ContactsWS · DraftWS · ContentWS` (Work) ·
`NewsWS · WikiWS · TrustWS · StudioWS · CodeWS · SystemWS` (System).

**13 Holographic Scenes** (`EVOLUTION_PATH`, Three.js maturity progression, "Day N"):
1 Genesis Lattice · 2 Sacred Sphere · 3 Shannon Network · 4 Geodesic Cathedral · 5 Lovelace Astrolabe ·
6 Von Neumann Tesseract · 7 Dirac Probability · 8 Mandelbrot Set · 9 Turing Möbius · 10 Ocean of Light ·
11 Fibonacci Nerve · 12 Transcendence · 13 Giga Earth (Rez).

---

## Complete Capability List

**Cloud/LLM:** Anthropic Claude (`claude-opus-4-8`) tool-using agent loop · Google Gemini (TTS,
image/music/video/poem gen, Live voice/vision) · Ollama local inference · model routing
(cloud_only/local_preferred/smart) · cost tracking.

**Voice/Vision:** Gemini Live WebSocket (PCM16 bidirectional audio + JPEG vision) · one-shot Gemini TTS ·
mood-adaptive voice personality · affective dialog (real-time sentiment).

**Security/Privacy:** Sovereign Vault (3-tier, local-only) · PII scrub-and-rehydrate · per-action
zero-trust vault authorization · dynamic privilege rings (0–3, single-call) · HMAC + Ed25519
proof-of-integrity attestation · hash-chained tamper-evident memory · loopback-trust auth ·
SHA-256-hashed audit logs.

**Memory/Knowledge:** cognitive memory ledger (rollback/quarantine) · personal wiki (CRUD + approval +
research) · embedding-based context pruning · Headroom context compression · trust graph.

**Cognition:** epistemic self-scoring (4 dimensions) with prompt-injection feedback loop · personality
evolution/maturity · self-improvement (SkillOpt, Karpathy auto-research, learnable skills).

**Productivity domains:** email/messages (classify/draft/act) · calendar (prep, enrich, quick-add) · news
briefings + front-page generation + source trust · career/job tracker + application engine ·
finance/health/family workspaces · sites/portfolio manager · contacts research ·
outreach · content pipeline · todos/flow engine.

**Dev/creative:** vibe-code studio · git ops (diff/branch/commit/PR/push/pull) · repo scan · file
read/list · code plan/apply/kill · creative generation (image/music/code-art/poem/video).

**Computer control:** permission-gated pyautogui (mouse/keyboard) with persisted permission + hard kill
switch · Google OAuth (Gmail + Calendar).

**Federation thesis:** Ed25519 peer attestation · "Liquid UI" app generation · Asimov's Mind Federation
roadmap.

---

## Endpoint Surface (178 total, by domain)

| Domain | Endpoints |
|---|---|
| Auth / static / shell | 9 |
| Agent / tasks / processes | 5 |
| Career ops | 4 |
| Evolution / briefings / news / sources | 16 |
| Messages (email/inbox) | 6 |
| Calendar | 9 |
| Google auth (OAuth) | 4 |
| Status / system / cognition | 7 |
| Memory / governance / integrity | 9 |
| Wiki / knowledge | 11 |
| Context engine (memory/compression) | 7 |
| Creations | 6 |
| Finance / health / personal data | 9 |
| Jobs / email | 4 |
| Creative generation | 5 |
| Vibe-code / dev studio | 5 |
| Logs (SSE) | 3 |
| Repos / git / files | 12 |
| Code planning/execution | 6 |
| Setup / settings | 4 |
| Chat | 6 |
| Ollama (local models) | 3 |
| Voice / audio | 2 |
| Notifications | 6 |
| Analysis / personality / trust editing | 4 |
| Todos | 6 |
| Drafts / content | 4 |
| Flow engine | 3 |
| Contacts | 3 |
| Routines | 2 |
| Outreach | 4 |
| Content pipeline | 3 |
| FutureSpeak (business workspace) | 10 |
| Computer control | 2 |
| WebSocket (`/ws/live`, Gemini Live) | 1 |

---

## Competitive Posture (summary)

Friday's distinctiveness clusters in three areas:

1. **Architecturally-enforced data sovereignty** — router + vault gate + PII scrub + re-gate-on-switch.
   The local-only guarantee is *structural*, not a configurable setting.
2. **Cryptographic governance & inter-agent trust** — HMAC-signed cLaws, single-call privilege rings,
   hash-chained tamper-evident memory, dual-signature (HMAC + Ed25519) attestation.
3. **Federation / anti-SaaS thesis** — user-owned locally-generated apps ("Liquid UI") and a federated
   network of sovereign agents, explicitly aimed at displacing centralized SaaS.

Probe points for the OpenClaw / Hermes comparison: whether either matches the *structural* (non-optional)
local-only privacy guarantee, and the per-action zero-trust + attestation trust model.
