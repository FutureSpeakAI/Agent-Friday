# Gemini Live Voice Flow (Cloud WebSocket Path)

## Quick Reference

| Property | Value |
|----------|-------|
| **Entry Point** | User clicks "Call" or "Start Interview" in the renderer |
| **Primary Hook** | `src/renderer/hooks/useGeminiLive.ts` |
| **WebSocket URL** | `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent` |
| **Model** | `gemini-2.5-flash-native-audio-preview` |
| **Mic Format** | 16kHz mono PCM, Int16, Base64-encoded |
| **Output Format** | 24kHz mono PCM Float32, played via Web Audio API |
| **Noise Gate** | RMS threshold < 0.015 (filters ambient noise, passes speech typically > 0.05 RMS) |
| **Session Timeout** | ~10 min (Gemini limit); proactive reconnect at ~5.5 min |
| **Max Reconnect Attempts** | 15 (linear backoff: `attempt * 3s`, capped at 30s) |
| **Episodic Memory Threshold** | >= 4 conversation turns AND >= 60 seconds duration |

## Flow Phases

### 1. Connection Setup

1. User triggers `connect()` in the `useGeminiLive` hook.
2. Gemini API key is fetched via `window.eve.getGeminiApiKey()`.
3. Tool declarations are assembled from multiple sources (skipped during onboarding):
   - External tools (passed by caller)
   - Browser tools (`window.eve.browser.listTools()`)
   - SOC/Browser-Use tools (`window.eve.soc.listTools()`)
   - GitLoader tools (`window.eve.gitLoader.listTools()`)
   - Connector tools (`window.eve.connectors.listTools()`)
   - MCP tools (`window.eve.mcp.listTools()`)
4. A `setupComplete` guard is set to `false` -- mic audio is blocked until Gemini confirms setup.
5. WebSocket opens with `?key=` query parameter (browser WebSocket API does not support custom auth headers).
6. On `ws.onopen`, the setup message is sent containing:
   - `model`: `gemini-2.5-flash-native-audio-preview`
   - `system_instruction` with the agent's system prompt
   - `tools` with function declarations
   - `generation_config` with `response_modalities: ['AUDIO']` and selected voice name
7. Gemini responds with `setupComplete` -- the guard is released and mic capture begins.

### 2. Mic Capture

The mic pipeline (`src/renderer/hooks/gemini/mic-pipeline.ts`) handles audio acquisition:

1. `getUserMedia()` requests the microphone with:
   - `sampleRate: 16000`
   - `channelCount: 1`
   - `echoCancellation: true`
   - `noiseSuppression: true`
   - `autoGainControl: true`
2. An `AudioContext` at 16kHz is created.
3. An `AnalyserNode` is connected for mic level visualization.
4. **AudioWorklet** is attempted first (`pcm-capture-processor.js`), with **ScriptProcessorNode** as fallback.
5. Each audio frame passes through a **client-side noise gate**:
   - RMS energy is computed across the frame.
   - Frames with RMS < 0.015 are silently dropped.
   - This filters keyboard clicks, fan noise, and ambient hum.
6. Passing frames are converted to Int16 PCM, Base64-encoded, and sent via WebSocket:
   ```json
   {
     "realtime_input": {
       "media_chunks": [{ "data": "<base64>", "mime_type": "audio/pcm;rate=16000" }]
     }
   }
   ```
7. Screen capture frames (JPEG) are forwarded on the same WebSocket when active.

### 3. WebSocket Message Handling

Incoming messages from Gemini are dispatched by type:

| Message Type | Handler |
|-------------|---------|
| `setupComplete` | Release mic guard; start keepalive interval; begin idle behavior |
| `serverContent` with `audioData` | Decode Base64 to Float32, enqueue in `AudioPlaybackEngine` |
| `serverContent` with `text` | Append to rolling transcript; emit to `SessionManager` |
| `toolCall` | Dispatch to `executeToolCall()` for routing to the correct handler |
| `toolCallCancellation` | Abort in-flight tool execution |
| `interrupted` | Flush playback engine; Gemini has detected user barge-in |

### 4. Audio Playback

The `AudioPlaybackEngine` (`src/renderer/audio/AudioPlaybackEngine.ts`) provides gapless playback:

- **Scheduling**: Uses `source.start(exactTime)` to schedule chunks at exact sample boundaries (no `onended` chaining).
- **Pre-buffer**: Waits for 2 chunks before starting playback to prevent micro-gaps.
- **Output**: 24kHz mono via Web Audio API `AudioContext`.
- **Generation counter**: Incremented on `flush()` -- stale `onended` callbacks from previous generations become no-ops.

#### Backpressure Zones

| Zone | Queue Fill | Action |
|------|-----------|--------|
| **Normal** | < 75% | No action |
| **Elevated** | >= 75% | Signal backpressure upstream; pause mic input |
| **Critical** | >= 90% | Drop oldest chunks AND signal backpressure |
| **Resume** | < 50% | Clear backpressure; resume mic input |

- Max queue size: 50 chunks (~5 seconds at 24kHz with ~100ms chunks).
- The 50% resume threshold creates hysteresis to prevent flapping.

#### AudioContext Resurrection (Phase 4.1)

- Liveness is verified by checking `ctx.state === 'running'` AND running an analyser probe.
- Up to 3 resurrection attempts before declaring audio degraded.
- Each resurrection increments the generation counter, invalidating stale sources.

### 5. Tool Call Dispatch

Tool calls arrive from Gemini and are routed by `executeToolCall()` (`src/renderer/hooks/gemini/tool-executor.ts`):

| Tool Category | Routing |
|--------------|---------|
| `ask_claude` | HTTP POST to local API server (`/api/chat`) |
| `save_memory` | `window.eve.memory.addImmediate()` |
| `setup_intelligence` | `window.eve.intelligence.setup()` |
| `create_task` / `list_tasks` / `delete_task` | `window.eve.scheduler.*` |
| Browser tools | `window.eve.browser.executeTool()` |
| SOC tools | `window.eve.soc.executeTool()` |
| MCP tools | `window.eve.mcp.callTool()` |
| Connector tools | `window.eve.connectors.executeTool()` |
| Desktop tools | `window.eve.desktop.executeTool()` |

All tool calls within a single `toolCall` message are executed in parallel via `Promise.all`.

### 6. Reconnection

Two reconnection paths exist:

#### Session Manager Reconnect (Proactive)

The `SessionManager` (`src/renderer/session/SessionManager.ts`) handles Gemini's ~10-minute session limit:

1. Proactive reconnect triggers at ~5.5 minutes (7 min timeout minus 90s buffer).
2. Waits for agent to finish speaking (no mid-sentence cuts).
3. Pre-fetches updated system instruction while still connected.
4. Builds conversation summary from rolling buffer (max 60 entries).
5. Closes old WebSocket; mic pipeline stays alive (callbacks reference `wsRef` by ref).
6. Opens new WebSocket with conversation summary injected into system instruction.
7. A voice identity anchor is appended to maintain consistent accent/character.
8. User perceives no gap -- mic frames are silently dropped during the ~1-2s switch.

#### Auto-Reconnect (Reactive)

When an unexpected WebSocket close is detected:

1. Up to 15 reconnect attempts.
2. Delay: `min(attempt * 3000ms, 30000ms)` -- linear backoff capped at 30 seconds.
3. If `navigator.onLine` is false, waits for the `online` event before retrying.
4. On success: resets attempt counter, restarts session timer, restores mic pipeline if needed.
5. On exhaustion: displays "tap the orb to reconnect" and stops auto-retry.

### 7. Session Lifecycle Effects

The `session-lifecycle.ts` module (`src/renderer/hooks/gemini/session-lifecycle.ts`) sets up:

| Effect | Purpose |
|--------|---------|
| **Sleep/Resume Detection** | Heartbeat every 5s; gaps > 15s trigger reconnect check |
| **Tab Focus Recovery** | Resumes suspended `AudioContext` on `visibilitychange` |
| **Mic Health Monitor** | Periodic check that mic stream tracks are still alive |
| **Periodic Memory Extraction** | Extracts memories from conversation every N minutes (requires >= 4 turns) |
| **Agent Result Surfacing** | Surfaces proactive agent results during idle periods |
| **Ambient Context Polling** | Polls system context (time, calendar, etc.) for injection |

### 8. Episodic Memory Save

On disconnect, if the session meets the threshold:
- **>= 4 conversation turns** AND **>= 60 seconds duration**
- The conversation transcript is sent to `window.eve.episodic.create()`.
- This creates a searchable episodic memory with timestamps for future context retrieval.

## Key Files

| File | Role |
|------|------|
| `src/renderer/hooks/useGeminiLive.ts` | Main hook -- state management, connect/disconnect, WebSocket message dispatch |
| `src/renderer/hooks/gemini/types.ts` | TypeScript types for `GeminiLiveState`, `GeminiRefs`, `ToolExecutionContext` |
| `src/renderer/hooks/gemini/mic-pipeline.ts` | `startMicPipeline()` / `stopMicPipeline()` -- mic acquisition, AudioWorklet, noise gate |
| `src/renderer/hooks/gemini/tool-executor.ts` | `executeToolCall()` -- routes Gemini tool calls to `window.eve.*` handlers |
| `src/renderer/hooks/gemini/tool-declarations.ts` | `sanitizeSchema()`, `buildFunctionDeclarations()` -- formats tools for Gemini API |
| `src/renderer/hooks/gemini/audio-helpers.ts` | `base64ToFloat32()`, `float32ToInt16()`, `arrayBufferToBase64()` |
| `src/renderer/hooks/gemini/session-lifecycle.ts` | Sleep detection, tab recovery, mic health, memory extraction, idle behavior |
| `src/renderer/audio/AudioPlaybackEngine.ts` | Gapless Web Audio playback with backpressure and AudioContext resurrection |
| `src/renderer/session/SessionManager.ts` | Session timeout, proactive reconnect, conversation rolling buffer |
| `src/renderer/session/IdleBehavior.ts` | Idle tier system for proactive agent engagement |

## Diagram

See [diagram.mermaid](./diagram.mermaid) for the visual flow.
