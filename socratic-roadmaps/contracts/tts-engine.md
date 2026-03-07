## Interface Contract: TTSEngine
**Sprint:** 4, Phase K.1
**Source:** src/main/voice/tts-engine.ts (to be created)

### Exports
- `ttsEngine` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| loadEngine(backend?) | `(backend?: TTSBackend): Promise<void>` | Initialize Kokoro or Piper |
| unloadEngine() | `(): void` | Free resources |
| synthesize(text, opts?) | `(text: string, opts?: SynthesisOptions): Promise<Float32Array>` | Text → audio |
| synthesizeStream(text, opts?) | `(text: string, opts?: SynthesisOptions): AsyncGenerator<Float32Array>` | Streaming synthesis |
| isReady() | `(): boolean` | Engine loaded |
| getAvailableVoices() | `(): VoiceInfo[]` | List voice models |
| getInfo() | `(): TTSEngineInfo` | Engine metadata |

### Types
```typescript
type TTSBackend = 'kokoro' | 'piper';

interface SynthesisOptions {
  voiceId?: string;
  speed?: number;      // 0.5 - 2.0, default 1.0
  pitch?: number;      // -0.5 to 0.5, default 0.0
}

interface VoiceInfo {
  id: string;
  name: string;
  language: string;
  backend: TTSBackend;
  sampleRate: number;
}

interface TTSEngineInfo {
  backend: TTSBackend;
  version: string;
  voiceCount: number;
}
```

### Audio Output
- Format: mono PCM Float32Array
- Sample rate: depends on voice model (typically 22050Hz or 24000Hz)
- Consumer (SpeechSynthesis) handles playback routing

### Dependencies
- Requires: Kokoro or Piper binary/model files
- Required by: SpeechSynthesis (K.3), VoiceCircle (L.1)
