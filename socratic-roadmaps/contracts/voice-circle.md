## Interface Contract: SpeechSynthesis + VoiceCircle
**Sprint:** 4, Phases K.3 + L.1
**Source:** src/main/voice/speech-synthesis.ts (to be created)

### SpeechSynthesis Exports
- `speechSynthesis` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| speak(text, opts?) | `(text: string, opts?: SpeakOptions): void` | Queue utterance |
| speakImmediate(text) | `(text: string): void` | Interrupt + speak |
| stop() | `(): void` | Stop all, clear queue |
| pause() | `(): void` | Pause current |
| resume() | `(): void` | Resume paused |
| isSpeaking() | `(): boolean` | Audio playing |
| getQueueLength() | `(): number` | Pending utterances |
| on(event, cb) | `(event: string, cb: Function): () => void` | Subscribe |

### Events
- `utterance-start` — Began playing an utterance
- `utterance-end` — Finished playing an utterance
- `queue-empty` — All queued speech complete
- `interrupted` — Speech was interrupted (barge-in)

### Types
```typescript
interface SpeakOptions {
  profileId?: string;    // Override active profile
  priority?: 'normal' | 'high';
  chunkAtSentences?: boolean;  // Default: true for text > 100 chars
}
```

### IPC Channels (main → renderer for playback)
- `voice:play-chunk` — Audio data for playback
- `voice:stop-playback` — Stop audio output
- `voice:playback-state` — Renderer reports playback state

### Voice Circle Flow (L.1 Integration)
```
TranscriptionPipeline.on('transcript') → llmClient.chat(text)
llmClient response → speechSynthesis.speak(response)
TranscriptionPipeline.on('voice-start') → speechSynthesis.stop() [barge-in]
speechSynthesis.on('queue-empty') → TranscriptionPipeline.start() [listen again]
```

### Dependencies
- Requires: TTSEngine (K.1), VoiceProfileManager (K.2), BrowserWindow (IPC)
- Required by: VoiceCircle integration test (L.1)
