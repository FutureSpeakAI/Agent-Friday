# Architecture — Agent Friday / Asimov's Mind

> Technical reference for the Friday Desktop system.  
> Last updated: 2026-05-28 · v4.2

---

## System Hierarchy

```
Asimov's Mind
│
├─ Agent Friday (persona + reasoning engine)
│   ├─ friday-desktop (web interface + API server)
│   │   └─ cLaws (governance policy engine)
│   └─ SkillOpt Engine (self-improving skills fleet)
│       ├─ job_scanner
│       └─ application_engine
│
├─ Vault (~/.friday/)
│   ├─ settings.json
│   ├─ agent-personality.txt
│   ├─ trust_graph.json
│   ├─ personality.json
│   ├─ chat_history.json
│   ├─ job_tracker.json           (career pipeline)
│   ├─ skills/                    (lightweight YAML skills)
│   ├─ skillopt/                  (versioned + measured skills)
│   │   └─ <skill_name>/
│   │       ├─ versions/v001.md, …
│   │       ├─ metrics.jsonl
│   │       ├─ best_skill.md
│   │       └─ research_log.jsonl
│   ├─ vault/
│   │   ├─ decision-bom.jsonl     (HMAC-signed audit log)
│   │   └─ context-log/           (daily JSONL activity logs)
│   └─ wiki/
│
└─ Wiki (~/wiki/)
    ├─ professional/
    ├─ family/
    ├─ legal/
    ├─ identity/
    └─ ...
```

**Asimov's Mind** is the governance and identity layer — the philosophical framework that gives Agent Friday its principles, its memory, and its boundaries.

**Agent Friday** is the persona — calm, perceptive, loyal. It manifests as a Claude-powered reasoning agent with tool access, a personal wiki, and a trust graph.

**Friday Desktop** is the interface — a Flask server assembling a Three.js holographic desktop in the browser.

**cLaws** is the policy engine — the Four Laws encoded as code, signing every decision.

---

## Seven Core Systems

### 1. Governance Gate (cLaws)

Every tool invocation passes through `_evaluate_policy()` before execution. The gate enforces four privilege rings and signs each decision with HMAC-SHA256.

**Decision Record format** (appended to `~/.friday/vault/decision-bom.jsonl`):
```json
{
  "timestamp": "2026-05-26T14:23:01.123Z",
  "tool": "run_command",
  "ring": 2,
  "policy": "network_required",
  "decision": "allow",
  "reason": "Authenticated session, ring 2 permitted",
  "hmac": "sha256:abc123..."
}
```

The HMAC key is derived from a stable machine identifier — records cannot be forged or silently edited. This is the Decision BOM (Bill of Materials): a complete audit trail of everything the agent has ever done.

**Ring definitions:**
- **Ring 0** — Read-only. Always allowed. No side effects.
- **Ring 1** — Local write. Always allowed. Affects only `~/.friday/` and `~/wiki/`.
- **Ring 2** — Network. Requires authenticated session (always true in normal use).
- **Ring 3** — OS Control. Requires `CC_ENABLED` flag set by user. Has rate limiter (20 actions/sec) and kill switch.

**Blocked operations** (hard-coded, cannot be overridden):
`rm`, `del`, `rmdir /s`, `format`, `shutdown`, `reg delete`, `taskkill`, `wipe`

---

### 2. Privacy Shield

Applied bidirectionally — before prompts reach Claude and before tool outputs re-enter the context.

**Auto-redacted patterns:**
| Pattern | Regex |
|---------|-------|
| SSN | `\d{3}-\d{2}-\d{4}` |
| Credit card | 13–19 consecutive digits |
| Phone | `(\+1\s?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}` |
| Email | Standard RFC-5321 |
| Street address | `\d+\s+\w+\s+(St\|Ave\|Rd\|...)` |

**Smart mode** (default): Tags PII as `[PII:type:hash]` and keeps a per-request lookup table to rehydrate after the model responds. The model never sees the raw value; the user sees the response with PII restored.

**Watchlist**: Additional tokens defined in `~/.friday/privacy_shield.json`:
```json
{"watchlist": ["CompanyName", "ProjectCodename", "SSN-of-family-member"]}
```

**User email bypass**: Addresses in `settings.user_email` and `settings.owner_identities` pass through unredacted.

---

### 3. Smart Context Loader

`_build_context_prompt()` constructs the system prompt by layering six sources:

```
Layer 0: Agent personality (brief identity, from agent-personality.txt)
Layer 1: Response preferences (tone, length, communication style)
Layer 2: cLaws principles + tool catalog
Layer 2.5: Project context files (.friday-context.md / AGENTS.md from CWD)
Layer 3: Live vault data (personality, trust graph, memory, todos, epistemic)
Layer 4: Smart wiki routing (keyword → wiki section)
Layer 5: Vision description (if screenshot provided)
Layer 6: Workspace context (active panel state from frontend)
```

**Smart routing** — `_load_smart_context()`:
- Message contains career/job/resume keywords → load `~/wiki/professional/`
- Message contains family/kids/custody keywords → load `~/wiki/family/` + `~/wiki/legal/`
- Message contains a person's name → resolve in trust graph → load their file
- Message contains finance/budget keywords → load `~/wiki/finance/`
- Message contains health/medication keywords → load `~/wiki/health/`

**Soft cap**: Total context is trimmed to 200KB per turn to avoid token overruns.

---

### 4. Decision BOM Audit Chain

Every significant action in the system emits a structured event to the context log.

**Context log location**: `~/.friday/vault/context-log/YYYY-MM-DD.jsonl`

**Event types:**
```
chat_user      — user sent a message
chat_agent     — agent responded
tool_call      — agent called a tool (with args and result)
file_read      — read_file tool was used
file_write     — write_file tool was used
wiki_edit      — wiki was modified
skill_write    — a skill was created/modified
cc_action      — Ring 3 OS control action
task_spawn     — background task was started
browse_web     — web content was fetched
```

Each event includes: `timestamp`, `session_id`, `event_type`, `data`, `workspace`.

The log is append-only — no event is ever deleted. Retention is configurable; default is forever (`context_retention_days: 0`).

---

### 5. Background Task Runner

`spawn_task` submits a task to a thread pool. Each task:
1. Spawns a fresh Claude agent with the full tool suite
2. Logs progress to `task.log` in real time
3. Appends each tool call to `task.tool_trace`
4. Updates `task.status` through `queued → running → complete | failed`

The Task Tray in the UI polls `/api/tasks` every 3 seconds to show live status. Tasks are stored in memory (`TASKS` dict) and not persisted across server restarts.

**API:**
- `GET /api/tasks` — list all tasks
- `GET /api/tasks/<id>` — task detail + log + tool trace
- `DELETE /api/tasks/<id>` — cancel a running task

---

### 6. Wiki Management System

The personal wiki at `~/wiki/` is a flat directory of Markdown and text files, organized into sections. Friday can read, search, propose edits, and apply corrections.

**Operations:**
- `read_wiki(path)` — read a file by relative path
- `search_wiki(query)` — full-text search, returns top 5 hits with excerpts
- `propose_wiki_update(file, section, new_value, reason)` — queues an edit for user approval
- `correct_wiki(old_text, new_text)` — immediate global find-replace (user-initiated corrections)

**Pending approval workflow** — all agent-initiated wiki edits go through a review queue:
1. Agent calls `propose_wiki_update`
2. Entry written to `~/.friday/wiki-pending.json`
3. Bell icon in UI shows count of pending proposals
4. User approves or rejects each one
5. Approved edits are written atomically

Direct edits via the wiki panel in the UI bypass the approval queue (user is the author).

---

### 7. Holographic Scene System

The Three.js scene in `ui_parts/styles_and_scene.html` renders 13 named "evolution structures." Each has distinct geometry, animation parameters, and camera choreography.

**Evolution path** (auto-rotates every 4 days from first launch):
| Index | ID | Name |
|-------|----|------|
| 0 | CUBES | Genesis Lattice |
| 1 | ICOSAHEDRON | Sacred Sphere |
| 2 | NETWORK | Shannon Network |
| 3 | DOME | Geodesic Cathedral |
| 4 | ASTROLABE | Lovelace Astrolabe |
| 5 | TESSERACT | Von Neumann Tesseract |
| 6 | QUANTUM | Dirac Probability |
| 7 | MANDELBROT | Mandelbrot Set |
| 8 | MOBIUS | Turing Möbius |
| 9 | GRID | Ocean of Light |
| 10 | CABLES | Fibonacci Nerve |
| 11 | NONE | Transcendence |
| 12 | EDEN | Giga Earth (Rez) |

**Persistence**: `POST /api/evolution { preferred_scene_index: N }` pins a scene. Setting to `null` returns to auto-rotation. Preference stored in `~/.friday/personality.json`.

**Mood system**: Scene color and animation speed respond to the agent's current "mood" — LISTENING (cyan, slow), REASONING (magenta, medium), EXECUTING (orange, fast), EXCITED (bright, fast), CALM (dim, very slow).

---

## Tool Registry (All 30 Tools)

### Ring 0 — Read-Only (Always Allowed)
| Tool | Description |
|------|-------------|
| `read_file` | Read any file up to 500KB |
| `read_wiki` | Read a personal wiki file by relative path |
| `search_wiki` | Full-text search across wiki (top 5 results with excerpts) |
| `query_trust_graph` | Look up a person by name/alias in the trust graph |
| `query_calendar` | Check upcoming calendar events |
| `get_career_pipeline` | Read current job search status |
| `get_briefing` | Fetch the most recent daily briefing |

### Ring 1 — Local Write (Always Allowed)
| Tool | Description |
|------|-------------|
| `write_file` | Write or append to any file; auto-creates directories |
| `write_clipboard` | Copy text to the Windows clipboard |
| `propose_wiki_update` | Queue a wiki edit for user approval |
| `correct_wiki` | Immediate global find-replace across wiki + vault JSONs |
| `learn_skill` | Create, modify, delete, or list skill YAML workflows |

### Ring 2 — Network (Requires Auth)
| Tool | Description |
|------|-------------|
| `search_web` | DuckDuckGo search with snippets and URLs |
| `browse_web` | Fetch full page content via BeautifulSoup |
| `search_email` | Search Gmail (via connector, if configured) |
| `draft_email` | Draft an email (via connector, if configured) |
| `open_url` | Launch a URL in Chrome |
| `spawn_task` | Start a background agent task |
| `run_command` | Run a non-destructive PowerShell command |
| `install_package` | Install a pip or npm package |

### Ring 3 — OS Control (Requires CC Enabled)
| Tool | Description |
|------|-------------|
| `move_mouse` | Move cursor to (x, y) |
| `click` | Left/right/double-click at coordinates |
| `type_text` | Type a string (keyboard injection) |
| `press_key` | Press a key or chord (e.g., `ctrl+c`) |
| `screenshot` | Capture screen, returns base64 PNG |
| `scroll` | Scroll at coordinates |

### Computer-Science Support Tools (Internal)
| Tool | Description |
|------|-------------|
| `describe_screenshot` | Gemini vision describes a screenshot |
| `analyze_file` | Gemini analyzes an uploaded file |
| `generate_image` | Gemini image generation |
| `generate_code_art` | p5.js code art via Gemini |

---

## Data Model — settings.json

```json
{
  "agent_name": "AGENT FRIDAY",
  "orchestrator_model": "claude-opus-4-7",
  "subagent_model": "claude-sonnet-4-6",
  "creative_model": "gemini-2.5-flash",
  "voice_model": "gemini-3.1-flash-live-preview",
  "tts_voice": "Aoede",
  "temperature": 0.7,
  "response_length": "standard",
  "communication_style": "professional",
  "user_email": "user@example.com",
  "owner_identities": ["email@example.com"],
  "context_logging_enabled": true,
  "off_record": false,
  "context_retention_days": 0,
  "news_priorities": ["AI/Tech", "Politics", "Media"],
  "camera_auto_describe": false,
  "camera_interval_sec": 3,
  "anthropic_api_key": "sk-ant-...",
  "gemini_api_key": "AIza...",
  "setup_complete": true
}
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | *(from settings.json)* |
| `GEMINI_API_KEY` | Google Gemini API key | *(from settings.json)* |
| `FRIDAY_PASSWORD` | HTTP Basic Auth password | *(none — open access)* |
| `FRIDAY_USERNAME` | HTTP Basic Auth username | `admin` |
| `FRIDAY_SECRET_KEY` | Flask session secret | `friday-default-secret-change-me` |
| `ANTHROPIC_MODEL` | Override default model | `claude-sonnet-4-6` |

API keys set via environment variables take priority over settings.json. Keys saved through the setup wizard are stored in settings.json and loaded at first use.

---

## API Surface (Key Endpoints)

```
GET  /                      → index.html (assembled UI)
GET  /api/health            → server status, model info, ring counts
GET  /api/settings          → current settings + personality
POST /api/settings          → update settings + personality

POST /api/chat              → main chat endpoint (streaming SSE)
GET  /api/chat/history      → conversation history
DELETE /api/chat/history    → clear history

GET  /api/setup/status      → { initialized: bool }
POST /api/setup/complete    → save wizard choices, mark setup done

POST /api/voice/tts         → Gemini TTS (returns WAV)
WS   /ws/live               → Gemini Live real-time audio

GET  /api/tasks             → list background tasks
GET  /api/tasks/<id>        → task detail + log
DELETE /api/tasks/<id>      → cancel task

GET  /api/wiki/structure    → wiki directory tree
POST /api/wiki/update       → propose/apply wiki edit
POST /api/wiki/search       → full-text wiki search
GET  /api/wiki/pending      → list pending proposals
POST /api/wiki/pending/<id>/approve
POST /api/wiki/pending/<id>/reject

GET  /api/evolution         → scene day + structure info
POST /api/evolution         → pin/unpin preferred scene index

POST /api/create/image      → Gemini image generation
POST /api/create/music      → Lyria music generation
POST /api/create/code-art   → p5.js code art
POST /api/create/video      → Veo 2.0 video synthesis

GET  /api/trust             → trust graph
GET  /api/personality       → personality traits
GET  /api/epistemic         → independence scores
GET  /api/notifications     → pending notifications
GET  /api/system            → disk + process stats
```

---

## Build System

The UI is split into three files assembled by `build_ui.py`:

```
ui_parts/
  head.html              → <head> tag: meta, CDN scripts, base styles
  styles_and_scene.html  → Three.js scene + all CSS
  app.html               → React components (JSX via Babel standalone)
                           ↓
python build_ui.py       → index.html  (single ~389KB file)
```

Run `python build_ui.py` after every change to any `ui_parts/` file.

---

*See `INSTALL.md` for setup instructions and `CHANGELOG.md` for version history.*
