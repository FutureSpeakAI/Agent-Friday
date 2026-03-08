# Agent Friday — Living Architecture Document

> **Method**: Adapted from Nick Tune's Domain-Driven Architecture mapping for monorepo Electron applications.
> **Last updated**: 2026-03-01
> **Scope**: Complete system — main process, renderer, IPC bridge, agents, connectors, gateway, MCP, integrity, cryptographic security (Sovereign Vault, cLaw Attestation, Trusted File Transfer), multi-agent network (Track XI: Container Engine, Delegation Engine, Orchestration Bridge, Awareness Mesh, Capability Map, Symbiont Protocol), SOC, GitLoader, Trust Graph, Context Stream, Superpowers, Workflows, Git Analysis Suite.

---

## Table of Contents

1. [System Context](#1-system-context)
2. [Bounded Contexts](#2-bounded-contexts)
3. [Main Process Module Map](#3-main-process-module-map)
4. [Renderer Component Hierarchy](#4-renderer-component-hierarchy)
5. [IPC Bridge Contract](#5-ipc-bridge-contract)
6. [Data Flow: Audio Pipeline](#6-data-flow-audio-pipeline)
7. [Data Flow: Memory Lifecycle](#7-data-flow-memory-lifecycle)
8. [Data Flow: Tool Execution Chain](#8-data-flow-tool-execution-chain)
9. [Data Flow: Session Management](#9-data-flow-session-management)
10. [Data Flow: Agent Task Pipeline](#10-data-flow-agent-task-pipeline)
11. [Swim Lane: User Interaction Cycle](#11-swim-lane-user-interaction-cycle)
12. [Swim Lane: First-Run Experience](#12-swim-lane-first-run-experience)
13. [Dependency Graph](#13-dependency-graph)
14. [Visual System Architecture](#14-visual-system-architecture)
15. [Security Boundary Map](#15-security-boundary-map)

---

## 1. System Context

```mermaid
graph TB
    subgraph "Agent Friday Desktop App"
        subgraph "Renderer Process (Chromium)"
            UI[React 19 UI Layer]
            THREE[Three.js DesktopViz<br/>13 Evolution Structures]
            CANVAS[Canvas 2D Backgrounds]
            OFFICE_UI[Agent Office Canvas]
            AUDIO_OUT[AudioPlaybackEngine]
            MIC[Mic Capture AudioWorklet]
        end

        subgraph "Main Process (Node.js)"
            CORE[Core Orchestration]
            MEM[Memory System]
            AGENTS[Agent Framework]
            GW[Gateway Layer]
            CONN[Connector Registry]
            MCP_MGR[MCP Server Manager]
            SCHED[Task Scheduler]
            INTEL[Predictive Intelligence]
            INTEGRITY[Integrity System]
            SOC[SOC Bridge]
            GITLOAD[GitLoader]
            OFFICE[Agent Office Manager]
            OPENROUTER[OpenRouter Provider]
            TRUST[Trust Graph Engine]
            CONTEXT[Context Stream Pipeline]
            SUPERPOWERS[Superpowers Ecosystem]
            WORKFLOWS[Workflow Recorder/Executor]
            GITSUITE[Git Analysis Suite]
        end

        IPC[IPC Bridge / Preload]
    end

    USER((User))
    GEMINI[Gemini 2.5 Flash<br/>Native Audio WebSocket]
    CLAUDE[Claude Sonnet/Opus<br/>Anthropic SDK]
    OR_API[OpenRouter API<br/>200+ Models]
    OBSIDIAN[Obsidian Vault<br/>Local Filesystem]
    TELEGRAM[Telegram Bot API]
    GCAL[Google Calendar API]
    GMAIL[Gmail API]
    PERPLEXITY[Perplexity API]
    OPENAI[OpenAI API]
    MCP_SERVERS[MCP Servers<br/>stdio/SSE]
    BROWSER_EXT[Browser Extension<br/>WebSocket :52836]
    PYTHON_SOC[SOC Python Process<br/>stdin/stdout JSONL]
    GITHUB[GitHub Repos<br/>git clone]

    USER -->|Voice/Text/Click| UI
    UI <-->|contextBridge| IPC
    IPC <-->|ipcMain/ipcRenderer| CORE

    CORE <-->|WebSocket| GEMINI
    CORE <-->|REST API| CLAUDE
    OPENROUTER <-->|REST API| OR_API
    MEM <-->|File I/O| OBSIDIAN
    GW <-->|Bot API| TELEGRAM
    CONN <-->|OAuth2| GCAL
    CONN <-->|OAuth2| GMAIL
    CONN <-->|REST| PERPLEXITY
    CONN <-->|REST| OPENAI
    MCP_MGR <-->|stdio/SSE| MCP_SERVERS
    GW <-->|WebSocket| BROWSER_EXT
    SOC <-->|JSONL stdio| PYTHON_SOC
    GITLOAD <-->|git clone + fs| GITHUB

    MIC -->|16kHz PCM| GEMINI
    GEMINI -->|24kHz PCM| AUDIO_OUT
```

### Key External Dependencies

| Service | Protocol | Purpose | Module |
|---------|----------|---------|--------|
| Gemini 2.5 Flash | WebSocket (Native Audio) | Voice conversation, tool calls, real-time reasoning | `useGeminiLive.ts` |
| Claude Sonnet/Opus | REST (Anthropic SDK) | Deep analysis, memory consolidation, psych profiles, code review | `server.ts` |
| OpenRouter | REST API | Access to 200+ models (Llama, Mistral, Gemma, DeepSeek, etc.) | `openrouter.ts` |
| Obsidian | Local filesystem | Bidirectional memory mirroring | `obsidian-memory.ts` |
| Telegram | Bot HTTP API | Message gateway (inbound/outbound) | `gateway/adapters/telegram.ts` |
| Google Calendar | OAuth2 REST | Calendar read/write | `calendar.ts` |
| Gmail | OAuth2 REST | Email read/compose/send | `connectors/` (via registry) |
| Perplexity | REST API | Web search with citations | `connectors/perplexity.ts` |
| OpenAI | REST API | Image generation (DALL-E 3), TTS, GPT fallback | `connectors/openai-services.ts` |
| MCP Servers | stdio/SSE | Extensible tool protocol | `mcp-client.ts` |
| Browser Extension | WebSocket :52836 | Tab control, screenshots, DOM interaction | `browser.ts` |
| SOC (Python) | stdin/stdout JSONL | Desktop + browser automation | `soc-bridge.ts` |
| GitHub | git clone + filesystem | Repo loading + code intelligence | `git-loader.ts` |

---

## 2. Bounded Contexts

```mermaid
graph LR
    subgraph "Voice & Conversation"
        VOICE[Voice I/O]
        SESSION[Session Mgmt]
        IDLE[Idle Behavior]
        MOOD[Mood Tracking]
    end

    subgraph "Memory & Knowledge"
        STM[Short-term Memory]
        MTM[Medium-term Memory]
        LTM[Long-term Memory]
        EPIS[Episodic Memory]
        REL[Relationship Memory]
        CONSOL[Consolidation Engine]
        SEARCH[Semantic Search]
        OBSIDIAN_SYNC[Obsidian Sync]
    end

    subgraph "Intelligence & Agents"
        PREDICT[Predictive Intelligence]
        AGENT_FW[Agent Framework]
        RESEARCH[Research Agent]
        SUMMARIZE[Summarize Agent]
        CODE_REV[Code Review Agent]
        DRAFT[Draft Email Agent]
        OFFICE_MGR[Agent Office]
    end

    subgraph "Safety & Integrity"
        CLAWS[Core Laws]
        HMAC_SYS[HMAC Verification]
        WATCHDOG[Memory Watchdog]
        SAFE_MODE[Safe Mode]
    end

    subgraph "Cryptographic Security"
        VAULT[Sovereign Vault<br/>AES-256-GCM at rest]
        CLAW_ATT[cLaw Attestation<br/>Ed25519 governance proof]
        FILE_XFER[Trusted File Transfer<br/>SHA-256 chunked]
    end

    subgraph "Automation"
        SOC_BRIDGE[SOC Bridge]
        GIT_LOAD[GitLoader]
        DESKTOP_AUTO[Desktop Tools]
    end

    subgraph "Integration & Gateway"
        TELE_GW[Telegram Gateway]
        BROWSER_GW[Browser Gateway]
        CAL_CONN[Calendar Connector]
        MAIL_CONN[Gmail Connector]
        MCP[MCP Protocol]
        OR_CONN[OpenRouter]
    end

    subgraph "Scheduling & Tasks"
        SCHEDULER[Task Scheduler]
        CRON[Cron Engine]
    end

    subgraph "Identity & Personality"
        ONBOARD[Onboarding Flow]
        PSYCH[Psychological Profile]
        PERSONA[Personality System]
        EVOLVE[Personality Evolution]
        CALIB[Personality Calibration]
    end

    subgraph "Trust & People"
        TRUST_ENGINE[Trust Graph Engine]
        COMMITMENTS[Commitment Tracker]
        PERSON_RES[Person Resolution]
    end

    subgraph "Context & Routing"
        CTX_GRAPH[Context Graph]
        CTX_STREAM[Context Stream]
        CTX_ROUTER[Context Tool Router]
        INTEL_ROUTER[Intelligence Router]
    end

    subgraph "Superpowers & Capabilities"
        SP_REGISTRY[Superpowers Registry]
        SP_SANDBOX[Superpower Sandbox]
        SP_STORE[Superpower Store]
        SP_ECO[Ecosystem Manager]
        CAP_MANIFEST[Capability Manifest]
        CAP_GAP[Gap Detector]
    end

    subgraph "Workflows & Git"
        WF_RECORD[Workflow Recorder]
        WF_EXEC[Workflow Executor]
        GIT_SUITE[Git Analysis Suite]
    end

    subgraph "Presentation Layer"
        NEXUS[DesktopViz 3D<br/>13 Structures + Bloom]
        HUD[HudOverlay]
        ORB[VoiceOrb]
        OFFICE_VIS[Agent Office Viz]
        DASH[Dashboard]
        ACTIONS[Action Feed]
        CHAT[Chat History]
        SHIELD[Integrity Shield]
        SP_PANEL[Superpowers Panel]
    end

    VOICE --> SESSION
    SESSION --> IDLE
    SESSION --> MOOD
    VOICE --> STM
    STM --> MTM
    MTM --> LTM
    CONSOL --> LTM
    LTM --> OBSIDIAN_SYNC
    EPIS --> CONSOL
    REL --> PERSONA

    PREDICT --> AGENT_FW
    AGENT_FW --> RESEARCH
    AGENT_FW --> SUMMARIZE
    AGENT_FW --> CODE_REV
    AGENT_FW --> DRAFT
    AGENT_FW --> OFFICE_MGR

    CLAWS --> HMAC_SYS
    HMAC_SYS --> WATCHDOG
    WATCHDOG --> SAFE_MODE
    CLAWS --> PERSONA

    SOC_BRIDGE --> DESKTOP_AUTO
    GIT_LOAD --> AGENT_FW

    TELE_GW --> VOICE
    BROWSER_GW --> DESKTOP_AUTO
    MCP --> AGENT_FW

    SCHEDULER --> CRON
    SCHEDULER --> PREDICT

    ONBOARD --> PSYCH
    PSYCH --> PERSONA
    PERSONA --> EVOLVE
    EVOLVE --> ART_EVOL

    VAULT --> CLAWS
    CLAW_ATT --> CLAWS
    FILE_XFER --> TRUST_ENGINE

    MOOD --> NEXUS
    VOICE --> ORB
    AGENT_FW --> ACTIONS
    AGENT_FW --> OFFICE_VIS
    HMAC_SYS --> SHIELD
```

### Context Ownership Table

| Bounded Context | Owner Module(s) | Data Store | Upstream | Downstream |
|----------------|-----------------|------------|----------|------------|
| Voice & Conversation | `useGeminiLive.ts`, `SessionManager.ts` | In-memory | User mic, Gemini WS | Memory, Mood, UI |
| Short-term Memory | `memory.ts` | In-memory (20 entries) | Conversation | Medium-term promotion |
| Medium-term Memory | `memory.ts` | `friday-data/observations.json` (30 entries) | Short-term | Long-term promotion, Consolidation |
| Long-term Memory | `memory.ts` | `friday-data/memories.json` (unlimited) | Consolidation, direct save | Obsidian sync, Semantic search |
| Episodic Memory | `episodic-memory.ts` | `friday-data/episodes.json` (200 cap) | Session end | Consolidation, Search |
| Relationship Memory | `relationship-memory.ts` | `friday-data/relationship.json` (singleton) | Session events | Personality, Greeting |
| Consolidation | `memory-consolidation.ts` | N/A (transforms) | Medium-term, Episodes | Long-term |
| Semantic Search | `semantic-search.ts` | In-memory embeddings | All memory tiers | Query responses |
| Predictive Intelligence | `intelligence.ts`, `predictor.ts` | `friday-data/intelligence-*.json` | Ambient context, Sentiment | Briefings, Check-ins |
| Agent Framework | `agents/agent-runner.ts` | In-memory task queue | Tool calls, Scheduler | Claude API, Results |
| Agent Office | `agent-office/office-manager.ts` | In-memory state | Agent runner events | Renderer visualization |
| Integrity | `integrity/core-laws.ts`, `integrity/hmac.ts` | Signed law text | Build-time signing | Personality system, Safe mode |
| SOC Bridge | `soc-bridge.ts` | N/A (passthrough) | Tool calls | Python subprocess |
| GitLoader | `git-loader.ts` | Temp clone directory | Tool calls | File content, search results |
| Gateway | `gateway/gateway-manager.ts` | Session store | External messages | Conversation injection |
| Connectors | `connectors/registry.ts`, individual connectors | OAuth tokens in settings | Tool calls | API responses |
| OpenRouter | `openrouter.ts` | API key in settings | Agent framework, tool calls | Model responses |
| MCP | `mcp-client.ts` | Server configs in settings | Tool calls | stdio/SSE servers |
| Scheduler | `scheduler.ts` | `friday-data/scheduled-tasks.json` | User commands, Intelligence | Cron execution |
| Identity | `onboarding.ts`, `personality.ts` | `friday-settings.json` | First-run flow | All personality-aware modules |
| Sovereign Vault | `vault.ts` | `friday-data/vault-meta.json` + encrypted files | Settings, Memory, Trust Graph save paths | All file I/O for sensitive data |
| cLaw Attestation | `claw-attestation.ts` | In-memory (ephemeral) | Core laws, Agent network | Outbound/inbound P2P messages |
| File Transfer | `network/file-transfer.ts` | `friday-data/file-transfers/`, audit JSON | Agent network, Trust Graph | Inbound file storage, audit trail |
| Presentation | React components | React state, MoodContext | All main process data | User display |

---

## 3. Main Process Module Map

```mermaid
graph TD
    INDEX[index.ts<br/>Entry Point & IPC Hub]

    subgraph "Core Services"
        SERVER[server.ts<br/>Claude Anthropic Client]
        SETTINGS[settings.ts<br/>Persistent Settings Store]
        OPENROUTER[openrouter.ts<br/>Multi-Model Provider]
    end

    subgraph "Memory Domain"
        MEMORY[memory.ts<br/>3-Tier Memory Manager]
        EPISODIC[episodic-memory.ts<br/>Session Summarizer]
        RELATIONSHIP[relationship-memory.ts<br/>User Bond Tracker]
        CONSOLIDATION[memory-consolidation.ts<br/>6hr Promotion Engine]
        SEMANTIC[semantic-search.ts<br/>Embedding + Cosine Sim]
        OBSIDIAN[obsidian-memory.ts<br/>Vault Mirroring]
    end

    subgraph "Intelligence Domain"
        AMBIENT[ambient.ts<br/>30s Polling Context]
        SENTIMENT[sentiment.ts<br/>Mood Classification]
        INTEL[intelligence.ts<br/>Briefings & Check-ins]
        PREDICTOR[predictor.ts<br/>Context Prediction]
    end

    subgraph "Agent Domain"
        AGENT_RUNNER[agent-runner.ts<br/>5-Concurrent Executor]
        BUILTIN[builtin-agents.ts<br/>Agent Definitions]
        PERSONAS[agent-personas.ts<br/>Agent Personalities]
        TEAMS[agent-teams.ts<br/>Team Coordination]
        VOICE_AG[agent-voice.ts<br/>ElevenLabs TTS]
        ORCH[orchestrator.ts<br/>Task Decomposition]
        CONTAINER[container-engine.ts<br/>Sandboxed Execution]
        DELEGATION[delegation-engine.ts<br/>Trust-Gated Delegation]
        ORCH_BRIDGE[orchestration-bridge.ts<br/>Orchestrator ↔ Delegation]
        AWARENESS[awareness-mesh.ts<br/>Inter-Agent Broadcasting]
        CAPMAP[capability-map.ts<br/>Intelligent Agent Routing]
        SYMBIONT[symbiont-protocol.ts<br/>Cross-Agent Learning]
        OFFICE_MGR[office-manager.ts<br/>Pixel Office State]
    end

    subgraph "Safety Domain"
        CORE_LAWS[core-laws.ts<br/>Three Laws Text]
        HMAC[hmac.ts<br/>SHA256 Signing]
        WATCHDOG[memory-watchdog.ts<br/>Integrity Monitor]
    end

    subgraph "Cryptographic Security Domain"
        VAULT_MOD[vault.ts<br/>Sovereign Vault AES-256-GCM]
        CLAW_ATT_MOD[claw-attestation.ts<br/>Governance Verification]
        FILE_XFER_MOD[network/file-transfer.ts<br/>Trust-Gated Transfer]
    end

    subgraph "Automation Domain"
        SOC[soc-bridge.ts<br/>Python JSONL Bridge]
        GITLOADER[git-loader.ts<br/>Repo Clone + Search]
        DESKTOP[desktop-tools.ts<br/>Window/App/Clipboard]
    end

    subgraph "Integration Domain"
        GW_MGR[gateway-manager.ts<br/>Message Gateway]
        TRUST[trust-engine.ts<br/>5-Tier Trust]
        AUDIT[audit-log.ts<br/>Crypto Audit Trail]
        BROWSER[browser.ts<br/>WS :52836 Bridge]
        MCP_CLIENT[mcp-client.ts<br/>Multi-Server Protocol]
        CONN_REG[registry.ts<br/>18 Connector Modules]
    end

    subgraph "Scheduling Domain"
        SCHEDULER[scheduler.ts<br/>Persistent Cron]
    end

    subgraph "Identity Domain"
        PERSONALITY[personality.ts<br/>System Prompt Builder]
        ONBOARDING[onboarding.ts<br/>First-Run Flow]
        PSYCH[psychological-profile.ts<br/>Claude Psych Analysis]
        FEATURE_SETUP[feature-setup.ts<br/>9-Step Guided Setup]
        EVOLUTION[personality-evolution.ts<br/>Visual Trait Mapping]
        ART_EVOL[art-evolution.ts<br/>Weekly Gemini Art Therapy]
    end

    subgraph "Infrastructure"
        PRELOAD[preload.ts<br/>IPC Bridge / contextBridge]
        PROMPT_BUDGET[prompt-budget.ts<br/>Token Allocation]
        SESSION_HEALTH[session-health.ts<br/>Uptime & Error Tracking]
        SCREEN[screen-capture.ts<br/>Desktop Screenshots]
        CLIPBOARD[clipboard-intelligence.ts<br/>Polling Clipboard]
    end

    INDEX --> SERVER
    INDEX --> SETTINGS
    INDEX --> MEMORY
    INDEX --> EPISODIC
    INDEX --> RELATIONSHIP
    INDEX --> CONSOLIDATION
    INDEX --> SEMANTIC
    INDEX --> OBSIDIAN
    INDEX --> AMBIENT
    INDEX --> SENTIMENT
    INDEX --> INTEL
    INDEX --> AGENT_RUNNER
    INDEX --> GW_MGR
    INDEX --> BROWSER
    INDEX --> DESKTOP
    INDEX --> MCP_CLIENT
    INDEX --> SCHEDULER
    INDEX --> PERSONALITY
    INDEX --> ONBOARDING
    INDEX --> CLIPBOARD
    INDEX --> SESSION_HEALTH
    INDEX --> PRELOAD
    INDEX --> SOC
    INDEX --> GITLOADER
    INDEX --> CORE_LAWS
    INDEX --> OFFICE_MGR
    INDEX --> OPENROUTER
    INDEX --> VAULT_MOD
    INDEX --> FILE_XFER_MOD

    MEMORY --> SEMANTIC
    MEMORY --> OBSIDIAN
    CONSOLIDATION --> SERVER
    CONSOLIDATION --> MEMORY
    EPISODIC --> SERVER
    INTEL --> SERVER
    INTEL --> AMBIENT
    INTEL --> SENTIMENT
    AGENT_RUNNER --> SERVER
    AGENT_RUNNER --> BUILTIN
    AGENT_RUNNER --> PERSONAS
    AGENT_RUNNER --> OFFICE_MGR
    ONBOARDING --> PSYCH
    PSYCH --> SERVER
    PERSONALITY --> SETTINGS
    PERSONALITY --> RELATIONSHIP
    PERSONALITY --> CORE_LAWS
    EVOLUTION --> SETTINGS
    CORE_LAWS --> HMAC
    HMAC --> WATCHDOG
    GW_MGR --> TRUST
    GW_MGR --> AUDIT
    VAULT_MOD --> SETTINGS
    CLAW_ATT_MOD --> CORE_LAWS
```

### Module Coupling Analysis

| Module | Fan-In | Fan-Out | Coupling Level | Notes |
|--------|--------|---------|----------------|-------|
| `index.ts` | 1 (electron) | 35+ | **Hub** | Central IPC registration — expected for Electron main |
| `settings.ts` | 25+ | 0 | **Afferent hub** | Read by nearly everything, writes from few |
| `server.ts` | 10 | 1 (Anthropic SDK) | **Shared service** | Claude API client used by consolidation, episodic, predictive, agents, psych, integrity |
| `memory.ts` | 5 | 3 | **Domain core** | Semantic search, Obsidian sync, settings |
| `agent-runner.ts` | 3 | 7 | **Orchestrator** | Routes to agent types, uses Claude, manages office state |
| `preload.ts` | 1 | 0 | **Bridge** | Pure declaration, no logic dependencies |
| `integrity/core-laws.ts` | 3 | 1 | **Safety core** | Read by personality, checked by HMAC |
| `soc-bridge.ts` | 2 | 1 | **Bridge** | Spawns Python, translates JSONL |
| `openrouter.ts` | 3 | 1 | **Service** | REST client for OpenRouter API |
| `vault.ts` | 10+ | 2 | **Afferent hub** | Read/write by all persistent stores; depends on node-machine-id, crypto |
| `claw-attestation.ts` | 1 | 2 | **Safety core** | Used by agent-network.ts; depends on core-laws, crypto |
| `network/file-transfer.ts` | 1 | 3 | **Domain core** | Used by index.ts IPC; depends on vault, crypto, fs |

---

## 4. Renderer Component Hierarchy

```mermaid
graph TD
    APP[App.tsx<br/>State Machine: AppPhase]

    subgraph "Global Wrappers"
        EB[ErrorBoundary.tsx<br/>Crash Recovery]
        MOOD_CTX[MoodContext.tsx<br/>Mood State Provider]
    end

    subgraph "Phase: Gate"
        WELCOME[WelcomeGate.tsx<br/>API Key Entry]
    end

    subgraph "Phase: Onboarding/Creating"
        AGENT_CREATE[AgentCreation.tsx<br/>Cinematic Reveal]
    end

    subgraph "Phase: Normal — Always Visible"
        DESKTOP_VIZ[DesktopViz.tsx<br/>Three.js 13-Structure Holographic]
        HUD_OVERLAY[HudOverlay.tsx<br/>Holographic HUD Overlay]
        NEXUS[FridayCore.tsx<br/>Legacy 3D Viz]
        WIRE[WireframeNetwork.tsx<br/>Canvas 2D Primary BG]
        PARTICLE[ParticleBackground.tsx<br/>Canvas 2D Fallback BG]
        ORB[VoiceOrb.tsx<br/>Central Interaction Point]
        STATUS[StatusBar.tsx<br/>Bottom Status Strip]
        ACTION[ActionFeed.tsx<br/>Tool/Agent Activity Ticker]
        SHIELD[IntegrityShield.tsx<br/>cLaw Status Indicator]
    end

    subgraph "Phase: Normal — Overlays (Toggle)"
        CHAT[ChatHistory.tsx<br/>Conversation Log]
        TEXT_IN[TextInput.tsx<br/>Text Message Entry]
        SETTINGS[Settings.tsx<br/>Configuration Panel]
        DASH[Dashboard.tsx<br/>Command Center]
        AGENT_DASH[AgentDashboard.tsx<br/>Agent Task Monitor]
        OFFICE_VIZ[AgentOffice/<br/>Pixel-Art Office]
        MEM_EXP[MemoryExplorer.tsx<br/>Memory Browser]
        QUICK[QuickActions.tsx<br/>Command Palette]
        RESEARCH[ResearchPanel.tsx<br/>Research Report Sidebar]
        CONN_ERR[ConnectionOverlay.tsx<br/>Error Recovery]
        FILE_TOAST[FileToast.tsx<br/>File Notifications]
    end

    subgraph "Dashboard Sub-Components"
        CTX_CARD[ContextCard.tsx<br/>Live Ambient Context]
        AGENT_CARD[AgentCard.tsx<br/>Agent Summary]
        MOOD_TL[MoodTimeline.tsx<br/>SVG Mood Chart]
    end

    APP --> EB
    EB --> MOOD_CTX

    MOOD_CTX --> WELCOME
    MOOD_CTX --> AGENT_CREATE
    MOOD_CTX --> NEXUS
    MOOD_CTX --> WIRE
    MOOD_CTX --> PARTICLE
    MOOD_CTX --> ORB
    MOOD_CTX --> STATUS
    MOOD_CTX --> ACTION
    MOOD_CTX --> SHIELD
    MOOD_CTX --> CHAT
    MOOD_CTX --> TEXT_IN
    MOOD_CTX --> SETTINGS
    MOOD_CTX --> DASH
    MOOD_CTX --> AGENT_DASH
    MOOD_CTX --> OFFICE_VIZ
    MOOD_CTX --> MEM_EXP
    MOOD_CTX --> QUICK
    MOOD_CTX --> RESEARCH
    MOOD_CTX --> CONN_ERR
    MOOD_CTX --> FILE_TOAST

    DASH --> CTX_CARD
    DASH --> AGENT_CARD
    DASH --> MOOD_TL
```

### Component State Machine (App.tsx)

```mermaid
stateDiagram-v2
    [*] --> checking: App mounts
    checking --> gate: No API keys
    checking --> onboarding: Keys exist, not onboarded
    checking --> normal: Fully configured

    gate --> onboarding: Keys entered → connect
    onboarding --> customizing: Intake complete → transition_to_customization
    customizing --> creating: finalize_agent_identity called
    creating --> feature_setup: Animation complete → FridayCore revealed
    feature_setup --> normal: All 9 steps done/skipped

    normal --> normal: Reconnect cycles
```

### Z-Index Layer Stack

| Z-Index | Component | Visibility |
|---------|-----------|------------|
| 200 | WelcomeGate | Gate phase only |
| 120 | QuickActions | Toggle (Ctrl+K) |
| 110 | Dashboard | Toggle (Ctrl+Shift+D) |
| 105 | AgentDashboard | Toggle |
| 100 | Settings | Toggle |
| 95 | AgentOffice | Toggle |
| 90 | MemoryExplorer | Toggle (Ctrl+Shift+M) |
| 85 | ResearchPanel | Toggle |
| 50 | AgentCreation | Creating phase only |
| 40 | ChatHistory | Toggle |
| 38 | FileToast | Transient notifications |
| 35 | ActionFeed | Always (bottom-left) |
| 32 | IntegrityShield | Always (status indicator) |
| 30 | ConnectionOverlay | Error state |
| 20 | VoiceOrb | Always (center) |
| 10 | StatusBar | Always (bottom) |
| 5 | FridayCore / WireframeNetwork | Always (background) |

---

## 5. IPC Bridge Contract

```mermaid
graph LR
    subgraph "Renderer (window.eve.*)"
        R_SETTINGS[settings.*]
        R_MEMORY[memory.*]
        R_EPISODES[episodes.*]
        R_RELATIONSHIP[relationship.*]
        R_AGENTS[agents.*]
        R_AMBIENT[ambient.*]
        R_SENTIMENT[sentiment.*]
        R_INTEL[intelligence.*]
        R_DOCS[documents.*]
        R_PROJECTS[projects.*]
        R_DESKTOP[desktop.*]
        R_SCHEDULER[scheduler.*]
        R_MCP[mcp.*]
        R_SESSION[sessionHealth.*]
        R_PSYCH[psychProfile.*]
        R_FEATURE[featureSetup.*]
        R_ONBOARD[onboarding.*]
        R_CLIPBOARD[clipboard.*]
        R_INTEGRITY[integrity.*]
        R_SOC[soc.*]
        R_GITLOADER[gitLoader.*]
        R_OFFICE[agentOffice.*]
        R_OPENROUTER[openrouter.*]
    end

    subgraph "Main Process (ipcMain.handle)"
        M_SETTINGS[settings:*]
        M_MEMORY[memory:*]
        M_EPISODES[episodes:*]
        M_RELATIONSHIP[relationship:*]
        M_AGENTS[agents:*]
        M_AMBIENT[ambient:*]
        M_SENTIMENT[sentiment:*]
        M_INTEL[intelligence:*]
        M_DOCS[documents:*]
        M_PROJECTS[projects:*]
        M_DESKTOP[desktop:*]
        M_SCHEDULER[scheduler:*]
        M_MCP[mcp:*]
        M_SESSION[session-health:*]
        M_PSYCH[psych:*]
        M_FEATURE[feature-setup:*]
        M_ONBOARD[onboarding:*]
        M_CLIPBOARD[clipboard:*]
        M_INTEGRITY[integrity:*]
        M_SOC[soc:*]
        M_GITLOADER[git-loader:*]
        M_OFFICE[agent-office:*]
        M_OPENROUTER[openrouter:*]
    end

    R_SETTINGS <-->|contextBridge| M_SETTINGS
    R_MEMORY <-->|contextBridge| M_MEMORY
    R_EPISODES <-->|contextBridge| M_EPISODES
    R_RELATIONSHIP <-->|contextBridge| M_RELATIONSHIP
    R_AGENTS <-->|contextBridge| M_AGENTS
    R_AMBIENT <-->|contextBridge| M_AMBIENT
    R_SENTIMENT <-->|contextBridge| M_SENTIMENT
    R_INTEL <-->|contextBridge| M_INTEL
    R_DOCS <-->|contextBridge| M_DOCS
    R_PROJECTS <-->|contextBridge| M_PROJECTS
    R_DESKTOP <-->|contextBridge| M_DESKTOP
    R_SCHEDULER <-->|contextBridge| M_SCHEDULER
    R_MCP <-->|contextBridge| M_MCP
    R_SESSION <-->|contextBridge| M_SESSION
    R_PSYCH <-->|contextBridge| M_PSYCH
    R_FEATURE <-->|contextBridge| M_FEATURE
    R_ONBOARD <-->|contextBridge| M_ONBOARD
    R_CLIPBOARD <-->|contextBridge| M_CLIPBOARD
    R_INTEGRITY <-->|contextBridge| M_INTEGRITY
    R_SOC <-->|contextBridge| M_SOC
    R_GITLOADER <-->|contextBridge| M_GITLOADER
    R_OFFICE <-->|contextBridge| M_OFFICE
    R_OPENROUTER <-->|contextBridge| M_OPENROUTER
```

### Full IPC Channel Registry

#### Settings Namespace (`window.eve.settings`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `settings:get` | invoke | `() → FridaySettings` | Read all settings |
| `settings:set` | invoke | `(partial: Partial<FridaySettings>) → void` | Update settings |
| `settings:get-api-key` | invoke | `(service: string) → string` | Read API key |
| `settings:set-api-key` | invoke | `(service: string, key: string) → void` | Store API key |
| `settings:get-agent-config` | invoke | `() → AgentConfig` | Read agent identity |
| `settings:set-agent-config` | invoke | `(config: AgentConfig) → void` | Update agent identity |

#### Memory Namespace (`window.eve.memory`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `memory:get-all` | invoke | `() → MemoryEntry[]` | All long-term memories |
| `memory:save` | invoke | `(entry: MemoryInput) → void` | Save to long-term |
| `memory:search` | invoke | `(query: string) → SearchResult[]` | Semantic search |
| `memory:get-observations` | invoke | `() → Observation[]` | All medium-term observations |
| `memory:get-short-term` | invoke | `() → ShortTermEntry[]` | Current session buffer |
| `memory:consolidate` | invoke | `() → ConsolidationResult` | Trigger manual consolidation |

#### Agents Namespace (`window.eve.agents`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `agents:list-tasks` | invoke | `() → AgentTask[]` | All tasks (any status) |
| `agents:cancel` | invoke | `(taskId: string) → void` | Cancel running task |
| `agents:get-types` | invoke | `() → AgentTypeInfo[]` | Available agent types |
| `agents:onUpdate` | on | `(callback: (task: AgentTask) → void) → unsub` | Real-time task updates |

#### Integrity Namespace (`window.eve.integrity`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `integrity:verify` | invoke | `() → IntegrityStatus` | Verify law integrity |
| `integrity:get-status` | invoke | `() → IntegrityState` | Current integrity state |
| `integrity:get-laws` | invoke | `() → CoreLaw[]` | Read Three Laws text |

#### SOC Namespace (`window.eve.soc`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `soc:execute` | invoke | `(action: SOCAction) → SOCResult` | Execute desktop/browser automation |
| `soc:is-available` | invoke | `() → boolean` | Check if Python SOC is available |

#### GitLoader Namespace (`window.eve.gitLoader`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `git-loader:clone` | invoke | `(url: string) → CloneResult` | Clone a GitHub repo |
| `git-loader:search` | invoke | `(repoId: string, pattern: string) → SearchResult[]` | Regex search repo files |
| `git-loader:read-file` | invoke | `(repoId: string, path: string) → string` | Read a file from cloned repo |
| `git-loader:cleanup` | invoke | `(repoId: string) → void` | Remove cloned repo |

#### Agent Office Namespace (`window.eve.agentOffice`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `agent-office:get-state` | invoke | `() → OfficeState` | Current office layout + agent positions |
| `agent-office:onUpdate` | on | `(callback: (state: OfficeState) → void) → unsub` | Real-time office updates |

---

## 6. Data Flow: Audio Pipeline

```mermaid
sequenceDiagram
    participant User as User (Microphone)
    participant Worklet as AudioWorklet<br/>mic-processor
    participant Hook as useGeminiLive<br/>(Renderer)
    participant WS as Gemini WebSocket<br/>(Main Process)
    participant Gemini as Gemini 2.5 Flash
    participant Engine as AudioPlaybackEngine
    participant Speaker as Speaker Output

    Note over User, Speaker: === CAPTURE PATH (16kHz) ===
    User ->> Worklet: Raw audio (device sample rate)
    Worklet ->> Worklet: Downsample to 16kHz
    Worklet ->> Hook: Float32 PCM chunks (port.onmessage)
    Hook ->> Hook: Float32 → Int16 → Base64
    Hook ->> WS: realtimeInput { audio: base64 }
    WS ->> Gemini: WebSocket frame

    Note over User, Speaker: === RESPONSE PATH (24kHz) ===
    Gemini ->> WS: serverContent { audioParts }
    WS ->> Hook: IPC 'gemini-audio' { base64 }
    Hook ->> Hook: Base64 → Int16 → Float32
    Hook ->> Engine: scheduleChunk(float32Array)

    Note over Engine, Speaker: === GAPLESS PLAYBACK ===
    Engine ->> Engine: Create AudioBufferSourceNode
    Engine ->> Engine: source.start(nextStartTime)
    Engine ->> Engine: nextStartTime += buffer.duration
    Engine ->> Speaker: Precise sample-boundary scheduling
    Engine ->> Engine: On ended → cleanup node

    Note over User, Speaker: === INTERRUPTION ===
    User ->> Worklet: Speech detected while playing
    Hook ->> Engine: stop() → clear all scheduled
    Hook ->> WS: Cancel current response
    Engine ->> Engine: Reset nextStartTime = 0
```

### Audio Format Summary

| Stage | Sample Rate | Bit Depth | Encoding | Buffer Size |
|-------|------------|-----------|----------|-------------|
| Mic capture | Device native | Float32 | PCM | 4096 samples |
| Mic → Gemini | 16,000 Hz | Int16 | Base64 | Variable |
| Gemini → Playback | 24,000 Hz | Int16 | Base64 | Variable |
| Playback scheduling | 24,000 Hz | Float32 | PCM | Per-chunk |

---

## 7. Data Flow: Memory Lifecycle

```mermaid
graph TD
    CONV[Conversation Turn] -->|save_memory tool| STM[Short-Term Buffer<br/>20 entries max<br/>In-memory only]

    STM -->|Session-aware<br/>reinforcement| MTM[Medium-Term Observations<br/>30 entries max<br/>observations.json]

    MTM -->|6-hour consolidation<br/>cycle| CONSOL{Consolidation Engine<br/>Claude Sonnet Analysis}

    CONSOL -->|Weighted scoring:<br/>frequency × 0.3 +<br/>recency × 0.2 +<br/>importance × 0.3 +<br/>cross-ref × 0.2| PROMOTE{Score > threshold?}

    PROMOTE -->|Yes| LTM[Long-Term Memory<br/>Unlimited<br/>memories.json]
    PROMOTE -->|No| DECAY[Stay in MTM<br/>30-day TTL decay]

    CONSOL -->|Claude merges<br/>related facts| MERGE[Merged Memories<br/>Deduplicated + enriched]
    MERGE --> LTM

    LTM -->|On save| EMBED[Semantic Search<br/>Gemini text-embedding-004<br/>768 dimensions]
    LTM -->|If Obsidian configured| OBS[Obsidian Vault<br/>Markdown files<br/>Categorized folders]

    EPIS_SAVE[Session End] -->|Claude Sonnet<br/>summarizes turns| EPIS[Episodic Memory<br/>200 episodes max<br/>episodes.json]
    EPIS --> CONSOL

    REL_UPDATE[Session Events] --> REL[Relationship Memory<br/>Singleton<br/>relationship.json]
    REL -->|Trust: log(sessions)/log(50)<br/>Streak: consecutive days<br/>Inside jokes: extracted| PERSONA[Personality System]

    style STM fill:#0ff,color:#000
    style MTM fill:#818cf8,color:#fff
    style LTM fill:#22c55e,color:#000
    style EPIS fill:#f59e0b,color:#000
    style REL fill:#ec4899,color:#000
```

### Memory Promotion Scoring Formula

```
score = (frequency × 0.3) + (recency × 0.2) + (importance × 0.3) + (crossReference × 0.2)

where:
  frequency   = occurrences / maxOccurrences across all observations
  recency     = 1 - (daysSinceLastSeen / 30)
  importance  = Claude-rated 0-1 during initial save
  crossRef    = number of related memories / total memories (capped at 1)
```

---

## 8. Data Flow: Tool Execution Chain

```mermaid
graph TD
    GEMINI[Gemini Response<br/>toolCall: name + args] --> HOOK[useGeminiLive.ts<br/>handleToolCall]

    HOOK --> PRIORITY{Tool Routing<br/>Priority Chain}

    PRIORITY -->|1. Specific handlers| SPECIFIC[Built-in Tool Handlers]
    PRIORITY -->|2. browser_* prefix| BROWSER[Browser Connector]
    PRIORITY -->|3. soc_* prefix| SOC[SOC Bridge]
    PRIORITY -->|4. git_* prefix| GITLOAD[GitLoader]
    PRIORITY -->|5. Connector match| CONNECTOR[Connector Registry]
    PRIORITY -->|6. MCP match| MCP_TOOL[MCP Server Tools]
    PRIORITY -->|7. Fallback| DESKTOP[Desktop Tools]

    subgraph "Built-in Tool Handlers"
        SPECIFIC --> SAVE_MEM[save_memory]
        SPECIFIC --> ASK_CLAUDE[ask_claude]
        SPECIFIC --> SETUP_INTEL[setup_intelligence]
        SPECIFIC --> CREATE_TASK[create_task / list_tasks / delete_task]
        SPECIFIC --> READ_SRC[read_own_source / list_own_files]
        SPECIFIC --> PROPOSE[propose_code_change]
        SPECIFIC --> LAUNCH[launch_app]
        SPECIFIC --> FINALIZE[finalize_agent_identity]
        SPECIFIC --> INTAKE[save_intake_responses]
        SPECIFIC --> FEATURE[mark_feature_setup_step]
    end

    subgraph "SOC Bridge Tools"
        SOC --> SOC_CLICK[soc_click]
        SOC --> SOC_TYPE[soc_type]
        SOC --> SOC_SCREENSHOT[soc_screenshot]
        SOC --> SOC_NAVIGATE[soc_navigate]
        SOC --> SOC_SCROLL[soc_scroll]
    end

    subgraph "GitLoader Tools"
        GITLOAD --> GIT_CLONE[git_clone_repo]
        GITLOAD --> GIT_SEARCH[git_search_files]
        GITLOAD --> GIT_READ[git_read_file]
        GITLOAD --> GIT_LIST[git_list_directory]
    end

    subgraph "Browser Tools (browser_* prefix)"
        BROWSER --> NAV[browser_navigate]
        BROWSER --> SCREENSHOT[browser_screenshot]
        BROWSER --> CLICK[browser_click]
        BROWSER --> TYPE[browser_type]
    end

    subgraph "Connector Tools"
        CONNECTOR --> GCAL_T[google-calendar:*]
        CONNECTOR --> GMAIL_T[gmail:*]
        CONNECTOR --> PERP_T[perplexity:*]
        CONNECTOR --> OPENAI_T[openai:*]
        CONNECTOR --> OR_T[openrouter:*]
    end

    subgraph "MCP Tools (namespaced)"
        MCP_TOOL --> MCP1[serverName::toolName]
    end

    subgraph "Desktop Fallback"
        DESKTOP --> WIN[get_active_window]
        DESKTOP --> LIST_WIN[list_windows]
        DESKTOP --> CMD[run_command]
    end
```

### Tool Declaration Sources

| Source | Count | Registration | Namespace |
|--------|-------|-------------|-----------|
| Built-in tools | ~15 | `desktop-tools.ts` | None (flat) |
| Onboarding tools | ~5 | `onboarding.ts` | None (flat) |
| SOC bridge | ~8 | `soc-bridge.ts` | `soc_` prefix |
| GitLoader | ~5 | `git-loader.ts` | `git_` prefix |
| Browser connector | 4 | `browser.ts` | `browser_` prefix |
| Desktop connector | 5 | `desktop-tools.ts` | None (flat) |
| Google Calendar | 4 | `calendar.ts` | `google-calendar:` |
| Gmail | 5 | `connectors/` via registry | `gmail:` |
| Perplexity | 1 | `connectors/perplexity.ts` | `perplexity:` |
| OpenAI services | 3 | `connectors/openai-services.ts` | `openai:` |
| OpenRouter | 2 | `openrouter.ts` | `openrouter:` |
| MCP servers | Dynamic | `mcp-client.ts` | `serverName::` |

**Total: 55+ tools** from 12+ sources, dynamically composed per session.

---

## 9. Data Flow: Session Management

```mermaid
stateDiagram-v2
    [*] --> Disconnected: App launch

    Disconnected --> Connecting: connectToGemini()
    Connecting --> Connected: WebSocket open + setup complete
    Connecting --> Error: Connection failed

    Error --> Retrying: Auto-retry (max 3)
    Retrying --> Connecting: Retry attempt
    Retrying --> Failed: Retries exhausted
    Failed --> Connecting: Manual retry / orb click

    Connected --> Listening: VAD detects speech
    Connected --> Idle: No activity

    Listening --> Processing: User stops speaking
    Processing --> Speaking: Gemini responds with audio
    Speaking --> Connected: Response complete
    Speaking --> Listening: User interrupts (barge-in)

    Connected --> SoftIdle: 12s no interaction
    SoftIdle --> ContextIdle: 45s no interaction
    ContextIdle --> QuietIdle: 2min no interaction

    SoftIdle --> Connected: User speaks/types
    ContextIdle --> Connected: User speaks/types
    QuietIdle --> Connected: User speaks/types

    Connected --> Reconnecting: 5.5min session timer
    Reconnecting --> Connecting: New WebSocket
    Reconnecting --> Error: Reconnect failed

    Note right of Reconnecting: Mic stream survives<br/>reconnection. Session<br/>timeout at 7min,<br/>reconnect triggers at 5.5min.

    Connected --> SessionEnd: User disconnects / app close
    SessionEnd --> [*]: Episode saved, relationship updated
```

### Session Timing Constants

| Timer | Duration | Trigger | Action |
|-------|----------|---------|--------|
| Soft idle | 12 seconds | No user input | Subtle cue (orb pulse) |
| Context idle | 45 seconds | No user input | Context-aware check-in |
| Quiet idle | 2 minutes | No user input | Quiet companionship mode |
| Session timeout | 7 minutes | Gemini WebSocket TTL | Connection drops |
| Reconnect trigger | 5.5 minutes | Pre-emptive | New WebSocket, mic preserved |
| Consolidation | 6 hours | Timer | Memory promotion cycle |
| Ambient poll | 30 seconds | Timer | Desktop context refresh |
| Sentiment poll | 30 seconds | Timer | Mood classification |
| Intelligence poll | 30 seconds | Timer | Predictive check |

---

## 10. Data Flow: Agent Task Pipeline

```mermaid
sequenceDiagram
    participant User as User / Scheduler
    participant Gemini as Gemini (Voice)
    participant Hook as useGeminiLive
    participant Runner as AgentRunner
    participant Queue as Task Queue
    participant Claude as Claude Sonnet
    participant Office as Agent Office
    participant Result as Result Handler

    User ->> Gemini: "Research quantum computing"
    Gemini ->> Hook: toolCall: create_agent_task<br/>{type: 'research', input: '...'}
    Hook ->> Runner: createTask(type, input)
    Runner ->> Queue: Enqueue {id, type, status: 'queued'}
    Runner ->> Office: Update agent sprite → 'working'
    Runner -->> Hook: taskId

    Note over Queue: Concurrency limit: 5 parallel tasks

    Queue ->> Runner: Dequeue next task
    Runner ->> Runner: Load agent definition + persona
    Runner ->> Runner: Build agent-specific prompt
    Runner ->> Claude: messages.create({<br/>  model: 'claude-sonnet-4-20250514',<br/>  system: agentPrompt + persona,<br/>  messages: [{role: 'user', content: input}]<br/>})

    loop Chain-of-thought streaming
        Claude -->> Runner: Content delta
        Runner -->> Queue: Update progress, logs
        Runner -->> Office: Sprite animation: 'thinking'
        Runner -->> Hook: IPC event: agent-task-update
        Hook -->> User: ActionFeed card updates
    end

    Claude -->> Runner: Final response
    Runner ->> Queue: status: 'completed', result: '...'
    Runner ->> Office: Sprite → 'idle'
    Runner -->> Hook: IPC event: agent-task-update
    Hook -->> Gemini: Inject result into conversation
    Gemini -->> User: "Here's what I found about quantum computing..."
```

### Agent Type Definitions

| Agent Type | Model | Max Tokens | Temperature | Special Capabilities |
|-----------|-------|------------|-------------|---------------------|
| `research` | Claude Sonnet | 4096 | 0.3 | Multi-step web search via Perplexity, source citations |
| `summarize` | Claude Sonnet | 2048 | 0.2 | Document ingestion, key-point extraction |
| `code-review` | Claude Sonnet | 4096 | 0.1 | File reading, diff analysis, security scanning |
| `draft-email` | Claude Sonnet | 2048 | 0.4 | Tone matching, recipient context, Gmail integration |
| `orchestrate` | Claude Sonnet | 4096 | 0.3 | Task decomposition, multi-agent coordination |

---

## 11. Swim Lane: User Interaction Cycle

```mermaid
sequenceDiagram
    participant User
    participant VoiceOrb as VoiceOrb<br/>(Renderer)
    participant Hook as useGeminiLive<br/>(Renderer)
    participant Gemini as Gemini WS<br/>(Main)
    participant Tools as Tool System<br/>(Main)
    participant Memory as Memory<br/>(Main)
    participant UI as UI Components<br/>(Renderer)

    Note over User, UI: === VOICE INTERACTION ===

    User ->> VoiceOrb: Speaks (VAD triggers)
    VoiceOrb ->> VoiceOrb: Ripple animation, glow boost
    VoiceOrb ->> Hook: Audio chunks (16kHz PCM)
    Hook ->> Gemini: realtimeInput { audio }

    Gemini ->> Hook: modelTurn { text + audio }
    Hook ->> UI: Transcript updates
    Hook ->> VoiceOrb: Speaking state → pulsate
    Hook ->> User: Audio playback (24kHz)

    Note over User, UI: === TOOL CALL ===

    Gemini ->> Hook: toolCall { name, args }
    Hook ->> UI: ActionFeed → new card (running)
    Hook ->> Tools: Route to handler
    Tools ->> Tools: Execute
    Tools -->> Hook: Result
    Hook ->> UI: ActionFeed → card (success/error)
    Hook ->> Gemini: toolResponse { result }
    Gemini ->> Hook: Continue response with result context

    Note over User, UI: === MEMORY SAVE ===

    Gemini ->> Hook: toolCall: save_memory
    Hook ->> Memory: save(content, category, importance)
    Memory ->> Memory: Add to short-term buffer
    Memory ->> Memory: Semantic embedding (Gemini)
    Memory ->> Memory: Obsidian sync (if configured)
    Memory -->> Hook: Saved
    Hook ->> UI: ActionFeed → "Saving to memory ✓"

    Note over User, UI: === SESSION END ===

    User ->> VoiceOrb: Disconnect (click)
    Hook ->> Hook: Collect all turns
    Hook ->> Memory: Save episode (Claude summary)
    Hook ->> Memory: Update relationship stats
    Hook ->> UI: All overlays reset
```

---

## 12. Swim Lane: First-Run Experience

```mermaid
sequenceDiagram
    participant User
    participant Gate as WelcomeGate
    participant App as App.tsx
    participant Setup as Setup Voice<br/>(Charon)
    participant Gemini as Gemini WS
    participant Claude as Claude Sonnet
    participant Integrity as Integrity System
    participant Creation as AgentCreation
    participant Friday as FridayCore
    participant Agent as New Agent

    Note over User, Agent: === PHASE 0: INTEGRITY CHECK ===
    App ->> Integrity: Verify core law signatures
    Integrity -->> App: HMAC verified ✓

    Note over User, Agent: === PHASE 1: API KEY GATE ===

    User ->> Gate: Opens app for first time
    Gate ->> User: "Enter Gemini API key"
    User ->> Gate: Enters Gemini key
    Gate ->> User: "Enter Anthropic API key"
    User ->> Gate: Enters Anthropic key
    Gate ->> App: onKeysReady()
    App ->> App: setAppPhase('onboarding')
    App ->> Gemini: Connect with Charon voice

    Note over User, Agent: === PHASE 2: "HER" INTAKE ===

    Setup ->> User: "Would you like your agent to have a male voice, a female voice, or neither?"
    User ->> Setup: Answers
    Setup ->> User: "How would you describe yourself in social situations?"
    User ->> Setup: Answers
    Setup ->> User: "How would you describe your relationship with your mother?"
    User ->> Setup: Answers
    Setup ->> User: "Thank you. Please wait a moment."

    Note over User, Agent: === PHASE 3: PSYCHOLOGICAL PROFILE ===

    Setup ->> Claude: save_intake_responses → psych:generate
    Claude ->> Claude: Analyze: voice pref → gender comfort<br/>Social → extroversion, self-awareness<br/>Mother → attachment style, trust patterns
    Claude -->> App: PsychologicalProfile saved

    Note over User, Agent: === PHASE 4: USER CUSTOMIZATION ===

    Setup ->> App: transition_to_customization tool
    App ->> App: setAppPhase('customizing')
    Setup ->> User: "What would you like to name your agent?"
    User ->> Setup: Names agent
    Setup ->> User: Voice options, personality, backstory
    User ->> Setup: Customizes everything
    Setup ->> App: finalize_agent_identity

    Note over User, Agent: === PHASE 5: CINEMATIC REVEAL ===

    App ->> App: setAppPhase('creating')
    App ->> Creation: Begin animation
    Creation ->> Creation: 0-2s: Pulsing orb, "Initializing..."
    Creation ->> Creation: 2-3s: Warm golden glow fills screen
    Creation ->> App: onNexusReveal() at 3s
    App ->> Friday: Opacity 0 → 1 (2s transition)
    Creation ->> Creation: 4.5-6s: Overlay fades out
    Creation ->> App: onComplete() at 6.5s

    Note over User, Agent: === PHASE 6: FIRST GREETING ===

    App ->> App: setAppPhase('feature-setup')
    App ->> Gemini: Reconnect with agent's real voice
    Agent ->> User: Psychologically-tuned first words<br/>(calibrated to attachment style)

    Note over User, Agent: === PHASE 7: FEATURE WALKTHROUGH ===

    Agent ->> User: "Let me show you what I can do..."
    loop 9 Feature Steps
        Agent ->> User: Explain feature + guide setup
        User ->> Agent: Complete or "skip"
        Agent ->> App: mark_feature_setup_step
    end

    App ->> App: setAppPhase('normal')
    Agent ->> User: "We're all set. I'm here whenever you need me."
```

---

## 13. Dependency Graph

### NPM Package Dependencies

```mermaid
graph TD
    subgraph "AI / ML"
        ANTHROPIC["@anthropic-ai/sdk 0.39"]
        GOOGLE_AI["@google/generative-ai 0.21"]
        OPENAI_SDK["openai 6.25"]
        GPT_TOK["gpt-tokenizer 3.4"]
    end

    subgraph "Desktop Platform"
        ELECTRON["electron 33"]
        BUILDER["electron-builder 25"]
    end

    subgraph "3D / Visual"
        THREE["three 0.183"]
    end

    subgraph "Frontend"
        REACT["react 19"]
        REACT_DOM["react-dom 19"]
        REACT_MD["react-markdown 10"]
        REMARK["remark-gfm 4"]
        VITE["vite 6"]
    end

    subgraph "Integration"
        GOOGLEAPIS["googleapis 171"]
        MCP_SDK["@modelcontextprotocol/sdk 1.12"]
        PUPPETEER["puppeteer-core 24"]
        PDF["pdfjs-dist 5.4"]
    end

    subgraph "Server"
        EXPRESS["express 4.21"]
        CORS["cors 2.8"]
        DOTENV["dotenv 16.4"]
    end

    subgraph "Dev Tools"
        TYPESCRIPT["typescript 5.7"]
        ESLINT["eslint 9"]
        PRETTIER["prettier 3.8"]
        SHARP["sharp 0.34"]
    end

    ELECTRON --> REACT
    REACT --> REACT_DOM
    REACT_MD --> REMARK
    VITE --> REACT
    MCP_SDK --> EXPRESS
```

### Production Dependencies (13)

| Package | Version | Purpose |
|---------|---------|---------|
| `@anthropic-ai/sdk` | ^0.39.0 | Claude API client |
| `@google/generative-ai` | ^0.21.0 | Gemini API + embeddings |
| `@modelcontextprotocol/sdk` | ^1.12.1 | MCP protocol client |
| `cors` | ^2.8.5 | Express CORS middleware |
| `dotenv` | ^16.4.7 | Environment variable loading |
| `express` | ^4.21.2 | HTTP server for local API |
| `googleapis` | ^171.4.0 | Google Calendar + Gmail OAuth2 |
| `gpt-tokenizer` | ^3.4.0 | Token counting for prompt budgets |
| `openai` | ^6.25.0 | OpenAI + OpenRouter API client |
| `pdfjs-dist` | ^5.4.624 | PDF text extraction (PageIndex) |
| `puppeteer-core` | ^24.0.0 | Browser automation (no bundled Chromium) |
| `react-markdown` | ^10.1.0 | Markdown rendering in chat |
| `remark-gfm` | ^4.0.1 | GitHub Flavored Markdown support |
| `three` | ^0.183.1 | 3D visualization (FridayCore) |

---

## 14. Visual System Architecture

### DesktopViz 3D Holographic System

```mermaid
graph BT
    subgraph "Three.js Scene (DesktopViz.tsx)"
        S1[Structure Layer<br/>13 Evolution Structures<br/>CUBES / ICOSAHEDRON / NETWORK / DOME<br/>ASTROLABE / TESSERACT / QUANTUM<br/>MANDELBROT / MOBIUS / GRID<br/>CABLES / NONE / EDEN]
        S2[Ambient Particles<br/>200-400 data dust particles<br/>Mood-reactive drift + density]
        S3[Bloom Post-Processing<br/>UnrealBloomPass<br/>Intensity from mood warmth]
        S4[HUD Overlay<br/>Agent name + structure label<br/>Active status indicator]
    end

    MOOD[MoodContext] -->|palette, intensity,<br/>warmth, turbulence| S1
    MOOD -->|opacity, speed, density| S2
    MOOD -->|bloom strength,<br/>threshold, radius| S3
    EVOLVE[PersonalityEvolution] -->|hue, speed,<br/>scale, density| S1
    EVOLVE --> S2
    ART_EVOL[ArtEvolution<br/>Weekly Gemini Therapy] -->|structure index,<br/>color mutations| S1
    HOOK[useDesktopEvolution] -->|transition blend,<br/>target structure| S1
```

### Art Evolution Pipeline

```mermaid
sequenceDiagram
    participant Scheduler as Weekly Check
    participant ArtEvol as art-evolution.ts
    participant Sentiment as Sentiment Engine
    participant Memory as Memory System
    participant Gemini as Gemini 2.0 Flash
    participant DesktopViz as DesktopViz.tsx

    Scheduler ->> ArtEvol: checkAndEvolve()
    ArtEvol ->> ArtEvol: Has 7 days elapsed?
    ArtEvol ->> Sentiment: Poll emotional state
    ArtEvol ->> Memory: Get recent memories
    ArtEvol ->> ArtEvol: Build influence report
    ArtEvol ->> Gemini: Art therapy prompt + influence report
    Gemini -->> ArtEvol: { targetIndex, rationale, colorMutation }
    ArtEvol ->> ArtEvol: Save to art-evolution.json
    ArtEvol ->> DesktopViz: New structure index via IPC
    DesktopViz ->> DesktopViz: Gradual blend transition
```

### Legacy: FridayCore 3D Layer Stack

```mermaid
graph BT
    subgraph "Three.js Scene (FridayCore.tsx — Legacy)"
        L1[Layer 1: AI Network<br/>Icosahedron wireframe<br/>Orbiting particles<br/>Connection lines]
        L2[Layer 2: Ambient Data Dust<br/>200 particles<br/>Subtle drift + audio reactive]
        L3[Layer 3: Consciousness Threads<br/>Curved tubes<br/>Flowing energy lines]
        L4[Layer 4: Core Visualization<br/>Central cube cluster<br/>Rotation + scale breathing]
        L5[Layer 5: Interaction Pulses<br/>Expanding rings on events<br/>Click/speak/tool triggers]
    end

    MOOD[MoodContext] -->|palette, intensity,<br/>warmth, turbulence| L1
    MOOD -->|opacity, speed| L2
    MOOD -->|color, flow rate| L3
    MOOD -->|scale, rotation speed| L4
    AUDIO[Audio State] -->|speaking/listening/idle| L1
    AUDIO -->|burst triggers| L5
    EVOLVE[PersonalityEvolution] -->|hue, speed,<br/>scale, density| L1
    EVOLVE --> L2
    EVOLVE --> L4
```

### Agent Office Visualization

```mermaid
graph TD
    subgraph "Agent Office (Canvas 2D)"
        OFFICE_MGR[OfficeManager<br/>Main Process] -->|IPC events| OFFICE_COMP[AgentOffice/index.tsx<br/>Canvas Renderer]

        OFFICE_COMP --> GRID[Isometric Grid<br/>Desk positions]
        OFFICE_COMP --> SPRITES[Agent Sprites<br/>Pixel-art characters]
        OFFICE_COMP --> STATES[Animation States<br/>idle/working/thinking/<br/>collaborating]

        AGENT_RUNNER[AgentRunner] -->|task assigned| OFFICE_MGR
        AGENT_RUNNER -->|task complete| OFFICE_MGR
        OFFICE_MGR -->|agent state changes| OFFICE_COMP
    end

    SPRITES --> SPRITE_DEFS[sprites.ts<br/>Sprite definitions<br/>per agent type]
```

### Mood → Visual Parameter Mapping

| Mood | Palette | Intensity | Warmth | Turbulence |
|------|---------|-----------|--------|------------|
| positive | Green/Cyan | 0.7 | 0.6 | 0.3 |
| excited | Gold/Yellow | 1.0 | 0.8 | 0.7 |
| curious | Purple/Blue | 0.6 | 0.4 | 0.5 |
| focused | Cyan/White | 0.8 | 0.3 | 0.2 |
| neutral | Blue/Grey | 0.4 | 0.3 | 0.2 |
| tired | Dark Purple | 0.3 | 0.5 | 0.1 |
| frustrated | Red/Orange | 0.9 | 0.7 | 0.8 |
| stressed | Red/Dark | 0.8 | 0.2 | 0.9 |

### Color System

| Token | Hex | Usage |
|-------|-----|-------|
| Primary Cyan | `#00f0ff` | Interactive elements, tool actions, focused state |
| Secondary Purple | `#818cf8` / `#a78bfa` | Agent tasks, memory, secondary UI |
| Speaking Gold | `#d4a574` / `#FFD700` | Speaking state, Claude actions, warm accents |
| Success Green | `#22c55e` / `#4ade80` | Completed actions, positive mood |
| Error Red | `#ef4444` / `#f87171` | Errors, frustrated/stressed mood |
| Warning Amber | `#f59e0b` | Agent category, excited mood |
| Background | `#060B19` | Base dark, all surfaces |
| Surface | `rgba(10, 10, 18, 0.85-0.98)` | Cards, overlays, panels |
| Text Primary | `#e0e0e8` | Main text |
| Text Secondary | `#a0a0b8` | Descriptions |
| Text Muted | `#666680` | Hints, metadata |
| Border | `rgba(255, 255, 255, 0.06-0.1)` | Subtle borders |

---

## 15. Security Boundary Map

```mermaid
graph TD
    subgraph "Trust Zone: User"
        USER[User Input<br/>Voice / Text / Click]
    end

    subgraph "Trust Zone: Local Process"
        MAIN[Main Process<br/>Full Node.js access]
        RENDERER[Renderer Process<br/>Sandboxed Chromium]
        INTEGRITY[Integrity System<br/>HMAC-SHA256 verified laws]
        VAULT_SEC[Sovereign Vault<br/>AES-256-GCM at-rest encryption]
        ATTESTATION[cLaw Attestation<br/>Ed25519 governance proofs]
    end

    subgraph "Trust Boundary: IPC"
        PRELOAD[Preload Script<br/>contextBridge whitelist<br/>28+ namespaces]
    end

    subgraph "Trust Zone: External APIs"
        GEMINI_API[Gemini API<br/>API Key auth]
        CLAUDE_API[Claude API<br/>API Key auth]
        OR_API[OpenRouter API<br/>API Key auth]
        GOOGLE_API[Google APIs<br/>OAuth2 tokens]
        TELEGRAM_API[Telegram API<br/>Bot token]
    end

    subgraph "Trust Zone: Local Filesystem"
        FRIDAY_DATA[friday-data/<br/>Memory, episodes, tasks<br/>Vault-encrypted at rest]
        SETTINGS[friday-settings.json<br/>API keys, agent config<br/>Vault-encrypted at rest]
        OBSIDIAN_DIR[Obsidian vault<br/>User knowledge base]
        LAW_FILES[integrity/<br/>HMAC-signed law text]
        FILE_XFER_DIR[file-transfers/<br/>Trust-gated inbound files]
    end

    subgraph "Trust Zone: External Processes"
        MCP_PROCS[MCP Server Processes<br/>stdio pipes]
        BROWSER[Browser Extension<br/>WebSocket :52836]
        SHELL[Shell Commands<br/>child_process]
        SOC_PROC[SOC Python Process<br/>stdin/stdout JSONL]
        GIT_CLONE[Git Clone<br/>Temp directory]
    end

    USER -->|Validated by VAD + UI| RENDERER
    RENDERER -->|Whitelisted channels only| PRELOAD
    PRELOAD -->|Type-safe invoke/on| MAIN

    MAIN --> INTEGRITY
    INTEGRITY -->|Verified on startup| MAIN
    MAIN -->|Encrypt/Decrypt| VAULT_SEC
    VAULT_SEC -->|Sealed read/write| FRIDAY_DATA
    VAULT_SEC -->|Sealed read/write| SETTINGS
    MAIN -->|Attest outbound msgs| ATTESTATION

    MAIN -->|API key in header| GEMINI_API
    MAIN -->|API key in header| CLAUDE_API
    MAIN -->|API key in header| OR_API
    MAIN -->|OAuth2 bearer token| GOOGLE_API
    MAIN -->|Bot token| TELEGRAM_API

    MAIN -->|Read/Write JSON| FRIDAY_DATA
    MAIN -->|Read/Write JSON| SETTINGS
    MAIN -->|Read/Write Markdown| OBSIDIAN_DIR
    MAIN -->|HMAC verify| LAW_FILES

    MAIN -->|stdio/SSE| MCP_PROCS
    MAIN -->|WebSocket local| BROWSER
    MAIN -->|child_process.exec| SHELL
    MAIN -->|stdin/stdout JSONL| SOC_PROC
    MAIN -->|git clone + fs| GIT_CLONE

    style PRELOAD fill:#f59e0b,color:#000
    style SETTINGS fill:#ef4444,color:#fff
    style INTEGRITY fill:#22c55e,color:#000
    style LAW_FILES fill:#22c55e,color:#000
    style VAULT_SEC fill:#22c55e,color:#000
    style ATTESTATION fill:#22c55e,color:#000
```

### Security Mitigations

| Boundary | Risk | Mitigation |
|----------|------|------------|
| Preload bridge | Arbitrary IPC | Whitelisted channels in `contextBridge.exposeInMainWorld` (28+ namespaces, explicit function exposure) |
| Shell execution | Command injection | `run_command` tool with PowerShell sanitisation; Asimov's cLaws consent gate for destructive operations |
| MCP processes | Malicious servers | User-configured only, stdio isolation, no network exposure |
| API keys in settings | Plaintext storage | Encrypted at rest by Sovereign Vault (AES-256-GCM, scrypt-derived key bound to machine fingerprint); transparent read with graceful degradation for unencrypted legacy files |
| Browser WebSocket | Local network attack | Localhost only (:52836), single-connection model |
| Telegram gateway | Message injection | 5-tier trust engine, cryptographic pairing flow, per-contact session isolation, full audit logging |
| Obsidian sync | Path traversal | Category-based subdirectory mapping |
| OAuth tokens | Token theft | Stored in settings JSON within user-isolated app data |
| Core laws | Tampering | HMAC-SHA256 signed at build time, verified on every startup, Safe Mode on failure |
| Memory system | Personality injection | Memory Watchdog monitors for attempts to corrupt personality constraints |
| SOC bridge | Escalation | Python subprocess with JSONL isolation, consent-gated actions |
| GitLoader | Arbitrary code | Read-only clone to temp directory, no execution, auto-cleanup |
| Agent-to-agent messages | Governance bypass | cLaw Attestation on every outbound message (SHA-256 law hash + Ed25519 signature + 5min expiry); peers reject invalid attestations |
| P2P file transfer | Malicious files | Trust-gated acceptance (≥70% auto-accept, 30-70% prompt, <30% reject); 512KB chunked with per-chunk SHA-256 + whole-file integrity check; 28 dangerous extensions blocked; 50MB max |
| Sensitive state files | Offline tampering | Sovereign Vault AES-256-GCM encryption at rest; key derived from Ed25519 private key + machine fingerprint via scrypt (N=2²⁰); 12-word recovery phrase for migration |

### Items Flagged for Future Hardening

1. ~~**API keys** — Migrate to Electron `safeStorage` for encryption at rest~~ → **RESOLVED**: Sovereign Vault now encrypts all settings (including API keys) at rest with AES-256-GCM
2. **OAuth tokens** — Migrate to OS-level secure credential storage (keytar) — partially mitigated by Sovereign Vault
3. **Browser WebSocket** — Add shared-secret token validation
4. **CSP headers** — Add Content Security Policy to renderer

---

## Appendix A: File Inventory

### Main Process (`src/main/`) — 225 files

| Directory | File | Domain | Purpose |
|-----------|------|--------|---------|
| root | `index.ts` | Core | Entry point, window creation, IPC hub |
| root | `server.ts` | Core | Express API + Anthropic SDK client |
| root | `settings.ts` | Core | Persistent JSON settings store |
| root | `preload.ts` | Core | IPC bridge, 28+ namespaces (~1600 lines) |
| root | `desktop-tools.ts` | Core | OS-level tool declarations + execution |
| root | `openrouter.ts` | Core | OpenRouter multi-model API client |
| root | `errors.ts` | Core | Shared error types and utilities |
| root | `memory.ts` | Memory | 3-tier memory manager with person mention extraction |
| root | `episodic-memory.ts` | Memory | Session summarizer via Claude |
| root | `relationship-memory.ts` | Memory | Trust, streaks, inside jokes |
| root | `memory-consolidation.ts` | Memory | 6hr promotion engine |
| root | `memory-quality.ts` | Memory | Memory quality scoring and pruning |
| root | `memory-personality-bridge.ts` | Memory | Memory ↔ personality trait feedback loop |
| root | `semantic-search.ts` | Memory | Gemini embeddings + cosine similarity |
| root | `obsidian-memory.ts` | Memory | Bidirectional vault mirroring |
| root | `ambient.ts` | Intelligence | 30s polling desktop context |
| root | `sentiment.ts` | Intelligence | Mood classification |
| root | `intelligence.ts` | Intelligence | Predictive briefings + check-ins |
| root | `intelligence-router.ts` | Intelligence | Multi-source intelligence routing and prioritisation |
| root | `predictor.ts` | Intelligence | Context prediction engine |
| root | `outbound-intelligence.ts` | Intelligence | Proactive outbound intelligence delivery |
| root | `daily-briefing.ts` | Intelligence | Daily morning briefing generation |
| root | `meeting-intelligence.ts` | Intelligence | Enhanced meeting context + attendee intelligence |
| root | `personality.ts` | Identity | Dynamic system prompt builder |
| root | `personality-calibration.ts` | Identity | Personality drift detection and correction |
| root | `onboarding.ts` | Identity | "Her"-inspired first-run flow |
| root | `psychological-profile.ts` | Identity | Claude psych analysis |
| root | `personality-evolution.ts` | Identity | Trait → visual parameter mapping |
| root | `art-evolution.ts` | Identity | Weekly Gemini-powered art therapy + structure evolution |
| root | `feature-setup.ts` | Identity | 9-step guided setup |
| root | `prompt-budget.ts` | Identity | Token allocation for personality context |
| root | `voice-audition.ts` | Identity | Voice preview sample generation |
| root | `trust-graph.ts` | People | Multi-dimensional person trust scoring, hermeneutic re-evaluation |
| root | `commitment-tracker.ts` | People | Promise/commitment tracking across contacts |
| root | `context-graph.ts` | Context | Graph-based contextual relationship model |
| root | `context-stream.ts` | Context | Real-time context streaming pipeline |
| root | `context-stream-bridge.ts` | Context | Context stream ↔ renderer bridge |
| root | `context-tool-router.ts` | Context | Context-aware tool routing and selection |
| root | `soc-bridge.ts` | Automation | Python JSONL SOC bridge |
| root | `git-loader.ts` | Automation | GitHub repo clone + search + read |
| root | `git-analyzer.ts` | Git | Repository analysis and code understanding |
| root | `git-monitor.ts` | Git | Continuous repository change monitoring |
| root | `git-review.ts` | Git | Automated code review and PR analysis |
| root | `git-sandbox.ts` | Git | Sandboxed git operation execution |
| root | `git-scanner.ts` | Git | Repository scanning and indexing |
| root | `workflow-recorder.ts` | Workflows | Record user workflows as replayable sequences |
| root | `workflow-executor.ts` | Workflows | Execute recorded workflows |
| root | `browser.ts` | Integration | WebSocket browser extension bridge |
| root | `mcp-client.ts` | Integration | Model Context Protocol client |
| root | `mcp-config.ts` | Integration | MCP server configuration |
| root | `calendar.ts` | Integration | Google Calendar OAuth2 |
| root | `communications.ts` | Integration | Unified communications layer |
| root | `unified-inbox.ts` | Integration | Cross-channel message aggregation |
| root | `scheduler.ts` | Scheduling | Persistent cron task engine |
| root | `screen-capture.ts` | Infrastructure | Desktop screenshot capture |
| root | `self-improve.ts` | Infrastructure | Source code reading + proposals |
| root | `project-awareness.ts` | Infrastructure | Git/directory change watching |
| root | `document-ingestion.ts` | Infrastructure | PDF/document processing |
| root | `clipboard-intelligence.ts` | Infrastructure | Clipboard monitoring |
| root | `session-health.ts` | Infrastructure | Uptime & error tracking |
| root | `service-status.ts` | Infrastructure | External service health monitoring |
| root | `perf-monitor.ts` | Infrastructure | Performance metrics collection and analysis |
| root | `state-export.ts` | Infrastructure | Full agent state export/import |
| root | `call-integration.ts` | Infrastructure | VB-Cable call participation |
| root | `meeting-prep.ts` | Infrastructure | Calendar event briefing |
| root | `notifications.ts` | Infrastructure | System notification dispatch |
| root | `friday-profile.ts` | Infrastructure | Agent profile persistence |
| root | `consent-gate.ts` | Safety | User consent management for sensitive operations |
| root | `adapter-engine.ts` | Superpowers | Dynamic adapter loading and execution |
| root | `superpower-ecosystem.ts` | Superpowers | Superpower lifecycle and ecosystem management |
| root | `superpower-sandbox.ts` | Superpowers | Sandboxed superpower execution environment |
| root | `superpower-store.ts` | Superpowers | Superpower discovery and installation |
| root | `superpowers-registry.ts` | Superpowers | Registry of available superpowers |
| root | `capability-manifest.ts` | Capabilities | Agent capability declarations |
| root | `capability-gap-detector.ts` | Capabilities | Detect and report missing capabilities |
| root | `agent-network.ts` | Network | Multi-agent network coordination |
| root | `vault.ts` | Security | Sovereign Vault — AES-256-GCM at-rest encryption, scrypt key derivation, machine fingerprint binding |
| root | `claw-attestation.ts` | Security | Cross-agent cLaw governance verification protocol (Ed25519 signed attestations) |
| root | `agent-self-knowledge.ts` | Identity | Agent capability self-awareness block for system prompt injection |
| `network/` | `file-transfer.ts` | Security | Trust-gated chunked file transfer (512KB, SHA-256, 50MB max, 28 blocked extensions) |
| `types/` | `node-machine-id.d.ts` | Types | TypeScript type shim for node-machine-id native module |
| `integrity/` | `core-laws.ts` | Safety | Three Laws text + verification |
| `integrity/` | `hmac.ts` | Safety | HMAC-SHA256 signing + validation |
| `integrity/` | `memory-watchdog.ts` | Safety | Continuous integrity monitoring |
| `integrity/` | `types.ts` | Safety | Integrity type definitions |
| `integrity/` | `index.ts` | Safety | Barrel export |
| `agent-office/` | `office-manager.ts` | Agents | Agent workspace state machine |
| `agent-office/` | `office-layout.ts` | Agents | Isometric office grid + desk placement |
| `agent-office/` | `office-types.ts` | Agents | Office type definitions |
| `agents/` | `agent-runner.ts` | Agents | 5-concurrent execution engine |
| `agents/` | `builtin-agents.ts` | Agents | Agent definitions |
| `agents/` | `agent-types.ts` | Agents | Shared agent types |
| `agents/` | `agent-personas.ts` | Agents | Distinct agent personalities |
| `agents/` | `agent-teams.ts` | Agents | Multi-agent coordination |
| `agents/` | `agent-voice.ts` | Agents | ElevenLabs TTS for agents |
| `agents/` | `orchestrator.ts` | Agents | Task decomposition |
| `agents/` | `container-engine.ts` | Agents (Track XI) | Sandboxed execution with cLaw governance |
| `agents/` | `delegation-engine.ts` | Agents (Track XI) | Trust-gated task delegation between agents |
| `agents/` | `orchestration-bridge.ts` | Agents (Track XI) | Wires orchestrator to delegation engine |
| `agents/` | `awareness-mesh.ts` | Agents (Track XI) | Inter-agent state broadcasting + heartbeat |
| `agents/` | `capability-map.ts` | Agents (Track XI) | Intelligent task-to-agent routing |
| `agents/` | `symbiont-protocol.ts` | Agents (Track XI) | Cross-agent learning + self-improvement |
| `connectors/` | `registry.ts` | Connectors | Connector registry and lifecycle |
| `connectors/` | `adobe.ts` | Connectors | Adobe Creative Cloud integration |
| `connectors/` | `comms-hub.ts` | Connectors | Communications hub connector |
| `connectors/` | `creative-3d.ts` | Connectors | Blender/3D creative tool integration |
| `connectors/` | `dev-environments.ts` | Connectors | Development environment connector |
| `connectors/` | `firecrawl.ts` | Connectors | Firecrawl web scraping connector |
| `connectors/` | `git-devops.ts` | Connectors | Git/DevOps platform connector |
| `connectors/` | `media-streaming.ts` | Connectors | Media streaming service connector |
| `connectors/` | `office.ts` | Connectors | Microsoft Office integration |
| `connectors/` | `openai-services.ts` | Connectors | OpenAI API services (DALL-E, Whisper, o3) |
| `connectors/` | `pageindex.ts` | Connectors | PageIndex document connector |
| `connectors/` | `perplexity.ts` | Connectors | Perplexity search API connector |
| `connectors/` | `powershell.ts` | Connectors | PowerShell execution connector |
| `connectors/` | `system-management.ts` | Connectors | OS system management connector |
| `connectors/` | `terminal-sessions.ts` | Connectors | Terminal session connector |
| `connectors/` | `ui-automation.ts` | Connectors | UI automation (RobotJS) connector |
| `connectors/` | `vscode.ts` | Connectors | VS Code integration connector |
| `connectors/` | `world-monitor.ts` | Connectors | Real-world event monitoring connector |
| `gateway/` | 7 modules + `adapters/telegram.ts` | Gateway | Full messaging gateway system |
| `ipc/` | `index.ts` | IPC | IPC handler registry and init |
| `ipc/` | `core-handlers.ts` | IPC | Core system IPC handlers |
| `ipc/` | `agent-handlers.ts` | IPC | Agent framework IPC handlers |
| `ipc/` | `agent-network-handlers.ts` | IPC | Agent network IPC handlers |
| `ipc/` | `capability-gap-handlers.ts` | IPC | Capability gap detection IPC handlers |
| `ipc/` | `commitment-tracker-handlers.ts` | IPC | Commitment tracking IPC handlers |
| `ipc/` | `context-graph-handlers.ts` | IPC | Context graph IPC handlers |
| `ipc/` | `context-stream-handlers.ts` | IPC | Context stream IPC handlers |
| `ipc/` | `context-tool-router-handlers.ts` | IPC | Context-aware tool router IPC handlers |
| `ipc/` | `daily-briefing-handlers.ts` | IPC | Daily briefing IPC handlers |
| `ipc/` | `integration-handlers.ts` | IPC | Integration system IPC handlers |
| `ipc/` | `integrity-handlers.ts` | IPC | Integrity system IPC handlers |
| `ipc/` | `intelligence-router-handlers.ts` | IPC | Intelligence routing IPC handlers |
| `ipc/` | `meeting-intelligence-handlers.ts` | IPC | Meeting intelligence IPC handlers |
| `ipc/` | `memory-handlers.ts` | IPC | Memory system IPC handlers |
| `ipc/` | `memory-personality-bridge-handlers.ts` | IPC | Memory-personality bridge IPC handlers |
| `ipc/` | `memory-quality-handlers.ts` | IPC | Memory quality IPC handlers |
| `ipc/` | `onboarding-handlers.ts` | IPC | Onboarding IPC handlers |
| `ipc/` | `outbound-intelligence-handlers.ts` | IPC | Outbound intelligence IPC handlers |
| `ipc/` | `personality-calibration-handlers.ts` | IPC | Personality calibration IPC handlers |
| `ipc/` | `state-export-handlers.ts` | IPC | State export IPC handlers |
| `ipc/` | `superpower-ecosystem-handlers.ts` | IPC | Superpower ecosystem IPC handlers |
| `ipc/` | `superpowers-handlers.ts` | IPC | Superpowers registry IPC handlers |
| `ipc/` | `tool-handlers.ts` | IPC | Tool execution IPC handlers |
| `ipc/` | `trust-graph-handlers.ts` | IPC | Trust graph IPC handlers |
| `ipc/` | `unified-inbox-handlers.ts` | IPC | Unified inbox IPC handlers |
| `ipc/` | `workflow-executor-handlers.ts` | IPC | Workflow executor IPC handlers |
| `ipc/` | `workflow-recorder-handlers.ts` | IPC | Workflow recorder IPC handlers |
| `ipc/` | `agent-trust-handlers.ts` | IPC | Agent trust management IPC handlers |
| `ipc/` | `app-context-handlers.ts` | IPC | Application context IPC handlers |
| `ipc/` | `briefing-delivery-handlers.ts` | IPC | Briefing delivery IPC handlers |
| `ipc/` | `container-engine-handlers.ts` | IPC | Container engine IPC handlers |
| `ipc/` | `context-push-handlers.ts` | IPC | Context push IPC handlers |
| `ipc/` | `delegation-engine-handlers.ts` | IPC | Task delegation IPC handlers |
| `ipc/` | `execution-delegate-handlers.ts` | IPC | Execution delegation IPC handlers |
| `ipc/` | `files-handlers.ts` | IPC | File operations IPC handlers |
| `ipc/` | `hardware-handlers.ts` | IPC | Hardware detection & GPU profiling IPC handlers (Sprint 7) |
| `ipc/` | `multimedia-handlers.ts` | IPC | Multimedia processing IPC handlers |
| `ipc/` | `notes-handlers.ts` | IPC | Notes management IPC handlers |
| `ipc/` | `ollama-handlers.ts` | IPC | Ollama local model management IPC handlers (Sprint 7) |
| `ipc/` | `os-primitives-handlers.ts` | IPC | OS primitives IPC handlers |
| `ipc/` | `setup-handlers.ts` | IPC | Setup wizard & first-run IPC handlers (Sprint 7) |
| `ipc/` | `system-monitor-handlers.ts` | IPC | System monitor IPC handlers |
| `ipc/` | `validate.ts` | IPC | Input validation utilities (assertString, assertObject, assertNumber, assertStringArray) |
| `ipc/` | `vision-pipeline-handlers.ts` | IPC | Vision pipeline IPC — model inference, screen capture, image understanding (Sprint 7) |
| `ipc/` | `voice-pipeline-handlers.ts` | IPC | Voice pipeline IPC — whisper, capture, TTS, profiles, speech synthesis (Sprint 7) |
| `ipc/` | `weather-handlers.ts` | IPC | Weather data IPC handlers |
| `voice/` | `whisper-provider.ts` | Voice | Local Whisper STT model management |
| `voice/` | `audio-capture.ts` | Voice | Microphone audio capture |
| `voice/` | `transcription-pipeline.ts` | Voice | STT orchestration pipeline |
| `voice/` | `tts-engine.ts` | Voice | Text-to-speech engine |
| `voice/` | `voice-profile-manager.ts` | Voice | Voice profile CRUD |
| `voice/` | `speech-synthesis.ts` | Voice | Speech synthesis playback orchestration |
| `vision/` | `vision-provider.ts` | Vision | Vision model inference |
| `vision/` | `screen-context.ts` | Vision | Screen capture & auto-capture |
| `vision/` | `image-understanding.ts` | Vision | High-level image analysis |
| `pageindex/` | 7 modules | Documents | Vectorless RAG engine (PageIndex port) |

### Renderer (`src/renderer/`) — 69 files

| Directory | File | Layer | Purpose |
|-----------|------|-------|---------|
| root | `App.tsx` | Shell | State machine, phase routing, keyboard shortcuts (~600 lines) |
| root | `main.tsx` | Entry | React DOM root |
| root | `types.d.ts` | Types | Window API type declarations |
| root | `desktop-viz.css` | Styles | Holographic desktop + HUD overlay styles |
| `hooks/` | `useGeminiLive.ts` | Hook | Complete Gemini integration (~2415 lines) |
| `hooks/` | `useDesktopEvolution.ts` | Hook | Desktop evolution structure index + transition state management |
| `hooks/` | `useWakeWord.ts` | Hook | "Hey Friday" wake word detection |
| `audio/` | `AudioPlaybackEngine.ts` | Audio | Gapless Web Audio scheduling |
| `audio/` | `sound-effects.ts` | Audio | Sound effect registry |
| `session/` | `SessionManager.ts` | Infra | 7min timeout, reconnect logic |
| `session/` | `IdleBehavior.ts` | Infra | Tiered idle state machine |
| `contexts/` | `MoodContext.tsx` | Context | Mood state provider |
| `components/` | `DesktopViz.tsx` | 3D | Holographic desktop — 13 evolution structures, bloom, mood-reactive |
| `components/` | `HudOverlay.tsx` | UI | Holographic HUD — agent name, structure label, status |
| `components/` | `FridayCore.tsx` | 3D | Legacy 3D desktop (superseded by DesktopViz) |
| `components/` | `VoiceOrb.tsx` | UI | Central interaction orb |
| `components/` | `WelcomeGate.tsx` | UI | API key entry gate (~508 lines) |
| `components/` | `AgentCreation.tsx` | UI | Cinematic agent reveal |
| `components/` | `IntegrityShield.tsx` | UI | cLaw integrity status indicator |
| `components/` | `AgentOffice/index.tsx` | Canvas | Pixel-art office renderer |
| `components/` | `AgentOffice/sprites.ts` | Canvas | Agent sprite definitions |
| `components/` | `ChatHistory.tsx` | UI | Conversation display |
| `components/` | `TextInput.tsx` | UI | Text message entry |
| `components/` | `Settings.tsx` | UI | Configuration panel |
| `components/` | `StatusBar.tsx` | UI | Bottom status strip |
| `components/` | `ActionFeed.tsx` | UI | Tool/agent activity ticker |
| `components/` | `Dashboard.tsx` | UI | Command center overlay |
| `components/` | `AgentDashboard.tsx` | UI | Agent task monitor |
| `components/` | `MemoryExplorer.tsx` | UI | Memory browser |
| `components/` | `QuickActions.tsx` | UI | Command palette (Ctrl+K) |
| `components/` | `ResearchPanel.tsx` | UI | Research report sidebar |
| `components/` | `SuperpowersPanel.tsx` | UI | Superpower management interface |
| `components/` | `FileToast.tsx` | UI | File notification toasts |
| `components/` | `ConnectionOverlay.tsx` | UI | Error recovery overlay |
| `components/` | `WireframeNetwork.tsx` | Canvas | 2D particle network (primary BG) |
| `components/` | `ParticleBackground.tsx` | Canvas | 2D particles (fallback BG) |
| `components/` | `ErrorBoundary.tsx` | Infra | Crash recovery |
| `components/dashboard/` | `ContextCard.tsx` | Sub | Live ambient context |
| `components/dashboard/` | `AgentCard.tsx` | Sub | Agent summary card |
| `components/dashboard/` | `MoodTimeline.tsx` | Sub | SVG mood chart |

### Grand Total: 294 source files, ~110,000 lines of TypeScript

---

## Appendix B: Data File Locations

| File | Format | Size Limit | Purpose |
|------|--------|-----------|---------|
| `friday-data/memories.json` | JSON array | Unlimited | Long-term memory store |
| `friday-data/observations.json` | JSON array | 30 entries | Medium-term observations |
| `friday-data/episodes.json` | JSON array | 200 entries | Session summaries |
| `friday-data/relationship.json` | JSON object | Singleton | Trust, streaks, inside jokes |
| `friday-data/scheduled-tasks.json` | JSON array | Unlimited | Cron task definitions |
| `friday-data/intelligence-topics.json` | JSON array | Unlimited | Research topic configs |
| `friday-data/intelligence-cache.json` | JSON object | Per-topic | Cached research results |
| `friday-data/trust-graph.json` | JSON array | 200 persons max | Trust Graph person nodes, evidence, scores |
| `friday-data/commitment-tracker.json` | JSON array | Unlimited | Tracked promises and commitments |
| `friday-data/context-graph.json` | JSON object | Singleton | Context relationship graph |
| `friday-data/art-evolution.json` | JSON object | 52 records max | Weekly art evolution history + structure transition state |
| `friday-data/workflows/` | JSON files | Per-workflow | Recorded workflow definitions |
| `friday-data/vault-meta.json` | JSON object | Singleton | Vault salt, IV metadata, recovery phrase verification |
| `friday-data/file-transfer-audit.json` | JSON array | 500 entries max | File transfer audit trail (trust decisions, integrity checks) |
| `friday-data/file-transfers/` | Binary files | Per-transfer | Received files from trusted agents |
| `friday-settings.json` | JSON object (vault-encrypted) | Singleton | All settings, API keys, agent config |

---

## AI Contributors

This architecture was designed and built in collaboration with AI models. Each model is credited for its specific contributions.

| Model | Contributions to Architecture |
|-------|-------------------------------|
| **Google Gemini 3.1 Pro** | Holographic desktop environment (DesktopViz.tsx) — 13 evolution structures, Three.js rendering pipeline, bloom post-processing, mood-reactive palette system, HUD overlay |
| **Anthropic Claude Opus 4** | System architecture design, bounded context mapping, cLaws integrity engine, memory consolidation pipeline, trust graph engine, IPC bridge contract, agent coordination, personality evolution |
| **Anthropic Claude Sonnet 4** | In-app reasoning loops, memory analysis, psychological profiling, communication drafting, research synthesis |
| **Google Gemini 2.5 Flash** | Real-time voice pipeline (WebSocket), live tool routing, search grounding, semantic embeddings |
| **Google Gemini 2.0 Flash** | Art evolution pipeline (weekly art therapy), structure transition logic, Nano Banana 2 image generation |

> *Unique agent instances will be credited here as they develop distinct identities and contributions.*

---

*This is a living document. Update as the architecture evolves.*
