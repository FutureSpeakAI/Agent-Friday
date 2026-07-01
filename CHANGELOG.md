# Changelog — Agent Friday / Asimov's Mind

All notable changes to this project are documented here.  
Format: [Semantic Versioning](https://semver.org) · Date: YYYY-MM-DD

> **Note:** Pre-1.0 releases have been archived. Current version: **5.0.0**

---

## [5.0.0] — 2026-07-01 — "Super Agent"

The developer-tool → sovereign-consumer-product transformation. Adds a local,
closed-loop learning system, overnight memory consolidation, user modeling, an
editable personality file, a bundled zero-key local model, voice-first
onboarding, and messaging-channel bridges — every one of them local-first and
routed through the existing cLaws governance + egress gate.

### Added

- **Learning Loop Engine** (`services/learning_loop.py`). Observes task outcomes,
  mines successful (task-type, tool-strategy) patterns into text *heuristics*,
  scores them with a Wilson lower bound blended with satisfaction, and promotes
  the best into the system prompt. Local-only, SQLite-backed (`learning.db`),
  bounded by `max_active_skills`. **Skills are advisory text, never executable
  code** — no new tool surface. Weekly `learning_epoch` scheduler job. API under
  `/api/learning/*`.
- **Memory Dreaming** (`services/memory_dreaming.py`). Nightly (03:00) local
  consolidation: reviews the day's ChromaDB conversation turns, extracts topics
  and durable facts (preferences/decisions/bio), feeds high-confidence facts to
  the user model, tags noise, and writes `~/.friday/dreams/<day>.md`. Never
  touches cloud. API under `/api/memory/dream*`.
- **User Modeling** (`services/user_model.py`). Tracks communication style
  (formality/verbosity), per-domain expertise, and workflow patterns from each
  turn; injects a compact **TIER_1** `== USER MODEL ==` block into every system
  prompt. SQLite-backed with a GDPR-style `forget()`. API under `/api/user-model/*`.
- **SOUL.md personality config** (`services/soul.py`). Friday's personality now
  lives in a user-editable `~/.friday/SOUL.md` (seeded from the shipped default,
  versioned in `soul_history/`). `core._load_agent_personality()` reads it first.
  API under `/api/soul*`.
- **Bundled Gemma / no-API-key mode.** Default local model is now **`gemma3:4b`**
  (Google's open Gemma 3 4B-IT, ~8 GB RAM). `install.{sh,ps1,bat}` auto-install
  Ollama and pull the model (best-effort, skippable via `FRIDAY_SKIP_MODEL=1`).
  Chat works fully offline with zero cloud keys; creative/voice degrade
  gracefully. `friday doctor` / `friday health` now report Ollama + Gemma + a
  "no-key mode ready" status.
- **Voice-First Onboarding** (`services/onboarding.py`). First-run state machine
  — greet → name → voice test → optional keys → Ed25519 identity → SOUL.md —
  spoken via the local voice engine (no cloud key required). API under
  `/api/onboarding/*`.
- **Channel bridges** (`services/channels/`). Discord (`discord.py`, graceful
  no-op when absent) and Telegram (stdlib, zero-dep) bots. Every inbound message
  runs the shared agent loop; every reply passes the **egress gate** before send.
  Disabled by default, allowlist-gated, bot tokens in the credential store. API
  under `/api/channels/*`.

### Fixed

- **Blueprint auto-discovery registered zero routes in two shipping paths** —
  the entire API 404'd. (1) The repo-root `server.py` shim `exec()`s the package
  server, so `__file__`-relative discovery globbed a nonexistent `<repo>/routes`.
  (2) The packaged **AgentFriday.exe** never bundled `routes/*` at all: the spec's
  `collect_submodules('agent_friday')` silently returned `[]` because `src` wasn't
  on `sys.path` at spec-eval time, and the dynamically-imported route modules were
  invisible to PyInstaller's static analysis. Fixed by enumerating routes via
  `pkgutil` with an explicit `ROUTE_MODULES` fallback for the frozen build
  (drift-guarded by `tests/api/test_blueprint_discovery.py`) and adding `src` to
  the spec's path so the route modules are bundled. Verified: `python server.py`
  and the frozen `.exe` both serve 200 on every endpoint.

### Notes

- All v5 subsystems are **local-only** and pass through cLaws governance and the
  egress gate. Nothing new introduces a default cloud dependency.
- 3162 tests pass (64 new). See `docs/SUPER_AGENT_BUILD_SPEC.md` for the full
  design and `docs/RELEASE_NOTES_v5.0.md` for the release summary.

---

## [Unreleased]

### Removed

- **Removed the personal Co-Parent/OFW workspace and `ofw_monitor` skill from the
  public release.** The co-parenting platform monitor, its custody-calendar
  tracking, and the related draft mode were personal to the original author and
  are not part of the open-source distribution.

---

## [4.5.0] — 2026-06-06

The public-release hardening pass. Prunes the surface area down to the core
general-purpose workspaces, makes the powerful-but-risky subsystems opt-in, and
strips the founder's personal content out of source so a fresh user starts clean.

### Removed

- **Stub workspaces.** `FinanceWS` and `HealthWS` (vault-gated placeholders with
  no real integrations) are removed, UI + routes (`/api/finance/*`,
  `/api/health/*`). They can return later as Seeds/plugins.
- **Personal Co-Parent workspace, removed entirely.** The dedicated workspace
  component, its API routes, the platform message loader + notification monitor,
  the related calendar keywords, the message-classification lane, and its draft
  mode are all gone. (Sensitive personal data was always gitignored and never
  shipped.)
- **Redundant dock entries.** `FamilyWS`, `TrustWS` (trust is now a tab in
  News + Contacts), and `StudioWS` (functions live in Dev Studio and the Sites
  workspace) are no longer separate dock entries.
- **Content workspace pipeline.** `ContentWS` and its kanban endpoints
  (`/api/content/pipeline|idea|draft`) are removed; writing is consolidated into
  the Draft workspace (reachable via News → Share to Draft) and the chat pipeline.
  The draft library serving routes (`/api/content/drafts*`) stay.
- **FutureSpeak business pipeline.** The personal-CRM endpoints
  (`/api/futurespeak/{pipeline,revenue,legal,assets}`) and their UI tabs are
  removed. The workspace remains as a general-purpose **Sites** portfolio/deploy
  manager (projects + scan + scaffold).

### Changed

- **Dock pruned to 10 core icons:** Home, News, Messages, Calendar, Career, Code,
  Wiki, Contacts, Sites, System. (Settings remains the gear-button slide-out.)
- **Computer Control is now opt-in.** New setting `computer_control_enabled`
  defaults to **false**. The feature is surfaced under Settings as **Experimental**
  with a clear warning; the Ring-3 runtime grant and the kill switch are unchanged,
  and the grant endpoint now refuses unless the feature is enabled.
- **SkillOpt nightly job disabled.** The 3:30 AM auto-research job is commented
  out for general release (marginal value while the skill library is small); the
  infrastructure stays for when there are 50+ skills.
- **Voice debug logging gated.** Per-chunk voice logs are off by default — client
  logs behind `window.FRIDAY_VOICE_DEBUG`, server `_vlog` behind the
  `FRIDAY_VOICE_DEBUG` env var.
- **De-personalized for new users.** Hardcoded author-specific content (name,
  family, bio, local news feeds, personal keyword lanes, and seeded personal
  portfolio sites) has been replaced with generic, settings-driven defaults across
  the news editor, draft, and message subsystems.

### Security

- **Vault encryption-at-rest, wired into the running app.** The `vault_crypto.py`
  primitives (AES-256-GCM + Argon2id, already present and tested) are now actually
  used by `server.py`. A vault key is derived once from `FRIDAY_PASSWORD` at startup
  (`_get_vault_key`); sensitive files (finance, health, and
  `vault/{legal,finances,family}`) are transparently encrypted on write
  and decrypted on read (`_vault_write_text` / `_vault_read_text`); and any existing
  plaintext is encrypted in place on first boot (`_migrate_vault_plaintext`, verifies
  a decrypt round-trip before replacing each file). With no `FRIDAY_PASSWORD` set the
  vault stays plaintext (logged at startup) — behaviour is unchanged for keyless
  local-dev. New tests: `tests/test_vault_at_rest.py`. This closes the gap documented
  in `docs/SITE_VS_REPO_DISCREPANCIES.md` (vault was previously plaintext at rest).

---

## [4.4.0] — 2026-06-06

The trust-and-portability release. Hardens authentication, adds a third
(OpenAI-compatible) provider with a full agentic tool loop, gates every tool
call behind a sandbox policy, ships a portable SKILL.md registry, and closes the
loop on skill learning so real chat usage feeds the optimizer.

### Added

- **OpenAI-compatible provider** — A third cloud provider alongside Anthropic and
  Ollama. Opt-in via `model_routing.cloud_provider = "openai"` plus
  `openai_base_url` (defaults to OpenRouter), `openai_model`, and `openai_api_key`
  (or env `OPENAI_API_KEY` / `OPENROUTER_API_KEY`). Unlocks OpenRouter's hundreds
  of models and any `/v1` endpoint. Ships a **full agentic tool loop** at parity
  with the Anthropic path. Vault / sensitive requests still never route here —
  they stay local or on Anthropic.
- **Portable skill registry** (`skill_registry.py`) — A portable "SKILL.md
  folder" format: YAML frontmatter plus a markdown body, agentskills.io-compatible.
  Import/export across folder, zip, legacy-YAML, and OpenClaw formats. New HTTP
  routes `/api/skills`, `/api/skills/import`, `/api/skills/<name>/export`, and
  `/api/skillopt/state`. Matched skills are injected into the system prompt each
  turn, so newly learned skills take effect without a restart.
- **Closed-loop learning** (`skill_capture.py`) — Captures turn trajectories to
  CognitiveMemory and JSONL, feeds real chat usage into the SkillOpt optimizer,
  and runs a nightly `skillopt-nightly` auto-research job. Connects the
  previously-dormant SkillOpt machinery to live usage.

### Security

- **Auth hardening** — The session secret is now a persisted random value
  (`~/.friday/secret_key`, mode `0600`) instead of a hardcoded default. Credential
  checks are constant-time (`hmac.compare_digest`). A per-IP login throttle caps
  attempts at 8 per 5 minutes. New env toggles: `FRIDAY_TRUST_LOOPBACK` (default
  on; set `0` to require login even on localhost), `FRIDAY_WS_TOKEN` (optional
  token gating the `/ws/live` voice WebSocket), and `FRIDAY_COOKIE_SECURE` (Secure
  cookie for HTTPS / tunnel). Session cookies are now `SameSite=Lax` and
  `HttpOnly`.
- **Tool-execution sandbox** — Every agent tool call passes through a policy gate
  controlled by `FRIDAY_SANDBOX_MODE` (`off` / `confine` [default] / `strict`)
  and `FRIDAY_SANDBOX_ROOT`. `confine` keeps `write_file` inside a root (default
  `HOME`) and runs `run_command` against a destructive-command blocklist;
  `strict` additionally allowlists commands.

### Fixed

- **Command injection in the vibe-code launcher** — Closed a command-injection
  hole in the vibe-code terminal launcher.

---

## [v4.3] — 2026-05-28

The self-evolving interface release. Adds Liquid UI and the Seeds & Gardens
workspace architecture.

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
