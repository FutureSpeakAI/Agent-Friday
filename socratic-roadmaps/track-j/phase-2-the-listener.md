# Phase J.2 — The Listener
## AudioCapture: Microphone Access and Voice Activity Detection

### Hermeneutic Focus
*The ear can transcribe, but it has no way to hear. This phase creates the sensory apparatus — microphone access, audio format conversion, and voice activity detection (VAD) that determines when a human is actually speaking vs. ambient silence.*

### Current State (Post-J.1)
- WhisperProvider can transcribe PCM float32 audio buffers
- No microphone access exists
- No audio format conversion exists
- Electron provides `navigator.mediaDevices.getUserMedia()` in renderer
- Audio data must flow from renderer → main process via IPC

### Architecture Context
```
AudioCapture (this phase)
├── startCapture()       — Request mic permission, begin recording
├── stopCapture()        — Release microphone
├── isCapturing()        — Currently recording
├── onVoiceStart(cb)     — VAD detected speech beginning
├── onVoiceEnd(cb)       — VAD detected speech ending
├── onAudioChunk(cb)     — Raw audio data (for streaming)
└── getAudioLevel()      — Current dB level (for UI meters)
```

### Validation Criteria (Test-First)
1. `AudioCapture.startCapture()` requests microphone permission
2. `AudioCapture.stopCapture()` releases the microphone stream
3. `AudioCapture.isCapturing()` tracks recording state correctly
4. VAD fires `onVoiceStart` when audio exceeds energy threshold
5. VAD fires `onVoiceEnd` after silence duration exceeds threshold (300ms default)
6. Audio chunks are converted to 16kHz mono PCM float32 for Whisper
7. `getAudioLevel()` returns 0-1 normalized dB for UI visualization
8. AudioCapture handles microphone permission denial gracefully
9. AudioCapture handles microphone disconnection during capture
10. All tests use mocked MediaStream — no real microphone in CI

### Socratic Inquiry

**Boundary:** *Where does audio capture happen — main process or renderer?*
Microphone access requires `getUserMedia()` which runs in the renderer. But WhisperProvider runs in main. So: renderer captures → IPC transfers audio chunks → main processes. AudioCapture abstracts this split.

**Inversion:** *What if the user denies microphone permission?*
`startCapture()` rejects with a typed error. The system continues functioning without voice — text input still works. The UI shows a permission-needed indicator.

**Constraint Discovery:** *What VAD approach — ML-based (Silero) or energy-based?*
Silero VAD (ONNX Runtime, ~1MB) is far more accurate — handles background noise, music, etc. Energy-based is simpler but triggers on any loud noise. Start with Silero VAD; fall back to energy-based if ONNX Runtime isn't available.

**Precedent:** *How does the existing IPC pattern work for streaming data?*
The codebase uses `ipcMain.handle()` for request-response. Streaming audio needs `webContents.send()` for continuous chunks. Follow the same pattern as event bridge but for audio buffers.

**Tension:** *Audio chunk size — small (low latency) vs. large (fewer IPC calls)?*
100ms chunks balance latency vs. overhead. At 16kHz mono float32 = 6.4KB per chunk = ~64KB/sec. Well within IPC capacity. Whisper works best with 0.5-30 second segments.

### Boundary Constraints
- Creates `src/main/voice/audio-capture.ts` (~130-160 lines)
- Creates `src/renderer/voice/audio-capture-renderer.ts` (~80-100 lines)
- Creates `tests/sprint-4/voice/audio-capture.test.ts`
- Does NOT transcribe audio (that's J.1/J.3)
- Does NOT manage transcription pipeline (that's J.3)
- IPC channels: `voice:audio-chunk`, `voice:start-capture`, `voice:stop-capture`

### Files to Read
1. `src/main/ipc-handlers.ts` — IPC pattern precedent
2. `src/main/voice/whisper-provider.ts` — Audio format requirements

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-j-phase-2.md` before closing.
