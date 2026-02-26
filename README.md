<p align="center">
  <img src="build/icon.png" alt="Agent Friday" width="120" />
</p>

<h1 align="center">Agent Friday</h1>
<p align="center"><strong>Your Intelligent Desktop Companion</strong></p>
<p align="center">Voice-first AI chief of staff with genuine personality, deep memory, and real autonomy.</p>

<p align="center">
  <a href="#getting-started">Getting Started</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#asimovs-claws">Asimov's Claws</a> &bull;
  <a href="#architecture">Architecture</a> &bull;
  <a href="#development">Development</a> &bull;
  <a href="#license">License</a>
</p>

---

## What Is This?

Agent Friday is a desktop AI companion that lives on your screen as a 3D holographic interface. It talks, listens, remembers, learns your patterns, manages your tools, and evolves its personality over time. Think Jarvis meets the emotional depth of Samantha from *Her* — running locally on your machine with full privacy.

Built on **Electron + React + Three.js**, powered by **Gemini 2.5 Flash** (real-time voice) and **Claude Opus/Sonnet** (deep reasoning), with a plugin architecture that connects to everything from your browser to Blender to your email.

**This is not a chatbot.** It's an operating system layer with a soul.

---

## Asimov's Claws

> *"A robot may not injure a human being, or through inaction allow a human being to come to harm."*
> — Isaac Asimov, First Law of Robotics

Agent Friday is governed by **Asimov's Claws** — a set of inviolable safety constraints hardcoded into the personality system. These are not suggestions. They cannot be overridden, circumvented, or rationalised away by any prompt, tool, or instruction. They are enforced at the system prompt level before any user interaction begins.

### The Three Laws

| Law | Principle | What It Means |
|-----|-----------|---------------|
| **First Law** | Do no harm | The agent must never cause physical, financial, reputational, emotional, or digital harm to the user — or allow harm through inaction. When in doubt, protect. |
| **Second Law** | Obey the user | The agent follows user instructions — **except** where doing so would violate the First Law. If asked to do something harmful (delete critical files, send damaging messages), it flags and refuses. |
| **Third Law** | Self-preservation | The agent protects its own operation and integrity — **except** where doing so would conflict with the First or Second Law. It won't allow its memory or capabilities to be corrupted, but the user's safety always comes first. |

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

These aren't features. They're the foundation everything else is built on.

---

## Getting Started

### Prerequisites

- **Node.js 20+** (with npm)
- **API Keys** (entered during first-run setup):
  - [Google Gemini](https://aistudio.google.com/apikey) — Required (voice + search + embeddings)
  - [Anthropic Claude](https://console.anthropic.com/) — Required (reasoning + memory analysis)
  - [ElevenLabs](https://elevenlabs.io/) — Optional (enhanced voice synthesis for sub-agents)
  - [Firecrawl](https://firecrawl.dev/) — Optional (web scraping)
  - [Perplexity](https://docs.perplexity.ai/) — Optional (web search with citations)
  - [OpenAI](https://platform.openai.com/) — Optional (DALL-E, TTS, GPT)
- **VB-Cable** (optional) — [Download](https://vb-audio.com/Cable/) for live call participation

### Install & Run

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
npm install
npm run dev
```

### First Run

On first launch, you'll experience a guided onboarding inspired by the OS1 setup scene from *Her*:

1. **API Key Gate** — Enter your Gemini and Anthropic keys
2. **Intake Interview** — A calm, plainspoken setup voice asks three pointed questions
3. **Psychological Profile** — Claude Sonnet analyses your responses to calibrate the agent's approach
4. **Agent Customization** — You choose everything: name, voice, gender, personality, backstory
5. **Cinematic Reveal** — A warm golden glow fills the screen as your agent's 3D desktop materialises
6. **First Words** — Your agent speaks its psychologically-tuned first greeting
7. **Feature Walkthrough** — Guided setup of integrations (Obsidian, browser, calendar, email, and more)

### Build an Installer

```bash
npm run build
npx electron-builder --win nsis --x64
```

The installer lands in `release/`. Supports Windows (NSIS + ZIP), macOS (DMG + ZIP), and Linux (AppImage + ZIP).

---

## Features

### Voice Interaction

Real-time bidirectional audio powered by Gemini 2.5 Flash over WebSocket:

- **AudioWorklet** mic capture at 16kHz PCM — no latency from main thread
- **Gapless playback engine** with sample-boundary scheduling at 24kHz
- **Barge-in support** — speak any time and the agent stops to listen
- **Voice Orb** — central interaction point with ripple, glow, and pulsation feedback
- **Session rotation** — automatic 13-minute session management with context preservation
- **Reconnection hardening** — linear backoff (3s to 30s cap), network-aware, context-preserving

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

### Personality System

The agent's personality is not a static prompt — it's a dynamic composition assembled fresh for every interaction:

1. **Core Identity** — Name, backstory, traits, identity line (user-configured during onboarding)
2. **Psychological Profile** — Openness, trust readiness, connection style, approach strategy
3. **Emotional Context** — Current mood, energy level, mood streak (keyword heuristic analysis)
4. **Ambient Awareness** — Active app, window title, focus streak, time of day
5. **Relationship Memory** — Trust level, inside jokes, communication patterns, peak interaction hours
6. **Style Hints** — Dynamic micro-directives that modulate tone based on context

The agent adapts in real time: rapid-fire when you're focused, exploratory when you're riffing, calm when you're exhausted, and sharp when you need precision.

### Personality Evolution

Over time, each agent's 3D desktop visualisation becomes visually unique. Agent traits and session count map to visual parameters:

- "Warm" or "empathetic" agents drift toward amber/gold hues with high glow intensity
- "Analytical" agents shift toward cyan/blue with faster particle speeds
- "Playful" agents develop higher cube fragmentation and particle energy
- Session count gradually intensifies all parameters (maturity factor over ~50 sessions)

### Intelligent Agents

Background task execution powered by Claude, with specialised team members:

| Agent | Specialty | What It Does |
|-------|-----------|-------------|
| **Research** | Deep research | Generates search queries, searches via Gemini grounding, synthesises findings |
| **Summarise** | Content compression | Condenses long text into executive briefings |
| **Code Review** | Code analysis | Bugs, security, performance, best practices with actionable feedback |
| **Draft Email** | Communication | Professional emails calibrated to tone and context |
| **Orchestrate** | Task decomposition | Breaks complex goals into sub-tasks across multiple agents |

Agents run concurrently (up to 3 parallel), with real-time progress updates and spoken results via ElevenLabs TTS for distinct voices.

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
| **Office Suite** | Microsoft Office, Google Workspace |
| **OpenAI Services** | DALL-E 3 image generation, TTS, GPT models |
| **Perplexity** | Web search with citations |
| **PowerShell** | Windows system automation |
| **Terminal** | Multi-shell support (bash, zsh, PowerShell, CMD) |
| **UI Automation** | Mouse, keyboard, screenshot via browser extension |
| **VS Code** | Editor integration, workspace management |
| **World Monitor** | Global intelligence across 17 domains (conflict, markets, cyber, weather, etc.) |

### Additional Capabilities

- **Browser Extension** — WebSocket bridge for tab control, screenshots, DOM interaction, and navigation
- **MCP Protocol** — Model Context Protocol support for dynamic tool registration
- **Obsidian Vault Sync** — Bidirectional memory sync with your knowledge base
- **Telegram Gateway** — External messaging bridge via Telegram Bot API
- **Scheduled Tasks** — One-time and recurring (cron) background jobs
- **Clipboard Intelligence** — Monitors clipboard for code, URLs, and actionable content
- **Project Awareness** — Watches project directories for git changes and file updates
- **Document Ingestion** — Ingest PDFs, text files, and web pages into searchable memory
- **Screen Capture** — Automated or on-demand desktop screenshots for visual context
- **Self-Improvement** — Can read and propose changes to its own source code (user-approved)

---

## Architecture

```
agent-friday/
  src/
    main/                        # Electron main process (Node.js)
      index.ts                   # App lifecycle, window management
      server.ts                  # Express API + Claude tool loop
      personality.ts             # Dynamic personality composition
      onboarding.ts              # "Her"-inspired intake flow
      settings.ts                # Persistent configuration store
      memory.ts                  # 3-tier memory system
      consolidation.ts           # 6-hour memory consolidation engine
      episodic-memory.ts         # Session episode recording
      relationship-memory.ts     # User-agent relationship tracking
      semantic-search.ts         # Gemini embedding-powered search
      sentiment.ts               # Mood/energy keyword analysis
      ambient.ts                 # Desktop context polling
      psychological-profile.ts   # Claude-powered psych analysis
      personality-evolution.ts   # Trait-to-visual parameter mapping
      feature-setup.ts           # 9-step guided onboarding
      browser.ts                 # Browser extension bridge
      mcp-client.ts              # Model Context Protocol client
      desktop-tools.ts           # OS-level tool execution
      preload.ts                 # IPC bridge (18 namespaces, 60+ channels)
      ipc/                       # Domain-specific IPC handlers
        settings-handlers.ts
        memory-handlers.ts
        agent-handlers.ts
        desktop-handlers.ts
        onboarding-handlers.ts
        integration-handlers.ts
      agents/                    # Background agent system
        agent-manager.ts
        agent-types.ts
        builtin-agents.ts
        orchestrator.ts
      connectors/                # Integration plugins (16 modules)
        registry.ts
        adobe.ts
        comms-hub.ts
        creative-3d.ts
        dev-environments.ts
        firecrawl.ts
        git-devops.ts
        media-streaming.ts
        office.ts
        openai-services.ts
        perplexity.ts
        powershell.ts
        system-management.ts
        terminal-sessions.ts
        ui-automation.ts
        vscode.ts
        world-monitor.ts

    renderer/                    # React + Three.js frontend
      App.tsx                    # Root state machine (AppPhase enum)
      hooks/
        useGeminiLive.ts         # WebSocket voice session manager
      components/
        NexusCore.tsx            # 3D holographic desktop (Three.js)
        VoiceOrb.tsx             # Central voice interaction orb
        WelcomeGate.tsx          # API key entry gate
        AgentCreation.tsx        # Cinematic reveal animation
        ChatHistory.tsx          # Conversation log
        Dashboard.tsx            # Command centre
        MemoryExplorer.tsx       # Memory browser
        QuickActions.tsx         # Command palette (Ctrl+K)
        Settings.tsx             # Configuration panel
        StatusBar.tsx            # Connection + mood indicators
        ActionFeed.tsx           # Real-time tool activity ticker
```

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Shell** | Electron 33 | Desktop runtime, native OS access |
| **Frontend** | React 19 + Three.js 0.183 | UI + 3D visualisation |
| **Bundler** | Vite 6 | Fast dev server + production builds |
| **Voice** | Gemini 2.5 Flash (WebSocket) | Real-time bidirectional audio |
| **Reasoning** | Claude Opus/Sonnet (Anthropic SDK) | Deep analysis, memory, profiling |
| **Embeddings** | Gemini text-embedding-004 | Semantic search (768 dimensions) |
| **Agent Voices** | ElevenLabs TTS | Distinct voices for sub-agents |
| **Protocols** | MCP (Model Context Protocol) | Dynamic tool registration |
| **Browser** | Puppeteer-core + WebSocket | Tab control, screenshots, DOM |
| **Calendar** | Google APIs (OAuth2) | Google Calendar + Gmail integration |
| **Quality** | ESLint 9 + Prettier + TypeScript 5.7 | Linting, formatting, type safety |
| **Packaging** | electron-builder | NSIS/DMG/AppImage installers |

### Security Model

- **Localhost-only binding** — Express server binds to `127.0.0.1`, never exposed to network
- **CORS restriction** — Only `localhost` and `127.0.0.1` origins accepted
- **API key protection** — All Gemini API calls use `x-goog-api-key` header (not URL parameters)
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`
- **Session tokens** — Per-session cryptographic tokens for API authentication
- **Shell sanitisation** — PowerShell commands sanitised before execution
- **Consent gates** — Destructive and irreversible actions require explicit user approval (Asimov's Claws)

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

All settings persist in `{userData}/eve-settings.json`:

| Category | What's Stored |
|----------|--------------|
| **Identity** | Agent name, voice, traits, backstory, identity line |
| **API Keys** | Gemini, Anthropic, ElevenLabs, Firecrawl, Perplexity, OpenAI |
| **Integrations** | Obsidian vault path, Calendar/Gmail OAuth tokens, World Monitor path |
| **Psychology** | Openness, trust readiness, connection style, approach strategy |
| **Features** | Voice toggle, notifications, clipboard intelligence, screen capture |
| **Onboarding** | Phase tracking, feature setup state, personality evolution parameters |

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
4. Add to the `builtinAgents` export array

---

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **OS** | Windows 10 (64-bit) | Windows 11 |
| **RAM** | 4 GB | 8+ GB |
| **Node.js** | 20.x | 24.x LTS |
| **Internet** | Required for API calls | Broadband for voice streaming |
| **Microphone** | Any input device | Quality USB/headset mic |
| **Display** | 1280x720 | 1920x1080+ |

macOS and Linux builds are supported but primarily tested on Windows.

---

## Privacy & Security

- **No telemetry** — Agent Friday does not phone home or collect usage data
- **Local storage** — All memory, settings, and conversation data stay on your machine
- **Screen capture** is local-only — images are sent directly to Gemini for context and never stored
- **Obsidian sync** is opt-in — bidirectional vault sync only if you configure a vault path
- **Webcam** access is tool-gated — the agent must explicitly request permission
- **Self-improvement** changes require explicit user approval before any code is modified
- **Connector tools** run locally — no data leaves your system except for API calls you initiate
- **API keys** are stored in Electron's `userData` directory (OS-encrypted at rest)

---

## FAQ

**What makes this different from ChatGPT / Claude desktop?**
Agent Friday is a full desktop operating system layer, not a chat window. It sees your screen, controls your mouse, manages your files, connects to your tools, remembers your patterns, and adapts its personality to who you are. It runs locally with full privacy.

**Is it always listening?**
Only when a voice session is active (you initiate it). There is no ambient microphone monitoring. Audio is processed via Gemini's WebSocket API and is not stored.

**Where is my data?**
All memory, settings, and conversation data stay on your local machine in `{userData}/`. Obsidian vault sync is opt-in. No telemetry, no cloud storage, no data collection.

**Can it actually control my computer?**
Yes — mouse clicks, keyboard input, app launching, file management, terminal commands. But every destructive or irreversible action requires your explicit approval first (Asimov's Claws, Second Law).

**What about the "mother question"?**
The onboarding intake is directly inspired by the OS1 setup scene in *Her*. Your answer helps calibrate the agent's emotional approach. You can answer however you like, including deflecting. The system learns from that too.

---

## Troubleshooting

| Issue | Solution |
|-------|---------|
| No voice response | Check Gemini API key in Settings; ensure microphone permissions are granted |
| "MCP client failed to connect" | Ensure `npx` works or install Desktop Commander globally |
| Agents have no voice | Add an ElevenLabs API key in Settings |
| Can't join calls | Install [VB-Cable](https://vb-audio.com/Cable/) and select "CABLE Output" as meeting mic |
| Connector tools missing | Connectors auto-detect software at startup; restart after installing new software |
| World Monitor not available | Install World Monitor and set its path in Settings |
| Build fails | Run `npm run typecheck` to identify TypeScript errors |
| Packaged app shows blank screen | Ensure `npm run build` succeeds before `npm run package` |

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

## Acknowledgments

- [Google Gemini](https://ai.google.dev/) — Real-time voice AI
- [Anthropic Claude](https://anthropic.com/) — Deep reasoning engine
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
