# Phase J.3 — The Stream
## TranscriptionPipeline: Wiring Ear to Mind

### Hermeneutic Focus
*The ear can transcribe and the listener can hear, but they aren't connected. This phase wires VAD events to transcription calls and emits structured text events that the LLM can process. The stream is the nerve fiber connecting perception to cognition.*

### Current State (Post-J.2)
- WhisperProvider transcribes PCM buffers to text
- AudioCapture provides VAD events and audio chunks
- No pipeline connects them
- No streaming/partial transcription exists

### Architecture Context
```
TranscriptionPipeline (this phase)
├── start()              — Begin listening (starts AudioCapture + WhisperProvider)
├── stop()               — Stop listening, cleanup
├── isListening()        — Pipeline active
├── onTranscript(cb)     — Final transcription result
├── onPartialTranscript(cb) — Streaming partial result
├── onError(cb)          — Transcription errors
└── getStats()           — Latency, accuracy metrics
```

### Validation Criteria (Test-First)
1. `start()` initializes AudioCapture and WhisperProvider
2. `stop()` cleanly shuts down both subsystems
3. VAD voice-start begins buffering audio chunks
4. VAD voice-end triggers transcription of buffered audio
5. `onTranscript` fires with final text after voice-end
6. `onPartialTranscript` fires periodically during long utterances (>2s)
7. Pipeline handles WhisperProvider unavailability gracefully
8. Pipeline handles AudioCapture failure gracefully
9. `getStats()` tracks average transcription latency
10. Multiple rapid utterances queue correctly (no dropped audio)

### Socratic Inquiry

**Boundary:** *When does a "partial" result fire vs. a "final" result?*
Partial fires every ~2 seconds during continuous speech (re-transcribe growing buffer). Final fires when VAD detects voice-end. This gives the UI real-time feedback while maintaining accuracy.

**Inversion:** *What if the user speaks for 5 minutes straight?*
Whisper handles up to 30 seconds well. For longer utterances, segment at natural pauses (VAD micro-pauses) or at 30-second boundaries. Each segment transcribes independently and results concatenate.

**Constraint Discovery:** *How does the pipeline know if transcription quality is poor?*
The ConfidenceAssessor from Sprint 3 evaluates LLM output, not STT output. For STT, heuristics suffice: very short transcriptions from long audio suggest errors, repeated "uh" patterns suggest the model heard noise, empty results from non-silent audio suggest failure.

**Precedent:** *How does the existing event bridge pattern work?*
EventEmitter-based with typed events. TranscriptionPipeline should follow the same pattern — typed event names, callback signatures, cleanup on stop().

**Safety Gate:** *Can transcription data leak to cloud providers?*
Never. WhisperProvider runs locally. Transcribed text flows to the LLM, which by Sprint 3 defaults to local-first routing. The CloudGate applies to LLM requests, not to raw transcription.

### Boundary Constraints
- Creates `src/main/voice/transcription-pipeline.ts` (~120-150 lines)
- Creates `tests/sprint-4/voice/transcription-pipeline.test.ts`
- Does NOT modify WhisperProvider or AudioCapture
- Does NOT handle TTS output (that's Track K)
- Does NOT handle conversation turn-taking (that's L.1)

### Files to Read
1. `src/main/voice/whisper-provider.ts` — Transcription API
2. `src/main/voice/audio-capture.ts` — VAD events API

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-j-phase-3.md` before closing.
