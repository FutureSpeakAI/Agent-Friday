# Session Journal: Track J, Phase 1 —"The Ear"

**Date:** 2026-03-07
**Tests added:** 10 (total: 4,105 across 106 files)
**New lines:** ~220 (`src/main/voice/whisper-provider.ts`) + ~60 (`src/main/voice/whisper-binding.ts`)

## What Was Built

`WhisperProvider` — a singleton that wraps whisper.cpp for local speech-to-text transcription. Accepts raw 16kHz mono PCM Float32Array audio and returns transcribed text with timed segments.

### Architecture Decision: Binding Abstraction Layer

The `WhisperProvider` does not directly interact with whisper.cpp. Instead, it imports a `whisper-binding` module that defines the interface (`loadModel`, `transcribe`, `freeModel`). This allows:

1. **Clean testing** — `vi.mock()` replaces the entire binding module, no native binary needed in CI.
2. **Future flexibility** — can swap subprocess spawn for native addon without touching `WhisperProvider`.
3. **Concern separation** — provider handles model lifecycle, queuing, and result normalization; binding handles C++ interop.

### Key Design Choices

1. **Sequential queue for transcription** — whisper.cpp is single-threaded on CPU. Concurrent `Promise.all()` calls are queued internally and processed one-by-one. Each caller gets its own promise resolved in order.

2. **Model file existence check before load** — `access()` is called before `binding.loadModel()` to give a clear "model file not found" error instead of a cryptic C++ crash.

3. **Duration calculated from buffer length** — `audio.length / 16000` gives exact duration in seconds. No reliance on whisper’s reported duration.

4. **Model discovery via filesystem scan** — `getAvailableModels()` reads `~/.nexus-os/models/whisper/` and parses `ggml-{size}.bin` filenames. Gracefully returns empty array if directory missing.

5. **Singleton pattern matches OllamaLifecycle** — `static getInstance()` + `static resetInstance()`, plus exported `whisperProvider` convenience instance.

## Patterns Established

- **`vi.hoisted()` for mock state**: All mock functions created inside `vi.hoisted()` so they are available when `vi.mock()` factories execute (hoisted to top of file).
- **Binding abstraction for native code**: Separate module for native interactions, entirely mocked in tests.
- **Queue-based serialization**: Promise-based queue for sequential processing of concurrent requests.

## What Phase J.2 Should Know

1. `WhisperProvider.transcribe()` expects `Float32Array` at 16kHz mono. AudioCapture must resample/mix before passing data.
2. `isReady()` must be checked before calling `transcribe()` — it throws if no model loaded.
3. The provider queues requests internally. AudioCapture does not need its own queuing logic.
4. Model path is `~/.nexus-os/models/whisper/ggml-{size}.bin`. The setup wizard (S6) will handle downloads.
5. Import via `import { whisperProvider } from '../voice/whisper-provider'` for the singleton.
