# Agent Friday (nexus-os) — Living Architecture Document

> **Method**: Adapted from Nick Tune's Domain-Driven Architecture mapping for monorepo Electron applications.
> **Last updated**: 2026-02-26
> **Scope**: Complete system — main process, renderer, IPC bridge, agents, connectors, gateway, MCP.

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
            THREE[Three.js NexusCore]
            CANVAS[Canvas 2D Backgrounds]
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
        end

        IPC[IPC Bridge / Preload]
    end

    USER((User))
    GEMINI[Gemini 2.5 Flash<br/>Native Audio WebSocket]
    CLAUDE[Claude Sonnet/Opus<br/>Anthropic SDK]
    OBSIDIAN[Obsidian Vault<br/>Local Filesystem]
    TELEGRAM[Telegram Bot API]
    GCAL[Google Calendar API]
    GMAIL[Gmail API]
    PERPLEXITY[Perplexity API]
    OPENAI[OpenAI API]
    MCP_SERVERS[MCP Servers<br/>stdio/SSE]
    BROWSER_EXT[Browser Extension<br/>WebSocket :52836]

    USER -->|Voice/Text/Click| UI
    UI <-->|contextBridge| IPC
    IPC <-->|ipcMain/ipcRenderer| CORE

    CORE <-->|WebSocket| GEMINI
    CORE <-->|REST API| CLAUDE
    MEM <-->|File I/O| OBSIDIAN
    GW <-->|Bot API| TELEGRAM
    CONN <-->|OAuth2| GCAL
    CONN <-->|OAuth2| GMAIL
    CONN <-->|REST| PERPLEXITY
    CONN <-->|REST| OPENAI
    MCP_MGR <-->|stdio/SSE| MCP_SERVERS
    GW <-->|WebSocket| BROWSER_EXT

    MIC -->|16kHz PCM| GEMINI
    GEMINI -->|24kHz PCM| AUDIO_OUT
```

### Key External Dependencies

| Service | Protocol | Purpose | Module |
|---------|----------|---------|--------|
| Gemini 2.5 Flash | WebSocket (Native Audio) | Voice conversation, tool calls, real-time reasoning | `gemini-live.ts` via `useGeminiLive.ts` |
| Claude Sonnet/Opus | REST (Anthropic SDK) | Deep analysis, memory consolidation, psych profiles, code review | `server.ts`, `personality.ts` |
| Obsidian | Local filesystem | Bidirectional memory mirroring | `obsidian-sync.ts` |
| Telegram | Bot HTTP API | Message gateway (inbound/outbound) | `telegram-gateway.ts` |
| Google Calendar | OAuth2 REST | Calendar read/write | `google-calendar.ts` |
| Gmail | OAuth2 REST | Email read/compose/send | `gmail.ts` |
| Perplexity | REST API | Web search with citations | `perplexity.ts` |
| OpenAI | REST API | Image generation (DALL-E 3), TTS, GPT fallback | `openai-services.ts` |
| MCP Servers | stdio/SSE | Extensible tool protocol | `mcp-manager.ts` |
| Browser Extension | WebSocket :52836 | Tab control, screenshots, DOM interaction | `browser-connector.ts` |

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
    end

    subgraph "Integration & Gateway"
        TELE_GW[Telegram Gateway]
        BROWSER_GW[Browser Gateway]
        CAL_CONN[Calendar Connector]
        MAIL_CONN[Gmail Connector]
        DESK_CONN[Desktop Connector]
        MCP[MCP Protocol]
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
    end

    subgraph "Presentation Layer"
        NEXUS[NexusCore 3D]
        ORB[VoiceOrb]
        DASH[Dashboard]
        ACTIONS[Action Feed]
        CHAT[Chat History]
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

    TELE_GW --> VOICE
    BROWSER_GW --> DESK_CONN
    MCP --> AGENT_FW

    SCHEDULER --> CRON
    SCHEDULER --> PREDICT

    ONBOARD --> PSYCH
    PSYCH --> PERSONA
    PERSONA --> EVOLVE

    MOOD --> NEXUS
    VOICE --> ORB
    AGENT_FW --> ACTIONS
```

### Context Ownership Table

| Bounded Context | Owner Module(s) | Data Store | Upstream | Downstream |
|----------------|-----------------|------------|----------|------------|
| Voice & Conversation | `useGeminiLive.ts`, `SessionManager.ts` | In-memory | User mic, Gemini WS | Memory, Mood, UI |
| Short-term Memory | `memory.ts` | In-memory (20 entries) | Conversation | Medium-term promotion |
| Medium-term Memory | `memory.ts` | `eve-data/observations.json` (30 entries) | Short-term | Long-term promotion, Consolidation |
| Long-term Memory | `memory.ts` | `eve-data/memories.json` (unlimited) | Consolidation, direct save | Obsidian sync, Semantic search |
| Episodic Memory | `episodic-memory.ts` | `eve-data/episodes.json` (200 cap) | Session end | Consolidation, Search |
| Relationship Memory | `relationship-memory.ts` | `eve-data/relationship.json` (singleton) | Session events | Personality, Greeting |
| Consolidation | `memory-consolidation.ts` | N/A (transforms) | Medium-term, Episodes | Long-term |
| Semantic Search | `semantic-search.ts` | In-memory embeddings | All memory tiers | Query responses |
| Predictive Intelligence | `predictive-intelligence.ts` | `eve-data/intelligence-*.json` | Ambient context, Sentiment | Briefings, Check-ins |
| Agent Framework | `agent-framework.ts` | In-memory task queue | Tool calls, Scheduler | Claude API, Results |
| Gateway | `telegram-gateway.ts`, `browser-connector.ts` | N/A (passthrough) | External messages | Conversation injection |
| Connectors | `google-calendar.ts`, `gmail.ts`, etc. | OAuth tokens in settings | Tool calls | API responses |
| MCP | `mcp-manager.ts` | Server configs in settings | Tool calls | stdio/SSE servers |
| Scheduler | `task-scheduler.ts` | `eve-data/scheduled-tasks.json` | User commands, Intelligence | Cron execution |
| Identity | `onboarding.ts`, `personality.ts` | `eve-settings.json` | First-run flow | All personality-aware modules |
| Presentation | React components | React state, MoodContext | All main process data | User display |

---

## 3. Main Process Module Map

```mermaid
graph TD
    INDEX[index.ts<br/>Entry Point & IPC Hub]

    subgraph "Core Services"
        SERVER[server.ts<br/>Claude Anthropic Client]
        GEMINI[gemini-live.ts<br/>Gemini WebSocket Manager]
        SETTINGS[settings.ts<br/>Persistent Settings Store]
    end

    subgraph "Memory Domain"
        MEMORY[memory.ts<br/>3-Tier Memory Manager]
        EPISODIC[episodic-memory.ts<br/>Session Summarizer]
        RELATIONSHIP[relationship-memory.ts<br/>User Bond Tracker]
        CONSOLIDATION[memory-consolidation.ts<br/>6hr Promotion Engine]
        SEMANTIC[semantic-search.ts<br/>Embedding + Cosine Sim]
        OBSIDIAN[obsidian-sync.ts<br/>Vault Mirroring]
    end

    subgraph "Intelligence Domain"
        AMBIENT[ambient.ts<br/>30s Polling Context]
        SENTIMENT[sentiment.ts<br/>Mood Classification]
        PREDICTIVE[predictive-intelligence.ts<br/>Briefings & Check-ins]
        WORLD[world-monitor.ts<br/>News + Weather + Stocks]
    end

    subgraph "Agent Domain"
        AGENT_FW[agent-framework.ts<br/>Task Queue + Execution]
        AGENT_TYPES[agent-types/<br/>research, summarize,<br/>code-review, draft-email]
    end

    subgraph "Integration Domain"
        TELE_GW[telegram-gateway.ts<br/>Bot API Bridge]
        BROWSER_CONN[browser-connector.ts<br/>WS :52836 Bridge]
        GCAL[google-calendar.ts<br/>OAuth2 Calendar]
        GMAIL_CONN[gmail.ts<br/>OAuth2 Email]
        DESKTOP[desktop-tools.ts<br/>Window/App/Clipboard]
        MCP_MGR[mcp-manager.ts<br/>Multi-Server Protocol]
        PERPLEXITY[perplexity.ts<br/>Search w/ Citations]
        OPENAI[openai-services.ts<br/>DALL-E + TTS + GPT]
    end

    subgraph "Scheduling Domain"
        SCHEDULER[task-scheduler.ts<br/>Persistent Cron]
    end

    subgraph "Identity Domain"
        PERSONALITY[personality.ts<br/>System Prompt Builder]
        ONBOARDING[onboarding.ts<br/>First-Run Flow]
        PSYCH[psychological-profile.ts<br/>Claude Psych Analysis]
        FEATURE_SETUP[feature-setup.ts<br/>9-Step Guided Setup]
        EVOLUTION[personality-evolution.ts<br/>Visual Trait Mapping]
    end

    subgraph "Infrastructure"
        PRELOAD[preload.ts<br/>IPC Bridge / contextBridge]
        TOOLS[tools.ts<br/>Gemini Tool Declarations]
        CLIPBOARD[clipboard-monitor.ts<br/>Polling Clipboard]
        SESSION_HEALTH[session-health.ts<br/>Uptime & Error Tracking]
    end

    INDEX --> SERVER
    INDEX --> GEMINI
    INDEX --> SETTINGS
    INDEX --> MEMORY
    INDEX --> EPISODIC
    INDEX --> RELATIONSHIP
    INDEX --> CONSOLIDATION
    INDEX --> SEMANTIC
    INDEX --> OBSIDIAN
    INDEX --> AMBIENT
    INDEX --> SENTIMENT
    INDEX --> PREDICTIVE
    INDEX --> AGENT_FW
    INDEX --> TELE_GW
    INDEX --> BROWSER_CONN
    INDEX --> GCAL
    INDEX --> GMAIL_CONN
    INDEX --> DESKTOP
    INDEX --> MCP_MGR
    INDEX --> SCHEDULER
    INDEX --> PERSONALITY
    INDEX --> ONBOARDING
    INDEX --> CLIPBOARD
    INDEX --> SESSION_HEALTH
    INDEX --> PRELOAD

    MEMORY --> SEMANTIC
    MEMORY --> OBSIDIAN
    CONSOLIDATION --> SERVER
    CONSOLIDATION --> MEMORY
    EPISODIC --> SERVER
    PREDICTIVE --> SERVER
    PREDICTIVE --> AMBIENT
    PREDICTIVE --> SENTIMENT
    AGENT_FW --> SERVER
    AGENT_FW --> AGENT_TYPES
    ONBOARDING --> PSYCH
    PSYCH --> SERVER
    PERSONALITY --> SETTINGS
    PERSONALITY --> RELATIONSHIP
    EVOLUTION --> SETTINGS
    TOOLS --> DESKTOP
    TOOLS --> MCP_MGR
    TELE_GW --> GEMINI
    SCHEDULER --> SETTINGS
```

### Module Coupling Analysis

| Module | Fan-In | Fan-Out | Coupling Level | Notes |
|--------|--------|---------|----------------|-------|
| `index.ts` | 1 (electron) | 30+ | **Hub** | Central IPC registration — expected for Electron main |
| `settings.ts` | 20+ | 0 | **Afferent hub** | Read by nearly everything, writes from few |
| `server.ts` | 8 | 1 (Anthropic SDK) | **Shared service** | Claude API client used by consolidation, episodic, predictive, agents, psych |
| `memory.ts` | 5 | 3 | **Domain core** | Semantic search, Obsidian sync, settings |
| `agent-framework.ts` | 3 | 5 | **Orchestrator** | Routes to agent types, uses Claude |
| `preload.ts` | 1 | 0 | **Bridge** | Pure declaration, no logic dependencies |

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
        NEXUS[NexusCore.tsx<br/>Three.js 5-Layer 3D]
        WIRE[WireframeNetwork.tsx<br/>Canvas 2D Primary BG]
        PARTICLE[ParticleBackground.tsx<br/>Canvas 2D Fallback BG]
        ORB[VoiceOrb.tsx<br/>Central Interaction Point]
        STATUS[StatusBar.tsx<br/>Bottom Status Strip]
        ACTION[ActionFeed.tsx<br/>Tool/Agent Activity Ticker]
    end

    subgraph "Phase: Normal — Overlays (Toggle)"
        CHAT[ChatHistory.tsx<br/>Conversation Log]
        TEXT_IN[TextInput.tsx<br/>Text Message Entry]
        SETTINGS[Settings.tsx<br/>Configuration Panel]
        DASH[Dashboard.tsx<br/>Command Center]
        AGENT_DASH[AgentDashboard.tsx<br/>Agent Task Monitor]
        MEM_EXP[MemoryExplorer.tsx<br/>Memory Browser]
        QUICK[QuickActions.tsx<br/>Command Palette]
        CONN_ERR[ConnectionOverlay.tsx<br/>Error Recovery]
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
    MOOD_CTX --> CHAT
    MOOD_CTX --> TEXT_IN
    MOOD_CTX --> SETTINGS
    MOOD_CTX --> DASH
    MOOD_CTX --> AGENT_DASH
    MOOD_CTX --> MEM_EXP
    MOOD_CTX --> QUICK
    MOOD_CTX --> CONN_ERR

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
    creating --> feature_setup: Animation complete → NexusCore revealed
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
| 90 | MemoryExplorer | Toggle (Ctrl+Shift+M) |
| 50 | AgentCreation | Creating phase only |
| 40 | ChatHistory | Toggle |
| 35 | ActionFeed | Always (bottom-left) |
| 30 | ConnectionOverlay | Error state |
| 20 | VoiceOrb | Always (center) |
| 10 | StatusBar | Always (bottom) |
| 5 | NexusCore / WireframeNetwork | Always (background) |

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
```

### Full IPC Channel Registry

#### Settings Namespace (`window.eve.settings`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `settings:get` | invoke | `() → EveSettings` | Read all settings |
| `settings:set` | invoke | `(partial: Partial<EveSettings>) → void` | Update settings |
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

#### Episodes Namespace (`window.eve.episodes`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `episodes:get-all` | invoke | `() → Episode[]` | All session summaries |
| `episodes:get-recent` | invoke | `(n: number) → Episode[]` | Last N episodes |
| `episodes:search` | invoke | `(query: string, limit: number) → Episode[]` | Semantic search episodes |
| `episodes:save-current` | invoke | `() → Episode` | Force-save current session |

#### Agents Namespace (`window.eve.agents`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `agents:list-tasks` | invoke | `() → AgentTask[]` | All tasks (any status) |
| `agents:cancel` | invoke | `(taskId: string) → void` | Cancel running task |
| `agents:get-types` | invoke | `() → AgentTypeInfo[]` | Available agent types |
| `agents:onUpdate` | on | `(callback: (task: AgentTask) → void) → unsub` | Real-time task updates |

#### Ambient Namespace (`window.eve.ambient`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `ambient:get-context` | invoke | `() → AmbientContext` | Current desktop context |
| `ambient:get-clipboard` | invoke | `() → ClipboardData` | Current clipboard |
| `ambient:onClipboard` | on | `(callback: (data: ClipboardData) → void) → unsub` | Clipboard changes |

#### Desktop Namespace (`window.eve.desktop`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `desktop:get-active-window` | invoke | `() → WindowInfo` | Active window title/app |
| `desktop:list-windows` | invoke | `() → WindowInfo[]` | All open windows |
| `desktop:focus-window` | invoke | `(title: string) → void` | Focus window by title |
| `desktop:launch-app` | invoke | `(name: string) → void` | Launch application |
| `desktop:run-command` | invoke | `(cmd: string) → string` | Execute shell command |

#### Documents Namespace (`window.eve.documents`)
| Channel | Direction | Signature | Purpose |
|---------|-----------|-----------|---------|
| `documents:pick-and-ingest` | invoke | `() → IngestResult` | File picker → ingest |
| `documents:list` | invoke | `() → Document[]` | All ingested documents |
| `documents:search` | invoke | `(query: string) → SearchResult[]` | Search document content |

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

### Consolidation Cycle (6 hours)

```mermaid
sequenceDiagram
    participant Timer as 6hr Timer
    participant Engine as ConsolidationEngine
    participant Claude as Claude Sonnet
    participant MTM as observations.json
    participant LTM as memories.json
    participant Embed as SemanticSearch

    Timer ->> Engine: triggerConsolidation()
    Engine ->> MTM: Read all observations
    Engine ->> LTM: Read all memories

    loop For each observation batch
        Engine ->> Claude: "Analyze these observations.<br/>Score importance. Find duplicates.<br/>Identify merge candidates."
        Claude -->> Engine: Scored + merge instructions
    end

    Engine ->> Engine: Apply promotion threshold
    Engine ->> LTM: Write promoted memories
    Engine ->> MTM: Remove promoted, decay old
    Engine ->> Embed: Re-index new memories

    Note over Engine: Also processes recent episodes<br/>for cross-referencing
```

---

## 8. Data Flow: Tool Execution Chain

```mermaid
graph TD
    GEMINI[Gemini Response<br/>toolCall: name + args] --> HOOK[useGeminiLive.ts<br/>handleToolCall()]

    HOOK --> PRIORITY{Tool Routing<br/>Priority Chain}

    PRIORITY -->|1. Specific handlers| SPECIFIC[Built-in Tool Handlers]
    PRIORITY -->|2. browser_* prefix| BROWSER[Browser Connector]
    PRIORITY -->|3. Connector match| CONNECTOR[Connector Registry]
    PRIORITY -->|4. MCP match| MCP_TOOL[MCP Server Tools]
    PRIORITY -->|5. Fallback| DESKTOP[Desktop Tools]

    subgraph "Built-in Tool Handlers"
        SPECIFIC --> SAVE_MEM[save_memory<br/>→ memory:save IPC]
        SPECIFIC --> ASK_CLAUDE[ask_claude<br/>→ server:ask-claude IPC]
        SPECIFIC --> SETUP_INTEL[setup_intelligence<br/>→ intelligence:setup IPC]
        SPECIFIC --> CREATE_TASK[create_task / list_tasks / delete_task<br/>→ scheduler:* IPC]
        SPECIFIC --> READ_SRC[read_own_source / list_own_files<br/>→ Desktop connector]
        SPECIFIC --> PROPOSE[propose_code_change<br/>→ Desktop connector]
        SPECIFIC --> LAUNCH[launch_app<br/>→ desktop:launch IPC]
        SPECIFIC --> FINALIZE[finalize_agent_identity<br/>→ onboarding:finalize IPC]
        SPECIFIC --> INTAKE[save_intake_responses<br/>→ psych:generate IPC]
        SPECIFIC --> FEATURE[mark_feature_setup_step<br/>→ feature-setup:advance IPC]
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
    end

    subgraph "MCP Tools (namespaced)"
        MCP_TOOL --> MCP1[serverName::toolName]
    end

    subgraph "Desktop Fallback"
        DESKTOP --> WIN[get_active_window]
        DESKTOP --> LIST_WIN[list_windows]
        DESKTOP --> CMD[run_command]
    end

    SAVE_MEM --> RESULT[Tool Result]
    ASK_CLAUDE --> RESULT
    BROWSER --> RESULT
    CONNECTOR --> RESULT
    MCP_TOOL --> RESULT
    DESKTOP --> RESULT

    RESULT --> GEMINI_RESP[Send toolResponse<br/>back to Gemini]
```

### Tool Declaration Sources

| Source | Count | Registration | Namespace |
|--------|-------|-------------|-----------|
| Built-in tools | ~15 | `tools.ts` `buildToolDeclarations()` | None (flat) |
| Onboarding tools | ~5 | `onboarding.ts` `buildOnboardingToolDeclaration()` | None (flat) |
| Browser connector | 4 | `browser-connector.ts` `TOOLS` | `browser_` prefix |
| Desktop connector | 5 | `desktop-tools.ts` `TOOLS` | None (flat) |
| Google Calendar | 4 | `google-calendar.ts` `TOOLS` | `google-calendar:` |
| Gmail | 5 | `gmail.ts` `TOOLS` | `gmail:` |
| Perplexity | 1 | `perplexity.ts` `TOOLS` | `perplexity:` |
| OpenAI services | 3 | `openai-services.ts` `TOOLS` | `openai:` |
| MCP servers | Dynamic | `mcp-manager.ts` `getAllTools()` | `serverName::` |

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
    participant FW as AgentFramework
    participant Queue as Task Queue
    participant Claude as Claude Sonnet
    participant Result as Result Handler

    User ->> Gemini: "Research quantum computing"
    Gemini ->> Hook: toolCall: create_agent_task<br/>{type: 'research', input: '...'}
    Hook ->> FW: createTask(type, input)
    FW ->> Queue: Enqueue {id, type, status: 'queued'}
    FW -->> Hook: taskId

    Note over Queue: Concurrency limit: 3 parallel tasks

    Queue ->> FW: Dequeue next task
    FW ->> FW: Load agent type definition
    FW ->> FW: Build agent-specific prompt
    FW ->> Claude: messages.create({<br/>  model: 'claude-sonnet-4-20250514',<br/>  system: agentPrompt,<br/>  messages: [{role: 'user', content: input}]<br/>})

    loop Streaming response
        Claude -->> FW: Content delta
        FW -->> Queue: Update progress, logs
        FW -->> Hook: IPC event: agent-task-update
        Hook -->> User: ActionFeed card updates
    end

    Claude -->> FW: Final response
    FW ->> Queue: status: 'completed', result: '...'
    FW -->> Hook: IPC event: agent-task-update
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
    participant Creation as AgentCreation
    participant Nexus as NexusCore
    participant Agent as New Agent

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
    App ->> Nexus: Opacity 0 → 1 (2s transition)
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

### NPM Package Dependencies (Key)

```mermaid
graph TD
    subgraph "AI / ML"
        ANTHROPIC["@anthropic-ai/sdk"]
        GOOGLE_AI["@google/generative-ai"]
    end

    subgraph "Desktop Platform"
        ELECTRON[electron 36.x]
        BUILDER[electron-builder]
        VITE_ELECTRON[vite-plugin-electron]
    end

    subgraph "3D / Visual"
        THREE[three 0.183]
        DREI["@react-three/drei"]
        FIBER["@react-three/fiber"]
        POSTPROCESSING[postprocessing]
    end

    subgraph "Frontend"
        REACT[react 19]
        REACT_DOM[react-dom 19]
        VITE[vite 6]
    end

    subgraph "Integration"
        GOOGLEAPIS[googleapis]
        TELEGRAF[telegraf]
        MCP_SDK["@anthropic-ai/mcp-sdk (planned)"]
    end

    subgraph "Utilities"
        CRON[node-cron]
        CHOKIDAR[chokidar]
        GRAY_MATTER[gray-matter]
        AXIOS[axios]
    end

    ELECTRON --> REACT
    FIBER --> THREE
    DREI --> FIBER
    POSTPROCESSING --> THREE
    VITE_ELECTRON --> VITE
    VITE_ELECTRON --> ELECTRON
```

---

## 14. Visual System Architecture

### NexusCore 3D Layer Stack

```mermaid
graph BT
    subgraph "Three.js Scene (NexusCore.tsx)"
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
    end

    subgraph "Trust Boundary: IPC"
        PRELOAD[Preload Script<br/>contextBridge whitelist]
    end

    subgraph "Trust Zone: External APIs"
        GEMINI_API[Gemini API<br/>API Key auth]
        CLAUDE_API[Claude API<br/>API Key auth]
        GOOGLE_API[Google APIs<br/>OAuth2 tokens]
        TELEGRAM_API[Telegram API<br/>Bot token]
    end

    subgraph "Trust Zone: Local Filesystem"
        EVE_DATA[eve-data/<br/>Memory, episodes, tasks]
        SETTINGS[eve-settings.json<br/>API keys, agent config]
        OBSIDIAN_DIR[Obsidian vault<br/>User knowledge base]
    end

    subgraph "Trust Zone: External Processes"
        MCP_PROCS[MCP Server Processes<br/>stdio pipes]
        BROWSER[Browser Extension<br/>WebSocket :52836]
        SHELL[Shell Commands<br/>child_process]
    end

    USER -->|Validated by VAD + UI| RENDERER
    RENDERER -->|Whitelisted channels only| PRELOAD
    PRELOAD -->|Type-safe invoke/on| MAIN

    MAIN -->|API key in header| GEMINI_API
    MAIN -->|API key in header| CLAUDE_API
    MAIN -->|OAuth2 bearer token| GOOGLE_API
    MAIN -->|Bot token| TELEGRAM_API

    MAIN -->|Read/Write JSON| EVE_DATA
    MAIN -->|Read/Write JSON| SETTINGS
    MAIN -->|Read/Write Markdown| OBSIDIAN_DIR

    MAIN -->|stdio/SSE| MCP_PROCS
    MAIN -->|WebSocket local| BROWSER
    MAIN -->|child_process.exec| SHELL

    style PRELOAD fill:#f59e0b,color:#000
    style SETTINGS fill:#ef4444,color:#fff
```

### Security Considerations

| Boundary | Risk | Current Mitigation |
|----------|------|--------------------|
| Preload bridge | Arbitrary IPC | Whitelisted channels in `contextBridge.exposeInMainWorld` |
| Shell execution | Command injection | `run_command` tool — **no sanitization observed** |
| MCP processes | Malicious servers | User-configured only, stdio isolation |
| API keys in settings | Plaintext storage | `eve-settings.json` on local filesystem — **not encrypted** |
| Browser WebSocket | Local network attack | Localhost only (:52836), no auth token |
| Telegram gateway | Message injection | Bot token auth, but messages forwarded to Gemini unsanitized |
| Obsidian sync | Path traversal | Category-based subdirectory mapping, **no path validation observed** |
| OAuth tokens | Token theft | Stored in settings JSON — **not encrypted** |

### Items Flagged for Hardening (Phase 3)

1. **API keys stored in plaintext** — Need encryption at rest (electron safeStorage / keytar)
2. **Shell command execution** — Need command whitelist or sandboxing
3. **Browser WebSocket no auth** — Need shared secret or token validation
4. **Telegram message injection** — Need input sanitization before Gemini injection
5. **Obsidian path traversal** — Need path normalization and jail
6. **OAuth tokens in plaintext** — Need secure credential storage
7. **No CSP headers** — Renderer should have Content Security Policy
8. **Hardcoded user name "Stephen"** — Across 4+ modules, should use settings

---

## Appendix A: File Inventory

### Main Process (`src/main/`)

| File | Lines | Domain | Purpose |
|------|-------|--------|---------|
| `index.ts` | ~600 | Core | Entry point, window creation, IPC hub |
| `server.ts` | ~180 | Core | Anthropic SDK client, Claude API wrapper |
| `gemini-live.ts` | ~350 | Core | Gemini WebSocket manager, audio routing |
| `settings.ts` | ~200 | Core | Persistent JSON settings store |
| `preload.ts` | ~400 | Core | IPC bridge, contextBridge declarations |
| `tools.ts` | ~300 | Core | Gemini tool declarations builder |
| `memory.ts` | ~450 | Memory | 3-tier memory manager |
| `episodic-memory.ts` | ~250 | Memory | Session summarizer via Claude |
| `relationship-memory.ts` | ~200 | Memory | Trust, streaks, inside jokes |
| `memory-consolidation.ts` | ~300 | Memory | 6hr promotion engine |
| `semantic-search.ts` | ~200 | Memory | Gemini embeddings, cosine similarity |
| `obsidian-sync.ts` | ~250 | Memory | Bidirectional vault mirroring |
| `ambient.ts` | ~200 | Intelligence | 30s polling desktop context |
| `sentiment.ts` | ~150 | Intelligence | Mood classification |
| `predictive-intelligence.ts` | ~350 | Intelligence | Briefings, check-ins, emotional support |
| `world-monitor.ts` | ~250 | Intelligence | News, weather, stocks feeds |
| `agent-framework.ts` | ~300 | Agents | Task queue, execution, concurrency |
| `agents/research.ts` | ~150 | Agents | Research agent definition |
| `agents/summarize.ts` | ~120 | Agents | Summarize agent definition |
| `agents/code-review.ts` | ~150 | Agents | Code review agent definition |
| `agents/draft-email.ts` | ~130 | Agents | Email drafting agent definition |
| `agents/index.ts` | ~30 | Agents | Agent type registry |
| `agents/types.ts` | ~50 | Agents | Shared agent types |
| `personality.ts` | ~250 | Identity | System prompt builder, personality config |
| `onboarding.ts` | ~400 | Identity | First-run flow, "Her" screenplay |
| `psychological-profile.ts` | ~200 | Identity | Claude psych analysis |
| `feature-setup.ts` | ~250 | Identity | 9-step guided setup |
| `personality-evolution.ts` | ~150 | Identity | Trait → visual mapping |
| `task-scheduler.ts` | ~250 | Scheduling | Persistent cron tasks |
| `clipboard-monitor.ts` | ~80 | Infrastructure | Polling clipboard changes |
| `session-health.ts` | ~120 | Infrastructure | Uptime, error tracking |
| `desktop-tools.ts` | ~200 | Connectors | Window, app, clipboard tools |
| `browser-connector.ts` | ~300 | Connectors | WebSocket browser extension bridge |
| `google-calendar.ts` | ~350 | Connectors | OAuth2 calendar CRUD |
| `gmail.ts` | ~400 | Connectors | OAuth2 email CRUD |
| `mcp-manager.ts` | ~400 | Connectors | Multi-server MCP protocol |
| `telegram-gateway.ts` | ~250 | Gateway | Bot API message bridge |
| `perplexity.ts` | ~150 | Services | Web search with citations |
| `openai-services.ts` | ~200 | Services | DALL-E, TTS, GPT |

### Renderer (`src/renderer/`)

| File | Lines | Layer | Purpose |
|------|-------|-------|---------|
| `App.tsx` | ~500 | Shell | State machine, phase routing, keyboard shortcuts |
| `main.tsx` | ~10 | Entry | React DOM root |
| `hooks/useGeminiLive.ts` | ~2100 | Hook | Complete Gemini integration, tool routing, audio |
| `hooks/useWakeWord.ts` | ~150 | Hook | "Hey Friday" wake word detection |
| `components/NexusCore.tsx` | ~800 | 3D | Three.js 5-layer visualization |
| `components/VoiceOrb.tsx` | ~400 | UI | Central interaction orb |
| `components/WireframeNetwork.tsx` | ~600 | Canvas | 2D particle network (primary BG) |
| `components/ParticleBackground.tsx` | ~160 | Canvas | 2D particles (fallback BG) |
| `components/ChatHistory.tsx` | ~300 | UI | Conversation display |
| `components/TextInput.tsx` | ~200 | UI | Text message entry |
| `components/Settings.tsx` | ~500 | UI | Configuration panel |
| `components/StatusBar.tsx` | ~200 | UI | Bottom status strip |
| `components/ActionFeed.tsx` | ~350 | UI | Tool/agent activity ticker |
| `components/Dashboard.tsx` | ~500 | UI | Command center overlay |
| `components/AgentDashboard.tsx` | ~525 | UI | Agent task monitor |
| `components/MemoryExplorer.tsx` | ~730 | UI | Memory browser |
| `components/QuickActions.tsx` | ~435 | UI | Command palette |
| `components/ConnectionOverlay.tsx` | ~250 | UI | Error recovery overlay |
| `components/AgentCreation.tsx` | ~350 | UI | Cinematic agent reveal |
| `components/WelcomeGate.tsx` | ~200 | UI | API key entry gate |
| `components/MoodContext.tsx` | ~200 | Context | Mood state provider |
| `components/ErrorBoundary.tsx` | ~195 | Infra | Crash recovery |
| `components/dashboard/ContextCard.tsx` | ~315 | Sub | Live ambient context |
| `components/dashboard/AgentCard.tsx` | ~240 | Sub | Agent summary card |
| `components/dashboard/MoodTimeline.tsx` | ~300 | Sub | SVG mood chart |
| `AudioPlaybackEngine.ts` | ~200 | Audio | Gapless Web Audio scheduling |
| `sound-effects.ts` | ~100 | Audio | Sound effect registry |
| `SessionManager.ts` | ~250 | Infra | 7min timeout, reconnect logic |
| `IdleBehavior.ts` | ~200 | Infra | Tiered idle state machine |

### Total: ~90 source files, ~15,000+ lines of TypeScript

---

## Appendix B: Data File Locations

| File | Format | Size Limit | Purpose |
|------|--------|-----------|---------|
| `eve-data/memories.json` | JSON array | Unlimited | Long-term memory store |
| `eve-data/observations.json` | JSON array | 30 entries | Medium-term observations |
| `eve-data/episodes.json` | JSON array | 200 entries | Session summaries |
| `eve-data/relationship.json` | JSON object | Singleton | Trust, streaks, inside jokes |
| `eve-data/scheduled-tasks.json` | JSON array | Unlimited | Cron task definitions |
| `eve-data/intelligence-topics.json` | JSON array | Unlimited | Research topic configs |
| `eve-data/intelligence-cache.json` | JSON object | Per-topic | Cached research results |
| `eve-settings.json` | JSON object | Singleton | All settings, API keys, agent config |

---

*This is a living document. Update as the architecture evolves.*
