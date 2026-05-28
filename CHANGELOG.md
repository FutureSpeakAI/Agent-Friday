# Changelog — Agent Friday / Asimov's Mind

All notable changes to this project are documented here.  
Format: [Semantic Versioning](https://semver.org) · Date: YYYY-MM-DD

---

## [v4.3] — 2026-05-28

The self-evolving interface release. Adds Liquid UI, OFW Monitor, and the
Seeds & Gardens workspace architecture.

### Liquid UI

- **`liquid_ui.py`** — Friday's self-evolving interface engine.
  - `LiquidUIRequest` captures intent — explicit ("I wish I could…") or
    behavioral (workspace ping-pong, repeated filters, error loops,
    dwell-time collapse).
  - `FeatureSpecGenerator` produces structured specs with complexity
    tier classification: trivial (<1m, auto), simple (1–5m), medium
    (5–30m), complex (30–120m), epic (2h+).
  - `LiquidUIBuilder` writes React + backend artifacts to
    `~/.friday/liquid_ui/features/<id>/`, snapshots state, emits a
    hot-reload token. Source tree stays pristine.
  - `SuggestEngine` runs four behavioral detectors and surfaces
    proactive `Suggestion` objects with confidence scores.
  - `SnapshotManager` — HMAC-irrelevant but path-stable rollback. Every
    change snapshots touched files; Ctrl+Z eligibility = within 30s.
    60-day retention; Settings exposes the full chain.
  - Every Liquid UI feature is also a SkillOpt skill — usage events
    update accuracy / satisfaction / completeness.
- **`ui_parts/liquid_ui_panel.html`** — React management panel with
  build queue, feature cards, proactive suggestions, snapshot history,
  ✨ Wish modal.

### OFW Monitor

- **`skills/ofw_monitor/`** — daily Our Family Wizard scan via Claude in
  Chrome. Messages / custody calendar / expense submissions.
  - Local lexicon-based sentiment (`cooperative` / `neutral` /
    `passive-aggressive` / `hostile`). LLM summarization OFF by default;
    a hard `_assert_local_only()` guard fails loud if disabled.
  - Tone-shift detection over a 14-day window.
  - 72-hour response-deadline tracker.
  - HMAC-SHA256 chained archive at `~/.friday/vault/ofw-archive.jsonl`
    — every record signs the previous one, tamper-evident on the entire
    stream. `monitor.py verify` walks the chain.
  - Phone / address / minor's name redacted via the privacy_shield
    before any notification fires.
  - Pluggable browser `Session` — `LocalSession` stub for tests / CLI,
    real session injected by server.py via the Claude-in-Chrome MCP.

### Workspace architecture

- README documents the **Seeds & Gardens** model and the new stock
  workspace layout:
  - Personal: Messages (unified inbox + outbound drafts), Family, Health
  - Professional: Career, Finances, Business, News
  - Creative: Studio (was "Content"; "Draft" rolls into Messages)
  - Infrastructure: Wiki, Trust, Code, Skills Observatory
  - Dashboard home with KPI cards, today's agenda, activity feed, alerts
  - ➕ Add Garden gallery: Smart Home, Travel, Education, Legal,
    Fitness, Entertainment, Real Estate, Pets …
- Design principles: pick 4–5 workspaces at setup; reorder by frequency;
  auto-minimize after 30 days unused; every menu has ✨ Suggest +
  right-click "Improve this workspace"; complete rollback via Liquid UI
  snapshots.

---

## [v4.2] — 2026-05-28

Self-improving skills release. Adds a SkillOpt-inspired engine, two
production skills, and a holographic Observatory UI.

### Skills system

- **`skillopt_engine.py`** — Versioned skills with composite scoring,
  validation gate (5% regression tolerance), and a Karpathy-style
  AutoResearch loop that proposes patches when rolling scores drop ≥ 10%
  below the all-time best. JSONL execution log per skill; `best_skill.md`
  artifact per champion. CLI: `python skillopt_engine.py status`.
- **`skills/job_scanner/`** — Autonomous LinkedIn discovery every 4h
  during active hours. Round-robin keyword rotation, score-weighted
  notifications (title × 3, salary × 2, remote × 2, skills × 2,
  seniority × 1.5, company × 1), dedup against `JobTracker`, daily cap
  of 6 priority alerts.
- **`skills/application_engine/`** — Full-cycle: intel → resume tailor →
  cover letter → ATS form plan → submission → tracker log. Epsilon-greedy
  resume A/B bandit. Quality gates: salary floor ($150K), confirmation
  above $300K, dedup-apply, brand-voice ≥ 0.75, cover-letter word count
  bounds. Greenhouse / Lever / Workable / SmartRecruiters field maps.
- **`data/job_tracker_schema.py`** — `JobListing`, `ApplicationRecord`,
  `JobTracker` dataclasses with atomic JSON writes, pipeline status
  tracking (discovered → triaged → applied → screening → interview →
  offer → closed/rejected/withdrawn), and 30-day response-rate analytics.
- **`notifications.py`** — Friday-Chat-ready templates: priority job
  alerts (🔴), daily digests (🟡), weekly reports (📊), interview
  detection (📞), skill improvement announcements (🧠), skill regression
  notes.

### UI

- **Skills Observatory** (`ui_parts/skills_observatory.html`) — React +
  Recharts workspace. Skill cards with sparkline trends, version history
  with inline diff, execution scatter plot with reference lines, active
  experiments panel, research log, champion-vs-challenger comparison.
  Holographic dark theme (`#0a0e1a` base, cyan `#00d4ff`, blue `#3b82f6`,
  magenta `#ff0080` accents, glass cards).

### Setup & onboarding

- **Existing-user detection** — Setup wizard and `friday` CLI now skip
  re-setup when any of these are present: `.setup_complete` marker,
  API keys in config or environment, or a generated `start.bat`. Use
  `setup_wizard.py --force` to redo setup from scratch.
- **Branded onboarding banner** — New users see the FutureSpeak.AI boxed
  ASCII art banner on first run.

### Cleanup & hygiene

- Removed one-shot scripts (`merge_gemini.py`, `patch_career.py`,
  `write_scene.py`), base64 chunk fragments (`chunks/`, `combine.b64`,
  `p0.b64`, `temp_b64.txt`), legacy PowerShell decoders, and stale
  install logs.
- Untracked `.asimovs-mind/vault/bridge-token` and `port` — these are
  per-machine secrets and should never have been in git history.
- Strengthened `.gitignore`: now covers `.env*`, `.claude/`, `*.pyc`,
  `settings.json`, `credentials.json`, skill-state JSONs, all editor
  backup variants.

---

## [v4.1] — 2026-05-26

Major feature release. Built in a single focused session. Everything below was designed, implemented, and shipped today.

### Governance & Security

- **Governance gate with privilege rings** — Every tool call passes through `_evaluate_policy()` before execution. Four rings (0=read-only, 1=local-write, 2=network, 3=OS-control) with distinct permission requirements.
- **Decision BOM audit chain** — HMAC-SHA256 signed decision records appended to `~/.friday/vault/decision-bom.jsonl`. Tamper-evident; covers every allow/deny decision with timestamp, tool, ring, policy, reason, and signature.
- **Computer control with kill switch** — Ring 3 (`move_mouse`, `click`, `type_text`, `press_key`, `screenshot`, `scroll`) enabled by user toggle. Rate-limited to 20 actions/second. Blinking red indicator in top bar. Kill switch button always visible in UI for instant suspension.
- **Blocked operations list** — Hard-coded deny list for destructive shell commands regardless of ring level: `rm`, `del`, `rmdir /s`, `format`, `shutdown`, `reg delete`, `taskkill`, and others.

### Voice Mode

- **Live WebSocket audio** — `/ws/live` endpoint connects to Gemini 3.1 Flash Live Preview for real-time bidirectional audio. Mic button in UI opens the WebSocket session.
- **Chat transcript persistence** — Voice conversations are transcribed and saved to chat history alongside text conversations, with `[voice]` provenance tag.
- **Context-log persistence** — Voice turns logged to `~/.friday/vault/context-log/` like text turns.
- **Adaptive voice/text mode** — UI auto-detects when a voice session is active and switches TTS response format (1–3 sentences, no markdown) for the Claude system prompt.
- **Audio device selector** — Settings panel shows available audio input/output devices, lets user switch without restart.
- **Fixed audio extraction path** — Resolved `chunk.data` vs `part.inline_data.data` extraction bug that caused silent audio responses.
- **Fixed Gemini Live API version** — Corrected `http_options` to use `v1alpha` (was using wrong version causing 404s).

### Chat UI

- **Rich markdown rendering** — Chat responses render full GitHub-flavored markdown: headers, bold, italic, inline code, fenced code blocks with syntax highlighting, bulleted and numbered lists, tables, blockquotes.
- **Code block copy button** — Each fenced code block has a copy-to-clipboard button in the top-right corner.
- **Message pinning** — Pin any chat message; pinned messages are excluded from the 30-day retention purge.
- **Chat history search** — Search bar filters chat history by message content.
- **Source citations** — Chat responses from tool-augmented turns show a "sources" section with links.

### Model Selector

- **Model selector UI** — Top bar shows model pills (orchestrator + subagent + creative). Click any pill to change model without restarting.
- **All Claude 4.x models** — Claude Opus 4.7, Sonnet 4.6, Haiku 4.5 available.
- **Gemini models** — Gemini 2.5 Flash, 2.0 Flash, 1.5 Pro, Lyria, Veo 2.0.

### Tool Expansion (12 → 30 tools)

**New tools added:**
- `query_calendar` — Check upcoming calendar events
- `get_career_pipeline` — Read job search status from wiki
- `get_briefing` — Fetch most recent daily briefing
- `learn_skill` — Create/modify/delete/list skill YAML workflows in `~/.friday/skills/`
- `search_email` — Search Gmail via connector
- `draft_email` — Draft email via connector
- `open_url` — Launch URL in Chrome
- `install_package` — pip/npm package installer
- `move_mouse` — Ring 3: move cursor
- `click` — Ring 3: mouse click
- `type_text` — Ring 3: keyboard injection
- `press_key` — Ring 3: key/chord press
- `screenshot` — Ring 3: screen capture (base64 PNG)
- `scroll` — Ring 3: mouse wheel
- `correct_wiki` — Global find-replace across entire wiki + vault JSONs
- `propose_wiki_update` — Queue wiki edit for user approval
- `describe_screenshot` — Gemini vision describes a screenshot
- `analyze_file` — Gemini multimodal file analysis

### Quick Draft with Background Tasks

- **`spawn_task` tool** — Agent can delegate deep work to a background thread with full tool access. Task runs in a Claude agent context; results appear in Task Tray.
- **Task Tray** — Bell-icon dropdown in top bar shows all active/completed tasks with live status, elapsed time, spinner, and collapsible log lines.
- **Cancel tasks** — Stop button kills a running background task.
- **Tool trace** — Each task stores a trace of every tool call it made, visible in the task detail panel.

### Holographic Scene

- **Scene persistence** — Preferred scene index stored in `~/.friday/personality.json`. Survives server restarts.
- **`POST /api/evolution`** — Set `{ preferred_scene_index: N }` to pin a scene; `null` to return to auto-rotation.
- **Terminal flash fixes** — Eliminated flash/flicker on scene transitions by fixing animation interpolation timing.
- **13 named structures** — Genesis Lattice, Sacred Sphere, Shannon Network, Geodesic Cathedral, Lovelace Astrolabe, Von Neumann Tesseract, Dirac Probability, Mandelbrot Set, Turing Möbius, Ocean of Light, Fibonacci Nerve, Transcendence, Giga Earth (Rez).

### Setup Wizard

- **CLI setup wizard** (`setup_wizard.py`) — Interactive rich terminal UI for first-run configuration. Covers agent name, orchestrator, creative engine, API keys, voice, scene selection, and writes `start.bat`.
- **Web setup wizard** — Glassmorphism overlay shown on first visit if `~/.friday/.setup_complete` is missing. Now includes API key entry step and scene picker (was previously just name/model/voice).
- **API key hot-reload** — Keys entered in the web wizard are live-loaded into the running process without restart.
- **`/api/setup/status`** — Returns `{ initialized: bool }` based on presence of `~/.friday/.setup_complete`.
- **`/api/setup/complete`** — Accepts all wizard choices including `anthropic_api_key`, `gemini_api_key`, `preferred_scene_index`.

### Privacy Shield

- **PII auto-redaction** — SSN, credit cards, phone numbers, email addresses, street addresses scrubbed before reaching Claude.
- **Smart tagging mode** — PII tagged as `[PII:type:hash]` with in-memory rehydration table; model never sees raw values, user sees restored responses.
- **Custom watchlist** — `~/.friday/privacy_shield.json` for project codenames, client names, and other sensitive tokens.
- **User email bypass** — Addresses in `user_email` and `owner_identities` settings pass through clean.

### Smart Context Loader

- **Keyword-routed wiki loading** — Message analysis routes relevant wiki sections into context automatically:
  - Career/job/resume → `~/wiki/professional/`
  - Family/kids/custody → `~/wiki/family/` + `~/wiki/legal/`
  - Named people → trust graph lookup → person's wiki file
  - Finance/budget → `~/wiki/finance/`
  - Health/medication → `~/wiki/health/`
- **Project context files** — Drop `.friday-context.md` or `AGENTS.md` in any project directory; automatically injected when messaging from that directory (Hermes-inspired).
- **200KB context cap** — Total context trimmed to prevent token overruns.

### Other Improvements

- **Append-only context logging** — Daily JSONL files in `~/.friday/vault/context-log/`, configurable retention.
- **Off-record mode** — Toggle to suspend chat logging without disabling tool-call logging.
- **Trajectory compression** — When chat history exceeds 2MB, old turns are summarized via a Claude call.
- **Wiki proposal workflow** — All agent-initiated wiki edits queue for user approval. Bell icon shows pending count.
- **Wiki global search** — Full-text search across all `.md` and `.txt` files in `~/wiki/`.
- **Epistemic scoring** — `/api/epistemic` endpoint scores independence across calibration, sourcing, uncertainty acknowledgment, bias resistance, and correction rate.
- **Personality traits** — `/api/personality` endpoint exposes maturity, curiosity, skepticism, humor, loyalty, directness, empathy, contrarianism.
- **Vibe Code terminals** — `/api/vibe-code/` endpoints spawn Claude tasks in new CMD windows with configurable workflow presets.
- **Camera mode** — Live video PIP with frame capture and auto-describe via Gemini vision.

---

## [v4.0] — 2026-04-14

### Added
- Initial Flask server with Anthropic Claude integration
- Personal wiki read/write with `read_wiki`, `search_wiki`, `propose_wiki_update`
- Three.js holographic scene (6 initial structures)
- Chat with persistent history (30-day retention, 500-message cap)
- PII scrubbing (basic SSN + CC patterns)
- Background task runner (first implementation)
- Trust graph integration
- Career ops tracker (parses `application-log.md`)
- Gemini creative endpoints: image, music, code art, poem, video
- TTS with 5 Gemini voice personas
- Settings panel with model selection, temperature, response length
- Daily briefing generation and serving
- Finance, health, vehicle workspace endpoints (template data)
- Countdowns endpoint
- Wiki pending approval workflow (first implementation)
- Mobile responsive layout

---

*Older history is available in git log.*
