## Interface Contract: AudioCapture
**Sprint:** 4, Phase J.2
**Source:** src/main/voice/audio-capture.ts (to be created)

### Exports
- `audioCapture` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| startCapture() | `(): Promise<void>` | Request mic, begin recording |
| stopCapture() | `(): void` | Release microphone |
| isCapturing() | `(): boolean` | Currently recording |
| getAudioLevel() | `(): number` | 0-1 normalized dB |
| on(event, cb) | `(event: string, cb: Function): () => void` | Subscribe |

### Events
- `voice-start` — VAD detected speech beginning
- `voice-end` — VAD detected speech ending (payload: `Float32Array` of buffered audio)
- `audio-chunk` — Raw audio data (16kHz mono PCM float32, ~100ms chunks)
- `error` — Microphone error (permission denied, disconnected)

### Types
```typescript
interface AudioCaptureConfig {
  sampleRate: number;         // Default: 16000
  vadThreshold: number;       // Default: 0.5 (Silero VAD confidence)
  silenceDuration: number;    // Default: 300 (ms of silence before voice-end)
  maxBufferDuration: number;  // Default: 30000 (ms, max single utterance)
}
```

### IPC Channels (renderer → main)
- `voice:audio-chunk` — Audio data from renderer mic capture
- `voice:start-capture` — Main requests renderer to start mic
- `voice:stop-capture` — Main requests renderer to stop mic

### Dependencies
- Requires: Electron BrowserWindow (for renderer mic access), Silero VAD (ONNX)
- Required by: TranscriptionPipeline (J.3), VoiceCircle (L.1)
