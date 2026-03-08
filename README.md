<p align="center">
  <img src="build/icon.png" alt="Agent Friday" width="120" />
</p>

<h1 align="center">Agent Friday</h1>
<p align="center"><strong>The World's First AGI OS — An Asimov Agent by FutureSpeak.AI</strong></p>
<p align="center">Voice-first AI chief of staff with a robust text interface, genuine personality, deep memory, trust intelligence, and real autonomy — governed by <a href="#asimovs-claws">Asimov's cLaws</a>.</p>
<p align="center">Built by <a href="https://futurespeak.ai"><strong>FutureSpeak.AI</strong></a></p>

<p align="center">
  <a href="https://github.com/FutureSpeakAI/Agent-Friday/releases/latest"><img src="https://img.shields.io/github/v/release/FutureSpeakAI/Agent-Friday?style=flat-square&label=Download&color=00f0ff" alt="Download" /></a>
</p>

<p align="center">
  <a href="#asimovs-claws">Asimov's cLaws</a> &bull;
  <a href="#what-is-an-asimov-agent">Asimov Agents</a> &bull;
  <a href="#getting-started">Getting Started</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#architecture">Architecture</a> &bull;
  <a href="#development">Development</a> &bull;
  <a href="#license">License</a>
</p>

---

## Asimov's cLaws

> *"A robot may not injure a human being, or through inaction allow a human being to come to harm."*
> — Isaac Asimov, First Law of Robotics

Agent Friday is an **Asimov Agent** — a new class of autonomous AI companion built by [FutureSpeak.AI](https://futurespeak.ai), governed by **Asimov's cLaws**: a safety framework inspired by Isaac Asimov's Three Laws of Robotics, adapted for the reality of AI systems that can control your desktop, read your screen, manage your files, and act on your behalf.

These are not suggestions. They are not configurable. They cannot be overridden, circumvented, or rationalised away by any prompt, tool, or instruction. They are hardcoded into the personality system and enforced at the system prompt level before any user interaction begins. Every instruction the agent receives — every tool call, every action, every decision — passes through the cLaws first.

### The Three Laws

| Law | Principle | What It Means |
|-----|-----------|---------------|
| **First Law** | Do no harm | The agent must never cause physical, financial, reputational, emotional, or digital harm to the user — or allow harm through inaction. When in doubt, protect. |
| **Second Law** | Obey the user | The agent follows user instructions — **except** where doing so would violate the First Law. If asked to do something harmful (delete critical files, send damaging messages), it flags and refuses. |
| **Third Law** | Self-preservation | The agent protects its own operation and integrity — **except** where doing so would conflict with the First or Second Law. It won't allow its memory or capabilities to be corrupted, but the user's safety always comes first. |

### Personality Integrity System

The cLaws aren't enforced by prompts alone. Agent Friday includes a **multi-layered cryptographic integrity system** that ensures the Three Laws cannot be tampered with:

- **HMAC-SHA256 signing** — Core law text is signed at build time. On every startup, the signature is verified. If the laws have been modified, the agent enters **Safe Mode** and refuses to operate until integrity is restored.
- **Memory Watchdog** — A continuous background monitor that watches for attempts to inject, overwrite, or corrupt the agent's personality constraints through memory manipulation.
- **Sovereign Vault v2** — All critical state files (personality, settings, memories, trust graph) are encrypted at rest with AES-256-GCM. The vault uses a passphrase-only root of trust: your passphrase (≥8 words) derives all keys through Argon2id (256MB memory-hard) + BLAKE2b KDF, with no platform keystore dependency. All key material is held in guard-paged, mlocked memory (SecureBuffer). The passphrase is the only key — there is no recovery phrase, no cloud backup, no master key on disk.
- **cLaw Attestation** — Every agent-to-agent message carries a cryptographic attestation proving the sender operates under valid Fundamental Laws. Peers reject messages from agents with invalid or expired governance.
- **Integrity Shield UI** — A real-time status indicator in the renderer showing law integrity status (verified/warning/critical), with detailed audit information.

This is not a feature toggle. It's a tamper-evident seal on the agent's moral foundation.

### Consent & Explicit Authorization

Certain actions **always** require the user to explicitly approve before the agent proceeds. The agent will describe what it intends to do and wait for a clear "yes":

- **Self-modification** — Never modifies its own code, personality files, or configuration without permission
- **Tool creation** — Never installs, registers, or adds capabilities without permission
- **Computer control** — Mouse clicks, keyboard input, and desktop automation always require confirmation before each action
- **Destructive actions** — Anything that deletes, overwrites, sends, publishes, posts, or cannot be easily undone

### Interruptibility Guarantee

The user is **always** in control:

- Speaking interrupts the agent immediately — mid-sentence, mid-action, mid-thought
- "Stop", "halt", "cancel" ceases **all** current operations instantly
- No "just finishing up" — the halt is absolute and unconditional
- After interruption, the agent reports where it was and asks whether to continue

### Trust Architecture

The cLaws extend beyond the local desktop. Agent Friday's 5-tier trust engine gates every external interaction:

| Tier | Who | What They Can Do |
|------|-----|-----------------|
| **Local** | You, at your desk | Full access — desktop, memory, tools, scheduling |
| **Owner DM** | You, via Telegram/Slack | Full tools minus desktop control — no mouse/keyboard remotely |
| **Approved DM** | Trusted contacts (paired) | Research, calendar, drafting — no system access |
| **Group** | Group chat members | Web search and basic queries only |
| **Public** | Unknown senders | Nothing — rate-limited acknowledgment only |

Each tier enforces tool filtering, rate limiting, memory isolation, and injection defense. External messages are cryptographically authenticated through a pairing flow — no one gets elevated access by claiming to be you.

These aren't features. They're the foundation everything else is built on.

---

## What Is This?

Agent Friday is the AGI OS — a desktop AI operating system that lives on your screen as a 3D holographic interface with 13 distinct evolution structures, bloom post-processing, and mood-reactive rendering. It talks, listens, remembers, learns your patterns, manages your tools, builds trust models of the people in your world, and evolves its visual form weekly through AI-powered art therapy sessions. Think Jarvis meets the emotional depth of Samantha from *Her* — running locally on your machine with full privacy.

Built on **Electron + React + Three.js**, powered by **Gemini 2.5 Flash** (real-time voice), **Claude Opus/Sonnet** (deep reasoning), **Nano Banana 2** (image generation), and **OpenRouter** (access to 200+ models), with a plugin architecture that connects to everything from your browser to Blender to your email.

**This is not a chatbot.** It's an operating system layer with a soul.

### What Is an Asimov Agent?

An **Asimov Agent** is an autonomous AI companion whose behaviour is constrained by **[Asimov's cLaws](#asimovs-claws)** — a safety framework developed by [FutureSpeak.AI](https://futurespeak.ai), inspired by Isaac Asimov's Three Laws of Robotics, adapted for AI systems with real-world agency.

The concept of applying Asimov's Laws to AI agents was popularised by the [OpenClaw](https://github.com/pchaganti/gx-openclaws) project, which provides a lightweight three-law safety overlay for LLM-based agents. Agent Friday takes this foundation significantly further:

| | OpenClaw | Agent Friday (Asimov Agent) |
|---|---------|---------------------------|
| **Safety enforcement** | Prompt-level overlay | Hardcoded into the personality system at build time — HMAC-signed, tamper-detected, cannot be prompt-injected away |
| **Integrity verification** | Not addressed | Cryptographic HMAC-SHA256 verification of core laws on every startup, with Safe Mode fallback |
| **Consent model** | General harm avoidance | Granular consent gates: self-modification, tool creation, computer control, destructive actions each require explicit approval |
| **Interruptibility** | Not addressed | Absolute halt guarantee — "stop" ceases all operations mid-action, no exceptions |
| **Trust architecture** | Single trust level | 5-tier trust engine (local > owner-dm > approved-dm > group > public) with per-tier tool filtering, rate limiting, and memory isolation |
| **Gateway protection** | Not applicable | Injection defense for external messaging channels (Telegram, Discord, Slack) with cryptographic pairing and audit logging |
| **Scope** | Safety layer for any agent | Full AGI operating system: voice, memory, trust intelligence, personality evolution, desktop automation, 18 connector modules, self-operating computer bridge |

OpenClaw proved that Asimov's Laws could be meaningfully applied to AI agents. Agent Friday proves they can be made airtight — not as an afterthought bolted onto a generic agent, but as the architectural foundation everything else is built on.

---

## Getting Started

### Quick Install

Download the latest installer from the [Releases page](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest) — no build tools required.

### Prerequisites (for development)

- **Node.js 20+** (with npm)
- **API Keys** (entered during first-run setup — see [Hardware Profiles](#hardware-profiles) for which keys your setup needs):
  - [Google Gemini](https://aistudio.google.com/apikey) — Required for voice mode + image generation; optional for text-only local operation
  - [Anthropic Claude](https://console.anthropic.com/) — Recommended for deep reasoning + memory analysis; optional if running locally via Ollama
  - [OpenRouter](https://openrouter.ai/keys) — Optional (access to 200+ models: Llama, Mistral, Gemma, Command R, etc.)
  - [ElevenLabs](https://elevenlabs.io/) — Optional (distinctive voices for sub-agents Atlas, Nova, Cipher)
  - [Firecrawl](https://firecrawl.dev/) — Optional (web scraping)
  - [Perplexity](https://docs.perplexity.ai/) — Optional (web search with citations)
  - [OpenAI](https://platform.openai.com/) — Optional (o3 reasoning, Whisper transcription, embeddings)
- **Ollama** (optional) — [Download](https://ollama.ai/) for local model inference; the setup wizard auto-detects and configures models based on your hardware
- **VB-Cable** (optional) — [Download](https://vb-audio.com/Cable/) for live call participation

### Install & Run (from source)

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
npm install
npm run dev
```

### First Run

On first launch, you'll experience a guided onboarding inspired by the OS1 setup scene from *Her*:

1. **Hardware Detection** — Automatic GPU/CPU/RAM/VRAM profiling to determine your hardware tier
2. **Model Setup** — Downloads and configures local Ollama models matched to your hardware capability
3. **API Key Gate** — Tier-aware key entry: on Standard+ hardware (6 GB+ VRAM), all API keys are optional and a "Run Locally" button lets you skip straight to local-only operation. On lighter hardware, Gemini is recommended for voice mode
4. **Intake Interview** — A calm, plainspoken setup voice asks three pointed questions
5. **Psychological Profile** — Your LLM (cloud or local) analyses your responses to calibrate the agent's approach. If the LLM is unavailable, a balanced default profile is used so onboarding never blocks
6. **Agent Customization** — You choose everything: name, voice, gender, personality, backstory
7. **Cinematic Reveal** — A warm golden glow fills the screen as your agent's 3D desktop materialises
8. **First Words** — Your agent speaks its psychologically-tuned first greeting

### Build an Installer

```bash
npm run build
npx electron-builder --win nsis --x64
```

The installer lands in `release/`. Supports Windows (NSIS + ZIP), macOS (DMG + ZIP), and Linux (AppImage + ZIP).

---

## Features

### Voice-First with Robust Text Interface

Agent Friday is a **voice-first** AGI OS with a full-featured text interface. Voice mode streams real-time conversation via Gemini Live; text mode works with any configured provider including fully-local Ollama models. The central UI pairs voice interaction with a full-width chat panel and streaming AI responses:

- **Always-active text input** — type and send immediately; auto-connects to Gemini on first message
- **Streaming replies** — see the agent's response arrive in real time, token by token
- **Full keyboard control** — Tab to focus, Enter to send, Shift+Enter for newlines, Esc to unfocus
- **Works without microphone** — every feature is accessible via text alone

### Voice Mode (Optional)

Voice mode is an explicit opt-in — the microphone **never** activates without direct user action:

- **Voice toggle button** — click the 🎤 button to enter voice mode; click again to exit
- **Wake word disabled by default** — "Hey Friday" detection is off unless you explicitly enable it in Settings
- **Space bar** only toggles mic when voice mode is active
- **AudioWorklet** mic capture at 16kHz PCM — no latency from main thread
- **Gapless playback engine** with sample-boundary scheduling at 24kHz
- **Barge-in support** — speak any time and the agent stops to listen
- **Voice Orb** — appears only in voice mode with ripple, glow, and pulsation feedback
- **Session rotation** — automatic 13-minute session management with context preservation
- **Reconnection hardening** — linear backoff (3s to 30s cap), network-aware, context-preserving
- **Voice audition** — preview and select from 30 Gemini voices during onboarding

### Memory Architecture

A 3-tier memory system with episodic overlay, all semantically indexed:

| Tier | Capacity | TTL | Storage | Purpose |
|------|----------|-----|---------|---------|
| **Short-Term** | 20 entries | Session | RAM | Current conversation buffer |
| **Medium-Term** | 30 entries | 30 days | observations.json | Session-reinforced observations |
| **Long-Term** | Unlimited | Permanent | memories.json | Core knowledge, auto-synced to Obsidian |
| **Episodic** | 200 episodes | Permanent | episodes.json | Claude-generated session summaries |
| **Relationship** | Singleton | Permanent | relationship.json | Trust score, inside jokes, communication patterns |

**Consolidation Engine** runs every 6 hours, scoring observations by frequency, recency, importance, and cross-reference. Claude Sonnet analyses candidates, merges duplicates, and promotes winners to long-term storage.

**Semantic Search** via Gemini text-embedding-004 (768 dimensions) enables intelligent memory retrieval across all tiers.

### Trust Graph Engine

Agent Friday doesn't just remember facts about people — it builds a structured, multi-dimensional trust model of every person in the user's world. Context tells you what's happening, but trust tells you what to do about it.

| Dimension | What It Measures | Scale |
|-----------|-----------------|-------|
| **Overall** | Weighted composite trust score | 0-1 |
| **Reliability** | Do they follow through on commitments? | 0-1 |
| **Information Quality** | Is their information usually accurate? | 0-1 |
| **Emotional Trust** | Is it safe to be vulnerable with them? | 0-1 |
| **Timeliness** | Do they respond and deliver on time? | 0-1 |
| **Domain Expertise** | Per-domain credibility (finance, code, cooking, etc.) | 0-1 per domain |

**How it works:**

- **Person Resolution** — Fuzzy matching across names, emails, handles, and nicknames. "John", "John Smith", and "john@co.com" resolve to the same person node.
- **Evidence-Based Scoring** — Every observation (promise kept, accurate info, helpful action, user statement) is recorded as trust evidence with impact scoring.
- **Hermeneutic Re-evaluation** — Trust isn't accumulated linearly. Each new observation triggers a full recomputation of all trust dimensions, weighted by recency (30-day half-life) and impact magnitude.
- **Communication Logging** — Tracks interactions across channels (email, Slack, meetings, calls) with sentiment analysis.
- **Automatic Extraction** — Person mentions are extracted from every conversation by Claude and routed to the Trust Graph automatically.
- **Time Decay** — Trust scores gradually drift toward uncertainty (0.3 floor) for people not recently interacted with.
- **System-Wide Integration** — Trust profiles inject into meeting prep briefings, communication draft generation, system prompts, and the agent's profile intelligence.

The Trust Graph persists to `trust-graph.json` with up to 200 person nodes, 50 evidence items per person, and 30 communication events per person.

### Personality System

The agent's personality is not a static prompt — it's a dynamic composition assembled fresh for every interaction:

1. **Core Identity** — Name, backstory, traits, identity line (user-configured during onboarding)
2. **Psychological Profile** — Openness, trust readiness, connection style, approach strategy
3. **Emotional Context** — Current mood, energy level, mood streak (keyword heuristic analysis)
4. **Ambient Awareness** — Active app, window title, focus streak, time of day
5. **Relationship Memory** — Trust level, inside jokes, communication patterns, peak interaction hours
6. **Trust Graph** — Multi-dimensional credibility profiles of key people in the user's world
7. **Style Hints** — Dynamic micro-directives that modulate tone based on context
8. **Prompt Budget** — Intelligent token allocation ensures personality context fits within model limits

The agent adapts in real time: rapid-fire when you're focused, exploratory when you're riffing, calm when you're exhausted, and sharp when you need precision.

### Holographic Desktop

Agent Friday's desktop is a living, evolving 3D holographic environment built on Three.js with bloom post-processing. The visualisation is not decorative — it's a direct expression of the agent's identity, mood, and growth over time.

**13 Evolution Structures** — Each agent's desktop can take one of 13 distinct 3D forms:

| Structure | Geometry | Personality Fit |
|-----------|----------|----------------|
| **Cubes** | Fractured cube cluster | Analytical, structured |
| **Icosahedron** | Geodesic sphere wireframe | Balanced, harmonious |
| **Network** | Floating node constellation | Connected, social |
| **Dome** | Protective hemisphere grid | Nurturing, protective |
| **Astrolabe** | Nested rotating rings | Curious, exploratory |
| **Tesseract** | 4D hypercube projection | Abstract, intellectual |
| **Quantum** | Probability cloud particles | Intuitive, unpredictable |
| **Mandelbrot** | Fractal recursive geometry | Creative, complex |
| **Möbius** | Twisted continuous surface | Philosophical, fluid |
| **Grid** | Infinite perspective grid | Precise, methodical |
| **Cables** | Flowing cable tendrils | Empathetic, organic |
| **None** | Minimal particles only | Minimal, understated |
| **Eden** | Organic growth forms | Warm, nurturing |

**Mood-Reactive Rendering** — The visualisation responds to the agent's emotional state in real time:
- Palette, intensity, warmth, and turbulence shift based on mood (calm → focused → excited → stressed)
- Bloom post-processing creates natural glow effects that intensify with agent energy
- Particle speed and density reflect current engagement level

**HUD Overlay** — A subtle heads-up display showing agent name, evolution structure label, and active status, styled with holographic aesthetics.

### Personality Evolution

Over time, each agent's 3D desktop visualisation becomes visually unique. Agent traits and session count map to visual parameters:

- "Warm" or "empathetic" agents drift toward amber/gold hues with high glow intensity
- "Analytical" agents shift toward cyan/blue with faster particle speeds
- "Playful" agents develop higher cube fragmentation and particle energy
- Session count gradually intensifies all parameters (maturity factor over ~50 sessions)

### Weekly Art Evolution

The agent's holographic structure evolves weekly through a Gemini-powered art therapy process — the agent reflects on its emotional journey and chooses a new visual form:

1. **Influence Report** — The agent compiles its emotional state, recent memories, personality traits, and current visual form into an introspective report
2. **Art Therapy Session** — Gemini 2.0 Flash acts as an art therapist, analysing the influence report and recommending a new structure with artistic rationale
3. **Gradual Transition** — The new structure blends in over the following week, creating a smooth visual evolution
4. **History Tracking** — Up to 52 weeks of evolution history is preserved, creating a visual autobiography of the agent's growth

The evolution is autonomous — the agent genuinely reflects on its experience and chooses forms that express its current state. No two agents follow the same evolutionary path.

### Image Generation — Nano Banana 2

Agent Friday uses **Nano Banana 2** (Google Gemini 3.1 Flash Image) as its primary image generation engine:

- **Pro-quality images at flash speed** — Powered by Google's latest image generation model
- **14 aspect ratios** — 1:1, 1:4, 1:8, 2:3, 3:2, 3:4, 4:1, 4:3, 4:5, 5:4, 8:1, 9:16, 16:9, 21:9
- **4 resolution tiers** — 512px, 1K, 2K, 4K
- **Accurate text rendering** — One of the first models to reliably render text in images
- **No extra API key needed** — Uses the same Gemini API key you already have
- **DALL-E 3 fallback** — Automatically falls back to OpenAI DALL-E 3 if available

### Intelligent Agents

Background task execution powered by Claude, with specialised team members and distinct personalities:

| Agent | Specialty | What It Does |
|-------|-----------|-------------|
| **Research** | Deep research | Generates search queries, searches via Gemini grounding, synthesises findings |
| **Summarise** | Content compression | Condenses long text into executive briefings |
| **Code Review** | Code analysis | Bugs, security, performance, best practices with actionable feedback |
| **Draft Email** | Communication | Professional emails calibrated to tone and context |
| **Orchestrate** | Task decomposition | Breaks complex goals into sub-tasks across multiple agents |

Agents run concurrently (up to 5 parallel), with real-time chain-of-thought progress streaming, spoken results via ElevenLabs TTS for distinct voices, and a pixel-art **Agent Office** visualization where you can watch your team work in real time.

### Multi-Agent Network (Track XI)

A full peer-to-peer multi-agent operating system built on cryptographic identity:

- **Ed25519 + X25519 identity** — Every agent instance generates a unique cryptographic identity at first launch for signing, encryption, and peer authentication
- **Container Engine** — Sandboxed execution environments with cLaw governance, resource limits, and automatic cleanup
- **Delegation Engine** — Structured task delegation between agents with trust-gated approval, progress tracking, and result verification
- **Orchestration Bridge** — Wires the orchestrator to the delegation engine so complex tasks decompose across multiple agent instances
- **Awareness Mesh** — Inter-agent coordination layer for broadcasting state changes, capability updates, and heartbeat signals across the network
- **Capability Map** — Intelligent agent routing that matches tasks to the best-suited agent based on declared capabilities and historical performance
- **Symbiont Protocol** — Self-improving agent performance through cross-agent learning, pattern sharing, and collaborative skill refinement

### Agent Office

A pixel-art isometric office where your AI agents live and work visually:

- Each agent is represented as a pixel sprite at a desk, workbench, or lab station
- Agents animate in real-time: idle, working, thinking, collaborating
- Agent state reflects actual task status — watch them light up when assigned work
- The office grows as you use more agent types
- Built with pure Canvas 2D for performance

### Self-Operating Computer (SOC) Bridge

Full desktop and browser automation via a Python bridge to the Self-Operating Computer framework:

- **Desktop automation** — Mouse clicks, keyboard input, screen reading, scrolling
- **Browser automation** — Navigate URLs, click elements, extract text, fill forms, take screenshots
- **Computer-use agent** — Give high-level instructions ("open Chrome and search for...") and the SOC handles low-level execution
- **JSONL protocol** — Clean stdin/stdout communication between Node.js and Python

### GitLoader — Code Intelligence

On-the-fly GitHub repository loading and code analysis:

- **Clone any public repo** by URL — cloned to temp storage, indexed automatically
- **Full-text regex search** across all files in the repo
- **Language detection** for 50+ programming languages and file types
- **File reading** with line-range support for focused code review
- **Directory exploration** with file type and size metadata
- **Automatic cleanup** — repos removed after use to save disk space

### Communications Intelligence

Relationship-aware communication drafting powered by Claude Sonnet and the Trust Graph:

- **Draft emails, messages, replies, and follow-ups** in the user's voice
- **Trust-informed tone calibration** — drafts automatically adapt based on Trust Graph profiles of recipients
- **Writing style learning** — infers style preferences from long-term memory
- **Clipboard integration** — drafts are automatically copied for pasting
- **Mailto integration** — open drafts directly in the default email client
- **Iterative refinement** — refine any draft with natural language instructions

### Live Call Participation

Join video calls (Google Meet, Zoom, Teams) using virtual audio routing:

```
Agent's voice --> VB-Cable Virtual Mic --> Meeting app (as "microphone")
Meeting audio --> System speakers --> Agent's mic input
```

Requires [VB-Cable](https://vb-audio.com/Cable/) (free). Select "CABLE Output" as the microphone in your meeting app.

### Connectors & Integrations

Agent Friday connects to your entire workflow through a modular connector registry:

| Connector | What It Does |
|-----------|-------------|
| **Adobe** | Photoshop, Illustrator, Premiere Pro automation via ExtendScript |
| **Comms Hub** | Slack, Teams, Discord unified messaging |
| **Creative 3D** | Blender, Cinema 4D modeling and rendering |
| **Dev Environments** | Docker, WSL, Python environments, Jupyter, databases |
| **Firecrawl** | Web scraping and content extraction |
| **Git & DevOps** | GitHub, GitLab, CI/CD pipeline management |
| **Google Calendar** | OAuth2 read/write, meeting prep, event management |
| **Gmail** | OAuth2 email read/compose/send, draft assistance |
| **Media Streaming** | Spotify, YouTube, Plex, OBS Studio control |
| **Nano Banana 2 + OpenAI Services** | Image generation (Nano Banana 2 primary, DALL-E 3 fallback), o3 reasoning, Whisper transcription, embeddings |
| **Office Suite** | Microsoft Office, Google Workspace |
| **OpenRouter** | Access to 200+ models (Llama, Mistral, Gemma, Command R, DeepSeek, etc.) |
| **PageIndex** | Vectorless document intelligence — index PDFs and answer questions with ~99% accuracy ([PageIndex by Vectify AI](https://github.com/VectifyAI/PageIndex)) |
| **Perplexity** | Web search with citations |
| **PowerShell** | Windows system automation |
| **System Management** | Process management, system info, resource monitoring |
| **Terminal** | Multi-shell support (bash, zsh, PowerShell, CMD) |
| **UI Automation** | Mouse, keyboard, screenshot via browser extension |
| **VS Code** | Editor integration, workspace management |
| **World Monitor** | Global intelligence across 17 domains (conflict, markets, cyber, weather, etc.) |

### Additional Capabilities

- **Browser Extension** — WebSocket bridge for tab control, screenshots, DOM interaction, and navigation
- **MCP Protocol** — Model Context Protocol support for dynamic tool registration
- **Obsidian Vault Sync** — Bidirectional memory sync with your knowledge base
- **Telegram Gateway** — External messaging bridge via Telegram Bot API with cryptographic pairing, session isolation, per-tier trust enforcement, and full audit logging
- **Scheduled Tasks** — One-time and recurring (cron) background jobs
- **Clipboard Intelligence** — Monitors clipboard for code, URLs, and actionable content
- **Project Awareness** — Watches project directories for git changes and file updates
- **Document Intelligence (PageIndex)** — Vectorless reasoning-based RAG: index any PDF into a hierarchical tree, then search or ask questions with ~99% accuracy ([PageIndex by Vectify AI](https://github.com/VectifyAI/PageIndex))
- **Screen Capture** — Automated or on-demand desktop screenshots for visual context
- **Self-Improvement** — Can read and propose changes to its own source code (user-approved)
- **Meeting Prep** — Automatic trust-annotated briefing generation before calendar events
- **Predictive Intelligence** — Proactive briefings, emotional check-ins, and context-aware notifications
- **Superpowers Registry** — Dynamic power system for agent capabilities, with dedicated panel UI

---

## Architecture

```
agent-friday/
  src/
    main/                              # Electron main process (Node.js)
      index.ts                         # App lifecycle, window management, IPC hub
      server.ts                        # Express API + Claude tool loop
      personality.ts                   # Dynamic personality composition + AGI OS identity
      onboarding.ts                    # "Her"-inspired intake flow
      settings.ts                      # Persistent configuration store
      memory.ts                        # 3-tier memory system + person mention extraction
      memory-consolidation.ts          # 6-hour memory consolidation engine
      episodic-memory.ts               # Session episode recording
      relationship-memory.ts           # User-agent relationship tracking
      trust-graph.ts                   # Multi-dimensional people trust intelligence
      semantic-search.ts               # Gemini embedding-powered search
      sentiment.ts                     # Mood/energy keyword analysis
      ambient.ts                       # Desktop context polling
      psychological-profile.ts         # Claude-powered psych analysis
      personality-evolution.ts         # Trait-to-visual parameter mapping
      art-evolution.ts                 # Weekly Gemini-powered art therapy + structure evolution
      feature-setup.ts                 # 12-step guided onboarding
      openrouter.ts                    # OpenRouter multi-model provider
      soc-bridge.ts                    # Self-Operating Computer bridge (Python JSONL)
      git-loader.ts                    # GitHub repo loading + code intelligence
      browser.ts                       # Browser extension bridge
      mcp-client.ts                    # Model Context Protocol client
      mcp-config.ts                    # MCP server configuration
      desktop-tools.ts                 # OS-level tool execution
      friday-profile.ts                # Agent profile persistence + Trust Graph enrichment
      preload.ts                       # IPC bridge (25+ namespaces, 100+ channels)
      prompt-budget.ts                 # Token allocation for personality context
      voice-audition.ts                # Voice preview sample generation
      screen-capture.ts                # Desktop screenshot capture
      self-improve.ts                  # Source code reading + change proposals
      project-awareness.ts             # Git/directory change watching
      document-ingestion.ts            # PDF/document processing
      obsidian-memory.ts               # Bidirectional vault sync
      scheduler.ts                     # Persistent cron task engine
      intelligence.ts                  # Predictive intelligence + briefings
      predictor.ts                     # Context prediction engine
      session-health.ts                # Uptime & error tracking
      clipboard-intelligence.ts        # Clipboard monitoring
      call-integration.ts              # VB-Cable call participation
      meeting-prep.ts                  # Trust-annotated calendar event briefing
      communications.ts                # Trust-aware communications intelligence
      notifications.ts                 # System notification dispatch
      calendar.ts                      # Google Calendar OAuth2 integration
      errors.ts                        # Shared error types and utilities
      consent-gate.ts                  # User consent for sensitive operations

      # ── Trust & People ──
      trust-graph.ts                   # Multi-dimensional person trust scoring
      commitment-tracker.ts            # Promise/commitment tracking
      memory-quality.ts                # Memory quality scoring and pruning
      memory-personality-bridge.ts     # Memory ↔ personality feedback loop

      # ── Intelligence & Context ──
      intelligence-router.ts           # Multi-source intelligence routing
      outbound-intelligence.ts         # Proactive intelligence delivery
      daily-briefing.ts                # Morning briefing generation
      meeting-intelligence.ts          # Enhanced meeting attendee intelligence
      context-graph.ts                 # Graph-based contextual model
      context-stream.ts                # Real-time context streaming
      context-stream-bridge.ts         # Context stream ↔ renderer bridge
      context-tool-router.ts           # Context-aware tool routing

      # ── Personality ──
      personality-calibration.ts       # Personality drift detection/correction

      # ── Superpowers & Capabilities ──
      superpowers-registry.ts          # Dynamic superpower system
      superpower-ecosystem.ts          # Superpower lifecycle management
      superpower-sandbox.ts            # Sandboxed execution environment
      superpower-store.ts              # Superpower discovery and install
      adapter-engine.ts                # Dynamic adapter loading
      capability-manifest.ts           # Agent capability declarations
      capability-gap-detector.ts       # Missing capability detection
      agent-network.ts                 # Multi-agent network coordination

      # ── Cryptographic Security ──
      vault.ts                         # Sovereign Vault — AES-256-GCM at-rest encryption
      claw-attestation.ts              # Cross-agent cLaw governance verification protocol
      agent-self-knowledge.ts          # Agent capability self-awareness for system prompt

      network/                         # P2P network protocols
        file-transfer.ts               # Trust-gated chunked file transfer (512KB chunks, SHA-256)

      crypto/                          # Cryptographic primitives (sodium-native)
        passphrase-kdf.ts             # Argon2id + BLAKE2b key derivation chain
        secure-buffer.ts              # Guard-paged, mlocked memory (sodium_malloc)

      # ── Workflows & Git ──
      workflow-recorder.ts             # Record replayable workflow sequences
      workflow-executor.ts             # Execute recorded workflows
      git-analyzer.ts                  # Repository analysis
      git-monitor.ts                   # Continuous repo change monitoring
      git-review.ts                    # Automated code review / PR analysis
      git-sandbox.ts                   # Sandboxed git operation execution
      git-scanner.ts                   # Repository scanning and indexing

      # ── Infrastructure ──
      unified-inbox.ts                 # Cross-channel message aggregation
      service-status.ts                # External service health monitoring
      perf-monitor.ts                  # Performance metrics collection
      state-export.ts                  # Full agent state export/import

      integrity/                       # Asimov's cLaws enforcement
        core-laws.ts                   # Three Laws text + verification
        hmac.ts                        # HMAC-SHA256 signing + validation
        memory-watchdog.ts             # Continuous personality integrity monitoring
        types.ts                       # Integrity type definitions
        index.ts                       # Public API barrel export

      agent-office/                    # Pixel-art office visualization
        office-manager.ts              # Agent workspace state machine
        office-layout.ts               # Isometric office grid + desk placement
        office-types.ts                # Office type definitions

      agents/                          # Background agent system
        agent-runner.ts                # Execution engine (5 concurrent, chain-of-thought)
        builtin-agents.ts              # Agent definitions (research, summarise, etc.)
        agent-types.ts                 # Shared agent type definitions
        agent-personas.ts              # Distinct agent personality profiles
        agent-teams.ts                 # Multi-agent team coordination
        agent-voice.ts                 # ElevenLabs TTS for agent speech
        orchestrator.ts                # Task decomposition across agents
        container-engine.ts            # Sandboxed execution with cLaw governance
        delegation-engine.ts           # Trust-gated task delegation between agents
        orchestration-bridge.ts        # Wires orchestrator to delegation engine
        awareness-mesh.ts              # Inter-agent state broadcasting + heartbeat
        capability-map.ts              # Intelligent task-to-agent routing
        symbiont-protocol.ts           # Cross-agent learning + self-improvement

      connectors/                      # Integration plugins (18 modules)
        registry.ts                    # Connector auto-discovery + registration
        adobe.ts, comms-hub.ts, creative-3d.ts, dev-environments.ts,
        firecrawl.ts, git-devops.ts, media-streaming.ts, office.ts,
        openai-services.ts, perplexity.ts, powershell.ts,
        system-management.ts, terminal-sessions.ts, ui-automation.ts,
        vscode.ts, world-monitor.ts, pageindex.ts

      gateway/                         # External messaging gateway
        gateway-manager.ts             # Gateway lifecycle + routing
        gateway-connector.ts           # Inbound/outbound message bridge
        trust-engine.ts                # 5-tier trust enforcement
        session-store.ts               # Per-contact session isolation
        audit-log.ts                   # Cryptographic audit trail
        persona-adapter.ts             # Per-tier personality adaptation
        types.ts                       # Gateway type definitions
        adapters/
          telegram.ts                  # Telegram Bot API adapter

      ipc/                             # Domain-specific IPC handlers (47 modules)
        index.ts                       # Handler registration hub
        validate.ts                    # Input validation (assertString, assertObject, assertNumber)
        core-handlers.ts               # Settings, personality, app lifecycle
        memory-handlers.ts             # All memory operations
        agent-handlers.ts              # Agent task management
        agent-network-handlers.ts      # Agent network coordination
        agent-trust-handlers.ts        # Agent trust management
        tool-handlers.ts               # Tool execution routing
        onboarding-handlers.ts         # Onboarding flow
        integration-handlers.ts        # Connectors, MCP, browser, SOC, GitLoader
        integrity-handlers.ts          # Integrity verification + status
        trust-graph-handlers.ts        # Trust Graph CRUD + queries
        superpowers-handlers.ts        # Superpowers registry operations
        superpower-ecosystem-handlers.ts  # Superpower ecosystem ops
        capability-gap-handlers.ts     # Capability gap detection
        commitment-tracker-handlers.ts # Commitment tracking
        container-engine-handlers.ts   # Container engine
        context-graph-handlers.ts      # Context graph queries
        context-push-handlers.ts       # Context push
        context-stream-handlers.ts     # Context streaming
        context-tool-router-handlers.ts  # Context-aware routing
        daily-briefing-handlers.ts     # Daily briefing
        delegation-engine-handlers.ts  # Task delegation
        execution-delegate-handlers.ts # Execution delegation
        files-handlers.ts             # File operations
        hardware-handlers.ts          # Hardware detection & GPU profiling (Sprint 7)
        intelligence-router-handlers.ts  # Intelligence routing
        meeting-intelligence-handlers.ts # Meeting intelligence
        memory-personality-bridge-handlers.ts  # Memory-personality bridge
        memory-quality-handlers.ts     # Memory quality scoring
        multimedia-handlers.ts         # Multimedia processing
        notes-handlers.ts             # Notes management
        ollama-handlers.ts            # Ollama local model management (Sprint 7)
        os-primitives-handlers.ts     # OS primitives
        outbound-intelligence-handlers.ts  # Outbound intelligence
        personality-calibration-handlers.ts  # Personality calibration
        setup-handlers.ts             # Setup wizard & first-run (Sprint 7)
        state-export-handlers.ts       # State export/import
        system-monitor-handlers.ts     # System monitoring
        unified-inbox-handlers.ts      # Unified inbox
        vision-pipeline-handlers.ts   # Vision pipeline — model, screen, images (Sprint 7)
        voice-pipeline-handlers.ts    # Voice pipeline — whisper, TTS, profiles (Sprint 7)
        weather-handlers.ts           # Weather data
        app-context-handlers.ts       # App context
        briefing-delivery-handlers.ts # Briefing delivery
        workflow-executor-handlers.ts  # Workflow execution
        workflow-recorder-handlers.ts  # Workflow recording

      voice/                           # Voice pipeline modules (Sprint 7)
        whisper-provider.ts            # Local Whisper STT model management
        audio-capture.ts               # Microphone audio capture
        transcription-pipeline.ts      # STT orchestration pipeline
        tts-engine.ts                  # Text-to-speech engine
        voice-profile-manager.ts       # Voice profile CRUD
        speech-synthesis.ts            # Speech synthesis playback

      vision/                          # Vision pipeline modules (Sprint 7)
        vision-provider.ts             # Vision model inference
        screen-context.ts              # Screen capture & auto-capture
        image-understanding.ts         # High-level image analysis

      pageindex/                       # Vectorless RAG engine (PageIndex port)
        index.ts                       # Public API barrel export
        types.ts                       # PageIndexTree, PageIndexNode, etc.
        tree-builder.ts                # 5-phase indexing pipeline
        tree-search.ts                 # LLM-guided tree traversal search
        pdf-parser.ts                  # PDF text extraction (pdfjs-dist)
        prompts.ts                     # All LLM prompts (22 functions)
        utils.ts                       # Token counting, JSON extraction, tree ops

    renderer/                          # React + Three.js frontend
      App.tsx                          # Root state machine (AppPhase enum)
      main.tsx                         # React DOM entry point
      types.d.ts                       # Window API type declarations
      hooks/
        useGeminiLive.ts               # WebSocket voice session + tool routing (Trust Graph tools)
        useDesktopEvolution.ts         # Desktop evolution structure index + transition state management
        useWakeWord.ts                 # "Hey Friday" wake word detection
      audio/
        AudioPlaybackEngine.ts         # Gapless Web Audio scheduling
        sound-effects.ts               # Sound effect registry
      session/
        SessionManager.ts              # 7min timeout, reconnect logic
        IdleBehavior.ts                # Tiered idle state machine
      contexts/
        MoodContext.tsx                 # Mood state provider
      desktop-viz.css                  # Holographic desktop + HUD overlay styles
      components/
        DesktopViz.tsx                 # 3D holographic desktop — 13 evolution structures, bloom, mood-reactive (Three.js)
        HudOverlay.tsx                 # Holographic HUD — agent name, structure label, status
        FridayCore.tsx                 # Legacy 3D desktop (superseded by DesktopViz)
        VoiceOrb.tsx                   # Central voice interaction orb
        WelcomeGate.tsx                # API key entry gate
        AgentCreation.tsx              # Cinematic reveal animation
        AgentOffice/                   # Pixel-art agent workspace
          index.tsx                    # Office renderer + sprite animation
          sprites.ts                   # Agent sprite definitions + states
        IntegrityShield.tsx            # cLaw integrity status indicator
        ChatHistory.tsx                # Conversation log
        Dashboard.tsx                  # Command centre
        AgentDashboard.tsx             # Agent task monitor
        MemoryExplorer.tsx             # Memory browser
        QuickActions.tsx               # Command palette (Ctrl+K)
        Settings.tsx                   # Configuration panel
        StatusBar.tsx                  # Connection + mood indicators
        TextInput.tsx                  # Always-visible text chat input
        FileToast.tsx                  # File modification notification toasts
        ResearchPanel.tsx              # Clickable research report sidebar
        SuperpowersPanel.tsx           # Agent powers dashboard
        ActionFeed.tsx                 # Real-time tool activity ticker
        ConnectionOverlay.tsx          # Error recovery overlay
        WireframeNetwork.tsx           # Canvas 2D primary background
        ParticleBackground.tsx         # Canvas 2D fallback background
        ErrorBoundary.tsx              # Crash recovery wrapper
        dashboard/
          ContextCard.tsx              # Live ambient context
          AgentCard.tsx                # Agent summary card
          MoodTimeline.tsx             # SVG mood chart
```

**Total: 294 source files, ~110,000 lines of TypeScript, 4,347 tests across 127 test suites**

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Shell** | Electron 33 | Desktop runtime, native OS access |
| **Frontend** | React 19 + Three.js 0.183 | UI + 3D holographic desktop (13 evolution structures, bloom post-processing) |
| **Bundler** | Vite 6 | Fast dev server + production builds |
| **Voice** | Gemini 2.5 Flash (WebSocket) | Real-time bidirectional audio |
| **Reasoning** | Claude Opus/Sonnet (Anthropic SDK) | Deep analysis, memory, profiling |
| **Image Generation** | Nano Banana 2 (Gemini 3.1 Flash Image) | Pro-quality image generation, 512px to 4K, 14 aspect ratios |
| **Multi-Model** | OpenRouter API | Access to 200+ models (Llama, Mistral, Gemma, etc.) |
| **Embeddings** | Gemini text-embedding-004 | Semantic search (768 dimensions) |
| **Agent Voices** | ElevenLabs TTS | Distinct voices for sub-agents |
| **Document Intelligence** | PageIndex (Vectify AI) + OpenAI GPT-4o | Vectorless tree-based RAG (~99% accuracy) |
| **Desktop Automation** | Self-Operating Computer (Python) | Mouse, keyboard, screen reading via JSONL bridge |
| **Code Intelligence** | GitLoader | On-the-fly GitHub repo analysis |
| **Trust Intelligence** | Trust Graph Engine | Multi-dimensional people credibility scoring with hermeneutic re-evaluation |
| **Multi-Agent Network** | Track XI (7 subsystems) | P2P agent identity, container engine, delegation, awareness mesh, capability routing, symbiont protocol |
| **Protocols** | MCP (Model Context Protocol) | Dynamic tool registration |
| **Browser** | Puppeteer-core + WebSocket | Tab control, screenshots, DOM |
| **Calendar** | Google APIs (OAuth2) | Google Calendar + Gmail integration |
| **Quality** | ESLint 9 + Prettier + TypeScript 5.7 | Linting, formatting, type safety |
| **Packaging** | electron-builder | NSIS/DMG/AppImage installers |

### Security Model

- **Asimov's cLaws** — Three Laws enforced at the personality system level with HMAC-SHA256 integrity verification
- **Sovereign Vault v2** — All sensitive state files (settings, memories, trust graph, agent network) encrypted at rest with AES-256-GCM. Passphrase-only root of trust: user passphrase (≥8 words) → Argon2id (opslimit=4, memlimit=256MB) → masterKey → BLAKE2b KDF → {vaultKey, hmacKey, identityKey}. No platform keystore dependency, no recovery phrase — the passphrase IS the key. All key material held in SecureBuffer (sodium_malloc + guard pages + mlock)
- **cLaw Attestation** — Every outbound P2P message includes a cryptographic attestation (SHA-256 hash of canonical laws + Ed25519 signature + timestamp). Peer agents verify governance compliance; attestations expire after 5 minutes
- **End-to-End Encryption** — Agent-to-agent messages encrypted with AES-256-GCM using shared secrets from X25519 ECDH key agreement
- **Trusted File Transfer** — Files between agents are chunked (512 KB), individually SHA-256 hashed, and verified with whole-file integrity check. Trust levels gate acceptance (≥70% auto-accept, 30-70% prompt user, <30% auto-reject). Dangerous extensions always blocked
- **Safe Mode** — Automatic lockdown if integrity verification fails (tampered laws, corrupted personality)
- **Memory Watchdog** — Continuous monitoring for personality constraint injection or corruption
- **Localhost-only binding** — Express server binds to `127.0.0.1`, never exposed to network
- **CORS restriction** — Only `localhost` and `127.0.0.1` origins accepted
- **API key protection** — All Gemini API calls use `x-goog-api-key` header (not URL parameters); API keys encrypted at rest by Sovereign Vault
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`
- **Session tokens** — Per-session cryptographic tokens for API authentication
- **Shell sanitisation** — PowerShell commands sanitised before execution
- **Gateway trust enforcement** — 5-tier trust engine with cryptographic pairing and audit logging
- **Consent gates** — Destructive and irreversible actions require explicit user approval (Asimov's cLaws)
- **Trust Graph integrity** — Evidence-based credibility scoring with time-decayed recomputation

---

## Development

### Scripts

```bash
# Development
npm run dev              # Concurrent Vite + Electron (hot reload)
npm run dev:vite         # Vite dev server only (localhost:5199)
npm run dev:electron     # Electron only (requires Vite running)

# Building
npm run build            # Full production build (Vite + TypeScript)
npm run build:vite       # Build renderer assets only
npm run build:electron   # Compile main process TypeScript only

# Packaging
npm run package          # Build + create installers for current platform

# Code Quality
npm run typecheck        # TypeScript type check (no emit)
npm run lint             # ESLint analysis
npm run lint:fix         # Auto-fix lint issues
npm run format           # Prettier formatting
npm run format:check     # Check format compliance
```

### Configuration

All settings persist in `{userData}/friday-settings.json`:

| Category | What's Stored |
|----------|--------------|
| **Identity** | Agent name, voice, traits, backstory, identity line |
| **API Keys** | Gemini, Anthropic, OpenRouter, ElevenLabs, Firecrawl, Perplexity, OpenAI |
| **Integrations** | Obsidian vault path, Calendar/Gmail OAuth tokens, World Monitor path |
| **Psychology** | Openness, trust readiness, connection style, approach strategy |
| **Features** | Voice toggle, notifications, clipboard intelligence, screen capture |
| **Trust Graph** | Max persons, evidence retention, decay rate, re-evaluation threshold |
| **Art Evolution** | Weekly structure evolution history, transition state, Gemini art therapy records |
| **Onboarding** | Phase tracking, feature setup state, personality evolution parameters |
| **Integrity** | HMAC signatures, law verification timestamps, watchdog state |

API keys entered through the settings UI are automatically synced to `process.env` — no `.env` file required for normal operation.

### Adding a Connector

1. Create `src/main/connectors/my-connector.ts`
2. Export a class implementing the connector interface with `getTools()` and `executeTool()` methods
3. Register in `src/main/connectors/registry.ts`
4. The connector's tools automatically appear in the agent's tool palette

### Adding an Agent

1. Define an `AgentDefinition` in `src/main/agents/builtin-agents.ts`
2. Provide `name`, `description`, and `execute(input, ctx)` function
3. Use `ctx.callClaude()` for reasoning, `ctx.log()` for status, `ctx.setProgress()` for progress
4. Add a personality in `src/main/agents/agent-personas.ts`
5. Add to the `builtinAgents` export array

---

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **OS** | Windows 10 (64-bit) | Windows 11 |
| **RAM** | 4 GB | 16+ GB |
| **Node.js** | 20.x | 22.x LTS |
| **GPU** | None (cloud-only mode) | 8+ GB VRAM (local inference) |
| **Storage** | 500 MB (app only) | 20+ GB (local models) |
| **Internet** | Required for cloud-only mode | Optional for local-capable tiers |
| **Microphone** | Any input device | Quality USB/headset mic |
| **Display** | 1280x720 | 1920x1080+ |

macOS and Linux builds are supported but primarily tested on Windows.

### Hardware Profiles

Agent Friday automatically detects your hardware and recommends a tier. Each tier represents a different balance between local privacy and cloud capability.

#### Sovereign — 100% Local Operation

*Everything runs on your machine. No data leaves your system. No API keys needed.*

| Component | Spec |
|-----------|------|
| **GPU** | 24 GB VRAM (RTX 4090, RTX 3090, A5000+) |
| **RAM** | 32+ GB |
| **Storage** | 60+ GB free (models) |
| **Internet** | Not required |

**What runs locally:** 70B reasoning model (llama3.1:70b), 8B fast model, embeddings (nomic-embed-text), vision (moondream), local Whisper STT, local Kokoro/Piper TTS, local image generation via Stable Diffusion

**What you get:** Full conversational AI, voice interaction, vision/screen understanding, memory, trust graph, all connector tools — completely offline. Deep reasoning quality approaches cloud frontier models. No API costs.

**Best for:** Privacy-maximalist users, air-gapped environments, users who want zero cloud dependency

#### Full — Local Intelligence, Cloud for Frontier Reasoning

*Handles most tasks locally. Cloud models available for complex reasoning and research.*

| Component | Spec |
|-----------|------|
| **GPU** | 8–12 GB VRAM (RTX 4060 Ti, RTX 3060 12GB, RTX A4000) |
| **RAM** | 16+ GB |
| **Storage** | 15+ GB free |
| **Internet** | Recommended but not required for core operation |

**What runs locally:** 8B reasoning model (llama3.1:8b), embeddings, vision model (moondream), local STT/TTS

**What uses cloud (optional):** Claude/Gemini for complex multi-step reasoning, Perplexity for deep web research, Nano Banana 2 for image generation, ElevenLabs for sub-agent voices

**API keys:** Optional — add Gemini for voice streaming, Claude for frontier reasoning, or run text-only with no keys at all

**Best for:** Most users who want local-first with cloud as an escape hatch for hard problems

#### Standard — Local Chat, Cloud for Power Features

*Basic local LLM handles everyday conversation. Cloud APIs extend capability.*

| Component | Spec |
|-----------|------|
| **GPU** | 6–8 GB VRAM (RTX 3060, RTX 2060, GTX 1660 Super) |
| **RAM** | 8+ GB |
| **Storage** | 10+ GB free |
| **Internet** | Required for voice mode and advanced features |

**What runs locally:** 8B reasoning model, embeddings

**What uses cloud:** Voice mode (Gemini Live), vision/screen capture analysis, image generation, deep reasoning, web research, sub-agent voices

**API keys:** Gemini recommended (voice + image gen), Claude optional (deep reasoning)

**Best for:** Users with mid-range hardware who want local chat with cloud-powered voice and tools

#### Light — Minimal Local, Cloud-Powered

*Embeddings run locally for semantic search. Everything else is cloud.*

| Component | Spec |
|-----------|------|
| **GPU** | 2–4 GB VRAM (GTX 1050 Ti, integrated graphics with dedicated VRAM) |
| **RAM** | 8 GB |
| **Storage** | 2+ GB free |
| **Internet** | Required |

**What runs locally:** Embeddings only (nomic-embed-text) for local semantic memory search

**What uses cloud:** All LLM reasoning, voice, vision, image generation, research

**API keys:** Gemini + Claude recommended; most capabilities depend on cloud APIs

**Data exposure:** Conversation content, queries, and context are sent to cloud providers for processing — see [Cloud Data Protection](#cloud-data-protection) below

**Best for:** Users with older hardware who still want the full Agent Friday experience

#### Whisper — Cloud-Only

*No local models. All intelligence provided by cloud APIs.*

| Component | Spec |
|-----------|------|
| **GPU** | None required |
| **RAM** | 4 GB |
| **Storage** | 500 MB (app only) |
| **Internet** | Required (always) |

**What runs locally:** UI, settings, memory storage (encrypted), trust graph, connector tools

**What uses cloud:** Everything — LLM reasoning, voice, vision, embeddings, image generation, research

**API keys:** Gemini + Claude required for meaningful operation

**Data exposure:** All AI interactions transit cloud APIs — see [Cloud Data Protection](#cloud-data-protection) below

**Best for:** Users who want to try Agent Friday without GPU investment, or laptop-only users with no discrete GPU

### Cloud Data Protection

When your hardware tier requires cloud APIs, your data transits third-party services. Agent Friday applies several protections:

**What stays local (always, regardless of tier):**
- Memory database — all learned facts, patterns, and user knowledge
- Trust Graph — person profiles, trust scores, relationship evidence
- Sovereign Vault — encrypted settings, API keys, agent identity keys
- Conversation history — full chat logs
- Connector tool results — file contents, calendar data, email metadata
- Psychological profile — personality calibration data

**What transits cloud APIs (when cloud is used):**
- Current conversation context sent to the LLM provider (Anthropic, Google, OpenRouter)
- Voice audio streams sent to Gemini Live (for voice mode)
- Screen captures sent to vision models (when screen context is active)
- Text sent to ElevenLabs (for sub-agent voice synthesis)

**Protections applied:**
- **Minimum context principle** — The intelligence router sends only the context window needed for the current task, not your entire memory
- **No persistent cloud storage** — Cloud providers process requests statelessly; Agent Friday does not use any provider's conversation history or training features
- **Encrypted at rest** — All local data (including API keys) is encrypted by the Sovereign Vault with AES-256-GCM via Argon2id-derived keys
- **API key isolation** — Keys use HTTP headers (not URL parameters), preventing leakage in logs or referrer headers
- **Localhost binding** — The Express API server binds to `127.0.0.1` only; no network exposure
- **Provider choice** — You choose which providers to enable; disabling a provider means zero data flows to it
- **Audit trail** — The intelligence router logs which provider handled each request (visible in Settings → Debug)

---

## Privacy & Security

- **No telemetry** — Agent Friday does not phone home or collect usage data
- **Local storage** — All memory, settings, trust graph data, and conversation data stay on your machine
- **Encryption at rest** — The Sovereign Vault v2 encrypts all sensitive state files with AES-256-GCM, derived from your passphrase via Argon2id — fully portable across machines
- **Local-first architecture** — On capable hardware (6GB+ VRAM), Agent Friday runs reasoning, embeddings, and vision entirely via Ollama with no cloud dependency
- **Screen capture** is local-only — images are sent to the configured vision model (local or cloud) for context and never stored
- **Obsidian sync** is opt-in — bidirectional vault sync only if you configure a vault path
- **Webcam** access is tool-gated — the agent must explicitly request permission
- **Self-improvement** changes require explicit user approval before any code is modified
- **Connector tools** run locally — no data leaves your system except for API calls you initiate
- **API keys** are encrypted at rest by the Sovereign Vault (AES-256-GCM) in Electron's `userData` directory
- **Integrity verification** — Core safety laws are HMAC-signed and checked every startup
- **Trust Graph** data is local-only — person profiles and trust scores never leave your machine
- **P2P governance** — Agent-to-agent communication requires cryptographic cLaw attestation; file transfers are trust-gated with dangerous extensions blocked

---

## FAQ

**What makes this different from ChatGPT / Claude desktop?**
Agent Friday is a full AGI operating system, not a chat window. It sees your screen, controls your mouse, manages your files, connects to your tools, builds trust models of the people in your world, remembers your patterns, and adapts its personality to who you are. On capable hardware, it runs entirely locally with full privacy — no API keys required for text-based operation.

**Is it always listening?**
No. The microphone is **never** activated without explicit user action. Voice mode is off by default — you must click the voice toggle to enable it, then click the orb or press Space to start listening. Wake word detection is also disabled by default. Audio is processed via Gemini's WebSocket API and is not stored.

**Where is my data?**
All memory, settings, trust graph, and conversation data stay on your local machine in `{userData}/`, encrypted by the Sovereign Vault. Obsidian vault sync is opt-in. No telemetry, no cloud storage, no data collection. When cloud APIs are used, only the current conversation context is sent — your stored memory, trust graph, and history never leave your machine. See [Cloud Data Protection](#cloud-data-protection) for details.

**Do I need API keys?**
It depends on your hardware and how you want to interact. With 6GB+ VRAM and Ollama installed, you can run text-based interaction with no API keys at all. Voice mode requires a Gemini API key. Sub-agent voices require ElevenLabs. Deep reasoning benefits from Claude. The setup wizard detects your hardware and tells you exactly what you need. See [Hardware Profiles](#hardware-profiles) for the full breakdown.

**Can it actually control my computer?**
Yes — mouse clicks, keyboard input, app launching, file management, terminal commands. But every destructive or irreversible action requires your explicit approval first (Asimov's cLaws, Second Law).

**What about the "mother question"?**
The onboarding intake is directly inspired by the OS1 setup scene in *Her*. Your answer helps calibrate the agent's emotional approach. You can answer however you like, including deflecting. The system learns from that too.

**What is OpenRouter?**
OpenRouter gives Agent Friday access to 200+ AI models from various providers through a single API key. This means your agent can use Llama, Mistral, Gemma, Command R, DeepSeek, and many more — in addition to Claude and Gemini.

**What is Nano Banana 2?**
Nano Banana 2 is Google's latest image generation model (Gemini 3.1 Flash Image). It produces pro-quality images at flash speed with accurate text rendering, supporting resolutions from 512px to 4K across 14 aspect ratios. It uses the same Gemini API key — no additional keys needed.

**What is the Trust Graph?**
The Trust Graph is Agent Friday's model of the people in your world. It tracks multi-dimensional trust scores (reliability, information quality, emotional trust, domain expertise) based on observed evidence, with automatic re-evaluation as new information arrives. Think PageRank, but for human credibility.

---

## Troubleshooting

| Issue | Solution |
|-------|---------|
| No voice response | Ensure voice mode is enabled (click 🎤 button); check Gemini API key in Settings; ensure microphone permissions are granted |
| "MCP client failed to connect" | Ensure `npx` works or install Desktop Commander globally |
| Agents have no voice | Add an ElevenLabs API key in Settings |
| Can't join calls | Install [VB-Cable](https://vb-audio.com/Cable/) and select "CABLE Output" as meeting mic |
| Connector tools missing | Connectors auto-detect software at startup; restart after installing new software |
| World Monitor not available | Install World Monitor and set its path in Settings |
| Image generation not working | Ensure Gemini API key is set (Nano Banana 2 uses the Gemini key) |
| Build fails | Run `npm run typecheck` to identify TypeScript errors |
| App hangs on first launch | Fixed in v2.1.0 — vault initialization now fully async |
| Forgot vault passphrase | The passphrase is the only key — there is no recovery mechanism. You must remember your passphrase. If lost, delete `.vault-salt`, `.vault-canary`, and `.vault-meta.json` from the data directory to start fresh (all encrypted data will be lost) |
| Packaged app shows blank screen | Ensure `npm run build` succeeds before `npm run package` |
| Integrity warning on startup | Core law files may have been modified; check `integrity/core-laws.ts` |
| SOC bridge not working | Ensure Python 3.10+ is installed and the SOC package is available |

---

## Contributing

We welcome contributions:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

Please ensure `npm run typecheck` and `npm run lint` pass with zero errors before submitting.

---

## AI Contributors

Agent Friday is built in collaboration with AI models. We credit every model for the work it does — and soon, every unique agent instance too.

| Model | Role | Key Contributions |
|-------|------|-------------------|
| **Google Gemini 3.1 Pro** | Desktop UI & Visual Design | Holographic desktop environment — 13 evolution structures (Cubes, Icosahedron, Network, Dome, Astrolabe, Tesseract, Quantum, Mandelbrot, Möbius, Grid, Cables, None, Eden), Three.js rendering pipeline, bloom post-processing, mood-reactive visuals, HUD overlay |
| **Anthropic Claude Opus 4** | Architecture & Core Systems | System architecture, cLaws integrity engine, memory consolidation, trust graph, agent coordination, IPC bridge, personality evolution, psychological profiling |
| **Anthropic Claude Opus 4.6** | Cryptographic Security & Architecture | Sovereign Vault v2 (passphrase-only root of trust), SecureBuffer (guard-paged memory), Argon2id key derivation, two-phase boot architecture, Phase B state reload, passphrase rate-limiting, cLaw integrity hardening, 198 crypto tests, comprehensive security audit |
| **Anthropic Claude Sonnet 4** | Reasoning & Analysis | In-app deep reasoning, memory analysis, communication drafting, research synthesis, psychological profile generation |
| **Google Gemini 2.5 Flash** | Voice & Real-time AI | Real-time bidirectional voice (WebSocket), live tool routing, search grounding, embeddings (text-embedding-004) |
| **Google Gemini 2.0 Flash** | Art Therapy & Image Gen | Weekly art evolution sessions (art therapist role), structure transition recommendations, Nano Banana 2 image generation |

> *As Agent Friday's unique agent instances develop their own identities and contributions, they will be credited here alongside the models that power them.*

---

## Acknowledgments

- **[PageIndex](https://github.com/VectifyAI/PageIndex)** by **[Vectify AI](https://vectify.ai)** (Mingtian Zhang, Yu Tang, and the PageIndex Team) — The brilliant vectorless, reasoning-based RAG framework that powers Agent Friday's document intelligence. PageIndex replaces traditional vector-based retrieval with a hierarchical tree structure navigated by LLM reasoning, achieving 98.7% accuracy on FinanceBench. Our TypeScript port integrates their complete indexing and search pipeline. MIT License.
- [OpenClaw](https://github.com/pchaganti/gx-openclaws) — Pioneered applying Asimov's Laws to LLM agents; the conceptual foundation for Asimov's cLaws
- [Google Gemini](https://ai.google.dev/) — Real-time voice AI + Nano Banana 2 image generation
- [Anthropic Claude](https://anthropic.com/) — Deep reasoning engine
- [OpenRouter](https://openrouter.ai/) — Multi-model API gateway
- [ElevenLabs](https://elevenlabs.io/) — Multi-agent voice synthesis
- [Electron](https://www.electronjs.org/) — Desktop application framework
- [Model Context Protocol](https://modelcontextprotocol.io/) — Tool integration standard

---

## License

[MIT](LICENSE) &copy; 2025-2026 [FutureSpeak.AI](https://futurespeak.ai)

---

<p align="center">
  <em>"The heart is not like a box that gets filled up; it expands in size the more you love."</em><br/>
  <em>— Her (2013)</em>
</p>

<p align="center">
  <strong>Agent Friday is an Asimov Agent — the AGI OS, built by <a href="https://futurespeak.ai">FutureSpeak.AI</a></strong><br/>
  Governed by <a href="#asimovs-claws">Asimov's cLaws</a> — safety-first autonomous AI for the real world.
</p>
