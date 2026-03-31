# Agent Friday — Architecture Overview

> **agent-friday** v3.13.0 | Electron 33 + React 19 + Vite 6 + TypeScript 5.7
> The first fully local, fully encrypted AI operating system.

## Process Model

Agent Friday is an Electron application with two processes communicating via IPC:

```
┌─────────────────────────────────────────────────────────────────┐
│  RENDERER (Chromium)                                            │
│  React 19 + Vite 6                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ FridayCode   │  │ FridayWeather│  │ FridayFiles  │  ...23   │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│  ┌──────────────┐  ┌──────────────────────────────────┐        │
│  │ useGeminiLive│  │ AudioPlaybackEngine (Web Audio)  │        │
│  └──────────────┘  └──────────────────────────────────┘        │
│                                                                 │
│  window.eve.*  (contextBridge — 88 namespaces, 880+ methods)   │
└─────────────────────┬───────────────────────────────────────────┘
                      │ IPC (invoke/handle + send/on)
┌─────────────────────┴───────────────────────────────────────────┐
│  MAIN (Node.js)                                                 │
│  253 TypeScript source files                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ Voice    │ │ Memory   │ │ Security │ │ Intelligence     │  │
│  │ Pipeline │ │ System   │ │ & Vault  │ │ Router           │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ Agents & │ │ Context  │ │ Hardware │ │ Connectors &     │  │
│  │ Delegatn │ │ Graph    │ │ & Setup  │ │ Gateway          │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │
│                     │                                           │
│          ┌──────────┴──────────┐                               │
│          │ Subprocess Binaries │                               │
│          │ whisper-cpp, piper, │                               │
│          │ sherpa-onnx, docker │                               │
│          └─────────────────────┘                               │
└─────────────────────────────────────────────────────────────────┘
         │                    │                    │
    ┌────┴────┐        ┌─────┴─────┐       ┌─────┴──────┐
    │ Ollama  │        │ Cloud APIs│       │ File System│
    │ :11434  │        │ Gemini,   │       │ ~/.nexus-os│
    │ (local) │        │ Anthropic │       │ settings   │
    └─────────┘        └───────────┘       └────────────┘
```

## IPC Bridge Pattern

Every renderer↔main interaction follows this pattern:

```
Renderer Component
  → window.eve.<namespace>.<method>(args)      [types.d.ts]
  → ipcRenderer.invoke('<channel>', args)       [preload.ts]
  → ipcMain.handle('<channel>', handler)        [*-handlers.ts]
  → Singleton service method                    [*.ts]
```

**Critical sync requirement:** `preload.ts` ↔ `types.d.ts` must match exactly.
Vite does NOT type-check — only `npx tsc --noEmit` catches mismatches.

See [ipc-channel-map.md](./ipc-channel-map.md) for the complete 880+ method reference.

## Bounded Contexts

### 1. Voice Pipeline (`src/main/voice/`)
Two parallel voice paths:
- **Gemini Live** — WebSocket to `wss://generativelanguage.googleapis.com`, real-time bidirectional audio
- **Local-first** — Whisper STT → Ollama LLM → Kokoro/Piper TTS (fully offline)

State machine with 13 states manages transitions and fallback.
See [flows/](./flows/) for detailed end-to-end traces.

### 2. Intelligence & LLM Routing (`src/main/providers/`, `src/main/intelligence-router.ts`)
Multi-provider routing: Ollama (local) → Anthropic → OpenRouter → HuggingFace.
CloudGate enforces consent before any cloud escalation.
IntelligenceRouter selects optimal model by task complexity and VRAM budget.

### 3. Memory & Context (`src/main/memory.ts`, `src/main/context-*.ts`)
Three memory tiers (short/medium/long-term) + episodic memory.
Obsidian vault integration for persistent notes.
ContextGraph builds a knowledge graph of entity relationships.
ContextStream provides real-time context updates.

### 4. Security & Integrity (`src/main/integrity/`, `src/main/vault-*.ts`)
- **Vault** — Argon2id KDF + AES-256-GCM, passphrase-only trust root
- **cLaw Attestation** — HMAC signing for data integrity
- **IntegrityManager** — Memory watchdog, safe mode detection
- **ConsentGate** — Explicit consent before cloud/external operations

### 5. Hardware & Setup (`src/main/hardware/`, `src/main/setup/`)
HardwareProfiler → TierRecommender → ModelOrchestrator pipeline.
SetupWizard state machine: idle → detecting → recommending → confirming → downloading → loading → complete.

### 6. Agent & Delegation (`src/main/agents/`, `src/main/agent-network.ts`)
Multi-agent orchestration with trust-scored delegation.
ContainerEngine for sandboxed code execution (Docker or direct subprocess).
AgentNetwork for P2P agent discovery and cross-agent task delegation.

### 6a. Autoresearch & Self-Improvement (`src/main/agents/`, `dev/`)
Six-engine autonomous improvement loop (v3.13.0):
- **IterationEngine** — orchestrates research/improve cycles from dev directives
- **DirectiveLoader** — parses `dev/*.md` markdown directives into engine instructions
- **ResultsLedger** — tracks run history, scores, and regressions
- **PromptEvolver** — mutates and tests prompt variants for quality improvement
- **ModelBreeder** — optimizes model selection across providers
- **SelfImprover** — adjusts `// --- TUNABLE ---` zone constants in source files

Engines initialize in parallel (3 batches) during startup via `index.ts`.

### 7. Personality & Self-Knowledge (`src/main/personality*.ts`, `src/main/friday-profile.ts`)
Personality evolution via calibration signals.
MemoryPersonalityBridge synchronizes memory engagement with personality drift.
PsychologicalProfile tracks agent dimensions.

### 8. Workflows & Commitments (`src/main/workflow-*.ts`, `src/main/commitment-tracker.ts`)
WorkflowRecorder captures user action patterns.
WorkflowExecutor replays recorded workflows.
CommitmentTracker manages deadlines and follow-ups.
DailyBriefingEngine generates contextual briefings.

### 9. Connectors & Gateway (`src/main/connectors/`, `src/main/gateway/`)
30+ tool connectors: Adobe, Office, VSCode, Git, Terminal, 3D, Streaming, etc.
GatewayManager handles multi-channel access (Telegram, Discord, etc.).

### 10. OS Primitives (`src/main/weather.ts`, `src/main/file-*.ts`, `src/main/notes-*.ts`)
Weather (Open-Meteo API + ipapi.co geolocation), Files, Notes, System Monitor.
These power the built-in "Friday" apps in the renderer.

## Singleton Initialization Order

Services initialize in dependency tiers (see `src/main/index.ts`):

| Tier | Services | Timing |
|------|----------|--------|
| 0 | CrashReporter, error wrapping | Pre-app |
| 1 | SettingsManager, VaultManager | Before app.ready |
| 2 | HardwareProfiler | app.whenReady |
| 3 | SetupWizard, ProfileManager | After hardware detect |
| 4 | OllamaLifecycle (polls :11434 every 30s) | After setup |
| 5 | Voice stack (AudioCapture, Whisper, TTS, Pipeline) | Lazy on first use |
| 6 | VisionProvider, ImageUnderstanding | Lazy on first image |
| 7 | CloudGate, IntelligenceRouter, LLMClient | After providers init |
| 8 | MemoryManager, ContextGraph, SemanticSearch | After LLM available |
| 9 | Personality, FridayProfile | After memory |
| 10+ | Agents, Delegation, Autoresearch (3-batch parallel), Connectors, Gateway | After core services |

**Circular dependency resolution:** `memory.ts` ↔ `integrity.ts` ↔ `trust-graph.ts` use `_lazyLoad()` pattern.

## Renderer Apps

23 built-in apps in `src/renderer/components/apps/`:
FridayCalc, FridayCanvas, FridayMaps, FridayNotes, FridayTasks, FridayCalendar,
FridayBrowser, FridayForge, FridayComms, FridayMonitor, FridayGallery, FridayMedia,
FridayNews, FridayGateway, FridayDocs, FridayTerminal, FridayContacts, FridayStage,
FridayCamera, FridayFiles, FridayRecorder, FridayCode, FridayWeather.

## Related Documentation

- [IPC Channel Map](./ipc-channel-map.md) — Complete reference of all 88 namespaces and 880+ IPC methods
- [External Dependencies](./external-dependencies.md) — Binaries, APIs, data directories, env vars
- [Flows](./flows/) — End-to-end flow documentation with Mermaid diagrams
