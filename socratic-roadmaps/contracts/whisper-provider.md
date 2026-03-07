## Interface Contract: WhisperProvider
**Sprint:** 4, Phase J.1
**Source:** src/main/voice/whisper-provider.ts (to be created)

### Exports
- `whisperProvider` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| loadModel(size?) | `(size?: WhisperModelSize): Promise<void>` | Load whisper.cpp model |
| unloadModel() | `(): void` | Free model memory |
| transcribe(audio) | `(audio: Float32Array): Promise<TranscriptionResult>` | Transcribe audio buffer |
| isReady() | `(): boolean` | Model loaded and functional |
| getAvailableModels() | `(): WhisperModelInfo[]` | List downloaded models |

### Types
```typescript
type WhisperModelSize = 'tiny' | 'base' | 'small' | 'medium' | 'large-v3';

interface TranscriptionResult {
  text: string;
  language: string;
  segments: TranscriptionSegment[];
  duration: number;       // Audio duration in seconds
  processingTime: number; // Transcription time in ms
}

interface TranscriptionSegment {
  text: string;
  start: number;  // seconds
  end: number;    // seconds
}

interface WhisperModelInfo {
  size: WhisperModelSize;
  path: string;
  fileSizeMB: number;
  downloaded: boolean;
}
```

### Audio Format
- Input: 16kHz mono PCM Float32Array
- Caller (AudioCapture) handles resampling and channel mixing

### Dependencies
- Requires: whisper.cpp binary or Node addon
- Required by: TranscriptionPipeline (J.3), VoiceCircle (L.1)
