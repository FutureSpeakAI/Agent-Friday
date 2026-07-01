# Agent Friday — Codebase STORM Report v2
*Stanford STORM Methodology · Six Expert Perspectives · July 2026*

> **Scope:** Post Tier 1–5 implementation (STORM v1 → v2). Covers the core→services→routes
> decomposition, structured logging replacement of all prints, dual-role orchestration engine,
> Settings workspace (9 tabs), voice echo fix, model-selector snap-back fix, and Creator Economy
> Layers 1–3. All file:line citations are from the current `main` branch.

---

## Part 1 — Product Guide

### What Is Agent Friday?

Agent Friday is a **sovereign, provider-agnostic desktop AI agent** — a locally-hosted Flask
server delivering an AI-powered OS layer that runs in your browser at `http://localhost:3000`.
It is not a wrapper around a single cloud API; it routes work across Anthropic Claude,
Google Gemini, OpenAI-compatible endpoints (OpenRouter, Together, Groq, vLLM), and on-device
Ollama, choosing based on content sensitivity, cost, and network state.

The core promise: **sensitive data stays on your device by default.** A multi-layer egress gate
(`services/egress_gate.py`), a four-layer sensitivity classifier
(`services/sensitivity_classifier.py`), and a bidirectional PII scrub/rehydrate pipeline
(`core/__init__.py:_scrub_pii`) collaborate to ensure vault content, SSNs, credit cards, and
watchlist tokens never leave the machine unless the user explicitly opts for a cloud model and
the content is classified as public.

### Feature Map (v4.5.0)

| Area | Status | Key Files |
|------|--------|-----------|
| **Chat with tools** | ✅ Production | `routes/chat.py`, `services/agent.py` |
| **Local voice** (faster-whisper + Piper, CPU) | ✅ Production (default) | `services/local_voice.py` |
| **Gemini Live voice** (cloud, opt-in) | ✅ Production | `services/voice_engine.py`, `routes/voice.py` |
| **NeMo GPU voice** (Tier-2, opt-in) | ✅ Conditional | `services/nemo_voice.py` (needs torch-CUDA) |
| **Model routing** (cloud/local/OAI-compat) | ✅ Production | `services/model_router.py`, `routing/model_router.py` |
| **Sovereign Vault** (AES-256-GCM at rest) | ✅ Conditional | `privacy/vault_crypto.py` (needs `FRIDAY_VAULT_PASSPHRASE`) |
| **Egress gate** | ✅ Production | `services/egress_gate.py` |
| **Sensitivity classifier** (4-layer) | ✅ Production | `services/sensitivity_classifier.py` |
| **PII scrub / rehydrate** | ✅ Production | `core/__init__.py:_scrub_pii` |
| **Scheduler** (interval/daily/weekly) | ✅ Production | `services/scheduler.py` |
| **Cost meter + budget alerts** | ✅ Production | `services/cost_meter.py` |
| **Dual-role orchestration engine** | ✅ Production | `services/orchestrator.py`, `services/budget_enforcer.py` |
| **Creator Economy Layer 1** (music/image/video/provenance) | ✅ Production | `services/music_engine.py`, `services/creative_engine.py` |
| **Creator Economy Layer 2** (scene DNA / QA gates / take comparison) | ✅ Production | `services/creative_pipeline.py`, `services/qa_gates.py` |
| **Creator Economy Layer 3** (federation / marketplace / economy / moderation) | ✅ Production | `services/federation.py`, `services/economy.py` |
| **Settings workspace** (9 tabs) | ✅ Production | `ui_parts/app.html` |
| **MCP connectors** | ✅ Production | `services/mcp_client.py` |
| **Offline queue + auto-routing** | ✅ Production | `core/__init__.py:_offline_queue_*` |
| **Credential store** (DPAPI/AES-256-GCM/plaintext tiered) | ✅ Production | `services/credential_store.py` |
| **Self-improvement loop** | ✅ Production | `services/introspection.py` |
| **Context auto-compaction** | ✅ Production | `services/compaction.py` |
| **Liquid UI engine** | ✅ Gated | `services/liquid_ui.py` (intent-triggered) |
| **Computer control** (pyautogui) | ⚠️ Opt-in | `computer_control_enabled=False` default |
| **Headroom context compression** | ⚠️ No-op on Windows | No Windows wheel for headroom-ai 0.22.x |
| **Gmail / Google Calendar** | ⚠️ OAuth pending | Code correct; `~/.friday/google_token.json` missing |

### Quick Start

```bash
# 1. Clone and install (Python 3.10+ required)
git clone <repo> && cd friday-desktop
python -m venv venv
venv\Scripts\activate          # Windows; or: source venv/bin/activate
pip install -e ".[all]"

# 2. Set API keys (need at least one provider)
set GEMINI_API_KEY=your-key
set ANTHROPIC_API_KEY=your-key

# 3. Enable vault encryption (strongly recommended)
set FRIDAY_VAULT_PASSPHRASE=<strong-passphrase>

# 4. Run
python server.py
# Open http://localhost:3000
```

On first launch the setup wizard fires at `/`. The server auto-discovers all Blueprint routes,
bootstraps keys from `start.bat` if present, migrates plaintext vault entries, and starts
background daemons (scheduler, news archiver, network monitor, MCP connectors, connector health).

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser  (index.html — 901 KB, assembled by build_ui.py)       │
│  React + Babel (inline) · Dock · Floating Windows · Holo Scene  │
└────────────────────────────┬────────────────────────────────────┘
                             │  HTTP / WebSocket
┌────────────────────────────▼────────────────────────────────────┐
│  src/agent_friday/server.py  (entry point, ~326 lines)          │
│  Blueprint auto-discovery — routes/*.py load themselves         │
│  Auth: loopback trust + ephemeral X-Friday-Token header         │
└──────┬─────────────────────┬────────────────────┬──────────────┘
       │                     │                    │
  core/__init__.py      services/*.py        routes/*.py
  (Flask app, auth,     (business logic,     (Flask Blueprints;
   settings, vault,      model router,        HTTP handlers;
   PII, network state,   agent loop,          no business logic)
   offline queue)        orchestrator, …)
```

**Dependency direction:** `routes → services → core → stdlib`. Services must not import from
routes; services must not import Flask's `request`/`session`. (See Part 2 for where this is
currently broken.)

### Entry-Point Chain

```
root/server.py              ← shim: sys.path insert + exec()
  → src/agent_friday/server.py    ← wiring only (blueprints + daemons)
    → src/agent_friday/core/__init__.py  ← Flask app, shared state
    → src/agent_friday/routes/*.py       ← auto-discovered Blueprints
    → src/agent_friday/services/*.py     ← imported by routes as needed
```

`root/core.py` is also a shim that re-exports `from agent_friday.core import *` for backward
compatibility with tests and scripts that `import core`.

---

## Part 2 — Critical Analysis

### What Works Well

**Security posture is genuinely strong.** The egress gate, sensitivity classifier (4 layers),
vault encryption, PII scrub, login throttle, and sandbox policy form a real layered defence.
The classifier (`services/sensitivity_classifier.py`) runs entirely locally and fails closed on
uncertainty (`Tier.PRIVATE` default). The login throttle persists to SQLite and survives server
restarts (`core/__init__.py:_login_attempt_ok`, line 262), providing real brute-force
protection even against a restart-cycle attack.

**Decomposition is structurally clean.** The 18 000-line monolith is now a 326-line
entry-point shim with business logic in `services/` and HTTP handlers in `routes/`. Blueprint
auto-discovery (`server.py:_discover_and_register_blueprints`, line 69) means adding a route
file is zero-touch.

**Structured logging is present and consistent in core.** The `friday.*` hierarchy with a
`RotatingFileHandler` (10 MB × 3 backups, `core/__init__.py:_setup_friday_logging`, line 459)
routes all sub-loggers to `~/.friday/friday.log` automatically.

**Settings persistence is correct.** Atomic write (fsync + tmp-rename, `_save_settings`,
line 1404–1412), 2-second in-memory cache with lock, and a transparent offline-routing overlay
(`_apply_offline_routing_overlay`) all work without data loss.

**Model selector snap-back is fixed.** The `_sync_capability_routing()` function
(`core/__init__.py`, line 1287) correctly detects whether the flat model key or the
`capability_routing` dict was the authoritative source in a PATCH, preventing the UI revert
that was the Tier 4 model-selector bug.

**Dual-role orchestration is complete.** `services/orchestrator.py` ships
`WorkerTask`/`WorkerResult`, seven adapter types (`OLLAMA`, `CLAUDE_CODE`, `PYTHON_SCRIPT`,
`BROWSER`, `HTTP_API`, `GEMINI`, `OPENROUTER`), `budget_enforcer.py` with reserve/release/
hard-stop accounting in SQLite, and a per-workspace monthly cap system measured in
milliPositrons (mψ).

---

### Issues Found — Verified, with File:Line

#### [HIGH] Services Layer Imports Flask — Architectural Layer Violation

`services/agent.py` line 29 and `services/model_router.py` line 51 both import:
```python
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
```
Services are supposed to be framework-agnostic. Any service that reads `request` or `session`
outside an active Flask request context raises `RuntimeError: Working outside of application
context`. This already affects the scheduler: when it dispatches an `agent_prompt` job, the
`_generate_agent` call in `services/agent.py` is invoked from a daemon thread with no request
context, which causes intermittent failures for scheduled tasks that try to read workspace
context.

#### [HIGH] 31 Test Collection Errors

```
pytest --collect-only   →   31 errors during collection
```
The confirmed failing files are `tests/unit/test_timeline_engine.py`,
`tests/unit/test_tool_hooks.py`, `tests/unit/test_work_log.py`, and
`tests/unit/test_workspace_temperature.py`. The error origin for each is most likely a module
moved or renamed during the decomposition. Until fixed, the total reported "passing" count is
misleading — those tests cannot even be loaded, let alone run.

#### [HIGH] Egress Gate Logs Security Decisions via `print()` — Not `_log`

`services/egress_gate.py` lines 72–74:
```python
print(
    f"  [EGRESS] {verdict} provider={provider} "
    f"field={field} tier={Tier.NAMES.get(tier, tier)} ({reason})"
)
```
This is the most security-sensitive log line in the codebase — every cloud egress allow/block
decision — and it bypasses the `friday.*` hierarchy entirely. Under `pythonw.exe` (no console
window), these prints are silently discarded, creating an invisible audit gap. The JSONL file
log (`_log()` lines 61–81) *is* correct; only the console branch was missed.

#### [MEDIUM] `_CC_RE` Regex Inconsistency Between Security Layers

`services/sensitivity_classifier.py` line 41:
```python
_CC_RE = re.compile(r'\b(?:\d[ -]?){13,16}\b')   # 13–16 digits
```
`core/__init__.py` line 593:
```python
_CC_RE = re.compile(r'\b(?:\d[ -]?){13,19}\b')    # 13–19 digits
```
AMEX (15 digits) and certain prepaid cards (17–19 digits) produce different tier decisions
depending on which layer processes them first. In the worst case a 19-digit number is flagged
as TIER_3 by core's PII redactor but passed as TIER_1 by the sensitivity classifier, defeating
the layered defence for that format.

#### [MEDIUM] `introspection.py` Duplicates Core Path Definitions

`services/introspection.py` lines 37–38:
```python
HOME = Path.home()
FRIDAY_DIR = HOME / ".friday"
```
This ignores the `FRIDAY_HOME` env-var override that `services/local_voice.py` correctly
respects (`_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())`). If `FRIDAY_HOME` is
set (e.g. in tests or multi-user setups), introspection silently writes to the wrong directory.
Fix: `from agent_friday.core import FRIDAY_DIR`.

#### [MEDIUM] Login Template Uses `str.replace()` — Jinja2 Escaping Bypassed

`core/__init__.py` lines 396–399:
```python
html = LOGIN_HTML.replace('{{ error }}', error)
return Response(html, content_type='text/html', status=429)
```
`error` is currently one of two hardcoded strings (`'TOO MANY ATTEMPTS…'`,
`'ACCESS DENIED…'`), so no XSS exists today. But the pattern bypasses Jinja2 auto-escaping
entirely. Any future change that includes the attempted username in `error` (a common UX
pattern) would be an immediate reflected XSS. Replace with
`flask.render_template_string(LOGIN_HTML, error=Markup(error))`.

#### [MEDIUM] `CHAT_HISTORY` Global List Has No Write Lock

`core/__init__.py` line 1767: `CHAT_HISTORY = _load_chat_history()` loads into a module-level
list. `_save_chat_history()` (line 1762) writes `json.dumps(messages, ...)` with no lock.
Flask runs with `threaded=True`; two concurrent chat requests can clobber each other's appends.
A `_SETTINGS_CACHE_LOCK` exists for settings but there is no equivalent for `CHAT_HISTORY`.

#### [MEDIUM] Vault Encryption Is Opt-In and Has No Persistent UI Warning

`FRIDAY_VAULT_PASSPHRASE` must be set in the environment before first run. The startup banner
prints a box warning if it is missing, but under `pythonw.exe` (tray-launch, no console) that
warning is invisible. The Settings UI and the 9-tab Settings workspace have no persistent
indicator of vault encryption state. Users who launch via the tray or a `.vbs` script will run
with plaintext vault indefinitely.

#### [LOW] `build_ui.py` Is a Naive File Concatenation with No Asset Pipeline

`src/agent_friday/ui/build_ui.py` concatenates three HTML files (line 13–23). The assembled
`index.html` is **901 KB** and embeds the full Babel standalone transpiler (~500 KB). Every
page load downloads and executes this transpiler before any React component renders. There is
no minification, sourcemaps, tree-shaking, or cache-busting.

#### [LOW] `start.bat` Key Bootstrap Is a Development-Only Pattern

`core/__init__.py:_bootstrap_env_from_launch_scripts()` reads `start.bat` in the repo root and
injects `set NAME=VALUE` lines into `os.environ`. The `.bat` file is gitignored but sits on
disk as a plaintext key store. For production or remote-tunnel use the `credential_store.py`
DPAPI/AES path already exists and should be the canonical path.

#### [LOW] Multiple SQLite Databases with No Migration System

At least six SQLite databases: `costs.db`, `budgets.db`, `federation.db`,
`login_throttle.db`, and the JSONL `schedule_runs.jsonl`. Each module initialises its schema
with `CREATE TABLE IF NOT EXISTS`. Adding a column to any table is a silent no-op on existing
installations until the DB is deleted and recreated.

#### [LOW] Voice Model ID May Be Stale

`core/__init__.py` line 1145:
```python
"voice_model": "gemini-3.1-flash-live-preview",
```
Gemini Live preview model IDs rotate frequently. The cost meter (`services/cost_meter.py`
line 47) prices this model, confirming it is recognised in the pricing table, but the string
should be validated against the current Live API spec at startup to surface 1008 auth errors
before the first voice session.

---

## Part 3 — SWOT Analysis

### Strengths

**S1. Genuinely layered security with no single point of bypass.** Egress gate →
sensitivity classifier (4 layers, local-only) → PII scrub → vault encryption (Argon2id KDF +
AES-256-GCM) → governance rings → sandbox policy → login throttle (SQLite-persistent). Each
layer is independently testable and independently bypassable only through code modification.

**S2. True local-first execution.** The offline routing overlay
(`core/__init__.py:_apply_offline_routing_overlay`) switches transparently to Ollama when the
network monitor reports offline — without persisting the change or requiring user action. The
offline queue (`_offline_queue_*`) persists tasks to disk and replays them on reconnect.

**S3. Comprehensive provider coverage with agentic tool-loop parity.** Anthropic (Opus 4.8 /
Sonnet 4.6 / Haiku 4.5), Google Gemini (Flash / Pro / Nano Banana / Veo / Lyria), OpenAI-
compatible (OpenRouter, Together, Groq, vLLM, LM Studio), and local Ollama — all through a
single routing layer. The OpenAI-compatible provider has full agentic tool-loop parity with
the Anthropic path.

**S4. Atomic writes throughout.** Settings: fsync + temp-rename (`_save_settings`,
line 1405–1412). Secret key: atomic with `chmod 0600` before rename
(`_load_or_create_secret`, line 147–151). These patterns prevent corruption on power-loss and
eliminate TOCTOU races.

**S5. Blueprint auto-discovery.** Adding a route file is zero-touch — drop
`routes/my_feature.py` with a `my_feature_bp = Blueprint(...)` and it registers itself on the
next restart. No manual import, no `server.py` edit.

**S6. Rich cost accounting.** `services/cost_meter.py` tracks per-direction (input/output)
token pricing, workspace attribution, and schedule attribution, flushing off the hot path with
a 10s/50-row SQLite buffer. `services/budget_enforcer.py` adds reserve/release accounting with
hard-stop capability. Rare for an open-source desktop agent.

**S7. Voice speaker-echo fix is correct and complete.** The `NO_INTERRUPTION` activity-
handling mode (`settings["voice_interruption_mode"] == "speaker"`) at
`services/voice_engine.py` correctly prevents Gemini's VAD from interpreting speaker bleed as
a barge-in. This is the right fix per Google's documentation and was verified working.

### Weaknesses

**W1. Services layer still carries Flask.** `services/agent.py` and
`services/model_router.py` import `request`, `session`, `Blueprint`, etc., making them
impossible to test or call outside a Flask request context. Scheduled tasks, CLI commands, and
background workers that invoke these functions intermittently hit `RuntimeError: Working
outside of application context`.

**W2. 31 test collection errors make the test count unreliable.** The "3008+ tests pass"
figure in CHANGELOG cannot be verified until the collection errors are fixed.

**W3. Inline Babel transpilation — 901 KB index.html.** Slow first-render, no sourcemaps,
no incremental compilation, and no cache-busting. Any JS change requires `build_ui.py` + a
manual browser reload.

**W4. No schema migration system across six SQLite databases.** Column additions, index
changes, and schema evolution are silent no-ops on existing installations.

**W5. Vault encryption dark by default.** `FRIDAY_VAULT_PASSPHRASE` must be set manually.
New users who launch via tray icon or `.vbs` (no console) miss the startup warning and run
with plaintext vault indefinitely.

### Opportunities

**O1. Pre-compile Babel.** One `esbuild` or `Vite` build step produces a minified JS bundle,
eliminates the 500 KB transpiler from page load, enables sourcemaps, and drops initial render
time from multi-second to sub-second. `build_ui.py` can remain as the JSX assembler; the
bundler takes its output.

**O2. Service layer purge.** Move `request`/`session` reads from `services/agent.py` and
`services/model_router.py` into `routes/chat.py` (which already exists). Services become
unit-testable without Flask context, and scheduled tasks stop hitting application-context
errors.

**O3. Unified SQLite migration helper.** A 30-line `_ensure_columns(conn, table, columns)`
helper that runs `ALTER TABLE ADD COLUMN IF NOT EXISTS` for each missing column, shared across
all database initializers, closes the schema-drift gap without adding a dependency.

**O4. `friday setup` CLI.** The `friday` CLI entry point is already registered in
`pyproject.toml`. A `friday setup` subcommand that prompts for keys, stores them via
`credential_store.protect()`, and creates `~/.friday/.env` would eliminate the `start.bat`
pattern for Linux/macOS and remote-access users entirely.

**O5. Vault passphrase in Settings UI.** The `_VAULT_ENCRYPTION_STATE` dict (already
maintained in `core/__init__.py`, line 212) just needs a UI surface. A persistent banner in
Settings → Security tab ("Vault is unencrypted — click here to enable") would eliminate the
silent plaintext vault problem.

### Threats

**T1. API keys as plaintext `.bat` file.** `start.bat` in the repo root contains API keys
in plaintext. The file is gitignored but sits on disk readable by any process running as the
same user. Migrating to `credential_store.py` DPAPI for all users would close this.

**T2. Gemini Live model ID instability.** Preview model IDs (`gemini-3.1-flash-live-preview`)
are retired without notice. A stale ID causes 1008 errors that surface as "voice broken" with
no actionable diagnostic for the user.

**T3. NeMo GPU voice uninstallable on the current venv.** The venv has CPU-only PyTorch.
NeMo requires a CUDA-index-URL pip invocation not in `pyproject.toml`. Users selecting
`voice_engine: local-gpu` fall back silently to CPU Tier-1 with no persistent UI indicator.

**T4. headroom-ai has no Windows wheel.** Context compression silently no-ops on Windows
("0% saved"). Users who see the setting enabled will have larger-than-expected context budgets
consumed.

**T5. `_CC_RE` regex inconsistency is a live data-exfiltration risk.** A credit card number
in the 17–19 digit range would pass the sensitivity classifier (classified TIER_1 / public)
but be caught by the egress gate's core PII redactor. Depending on call order, it could leak
to cloud before the egress gate fires.

---

## Part 4 — Priority Fixes

### P1 — Fix 31 Test Collection Errors `[CRITICAL]`
**Problem:** `pytest --collect-only` aborts with 31 errors, masking an unknown number of
broken tests.  
**File:** `tests/unit/test_work_log.py`, `test_tool_hooks.py`, `test_timeline_engine.py`,
`test_workspace_temperature.py` (+ 27 others).  
**Fix:** For each failing file, run `pytest tests/unit/test_<name>.py -v 2>&1 | head 30` to
surface the import error. Most will be module-path renames from the decomposition (e.g.
`from server import X` → `from agent_friday.services.agent import X`). Fix imports one file at
a time and re-run `pytest -q` to track progress.

### P2 — Route Egress Gate Logs Through `_log` `[HIGH]`
**Problem:** `services/egress_gate.py` line 72 prints security decisions to stdout, bypassing
the structured log file. Under `pythonw.exe` these are silently discarded.  
**Fix:**
```python
# At module top (after imports):
_log = logging.getLogger("friday.egress")

# Replace lines 72-74:
_log.info("%s provider=%s field=%s tier=%s (%s)",
          verdict, provider, field, Tier.NAMES.get(tier, tier), reason)
```
Two-line change; the JSONL audit file is already correct.

### P3 — Resolve `_CC_RE` Regex Inconsistency `[HIGH]`
**Problem:** Credit-card regex in `core/__init__.py` (13–19 digits) differs from
`sensitivity_classifier.py` (13–16 digits). 17–19 digit cards get inconsistent tier
decisions.  
**Fix:** Export `_CC_RE` from `core/__init__.py` and import it in
`sensitivity_classifier.py`:
```python
# services/sensitivity_classifier.py line 41 → replace with:
from agent_friday.core import _CC_RE   # canonical 13–19 digit pattern
```

### P4 — Move Flask Imports Out of `services/agent.py` and `services/model_router.py` `[HIGH]`
**Problem:** Services import `request`, `session`, `Blueprint`, etc., blocking testability
and causing context errors in scheduled tasks.  
**Fix:** Audit every `request.*` and `session.*` usage in these files. Pass the values as
explicit parameters from the route layer (`workspace`, `model`, `session_id`, etc.). Extract
remaining Blueprint handlers into `routes/chat.py`. This is the most invasive fix but is the
architectural prerequisite for reliable scheduled-task execution.

### P5 — Fix `introspection.py` FRIDAY_DIR Import `[MEDIUM]`
**Problem:** `services/introspection.py` lines 37–38 redefine `HOME` and `FRIDAY_DIR`
instead of importing from `core`, ignoring the `FRIDAY_HOME` env-var override.  
**Fix:** Replace those two lines with:
```python
from agent_friday.core import FRIDAY_DIR, HOME
```

### P6 — Replace Login Template `str.replace()` with Jinja2 `[MEDIUM]`
**Problem:** `core/__init__.py` line 396 uses `LOGIN_HTML.replace('{{ error }}', error)`,
bypassing XSS escaping.  
**Fix:**
```python
from markupsafe import Markup
html = LOGIN_HTML.replace('{{ error }}', str(Markup.escape(error)))
```
Or move `LOGIN_HTML` to `templates/login.html` and use `render_template("login.html",
error=error)` with Jinja2 auto-escaping enabled.

### P7 — Add Write Lock to `CHAT_HISTORY` `[MEDIUM]`
**Problem:** `core/__init__.py` line 1767: global list, no lock, concurrent appends race.  
**Fix:** Add `_CHAT_HISTORY_LOCK = threading.Lock()` alongside `_SETTINGS_CACHE_LOCK` and
acquire it in every write path (`_save_chat_history` and any mutation of `CHAT_HISTORY`).

### P8 — Surface Vault Encryption State in Settings UI `[MEDIUM]`
**Problem:** `FRIDAY_VAULT_PASSPHRASE` unset means plaintext vault, but the UI shows nothing.  
**Fix:** `_VAULT_ENCRYPTION_STATE` (`core/__init__.py` line 212) already tracks `enabled`,
`error`, and `warning`. Add a status row to the Settings → Security tab that shows "🔒
Encrypted" or "⚠️ Unencrypted — set FRIDAY_VAULT_PASSPHRASE" based on this dict.

### P9 — Pre-Compile Babel at Build Time `[MEDIUM]`
**Problem:** 901 KB `index.html` with 500+ KB inline Babel runtime, slow renders,
no sourcemaps.  
**Fix:** Add a build step (esbuild or Vite) that transpiles the JSX assembled by
`build_ui.py`. Output a `static/bundle.js` referenced by a single
`<script src="/static/bundle.js">` tag. The build step runs on commit (pre-commit hook or CI).

### P10 — Add SQLite Schema Migration Helper `[MEDIUM]`
**Problem:** Six SQLite databases, all using `CREATE TABLE IF NOT EXISTS`. Column additions
are silent no-ops on existing installations.  
**Fix:** Add a shared utility:
```python
def _ensure_columns(conn, table: str, columns: dict[str, str]):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, definition in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    conn.commit()
```
Call from each module's `_init_db()` after initial schema creation.

### P11 — Validate Voice Model ID at Startup `[LOW]`
**Problem:** `DEFAULT_SETTINGS["voice_model"]` may reference a retired Gemini Live preview
model ID, causing opaque 1008 failures on first voice session.  
**Fix:** In `services/voice_engine.py`, log a `WARNING` and set a `voice_model_warning` flag
in `/api/health` if the configured voice model returns 404 or is absent from the Live API
model list. Surface this in the Settings → Voice tab.

### P12 — Add `friday setup` CLI Subcommand `[LOW]`
**Problem:** New users must discover `start.bat` to inject API keys; no shell-agnostic path
exists.  
**Fix:** Extend `src/agent_friday/cli.py` with a `friday setup` subcommand that prompts for
keys interactively, stores them via `credential_store.protect()`, sets
`FRIDAY_VAULT_PASSPHRASE` in a sourced `~/.friday/.env`, and runs a quick provider health
check. Eliminates the `.bat` dependency for Linux/macOS and remote-access users.

---

## Part 5 — Developer Onboarding Guide

### Prerequisites

- **Python 3.10+** (3.12 recommended; 3.13 is untested)
- **Git**
- **Windows 10/11** for full features (DPAPI credential encryption, computer control,
  global kill-switch). macOS and Linux work for all non-Windows features.
- **Ollama** (optional but recommended for local inference): install from `https://ollama.ai`,
  then `ollama pull gemma4:latest`

### Clone and Install

```bash
git clone <repo>
cd friday-desktop

# Create and activate a virtual environment
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install all optional extras for development
pip install -e ".[all]"

# Do NOT install [voice-local-gpu] unless you have an NVIDIA GPU with CUDA
# and are prepared to reinstall torch with the correct CUDA index URL separately.
```

> **Windows gotcha:** The bare `python` command on Windows may resolve to the system Python or
> another venv (e.g. hermes-agent). When in doubt use `venv\Scripts\python.exe` explicitly.
> See the memory note: "python on PATH is the hermes venv".

### Configure API Keys

**Option A — Environment variables (recommended for development):**
```batch
set GEMINI_API_KEY=your-key
set ANTHROPIC_API_KEY=your-key
set FRIDAY_VAULT_PASSPHRASE=your-strong-passphrase
```

**Option B — `start.bat` (current production pattern):**
The server reads `start.bat` in the repo root and bootstraps `set NAME=VALUE` lines into
`os.environ` via `core/__init__.py:_bootstrap_env_from_launch_scripts()`. The `_FORCE_OVERRIDE`
set in that function ensures API keys from `start.bat` always win over stale Windows User-scope
env vars (the fix for the Gemini 1008 voice auth failures).

**Option C — Setup wizard:**
On first run the wizard fires at `http://localhost:3000/` and stores keys via
`services/credential_store.py` (DPAPI on Windows, AES-256-GCM + Argon2id otherwise).

### Run the Dev Server

```bash
python server.py
# Server listens at http://localhost:3000 (or $FRIDAY_PORT)
# Logs: ~/.friday/friday.log (rotating, 10 MB × 3 backups)
# WARNING+ is also mirrored to stderr

# To use a different port:
FRIDAY_PORT=3010 python server.py
```

**Critical:** The server runs with `debug=False` (no reloader). Changes to `routes/*.py`,
`services/*.py`, or `core/__init__.py` require a full `Ctrl+C` and restart. The browser
front-end does not hot-reload — force-refresh after any `build_ui.py` run.

### Build the UI

The 901 KB `index.html` is built from three source files in `ui_parts/`. After editing any
`ui_parts/*.html`, regenerate it:

```bash
python src/agent_friday/ui/build_ui.py
# Output: index.html (repo root) — committed to git
```

`app.html` (8138 lines) contains all React workspace components. `head.html` contains meta
tags and CDN imports. `styles_and_scene.html` contains CSS and the Three.js holographic scene.

### Run Tests

```bash
# Full suite (using the correct venv Python):
venv\Scripts\python.exe -m pytest -q    # Windows
python -m pytest -q                      # macOS/Linux

# Skip known-failing collection errors while they're being repaired:
python -m pytest -q \
  --ignore=tests/unit/test_timeline_engine.py \
  --ignore=tests/unit/test_tool_hooks.py \
  --ignore=tests/unit/test_work_log.py \
  --ignore=tests/unit/test_workspace_temperature.py

# Single module:
python -m pytest tests/unit/test_egress_gate.py -v

# FRIDAY_TESTING=1 is set by tests/conftest.py — it suppresses daemon threads,
# stubs all LLM entry points, and redirects ~/.friday to a tmpdir.
# NEVER import server without this set unless you want background threads to start.
```

### Architecture Mental Model

The module dependency DAG is: **`routes` → `services` → `core` → `(stdlib + Flask)`**.

```
core/__init__.py        — Flask app object, all shared state, settings, PII, auth, network state
core/config.py          — re-exports path constants (FRIDAY_DIR, WIKI_DIR, …) for lightweight imports
services/model_router.py— HOW to execute a model call (_call_claude, _call_ollama, _call_openai)
routing/model_router.py — WHICH provider/model to use (get_router, provider_family, task_overrides)
services/agent.py       — tool registry, CLAUDE_TOOLS, _execute_tool, _call_claude_agent, _generate_agent
services/orchestrator.py— dual-role orchestration (WorkerTask, WorkerResult, adapters, budgets)
routes/*.py             — HTTP handlers; one Blueprint per file, auto-discovered, no business logic
```

**The two router modules are distinct and important:**
- `routing/model_router.py` answers "which provider?" (routing decisions, offline overlay)
- `services/model_router.py` answers "how to call it?" (execution: HTTP, tool loop, streaming)

Import from `routing/` when you only need routing decisions. Import from `services/` when you
need to actually make a model call.

### Adding a New Service Capability

1. Create `src/agent_friday/services/my_feature.py`
2. Import `from agent_friday.core import FRIDAY_DIR, _load_settings` as needed
3. **Do not** import from Flask. **Do not** import from `routes/`.
4. Write a test in `tests/unit/test_my_feature.py`. Copу `tests/unit/test_egress_gate.py` as
   a template — it shows how the conftest stubs work.
5. If you need a scheduled job, call `register_builtin_task()` in `services/scheduler.py`.

### Adding a New API Route

1. Create `src/agent_friday/routes/my_feature.py`
2. Define at module level:
   ```python
   from flask import Blueprint, jsonify, request
   my_feature_bp = Blueprint("my_feature", __name__)
   ```
3. Add handlers with `@my_feature_bp.route("/api/my_feature/...")` decorators
4. Done — Blueprint auto-discovery picks it up on the next server restart
5. Optional: add `tests/api/test_my_feature.py` using the api conftest

### Adding a New Workspace

1. Add an entry to `DOCK_GROUPS` in `ui_parts/app.html`:
   ```js
   {id:'myws', ico:'🔧', label:'My Workspace', core:true}
   ```
2. Create a React component in `ui_parts/app.html` — search for `function NewsWS()` as a
   template for the standard layout (header + tabs + content area + floating window).
3. Optionally add routes in a new Blueprint file.
4. Run `python src/agent_friday/ui/build_ui.py` to regenerate `index.html`.
5. Restart the server and hard-refresh the browser.

### Key Files to Know First

| File | Why You Need It |
|------|----------------|
| `src/agent_friday/core/__init__.py` | Flask app, ALL shared state, settings, PII, auth — read this first |
| `src/agent_friday/server.py` | Wiring: Blueprint discovery, daemon startup sequence |
| `src/agent_friday/services/agent.py` | Tool registry, `CLAUDE_TOOLS`, `_execute_tool`, agent loop |
| `src/agent_friday/services/model_router.py` | `_generate_text()`, `_call_claude()`, `_call_ollama()` |
| `src/agent_friday/services/egress_gate.py` | Security boundary — read before touching any cloud call |
| `ui_parts/app.html` | All 8138 lines of React workspaces and components |
| `tests/conftest.py` | LLM stubs, tmpdir redirect — understand this before writing tests |

### Common Pitfalls

| Pitfall | Solution |
|---------|----------|
| `RuntimeError: Working outside of application context` | You're calling `request`/`session` from a service. Pass values as explicit parameters from the route layer. |
| Tests read/write real `~/.friday` data | `tests/conftest.py` redirects HOME — verify `FRIDAY_TESTING=1` is set in your test invocation. |
| Server changes don't take effect | No hot-reload. `Ctrl+C` + `python server.py`. |
| Voice 1008 auth errors | Stale Gemini key in Windows User-scope env vars shadowing `start.bat`. Open a fresh terminal and `echo %GEMINI_API_KEY%` to verify the right key is loaded. |
| `index.html` out of date after UI edits | Run `python src/agent_friday/ui/build_ui.py` and hard-refresh. |
| Vault stays plaintext | Set `FRIDAY_VAULT_PASSPHRASE` in the environment *before* starting the server. |
| Headroom shows "0% saved" | Expected on Windows — no wheel available for headroom-ai 0.22.x. Context compression falls back to passthrough silently. |
| `NeMo/GPU voice` falls back to CPU Tier-1 silently | The venv torch is CPU-only. Install torch-CUDA via the NVIDIA pip index separately before enabling `voice_engine: local-gpu`. |
| `bare python` resolves to wrong environment | Use `venv\Scripts\python.exe` explicitly on Windows. |

### Environment Variables Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMINI_API_KEY` | (none) | Google Gemini + Lyria + Veo + voice |
| `ANTHROPIC_API_KEY` | (none) | Claude (Opus/Sonnet/Haiku) |
| `OPENAI_API_KEY` | (none) | OpenAI-compatible provider |
| `FRIDAY_VAULT_PASSPHRASE` | (none) | Vault AES-256-GCM encryption (strongly recommended) |
| `FRIDAY_PASSWORD` | (none) | Fallback for both vault KDF and HTTP auth (separate the two via dedicated vars) |
| `FRIDAY_REMOTE_KEY` | (none) | HTTP auth key for remote/tunnel access |
| `FRIDAY_PORT` | `3000` | Server listen port |
| `FRIDAY_BIND_HOST` | `127.0.0.1` | Bind address (loopback only by default) |
| `FRIDAY_TRUST_LOOPBACK` | `1` | Set `0` to require login even from localhost |
| `FRIDAY_SANDBOX_MODE` | `confine` | Tool sandbox: `off` / `confine` / `strict` |
| `FRIDAY_TESTING` | (unset) | Set `1` to suppress daemons and stub LLMs for tests |
| `FRIDAY_VOICE_DEBUG` | (unset) | Set `1` to enable verbose voice logging |
| `FRIDAY_HOME` | `~` | Override base home dir (used by local voice and tests) |

---

*Generated July 2026 · STORM v2 · Perspectives: Security, DevOps, Software Architect, UX/Frontend, Product, End User*
