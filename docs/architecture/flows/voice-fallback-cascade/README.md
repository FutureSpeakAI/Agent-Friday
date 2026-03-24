# Voice Fallback Cascade (State Machine + Path Switching)

## Quick Reference

| Property | Value |
|----------|-------|
| **State Machine** | `src/main/voice/voice-state-machine.ts` (class `VoiceStateMachine`) |
| **Fallback Manager** | `src/main/voice/voice-fallback-manager.ts` (class `VoiceFallbackManager`) |
| **Health Monitor** | `src/main/voice/voice-health-monitor.ts` (class `VoiceHealthMonitor`) |
| **Connection Stages** | `src/main/voice/connection-stage-monitor.ts` (class `ConnectionStageMonitor`) |
| **Process** | Electron main process (singletons) |
| **Total States** | 16 |
| **Voice Paths** | PersonaPlex (priority 0) -> Cloud/Gemini (priority 1) -> Local/Whisper+Ollama+TTS (priority 2) -> Text (priority 99) |
| **Teardown Timeout** | 5 seconds (prevents hung teardown from blocking path switch) |
| **Degraded Auto-Switch** | 10 seconds in degraded state before switching to next path |
| **Health Check Escalation** | silent (1 failure) -> subtle (2 failures) -> visible (3+ failures) |
| **IPC Surface** | Read-only from renderer (state, log, health, events) |

## State Machine

### States

The `VoiceStateMachine` defines 16 states. Each represents a real, observable condition
that the user would notice if surfaced in the UI.

| State | Description | Terminal? |
|-------|-------------|-----------|
| `IDLE` | No voice activity. Natural resting state. | No (but no timeout) |
| `REQUESTING_MIC` | Waiting for `getUserMedia` permission dialog. | No |
| `MIC_GRANTED` | Microphone active; choosing voice path (personaplex vs. cloud vs. local). | No |
| `MIC_DENIED` | User denied microphone permission. Recoverable -- can retry. | No |
| `CONNECTING_PERSONAPLEX` | WebSocket opening to local PersonaPlex server (port 8998). | No |
| `PERSONAPLEX_ACTIVE` | PersonaPlex full-duplex audio flowing both directions (verified). | No |
| `PERSONAPLEX_DEGRADED` | PersonaPlex connected but audio unhealthy. | No |
| `CONNECTING_CLOUD` | WebSocket opening to Gemini. | No |
| `CLOUD_ACTIVE` | Gemini voice flowing both directions (verified with audio proof). | No |
| `CLOUD_DEGRADED` | Gemini connected but audio unhealthy (jitter, silence, context dead). | No |
| `CONNECTING_LOCAL` | Whisper + Ollama + TTS initializing. | No |
| `LOCAL_ACTIVE` | Local voice pipeline flowing (verified). | No |
| `LOCAL_DEGRADED` | Local pipeline partial (e.g., TTS failed but Whisper works). | No |
| `TEXT_FALLBACK` | All voice paths failed. Text-only mode. Universal floor. | Yes |
| `ERROR` | Unrecoverable error. Requires explicit user action. | Yes |
| `DISCONNECTING` | Cleanup in progress. Tearing down voice components. | No |

### State Timeouts

Every non-terminal state has a timeout that auto-transitions to a fallback.
No state can be infinite -- this is the primary defense against getting stuck.

| State | Timeout | Target | Rationale |
|-------|---------|--------|-----------|
| `REQUESTING_MIC` | 30s | `MIC_DENIED` | OS permission dialog should not hang this long |
| `MIC_DENIED` | 60s | `TEXT_FALLBACK` | Give user time to reconsider, then fall back |
| `MIC_GRANTED` | 10s | `TEXT_FALLBACK` | Path selection should be near-instant |
| `CONNECTING_PERSONAPLEX` | 30s | `CONNECTING_LOCAL` | PersonaPlex server may need GPU warm-up; fall back to local |
| `PERSONAPLEX_ACTIVE` | 600s | `PERSONAPLEX_DEGRADED` | 10 min backstop if health monitor misses degradation |
| `PERSONAPLEX_DEGRADED` | 30s | `CONNECTING_LOCAL` | Recovery window; then fall back to local |
| `CONNECTING_CLOUD` | 15s | `CONNECTING_LOCAL` | Cloud failed -- try local |
| `CLOUD_ACTIVE` | 600s | `CLOUD_DEGRADED` | 10 min backstop if health monitor misses degradation |
| `CLOUD_DEGRADED` | 30s | `CONNECTING_LOCAL` | Recovery window; then switch to local |
| `CONNECTING_LOCAL` | 45s | `TEXT_FALLBACK` | Model loading can be slow; 45s is generous |
| `LOCAL_ACTIVE` | 600s | `LOCAL_DEGRADED` | 10 min backstop for undetected degradation |
| `LOCAL_DEGRADED` | 30s | `TEXT_FALLBACK` | Recovery window; then fall to text |
| `DISCONNECTING` | 10s | `IDLE` | Force IDLE if cleanup hangs |

### Transition Table

Every legal state transition is explicitly defined. Transitions not in the table are
rejected by the state machine. `ERROR` is reachable from any active state (wildcard
set: `REQUESTING_MIC`, `MIC_GRANTED`, `CONNECTING_CLOUD`, `CLOUD_ACTIVE`,
`CLOUD_DEGRADED`, `CONNECTING_PERSONAPLEX`, `PERSONAPLEX_ACTIVE`,
`PERSONAPLEX_DEGRADED`, `CONNECTING_LOCAL`, `LOCAL_ACTIVE`, `LOCAL_DEGRADED`,
`TEXT_FALLBACK`, `DISCONNECTING`).

```
IDLE ───────────────────> REQUESTING_MIC
IDLE ───────────────────> TEXT_FALLBACK
IDLE ───────────────────> CONNECTING_LOCAL
IDLE ───────────────────> CONNECTING_CLOUD
IDLE ───────────────────> CONNECTING_PERSONAPLEX

REQUESTING_MIC ────────> MIC_GRANTED
REQUESTING_MIC ────────> MIC_DENIED
REQUESTING_MIC ────────> TEXT_FALLBACK

MIC_DENIED ─────────────> REQUESTING_MIC  (retry)
MIC_DENIED ─────────────> TEXT_FALLBACK
MIC_DENIED ─────────────> IDLE            (user cancelled)

MIC_GRANTED ────────────> CONNECTING_CLOUD
MIC_GRANTED ────────────> CONNECTING_PERSONAPLEX
MIC_GRANTED ────────────> CONNECTING_LOCAL
MIC_GRANTED ────────────> TEXT_FALLBACK
MIC_GRANTED ────────────> DISCONNECTING

CONNECTING_PERSONAPLEX ─> PERSONAPLEX_ACTIVE
CONNECTING_PERSONAPLEX ─> CONNECTING_LOCAL   (PersonaPlex failed)
CONNECTING_PERSONAPLEX ─> CONNECTING_CLOUD   (PersonaPlex failed, try cloud)
CONNECTING_PERSONAPLEX ─> TEXT_FALLBACK
CONNECTING_PERSONAPLEX ─> DISCONNECTING

PERSONAPLEX_ACTIVE ─────> PERSONAPLEX_DEGRADED
PERSONAPLEX_ACTIVE ─────> DISCONNECTING

PERSONAPLEX_DEGRADED ───> PERSONAPLEX_ACTIVE      (recovered)
PERSONAPLEX_DEGRADED ───> CONNECTING_PERSONAPLEX   (retry)
PERSONAPLEX_DEGRADED ───> CONNECTING_LOCAL          (give up PersonaPlex)
PERSONAPLEX_DEGRADED ───> CONNECTING_CLOUD          (try cloud)
PERSONAPLEX_DEGRADED ───> TEXT_FALLBACK
PERSONAPLEX_DEGRADED ───> DISCONNECTING

CONNECTING_CLOUD ───────> CLOUD_ACTIVE
CONNECTING_CLOUD ───────> CONNECTING_PERSONAPLEX   (cloud failed, try PersonaPlex)
CONNECTING_CLOUD ───────> CONNECTING_LOCAL          (cloud failed)
CONNECTING_CLOUD ───────> TEXT_FALLBACK
CONNECTING_CLOUD ───────> DISCONNECTING

CLOUD_ACTIVE ───────────> CLOUD_DEGRADED
CLOUD_ACTIVE ───────────> DISCONNECTING

CLOUD_DEGRADED ─────────> CLOUD_ACTIVE     (recovered)
CLOUD_DEGRADED ─────────> CONNECTING_CLOUD (retry cloud)
CLOUD_DEGRADED ─────────> CONNECTING_PERSONAPLEX  (try PersonaPlex)
CLOUD_DEGRADED ─────────> CONNECTING_LOCAL  (give up cloud)
CLOUD_DEGRADED ─────────> TEXT_FALLBACK
CLOUD_DEGRADED ─────────> DISCONNECTING

CONNECTING_LOCAL ───────> LOCAL_ACTIVE
CONNECTING_LOCAL ───────> LOCAL_DEGRADED    (partial — e.g., Ollama works but no TTS)
CONNECTING_LOCAL ───────> TEXT_FALLBACK
CONNECTING_LOCAL ───────> DISCONNECTING

LOCAL_ACTIVE ───────────> LOCAL_DEGRADED
LOCAL_ACTIVE ───────────> DISCONNECTING

LOCAL_DEGRADED ─────────> LOCAL_ACTIVE     (recovered)
LOCAL_DEGRADED ─────────> CONNECTING_LOCAL (retry)
LOCAL_DEGRADED ─────────> TEXT_FALLBACK
LOCAL_DEGRADED ─────────> DISCONNECTING

TEXT_FALLBACK ──────────> IDLE             (user restarts)
TEXT_FALLBACK ──────────> REQUESTING_MIC   (retry voice)
TEXT_FALLBACK ──────────> CONNECTING_PERSONAPLEX
TEXT_FALLBACK ──────────> CONNECTING_LOCAL
TEXT_FALLBACK ──────────> CONNECTING_CLOUD
TEXT_FALLBACK ──────────> DISCONNECTING

ERROR ──────────────────> IDLE
ERROR ──────────────────> TEXT_FALLBACK
ERROR ──────────────────> REQUESTING_MIC

DISCONNECTING ──────────> IDLE
DISCONNECTING ──────────> ERROR            (cleanup error)
```

### Reachability Proof

All 16 states are reachable, and every non-terminal state has a path to `TEXT_FALLBACK`:

- `IDLE` -> `REQUESTING_MIC` -> `MIC_DENIED` -> `TEXT_FALLBACK`
- `PERSONAPLEX_ACTIVE` -> `PERSONAPLEX_DEGRADED` -> `CONNECTING_LOCAL` -> `TEXT_FALLBACK`
- `CLOUD_ACTIVE` -> `CLOUD_DEGRADED` -> `CONNECTING_LOCAL` -> `TEXT_FALLBACK`
- `LOCAL_ACTIVE` -> `LOCAL_DEGRADED` -> `TEXT_FALLBACK`
- `ERROR` -> `TEXT_FALLBACK`
- `DISCONNECTING` -> `IDLE` -> ... -> `TEXT_FALLBACK`

### Error Categories

Errors emitted by the state machine are classified for user-facing messaging:

| Category | Description |
|----------|-------------|
| `mic-permission` | getUserMedia denied or unavailable |
| `network` | WebSocket or fetch failure |
| `api-key` | Missing or invalid Gemini API key |
| `model-unavailable` | Ollama model not downloaded, Whisper binary missing |
| `audio-hardware` | No audio output device, AudioContext suspended |
| `timeout` | A state transition timed out |
| `internal` | Bug -- should never happen in production |

### Events Emitted

| Event | Payload | Description |
|-------|---------|-------------|
| `state-change` | `{ from: VoiceState, to: VoiceState, reason: string }` | Every state transition |
| `error` | `{ state: VoiceState, error: Error, category: ErrorCategory }` | Error with classification |
| `health-update` | `{ state: VoiceState, metrics: HealthMetrics }` | Periodic health while active |

## Fallback Manager

The `VoiceFallbackManager` sits above the state machine and manages path selection,
mid-session switching, and conversation context preservation.

### Voice Paths (Priority Order)

| Priority | Path | Description | Availability Check |
|----------|------|-------------|-------------------|
| 0 | `personaplex` | PersonaPlex 7B full-duplex speech-to-speech -- fastest local when CUDA GPU available | `personaplex-server.isRunning()` check |
| 1 | `cloud` | Gemini WebSocket -- bidirectional audio, highest quality | Gemini API key exists |
| 2 | `local` | Whisper + Ollama + TTS -- offline capable | Ollama health check (3s timeout) |
| 99 | `text` | No voice -- text input/output only | Always available |

Path priority can be overridden via `setPathPriority()` for users who prefer local-first.

### Availability Probing

`probeAvailability()` checks prerequisites without starting any path:

- **PersonaPlex**: Dynamic import of `personaplex-server`, calls `isRunning()`. Checks whether the managed Python sidecar is running on port 8998 with a CUDA GPU. Does not guarantee inference will succeed (GPU could be out of VRAM).
- **Cloud**: Checks for Gemini API key existence via `settingsManager.getGeminiApiKey()` (not validity -- that happens during connection).
- **Local**: HTTP health check to Ollama via `ollamaProvider.checkHealth()`, 3-second timeout. If Ollama takes > 3s, it is too slow for real-time voice.
- **Text**: Always available. No external dependencies.

Returns a sorted array of `PathConfig` objects (sorted by priority, lower = first) with `available` boolean and `reason` string.

### Path Switching

When a path fails or degrades, the fallback manager orchestrates the switch:

1. **Re-entry guard**: If a switch is already in progress (`switching` flag), the failure is ignored to prevent concurrent teardown/startup races.
2. **Capture snapshot**: Conversation messages, system prompt, and tools are saved as a `ConversationSnapshot`. Audio state (WebSocket handles, pending TTS, buffers) is NOT preserved.
3. **Teardown current path**: Full cleanup within 5-second timeout (`Promise.race` against a reject timer). Per-path cleanup:
   - **Cloud**: Stops `speechSynthesis` (best-effort).
   - **Local**: Stops `transcriptionPipeline` + `speechSynthesis` (best-effort). Models remain loaded for fast restart.
   - **PersonaPlex**: Calls `cleanupPersonaPlex()` via dynamic import to close WSS and release GPU buffers (best-effort).
   - **Text**: No-op (no transport to tear down).
4. **Probe next path**: Re-runs `probeAvailability()` to find the next available path that hasn't been attempted.
5. **Start next path**: Transitions state machine and sets `currentPath`. Uses the captured snapshot for conversation continuity.
6. **Emit events**: `switch-start` before, `switch-complete` or `switch-failed` after. If all voice paths are exhausted, emits `all-paths-exhausted` and falls to `TEXT_FALLBACK`.

**Design principle**: The user should never need to say anything twice. Messages survive
the switch; audio state does not.

### Conversation Snapshot

What survives a path switch:

| Preserved | Not Preserved |
|-----------|---------------|
| Message history (user + AI turns) | WebSocket connection state |
| System prompt | Audio buffers |
| Active tool definitions | Pending TTS utterances |
| Timestamp of capture | Mic stream handles |

### Events

| Event | Payload | When |
|-------|---------|------|
| `switch-start` | `{ from: VoicePath \| null, to: VoicePath, reason: string }` | Path switch beginning |
| `switch-complete` | `{ path: VoicePath, hadContext: boolean }` | Path switch succeeded |
| `switch-failed` | `{ path: VoicePath, error: Error }` | New path also failed |
| `all-paths-exhausted` | `{ errors: Array<{ path: VoicePath, error: string }> }` | All voice paths failed; text fallback engaged |

### Anti-Flapping

- **Degraded auto-switch delay**: 10 seconds in a degraded state before switching.
  This prevents switching on transient network hiccups.
- **Re-entry guard**: A `switching` flag prevents overlapping switch operations.
  Rapid failures cannot trigger concurrent teardown/startup races.
- **Attempted paths tracking**: `attemptedPaths` set prevents re-trying a path that
  already failed in the current session (until explicitly reset).

## Health Monitoring

The `VoiceHealthMonitor` (`src/main/voice/voice-health-monitor.ts`) detects silent failures
where the pipeline appears healthy but has actually stopped working.

### Built-in Health Check Factories

| Check | Interval | Failure Mode Detected |
|-------|----------|-----------------------|
| `audio-output-liveness` | 10s | `AudioContext` suspended or closed (no output despite "connected" status) |
| `audio-roundtrip` | 10s | WebSocket open but AI not sending audio (silent connection) |
| `mic-stream-liveness` | 5s | Mic streaming but no audio chunks arriving (muted/dead mic) |
| `llm-response-liveness` | 30s | Ollama processing but response never arrives (hung model) |

### Escalation Ladder

| Consecutive Failures | Level | Action |
|---------------------|-------|--------|
| 1 | `silent` | Auto-recover only, log internally. User does not know. |
| 2 | `subtle` | Show small status indicator. Auto-recover continues. |
| 3+ | `visible` | Show message with recovery options. User must engage. |

Health checks are registered by voice pipeline components when appropriate (e.g.,
`audio-output-liveness` only during `CLOUD_ACTIVE` or `LOCAL_ACTIVE`). They are
not registered automatically -- the monitor starts them when voice becomes active.

## Connection Stage Monitor

The `ConnectionStageMonitor` (`src/main/voice/connection-stage-monitor.ts`) tracks
six granular sub-stages within a single connection attempt. Unlike the state machine
(which tracks high-level states like `CONNECTING_CLOUD`), this monitor tracks the
steps WITHIN a connection.

### Connection Stages

| Stage | Timeout | User-Facing Message | Failure Action |
|-------|---------|--------------------|--------------------|
| `mic-permission` | 30s | "Requesting microphone access..." | Check for hidden permission dialog; open System Settings |
| `backend-probe` | 5s | "Checking backend availability..." | Ensure Ollama is running or check internet connection |
| `model-validation` | 5s | "Verifying model availability..." | Run `ollama pull <model>` or verify API key |
| `connection-open` | 5s | "Opening voice connection..." | Try again or switch paths in Settings |
| `setup-confirmation` | 5s | "Completing voice setup..." | Disconnect and reconnect |
| `first-audio-frame` | 5s | "Waiting for audio..." | Check mic is not muted; verify audio output working |

Each stage timeout produces a specific `failureMessage` and `failureAction` for
actionable error guidance, instead of a generic "connection failed."

## IPC Surface

The renderer communicates with the voice state system through a read-only IPC bridge.
The renderer cannot drive state transitions -- only main-process voice components can.

### Voice State Channels

| Channel | Direction | Returns |
|---------|-----------|---------|
| `voice-state:get-state` | Renderer -> Main | Current `VoiceState` string |
| `voice-state:get-transition-log` | Renderer -> Main | Full `TransitionLogEntry[]` array |
| `voice-state:get-health` | Renderer -> Main | Current `HealthMetrics` snapshot |
| `voice-state:event:state-change` | Main -> Renderer | Pushed on every state transition |

### Voice Fallback Channels

| Channel | Direction | Returns |
|---------|-----------|---------|
| `voice-fallback:probe-availability` | Renderer -> Main | `PathConfig[]` sorted by priority |
| `voice-fallback:start-best-path` | Renderer -> Main | The `VoicePath` that was started |
| `voice-fallback:get-current-path` | Renderer -> Main | Current active `VoicePath \| null` |
| `voice-fallback:switch-to` | Renderer -> Main | Switch to a specific path |
| `voice-fallback:event:switch-start` | Main -> Renderer | Path switch beginning |
| `voice-fallback:event:switch-complete` | Main -> Renderer | Path switch succeeded |
| `voice-fallback:event:switch-failed` | Main -> Renderer | New path failed |
| `voice-fallback:event:all-paths-exhausted` | Main -> Renderer | All voice paths exhausted |

### Preload Bridge

The renderer accesses these via `window.eve`:

```typescript
window.eve.voiceState.getState()              // -> Promise<VoiceState>
window.eve.voiceState.getTransitionLog()      // -> Promise<TransitionLogEntry[]>
window.eve.voiceState.getHealth()             // -> Promise<HealthMetrics>
window.eve.voiceState.onStateChange(callback) // -> unsubscribe function

window.eve.voiceFallback.probeAvailability()  // -> Promise<PathConfig[]>
window.eve.voiceFallback.startBestPath(...)   // -> Promise<VoicePath>
window.eve.voiceFallback.getCurrentPath()     // -> Promise<VoicePath | null>
window.eve.voiceFallback.switchTo(path)       // -> Promise<void>
```

## Key Files

| File | Role |
|------|------|
| `src/main/voice/voice-state-machine.ts` | 16-state machine: transitions, timeouts, guards, health monitoring |
| `src/main/voice/voice-fallback-manager.ts` | 4-path selection, mid-session switching, conversation snapshot, degraded auto-switch |
| `src/main/voice/voice-health-monitor.ts` | Periodic health checks with escalation ladder (silent/subtle/visible) |
| `src/main/voice/voice-error-classifier.ts` | Classifies errors into `ErrorCategory` for user messaging |
| `src/main/voice/connection-stage-monitor.ts` | 6-stage connection progress tracking with per-stage timeouts and failure guidance |
| `src/main/voice/personaplex-server.ts` | PersonaPlex Python sidecar: venv, PyTorch+CUDA, SSL cert, port 8998 |
| `src/main/voice/personaplex-voice-path.ts` | PersonaPlex WebSocket manager: PCM Float32 -> Int16 to server, Ogg Opus from server |
| `src/main/ipc/voice-state-handlers.ts` | IPC bridge: renderer read-only access to state machine |
| `src/main/ipc/voice-fallback-handlers.ts` | IPC bridge: renderer access to fallback manager |
| `src/main/ipc/connection-stage-handlers.ts` | IPC bridge: connection stage events |
| `src/main/preload.ts` | Preload bridge exposing `window.eve.voiceState` and `window.eve.voiceFallback` |
| `src/renderer/hooks/useVoiceState.ts` | React hook for consuming voice state in components |
| `tests/voice/voice-state-machine.test.ts` | Unit tests for state machine transitions and timeouts |
| `tests/voice/voice-fallback-manager.test.ts` | Unit tests for fallback path selection and switching |
| `tests/voice/voice-error-classifier.test.ts` | Unit tests for error classification |

## Diagram

See [diagram.mermaid](./diagram.mermaid) for the visual state machine and fallback flow.
