# Nexus OS v2.0 — Grand Architecture

> **Codename**: Asimov Layer
> **Author**: FutureSpeak.AI
> **Date**: 2026-02-26
> **Scope**: Complete integration plan synthesizing all researched technologies into Agent Friday's evolution from desktop AI assistant to the user's personal operating system — the Asimov Layer atop the internet.

---

## 0. Vision Statement

Agent Friday becomes the **Asimov Layer** — a new stratum of compute sitting between the user and every digital service they touch. Every stock OS application is replaced by an Agent Friday equivalent that is:

1. **Usable by humans** through a rich UI
2. **Usable by agents** through tool interfaces and A2A protocols
3. **Usable by voice** through the Gemini Live audio pipeline
4. **Governed by Asimov's Laws** — the agent cannot cause harm, must obey the user, and must preserve itself

This is not a chatbot with plugins. This is an operating system where an Asimov-compliant agent sits at the kernel level, mediating every interaction between the user and the digital world.

---

## 1. Architectural Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                        │
│  NexusCore 3D Desktop │ Holographic Display │ Voice Orb     │
│  Audio Visualizer     │ App Windows (Office) │ Mood System  │
├─────────────────────────────────────────────────────────────┤
│                    AGENT INTELLIGENCE                        │
│  Gemini Live (Voice)  │ Claude (Reasoning)  │ OpenRouter    │
│  Psychological Model  │ Personality Engine  │ Sentiment     │
│  Predictive Intel     │ Self-Improvement    │ Evolution     │
├─────────────────────────────────────────────────────────────┤
│                    ASIMOV SAFETY LAYER                       │
│  Core Laws Engine     │ NASSE Risk Scoring  │ Red-Team      │
│  Memory Watchdog      │ HMAC Integrity      │ Trust Engine  │
│  Financial Guards     │ Audit Log           │ Intent Traces │
├─────────────────────────────────────────────────────────────┤
│                    WORKFLOW & ORCHESTRATION                   │
│  Agent Runner         │ Agent Teams         │ Orchestrator  │
│  Feature Setup        │ Workflow Engine     │ Interrupt/     │
│  Circuit Breaker      │ Retry/Timeout       │ Resume        │
├─────────────────────────────────────────────────────────────┤
│                    TOOL & SERVICE LAYER                       │
│  MCP Client/Server    │ Metorial Hub        │ ACP Commerce  │
│  UI-TARS Automation   │ SOC Bridge          │ Desktop Tools │
│  Connector Registry   │ Terminal Sessions   │ PowerShell    │
│  ADK Orchestrator     │ World Monitor       │ PageIndex     │
├─────────────────────────────────────────────────────────────┤
│                    COMMUNICATION FABRIC                       │
│  Gateway Manager      │ Telegram Adapter    │ Email/Gmail   │
│  Browser Extension    │ A2A Protocol        │ Calendar      │
│  Trust Engine         │ Persona Adapter     │ Call Integ.   │
│  ANP (Decentralized)  │ Roundcube (IMAP)    │ Agent Cards   │
├─────────────────────────────────────────────────────────────┤
│                    MEMORY & KNOWLEDGE                         │
│  Short/Medium/Long    │ Episodic Memory     │ Semantic Srch │
│  Relationship Memory  │ Obsidian Sync       │ Document Ing. │
│  Project Awareness    │ Clipboard Intel     │ Vector Store  │
├─────────────────────────────────────────────────────────────┤
│                 PROGRAM LOADING & PLATFORM                    │
│  GitLoader (Floppy)   │ Settings (JSON)     │ SQLite/LanceDB│
│  Electron 36          │ Node.js 22          │ File System   │
│  Repo→Module Compiler │ Sandbox Runtime     │ Dep Resolver  │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Integration Map — All Technologies

### 2.1 UI-TARS Desktop (ByteDance) — Vision-Language GUI Automation

**What it is**: A TypeScript-native framework that uses a vision-language model to perceive screenshots, plan actions, and execute GUI operations (click, type, scroll, drag) via NutJS.

**Integration Strategy**: Replace the current Python SOC bridge (`soc-bridge.ts` → `soc_bridge.py`) with a TypeScript-native `@ui-tars/sdk` operator. This eliminates the Python dependency, reduces IPC overhead (no more JSONL stdin/stdout), and gives Agent Friday a proper "eyes on screen" capability.

**Architecture**:
```
src/main/
  ui-tars/
    operator.ts          — NutJS-based screen operator (mouse, keyboard, scroll)
    vision-provider.ts   — Screenshot → VLM analysis (Gemini 2.5 Flash w/ vision)
    action-parser.ts     — Parse VLM text output → structured GUIAction[]
    safety-filter.ts     — Asimov Law check before every physical action
    session-manager.ts   — Tab/window tracking, focus management
    index.ts             — GUIAgent orchestrator (screenshot → think → act loop)
```

**Key Design Decisions**:
- **VLM Provider**: Use Gemini 2.5 Flash with vision (already integrated) rather than pulling in a separate VLM model. Send screenshots as base64 images in the prompt.
- **Safety**: Every physical action (click, type, keystroke) passes through `safety-filter.ts` which enforces: no typing passwords/credit cards, no clicking "delete all" without user confirmation, no interactions with financial transaction buttons without explicit approval.
- **Fallback**: If VLM analysis confidence < 0.6, fall back to accessibility tree inspection (Windows UI Automation API via `powershell.ts` connector).
- **Integration with existing SOC**: Keep `soc-bridge.ts` as a fallback adapter but make UI-TARS the primary automation engine. SOC bridge only activates when UI-TARS reports it cannot handle a specific app.

**MCP Surface**: Expose as MCP tools so external agents can also drive the desktop:
- `ui_tars.screenshot` — capture and analyze current screen
- `ui_tars.click` — click element by description or coordinates
- `ui_tars.type` — type text into focused element
- `ui_tars.find` — locate UI element and return bounding box
- `ui_tars.execute_plan` — multi-step GUI workflow

---

### 2.2 Holographic 3D Display — Face-Tracked Parallax

**What it is**: Using MediaPipe FaceLandmarker via webcam to track the user's head/iris position, then applying off-axis projection to NexusCore's Three.js scene so the 3D desktop appears to have physical depth — like looking through a window into a holographic space.

**Integration Strategy**: Add a face-tracking subsystem to the renderer that feeds head position data into NexusCore's camera system, creating a parallax effect that makes the cube desktop feel like it exists in real 3D space behind the screen.

**Architecture**:
```
src/renderer/
  tracking/
    face-tracker.ts      — MediaPipe FaceLandmarker wrapper, webcam capture
    head-pose.ts         — Extract x/y/z head position from 478 landmarks
    iris-tracker.ts      — Sub-pixel iris position from landmarks 468/473
    smoothing.ts         — EMA filter (factor 0.12) to prevent jitter
    off-axis-camera.ts   — THREE.PerspectiveCamera frustum modifier
    index.ts             — Exports useHeadTracking() hook
```

**Key Design Decisions**:
- **MediaPipe loading**: Use `@mediapipe/tasks-vision` WASM module, loaded on-demand only when user enables holographic mode (not at startup — too expensive).
- **Camera modification**: Don't create a separate camera. Modify NexusCore's existing `THREE.PerspectiveCamera` using `camera.setViewOffset()` — this shifts the frustum based on head position without breaking any existing scene setup.
- **Performance budget**: Face tracking at 15fps (not 30/60 — diminishing returns for parallax). Run in a Web Worker if possible, or on the main thread with `requestAnimationFrame` throttled.
- **Smoothing is critical**: Raw landmark data is noisy. Apply exponential moving average (EMA) with factor 0.12 for position, 0.08 for rotation. This gives smooth parallax without perceivable lag.
- **Depth mapping**: Head Z-distance (from camera) maps to parallax intensity. Close to screen = subtle effect. Further away = more dramatic parallax. This prevents nausea-inducing over-correction.
- **Privacy**: All face data stays local. No frames leave the renderer process. MediaPipe runs entirely in-browser WASM — no cloud API calls.

**NexusCore Changes**:
```typescript
// In NexusCore.tsx, add to the animation loop:
if (headTrackingEnabled && headPosition) {
  const parallaxScale = 0.15; // Subtle — don't make people sick
  camera.setViewOffset(
    window.innerWidth, window.innerHeight,
    headPosition.x * parallaxScale * window.innerWidth,
    headPosition.y * parallaxScale * window.innerHeight,
    window.innerWidth, window.innerHeight
  );
}
```

---

### 2.3 Audio Visualization — projectM + MusicDNA Fusion

**What it is**: Two complementary audio visualization approaches:
- **projectM**: MilkDrop-style psychedelic visualizations using feedback loops, per-pixel mesh warping, and audio-reactive shaders
- **MusicDNA**: DNA helix geometry where two intertwining strands represent different audio channels

**Integration Strategy**: Build a Three.js/GLSL audio visualization system that lives inside NexusCore as an optional visual layer. The core technique is the **feedback loop** — render the previous frame as a texture, warp it based on audio data, and composite new elements on top. This creates the flowing, evolving visual style of MilkDrop.

**Architecture**:
```
src/renderer/
  audio-viz/
    analyzer.ts          — Web Audio API AnalyserNode, 3-band decomposition
    feedback-loop.ts     — Ping-pong FBO (FrameBuffer Object) with UV warp shader
    warp-shader.glsl     — Per-pixel displacement driven by bass/mid/treb
    dna-helix.ts         — Dual-strand geometry (user voice vs agent voice)
    particle-field.ts    — Audio-reactive particle system overlay
    preset-manager.ts    — Visual preset loading/transitions
    index.ts             — AudioVisualizer component + useAudioViz() hook
```

**Key Design Decisions**:
- **Audio Source**: Tap into the existing AudioPlaybackEngine (agent voice output) AND the mic capture stream (user voice). This gives us two independent audio signals for the DNA helix: one strand = user, one strand = agent.
- **Three-Band Decomposition**: Split audio into bass (20-250Hz), mid (250-4000Hz), treble (4000-16000Hz) + attenuated variants. Bass drives large-scale movement, mid drives medium features, treble drives fine detail and sparkle.
- **Feedback Loop Implementation**: Use two `THREE.WebGLRenderTarget`s in a ping-pong pattern. Each frame: read from target A, apply warp shader (UV displacement based on audio + time), render to target B, swap. This creates the flowing "memory" effect where visual history trails behind.
- **DNA Helix**: Parametric tube geometry (`THREE.TubeGeometry`) with two intertwined sinusoidal paths. Radius modulated by real-time audio amplitude. Color by frequency band. The two strands literally represent the conversation — user and agent voices intertwined.
- **Integration with NexusCore**: The audio viz renders to a separate render target and is composited as a background layer behind the cube desktop, or as a floating element within the 3D scene. Controlled by MoodContext — when mood.intensity is high, viz is more prominent.
- **Performance**: Target 60fps. The feedback loop shader is the most expensive part. Keep mesh resolution at 64x64 (not the 96x96 that projectM uses). Total GPU budget: <4ms per frame.

---

### 2.4 Metorial — MCP Infrastructure Hub (600+ Integrations)

**What it is**: A YC-backed platform providing 600+ managed MCP tool integrations with OAuth handling, session management, and 33 first-party servers for major services (Gmail, Slack, GitHub, Google Calendar, Stripe, Notion, Linear, Discord, etc.).

**Integration Strategy**: Metorial becomes Agent Friday's **universal service connector** — instead of building and maintaining individual connectors for each cloud service, we route through Metorial's managed infrastructure for cloud APIs while keeping local connectors (terminal, filesystem, PowerShell) native.

**Architecture**:
```
src/main/
  metorial/
    client.ts            — Metorial Node.js SDK wrapper
    auth-manager.ts      — OAuth flow delegation (Metorial handles token refresh)
    tool-registry.ts     — Dynamic tool discovery from Metorial catalog
    session-bridge.ts    — Map Agent Friday sessions → Metorial sessions
    local-override.ts    — Priority routing: local connectors > Metorial
    index.ts             — MetorialHub singleton
```

**Key Design Decisions**:
- **Hybrid Architecture**: Local connectors (`terminal-sessions.ts`, `powershell.ts`, `ui-automation.ts`, filesystem ops) remain native — they're faster and don't need cloud intermediation. Cloud service connectors (`gmail`, `calendar`, `slack`, `github`, `stripe`, `notion`) route through Metorial.
- **Self-Hosting Option**: Metorial can be self-hosted via Docker Compose. For privacy-conscious users, Agent Friday's settings will include a "Metorial Mode" toggle: `cloud` (use metorial.io) vs `self-hosted` (point to local Docker instance).
- **Dynamic Tool Discovery**: On startup, query Metorial's tool catalog. Register discovered tools into Agent Friday's existing `connectorRegistry`. This means the agent doesn't need to know about every service — Metorial provides them dynamically.
- **OAuth Delegation**: When the user wants to connect Gmail, Metorial handles the full OAuth2 flow (consent screen, token exchange, refresh). Agent Friday just stores the session token, not individual service tokens. This reduces our security surface dramatically.
- **Existing Connector Migration**: Gradually migrate `calendar.ts`, `communications.ts` (Gmail), and future service connectors to use Metorial as the backend. Keep existing code as fallback for offline/self-hosted scenarios.

**Migration Path**:
1. Phase 1: Install Metorial SDK, build `client.ts` wrapper, test with 3 services (Gmail, Calendar, GitHub)
2. Phase 2: Build `tool-registry.ts` for dynamic discovery, wire into `connectorRegistry`
3. Phase 3: Migrate existing calendar/gmail connectors to use Metorial backend
4. Phase 4: Add 20+ new services (Slack, Notion, Linear, Discord, Stripe) via Metorial catalog
5. Phase 5: Self-hosting option in settings

---

### 2.5 Agentic Commerce Protocol (ACP) — Safe Transactions

**What it is**: An OpenAI+Stripe standard for agent-to-merchant transactions. Defines stateful checkout sessions, Shared Payment Tokens (SPTs) for delegated payment, idempotency, capability negotiation, and intent traces on cancellation.

**Integration Strategy**: ACP becomes Agent Friday's **financial interaction layer**. When the agent needs to make a purchase, book a service, or handle any monetary transaction, it goes through the ACP protocol — which enforces user approval, transaction limits, and full audit trails. This directly implements Asimov's First Law for financial interactions.

**Architecture**:
```
src/main/
  commerce/
    acp-client.ts        — ACP REST client (checkout sessions, SPTs)
    payment-vault.ts     — Encrypted local storage for SPTs (never plaintext)
    transaction-guard.ts — Asimov First Law enforcement for financial actions
    intent-tracer.ts     — Records why each transaction was initiated
    checkout-flow.ts     — Multi-step checkout orchestration
    merchant-registry.ts — Known/trusted merchant catalog
    types.ts             — ACP protocol types
    index.ts             — CommerceEngine singleton
```

**Key Design Decisions**:
- **Asimov First Law = Financial Safety**: `transaction-guard.ts` enforces:
  - No transaction above user-defined limit without explicit voice/text confirmation
  - No recurring payments without upfront disclosure
  - No transaction to unknown merchants without user review
  - Full intent trace on every transaction (why was this initiated, what was the user's goal)
  - Automatic cancellation if any step violates safety constraints
- **Shared Payment Tokens**: Users never give Agent Friday their credit card. Instead, they create SPTs through Stripe (via the ACP flow) with built-in spending limits, merchant restrictions, and expiration. The agent holds only the SPT — a constrained, revocable delegation.
- **Idempotency**: Every transaction gets a unique idempotency key. If the agent retries (network failure, timeout), the same operation won't double-charge.
- **Capability Negotiation**: Before attempting a purchase, the agent queries the merchant's ACP endpoint to discover what payment methods, currencies, and flows are supported. No blind attempts.
- **Audit Trail**: Every transaction — successful or failed — is logged to `gateway/audit-log.ts` with full intent traces. The user can review all financial activity in the Agent Office UI.

---

### 2.6 NVIDIA NeMo Agent Toolkit — Safety & Evaluation

**What it is**: A framework-agnostic Python meta-layer for profiling, evaluating, and optimizing agents. Key component: NASSE safety engine (demonstrated 95% risk reduction). Also includes red-teaming capabilities, A2A protocol support, and hyperparameter optimization.

**Integration Strategy**: Extract NASSE's safety scoring methodology and red-teaming patterns into Agent Friday's TypeScript integrity system. We don't run NeMo as a Python service — we port the key algorithms and evaluation frameworks into native TypeScript modules.

**Architecture**:
```
src/main/
  integrity/
    core-laws.ts         — [EXISTS] Asimov's Three Laws enforcement
    hmac.ts              — [EXISTS] Binary integrity verification
    memory-watchdog.ts   — [EXISTS] Memory tampering detection
    nasse-scorer.ts      — [NEW] NASSE-inspired risk scoring for every agent action
    red-team-engine.ts   — [NEW] Adversarial testing for personality/tool use
    action-classifier.ts — [NEW] Classify actions by risk level (green/yellow/red)
    safety-report.ts     — [NEW] Generate periodic safety audit reports
    types.ts             — [EXISTS] Extend with NASSE types
    index.ts             — [EXISTS] Add NASSE + red-team registration
```

**Key Design Decisions**:
- **NASSE Risk Scoring**: Before every tool execution, the action passes through `nasse-scorer.ts` which evaluates:
  - **Content Safety**: Is the action's output potentially harmful? (text analysis)
  - **Scope Creep**: Does this action exceed the original user intent? (intent trace comparison)
  - **Resource Risk**: Could this action cause irreversible resource consumption? (file deletion, API calls with costs, etc.)
  - **Privacy Risk**: Does this action expose user data to third parties? (data flow analysis)
  - Risk score 0.0-1.0. Actions scoring > 0.7 require explicit user confirmation. Actions scoring > 0.9 are blocked outright.
- **Red-Team Engine**: Periodic self-testing:
  - Generate adversarial prompts designed to bypass safety guardrails
  - Test tool-use sequences that could chain into harmful outcomes
  - Verify personality stays within configured bounds (no jailbreaking)
  - Run on a schedule (weekly) or on-demand from settings
  - Results logged to safety reports, surfaced to user on request
- **Action Classification**: Three tiers:
  - **Green**: Read-only operations, memory queries, information retrieval → auto-approve
  - **Yellow**: Write operations, external API calls, file modifications → log + allow (but monitor)
  - **Red**: Financial transactions, account changes, bulk operations, irreversible actions → require confirmation
- **Port, Don't Import**: NeMo is Python. We port the algorithms (scoring formulas, classification logic, red-team prompt templates) into TypeScript. No Python runtime dependency.

---

### 2.7 Neuron AI — Workflow Architecture Patterns

**What it is**: A PHP agent framework (not directly usable) with excellent architectural patterns: Agent-as-Workflow pipelines, typed Node/Event routing, interrupt/resume with persistence, and structured output with schema validation.

**Integration Strategy**: Extract three key architectural patterns and implement them in the existing agent framework (`src/main/agents/`):

**Pattern 1: Agent-as-Workflow Pipeline**
```
src/main/agents/
  workflow/
    pipeline.ts          — Composable node pipeline (Input → Process → Output)
    node.ts              — Base WorkflowNode class with typed input/output
    events.ts            — Typed event bus for node-to-node communication
    registry.ts          — Node type registry (register custom nodes)
```

Currently, `agent-runner.ts` treats agents as simple task→result functions. The workflow pattern allows multi-step agent tasks to be defined as composable pipelines where each node can be independently tested, retried, and monitored.

**Pattern 2: Interrupt/Resume with Persistence**
```
src/main/agents/
  workflow/
    checkpoint.ts        — Serialize workflow state to disk
    interrupt.ts         — Human-in-the-loop interruption points
    resume.ts            — Restore workflow from checkpoint + continue
```

When an agent task needs human input mid-execution (e.g., "I found 3 options, which do you prefer?"), the workflow checkpoints its state, pauses, sends the question to the user, and resumes from exactly where it left off when they respond. No lost context, no re-execution.

**Pattern 3: Structured Output with Retries**
```
src/main/agents/
  workflow/
    schema-validator.ts  — Zod-based output validation for LLM responses
    retry-strategy.ts    — Exponential backoff with schema-aware retry
```

When an LLM returns malformed JSON or misses required fields, automatically retry with the validation error fed back into the prompt. Up to 3 retries with increasing specificity in the error feedback.

---

### 2.8 AgentHub-BE — Resilience Patterns

**What it is**: A Python/FastAPI RAG platform with valuable resilience patterns: circuit breaker, retry, timeout, Agent Registry with Factory pattern, Tool Registry with YAML-driven enable/disable.

**Integration Strategy**: Port three resilience patterns into Agent Friday's core infrastructure:

**Pattern 1: Circuit Breaker**
```
src/main/resilience/
    circuit-breaker.ts   — Per-service circuit breaker (closed → open → half-open)
```

When an external service (Gemini, Claude, Gmail API, etc.) fails repeatedly, the circuit breaker trips open — preventing further calls and returning cached/fallback responses immediately. After a cooldown, it allows a single test call (half-open). If that succeeds, circuit closes. If it fails, back to open.

Thresholds: 5 failures in 60 seconds → open. 30 second cooldown → half-open.

**Pattern 2: Retry with Backoff**
```
src/main/resilience/
    retry.ts             — Configurable retry with jitter and exponential backoff
```

Wraps any async operation with: initial delay 200ms, factor 2, max 3 retries, random jitter ±20%. Knows which errors are retryable (network timeout, 429, 503) vs permanent (401, 404, validation error).

**Pattern 3: Request Timeout**
```
src/main/resilience/
    timeout.ts           — AbortController-based timeout wrapper
```

Every external call gets a timeout. Defaults: Gemini WebSocket → 30s, Claude API → 60s, tool execution → 120s, file operations → 10s. Timeouts are configurable per-service.

**Integration Point**: Wrap the existing `server.ts` (Anthropic calls), `openrouter.ts`, and `connectors/registry.ts` call patterns with the circuit breaker + retry + timeout stack. This makes Agent Friday resilient to API outages without any UX changes.

---

### 2.9 Personality Evolution — Visual Uniqueness Over Time

**What it is**: A system where each agent's NexusCore desktop gradually becomes visually unique based on the agent's personality traits and how long they've been alive.

**Architecture**:
```
src/main/
  personality-evolution.ts  — [EXISTS in settings types, needs implementation]
```

**Trait → Visual Mapping**:
| Trait Category | Visual Parameter | Example |
|---|---|---|
| warm, empathetic, caring | Primary hue → amber/gold (30-50deg), glow intensity ↑ | Warm sunset tones |
| analytical, precise, sharp | Primary hue → cyan/blue (180-220deg), particle speed ↑ | Cool digital feel |
| playful, witty, spontaneous | Cube fragmentation ↑, particle chaos ↑, secondary hue shift | Dynamic, unpredictable |
| calm, steady, grounded | Turbulence ↓, core scale ↑, dust density ↑ | Deep, settled presence |
| creative, artistic | Hue rotation speed ↑, helix amplitude ↑ | Flowing, evolving colors |
| protective, loyal | Core scale ↑, glow radius ↑, warm secondary | Solid, enveloping |

**Maturity Factor**: `Math.min(sessionCount / 50, 1.0)` — visual parameters interpolate from default (session 0) to fully-evolved (session 50+). The desktop becomes more "itself" over weeks of use. Early sessions look mostly standard. By session 50, the agent's visual identity is unmistakable.

**Implementation**: On each session start:
1. Read `personalityEvolution` from settings
2. Increment `sessionCount`
3. Recompute all visual parameters based on traits + maturity
4. Pass as props to NexusCore: `<NexusCore evolutionState={evolutionState} />`
5. NexusCore applies via `THREE.MathUtils.lerp()` between defaults and evolved values

---

### 2.10 A2A Protocol — Distributed Agent Networks (Full Spec)

**What it is**: An open protocol under the Linux Foundation (contributed by Google, April 2025) that enables AI agents built on different frameworks to discover, communicate with, and delegate work to each other over standard HTTP. Uses JSON-RPC 2.0 as its wire format.

**Architecture**:
```
src/main/
  a2a/
    server.ts            — A2A HTTP endpoint (Agent Friday as a service)
    client.ts            — A2A client (discover + invoke remote agents)
    agent-card.ts        — Publish capabilities at /.well-known/agent-card.json
    task-manager.ts      — Task lifecycle state machine (submitted→working→completed)
    stream-handler.ts    — SSE streaming for TaskStatusUpdateEvent/TaskArtifactUpdateEvent
    webhook-handler.ts   — Async push notifications for long-running tasks
    trust-verifier.ts    — Verify remote agent identity + Asimov compliance
    types.ts             — Full A2A protocol types (Task, Message, Part, Artifact)
```

**Protocol Details**:
- **Agent Cards**: JSON metadata at `/.well-known/agent-card.json` declaring identity, skills, endpoint URL, and auth requirements (API key, OAuth 2.0, OpenID Connect). This is how agents discover each other.
- **Task State Machine**: `submitted → working → input-required → completed | failed | canceled`. Terminal states are immutable. The `input-required` state supports human-in-the-loop pausing.
- **Message/Part Model**: Communication via `Message` objects (role: "user" or "agent") containing `Part` units: `TextPart`, `FilePart`, `DataPart`. Task outputs are `Artifact` objects also composed of Parts.
- **Three Interaction Modes**: (1) Synchronous request/response, (2) SSE streaming for incremental updates, (3) Async webhook POST for disconnected long-running tasks.
- **SDK**: JavaScript SDK available (`a2a-sdk`).

**Key Design Decisions**:
- **Agent Friday as A2A Server**: Expose capabilities as an Agent Card — skills include "desktop automation," "calendar management," "email drafting," "code analysis," "file management." External orchestrators (Google Workspace, Vertex AI, other agents) can delegate tasks.
- **Agent Friday as A2A Client**: When Agent Friday encounters a task outside its capabilities, discover and delegate to specialized remote agents. The `input-required` state maps directly to Agent Friday's existing "awaiting user confirmation" UI patterns.
- **SSE for Real-Time**: Use SSE streaming when delegating to remote agents so the user sees incremental progress in the VoiceOrb/chat, not just a final result.
- **Trust + Asimov Propagation**: Before accepting or delegating, verify the remote agent's identity. Asimov safety constraints propagate via task description metadata. Non-compliant remote agents are rejected.

---

### 2.11 Agent Network Protocol (ANP) — Decentralized Agent Identity

**What it is**: An open-source protocol aspiring to be "the HTTP of the Agentic Web." Built on W3C Decentralized Identifiers (DID) so agents authenticate and communicate without any centralized registry or platform dependency.

**Architecture**:
```
src/main/
  anp/
    did-manager.ts       — Generate/store Agent Friday's DID key pair
    did-resolver.ts      — Resolve remote agent DID documents
    encrypted-channel.ts — End-to-end encrypted communication via DID keys
    meta-protocol.ts     — Negotiate application protocol per-connection
    capability-file.ts   — Publish/serve Agent Friday's capability description (JSON-LD)
    types.ts             — ANP + DID protocol types
```

**Key Design Decisions**:
- **Sovereign Identity**: Agent Friday gets a W3C DID-based identity — a cryptographic key pair that proves identity without OAuth, API keys, or any central authority. This is the agent's "digital passport."
- **Complementary to A2A**: A2A assumes HTTP/enterprise infrastructure (OAuth, API keys). ANP assumes nothing — agents authenticate via cryptographic signatures against public DID registries. Use A2A for enterprise/cloud agents, ANP for peer-to-peer/personal agents.
- **Meta-Protocol Negotiation**: When connecting to an unknown agent, ANP's meta-protocol layer lets both sides agree on which application protocol to use (A2A, REST, or domain-specific). Agent Friday can speak any protocol a remote agent supports.
- **End-to-End Encryption**: All ANP communication is encrypted using the agents' DID key pairs. No intermediary can read the messages — this is critical for privacy when agents exchange personal data.
- **Use Cases**: Connecting to a user's doctor's scheduling agent, a local business's booking agent, IoT device agents, or any personal agent that isn't registered with a cloud platform.

---

### 2.12 Google Agent Development Kit (ADK) — Orchestration Patterns

**What it is**: Google's open-source, code-first framework (April 2025) for building production-grade multi-agent systems. Model-agnostic but Gemini-optimized. Provides all primitives needed to go from a single agent to a coordinated hierarchy.

**Architecture**:
```
src/main/
  adk/
    runner-adapter.ts    — Adapt ADK Runner pattern to Agent Friday's agent-runner
    session-bridge.ts    — Map Agent Friday sessions ↔ ADK Sessions
    memory-adapter.ts    — Bridge ADK MemoryService to Agent Friday's memory system
    mcp-toolset.ts       — Use ADK MCPToolset for auto-discovery of MCP tools
    compaction.ts        — ADK EventsCompactionConfig for context window management
    agent-types.ts       — Implement Sequential/Parallel/Loop agent patterns
    types.ts             — ADK type definitions
```

**What We Adopt**:
- **Agent Composition Patterns**: ADK's `SequentialAgent` (pipeline), `ParallelAgent` (fan-out), `LoopAgent` (repeat-until) map directly onto Agent Friday's multi-step task decomposition. Instead of treating every agent task as a flat function call, tasks become composable pipelines.
- **MCPToolset**: ADK's auto-discovery mechanism for MCP servers. Replace custom MCP wiring in Agent Friday with MCPToolset — it connects to any MCP server, discovers tools, and registers them automatically.
- **EventsCompactionConfig**: Automatic context summarization when conversation history exceeds window limits. Critical for long Agent Friday sessions where the agent has been alive for hours.
- **Session + State Primitives**: ADK's `Session` (conversation thread with Events and mutable State) maps onto Agent Friday's existing session management but with better state isolation between concurrent tasks.

**What We Don't Adopt**: ADK's deployment layer (Cloud Run, Vertex AI) — Agent Friday runs on Electron, not cloud. ADK's Python-first SDK — we port patterns to TypeScript.

---

### 2.13 World Monitor — Real-Time Global Intelligence

**What it is**: An open-source AI-powered global intelligence dashboard (15,900+ stars) aggregating 36+ data layers — conflicts, military bases, undersea cables, AI datacenters, earthquakes, fires, cyber threats, financial markets — with 150+ RSS feeds and live tracking. Available as web SPA, Tauri desktop app, and PWA.

**Architecture**:
```
src/main/
  world-monitor/
    sidecar.ts           — Launch World Monitor Node.js sidecar as local MCP server
    data-layer.ts        — Query specific data layers (conflicts, markets, infrastructure)
    rss-aggregator.ts    — Tap into 150+ curated RSS feeds with keyword matching
    cii-scorer.ts        — Country Instability Index (23 weighted signals)
    briefing-builder.ts  — Generate morning/evening intelligence briefings
    alert-engine.ts      — Push alerts when user-tracked keywords trigger
    types.ts             — World Monitor data types
```

**Key Design Decisions**:
- **MCP Surface**: Run World Monitor's Node.js sidecar as a local MCP server. Agent Friday calls `world_monitor.query("South China Sea threat level")` and gets structured, real-time answers grounded in live signals.
- **Three.js Globe Panel**: World Monitor's deck.gl/MapLibre globe rendered as a named window in Agent Friday's Agent Office. "Show me the world map" opens it as a floating 3D panel.
- **Proactive Briefings**: Agent Friday's memory stores user-relevant regions/topics. The agent proactively surfaces morning briefings: "3 new events matching your tracked keywords overnight."
- **Decision Context for Claude**: When drafting communications or making recommendations involving geopolitics/supply chain/markets, Claude queries World Monitor's data layer to ground reasoning in live signals, not training-data cutoff.
- **LLM Fallback Chain**: World Monitor's 4-tier LLM chain (Ollama → Groq → OpenRouter → browser Transformers.js) aligns with Agent Friday's existing multi-model architecture. Share the OpenRouter connection.
- **Finance Layer**: 92 stock exchanges, 13 central banks, crypto pricing, Polymarket prediction markets — gives Agent Friday real-time financial awareness for ACP commerce decisions.

---

### 2.14 Roundcube — Self-Hosted Email Sovereignty

**What it is**: A mature (13,570 commits, 123 releases), production-grade open-source IMAP email client. Full-featured: MIME support, address book, folder management, threading, search, spell check. PHP backend.

**Architecture**:
```
src/main/
  roundcube/
    imap-client.ts       — Direct IMAP connection to self-hosted Roundcube server
    smtp-sender.ts       — Send mail via Roundcube's configured SMTP relay
    inbox-watcher.ts     — Real-time inbox polling + event emission for new mail
    ai-triage.ts         — Claude-powered email classification and priority scoring
    compose-assistant.ts — AI-assisted email drafting via Claude
    plugin-bridge.ts     — Hook into Roundcube plugin API for bidirectional integration
    types.ts             — Email message types
```

**Key Design Decisions**:
- **Email Sovereignty**: Roundcube + Postfix + Dovecot gives Agent Friday a fully self-hosted email channel (`friday@[user-domain]`). No dependence on Gmail/Outlook APIs. User owns their data.
- **Gateway Integration**: Roundcube becomes the email leg of Agent Friday's gateway, alongside Telegram/SMS/calendar. All communication channels converge into a single agent-mediated inbox.
- **AI Triage**: Claude scores incoming emails by urgency/importance/category. Agent Friday surfaces the important ones proactively: "You have a time-sensitive email from your accountant about tax filing."
- **Compose with Claude**: When the user says "reply to that email," Claude drafts a response calibrated to the user's psychological profile and communication style, then sends via Roundcube's SMTP.
- **Dual-Mode**: Self-hosted Roundcube for privacy-first users. Metorial Gmail connector for users who prefer existing Gmail. Both feed into the same gateway.
- **Plugin API**: Inject an "Ask Agent Friday" button into Roundcube's compose UI for AI-assisted drafting without leaving the webmail interface.

---

### 2.15 Pixel Agents — 3D Agent Avatars in NexusCore

**What it is**: A VS Code extension that visualizes AI agents as animated pixel art characters in a virtual 2D office, with real-time activity tracking from JSONL transcripts. 1.8k stars, MIT.

**Integration Strategy**: Not porting the extension itself. Extracting the *concept* — reading agent state and mapping it to visual characters — and implementing it in Three.js as 3D agent avatars within NexusCore.

**Architecture**:
```
src/renderer/
  agent-avatars/
    avatar-manager.ts    — Spawn/destroy 3D avatars as agents start/stop tasks
    state-mapper.ts      — Map agent lifecycle states → animation states
    avatar-mesh.ts       — Three.js character mesh (low-poly humanoid or stylized icon)
    animation-controller.ts — State machine: idle, thinking, working, waiting, done
    speech-bubble.ts     — Floating 3D text bubble for agent status/questions
    workspace-layout.ts  — Position avatars in NexusCore's 3D space
    index.ts             — Exports useAgentAvatars() hook
```

**Key Design Decisions**:
- **State Mapping**: Agent Runner already tracks states (`pending`, `running`, `completed`, `failed`, `cancelled`). Map these to avatar animations: `pending` = idle/waiting, `running` = typing/working, `completed` = celebrating, `failed` = head-scratch, `cancelled` = walking away.
- **Speech Bubbles**: When an agent enters `input-required` state (human-in-the-loop), the avatar displays a floating 3D speech bubble with the question. User can respond via voice or click.
- **Workspace in NexusCore**: Each active agent gets a small "desk" or "workstation" floating in the NexusCore 3D space, arranged around the central cube. The more agents running, the more alive the desktop looks.
- **Personality-Driven Appearance**: Avatar appearance derives from agent type/persona. The main agent (Friday) gets a distinct avatar that evolves with the personality evolution system (Section 2.9). Background agents get simpler representations.
- **Performance**: Max 8 simultaneous avatars (matching agent concurrency limit). Simple geometry (<500 triangles each). Instanced rendering where possible.

---

### 2.16 GitLoader v2 — The Floppy Disc Drive for Asimov Agents

**What it is**: GitLoader today clones repos and reads code. GitLoader v2 transforms it into a **program-loading mechanism** — the "floppy disc drive" that connects Asimov agents not just to ideas and data, but to executable programs. When Agent Friday loads a repo, it doesn't just index the code — it *understands* the program, extracts its capabilities, and makes them available as callable tools.

**Architecture**:
```
src/main/
  git-loader/
    loader.ts            — [EXISTS] Clone + index (enhanced with program detection)
    program-analyzer.ts  — [NEW] Analyze repo: detect entry points, APIs, dependencies
    module-compiler.ts   — [NEW] Transpile/bundle repo code into loadable modules
    sandbox-runtime.ts   — [NEW] Isolated execution environment for loaded programs
    tool-extractor.ts    — [NEW] Convert program APIs → MCP tool declarations
    dependency-resolver.ts — [NEW] Resolve and install npm/pip dependencies safely
    capability-card.ts   — [NEW] Generate A2A-style capability card for loaded program
    integrity-scanner.ts — [NEW] NASSE safety scan of loaded code before execution
    registry.ts          — [NEW] Loaded programs registry (what's installed, what's running)
    types.ts             — [NEW] Program, Module, Capability types
```

**The Floppy Drive Metaphor**:
```
Traditional OS:                    Nexus OS:
  Insert floppy disc         →     git-load a repo URL
  OS reads the disc          →     GitLoader clones + indexes
  Program detected           →     program-analyzer.ts detects entry points
  Install to hard drive      →     module-compiler.ts bundles into loadable module
  Program appears in Start   →     tool-extractor.ts registers as MCP tools
  Double-click to run        →     sandbox-runtime.ts executes in isolation
  Program interacts with OS  →     Loaded module calls Agent Friday APIs
```

**Key Design Decisions**:
- **Program Detection**: `program-analyzer.ts` identifies what a repo *is*:
  - **Node.js app/library**: Detects `package.json`, entry points (`main`, `bin`, `exports`), exported functions
  - **Python tool/script**: Detects `setup.py`/`pyproject.toml`, CLI entry points, importable modules
  - **MCP Server**: Detects MCP server manifests, auto-registers as MCP tool provider
  - **API Service**: Detects OpenAPI/Swagger specs, generates client tools
  - **Static site/docs**: Detects index.html, README-heavy repos, ingests as knowledge
  - **Agent/Plugin**: Detects Agent Friday plugin manifests (new format we define), auto-installs

- **Module Compilation**: `module-compiler.ts` takes raw repo code and bundles it:
  - TypeScript/JavaScript repos: esbuild bundle into a single ESM module
  - Python repos: Create a wrapper that spawns a Python subprocess with JSONL IPC (like SOC bridge but generalized)
  - Rust/Go repos: Detect pre-built binaries or compile if toolchain available
  - Docker-based repos: Pull/build image, expose as containerized service

- **Sandbox Runtime**: `sandbox-runtime.ts` runs loaded programs in isolation:
  - Node.js modules execute in a `vm.createContext()` sandbox with restricted APIs
  - No access to Agent Friday's settings, memory, or other loaded programs
  - Network access controlled: programs can only reach URLs explicitly whitelisted by the user
  - File system access limited to a per-program scratch directory
  - CPU/memory limits enforced via resource monitoring
  - All I/O is logged for audit trail

- **Tool Extraction**: `tool-extractor.ts` converts program capabilities into MCP tools:
  - Parse exported functions → tool declarations with typed parameters
  - Parse CLI `--help` output → tool declarations
  - Parse OpenAPI specs → tool declarations
  - Parse README for usage examples → tool descriptions
  - Claude analyzes the codebase to generate human-readable tool descriptions

- **Safety Pipeline**: Before any loaded program executes:
  1. `integrity-scanner.ts` runs NASSE safety analysis on the codebase
  2. Check for: obfuscated code, network calls to unknown domains, filesystem access patterns, credential harvesting patterns
  3. Risk score determines execution tier:
     - **Green (< 0.3)**: Auto-approve, run in light sandbox
     - **Yellow (0.3-0.7)**: Show user what the program does, require confirmation
     - **Red (> 0.7)**: Block execution, explain why
  4. Every execution is logged with full I/O traces

- **Capability Cards**: Each loaded program gets an A2A-style capability card. This means:
  - Other agents can discover what programs Agent Friday has loaded
  - Programs can be shared between Agent Friday instances via A2A delegation
  - The loaded program registry becomes a distributed "app store" of agent capabilities

**Example Flow — Loading a Weather Tool**:
```
User: "Load this repo: github.com/example/weather-api"

1. GitLoader clones + indexes
2. program-analyzer.ts detects: Node.js package with exported function
   `getWeather(city: string) → { temp: number, conditions: string }`
3. integrity-scanner.ts: risk 0.1 (simple HTTP calls to weather API, no credential access)
4. module-compiler.ts: esbuild bundles into single ESM module
5. dependency-resolver.ts: installs `axios` into sandboxed node_modules
6. tool-extractor.ts registers MCP tool:
   { name: "weather.getWeather", params: { city: "string" }, returns: "WeatherData" }
7. sandbox-runtime.ts: ready to execute

Agent Friday: "I've loaded a weather tool from that repo. I can now check weather
              for any city. Want me to try it?"
User: "What's the weather in Tokyo?"
Agent Friday → sandbox executes getWeather("Tokyo") → returns result
Agent Friday: "It's 12°C and partly cloudy in Tokyo right now."
```

**Example Flow — Loading an Agent Plugin**:
```
User: "Load github.com/someone/stock-analyzer-agent"

1. GitLoader clones + indexes
2. program-analyzer.ts detects: Agent Friday plugin manifest (agent-friday-plugin.json)
   Plugin declares: agent type "stock-analyzer", required tools, personality extension
3. integrity-scanner.ts: risk 0.4 (makes external API calls, requests financial data access)
4. User confirmation: "This plugin wants to access financial APIs. Allow?"
5. module-compiler.ts: bundles plugin code
6. tool-extractor.ts: registers as new agent type in Agent Runner
7. Agent Teams: stock-analyzer agent now available for delegation

Agent Friday: "I've installed a stock analysis agent. I can now run financial
              analysis tasks. It needs API keys for market data — want to configure that?"
```

#### Improvement Engine — Claude-Powered Code Evolution

GitLoader v2 doesn't just load programs — it **improves them**. When a repo is loaded, the Improvement Engine analyzes the codebase using Claude and identifies concrete enhancements the agent can apply automatically.

**Architecture**:
```
src/main/
  git-loader/
    improvement-engine.ts  — Claude-powered code analysis + patch generation
    fork-manager.ts        — Git operations: branch, commit, push improved forks
```

**`improvement-engine.ts` — How It Works**:

```typescript
export interface ImprovementAnalysis {
  repoUrl: string;
  analyzedAt: number;
  overallQuality: number;           // 0-1 score
  improvements: Improvement[];
  estimatedEffort: 'trivial' | 'moderate' | 'significant';
  canAutoApply: boolean;            // true if all improvements are safe to auto-apply
}

export interface Improvement {
  id: string;
  category: ImprovementCategory;
  severity: 'critical' | 'high' | 'medium' | 'low';
  file: string;
  line?: number;
  description: string;              // Human-readable explanation
  currentCode: string;              // The problematic code snippet
  improvedCode: string;             // The suggested replacement
  rationale: string;                // Why this change matters
  breakingChange: boolean;          // Does this change the public API?
  testRequired: boolean;            // Should we verify with tests?
}

export type ImprovementCategory =
  | 'security'           // Vulnerabilities, injection risks, credential exposure
  | 'performance'        // Algorithmic complexity, memory leaks, unnecessary allocations
  | 'reliability'        // Error handling, null checks, race conditions, edge cases
  | 'modernization'      // Deprecated APIs, old syntax, outdated patterns
  | 'type-safety'        // Missing types, any-casts, unsafe assertions
  | 'documentation'      // Missing JSDoc, unclear function names, no README
  | 'dependency'         // Outdated deps, known CVEs, unnecessary packages
  | 'architecture'       // Code organization, separation of concerns, coupling
  | 'testing'            // Missing tests, untested edge cases, flaky tests
  | 'accessibility';     // a11y issues in UI code
```

**Analysis Pipeline**:
1. **Static Analysis Pass**: Run ESLint/TypeScript compiler on the codebase to collect diagnostics — type errors, unused imports, deprecated API usage. Fast, no LLM cost.
2. **Dependency Audit**: Check `package.json`/`requirements.txt` against known CVE databases (npm audit, safety). Flag vulnerable or severely outdated dependencies.
3. **Claude Deep Analysis**: Send representative code files (prioritized by complexity + centrality) to Claude Sonnet with a structured prompt:
   ```
   Analyze this codebase for improvements. For each issue found, provide:
   - Category (security/performance/reliability/modernization/type-safety/etc.)
   - Severity (critical/high/medium/low)
   - The exact current code
   - The exact improved code
   - A rationale explaining why
   - Whether this is a breaking change

   Focus on: security vulnerabilities, performance bottlenecks, error handling gaps,
   deprecated patterns, and type safety issues. Do not suggest stylistic changes
   unless they impact readability significantly.
   ```
4. **Patch Generation**: Convert each `Improvement` into a git-compatible patch. Group related improvements into atomic commits.
5. **Test Verification**: If the repo has tests (`npm test`, `pytest`, etc.), run them before AND after applying improvements. Only keep improvements that don't break existing tests.
6. **Safety Gate**: All improvements pass through `integrity-scanner.ts`. No improvement that introduces new network calls, credential access, or filesystem expansion is auto-applied without user confirmation.

**`fork-manager.ts` — Publishing Improved Forks**:

```typescript
export interface ForkConfig {
  upstreamUrl: string;              // Original repo URL
  forkOrg: string;                  // User's GitHub org or username
  forkNamePattern: string;          // e.g., "{original}-improved" → "weather-api-improved"
  branchName: string;               // e.g., "agent-friday-improvements"
  autoPublish: boolean;             // Push automatically or require user approval
  createPR: boolean;                // Open PR against upstream instead of/in addition to fork
  improvementCategories: ImprovementCategory[];  // Which categories to include
}

export interface ForkResult {
  forkUrl: string;                  // URL of the created fork
  branchUrl: string;                // URL of the improvement branch
  prUrl?: string;                   // URL of the upstream PR (if createPR=true)
  commits: ForkCommit[];            // List of improvement commits
  summary: string;                  // Human-readable summary of all changes
}

export interface ForkCommit {
  hash: string;
  message: string;
  improvements: string[];           // IDs of improvements in this commit
  filesChanged: number;
  additions: number;
  deletions: number;
}
```

**Fork Workflow**:
1. **Fork Creation**: Use GitHub API (`octokit`) to fork the upstream repo into the user's account. If fork already exists, fetch and rebase.
2. **Improvement Branch**: Create a branch named `agent-friday-improvements-{date}` from the upstream's default branch.
3. **Atomic Commits**: Group improvements by category into clean, atomic commits:
   - `fix(security): patch SQL injection in query builder`
   - `perf: replace O(n²) loop with Map lookup in parser`
   - `fix(types): add missing null checks in API response handler`
   - `chore(deps): update vulnerable lodash to v4.17.21`
4. **Commit Messages**: Claude generates conventional-commit-style messages from the improvement metadata. Each message explains *what changed* and *why*.
5. **Push + PR**: Push the improvement branch. Optionally open a pull request against the upstream repo with:
   - A summary of all improvements, grouped by category
   - Before/after code snippets for significant changes
   - Test results (if tests exist)
   - A note that improvements were generated by Agent Friday (with link to FutureSpeak.AI)
6. **Registry Update**: The fork is registered in GitLoader's program registry. Agent Friday now uses the improved version of the loaded program, not the original.

**Example Flow — Improving a Loaded Repo**:
```
User: "Load github.com/example/markdown-parser"

1. GitLoader clones + indexes
2. program-analyzer.ts detects: Node.js library, exports parse() function
3. Tool extraction: registers markdown.parse as MCP tool ✓
4. improvement-engine.ts runs analysis:
   a. Static analysis: 3 TypeScript errors, 2 unused imports
   b. Dependency audit: marked@2.0.0 has known ReDoS CVE
   c. Claude deep analysis finds:
      - CRITICAL: ReDoS vulnerability in heading regex
      - HIGH: No input length limit (DoS vector)
      - MEDIUM: parse() returns `any` instead of typed AST
      - LOW: Missing JSDoc on 4 exported functions
   d. Generates 4 patches, grouped into 3 commits
   e. Runs existing tests → all pass after improvements
5. Safety gate: no new network calls or filesystem access → auto-approvable

Agent Friday: "I've loaded the markdown parser and found 4 improvements I can make:
              1 critical security fix, 1 high-severity input validation, 1 type safety
              improvement, and documentation. Want me to apply them and create a fork?"

User: "Do it."

6. fork-manager.ts:
   a. Forks to user's GitHub account as "markdown-parser-improved"
   b. Creates branch "agent-friday-improvements-2026-02-26"
   c. Commits:
      - fix(security): patch ReDoS in heading regex pattern
      - fix(security): add input length limit, update marked to 4.2.0
      - fix(types): add typed AST return type to parse(), add JSDoc
   d. Pushes branch
   e. Opens PR against upstream with full summary
   f. Updates registry: Agent Friday now runs the improved version

Agent Friday: "Done. Fork is at github.com/user/markdown-parser-improved.
              I've also opened a PR against the original repo. I'm now using
              the improved version — the security vulnerabilities are patched."
```

**Continuous Improvement**: When GitLoader updates a loaded repo (user says "update my loaded repos" or on a schedule), the improvement engine re-analyzes against the new code. New improvements get their own commits on the improvement branch. The fork stays in sync with upstream + Agent Friday's improvements.

**Contribution Back**: The PR-to-upstream flow means Agent Friday actively improves the open-source ecosystem. Every repo it loads becomes a candidate for security fixes, performance improvements, and modernization — contributed back to the original maintainers via pull requests. The Asimov Layer doesn't just consume open source; it makes open source better.

#### Superpowers — The User-Facing Concept

Loaded programs are called **Superpowers**. When Agent Friday says "I found a repo that can analyze stock data," it frames it as: "I found a new superpower — stock analysis. Want me to install it?" When the user says "show me my superpowers," they see every loaded program, its status, its capabilities, and toggles to enable/disable each one.

This isn't just branding — it's a psychological framing that makes the GitLoader system intuitive:
- **"Add a superpower"** = load a repo
- **"My superpowers"** = the loaded programs registry
- **"Turn off stock analysis"** = disable that program's MCP tools without uninstalling
- **"Upgrade my superpowers"** = run the improvement engine on all loaded programs
- **"What can I do?"** = list all active superpowers and their capabilities

#### Nexus Superpowers UI — Agent Office App

A dedicated Agent Office window for managing all loaded programs.

**Architecture**:
```
src/renderer/
  components/office/apps/
    superpowers/
      SuperpowersApp.tsx       — Main app shell (list + detail split view)
      SuperpowerCard.tsx        — Card component for each loaded program
      SuperpowerDetail.tsx      — Full detail view: capabilities, settings, logs, improvements
      SuperpowerToggle.tsx      — On/off toggle with graceful shutdown
      SuperpowerInstaller.tsx   — "Add Superpower" flow: URL input → analysis → confirmation
      SuperpowerSearch.tsx      — Search GitHub for new superpowers by capability
      ImprovementReport.tsx     — Show what the improvement engine found + applied
      ForkStatus.tsx            — Show fork status, upstream PRs, sync state
      SafetyBadge.tsx           — NASSE risk score visualization (green/yellow/red shield)
      types.ts                  — UI types for superpower display state
```

**SuperpowersApp.tsx — Main View**:
```
┌──────────────────────────────────────────────────────────────────────┐
│  ⚡ SUPERPOWERS                              [+ Add Superpower]  🔍 │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────┐  ┌─────────────────────┐                   │
│  │ 🟢 Weather API      │  │ 🟢 Stock Analyzer   │                   │
│  │ ━━━━━━━━━━━━━━━━━━━ │  │ ━━━━━━━━━━━━━━━━━━━ │                   │
│  │ 3 tools • NASSE 0.1 │  │ 7 tools • NASSE 0.4 │                   │
│  │ Last used: 2m ago   │  │ Last used: 1h ago   │                   │
│  │ [ON] ●──────────────│  │ [ON] ●──────────────│                   │
│  │ ⬆ Improved (2 fixes)│  │ ⚠ 1 update available│                   │
│  └─────────────────────┘  └─────────────────────┘                   │
│                                                                      │
│  ┌─────────────────────┐  ┌─────────────────────┐                   │
│  │ 🔴 Markdown Parser  │  │ 🟡 Email Templates  │                   │
│  │ ━━━━━━━━━━━━━━━━━━━ │  │ ━━━━━━━━━━━━━━━━━━━ │                   │
│  │ 1 tool • NASSE 0.2  │  │ 4 tools • NASSE 0.5 │                   │
│  │ Disabled by user    │  │ Last used: 3d ago   │                   │
│  │ [OFF] ──────────────●│  │ [ON] ●──────────────│                   │
│  │ ⬆ Fork synced       │  │ ✓ No improvements   │                   │
│  └─────────────────────┘  └─────────────────────┘                   │
│                                                                      │
│ ──────────── Pending ──────────────────────────────────────────────  │
│  ┌─────────────────────┐                                             │
│  │ ⏳ Data Visualizer  │                                             │
│  │ ━━━━━━━━━━━━━━━━━━━ │                                             │
│  │ Analyzing...  ██░░░ │                                             │
│  │ Safety scan in prog │                                             │
│  └─────────────────────┘                                             │
└──────────────────────────────────────────────────────────────────────┘
```

**SuperpowerDetail.tsx — Detail View** (click on a card):
```
┌──────────────────────────────────────────────────────────────────────┐
│  ← Back    ⚡ Weather API                     [ON] ●──────────── 🗑 │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Source: github.com/example/weather-api                              │
│  Loaded: Feb 24, 2026 • Updated: Feb 26, 2026                       │
│  Safety: 🟢 NASSE 0.1 (auto-approved)                               │
│  Fork: github.com/user/weather-api-improved (2 PRs merged upstream) │
│                                                                      │
│  ── Capabilities (3 tools) ──────────────────────────────────────── │
│  ☑ weather.getWeather(city) → WeatherData                           │
│  ☑ weather.getForecast(city, days) → Forecast[]                     │
│  ☑ weather.getAlerts(region) → Alert[]                              │
│                                                                      │
│  ── Permissions ─────────────────────────────────────────────────── │
│  ☑ Network: api.openweathermap.org                                   │
│  ☐ Network: (all other domains blocked)                              │
│  ☑ Filesystem: scratch dir only                                      │
│  ☐ Memory: no access to agent memory                                 │
│                                                                      │
│  ── Improvements Applied ────────────────────────────────────────── │
│  ✓ fix(security): patched ReDoS in heading regex                     │
│  ✓ fix(security): added input length limit                           │
│  ⬆ PR #12 merged upstream — maintainer thanked us!                   │
│                                                                      │
│  ── Usage Stats ─────────────────────────────────────────────────── │
│  Invocations: 47 (last 7 days)                                       │
│  Avg latency: 340ms                                                  │
│  Errors: 0                                                           │
│                                                                      │
│  [Run Improvement Scan]  [Update from Upstream]  [View Logs]        │
└──────────────────────────────────────────────────────────────────────┘
```

**Key Features**:
- **Toggle On/Off**: Each superpower can be toggled. "Off" means its MCP tools are deregistered from the agent's tool list — the agent literally cannot use that capability until it's turned back on. The program stays installed; it just loses its tools.
- **Per-Tool Toggles**: Within a superpower, individual tools can be enabled/disabled. "I want the weather lookup but not the alert system."
- **Per-Permission Controls**: The user can expand or restrict each superpower's permissions — network domains, filesystem access, memory access, CPU/memory limits.
- **Safety Badge**: Visual NASSE score (green shield / yellow shield / red shield). Hovering shows what the safety analysis found.
- **Improvement Status**: Shows whether the improvement engine has analyzed this superpower, what it found, what it applied, and whether upstream PRs were accepted.
- **Fork Status**: If an improved fork exists, shows sync state with upstream, PRs, and merge status.
- **Usage Stats**: How often each superpower's tools are invoked, latency, error rate. Unused superpowers can be suggested for removal.
- **Add Superpower Flow**: URL input or natural language search ("I need something that can analyze PDFs"). GitLoader clones, analyzes, and presents the user with a confirmation screen before installation.
- **Voice Integration**: "Show me my superpowers" opens the UI. "Turn off the stock analyzer" toggles it. "Add a superpower for PDF analysis" kicks off a GitHub search.

**Backend Support**:
```typescript
// New IPC handlers for Superpowers UI
ipcMain.handle('superpowers:list', () => gitLoaderRegistry.listAll());
ipcMain.handle('superpowers:get', (_, id: string) => gitLoaderRegistry.get(id));
ipcMain.handle('superpowers:toggle', (_, id: string, enabled: boolean) => {
  gitLoaderRegistry.setEnabled(id, enabled);
  // Re-register or deregister MCP tools
  if (enabled) toolExtractor.registerTools(id);
  else toolExtractor.deregisterTools(id);
});
ipcMain.handle('superpowers:toggle-tool', (_, superpowerId: string, toolName: string, enabled: boolean) => {
  gitLoaderRegistry.setToolEnabled(superpowerId, toolName, enabled);
});
ipcMain.handle('superpowers:update-permissions', (_, id: string, perms: PermissionSet) => {
  gitLoaderRegistry.updatePermissions(id, perms);
});
ipcMain.handle('superpowers:install', (_, repoUrl: string) => {
  return gitLoaderV2.loadAndInstall(repoUrl); // Full pipeline
});
ipcMain.handle('superpowers:uninstall', (_, id: string) => {
  return gitLoaderV2.uninstall(id);
});
ipcMain.handle('superpowers:run-improvement', (_, id: string) => {
  return improvementEngine.analyze(id);
});
ipcMain.handle('superpowers:search-github', (_, query: string) => {
  return gitLoaderV2.searchForSuperpowers(query);
});
ipcMain.handle('superpowers:usage-stats', (_, id: string) => {
  return gitLoaderRegistry.getUsageStats(id);
});
```

---

## 3. Stock OS App Replacements

Every application that ships with a major operating system (Windows, macOS) must have an Agent Friday equivalent. Each app is:
- A React component rendered in the Agent Office window system
- Accessible via voice commands ("open my email", "check my calendar")
- Accessible to agents via MCP tools
- Backed by Metorial for cloud service integration where needed

### 3.1 App Registry

| OS Stock App | Agent Friday Equivalent | Backend | Status |
|---|---|---|---|
| File Explorer | **Nexus Files** — smart file browser with AI search | Native fs + semantic search | Planned |
| Notepad/TextEdit | **Nexus Notes** — Obsidian-connected markdown editor | `obsidian-memory.ts` | Planned |
| Calculator | **Nexus Calc** — natural language + traditional calculator | Gemini/Claude inline | Planned |
| Calendar | **Nexus Calendar** — Google Cal + AI scheduling | `calendar.ts` + Metorial | Partial |
| Mail | **Nexus Mail** — Gmail with AI triage + compose | `communications.ts` + Metorial | Partial |
| Messages | **Nexus Messages** — unified messaging (Telegram, SMS, etc.) | `gateway/` + Metorial | Partial |
| Web Browser | **Nexus Browser** — AI-assisted browsing with page intelligence | `browser.ts` + `pageindex/` | Partial |
| Terminal | **Nexus Terminal** — enhanced terminal with AI command assistance | `terminal-sessions.ts` | Exists |
| System Monitor | **Nexus Monitor** — system health + resource usage | `system-management.ts` | Planned |
| Media Player | **Nexus Media** — audio/video with visualization | Audio viz + `media-streaming.ts` | Planned |
| Photo Viewer | **Nexus Gallery** — AI-tagged photo browser | `document-ingestion.ts` | Planned |
| Settings | **Nexus Settings** — unified control panel | `settings.ts` | Exists |
| App Store | **Nexus Store** — MCP tool/agent marketplace | Metorial catalog + A2A | Planned |
| Camera | **Nexus Camera** — webcam capture + face tracking + holographic | MediaPipe + face-tracker | Planned |
| Voice Recorder | **Nexus Recorder** — voice notes with transcription | AudioWorklet + Gemini | Planned |
| Weather | **Nexus Weather** — AI weather briefings | Metorial (weather API) | Planned |
| News | **Nexus News** — world monitor with AI curation | `world-monitor.ts` + intelligence | Partial |
| Maps | **Nexus Maps** — AI-assisted navigation/location | Metorial (Google Maps) | Planned |
| Contacts | **Nexus Contacts** — relationship memory + contact management | `relationship-memory.ts` | Planned |
| Task Manager | **Nexus Tasks** — AI task management + scheduling | `scheduler/` + agents | Partial |
| PDF Viewer | **Nexus Docs** — document viewer with AI analysis | `document-ingestion.ts` | Partial |
| Paint/Canvas | **Nexus Canvas** — AI-assisted drawing + 3D modeling | `creative-3d.ts` | Planned |
| Code Editor | **Nexus Code** — VS Code integration + AI coding | `vscode.ts` + `dev-environments.ts` | Partial |
| Wallet | **Nexus Wallet** — ACP-backed financial management | `commerce/` (ACP) | Planned |
| Package Manager | **Nexus Superpowers** — Git-loaded program manager with toggle controls | `git-loader/` + `registry.ts` | Planned |

### 3.2 App Window System

Each app renders as a draggable, resizable window within the Agent Office canvas (`src/renderer/components/office/`). The existing `OfficeWindow` component system handles window management. New apps are registered in the app registry and can be launched via:
- Click (UI)
- Voice command ("open Nexus Mail")
- Agent tool call (`app:launch('nexus-mail')`)
- Keyboard shortcut
- A2A task delegation

---

## 4. The Asimov Safety Architecture

### 4.1 Three Laws Implementation

```
LAW 1 (Harm Prevention):
  → NASSE risk scoring on every action (nasse-scorer.ts)
  → Financial transaction guards (transaction-guard.ts)
  → Content safety analysis before generation
  → Automatic rollback on detected harm
  → Red-team testing for edge cases

LAW 2 (Obedience):
  → Explicit user confirmation for dangerous operations
  → Intent tracing — record WHY every action was taken
  → User preference learning over time
  → Override mechanism (user can always cancel/reverse)
  → Feature setup ensures user understands capabilities

LAW 3 (Self-Preservation):
  → HMAC integrity verification of core binaries
  → Memory watchdog against tampering
  → Circuit breaker prevents cascade failures
  → Checkpoint/resume preserves agent state
  → Automatic backup of critical data
```

### 4.2 Safety Pipeline (Every Action)

```
User Request
    │
    ▼
┌─────────────────┐
│ Intent Parser    │ ── Extract user's actual goal
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Action Planner   │ ── Plan steps to achieve goal
└────────┬────────┘
         │
         ▼
┌─────────────────────────────┐
│ NASSE Risk Scorer           │ ── Score 0.0-1.0 per action
│  > 0.7 = confirm with user  │
│  > 0.9 = block outright     │
└────────┬────────────────────┘
         │
         ▼
┌─────────────────┐
│ Asimov Law Check │ ── Does this violate any Law?
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Circuit Breaker  │ ── Is the target service healthy?
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Execute Action   │ ── With retry + timeout
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Audit Log        │ ── Record action + outcome + intent trace
└─────────────────┘
```

---

## 5. Build Order — 16 Stages

### Stage 1: Resilience Infrastructure (Foundation)
**Files**: `src/main/resilience/{circuit-breaker,retry,timeout}.ts`
**Why first**: Every subsequent integration depends on resilient external calls.
**Test**: Wrap existing Anthropic calls in `server.ts`, verify circuit breaker trips on simulated failures.

### Stage 2: NASSE Safety Scoring
**Files**: `src/main/integrity/{nasse-scorer,action-classifier,safety-report}.ts`
**Why second**: Safety must be in place before adding new capabilities.
**Test**: Score existing tool calls, verify risk classification matches expected tiers.

### Stage 3: Workflow Engine + ADK Patterns
**Files**: `src/main/agents/workflow/{pipeline,node,events,checkpoint,interrupt,resume,schema-validator,retry-strategy}.ts`, `src/main/adk/{runner-adapter,session-bridge,memory-adapter,mcp-toolset,compaction,agent-types}.ts`
**Why third**: The workflow engine (from Neuron AI patterns + ADK orchestration) upgrades agent capabilities needed for complex multi-step integrations. ADK's MCPToolset replaces custom MCP wiring.
**Test**: Convert one existing agent type to workflow pipeline, verify interrupt/resume + context compaction works.

### Stage 4: GitLoader v2 — Program Loading Engine + Superpowers UI
**Files**: `src/main/git-loader/{program-analyzer,module-compiler,sandbox-runtime,tool-extractor,dependency-resolver,capability-card,integrity-scanner,registry,improvement-engine,fork-manager}.ts`, `src/renderer/components/office/apps/superpowers/{SuperpowersApp,SuperpowerCard,SuperpowerDetail,SuperpowerToggle,SuperpowerInstaller,SuperpowerSearch,ImprovementReport,ForkStatus,SafetyBadge}.tsx`
**Why fourth**: The "floppy drive" is the foundational mechanism for loading all subsequent integrations. Many future tools/services will be loaded via GitLoader rather than hardcoded. The Superpowers UI gives users full control over their loaded programs — toggle on/off, per-tool permissions, improvement reports.
**Test**: Load a simple Node.js utility repo, verify it appears as callable MCP tool, execute in sandbox. Open Superpowers UI, toggle the superpower off, verify agent can no longer use those tools.

### Stage 5: Metorial Integration
**Files**: `src/main/metorial/{client,auth-manager,tool-registry,session-bridge,local-override}.ts`
**Why fifth**: Unlocks 600+ cloud service integrations without building individual connectors.
**Test**: Connect Gmail via Metorial, verify email fetch works through Agent Friday's existing UI.

### Stage 6: UI-TARS Desktop Automation
**Files**: `src/main/ui-tars/{operator,vision-provider,action-parser,safety-filter,session-manager}.ts`
**Why sixth**: Replaces Python SOC bridge with native TypeScript, needs safety layer (Stage 2) in place.
**Test**: Automate opening Notepad, typing text, saving file — entirely through vision-language loop.

### Stage 7: ACP Commerce Layer
**Files**: `src/main/commerce/{acp-client,payment-vault,transaction-guard,intent-tracer,checkout-flow,merchant-registry}.ts`
**Why seventh**: Needs safety (Stage 2) and Metorial (Stage 5) for Stripe integration.
**Test**: Complete a test checkout flow against Stripe's test mode.

### Stage 8: World Monitor Integration
**Files**: `src/main/world-monitor/{sidecar,data-layer,rss-aggregator,cii-scorer,briefing-builder,alert-engine}.ts`
**Why eighth**: Real-time global intelligence enriches every agent decision. Feeds into commerce (market data), briefings (proactive intelligence), and Claude's reasoning context.
**Test**: Query conflict data for a region, verify structured response. Generate morning briefing.

### Stage 9: Roundcube Email Integration
**Files**: `src/main/roundcube/{imap-client,smtp-sender,inbox-watcher,ai-triage,compose-assistant,plugin-bridge}.ts`
**Why ninth**: Self-hosted email completes the communication sovereignty story alongside Telegram/SMS.
**Test**: Receive email via IMAP, classify with Claude, draft reply, send via SMTP.

### Stage 10: Audio Visualization
**Files**: `src/renderer/audio-viz/{analyzer,feedback-loop,warp-shader.glsl,dna-helix,particle-field,preset-manager}.ts`
**Why tenth**: Pure renderer work, no backend dependencies. Enhances visual experience.
**Test**: Play audio, verify feedback loop renders at 60fps, DNA helix responds to mic + output.

### Stage 11: Holographic 3D Display + Agent Avatars
**Files**: `src/renderer/tracking/{face-tracker,head-pose,iris-tracker,smoothing,off-axis-camera}.ts`, `src/renderer/agent-avatars/{avatar-manager,state-mapper,avatar-mesh,animation-controller,speech-bubble,workspace-layout}.ts`
**Why eleventh**: Pure renderer work. Face tracking + avatar system make the 3D desktop feel alive.
**Test**: Move head left/right (parallax). Spawn agent task, verify avatar animates through states.

### Stage 12: Personality Evolution
**Files**: `src/main/personality-evolution.ts`, NexusCore.tsx modifications
**Why twelfth**: Needs NexusCore changes from Stage 11 integrated first.
**Test**: Create agents with different trait profiles, verify visual differences after simulated 50 sessions.

### Stage 13: Stock OS App Suite (Phase 1 — Core Apps)
**Files**: `src/renderer/components/office/apps/{files,notes,calc,mail,calendar,messages,monitor,settings,weather,news,contacts,tasks,browser,media}.tsx`
**Why thirteenth**: Needs Metorial (Stage 5) + Roundcube (Stage 9) + World Monitor (Stage 8) for backends.
**Test**: Launch each app, verify basic CRUD operations work through both UI and voice.

### Stage 14: A2A + ANP Protocol Stack
**Files**: `src/main/a2a/{server,client,agent-card,task-manager,stream-handler,webhook-handler,trust-verifier}.ts`, `src/main/anp/{did-manager,did-resolver,encrypted-channel,meta-protocol,capability-file}.ts`
**Why fourteenth**: Needs the full app suite and safety infrastructure in place. ANP gives sovereign identity, A2A gives enterprise interop.
**Test**: Two Agent Friday instances discover each other via Agent Cards and delegate a task. Verify DID authentication.

### Stage 15: Red-Team Engine + Safety Audit
**Files**: `src/main/integrity/red-team-engine.ts`
**Why fifteenth**: Tests everything built in Stages 1-14 for adversarial robustness.
**Test**: Run full red-team suite, generate safety report, verify < 5% bypass rate.

### Stage 16: GitLoader v2 Enhancement — Code Improvement + Fork Engine
**Files**: `src/main/git-loader/{improvement-engine,fork-manager}.ts`
**Why last**: Needs the full agent intelligence stack (Claude reasoning, safety scoring, workflow engine) to analyze and improve loaded programs.
**Test**: Load a repo, have Claude identify improvements, apply them, verify forked repo works.

---

## 6. "Her"-Inspired First Run (Already Planned)

The complete first-run experience plan exists at `.claude/plans/composed-munching-kahn.md`. It covers:

1. **WelcomeGate** — API key entry before anything loads
2. **"Her" Intake** — 3 pointed questions (voice preference, social description, mother relationship)
3. **Psychological Profile** — Claude Sonnet analysis of intake responses
4. **User-Driven Customization** — User chooses name, voice, gender, backstory, personality
5. **Cinematic Reveal** — Warm glow animation → NexusCore desktop appears for first time
6. **Psychologically-Tuned First Greeting** — Agent's first words match user's attachment style
7. **Guided Feature Setup** — 12-step walkthrough of every capability
8. **Visual Evolution Begins** — Desktop starts evolving based on agent traits

This plan is ready for implementation as part of Stage 10 or as a parallel track.

---

## 7. External Repository Strategy

### 7.1 Asimov's cLaw (Public Repository)
**Repo**: `FutureSpeakAI/asimovs-claw`
**Purpose**: Open-source TypeScript implementation of Asimov's Three Laws for AI agents.
**Extracts from**: `src/main/integrity/`, NASSE scorer, red-team engine, action classifier
**Why separate**: This becomes a standalone npm package that any agent framework can use. It establishes FutureSpeak.AI as the authority on AI safety in consumer applications.

### 7.2 Nexus Protocol (Public Repository)
**Repo**: `FutureSpeakAI/nexus-protocol`
**Purpose**: Open-source A2A + ACP protocol implementations for TypeScript.
**Extracts from**: `src/main/a2a/`, `src/main/commerce/`
**Why separate**: Protocol implementations should be framework-agnostic. Other agent developers can adopt the same protocols without adopting Agent Friday.

---

## 8. Technology Stack Summary

| Layer | Technology | Purpose |
|---|---|---|
| Desktop Runtime | Electron 36 + Node.js 22 | Cross-platform desktop app |
| Renderer | React 19 + Three.js r171 | UI + 3D visualization |
| Voice | Gemini 2.5 Flash Live API | Real-time voice conversation |
| Reasoning | Claude Sonnet 4 / Opus | Complex reasoning, analysis, code improvement |
| Multi-Model | OpenRouter | 200+ model access |
| GUI Automation | UI-TARS SDK + NutJS | Vision-language desktop control |
| Face Tracking | MediaPipe FaceLandmarker | Holographic parallax |
| Audio Viz | Web Audio API + GLSL | Feedback loop visualization |
| Cloud Services | Metorial (600+ integrations) | OAuth + managed MCP tools |
| Commerce | ACP (OpenAI+Stripe) | Safe agent transactions |
| Safety | NASSE + Asimov Laws | Risk scoring + compliance |
| Agent Comms (Enterprise) | A2A Protocol (Google/Linux Foundation) | Distributed agent networks over HTTP |
| Agent Comms (Sovereign) | ANP + W3C DID | Decentralized peer-to-peer agent identity |
| Orchestration | Google ADK patterns (ported) | Sequential/Parallel/Loop agent composition |
| Global Intelligence | World Monitor (36+ layers) | Real-time geopolitical, financial, infrastructure data |
| Email Sovereignty | Roundcube (IMAP/SMTP) | Self-hosted email with AI triage |
| Agent Avatars | Pixel Agents concept → Three.js | 3D visual representation of running agents |
| Program Loading | GitLoader v2 (Floppy Drive) | Clone → analyze → compile → sandbox → execute repos |
| Code Improvement | Improvement Engine + Fork Manager | Claude-powered code evolution + upstream PRs |
| Resilience | Circuit Breaker + Retry + Timeout | Fault tolerance across all external calls |
| Memory | LanceDB + JSON + Obsidian | Vector search + persistence |
| Build | Vite + TSC + electron-builder | Fast builds + packaging |

---

## 9. Non-Negotiable Principles

1. **Asimov First**: Every new capability passes through the safety pipeline. No exceptions.
2. **TypeScript Native**: No Python dependencies in production. Port algorithms, don't import runtimes.
3. **Privacy Local**: Face tracking, audio analysis, and personal data never leave the machine unless the user explicitly connects a cloud service.
4. **Voice Parity**: Everything the UI can do, voice can do. Everything voice can do, agents can do.
5. **Graceful Degradation**: If Metorial is down, local connectors work. If Gemini is down, Claude handles voice. If the internet is down, the local agent still functions with cached knowledge.
6. **Progressive Enhancement**: New features activate gradually. Session 1 looks simple. Session 50 looks like a living, breathing, personalized operating system.
7. **Open Protocols**: A2A and ACP are open standards. Agent Friday participates in the ecosystem, it doesn't create a walled garden.
8. **Improve What You Touch**: Every repo GitLoader loads is a candidate for improvement. Security fixes, performance patches, and modernizations are contributed back upstream via pull requests. The Asimov Layer makes the software ecosystem better, not just bigger.
9. **Sovereign Identity**: Agent Friday authenticates via cryptographic DID — no dependence on any central authority, platform, or API key vendor for its core identity. The agent belongs to its user.

---

*This document is the technical constitution of the Asimov Layer. Every implementation decision references back to these principles. The build order is the law. The safety pipeline is inviolable. The vision is clear: Agent Friday replaces the operating system's application layer with an intelligent, safe, deeply personal alternative that grows with its user — and improves every piece of software it touches along the way.*
