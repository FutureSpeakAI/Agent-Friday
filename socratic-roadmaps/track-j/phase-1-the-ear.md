# Phase J.1 — The Ear
## WhisperProvider: Local Speech-to-Text Engine

### Hermeneutic Focus
*How does the system hear? Before we can wire microphones or stream audio, we need a provider that can take a raw audio buffer and return transcribed text. This is the foundational perception layer — the ear that converts sound waves into meaning.*

### Current State (Post-Sprint 3)
- OllamaProvider exists for LLM inference and embeddings
- OllamaLifecycle manages model health and discovery
- No audio processing infrastructure exists anywhere
- Electron provides `desktopCapturer` and `navigator.mediaDevices` for audio access
- whisper.cpp is a C++ implementation of OpenAI's Whisper (MIT license)

### Architecture Context
```
WhisperProvider (this phase)
├── loadModel(size)       — Load whisper.cpp model into memory
├── transcribe(buffer)    — PCM float32 audio → text
├── isReady()             — Model loaded and functional
├── getAvailableModels()  — List downloaded models
└── unloadModel()         — Free memory
```

### Validation Criteria (Test-First)
1. `WhisperProvider.loadModel('tiny')` succeeds when model file exists
2. `WhisperProvider.loadModel('tiny')` returns graceful error when model missing
3. `WhisperProvider.transcribe(silentBuffer)` returns empty string for silence
4. `WhisperProvider.transcribe(audioBuffer)` returns non-empty text for speech
5. `WhisperProvider.isReady()` returns false before loadModel, true after
6. `WhisperProvider.getAvailableModels()` lists downloaded model files
7. `WhisperProvider.unloadModel()` frees resources, `isReady()` returns false
8. Transcription works with 16kHz mono PCM float32 input format
9. Provider handles concurrent transcription requests sequentially (queue)
10. All tests pass with mocked whisper.cpp bindings (no native dependency in CI)

### Socratic Inquiry

**Boundary:** *What is the minimal audio format WhisperProvider accepts?*
Whisper requires 16kHz mono PCM float32. Any format conversion (resampling, channel mixing) belongs in AudioCapture (J.2), not here. WhisperProvider receives ready-to-transcribe buffers.

**Inversion:** *What if whisper.cpp isn't installed or the model file is missing?*
`isReady()` returns false. `transcribe()` throws a typed error. The TranscriptionPipeline (J.3) handles the fallback — WhisperProvider just reports its state honestly.

**Constraint Discovery:** *Should we use whisper.cpp as a native Node addon or a subprocess?*
Subprocess is simpler and more portable — spawn whisper-cli with audio piped via stdin. Native addon gives better performance but requires node-gyp builds. Start with subprocess, measure latency, upgrade to addon only if needed.

**Precedent:** *How does OllamaProvider handle model management?*
OllamaProvider delegates to Ollama's own model management. WhisperProvider must handle this itself — download model files, verify checksums, track available models. Follow the same pattern as OllamaLifecycle for health/readiness.

**Tension:** *GPU acceleration vs. CPU-only for Whisper?*
Whisper tiny/base run real-time on CPU. GPU helps for larger models but competes with LLM VRAM. Default to CPU-only — it's sufficient for real-time tiny/base and avoids VRAM contention.

### Boundary Constraints
- Creates `src/main/voice/whisper-provider.ts` (~150-200 lines)
- Creates `tests/sprint-4/voice/whisper-provider.test.ts`
- Does NOT handle microphone access (that's J.2)
- Does NOT handle streaming/partial results (that's J.3)
- Does NOT download models automatically (that's a setup wizard concern, S6)
- All tests use mocked whisper.cpp — no native binary required in CI

### Files to Read
1. `src/main/providers/ollama-provider.ts` — Provider pattern precedent
2. `src/main/ollama-lifecycle.ts` — Model management pattern precedent

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-j-phase-1.md` before closing.
