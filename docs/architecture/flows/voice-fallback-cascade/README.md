# Voice Fallback Cascade (State Machine + Path Switching)

## Quick Reference

| Property | Value |
|----------|-------|
| **State Machine** | `src/main/voice/voice-state-machine.ts` (class `VoiceStateMachine`) |
| **Fallback Manager** | `src/main/voice/voice-fallback-manager.ts` (class `VoiceFallbackManager`) |
| **Process** | Electron main process (singletons) |
| **Total States** | 13 |
| **Voice Paths** | Cloud (Gemini) -> Local (Whisper+Ollama+TTS) -> Text (always available) |
| **Teardown Timeout** | 5 seconds (prevents hung teardown from blocking path switch) |
| **Degraded Auto-Switch** | 10 seconds in degraded state before switching to next path |
| **IPC Surface** | Read-only from renderer (state, log, health, events) |

## State Machine

### States

The `VoiceStateMachine` defines 13 states. Each represents a real, observable condition
that the user would notice if surfaced in the UI.

| State | Description | Terminal? |
|-------|-------------|-----------|
| `IDLE` | No voice activity. Natural resting state. | No (but no timeout) |
| `REQUESTING_MIC` | Waiting for `getUserMedia` permission dialog. | No |
| `MIC_GRANTED` | Microphone active; choosing voice path (cloud vs. local). | No |
| `MIC_DENIED` | User denied microphone permission. Recoverable -- can retry. | No |
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
| `CONNECTING_CLOUD` | 15s | `CONNECTING_LOCAL` | Cloud failed -- try local |
| `CLOUD_ACTIVE` | 120s | `CLOUD_DEGRADED` | Backstop if health monitor misses degradation |
| `CLOUD_DEGRADED` | 30s | `CONNECTING_LOCAL` | Recovery window; then switch to local |
| `CONNECTING_LOCAL` | 45s | `TEXT_FALLBACK` | Model loading can be slow; 45s is generous |
| `LOCAL_ACTIVE` | 120s | `LOCAL_DEGRADED` | Backstop for undetected degradation |
| `LOCAL_DEGRADED` | 30s | `TEXT_FALLBACK` | Recovery window; then fall to text |
| `DISCONNECTING` | 10s | `IDLE` | Force IDLE if cleanup hangs |

### Transition Table

Every legal state transition is explicitly defined. Transitions not in the table are
rejected by the state machine. `ERROR` is reachable from any state (wildcard).

```
IDLE ──────────────> REQUESTING_MIC
IDLE ──────────────> TEXT_FALLBACK
IDLE ──────────────> CONNECTING_LOCAL
IDLE ──────────────> CONNECTING_CLOUD

REQUESTING_MIC ───> MIC_GRANTED
REQUESTING_MIC ───> MIC_DENIED
REQUESTING_MIC ───> TEXT_FALLBACK

MIC_DENIED ────────> REQUESTING_MIC  (retry)
MIC_DENIED ────────> TEXT_FALLBACK
MIC_DENIED ────────> IDLE            (user cancelled)

MIC_GRANTED ───────> CONNECTING_CLOUD
MIC_GRANTED ───────> CONNECTING_LOCAL
MIC_GRANTED ───────> TEXT_FALLBACK
MIC_GRANTED ───────> DISCONNECTING

CONNECTING_CLOUD ──> CLOUD_ACTIVE
CONNECTING_CLOUD ──> CONNECTING_LOCAL  (cloud failed)
CONNECTING_CLOUD ──> TEXT_FALLBACK
CONNECTING_CLOUD ──> DISCONNECTING

CLOUD_ACTIVE ──────> CLOUD_DEGRADED
CLOUD_ACTIVE ──────> DISCONNECTING

CLOUD_DEGRADED ────> CLOUD_ACTIVE     (recovered)
CLOUD_DEGRADED ────> CONNECTING_CLOUD (retry cloud)
CLOUD_DEGRADED ────> CONNECTING_LOCAL  (give up cloud)
CLOUD_DEGRADED ────> TEXT_FALLBACK
CLOUD_DEGRADED ────> DISCONNECTING

CONNECTING_LOCAL ──> LOCAL_ACTIVE
CONNECTING_LOCAL ──> TEXT_FALLBACK
CONNECTING_LOCAL ──> DISCONNECTING

LOCAL_ACTIVE ──────> LOCAL_DEGRADED
LOCAL_ACTIVE ──────> DISCONNECTING

LOCAL_DEGRADED ────> LOCAL_ACTIVE     (recovered)
LOCAL_DEGRADED ────> CONNECTING_LOCAL (retry)
LOCAL_DEGRADED ────> TEXT_FALLBACK
LOCAL_DEGRADED ────> DISCONNECTING

TEXT_FALLBACK ─────> IDLE             (user restarts)
TEXT_FALLBACK ─────> REQUESTING_MIC   (retry voice)

ERROR ─────────────> IDLE
ERROR ─────────────> TEXT_FALLBACK

DISCONNECTING ─────> IDLE
```

### Reachability Proof

All 13 states are reachable, and every non-terminal state has a path to `TEXT_FALLBACK`:

- `IDLE` -> `REQUESTING_MIC` -> `MIC_DENIED` -> `TEXT_FALLBACK`
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
| 1 | `cloud` | Gemini WebSocket -- bidirectional audio, highest quality | Gemini API key exists |
| 2 | `local` | Whisper + Ollama + TTS -- offline capable | Ollama health check (3s timeout) |
| 99 | `text` | No voice -- text input/output only | Always available |

Path priority can be overridden via `setPathPriority()` for users who prefer local-first.

### Availability Probing

`probeAvailability()` checks prerequisites without starting any path:

- **Cloud**: Checks for Gemini API key existence (not validity -- that happens during connection).
- **Local**: HTTP health check to Ollama (`localhost`), 3-second timeout. If Ollama takes > 3s, it is too slow for real-time voice.
- **Text**: Always available. No external dependencies.

Returns a sorted array of `PathConfig` objects with `available` boolean and `reason` string.

### Path Switching

When a path fails or degrades, the fallback manager orchestrates the switch:

1. **Capture snapshot**: Conversation messages, system prompt, and tools are saved.
   Audio state (WebSocket handles, pending TTS, buffers) is NOT preserved.
2. **Teardown current path**: Full cleanup within 5-second timeout.
   All listeners, timers, connections, and queues are destroyed.
3. **Start next path**: Using the captured snapshot for conversation continuity.
4. **Emit events**: `switch-start` before, `switch-complete` or `switch-failed` after.

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
| `src/main/voice/voice-state-machine.ts` | 13-state machine: transitions, timeouts, guards, health monitoring |
| `src/main/voice/voice-fallback-manager.ts` | Path selection, mid-session switching, conversation snapshot |
| `src/main/voice/voice-health-monitor.ts` | Periodic health checks for active voice paths |
| `src/main/voice/voice-error-classifier.ts` | Classifies errors into `ErrorCategory` for user messaging |
| `src/main/voice/connection-stage-monitor.ts` | Tracks connection progress stages |
| `src/main/ipc/voice-state-handlers.ts` | IPC bridge: renderer read-only access to state machine |
| `src/main/ipc/voice-fallback-handlers.ts` | IPC bridge: renderer access to fallback manager |
| `src/main/ipc/connection-stage-handlers.ts` | IPC bridge: connection stage events |
| `src/main/preload.ts` | Preload bridge exposing `window.eve.voiceState` and `window.eve.voiceFallback` |
| `src/renderer/hooks/useVoiceState.ts` | React hook for consuming voice state in components |
| `tests/voice/voice-state-machine.test.ts` | Unit tests for state machine transitions and timeouts |
| `tests/voice/voice-fallback-manager.test.ts` | Unit tests for fallback path selection and switching |
| `tests/voice/voice-error-classifier.test.ts` | Unit tests for error classification |
