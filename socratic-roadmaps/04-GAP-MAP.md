# Sprint 4 Gap Map — "The Voice"

## Baseline
- Tests: ~4,100 (after Sprint 3)
- Sprint 3 delivered: OllamaProvider, EmbeddingPipeline, OllamaLifecycle, ConfidenceAssessor, CloudGate, local-first routing

## What Exists (Post-Sprint 3)

| Module | Location | Status |
|--------|----------|--------|
| OllamaProvider | `src/main/providers/ollama-provider.ts` | Handles /api/chat, /api/embed, /api/tags |
| OllamaLifecycle | `src/main/ollama-lifecycle.ts` | Health polling, model discovery, VRAM tracking |
| Electron IPC | `src/main/ipc-handlers.ts` | Main↔renderer communication |
| BrowserWindow | `src/main/window-manager.ts` | Window lifecycle management |

## What's Missing

No audio infrastructure exists. No microphone access, no speech recognition, no text-to-speech. The system communicates only through text. Agent Friday cannot hear or speak.

### Gap J — The Ear (Speech-to-Text)

The system has no way to capture audio or transcribe speech. Whisper.cpp provides MIT-licensed local STT that runs on CPU (no VRAM cost). Models range from tiny (75MB, real-time on any hardware) to large-v3 (1.5GB, highest accuracy).

| Subgap | Description | Delivered By |
|--------|-------------|-------------|
| J.1 | WhisperProvider — Load whisper.cpp, manage models, transcribe audio buffers | Phase J.1 |
| J.2 | AudioCapture — Microphone access via Electron, voice activity detection (VAD), audio buffering | Phase J.2 |
| J.3 | TranscriptionPipeline — Wire VAD → buffer → transcribe → emit text events, streaming partial results | Phase J.3 |

### Gap K — The Mouth (Text-to-Speech)

The system cannot produce spoken output. Kokoro (Apache 2.0) provides high-quality neural TTS that runs on CPU. Piper (MIT) serves as a lighter fallback. Both are always-local — voice data never leaves the machine.

| Subgap | Description | Delivered By |
|--------|-------------|-------------|
| K.1 | TTSEngine — Load Kokoro/Piper, manage voice models, synthesize speech from text | Phase K.1 |
| K.2 | VoiceProfileManager — Voice selection, speed/pitch settings, per-profile preferences | Phase K.2 |
| K.3 | SpeechSynthesis — Queue management, SSML-lite support, interrupt handling, audio output routing | Phase K.3 |

### Gap L — The Dialogue (Voice Circle)

Even with STT and TTS individually working, the full voice loop (hear → think → speak) has never been tested end-to-end. Integration must verify: microphone → transcription → LLM processing → TTS → speaker output, with proper turn-taking and interrupt handling.

| Subgap | Description | Delivered By |
|--------|-------------|-------------|
| L.1 | VoiceCircle integration test — Full hear→think→speak loop, interrupt handling, graceful degradation | Phase L.1 |

## Hardware Budget

```
Voice Stack VRAM: 0 GB (all CPU-based)
Voice Stack RAM:  ~0.5-1.5 GB (Whisper model + Kokoro model)
Voice Stack CPU:  Moderate (real-time on 4+ core machines)
```

Voice does NOT compete with the LLM for VRAM. This is by design.

## Technology Choices

| Component | Primary | Fallback | License | Runs On |
|-----------|---------|----------|---------|---------|
| STT | whisper.cpp (via addon or subprocess) | Web Speech API (cloud, degraded) | MIT | CPU |
| TTS | Kokoro | Piper | Apache 2.0 / MIT | CPU |
| VAD | Silero VAD (ONNX) | Energy-based threshold | MIT | CPU |
| Audio I/O | Electron desktopCapturer + getUserMedia | N/A | Electron built-in | System |
