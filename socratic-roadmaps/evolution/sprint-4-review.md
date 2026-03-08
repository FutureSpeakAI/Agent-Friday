# Sprint 4 Review -- "The Voice"

**Sprint period:** Sessions across multiple days
**Test count:** 4,095 (Sprint 3 end) -> 4,165 (Sprint 4 end) = **+70 tests**
**Test files:** 112 (all passing)
**Safety Gate:** PASSED on every phase

## Sprint Goal

Build the complete voice pipeline for Agent Friday: speech-to-text
transcription via local Whisper models, audio capture with voice activity
detection, real-time transcription pipeline, text-to-speech synthesis,
voice profile management, and a full voice conversation circle with
barge-in interruption and turn-taking. Agent Friday can listen, think,
and speak -- completing the dialogue loop.

## Phase Summary

| Phase | Track | Name | Tests Added | Key Deliverable |
|-------|-------|------|-------------|-----------------|
| J.1 | J | The Ear | +10 | WhisperProvider -- local Whisper.cpp STT integration |
| J.2 | J | The Listener | +10 | AudioCapture -- microphone capture with VAD |
| J.3 | J | The Stream | +10 | TranscriptionPipeline -- real-time speech-to-text flow |
| K.1 | K | The Mouth | +10 | TTSEngine -- text-to-speech synthesis backend |
| K.2 | K | The Timbre | +10 | VoiceProfileManager -- voice profiles and preferences |
| K.3 | K | The Utterance | +10 | SpeechSynthesisManager -- utterance queue, interrupts, pause/resume |
| L.1 | L | The Dialogue | +10 | Integration test: full voice circle (10 criteria) |

**Total: 7 phases, 6 modules built, 1 integration suite, +70 tests**

## Architecture Overview

```
User speaks into microphone
    |
    v
AudioCapture (VAD: voice-start -> audio-chunk -> voice-end)
    |
    v
TranscriptionPipeline (buffers audio, transcribes on voice-end)
    |
    +---> WhisperProvider.transcribe(Float32Array)
    |         |
    |         v
    |     Whisper.cpp (local GGML model)
    |         |
    |         v
    |     TranscriptEvent { text, language, duration, latencyMs }
    |
    v
wireVoiceCircle: transcript -> llmClient.complete() -> synth.speak()
    |
    +---> LLM (local Ollama or cloud Anthropic)
    |         |
    |         v
    |     LLMResponse.content
    |
    v
SpeechSynthesisManager (sentence chunking, queue, generation counter)
    |
    +---> TTSEngine.synthesize(text) -> Float32Array PCM
    +---> VoiceProfileManager.getActiveProfile() -> voice settings
    |
    v
BrowserWindow.webContents.send("voice:play-chunk", audio)

--- Interrupt paths ---
voice-start during speech --> synth.stop() (barge-in)
queue-empty after speech  --> pipeline.start() (turn-taking)
```

## Architecture Decisions

### 1. Singleton Pattern with resetInstance()
All voice modules (AudioCapture, TranscriptionPipeline, SpeechSynthesisManager)
use the singleton-with-reset pattern. The static resetInstance() method stops
the instance and nullifies the reference, enabling clean test isolation without
module-level state leakage.

### 2. Event-Driven Pipeline Composition
The voice pipeline is composed entirely through events: AudioCapture emits
voice-start/voice-end/audio-chunk, TranscriptionPipeline emits transcript,
SpeechSynthesisManager emits utterance-start/utterance-end/queue-empty.
This allows each module to be tested independently and composed at integration
time without circular dependencies.

### 3. Generation Counter for Speech Cancellation
SpeechSynthesisManager uses a generation counter rather than AbortController
to handle interruption. When stop() or speakImmediate() is called, the
generation bumps and in-flight processLoops detect the mismatch and exit.
This is simpler and more reliable than trying to cancel in-flight promises.

### 4. Energy-Based Voice Activity Detection
AudioCapture uses an energy-based VAD algorithm rather than a neural VAD.
This avoids loading a second ML model and keeps latency minimal. The energy
threshold approach works well for the common case (single speaker in a quiet
room) and can be enhanced later with WebRTC VAD or Silero.

### 5. Sentence Chunking for Time-to-First-Audio
SpeechSynthesisManager splits long text at sentence boundaries before
synthesis. This means the first sentence can begin playing while later
sentences are still being synthesized, dramatically reducing perceived latency.

### 6. Graceful Degradation Over Hard Failure
The voice circle degrades gracefully when components are unavailable:
- No STT: pipeline.start() catches the error and stays silent (text input still works)
- No TTS: speak() failure is caught (text output still works)
- No both: the system functions as a text-only chat
This ensures Agent Friday is always usable regardless of hardware capabilities.

## Technical Insights

### Mock Boundary Architecture
Integration tests mock at the hardware boundary (AudioCapture events,
WhisperProvider.transcribe, TTSEngine.synthesize) while letting all higher-level
logic (TranscriptionPipeline state machine, SpeechSynthesisManager queue,
llmClient provider routing) run with real code. This gives high confidence
that the modules compose correctly without requiring microphone or speaker access.

### Never-Resolving Promises for Timing Tests
The barge-in test uses a TTS mock that returns a never-resolving promise.
This keeps synth.isSpeaking() true indefinitely, accurately modeling a long
utterance being interrupted mid-synthesis. After synth.stop() bumps the
generation counter, the never-resolving promise becomes harmless (its result
is ignored).

### LLM Provider Fallback in Voice Context
The voice circle uses llmClient.complete() without specifying a provider.
This means the intelligence router from Sprint 3 handles provider selection,
including local-first routing with Ollama and cloud fallback via CloudGate.
The voice tests validate this works with both local and cloud providers.

## What Sprint 4 Proved

The voice pipeline is complete. Agent Friday can:

1. **Listen** -- Capture microphone audio with voice activity detection
2. **Understand** -- Transcribe speech to text using local Whisper models
3. **Think** -- Route transcribed text through the LLM intelligence pipeline
4. **Speak** -- Synthesize responses to audio with sentence-level streaming
5. **Respect turns** -- Resume listening after finishing speaking
6. **Handle interruption** -- Stop speaking when the user starts talking
7. **Degrade gracefully** -- Function as text-only when voice hardware is unavailable
8. **Customize voice** -- Apply voice profiles for speed, pitch, and volume

The Dialogue is not just a speech wrapper -- it is a complete conversational
system that composes voice I/O with sovereign intelligence routing.

## What Sprint 5 Builds On

Sprint 5 will connect the voice and intelligence pipelines to the context system:
- Context-aware voice conversations using hermeneutic circle from Sprint 2
- Tool execution with voice-initiated commands
- Persistent conversation memory across voice sessions
- Voice-driven workflow automation

## Metrics

- **70 new tests** across 7 phases
- **0 regressions** -- all 4,095 pre-existing tests remained green
- **6 source files created** (main process modules)
- **7 test files created** (unit + integration)
- **10 integration criteria** validated the full voice circle
- **3 tracks advanced** (J: Speech Input, K: Speech Output, L: Voice Integration)