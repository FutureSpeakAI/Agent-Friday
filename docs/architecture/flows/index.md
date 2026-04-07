# Agent Friday -- Operational Flow Documentation

This directory documents the key operational flows in Agent Friday's architecture.
Each flow describes the end-to-end data path from user input to agent response,
including key files, protocols, failure modes, and specifications.

## Documented Flows

### Voice Pipelines

| Flow | Path | Description |
|------|------|-------------|
| [Gemini Live Voice](./gemini-live-voice/README.md) | Cloud WebSocket | Bidirectional audio via Gemini 2.5 Flash over WebSocket. Richest voice quality, lowest latency for TTS, supports native audio interruption. Requires network + Gemini API key. |
| [Local Voice Conversation](./local-voice-conversation/README.md) | Whisper + Ollama + TTS | Fully local pipeline running in the Electron main process. Whisper STT for transcription, Ollama for LLM inference, Kokoro/Piper for TTS. Works offline with downloaded models. |
| [Voice Fallback Cascade](./voice-fallback-cascade/README.md) | State Machine + Path Switching | The 13-state VoiceStateMachine and VoiceFallbackManager that select, monitor, and switch between voice paths. Ensures the user always has a working interaction mode. |

### Core Systems

| Flow | Path | Description |
|------|------|-------------|
| [Text Chat](./text-chat/README.md) | TextInput -> LLM -> ChatHistory | Typed message flow through multi-provider LLM routing (Anthropic/OpenRouter/Ollama), iterative tool-use loops, and Markdown response rendering via Zustand store. |
| [Agent System](./agent-system/README.md) | Spawn -> Queue -> Execute -> Voice | Background agent orchestration with personas (Atlas, Nova, Cipher), team collaboration, chain-of-thought streaming, awareness mesh, and voice synthesis per persona. |
| [Memory System](./memory-system/README.md) | Extract -> Store -> Consolidate -> Retrieve | Three-tier memory (short/medium/long-term) with LLM-powered extraction, episodic sessions, relationship tracking, periodic consolidation, Obsidian sync, and semantic search. |

### Onboarding & Settings

| Flow | Path | Description |
|------|------|-------------|
| [Onboarding Wizard](./onboarding-wizard/README.md) | Multi-step wizard | First-run "Her"-style interview that discovers user identity, agent name, voice, and personality through natural conversation. |
| [Settings & Vault](./settings-vault/README.md) | Settings UI + Sovereign Vault | Encrypted persistent settings, API key management, passphrase-protected vault, and two-phase boot. |

### Infrastructure & Integrations

| Flow | Path | Description |
|------|------|-------------|
| [Application Boot](./application-boot/README.md) | Electron launch → Ready | Full boot sequence from `app.whenReady()` through singleton init, preload injection, vault unlock, and renderer mount. |
| [Gateway & Trust](./gateway-trust/README.md) | Request → Trust tier → Allow/Deny | cLaw enforcement gateway with 5 trust tiers, HMAC attestation, consent gates, and integrity verification. |
| [Integration Connectors](./integration-connectors/README.md) | Telegram / Discord / Calendar / Obsidian | External service connectors configured during onboarding, managed by CommunicationHub with unified message routing. |

### Session, Cost & Theming (Sprint 8)

| Flow | Path | Description |
|------|------|-------------|
| Session Persistence | JSONL DAG with auto-compaction | Conversation sessions persisted as append-only JSONL with DAG-based parent references. Auto-compaction merges old entries to keep file sizes bounded. Managed by `src/main/session-persistence.ts`. |
| Cost Tracking | Per-turn token and USD tracking | Tracks input/output tokens and estimated USD cost per LLM turn. Aggregates daily totals. Surfaced in UI via cost display panel. Managed by `src/main/cost-tracker.ts`. |
| Theme System | JSON token-based theming with mood modifiers | JSON design-token themes loaded by `ThemeProvider` in the renderer. Mood modifiers dynamically adjust palette based on agent personality state. Theme files in `src/renderer/themes/`. |

## Architecture Overview

```
User speaks
    |
    v
+-------------------+
| VoiceFallbackMgr  |  Selects best available path
+-------------------+
    |           |
    v           v
+--------+  +--------+
| Cloud  |  | Local  |  <-- Two voice paths
| Gemini |  | Whisper |
| WS     |  | Ollama |
|        |  | TTS    |
+--------+  +--------+
    |           |
    v           v
+-------------------+
| AudioPlayback     |  Renderer: gapless Web Audio playback
| Engine            |
+-------------------+
    |
    v
User hears response
```

## Cross-Cutting Concerns

- **Episodic Memory**: Both cloud and local paths save conversation episodes when sessions
  meet the threshold (>= 4 turns AND >= 60 seconds).
- **Barge-in**: Both paths support user interruption of agent speech. Cloud uses native
  Gemini interruption; local stops the TTS queue immediately.
- **Backpressure**: The AudioPlaybackEngine signals queue pressure at 75% (elevated) and
  90% (critical) fill levels, affecting mic input across both paths.
- **Session Management**: The SessionManager handles Gemini's ~10-minute session limit
  with seamless reconnection at ~5.5 minutes, preserving conversation context.

## Diagram Files

Each flow directory includes a `.mermaid` diagram file that can be rendered by any
Mermaid-compatible viewer (GitHub, VS Code with Mermaid extension, mermaid.live, etc.).
