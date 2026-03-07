## Interface Contract: TranscriptionPipeline
**Sprint:** 4, Phase J.3
**Source:** src/main/voice/transcription-pipeline.ts (to be created)

### Exports
- `transcriptionPipeline` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| start() | `(): Promise<void>` | Initialize AudioCapture + WhisperProvider |
| stop() | `(): void` | Stop listening, cleanup |
| isListening() | `(): boolean` | Pipeline active |
| getStats() | `(): TranscriptionStats` | Latency metrics |
| on(event, cb) | `(event: string, cb: Function): () => void` | Subscribe |

### Events
- `transcript` — Final transcription result (payload: `TranscriptionResult`)
- `partial` — Streaming partial result (payload: `{text: string}`)
- `voice-start` — User started speaking
- `voice-end` — User stopped speaking
- `error` — Pipeline error

### Types
```typescript
interface TranscriptionStats {
  totalTranscriptions: number;
  averageLatencyMs: number;
  lastLatencyMs: number;
}
```

### Dependencies
- Requires: WhisperProvider (J.1), AudioCapture (J.2)
- Required by: VoiceCircle (L.1)
