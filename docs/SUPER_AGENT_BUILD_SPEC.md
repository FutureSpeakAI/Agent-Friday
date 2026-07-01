# Agent Friday v5.0 — Super Agent Build Spec
*The final transformation: developer tool → sovereign consumer product.*
*FutureSpeak.AI · Asimov's Mind · July 2026*

---

## 0. Guiding principles

1. **Sovereign by default.** Everything new must pass through the existing
   `services/egress_gate.py` + `services/sensitivity_classifier.py`. Learning,
   dreaming, and user-modeling are **local-only** subsystems — they never call
   cloud. Channel bridges route every inbound/outbound message through the same
   agent loop + egress gate as the chat UI.
2. **Zero-friction install.** From `git clone` to a talking agent in ≤5 minutes,
   with **no cloud API key required**. Bundled Gemma via Ollama is the default
   brain. Cloud keys are optional upgrades.
3. **Google open-source preference.** `gemma-3-4b-it` (via Ollama, tag
   `gemma3:4b`) is the recommended local model — runs on ~8 GB RAM. Larger Gemma
   tags documented as upgrades.
4. **No new architectural debt.** New code obeys `routes → services → core →
   stdlib`. Services must not import Flask. Every new service is a leaf module
   with a graceful-degradation envelope (return a well-formed dict, never raise
   to the caller). Every new subsystem ships with unit tests copied from an
   existing template (`tests/unit/test_introspection.py`).

---

## 1. Learning Loop Engine — `services/learning_loop.py`

A local, closed-loop self-improvement engine. Observes task outcomes, mines
successful patterns into **skill candidates**, scores them, and promotes the
best to active use. Persists in SQLite. Never touches cloud.

### Data model — `~/.friday/learning.db`
```
observations(
  obs_id TEXT PK, ts REAL, task_type TEXT, prompt_hash TEXT,
  approach TEXT,           -- short label of the strategy used
  success INTEGER,         -- 1/0
  satisfaction REAL,       -- 0..1 (from user rating / implicit signals)
  revisions INTEGER,       -- how many times the user asked to redo
  duration_s REAL, tokens INTEGER, workspace TEXT, meta_json TEXT
)
skills(
  skill_id TEXT PK, name TEXT, task_type TEXT, created_ts REAL,
  pattern TEXT,            -- distilled instruction / heuristic
  status TEXT,             -- candidate | validating | active | retired
  score REAL, trials INTEGER, wins INTEGER, source_obs_json TEXT
)
skill_trials(
  trial_id TEXT PK, skill_id TEXT, ts REAL, success INTEGER,
  satisfaction REAL, note TEXT
)
```

### Public API (all pure/side-effect-scoped, return dict envelopes)
- `observe(task_type, prompt, *, approach, success, satisfaction=None, revisions=0, duration_s=0, tokens=0, workspace="", meta=None) -> dict` — record one outcome.
- `mine_candidates(min_success=0.7, min_samples=3) -> list[dict]` — cluster
  observations by `(task_type, approach)`, emit skill candidates where the
  success rate and sample count clear the floor. Idempotent (dedups by pattern).
- `score_skill(skill_id) -> float` — Wilson-lower-bound over `skill_trials`
  wins/trials, blended with mean satisfaction.
- `record_trial(skill_id, success, satisfaction=None, note="") -> dict`.
- `promote(threshold=0.65, min_trials=3) -> list[dict]` — candidate→active when
  score clears threshold; active→retired when score decays below `retire=0.4`.
- `active_skills(task_type=None) -> list[dict]` — the promoted heuristics, for
  injection into the agent's system prompt (Ring-0, local).
- `run_epoch() -> dict` — one full cycle: `mine_candidates` → `promote` →
  summary. This is what the scheduler drives weekly.
- `state() -> dict` — counts by status, top skills, last epoch summary (UI).

### cLaws / governance
Learning is Ring-0 (local read/write under `~/.friday`). `run_epoch` respects a
`settings["learning_loop"]["enabled"]` flag (default **True**) and a hard cap on
`max_active_skills` (default 50) so the prompt-injection budget stays bounded.
Skill patterns are **text heuristics**, never executable code — no eval, no new
tool surface. They are injected as advisory system-prompt lines only.

### Integration
- `services/agent.py` chat/agent turn calls `learning_loop.observe(...)` on
  completion (best-effort, wrapped in try/except).
- `_get_friday_system_prompt` folds `active_skills()` into a
  `== LEARNED HEURISTICS ==` block (bounded to N lines).
- Scheduler builtin `learning_epoch` (weekly, Sun) → `run_epoch()`.

---

## 2. Memory Dreaming — `services/memory_dreaming.py`

Overnight consolidation. Reviews the day's conversation turns from ChromaDB
(`conversation_memory.get_conversation_memory()`), extracts recurring topics and
durable facts, writes consolidated long-term entries, and prunes noise. **All
local** — heuristic pattern extraction + the local embedding model already
present; no cloud call.

### Data model — `~/.friday/dreams/` + `~/.friday/dreams.db`
```
dreams(
  dream_id TEXT PK, ts REAL, day TEXT,            -- YYYY-MM-DD consolidated
  turns_reviewed INTEGER, topics_json TEXT,
  consolidated_json TEXT,                          -- durable entries created
  pruned INTEGER, summary TEXT
)
```
Each run also writes a human-readable `~/.friday/dreams/<day>.md`.

### Public API
- `dream(day=None, *, memory=None) -> dict` — the consolidation pass for one day
  (defaults to yesterday). Steps:
  1. Pull turns for `day` via `memory.recent(n=...)` (filtered by date).
  2. **Topic extraction** — keyword frequency (reuse
     `conversation_memory.extract_keywords`) → top topics.
  3. **Durable-fact mining** — sentences matching preference/decision/fact
     patterns ("I prefer", "we decided", "my … is", "remember that").
  4. **Consolidate** — write durable facts to long-term store
     (`consolidated_json`) and, when high-confidence, hand to
     `user_model.note_fact(...)`.
  5. **Prune noise** — flag one-off clarifications / greetings for retention
     policy (respects `context_retention_days`); never deletes user data
     destructively — only tags low-value turns as `low_value` metadata.
- `recent_dreams(n=7) -> list[dict]`.
- `state() -> dict`.

### Scheduler
Builtin `memory_dreaming` — **daily 03:00 local**, `notify="silent"`. Guarded by
`settings["memory_dreaming"]["enabled"]` (default **True**).

---

## 3. User Modeling — `services/user_model.py`

Tracks who the user is and how they like to work, and injects a compact model
into the system prompt. Persists in SQLite. Respects vault tiers — the injected
summary is TIER_1 (behavioral prefs, never raw PII).

### Data model — `~/.friday/user_model.db`
```
traits(
  key TEXT PK, value TEXT, confidence REAL, updated_ts REAL, evidence INTEGER
)
facts(
  fact_id TEXT PK, ts REAL, category TEXT,        -- preference|expertise|workflow|bio
  text TEXT, confidence REAL, source TEXT
)
signals(
  sig_id TEXT PK, ts REAL, kind TEXT, value TEXT  -- raw observations feeding traits
)
```

### Tracked dimensions (traits)
- `comm.formality` (0 casual … 1 formal), `comm.verbosity` (0 terse … 1 verbose)
- `expertise.<domain>` (0 novice … 1 expert) — inferred from vocabulary + asks
- `workflow.active_hours` (histogram), `workflow.top_tools`, `workflow.top_workspaces`

### Public API
- `observe_message(text, *, role, workspace="", ts=None)` — update comm/​expertise
  signals from a message (heuristic; local).
- `observe_event(kind, value)` — tool use, workspace switch, session time.
- `note_fact(category, text, *, confidence=0.6, source="dream")` — durable fact
  (called by memory dreaming + explicitly).
- `get_trait(key, default=None)`, `set_trait(key, value, confidence, evidence=1)`.
- `render_user_model_prompt(max_facts=8) -> str` — compact `== USER MODEL ==`
  block for the system prompt. Empty string when nothing learned yet.
- `profile() -> dict` — full model for the Settings UI.
- `forget(category=None)` — GDPR-style reset (all or by category).

### Integration
`_get_friday_system_prompt` folds `render_user_model_prompt()` in after
self-knowledge. `routes/chat.py` calls `observe_message` per user turn
(best-effort). Gated by `settings["user_modeling"]["enabled"]` (default **True**).

---

## 4. SOUL.md Personality Config — `services/soul.py` + core hook

Move personality from hardcoded defaults to a **user-editable markdown file** at
`~/.friday/SOUL.md`. Friday reads it at startup and on change.

### Files
- `~/.friday/SOUL.md` — canonical personality. Seeded on first load with the
  current Friday persona rendered as markdown.
- Legacy `~/.friday/agent-personality.txt` remains a fallback.

### `services/soul.py` API
- `soul_path() -> Path`
- `default_soul() -> str` — the shipped markdown (Jarvis-with-a-newsroom-editor).
- `ensure_soul() -> Path` — create SOUL.md from default if missing.
- `load_soul() -> str` — full markdown (cached by mtime).
- `save_soul(text) -> dict` — validate (non-empty, ≤ 32 KB) + write atomically,
  bump version history in `~/.friday/soul_history/`.
- `render_personality() -> str` — the text handed to the system prompt (the file
  body, minus an optional front-matter title).
- `state() -> dict` — path, exists, bytes, mtime, version count.

### core hook
`core/__init__.py:_load_agent_personality()` is modified to prefer `SOUL.md`
(new constant `SOUL_FILE = FRIDAY_DIR / "SOUL.md"`) over `agent-personality.txt`
over `DEFAULT_AGENT_PERSONALITY`. No service import from core — core reads the
file directly; `services/soul.py` owns creation/validation/history.

### UI
Settings → Personality tab gets a SOUL.md editor (textarea + Save + Reset to
default + version history). Route-backed (`/api/soul`).

---

## 5. Bundled Gemma — installer + defaults + doctor

### Defaults (`core/__init__.py:DEFAULT_SETTINGS`)
- `model_routing.local_model` → **`gemma3:4b`** (was `gemma4:latest`).
- `capability_routing.local.model` → **`gemma3:4b`**.
- New `setup.bundled_model = "gemma3:4b"` and `setup.no_key_mode` flag.
- Fallback logic in `model_router._pick_local_model` prefers `gemma3:4b`, then
  any installed gemma tag, then the first installed model.

### Installers (`scripts/install.{bat,ps1,sh}`)
Each script, after venv + deps + UI build, runs a **model bootstrap**:
1. Detect Ollama (`ollama --version`). If missing, install:
   - Windows: download + silent-run `OllamaSetup.exe` (or instruct via winget).
   - macOS: `brew install ollama` (fallback: curl script).
   - Linux: `curl -fsSL https://ollama.com/install.sh | sh`.
2. `ollama pull gemma3:4b` (skips if already present).
3. Write `local_model=gemma3:4b` into settings and set `no_key_mode=true` when
   no cloud key is present.
Model bootstrap is **best-effort + skippable** (`--no-model` flag / env
`FRIDAY_SKIP_MODEL=1`) — install never hard-fails on a missing GPU or slow pull.

### `friday doctor` (`cli.py`)
Add checks: Ollama installed + running, `gemma3:4b` pulled, and a "no-key mode
ready" green line when local chat is available even with zero cloud keys. Add a
shared helper `_ollama_probe()` used by both `cmd_status` and `cmd_health`.

### No-API-key mode
`services/demo_mode.py` already returns canned replies when **no** provider is
configured. v5 changes the story: when Ollama+Gemma are present, `no_key_mode`
routes chat to local Gemma (real answers) and only **degrades** creative/voice
(clear "needs a cloud key" notice) — never a dead demo.

---

## 6. Voice-First Onboarding — `services/onboarding.py` + `routes/onboarding.py`

On first run (`~/.friday/.setup_complete` absent), Friday greets by voice and
walks setup. Backend provides the script + state machine; the UI drives TTS/mic.

### State machine (`~/.friday/onboarding.json`)
```
{ step, name, voice_pref, keys_added[], identity_pubkey, complete, started_ts }
```
Steps: `greet → name → voice_test → keys(optional) → identity → soul → done`.

### API (`routes/onboarding.py`)
- `GET  /api/onboarding/state` — current step + the line Friday should speak.
- `POST /api/onboarding/step` — advance with `{answer}`; returns next line.
- `POST /api/onboarding/complete` — writes `.setup_complete`, generates the
  Ed25519 federation identity (reuse `services/federation.py`), and creates
  `SOUL.md` from the user's stated preferences.

### Greeting
> "Hi, I'm Friday. I'm your personal AI, and I run right here on your computer.
> No cloud required. Let me help you get set up — what should I call you?"

The greeting/step lines are pure functions (unit-testable). Voice output uses the
existing local-voice engine (`voice_engine: local`) so onboarding **talks with
zero cloud keys**.

---

## 7. Channel Integration — `services/channels/`

Connect Friday to messaging platforms. Every inbound message runs through the
**same agent loop + egress gate** as the chat UI. Ship Discord + Telegram.

### Files
- `channels/__init__.py`
- `channels/base.py` — `ChannelAdapter` ABC: `name`, `configure(cfg)`,
  `start()`, `stop()`, `send(chat_id, text)`, `on_message(handler)`, `status()`.
- `channels/discord_bridge.py` — Discord bot (long-poll / gateway via
  `discord.py` **if installed**, else a documented no-op with `status="missing_dep"`).
- `channels/telegram_bridge.py` — Telegram Bot API over stdlib `urllib`
  (long-poll `getUpdates`; no third-party dep required).
- `channels/manager.py` — registry, config load/save
  (`~/.friday/channels.json`), lifecycle, and the shared
  `handle_incoming(channel, chat_id, text) -> reply` that funnels to
  `services.agent._generate_agent` and back through `send`.

### Security
- Bot tokens stored via `services/credential_store.py` (never in channels.json).
- Every reply passes `egress_gate` before send (a channel is an egress).
- Per-channel allowlist of chat IDs (default: empty = reply to none until the
  user authorizes a chat) so a public bot can't be driven by strangers.
- Channels disabled by default (`settings["channels"]["enabled"]=False`).

### API (`routes/channels.py`)
- `GET  /api/channels` — configured channels + status.
- `POST /api/channels/<name>/configure` — set token (→ credential store) + opts.
- `POST /api/channels/<name>/start|stop`.
- `POST /api/channels/<name>/test` — send a test message.

---

## 8. Settings additions (`DEFAULT_SETTINGS`)

```python
"learning_loop":  {"enabled": True, "max_active_skills": 50, "epoch_weekday": 6},
"memory_dreaming":{"enabled": True, "hour": 3, "keep_topics": 12},
"user_modeling":  {"enabled": True, "inject_prompt": True},
"soul":           {"path": "~/.friday/SOUL.md"},   # informational
"setup":          {"bundled_model": "gemma3:4b", "no_key_mode": False},
"channels":       {"enabled": False, "discord": {}, "telegram": {}},
"onboarding":     {"voice_first": True},
```
Plus `model_routing.local_model` and `capability_routing.local.model` →
`gemma3:4b`.

---

## 9. Routes to add (auto-discovered blueprints)

| File | Blueprint | Paths |
|------|-----------|-------|
| `routes/soul.py` | `soul_bp` | `/api/soul`, `/api/soul/reset`, `/api/soul/history` |
| `routes/user_model.py` | `user_model_bp` | `/api/user-model`, `/api/user-model/forget` |
| `routes/learning.py` | `learning_bp` | `/api/learning/state`, `/api/learning/epoch`, `/api/learning/skills` |
| `routes/dreaming.py` | `dreaming_bp` | `/api/memory/dream`, `/api/memory/dreams` |
| `routes/channels.py` | `channels_bp` | `/api/channels/*` |
| `routes/onboarding.py` | `onboarding_bp` | `/api/onboarding/*` |

---

## 10. Scheduler wiring (`services/scheduler.py:_register_default_builtin_tasks`)

```python
register_builtin_task("memory_dreaming", memory_dreaming.dream,
    label="Memory dreaming", default_trigger="daily",
    default_spec={"hour": 3, "minute": 0}, notify="silent")
register_builtin_task("learning_epoch", learning_loop.run_epoch,
    label="Learning epoch", default_trigger="weekly",
    default_spec={"weekday": 6, "hour": 4, "minute": 0}, notify="on_complete")
```
Both lazily imported + wrapped in try/except like the existing roster.

---

## 11. System-prompt integration (`model_router._get_friday_system_prompt`)

After the SELF-KNOWLEDGE block, fold in (best-effort, each guarded):
1. `== USER MODEL ==` from `user_model.render_user_model_prompt()`.
2. `== LEARNED HEURISTICS ==` from `learning_loop.active_skills()` (bounded).

Both are local, TIER_1, and skipped when their `enabled` flag is off or the
subsystem returns empty.

---

## 12. Tests to write (`tests/unit/`)

| File | Covers |
|------|--------|
| `test_soul.py` | default/ensure/load/save/render/history, mtime cache |
| `test_user_model.py` | observe_message→traits, note_fact, render prompt, forget |
| `test_learning_loop.py` | observe→mine→promote→active_skills, Wilson score, run_epoch |
| `test_memory_dreaming.py` | dream() over a stub memory, topic + fact extraction, prune tagging, graceful-empty |
| `test_channels.py` | manager config roundtrip, telegram bridge parse, handle_incoming funnel (agent stubbed), egress on send |
| `test_onboarding.py` | state machine steps are pure, complete writes marker + identity |

All copy the isolation pattern from `tests/unit/test_introspection.py` (single
module import, `FRIDAY_TESTING=1`, temp home from root conftest).

---

## 13. Build / verify / release loop

1. Implement feature → `python -m pytest tests/unit/test_<feature>.py -q` → fix.
2. After all features: full suite `python -m pytest -q` (target ≥ prior count,
   0 new failures).
3. `python -m agent_friday.ui.build_ui` (rebuild index.html for the new Settings
   panels).
4. `FRIDAY_TESTING=1 python -c "import agent_friday.server"` — clean import.
5. `friday doctor` — Ollama/Gemma/keys/encryption all reported.
6. Update `CHANGELOG.md` + `docs/RELEASE_NOTES_v5.0.md` + README Quick Start.
7. Commit: *"feat: Agent Friday v5.0 — Super Agent transformation …"*; push.
8. Tag `v5.0.0`; PyInstaller `AgentFriday.exe`; GitHub release with the .exe.
   *(The .exe build + public release is an owner-gated, outward-facing step —
   prepared here, executed on owner confirmation.)*

---

## 14. Non-goals / explicitly rejected

- **Rejected security postures from Hermes/OpenClaw.** Nothing bypasses the
  egress gate or classifier. Learning/dreaming/user-model are local-only.
- No executable skill code from the learning loop (text heuristics only).
- No always-on public channel exposure (allowlist-gated, disabled by default).
- No cloud dependency introduced anywhere in the default path.
