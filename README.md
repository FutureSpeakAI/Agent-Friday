# Agent Friday — Asimov's Mind

> **Sovereign AI infrastructure for your personal desktop.**  
> Built by [FutureSpeak.AI](https://futurespeak.ai) · By Stephen C. Webster

---

```
 █████╗  ██████╗ ███████╗███╗   ██╗████████╗    ███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗
██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝    ██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝
███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║       █████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝
██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║       ██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝
██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║       ██║     ██║  ██║██║██████╔╝██║  ██║   ██║
╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝       ╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝
                          A S I M O V ' S   M I N D
```

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-cyan?style=flat-square)](https://www.python.org)
[![Flask](https://img.shields.io/badge/flask-3.x-magenta?style=flat-square)](https://flask.palletsprojects.com)
[![Claude](https://img.shields.io/badge/claude-opus%204.7-blue?style=flat-square)](https://anthropic.com)
[![Gemini](https://img.shields.io/badge/gemini-2.5%20flash-orange?style=flat-square)](https://deepmind.google)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

---

## What is Agent Friday?

Agent Friday is a **sovereign AI desktop** — a personal intelligence layer that runs on your own machine, speaks your language, knows your life, and acts on your behalf without sending your data to a cloud service. It combines the reasoning depth of Claude with the creative breadth of Gemini, wrapped in a holographic browser interface inspired by the aesthetics of vaporwave and cyberpunk cinema.

The system is governed by **Asimov's cLaws** — a four-principle policy engine that signs every decision with HMAC-SHA256, enforces privilege rings on all tools, and keeps a tamper-evident audit trail of everything the agent does. Your data stays on your machine. Your agent works for you.

---

## Features

### Intelligence Layer
- **Claude Opus 4.7** as primary orchestrator — deep reasoning, complex multi-step tool use
- **Claude Sonnet 4.6** for subagent background tasks
- **Gemini 2.5 Flash** for creative generation, vision analysis, and live voice
- Hot-swappable model selector in the UI — switch without restarting
- Persistent 500-message chat history with pinning and 30-day retention
- Trajectory compression: summarizes old context automatically when history grows large

### 30-Tool Agent (4 Privilege Rings)
| Ring | Access | Tools |
|------|--------|-------|
| **0** — Read-only | Always | `read_file`, `read_wiki`, `search_wiki`, `query_trust_graph`, `query_calendar`, `get_career_pipeline`, `get_briefing` |
| **1** — Local write | Always | `write_file`, `write_clipboard`, `propose_wiki_update`, `correct_wiki`, `learn_skill` |
| **2** — Network | Authenticated | `search_web`, `browse_web`, `search_email`, `draft_email`, `open_url`, `spawn_task`, `run_command`, `install_package` |
| **3** — OS Control | CC enabled | `move_mouse`, `click`, `type_text`, `press_key`, `screenshot`, `scroll` |

### Governance — Asimov's cLaws
- Every tool call is policy-evaluated before execution
- HMAC-SHA256 signed decision records appended to `~/.friday/vault/decision-bom.jsonl`
- Kill switch suspends Ring 3 computer control instantly
- Rate limiter: max 20 OS actions/second
- Blocked operations: `rm`, `del`, `format`, `shutdown`, `reg delete`, and other destructive commands

### Privacy Shield
- SSN, credit card, phone, email, and street address auto-redacted before reaching the model
- Custom watchlist tokens in `~/.friday/privacy_shield.json`
- Smart tagging mode: re-injects PII back into responses (not destructive)
- User's own email addresses are whitelisted and pass through clean

### Context System
- **Smart context loading**: keyword-routing loads the right wiki sections automatically
  - Career keywords → `~/wiki/professional/`
  - Family keywords → `~/wiki/family/` + `~/wiki/legal/`
  - Named people → trust graph lookup
- **Project context files**: drop `.friday-context.md` or `AGENTS.md` in any project directory
- **Vault injection**: personality, memory, trust graph, todos, epistemic state
- 200KB soft cap per turn — never burns tokens on irrelevant context

### Voice Mode
- Real-time WebSocket audio via **Gemini 3.1 Flash Live**
- 5 TTS voice personas: Aoede, Puck, Charon, Kore, Leda
- Chat transcripts and context-log persistence across voice sessions
- Auto-detects voice vs. text mode — switches seamlessly
- Audio device selector in Settings

### Creative Generation
- **Images** — Gemini 2.0 Flash image generation
- **Music** — Google Lyria music synthesis
- **Code Art** — p5.js HTML art via Gemini
- **Poetry** — text/poem generation
- **Video** — Veo 2.0 video synthesis (async, polls for completion)
- All saved to `~/Desktop/friday-creations/`

### Background Tasks
- `spawn_task` delegates long work to a background thread agent
- Task Tray in the UI shows live status, elapsed time, log lines
- Each task gets its own Claude context with full tool access
- Results persist in memory across sessions

### Wiki & Knowledge Base
- Personal wiki at `~/wiki/` (Markdown, syncs to Google Drive if mounted)
- Full-text search across all wiki files
- Proposal workflow: agent proposes edits, you approve/reject
- Global find-replace (`correct_wiki`) — fix wrong facts across the entire wiki instantly
- Career ops tracker, finance/health/vehicle workspaces

### Self-Improvement — SkillOpt-Inspired
- **`skillopt_engine.py`** — Karpathy-style skill optimization engine
  - Skills are versioned (`v001`, `v002`, ...) with full content history
  - Every execution logged JSONL to `~/.friday/skillopt/<skill>/metrics.jsonl`
  - Composite scoring across accuracy, latency, cost, user_satisfaction, completeness
  - **Validation Gate** blocks any new version within 5% regression of the all-time best
  - **AutoResearch Loop** — when rolling-10 score drops 10% below best, the engine
    hypothesizes root causes (errors, latency, drift) and proposes patch edits
    to the SKILL.md content; candidates run through the gate before promotion
  - `best_skill.md` artifact tracks the current champion per skill
- **Built-in skills** in `skills/`:
  - `job_scanner` — autonomous LinkedIn discovery every 4h, keyword-rotating,
     score-weighted notifications (priority threshold 0.80)
  - `application_engine` — full-cycle tailoring + cover letter + ATS form plan +
     submission, with epsilon-greedy resume A/B bandit, salary-floor gate,
     brand-voice check, dedup-apply enforcement
- **Skills Observatory** (`/skills/observatory`) — React + Recharts workspace:
  sparkline trends, version-diff viewer, score scatter, active experiments,
  research log, best-vs-challenger comparison
- `learn_skill` still creates lightweight YAML skill definitions in
  `~/.friday/skills/` for one-off patterns

### Holographic UI
- **Three.js** scene with 13 named evolution structures
- Auto-rotates every 4 days, or pin your favorite
- FutureSpeak.AI cyan/magenta color scheme
- Glassmorphism floating windows, draggable and resizable
- Responsive: full desktop HUD or mobile-friendly slide-up panels
- Fonts: Orbitron, Inter, JetBrains Mono

---

## Quick Start

### One-Line Install

**Linux / macOS / WSL2:**
```bash
curl -fsSL https://raw.githubusercontent.com/FutureSpeakAI/asimovs-mind/main/scripts/install.sh | bash
```

**Windows PowerShell:**
```powershell
iex (irm https://raw.githubusercontent.com/FutureSpeakAI/asimovs-mind/main/scripts/install.ps1)
```

The installer clones the repo, creates a venv, installs deps, registers the `friday` command, and runs setup.

---

### Manual Install

```bash
git clone https://github.com/FutureSpeakAI/friday-desktop.git
cd friday-desktop
pip install flask anthropic google-genai rich colorama pyautogui beautifulsoup4 requests pyyaml
friday setup
```

---

### CLI Commands

```
friday                    Start Agent Friday (server + browser)
friday setup              Full interactive setup wizard
friday setup --quick      Minimal setup — just name + API keys
friday model              Change your LLM model
friday tools              Browse and configure tool rings
friday config set K V     Set a config value
friday config get [K]     Show config
friday status             Health check (alias: friday doctor)
friday update             Pull latest + rebuild
friday skills             Browse and manage skills
```

### Prerequisites
- Python 3.10 or later
- [Anthropic API key](https://console.anthropic.com) (required for chat)
- [Google Gemini API key](https://aistudio.google.com/app/apikey) (optional — voice + creative)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Asimov's Mind                                 │
│           (Governance, Vault, Privacy, Identity)                 │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    Agent Friday                                  │
│     (Flask server · Claude orchestrator · 30-tool agent)        │
└──────┬──────────────────────┬────────────────────┬───────────────┘
       │                      │                    │
┌──────▼──────────┐  ┌────────▼────────────┐  ┌───▼──────────────┐
│ Friday Desktop  │  │ Gemini Creative      │  │ SkillOpt Engine   │
│ (Three.js UI ·  │  │ Engine               │  │ + Skills Fleet    │
│  React panels · │  │ (Voice · Images ·    │  │ (job_scanner,     │
│  Holographic    │  │  Music · Video)      │  │  application_eng) │
│  scene +        │  └──────────────────────┘  │ ↻ self-improving  │
│  Skills         │                            └───────────────────┘
│  Observatory)   │
└─────────────────┘
```

**Stack:**
- Backend: Python / Flask (single-file `server.py`, ~6600 lines)
- Frontend: Vanilla React via CDN + Three.js (assembled from `ui_parts/` by `build_ui.py`)
- AI: Anthropic Claude (reasoning) + Google Gemini (creative/voice)
- Storage: `~/.friday/` directory — JSON files, no database required
- Auth: Optional HTTP Basic Auth via `FRIDAY_PASSWORD` env var

---

## Model Support

| Model | Role | Provider |
|-------|------|----------|
| Claude Opus 4.7 | Primary orchestrator | Anthropic |
| Claude Sonnet 4.6 | Subagent / background tasks | Anthropic |
| Claude Haiku 4.5 | Fast responses | Anthropic |
| Gemini 2.5 Flash | Creative engine, vision | Google |
| Gemini 3.1 Flash Live | Real-time voice | Google |
| Gemini 2.0 Flash | Images, code art | Google |
| Veo 2.0 | Video generation | Google |
| Lyria | Music generation | Google |

All models selectable at runtime — no restart required.

---

## Skills System

Agent Friday's skills system is inspired by **SkillOpt** — each skill is a
small, self-contained capability that improves over time through measured
execution.

### Storage layout

```
~/.friday/skillopt/<skill_name>/
    versions/v001.md, v002.md, ...   versioned SKILL.md content
    versions/v001.json, ...          per-version rolled-up metrics
    metrics.jsonl                    append-only execution log
    best_skill.md                    current champion artifact
    config.json                      weights + thresholds
    research_log.jsonl               autoresearch findings + outcomes
```

### Composite scoring

Each execution produces a composite score in [0, 1], weighted across:

| Component         | Default weight | Notes                                  |
|-------------------|----------------|----------------------------------------|
| `accuracy`        | 0.40           | task-specific correctness              |
| `user_satisfaction` | 0.25         | explicit chat feedback                 |
| `latency`         | 0.15           | normalized vs per-skill target         |
| `completeness`    | 0.10           | required-output coverage               |
| `cost`            | 0.10           | dollars-per-run (LLM tokens)           |

Weights are configurable per skill in `~/.friday/skillopt/<skill>/config.json`.

### Validation gate

A candidate version is promoted only if it:

1. Scores within **5%** of the all-time best, **and**
2. Beats the immediate baseline by a non-noise margin (≥ 0.5%).

### AutoResearch loop

When the 10-execution rolling mean drops **10% below best**, the engine:

1. Pulls recent executions + the current champion content,
2. Hypothesizes the root cause (errors, latency, drift, prompt issues),
3. Proposes patch edits — `append`, `replace`, or `patch`,
4. Hands candidates to a training epoch — the gate decides.

Hook in an LLM-backed researcher by passing one to `SkillOptEngine(researcher=…)`.
Without one, the engine falls back to a heuristic researcher that detects
error spikes and latency regressions.

### Built-in skills

- **`skills/job_scanner/`** — autonomous job discovery (LinkedIn, every 4h
  during active hours, keyword-rotating, score-weighted). Priority threshold
  0.80, daily cap of 6 priority alerts.
- **`skills/application_engine/`** — full-cycle application: intel → resume
  tailor → cover letter → ATS form plan → submission → tracker log. Resume
  variants picked via epsilon-greedy bandit. Quality gates: salary floor
  ($150K), confirmation above $300K, dedup-apply, brand-voice ≥ 0.75.
- **`skills/ofw_monitor/`** — daily Our Family Wizard scan via Claude in
  Chrome. Messages, custody calendar, expense submissions; **local-only**
  lexicon-based sentiment (no LLM by default); response-deadline tracker;
  HMAC-SHA256 chained archive in the Sovereign Vault. Notification
  priorities: new_message (high), calendar_change (high),
  expense_submitted (medium), response_overdue (critical), tone_shift (low).

### Skills Observatory

Live web workspace at **`/skills/observatory`** (also embeddable in the
holographic desktop as a floating window). Shows:

- Sparkline score trends across the fleet
- Version history with one-click promotion / demotion
- Inline diff between any two versions
- Execution scatter plot with 0.8 / 0.5 reference lines
- Active experiments / A/B challengers
- Research log — every autoresearch finding with hypotheses + applied edits
- Champion vs Challenger comparison

CLI inspection:

```bash
python skillopt_engine.py status        # fleet snapshot
python skillopt_engine.py show <skill>  # detail
python skillopt_engine.py versions <skill>
python skillopt_engine.py export        # JSON snapshot for the Observatory
```

---

## Liquid UI — The Self-Evolving Interface

Friday's UI literally reshapes itself around each user. Anything you wish
the desktop did, you can ask for — and the interface grows that capability
in place.

### How it works

1. **Intent capture.** `LiquidUIRequest` records the signal — either an
   explicit "I wish I could…" sent from chat / the ✨ Suggest button, or a
   behavioral observation from the `SuggestEngine` (repeated context
   switching, dead clicks, error loops, dwell-time collapse).
2. **Spec generation.** `FeatureSpecGenerator` turns intent into a
   structured `FeatureSpec`: title, description, complexity tier, data
   model, React components, backend routes, integrations, success
   metrics, open questions.
3. **Tier classification.** Each spec lands in one of five buckets, with
   different review behavior:

   | Tier      | Time budget | Review              |
   |-----------|-------------|---------------------|
   | trivial   | < 1 min     | auto-approved, hot-reloaded |
   | simple    | 1–5 min     | quick-confirm modal |
   | medium    | 5–30 min    | spec review with edits |
   | complex   | 30–120 min  | detailed review, may spawn a background task |
   | epic      | 2+ hours    | full spec + delivery roadmap |

4. **Build.** `LiquidUIBuilder` generates React + backend artifacts into
   `~/.friday/liquid_ui/features/<id>/`, takes a snapshot, emits a hot-reload
   token. Source tree stays clean.
5. **Track.** Every feature is also a SkillOpt skill — usage events feed
   accuracy / satisfaction / completeness back into the same versioning
   and evolution loop that powers the skills fleet.

### Rollback

Every Liquid UI change creates a snapshot. **Ctrl+Z within 30 seconds**
reverts in one click; beyond that, Settings → Liquid UI History shows the
full chain with one-click revert and per-snapshot file inspection.
Snapshots are retained for 60 days.

### Surfaces

- ✨ **Suggest button** on every workspace — "What would make this better?"
  with context-aware proposed specs.
- **Right-click → "Improve this workspace"** anywhere.
- **Liquid UI Panel** (`/liquid` route, also embeddable as a floating
  window) — active feature requests, build queue, usage metrics,
  snapshot history. See `ui_parts/liquid_ui_panel.html`.

### CLI

```bash
python liquid_ui.py wish "I wish my dashboard showed today's OFW priority"
python liquid_ui.py status              # fleet status JSON
python liquid_ui.py list --status live
python liquid_ui.py revert <snapshot_id>
python liquid_ui.py scan                # run SuggestEngine pass
```

---

## Workspace Architecture — Seeds & Gardens

Friday isn't a fixed set of tabs. It's a **garden** — workspaces are seeds
you plant when you want them, and they rearrange themselves around how
you actually work.

### Stock workspaces (the starter seeds)

**Personal**
- **Messages** — unified inbox across Gmail, SMS, Signal, WhatsApp, social DMs, and outbound drafts (the old "Draft" feature folds in here)
- **Family** — household calendar, kid logistics, OFW summary card
- **Health** — vitals, providers, prescriptions, fitness tracking

**Professional**
- **Career** — job tracker, application pipeline, interview prep
- **Finances** — accounts, budgets, statements, tax docs
- **Business** — FutureSpeak.AI ops, deals, investor pipeline
- **News** — daily briefing, watched topics, source management

**Creative**
- **Studio** — content library + generation tools (replaces "Content"; the
  old Draft surface for outbound messaging now lives in Messages)

**Infrastructure**
- **Wiki** — personal knowledge base, full-text search, proposal workflow
- **Trust** — trust graph, people, relationships
- **Code** — repos, CI, local environments
- **Skills Observatory** — the SkillOpt fleet view

### Dashboard home

The default landing surface. Shows:
- **KPI cards** — your top metrics, rearranged by frequency of use
- **Today's agenda** — calendar, priorities, due-soon items
- **Recent activity feed** — fresh signals across all gardens
- **Alerts** — priority notifications (🔴 priority jobs, ⏰ OFW overdue,
  📅 calendar changes, etc.)

### Navigation hierarchy

```
Dashboard ▸ Personal ▸ Professional ▸ Creative ▸ Infrastructure ▸ ➕ Add Garden ▸ Settings
```

The order isn't fixed. Workspaces reorder based on how often you actually
visit them — the ones you use most float left.

### ➕ Add Garden

Opens a gallery of additional workspace seeds you can plant with one
click. Current catalog includes:

- Smart Home (Home Assistant, presence, automations)
- Travel (trips, bookings, itineraries)
- Education (courses, study, certifications)
- Legal (case files, contracts, court calendar)
- Fitness (training plans, recovery, performance)
- Entertainment (watch / read / play queue)
- Real Estate (properties, comps, mortgages)
- Pets (vet, food, schedules)
- (and more — each seed is just a starter spec the Liquid UI engine grows)

### Design principles

1. **Don't overwhelm.** The setup wizard asks you to pick **4–5 workspaces
   to start**. Everything else is a seed in the gallery.
2. **Reorder by usage.** The garden remembers what you actually do.
3. **Auto-minimize the unused.** Workspaces untouched for 30 days collapse
   into a "compost" tray; one click to restore.
4. **Every menu has ✨ Suggest** + a right-click "Improve this workspace"
   that opens the Liquid UI request flow with context pre-filled.
5. **Complete rollback.** Every Liquid UI change creates a snapshot.
   Ctrl+Z within 30 seconds; full history in Settings.

### Why this matters

Most apps add features by accretion — you get more buttons every release,
relevant or not. Friday goes the other way: you start with a small,
familiar surface, and the system grows what's useful to *you* while
hiding what isn't. The result is a desktop that, over time, looks less
like everyone else's and more like yours.

---

## Roadmap

| Version | Status | Summary |
|---------|--------|---------|
| **v4.1** | ✅ Current | 30-tool agent, governance gate, voice mode, holographic UI, privacy shield |
| **v4.2** | ✅ | SkillOpt-inspired skills system, Skills Observatory, job pipeline (scanner + application_engine) |
| **v4.3** | ✅ Current | Liquid UI (self-evolving interface), OFW Monitor skill, Seeds & Gardens workspace architecture, rollback system |
| **v5.0** | 🚧 Next | Standalone Electron app (`asimov init` CLI, auto-updater, tray icon) |
| **v6.0** | 📋 Planned | Federation — multiple Friday instances communicate as a mesh |
| **v7.0** | 📋 Planned | Native apps for iOS/Android, OS-level integration |

---

## Contributing

Agent Friday is open source under the MIT license. Contributions are welcome.

- Fork the repo and create a branch from `main`
- Run `python build_ui.py` after any changes to `ui_parts/`
- Test with `python server.py` before submitting a PR
- See `ARCHITECTURE.md` for system design and `INSTALL.md` for full setup instructions

---

## Attribution

**Author:** [Stephen C. Webster](https://stephencwebster.com)  
**Organization:** [FutureSpeak.AI](https://futurespeak.ai)  
**Agent:** Agent Friday — Asimov's Mind v4.1  

> *"A robot may not injure a human being, or, through inaction, allow a human being to come to harm."*  
> — Isaac Asimov, Three Laws of Robotics (1942)

---

*Built with Claude Opus 4.7 and Gemini 2.5 Flash. Runs on your machine. Works for you.*
