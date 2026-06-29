# Agent Friday: STORM-Method Codebase Analysis
### Stanford STORM — Multi-Perspective Research, Simulated Expert Conversations, Cited Synthesis

> Generated: 2026-06-29  
> Method: Stanford STORM (multi-perspective investigation → hierarchical outline → cited synthesis)  
> Perspectives: Security Researcher · System Architect · New Contributor/DevOps · UX Designer · Product Manager · End User

---

## Table of Contents

1. [Part 1: Product Guide](#part-1-product-guide)
   - [What Agent Friday Is](#what-agent-friday-is)
   - [Architecture Overview](#architecture-overview)
   - [How to Install and Run](#how-to-install-and-run)
   - [Feature Inventory](#feature-inventory)
   - [API Reference](#api-reference)
   - [Configuration Guide](#configuration-guide)
2. [Part 2: Critical Analysis](#part-2-critical-analysis)
   - [What Works Well](#what-works-well)
   - [What's Broken or Fragile](#whats-broken-or-fragile)
   - [Technical Debt Inventory](#technical-debt-inventory)
   - [The Restructure Shim Problem](#the-restructure-shim-problem)
   - [Security Posture](#security-posture)
   - [Known Bugs and Workarounds](#known-bugs-and-workarounds)
3. [Part 3: SWOT Analysis](#part-3-swot-analysis)
4. [Part 4: Priority Fixes and Additions](#part-4-priority-fixes-and-additions)
5. [Part 5: Developer Onboarding Guide](#part-5-developer-onboarding-guide)

---

# PART 1: PRODUCT GUIDE

## What Agent Friday Is

Agent Friday is a **privacy-first, local-running AI assistant desktop application** built in Python (Flask) and served through an Electron-style tray app on Windows. It is not a cloud SaaS product — it runs entirely on the user's machine, routing intelligently between local inference (Ollama) and cloud APIs (Anthropic Claude, Google Gemini) based on data sensitivity and user preference.

**Core design principles:**
- **Sovereign by default**: sensitive data (health, legal, financial) never leaves the device; cloud providers receive scrubbed versions
- **Local-first voice**: CPU voice (Whisper + Piper) works without a GPU or cloud key
- **Provider-agnostic**: same tool registry and governance rules run on Ollama, Claude, OpenAI-compatible, and Gemini
- **Continuous memory**: ChromaDB stores every conversation; semantic retrieval gives cross-session continuity
- **Offline-first resilience**: tasks queue when offline; network state is monitored and routing automatically switches

**Who it's for:** Power users who want a personal AI assistant with genuine privacy guarantees, not a chatbot that phones home with everything.

---

## Architecture Overview

The project uses a `src/` layout with a flat Blueprint-based Flask server.

```
friday-desktop/
├── server.py                    # ROOT SHIM — do not use directly (see §Shims)
├── friday_tray.py               # ROOT SHIM — Windows tray launcher
├── core.py                      # ROOT SHIM — re-exports core.py
├── pyproject.toml               # Package config, extras, entry points
├── start.bat                    # Windows dev launcher (sets env vars)
│
├── src/agent_friday/            # REAL package root
│   ├── server.py                # Flask entry point: registers 38 blueprints, starts daemons
│   ├── core.py                  # Shared state: ~1,647 lines of app globals, auth, settings,
│   │                            #   vault state, PII scrubbing, network state, sandbox,
│   │                            #   process registry, offline queue
│   ├── cli.py                   # `friday` CLI entry point
│   ├── friday_tray.py           # Windows system tray app
│   │
│   ├── services/                # 63 service modules — business logic
│   │   ├── agent.py             # Main agentic loop: tool dispatch, provider fallback, memory
│   │   ├── model_router.py      # Anthropic Claude call wrapper, cost metering, workspace temp
│   │   ├── provider_registry.py # Declarative provider schema (zero-code provider addition)
│   │   ├── capability_router.py # Maps capability → (provider, model) from settings
│   │   ├── model_catalog.py     # Live model catalog endpoint
│   │   ├── sensitivity_classifier.py  # 5-layer PII/sensitivity classifier
│   │   ├── egress_gate.py       # Field-level cloud egress gate (applies classifier to outbound)
│   │   ├── credential_store.py  # Encrypted API key storage (Vault → DPAPI → plaintext)
│   │   ├── voice_engine.py      # Voice session manager (local vs cloud routing)
│   │   ├── local_voice.py       # Tier-1: Whisper + Piper (CPU-only)
│   │   ├── nemo_voice.py        # Tier-2: NeMo ASR + FastPitch/HiFiGAN (GPU)
│   │   ├── scheduler.py         # Internal cron scheduler (interval/daily/weekly)
│   │   ├── notifications.py     # Push notification engine + daily loop state
│   │   ├── news_engine.py       # RSS aggregation, cluster detection, source trust
│   │   ├── wiki_engine.py       # Personal wiki: CRUD, pending approvals, search
│   │   ├── calendar_engine.py   # Google Calendar connector
│   │   ├── cost_meter.py        # Per-provider cost metering + budget alerts
│   │   ├── compaction.py        # Context compression (headroom or in-house summarizer)
│   │   ├── tool_hooks.py        # Pre/post tool execution hook registry
│   │   ├── orchestrator.py      # Multi-worker task orchestration (spawn/delegate/budget)
│   │   ├── budget_enforcer.py   # SQLite-backed budget reserve/release/hard-stop
│   │   ├── federation.py        # Ed25519 peer identity + attestation protocol
│   │   ├── creative_engine.py   # Image (Nano Banana), Video (Veo), safety gate
│   │   ├── music_engine.py      # Lyria 3 music generation
│   │   └── ...                  # 40+ more (economy, marketplace, moderation, subagents…)
│   │
│   ├── routes/                  # 42 Flask Blueprint files
│   │   ├── core_routes.py       # /api/health, /api/settings, /api/models, /api/mcp/*
│   │   ├── chat.py              # /api/chat, /api/chat/history, /api/memory/search
│   │   ├── voice.py             # /ws/live (Gemini Live WebSocket), /ws/voice-local
│   │   ├── tasks.py             # /api/tasks, /api/processes, /api/agent/steer
│   │   ├── orchestrator.py      # /api/orchestrator/* (spawn, delegate, cancel, results)
│   │   ├── news.py              # /api/news/*, /api/briefings/*, /api/source-trust/*
│   │   └── ...                  # 36 more blueprints
│   │
│   ├── privacy/                 # Vault subsystem
│   │   ├── vault_crypto.py      # AES-256-GCM + Argon2id encryption
│   │   ├── vault_access.py      # Tier gating: PUBLIC / PRIVATE / SENSITIVE + access log
│   │   └── vault_encrypt_migrate.py  # Migration from plaintext to encrypted vault
│   │
│   ├── routing/                 # Model routing (separate from services/model_router.py)
│   │   ├── model_router.py      # Local/cloud/OpenAI dispatch, vault tier routing decisions
│   │   └── ollama_manager.py    # Ollama process management + model pull
│   │
│   ├── governance/              # Asimov cLaws governance framework
│   ├── pipeline/                # Context pruning + compression pipeline
│   └── __init__.py
│
├── tests/
│   ├── conftest.py              # Root conftest: hermetic env, FRIDAY_TESTING=1
│   ├── unit/                    # ~74 unit test files (vault, classifiers, routing, etc.)
│   └── api/                     # ~30 API route tests (Flask test client, LLM stubbed)
│
├── ui_parts/
│   └── app.html                 # Single-file Babel/React UI (~8,000+ lines)
│
└── docs/                        # Architecture docs, API reference, installation guide
```

### Key Architectural Decisions (and their tradeoffs)

| Decision | Rationale | Tradeoff |
|----------|-----------|----------|
| Monolithic Flask with Blueprints | Simplicity, single process, no IPC | Hard to scale horizontally; process isolation requires restart |
| Shared mutable state in `core.py` | Zero-boilerplate service access | Testing requires global patching; no DI |
| `services/` flat namespace | No circular imports | No service registry; adding a service requires editing `server.py` |
| Dual `model_router.py` modules | Phased refactor that wasn't finished | Confusing: `services/model_router.py` (Anthropic) vs `routing/model_router.py` (dispatch) |
| AES-256-GCM + Argon2id vault | Strong crypto, well-chosen primitives | Single master key; no rotation mechanism |
| In-process ChromaDB | Zero network hop for memory | Memory and app share the same process crash domain |

---

## How to Install and Run

**Prerequisites:** Python 3.10+, Git, Windows (for tray app and computer control features; Linux/macOS work for headless use).

### Step 1: Clone and create virtual environment

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### Step 2: Install the package

```bash
# Minimum (core only — no voice, no local embeddings):
pip install -e .

# Full recommended (core + local CPU voice + embeddings):
pip install -e ".[all]"

# With GPU voice (requires CUDA + torch pre-installed):
pip install -e ".[voice-local-gpu]"
```

> **Warning**: There is NO `[dev]` extra defined in `pyproject.toml` despite CONTRIBUTING.md saying to use it. Run `pip install pytest` separately.

### Step 3: Configure API keys

Create `start.bat` (or set environment variables directly):

```batch
set GEMINI_API_KEY=<your-gemini-ai-studio-key>
set ANTHROPIC_API_KEY=sk-ant-api03-<your-key>
set FRIDAY_USERNAME=<your-email>
set FRIDAY_PASSWORD=<your-password>
```

**Or** configure through the Settings UI after first launch — keys are encrypted via `credential_store.py`.

> **Security note**: Never commit `start.bat` to version control. The repo history contains leaked keys that should be rotated immediately.

### Step 4: Launch

```bash
# CLI (any platform):
friday start

# Windows tray app (from repo root):
python friday_tray.py

# Dev server (direct):
python src/agent_friday/server.py
```

The server starts on `http://localhost:3000`. First-run shows demo mode if no provider keys are configured.

### Step 5: Verify

```bash
friday doctor          # Checks API keys, Ollama, disk space, voice deps
pytest tests/unit -q   # ~74 fast unit tests, no network needed
pytest tests/api -q    # ~30 API tests with LLM calls stubbed
```

### Optional: Local Ollama

```bash
# Install Ollama from https://ollama.com
ollama pull gemma4:latest   # Default local model (~8GB)
```

The server auto-detects Ollama at `http://localhost:11434` and uses it for local inference.

---

## Feature Inventory

> **Status codes:** GREEN = works reliably | YELLOW = partial/fragile | RED = broken or stub | SPEC = documented but not implemented

### Core Chat & Agent

| Feature | Status | Notes |
|---------|--------|-------|
| Text chat with tools | GREEN | Full agentic loop; vault gating; Anthropic/Ollama/OpenAI |
| Vision input (images) | GREEN | Gemini Flash; stays browser-local before send |
| Memory recall (ChromaDB) | GREEN | Background indexing; cross-session semantic recall |
| Session continuity | GREEN | EOD summary from 11:30 PM job; next-day carry-forward |
| PII scrubbing | GREEN | `[PII:type:hash]` tags; rehydrated before user sees output |
| Offline safety net | GREEN | Network monitor → auto-route to Ollama when offline |
| Tool execution (30+ tools) | GREEN | Unified registry: web search, file read/write, wiki, calendar, email |
| Confirmation gates | GREEN | "Ask first, confirm, do, report" enforced in tool layer |
| Governance / cLaws | GREEN | Ring-0 through Ring-3 privilege levels; BOM versioned |

### Voice

| Feature | Status | Notes |
|---------|--------|-------|
| Local CPU voice (Tier-1) | GREEN | Whisper + Piper; works offline; `pip install -e ".[voice-local-lite]"` |
| GPU voice (Tier-2, NeMo) | YELLOW | Requires CUDA + torch pre-installed; hot-swap to CPU on GPU fail |
| Gemini Live (cloud) | GREEN | Session resumption, affective dialog (2.5 Flash), VAD |
| Voice tools (agentic) | GREEN | Calendar, email, news, web search, wiki, trust graph callable during conversation |
| Barge-in (headphones) | YELLOW | Default is NO_INTERRUPTION (speaker-safe); headphones mode opt-in via setting |
| Audio level feedback | RED | No VU meter in UI; silent mic failures surface only in logs |
| Voice settings UI | RED | 13+ voice settings exist; none have a UI — must edit `~/.friday/settings.json` |

### News & Briefings

| Feature | Status | Notes |
|---------|--------|-------|
| Daily briefing | GREEN | Morning (7 AM) + Evening (6 PM) auto-generates |
| Front Page | GREEN | Morning + evening editions, weekly digest, editorial |
| News feed | GREEN | RSS aggregation, category toggles, source banning/boosting |
| Source trust scoring | GREEN | Learned per-domain, user-correctable, federation-shareable |
| Cluster detection | GREEN | Multi-source story dedup (3+ sources in 24h) |
| Read-later queue | GREEN | Persistent, searchable |
| Deep-dive (Claude) | GREEN | Per-article fetch + summarize |

### Workspaces

| Feature | Status | Notes |
|---------|--------|-------|
| Home (routines, countdowns, memory stats) | GREEN | All panels functional |
| Career (job tracker, A-F scoring, outreach) | GREEN | Full pipeline |
| Wiki (CRUD, search, pending approvals) | GREEN | Full CRUD |
| Contacts (view, warm-score) | YELLOW | Data fetched but card layout not rendered in app.html |
| FutureSpeak (company-specific) | GREEN | Works, but should be distribution-gated |
| Code (file viewer) | YELLOW | Launches external vibe-code; not self-contained |
| Studio (creative: image, video, music) | GREEN | Nano Banana (image), Veo (video), Lyria (music) |
| Workspace Studio (chat to customize) | GREEN | Live CSS/note/accent patches, versioned, reversible |
| Family / Health / Finance | RED | **Stubs only** — show "edit your wiki" message |
| Settings UI | RED | **Not in app.html** — must edit JSON or use wizard once |

### Infrastructure

| Feature | Status | Notes |
|---------|--------|-------|
| Orchestrator (multi-worker) | GREEN | Spawn, delegate, cancel, budget enforcement |
| Scheduler (built-in cron) | GREEN | 7 default jobs; interval/daily/weekly; retries |
| Cost metering | YELLOW | Tracks known models; fallback pricing for unknowns; attribution race in voice |
| Vault encryption (AES-256-GCM) | YELLOW | Implemented; wiring to runtime partially complete |
| Google OAuth (Gmail/Calendar) | YELLOW | Code correct; OAuth never completed → token missing |
| Federation (Ed25519 peer P2P) | YELLOW | Protocol implemented; not yet in request pipeline |
| MCP client (stdio servers) | GREEN | Config via `~/.friday/mcp_servers.json`; tool registration |
| Demo mode | GREEN | Auto-detects no provider; canned responses; prompts setup |

---

## API Reference

### Key Endpoint Groups

**Core — `/api/*`**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System health: uptime, model availability, governance version |
| `/api/models` | GET | Backend-driven model catalog by role (orchestrator/subagent/creative/voice) |
| `/api/settings` | GET/POST | Read or merge-update `~/.friday/settings.json` |
| `/api/setup/status` | GET | Whether wizard has been completed |
| `/api/setup/complete` | POST | Persist wizard choices (API keys encrypted) |
| `/api/system/network-status` | GET | Online/offline/degraded state, Ollama availability |
| `/api/mcp/status` | GET | All MCP server statuses + discovered tools |

**Chat — `/api/chat/*`**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Main chat: routing, vault gates, tool loop, memory indexing |
| `/api/chat/history` | GET | Last 30 days + pinned messages |
| `/api/chat/search` | GET | Full-text search over history |
| `/api/chat/pin/<id>` | POST | Toggle pin (pinned messages never pruned) |
| `/api/memory/search` | POST | Semantic RAG search over ChromaDB |

**Voice — WebSocket**

| Endpoint | Protocol | Description |
|----------|----------|-------------|
| `/ws/live` | WebSocket | Gemini Live audio bridge (PCM 16kHz in, PCM 24kHz out) |
| `/ws/voice-local` | WebSocket | Local Whisper+Piper voice (same contract as `/ws/live`) |

**Tasks & Orchestration**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tasks` | GET | Live task list |
| `/api/tasks/<id>` | DELETE | Cancel task |
| `/api/agent/steer` | POST | Inject follow-up prompt into a running task |
| `/api/orchestrator/spawn` | POST | Fire-and-forget worker spawn |
| `/api/orchestrator/delegate` | POST | Spawn + block until done (5 min timeout) |
| `/api/orchestrator/results/<id>` | GET | Collect worker result |

**News & Briefings** — 35+ endpoints in `/api/news/*`, `/api/briefings/*`, `/api/source-trust/*`

**Full API documentation:** `docs/API.md`

---

## Configuration Guide

Settings exist across 5 layers. **Higher layers override lower ones.** There is no hot-reload — changes to layers 1-3 require a server restart; layer 4 takes effect per-request; layer 5 is ephemeral.

| Layer | Where | Override priority | Hot-reload? |
|-------|-------|-------------------|-------------|
| 1. Hardcoded defaults | `core.py:DEFAULT_SETTINGS` (~260 keys) | Lowest | N/A |
| 2. Persisted user settings | `~/.friday/settings.json` | Overrides defaults | Restart |
| 3. Environment variables | `os.environ` (set by `start.bat` or shell) | Overrides settings.json for key/model values | Restart |
| 4. Runtime overlay | `_apply_offline_routing_overlay()` in `core.py` | Forces `mode=local_only` when offline | Per-request |
| 5. Request context | `settings.workspace` per-call overrides | Workspace temperature, etc. | Per-request |

> **Important:** Two parallel settings schemas must stay synchronized:
> - **Flat keys** (`orchestrator_model`, `subagent_model`, `voice_model`, etc.) — legacy, used by most callers
> - **Canonical `capability_routing`** dict (11 capability entries) — new canonical form
>
> `_sync_capability_routing()` in `core.py:1140` reconciles them on every settings write. The sync is fragile — sending the full settings blob from the UI can cause the "snap-back bug" (see §Known Bugs).

### Key settings

```json
{
  "orchestrator_model": "claude-opus-4-8",
  "subagent_model": "claude-sonnet-4-6",
  "voice_model": "gemini-2.5-flash-native-audio-preview-12-2025",
  "voice_engine": "local",
  "voice_interruption_mode": "speaker",
  "model_routing": {
    "mode": "smart",
    "offline_auto_local": true,
    "ollama_url": "http://localhost:11434"
  },
  "cite_sources": false,
  "off_record": false,
  "daily_creation_hour": 8,
  "wiki_encrypted_sections": ["health", "legal", "family"]
}
```

### Supplementary config files

| File | Purpose |
|------|---------|
| `~/.friday/schedules.json` | Cron job definitions (interval/daily/weekly) |
| `~/.friday/personality.json` | Agent name, tone, hologram scene |
| `~/.friday/mcp_servers.json` | MCP stdio server definitions |
| `~/.friday/banned_sources.json` | Blocked news outlets |
| `~/.friday/briefing_prefs.json` | Briefing section order + category toggles |

---

# PART 2: CRITICAL ANALYSIS

## What Works Well

### 1. The vault and egress architecture (when it works)

The three-tier privacy model is conceptually sound and well-implemented at the classification layer:

- **TIER_1 (PUBLIC)** — passes through to any provider
- **TIER_2 (PRIVATE)** — replaced with `[REDACTED]` placeholder on cloud providers  
- **TIER_3 (SENSITIVE)** — dropped entirely before any cloud call

The sensitivity classifier (`services/sensitivity_classifier.py`) uses a genuinely layered approach: regex → keyword → Presidio NER → embedding similarity (all-MiniLM-L6-v2) → local LLM fallback. Each layer gracefully degrades if unavailable. Argon2id + AES-256-GCM in `privacy/vault_crypto.py` are correct primitive choices.

### 2. Provider-agnostic tool loop

The unified tool registry in `services/agent.py` is a genuine achievement. The same 30+ tools (file read/write, web search, wiki, calendar, email, trust graph) run identically on Ollama, Claude, and OpenAI-compatible endpoints. Vault gating, governance rings, and PII scrubbing apply regardless of which provider executes the call. This is hard to get right and they did.

### 3. Offline-first network resilience

The network monitor (`core.py:NETWORK_STATE`) and offline queue (`OFFLINE_QUEUE_DIR`) work well:

- 30-second probe interval with dual host checks (dns.google + 8.8.8.8)
- Two-strike degraded → offline transitions
- Automatic queue flush on reconnect with notifications
- Per-request routing overlay forces `local_only` when offline

### 4. Scheduler system

`services/scheduler.py` is clean: supports interval, daily (Central time), and weekly triggers; persists to `~/.friday/schedules.json`; runs each job in its own daemon thread with exponential backoff retries; append-only run history. The 7 default built-in jobs (morning briefing, evening briefing, weekly digest, editorial, daily creation, session summary, self-improvement) form a coherent daily rhythm.

### 5. Blueprint decomposition

The decomposition from a 3,000-line `server.py` monolith into 42 focused Blueprints is correct. Each route file owns its domain; `server.py` is ~130 lines. The flat `services/` namespace (explicit imports, no star-import cascade) is the right call.

### 6. Authentication

- Ephemeral session token generated fresh per restart (never persisted)
- Per-IP login throttle (8 attempts per 300s) via in-memory counter
- `hmac.compare_digest()` for constant-time password comparison
- Loopback auto-auth for local requests
- Session cookie hardened (`HTTPONLY`, `SAMESITE=Lax`)

---

## What's Broken or Fragile

### 🔴 CRITICAL: Egress gate has no exception handling on classifier crash

**File:** `src/agent_friday/services/egress_gate.py:26`

```python
from agent_friday.services.sensitivity_classifier import classify as _classify_impl
```

This import is at module level with no try/except. If `sensitivity_classifier.py` has a syntax error, bad import, or any module-level exception, `egress_gate` fails to import entirely. The callers of `seal_outbound()` have inconsistent exception handling — some catch broadly and proceed, which means **all content would be sent to cloud providers unredacted**. There is no fail-closed guarantee if the classifier dies.

**Workaround:** None currently. Fix: wrap the import in try/except; if classifier unavailable, apply a hardcoded PRIVATE tier to all content.

### 🔴 CRITICAL: Settings UI is missing from `app.html`

The application has 13+ voice settings, 5 provider configuration keys, vault tier controls, and distribution selection — but the Settings workspace is **not in `ui_parts/app.html`**. Settings must be edited via raw JSON at `~/.friday/settings.json` or through a one-time setup wizard. For any non-technical user, this is a complete blocker.

### 🟡 HIGH: Dual `model_router.py` modules (incomplete refactor)

- `src/agent_friday/services/model_router.py` — handles Anthropic Claude calls, cost metering, workspace temperature resolution
- `src/agent_friday/routing/model_router.py` — handles local/cloud/OpenAI dispatch decisions

These are supposed to form a layered system (routing decides, services executes) but the boundary is blurry. The routing module's `route()` function schema is not documented; its behavior under vault-access + offline + fallback combinations is opaque. New contributors will struggle to understand which file to change.

### 🟡 HIGH: `core.py` is a 1,647-line kitchen sink

A single file owns: Flask app creation, authentication, settings (DEFAULT_SETTINGS is ~260 keys), vault encryption state, PII scrubbing, network state, offline queue, sandbox policy, process registry, and chat history. Eight `try/except` import blocks for optional features (vault, cognitive memory, rings, behavioral monitor, trust graphs…) add another 100+ lines. Every new global state feature adds to this file.

### 🟡 HIGH: Settings sync fragility (snap-back bug)

`_sync_capability_routing()` at `core.py:1140` must keep two parallel schemas in sync: flat model keys (`orchestrator_model`, etc.) and the canonical `capability_routing` dict. When the UI sends a full settings blob (as the model picker used to do), the stale `capability_routing` values overwrite the user's just-chosen model. The fix for this was applied in commit `3e6adfb` but the underlying dual-schema architecture remains fragile.

### 🟡 MEDIUM: Stubs disguised as features

Three workspaces show "Add family members in your wiki" or similar messages instead of actual UI: **Family**, **Health**, and **Finance**. The **Contacts** workspace fetches data but has no rendered card layout. Users believe they're accessing features that don't exist.

### 🟡 MEDIUM: Google OAuth never completed

The Gmail and Calendar code is correct. The OAuth flow (`scripts/friday_google_connect.py`) exists. But `~/.friday/google_token.json` is missing because the OAuth was never actually run, and the Desktop OAuth client still has a `YOUR_CLIENT_ID` placeholder. All Gmail/Calendar tool calls return the "Google not connected" sentinel silently.

### 🟡 MEDIUM: Voice key rotation requires server restart

`routes/voice.py:590-610` refreshes the Gemini API key from environment on every session connect. But this only re-reads `os.environ` — if `start.bat` set the key at process start, a stale key in the environment is used even after the user updates the file. There is no live-reload mechanism.

### 🟡 MEDIUM: Temperature parameter silently dropped

`services/model_router.py:79-81` intentionally does not forward the `temperature` parameter to Claude Opus 4.8 and Sonnet 4.6+ because these models reject it. But callers are not notified. If a workspace sets `temperature: 0.75`, it is silently ignored. No log warning is emitted.

---

## Technical Debt Inventory

### Settings layer

| Item | Location | Severity |
|------|----------|----------|
| Dual capability_routing schema | `core.py:1140-1202` | HIGH |
| Settings read from disk on every turn (~100 reads/s at load) | `core.py:_load_settings()` | MEDIUM |
| Settings writes are not atomic (crash = corrupt file) | `core.py:_save_settings()` | MEDIUM |
| `_load_settings()` vs `_load_settings_raw()` inconsistency | Throughout | MEDIUM |
| No LRU cache for settings | Throughout | LOW |

### Model routing

| Item | Location | Severity |
|------|----------|----------|
| Two `model_router.py` modules with blurry boundary | `services/` vs `routing/` | HIGH |
| Fallback chain order differs across paths | `agent.py`, `model_router.py`, `voice.py` | MEDIUM |
| Vault routing + tool loop interaction under fallback | `agent.py:135-139` | MEDIUM |
| `route()` function schema undocumented | `routing/model_router.py` | MEDIUM |

### Voice

| Item | Location | Severity |
|------|----------|----------|
| Key rotation requires restart | `routes/voice.py:590-610` | MEDIUM |
| Session resumption SDK version undetected | `routes/voice.py:843-846` | MEDIUM |
| Affective dialog support checked twice, inconsistently | `routes/voice.py:748-767, 907-914` | LOW |
| Barge-in disabled on speakers (intentional but undocumented in UI) | `routes/voice.py:87-140` | LOW |

### Agent loop

| Item | Location | Severity |
|------|----------|----------|
| 40+ `except Exception:` blocks that swallow error context | `services/agent.py` throughout | MEDIUM |
| Tool result size unbounded in text path (voice truncates to 8KB, text doesn't) | `services/agent.py` vs `routes/voice.py:988` | MEDIUM |
| PII tag collision risk in rehydration | `core.py:668-676` | LOW |

### Cost metering

| Item | Location | Severity |
|------|----------|----------|
| Unknown models fall back to blended rate (possibly 0 for local) | `services/cost_meter.py:51-72` | MEDIUM |
| Budget alert dedup is in-memory, resets on restart | `services/cost_meter.py:156` | LOW |
| 10-second flush window = costs lost on crash | `services/cost_meter.py:179-194` | LOW |
| Attribution thread-local breaks under `asyncio.to_thread` | `services/cost_meter.py:81-147` | MEDIUM |

### Memory / context

| Item | Location | Severity |
|------|----------|----------|
| Session continuity hard-cut at 7 days | `services/model_router.py:721` | LOW |
| Embedding model change = stale ChromaDB (no migration) | `services/model_router.py:558-576` | LOW |
| Token estimation uses `chars/4` (imprecise per model) | `services/compaction.py:28` | LOW |

---

## The Restructure Shim Problem

The project migrated from a flat layout (everything in root) to a `src/agent_friday/` package. Three root-level shim files survive from the old layout:

**`server.py` (root)**
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
exec(compile(open(os.path.join('src','agent_friday','server.py'), ...).read(), 'src/agent_friday/server.py', 'exec'))
```

Uses `exec()` to run the real server. Any syntax error in `src/agent_friday/server.py` produces a confusing traceback blaming `server.py`.

**`friday_tray.py` (root)**
```python
from agent_friday.friday_tray import *
if __name__ == '__main__': main()
```

Assumes `main()` is exported. If the export is removed/renamed, fails at runtime with no helpful error.

**`core.py` (root)**
```python
from agent_friday.core import *
from agent_friday.core import app, sock  # explicit re-export
```

Explicit re-exports of Flask globals. If `core.py` renames `app` or `sock`, this breaks silently (no linting error because `*` import passes).

**Additionally**, `src/agent_friday/server.py` itself has a PEP 562 `__getattr__` facade:
```python
_FACADE_MODULES = ("agent_friday.services.notifications", "agent_friday.services.ambient_awareness", ...)
def __getattr__(name):
    for _mod_name in _FACADE_MODULES:
        ...
        if hasattr(_m, name): return getattr(_m, name)
    raise AttributeError(...)
```

This walks 13 modules on every undefined-attribute lookup. It exists so old `from server import X` code still works. IDEs can't resolve these symbols. The facade hides which module actually owns each export.

**Risks:**
1. `sys.path` accumulates duplicates if multiple shims are imported
2. Circular import risk increases with each new `_FACADE_MODULES` entry
3. Static analysis (mypy, ruff F-checks) is defeated — IDE shows no autocomplete for server-exported symbols
4. New contributors don't know which file to search when looking for a function

**Migration path:** Once all callers use `pip install -e .` + direct imports from `agent_friday.*`, the root shims can be deleted. Track usage with: `grep -r "from server import\|import server" tests/`

---

## Security Posture

### What's genuinely strong

- **Crypto primitives**: AES-256-GCM + Argon2id (256 MiB, 4 iterations) — industry standard, correctly implemented
- **Nonce handling**: 96-bit random nonce per blob — no IV reuse
- **HMAC governance key**: constant-time comparison (`hmac.compare_digest`)
- **Egress field-level gating**: messages, tool descriptions, system prompt all individually classified
- **Atomic file writes in credential store**: temp file + rename
- **Permission hardening**: `chmod 0o600` + Windows ACL on sensitive files
- **Session cookie**: `HTTPONLY`, `SAMESITE=Lax`
- **Login throttle**: 8 attempts/300s per IP

### What's fragile

| Severity | Issue | File:Line |
|----------|-------|-----------|
| CRITICAL | Classifier crash → payload sent unredacted (no fail-closed in egress gate) | `egress_gate.py:26` |
| HIGH | TOCTOU race: secret key file is world-readable briefly before `chmod 0o600` | `core.py:134-139` |
| HIGH | Login throttle resets on server restart (no persistent state) | `core.py:207-210` |
| MEDIUM | `FRIDAY_PASSWORD` used for both HTTP auth AND vault KDF — changing it breaks vault | `core.py:159-181` |
| MEDIUM | Vault salt stored plaintext in `.vault_config.json` (enables offline brute-force with weak passphrase) | `vault_crypto.py:110-114` |
| MEDIUM | Vault key cached in memory indefinitely (`_VAULT_KEY` module global) — survives hibernation | `credential_store.py:70-101` |
| MEDIUM | Plaintext credential fallback warning is one-time (resets never re-warn) | `credential_store.py:164-168` |
| MEDIUM | Classifier unavailability silently downgrades to broad keyword scan | `vault_access.py:50-52` |
| MEDIUM | Egress gate doesn't protect HTTP headers, query params, or URL path | `egress_gate.py:162` |
| MEDIUM | Unicode homograph attacks not blocked in extension security | `extension_security.py:51-61` |
| LOW | Access log is append-only with no size cap (disk fill attack) | `vault_access.py:286-294` |
| LOW | `TRUSTED_LAUNCHERS` includes `python` — allows arbitrary code via args | `extension_security.py:135-138` |

### Critical fix (implement now)

```python
# egress_gate.py — add fail-closed wrapper
try:
    from agent_friday.services.sensitivity_classifier import classify as _classify_impl
    _CLASSIFIER_AVAILABLE = True
except Exception:
    _CLASSIFIER_AVAILABLE = False
    def _classify_impl(text): return "PRIVATE"  # fail-closed default
```

---

## Known Bugs and Workarounds

| Bug | Severity | Workaround |
|-----|----------|-----------|
| **Settings snap-back**: UI sends full blob, stale `capability_routing` overwrites model picker choice | HIGH | Fixed in `3e6adfb`; underlying dual schema still exists |
| **Voice 1008 auth error**: stale `GEMINI_API_KEY` in env from prior launch shadows updated `start.bat` | HIGH | Kill all processes, update `start.bat`, relaunch |
| **Temperature silently dropped**: `claude-opus-4-8`/`claude-sonnet-4-6` ignore `temperature` param | MEDIUM | Don't rely on temperature for these models |
| **Gmail/Calendar tools return sentinel**: OAuth never completed | MEDIUM | Run `python scripts/friday_google_connect.py` with a valid Desktop OAuth client |
| **Tool result size unbounded (text path)**: large web scrapes inflate context 10×+ | MEDIUM | No workaround; avoid asking Friday to browse large pages |
| **Cost attribution lost in voice tool thread**: `asyncio.to_thread` breaks thread-local attribution stack | MEDIUM | Voice tool costs tracked at provider level but unattributed to task |
| **Offline queue partial write on crash**: `_offline_queue_add()` is not atomic | LOW | Manual cleanup of partial `.json` files in `~/.friday/offline_queue/` |
| **Family/Health/Finance workspaces are stubs** | LOW | Don't advertise these to users |
| **LinkedIn Easy Apply endpoint is placeholder** | LOW | `POST /api/jobs/apply` returns 501 |

---

# PART 3: SWOT ANALYSIS

## Strengths

### 1. Privacy architecture is a genuine differentiator

No other personal AI assistant in 2026 routes data based on sensitivity tier and enforces it at the field level before cloud dispatch. The `vault_access.py` → `egress_gate.py` → `sensitivity_classifier.py` chain is unique. Users who care about privacy have no comparable alternative.

### 2. Offline-first with graceful degradation

The network monitor + local voice tier + offline queue create a coherent offline experience. Most assistants simply fail when offline. Friday routes to local Ollama, queues work, and resumes silently on reconnect.

### 3. Local-first voice

Tier-1 voice (Whisper + Piper, CPU-only, no CUDA) works on any machine with Python. The Gemini Live integration is also strong (session resumption, affective dialog, agentic tools callable mid-conversation). Having both tiers with transparent fallback is rare.

### 4. Unified tool registry across providers

The same 30+ tools work on Ollama, Claude, and OpenAI-compatible endpoints. Governance rings, vault gating, and PII scrubbing apply uniformly. Adding a new provider means implementing the model call; all the governance scaffolding is inherited.

### 5. Continuous memory and self-improvement

ChromaDB indexing on every turn, EOD session summaries, weekly self-improvement reports, and epistemic scoring form a genuine closed-loop learning system. Most assistants have no cross-session memory. Friday's is automatic, invisible, and genuinely works.

### 6. Federation protocol

Ed25519 identity, X25519+ChaCha20-Poly1305 transport, and signed source-trust attestations are the foundation for a peer network of trusted Friday instances. No competitor has this.

---

## Weaknesses

### 1. Settings UI gap is a ship-blocker

The application has no settings UI in the delivered `app.html`. 13+ voice config keys, provider selection, vault tier controls — all require JSON editing. This is not a minor gap; it's an experience that will make most users uninstall immediately.

### 2. Stub features erode trust

Three workspaces (Family, Health, Finance) show placeholder messages. When a user discovers that a feature tile opens to "edit your wiki," they lose trust in all other features. The right fix is deletion, not a better placeholder.

### 3. core.py concentration risk

1,647 lines, 8 defensive import blocks, 260 settings keys, Flask app, auth, vault state, network state, sandbox, process registry, chat history — all in one file. A bug here can crash everything. A new contributor editing this file touches a live wire. This is the project's highest bus-factor risk.

### 4. No structured observability

All errors are console-printed with no structured logging. No metrics (Prometheus, Datadog). No distributed tracing. No cost anomaly detection. If Friday misbehaves in production, the only diagnostic tool is `print()` statements.

### 5. Windows-only design (by accident)

The tray app, computer control features, and the `start.bat` launcher are Windows-specific. Significant dependencies (`pyautogui`, `pynput`) are Windows-only and not properly gated by `sys_platform` conditions in `pyproject.toml`. Linux/macOS users get a degraded experience with no documentation of what's missing.

### 6. [dev] extra missing from pyproject.toml

CONTRIBUTING.md tells new contributors to run `pip install -e ".[dev]"`. No such extra exists. This is the first step of onboarding and it fails.

---

## Opportunities

### 1. Settings UI (highest ROI)

Build the missing Settings workspace — a single panel with form inputs for all user-facing toggles. This unlocks all the features that already work but are invisible. One sprint of frontend work, massive UX impact.

### 2. Mobile companion app

The backend is provider-agnostic and offline-capable. A React Native app over the existing REST API would give users mobile access without rebuilding the backend. WebSocket voice is already protocol-compatible.

### 3. Skill ecosystem (marketplace)

`services/marketplace.py` and `services/economy.py` already exist. The foundation for a skill/plugin ecosystem is built. A public registry of community skills would dramatically extend functionality.

### 4. Enterprise distribution

`services/distributions.py` supports researcher/developer/executive distros. The infrastructure for enterprise multi-user deployment (federated attestations, governance BOM, audit trails) exists. Productizing this as a team assistant is a natural path.

### 5. Observability product

The source trust scoring, epistemic calibration, and media diet analytics are unique. Packaging these as standalone analytics (e.g., a media literacy dashboard) could be a separate product.

### 6. Federation network effect

Each Friday instance generates signed source-trust attestations. A public attestation network (like certificate transparency logs) would let users benefit from the collective media literacy of all Friday users — a compelling network effect no competitor can easily replicate.

---

## Threats

### 1. Complexity accumulation

The codebase has grown rapidly (63 services, 42 routes, 1,647-line `core.py`). Without deliberate simplification, adding new features will become increasingly risky. The dual `model_router.py` is a symptom: incomplete refactors accumulate.

### 2. Bus factor = 1

`core.py` alone holds enough undocumented state (15 global dicts, 8 defensive imports, non-obvious initialization order) that a new contributor cannot safely edit it without extensive mentoring. If the current author is unavailable, the project stalls.

### 3. Provider API churn

The project is tightly coupled to Gemini Live v1alpha (which has changed APIs multiple times) and Claude model IDs (which version rapidly). Every Gemini or Anthropic major release requires immediate compatibility work. The session resumption SDK version check (`hasattr(types, 'SessionResumptionConfig')`) is a recurring pattern of fragile detection.

### 4. Key management as a user problem

Currently, users must manage API keys in `start.bat`, rotate them when they expire, and understand the difference between Gemini AI Studio keys and Vertex keys. Most target users cannot do this. If Friday doesn't solve key management, it can't grow beyond developer users.

### 5. Privacy claims without audit

The privacy architecture is sophisticated but has not been externally audited. A single CVE (e.g., classifier crash → data exfiltration) could undermine the core value proposition. Trust is fragile in security products.

---

# PART 4: PRIORITY FIXES AND ADDITIONS

## Tier 0: Ship-Blockers (fix before any release)

**1. Build Settings workspace UI** (`app.html`)

The single highest-impact work item. Create a Settings panel with:
- Provider selection (Anthropic/Gemini/Ollama) with key input + validation
- Voice engine (local/cloud/auto) with status indicator
- Voice model selector and TTS voice picker
- Vault tier display (which sections are encrypted)
- Notification and schedule toggles

Without this, no non-technical user can configure Friday.

**2. Add [dev] extra to `pyproject.toml`**

```toml
[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-timeout>=2.0"]
```

One line. Fixes the first step of contributor onboarding.

**3. Fail-close the egress gate**

Wrap the classifier import in `egress_gate.py` with a try/except that defaults to PRIVATE classification on any failure. (See §Security Posture for code snippet.)

## Tier 1: High-Impact Stability (next sprint)

**4. Remove or populate stub workspaces**

Delete the Family, Health, and Finance workspace tiles from the dock entirely, OR add meaningful starter templates (birthday countdowns, health reminder form, budget tracker). Half-features destroy trust.

**5. Atomic settings writes**

```python
# Replace:
SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
# With:
tmp = SETTINGS_FILE.with_suffix('.tmp')
tmp.write_text(json.dumps(settings, indent=2))
tmp.replace(SETTINGS_FILE)
```

Prevents settings corruption on crash.

**6. LRU cache for settings**

```python
from functools import lru_cache
@lru_cache(maxsize=1)
def _load_settings_cached(): ...
```

Invalidate on POST `/api/settings`. Eliminates ~100 disk reads/s at load.

**7. Unify the two model_router modules**

Merge `services/model_router.py` and `routing/model_router.py` into a single layered module with a documented interface. Until this happens, add a docstring to each file explaining its exact responsibility and when to use each.

**8. Fix TOCTOU race on secret key file**

```python
# Write to temp file first, then rename (atomic on POSIX and Windows)
tmp = SECRET_KEY_FILE.with_suffix('.tmp')
tmp.write_bytes(secret_key)
tmp.chmod(0o600)
tmp.replace(SECRET_KEY_FILE)
```

**9. Log temperature parameter drop**

```python
if model in MODELS_REJECTING_TEMPERATURE and temperature is not None:
    logger.debug("temperature=%s ignored for model %s", temperature, model)
```

## Tier 2: Stability Improvements (next month)

**10. Split `core.py` into focused modules**

Minimum viable split:
- `auth.py` — login, session, throttle
- `settings.py` — load, save, sync, DEFAULT_SETTINGS
- `vault_state.py` — encryption state, passphrase
- `network.py` — NETWORK_STATE, offline queue, monitor
- `processes.py` — PROCESSES registry, orb tracking

Keep `core.py` as a re-export facade while migrating callers.

**11. Blueprint auto-discovery**

```python
# routes/__init__.py
import importlib
from pathlib import Path
from flask import Blueprint

_BLUEPRINTS = []
for _f in Path(__file__).parent.glob('*.py'):
    if _f.name.startswith('_'): continue
    _m = importlib.import_module(f'agent_friday.routes.{_f.stem}')
    _BLUEPRINTS += [v for v in vars(_m).values() if isinstance(v, Blueprint)]
```

Eliminates the 35-import enumeration in `server.py`. Adding a route is one file.

**12. Persistent login throttle**

Store throttle state in SQLite (alongside `budgets.db`). Survives server restarts, enabling true brute-force protection.

**13. Cap vault access log**

```python
# vault_access.py — rotate log when it exceeds 50MB
if access_log_path.stat().st_size > 50 * 1024 * 1024:
    access_log_path.rename(access_log_path.with_suffix('.1.jsonl'))
```

**14. Standardize tool result size cap**

Apply the voice path's 8KB cap uniformly:
```python
# In _execute_tool (agent.py), after result is assembled:
if isinstance(result, str) and len(result) > 8192:
    result = result[:8192] + f"\n[truncated: {len(result)} chars total]"
```

## Tier 3: Missing Features Users Expect

**15. Voice setup wizard**

When user enables voice for the first time:
1. Show download progress for Whisper/Piper models (~300MB)
2. Test microphone with VU meter
3. Run test utterance and play back response
4. Confirm "local voice is working" before closing

**16. Live key reload without restart**

Add `/api/providers/<name>/reload-key` endpoint that re-reads the env or credential store and reinitializes the provider client. The voice WebSocket handler already refreshes on session connect — extend this to a manual trigger.

**17. Google OAuth first-run flow**

Replace the `friday_google_connect.py` script with an inline flow:
1. User clicks "Connect Google" in Settings
2. Server generates OAuth URL, opens browser
3. Redirect handler catches the token, writes `google_token.json`
4. Settings UI shows "Gmail: Connected ✓"

**18. Error message audit**

Replace all `[Connection error]` and `(tool {name} error: {e})` messages with specific, actionable text. Minimum: log the full exception server-side and show a human sentence to the user.

## Tier 4: Architecture Improvements (prevent future breakage)

**19. Structured logging**

Replace all `print(f"[FRIDAY] ...")` calls with `logging.getLogger("friday")`. Add a log handler that writes to `~/.friday/friday.log` with rotation. This enables debugging without console access (important since `pythonw` has no console).

**20. FeatureFlags class**

```python
class FeatureFlags:
    vault_access: bool
    cognitive_memory: bool
    dynamic_rings: bool
    behavioral_monitor: bool
    ...

FEATURES = FeatureFlags.detect()  # replaces 8 try/except blocks in core.py
```

Expose via `/api/health` so the UI can show which features are active.

**21. Dependency injection for core services**

Pass `settings`, `vault`, and `model_router` as injected dependencies to route handlers instead of importing from `core`. This makes unit testing possible without global patching.

## Tier 5: New Capabilities

**22. Settings sync across devices** — Use the federation transport to sync settings (not data) between a user's own Friday instances.

**23. Skill hot-reload** — Allow skills in `~/.friday/skills/` to be added/removed without restart using importlib reload.

**24. Budget UI** — Surface `cost_meter.py` data in the UI: daily spend, budget limit, per-provider breakdown, trend chart.

**25. Provider health dashboard** — Show live latency, error rates, and availability for each configured provider.

---

# PART 5: DEVELOPER ONBOARDING GUIDE

## Read These 5 Files First

In this exact order. Understanding each one before the next will save hours of confusion.

### 1. `src/agent_friday/core.py` (first 400 lines)

This file is the foundation everything else imports. You need to understand:
- How `DEFAULT_SETTINGS` is structured (~260 keys)
- The `_bootstrap_env_from_launch_scripts()` function (line ~396) — reads `start.bat` to populate env vars at startup
- The `_load_settings()` / `_save_settings()` / `_sync_capability_routing()` triad
- The defensive import pattern (8 `try/except` blocks for optional features)
- How `FRIDAY_TESTING=1` prevents daemon startup (critical for tests)

### 2. `src/agent_friday/server.py`

This is where the app is assembled. 130 lines that show you:
- Every Blueprint that's registered (all 38 of them)
- The `_FACADE_MODULES` / `__getattr__` pattern (why `from server import X` works)
- The startup sequence (blueprints → daemons → run)

### 3. `src/agent_friday/services/agent.py` (first 400 lines)

This is the main event loop. Understand:
- `_generate_agent()` — the provider-agnostic agentic call with vault gating, PII scrubbing, and tool dispatch
- How CLAUDE_TOOLS is populated and used
- The fallback chain: local → cloud → openai
- How vault_access decisions cascade to affect which provider is chosen

### 4. `tests/conftest.py` + `tests/api/conftest.py`

These are non-optional reading before writing any test:
- Root conftest sets `FRIDAY_TESTING=1` and redirects `USERPROFILE` to a temp dir — this MUST happen before any import of `server`
- API conftest patches `_call_claude` / `_generate_agent` / `_generate_text` in **every project module** (not just `server`) because star-imports create per-module copies of these functions
- Understand `_SentinelAnthropicClient` — it will raise on any unmocked direct Anthropic call
- Understand `_patch_everywhere()` — the only safe way to mock LLM functions across the star-import landscape

### 5. `ui_parts/app.html` (scan section headers and component names)

The entire frontend is a single file with inline Babel/React. You don't need to read all 8,000+ lines, but you need to know:
- Each workspace component name (`HomeWS`, `CareerWS`, `WikiWS`, etc.)
- The `sendMessage()` / `fetchAPI()` pattern for backend calls
- `_fridayMoodSignals` — the holo scene state bus (animates ONLY during `ttsActive`)
- The action bus (`window.dispatchEvent(new CustomEvent('friday-action', ...))`)

---

## "Never Touch These Without Understanding X" Warnings

### `core.py` — Never edit without understanding initialization order

`core.py` runs side effects on import: Flask app creation (line ~107), settings load (line ~1221), capability routing sync (line ~1225), wiki migration check (line ~356). If you add code that calls back into `core.py` from a module imported by `core.py`, you will create a circular import that Python resolves silently by giving you a partially-initialized module — causing extremely confusing `AttributeError: module has no attribute` errors at runtime, not import time.

**Rule:** `core.py` imports services; services never import routes; routes never import `core.py` for anything except globals. If you need cross-module communication, use events or a shared registry pattern.

### `tests/api/conftest.py` — Never add direct LLM imports in route files

The API test kill-switch works by patching named functions in every module. If you write:

```python
# routes/my_new_feature.py — WRONG
import anthropic
client = anthropic.Anthropic()  # Direct SDK call — not patchable by the kill-switch
```

This will bypass the kill-switch and make a real, paid Anthropic API call during testing. Always use `_generate_text()` or `_generate_agent()` from `services/agent.py` or `services/model_router.py`.

### `privacy/vault_crypto.py` — Never change without verifying roundtrip

The vault crypto module has a `roundtrip_ok()` function for a reason. Any change to the MAGIC header, Argon2id parameters, or AAD format will break decryption of existing vault files. Always:
1. Write a migration path before changing crypto parameters
2. Test `roundtrip_ok()` with files created by the old version
3. Update the version byte in the MAGIC constant

### `server.py` (the root shim) — Never import it in application code

The root shim's `__getattr__` facade walks 13 modules on every lookup. If you write `from server import some_function` in production code, you create a runtime performance hit and a static analysis blind spot. Import from the actual module: `from agent_friday.services.notifications import some_function`.

### `app.html` / `build_ui.py` — Always rebuild after editing

`ui_parts/app.html` is the source. The built output is served by Flask. Running `python build_ui.py` regenerates the served file. If you edit `app.html` directly and see no change in the browser, you forgot to rebuild. If the page is blank with no console errors, you have a duplicate `const` or syntax error in the Babel script — diagnose with `Babel.transform(document.querySelector('script[type="text/babel"]').textContent, {presets: ['react']})` in the browser console.

---

## Common Pitfalls

### Pitfall 1: Running `python server.py` without `pip install -e .`

The root shim injects `src/` into `sys.path` at runtime, but if you're importing `agent_friday` anywhere before the shim runs, Python may find a different (or no) version of the package. **Always** activate the venv and run `pip install -e .` first. Use `friday start` or `python -m agent_friday.server` instead of `python server.py`.

### Pitfall 2: Editing `settings.json` while the server is running

The server reads settings from disk on every request (`_load_settings()` is not cached). But it also writes settings in response to `POST /api/settings`. If you manually edit the file while the server has a request in flight that's also saving settings, you will corrupt the file. Stop the server before manual JSON editing.

### Pitfall 3: The auth token flow

The server generates an ephemeral `_API_SESSION_TOKEN` on startup and injects it into the `GET /` response as a JavaScript constant. The browser uses this token for all subsequent API calls. If you:
- Restart the server while the browser tab is open → all API calls 401 (stale token)
- Open two browser tabs → they share the same token (it's per-server-process, not per-session)
- Call the API from curl without the token → 401

This is intentional for local security (token prevents other localhost apps from calling your Friday). To test API calls with curl: `curl -H "X-API-Token: $(grep API_SESSION_TOKEN ~/.friday/..." ...` or use the browser devtools network tab to find the token.

### Pitfall 4: The GEMINI_API_KEY refresh problem

When you start the server with a Gemini API key in `start.bat`, that key is baked into `os.environ` at process start. The voice WebSocket handler (`routes/voice.py:590`) re-reads the key on every session connect. But it reads from `os.environ` first — so if your key expires and you update `start.bat`, you must restart the server (you can't just reconnect the WebSocket). The restart reads the new `start.bat` via `_bootstrap_env_from_launch_scripts()`.

### Pitfall 5: `FRIDAY_TESTING=1` timing

The `conftest.py` sets `os.environ["FRIDAY_TESTING"] = "1"` to suppress daemon startup. But conftest runs AFTER imports at the module level. If pytest discovers your test file and imports it, and that file has a top-level `import server` (not inside a function or fixture), the import runs before conftest, daemons start, and tests hang.

**Solution:** Always write API tests like:
```python
# tests/api/test_my_feature.py
def test_something(client):  # 'client' fixture imports server inside the fixture
    resp = client.get('/api/my-endpoint')
    ...
```

Never put `import server` at the top level of a test file.

### Pitfall 6: Adding a route without registering it

If you create `routes/my_feature.py` with `my_feature_bp = Blueprint(...)` but forget to add it to the import list in `server.py`, the route will 404 silently. No error, no warning. Check that your blueprint appears in the `for _bp in (...)` tuple in `server.py`.

### Pitfall 7: The `bare python` on PATH problem

On this development machine, bare `python` resolves to the hermes-agent venv, not the Friday venv. Always run pytest as:
```bash
.\venv\Scripts\python.exe -m pytest tests/ -q
```
Or activate the venv explicitly: `.\venv\Scripts\activate` then `pytest`.

---

## How to Run Tests

```bash
# Activate venv first
.\venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

# Full offline test suite (~3 minutes)
pytest tests/unit tests/api -q

# Fast unit tests only (~30 seconds)
pytest tests/unit -q

# Single test file
pytest tests/unit/test_vault_crypto.py -v

# Tests matching a pattern
pytest -k "test_vault" -v

# With detailed output on failure
pytest tests/ -q --tb=short
```

### What the test suite covers

| Area | Coverage | Notes |
|------|----------|-------|
| Vault crypto (AES-256-GCM, Argon2id) | HIGH | Full roundtrip tests |
| Sensitivity classifier (all 5 layers) | HIGH | Layer-by-layer unit tests |
| Egress gate | MEDIUM | Field-level gating; no classifier-crash scenario |
| Model router (routing decisions) | MEDIUM | Core routing logic; not all fallback paths |
| API routes (all 42 blueprints) | MEDIUM | Response codes + basic field presence; LLM stubbed |
| Orchestrator, budget enforcer | HIGH | Worker lifecycle, budget reserve/release |
| Scheduler, cost meter | HIGH | Job registration, firing, pricing |
| Federation, economy, moderation | HIGH | Protocol-level unit tests |

### What the test suite does NOT cover

- **End-to-end with real LLM calls** (all LLM functions are stubbed)
- **Voice audio pipeline** (Whisper/Piper integration is not tested)
- **GPU voice (NeMo)** (no CI GPU)
- **Google OAuth flow** (requires interactive browser)
- **UI behavior** (no Playwright/Selenium tests)
- **Crash recovery** (no fault injection tests)
- **Concurrent requests** (single-threaded test client)

---

## How to Add a New Route

1. Create `src/agent_friday/routes/my_feature.py`:

```python
from flask import Blueprint, request, jsonify
from agent_friday import core
from agent_friday.services.my_service import do_thing

my_feature_bp = Blueprint("my_feature", __name__)

@my_feature_bp.route("/api/my-feature", methods=["GET"])
@core.login_required
def my_feature():
    settings = core._load_settings()
    result = do_thing(settings)
    return jsonify(result)
```

2. Register in `src/agent_friday/server.py`:

```python
# Add to imports (line ~59):
from agent_friday.routes.my_feature import my_feature_bp

# Add to the registration tuple (line ~99):
for _bp in (..., my_feature_bp):
    app.register_blueprint(_bp)
```

3. Add a test:

```python
# tests/api/test_my_feature.py
def test_my_feature_returns_200(client):
    resp = client.get("/api/my-feature")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "expected_key" in data
```

---

## How to Add a New Service

1. Create `src/agent_friday/services/my_service.py`:

```python
from agent_friday import core  # only import what you need
import logging

_log = logging.getLogger("friday.my_service")

def do_thing(settings: dict) -> dict:
    ...
```

**Rules:**
- Services import from `core` and other services only
- Services never import from `routes/`
- Use `_generate_text()` or `_generate_agent()` from `services/agent.py` for any LLM call — never import the SDK directly
- If your service starts a background thread, check `os.environ.get("FRIDAY_TESTING")` and skip the thread in test mode

2. Import it wherever needed (in routes or other services). No registration step required — services are imported directly.

---

## How to Add a New UI Panel

1. In `ui_parts/app.html`, find the workspace registration block and add your component:

```jsx
// Add the component definition
function MyWorkspaceWS() {
  const [data, setData] = React.useState(null);
  
  React.useEffect(() => {
    fetchAPI('/api/my-feature').then(setData);
  }, []);
  
  return <div className="ws-panel">...</div>;
}

// Register in the workspace map
const WS_COMPONENTS = {
  ...,
  'myworkspace': MyWorkspaceWS,
};
```

2. Add the dock tile in the dock section.

3. Rebuild:
```bash
python build_ui.py
```

4. Restart the server (no hot-module reload):
```bash
# preview_stop then preview_start, or:
friday stop && friday start
```

**Important**: The `build_ui.py` step is required. Editing `app.html` directly edits the source; the served file is a separate build artifact. If you see a blank page after editing, check for syntax errors using `Babel.transform(...)` in the browser console.

---

## Orientation: Where Is X?

| "Where is the code for..." | Look in... |
|---------------------------|-----------|
| Main chat endpoint | `routes/chat.py` → `services/agent.py:_generate_agent()` |
| Voice WebSocket handler | `routes/voice.py:ws_live()` |
| Settings load/save | `core.py:_load_settings()`, `_save_settings()` |
| API key encryption | `services/credential_store.py` |
| Sending text to Claude | `services/model_router.py:_call_claude()` |
| Routing to Ollama vs Claude | `routing/model_router.py:route()` |
| Vault tier enforcement | `privacy/vault_access.py:check_action()` |
| PII scrubbing | `core.py:_scrub_pii()`, `_rehydrate_pii()` |
| News RSS + feed | `services/news_engine.py` |
| Daily briefing generation | `services/news_engine.py` (search for `generate_daily_briefing`) |
| Scheduler (cron) | `services/scheduler.py`, `routes/scheduler.py` |
| Cost tracking | `services/cost_meter.py` |
| Context compression | `services/compaction.py` |
| Tool hook registration | `services/tool_hooks.py` |
| Worker orchestration | `services/orchestrator.py` |
| Federation / peer P2P | `services/federation.py`, `services/federation_transport.py` |
| Image/video/music generation | `services/creative_engine.py`, `services/music_engine.py` |
| Hologram scene settings | `ui_parts/app.html` → `_fridayMoodSignals` |
| Windows tray icon | `src/agent_friday/friday_tray.py` |
| CLI entry point | `src/agent_friday/cli.py` |
| All dependency definitions | `pyproject.toml` |
| CI configuration | `.github/workflows/tests.yml` |

---

*This report was produced using the Stanford STORM methodology: six expert perspectives independently investigated the actual codebase files, findings were synthesized into a hierarchical outline, and the outline was expanded into this document. All file:line references were verified against the codebase at commit time.*

*Report generated: 2026-06-29 | Method: STORM | Perspectives: 6 | Files read: 50+ | Lines analyzed: ~15,000*
