# Agent Friday -- Voice Pipeline Architecture Flows

This directory documents the three voice pipeline paths in Agent Friday's architecture.
Each flow describes the end-to-end data path from user input to agent response,
including key files, protocols, failure modes, and audio specifications.

## Documented Flows

| Flow | Path | Description |
|------|------|-------------|
| [Gemini Live Voice](./gemini-live-voice/README.md) | Cloud WebSocket | Bidirectional audio via Gemini 2.5 Flash over WebSocket. Richest voice quality, lowest latency for TTS, supports native audio interruption. Requires network + Gemini API key. |
| [Local Voice Conversation](./local-voice-conversation/README.md) | Whisper + Ollama + TTS | Fully local pipeline running in the Electron main process. Whisper STT for transcription, Ollama for LLM inference, Kokoro/Piper for TTS. Works offline with downloaded models. |
| [Voice Fallback Cascade](./voice-fallback-cascade/README.md) | State Machine + Path Switching | The 13-state VoiceStateMachine and VoiceFallbackManager that select, monitor, and switch between voice paths. Ensures the user always has a working interaction mode. |

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
