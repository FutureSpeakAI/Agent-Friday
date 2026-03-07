# Phase L.1 — The Dialogue
## VoiceCircle: The Complete Hear→Think→Speak Integration

### Hermeneutic Focus
*Each organ exists — the ear, the listener, the stream, the mouth, the timbre, the utterance. But do they form a living conversation? This phase tests the full voice circle: microphone → transcription → LLM → synthesis → speaker, with turn-taking and interrupt handling. Only through integration does the whole reveal what the parts could not.*

### Current State (Post-K.3)
- TranscriptionPipeline: mic → VAD → Whisper → text events
- SpeechSynthesis: text → TTS → audio output with queue/interrupt
- LLM pipeline: local-first routing from Sprint 3
- No conversation coordination exists
- No turn-taking logic exists
- No barge-in handling exists

### Architecture Context
```
Voice Circle Integration
├── TranscriptionPipeline.onTranscript → LLM input
├── LLM response → SpeechSynthesis.speak
├── TranscriptionPipeline.onVoiceStart → SpeechSynthesis.stop (barge-in)
├── SpeechSynthesis.onQueueEmpty → TranscriptionPipeline.start (listen again)
└── Graceful degradation at every node
```

### Validation Criteria (Test-First)
1. Transcribed text flows to the LLM as a chat message
2. LLM text response flows to SpeechSynthesis for speaking
3. Barge-in: user voice-start interrupts Agent Friday's speech
4. Turn-taking: system listens after finishing speaking
5. Voice circle works with local LLM (Ollama) when available
6. Voice circle works with cloud LLM when local unavailable (gated)
7. System degrades gracefully when STT unavailable (text-only input)
8. System degrades gracefully when TTS unavailable (text-only output)
9. System degrades gracefully when both STT and TTS unavailable
10. Full round-trip latency tracked: voice-end → speech-start < 3s target

### Socratic Inquiry

**Boundary:** *Where does conversation state live?*
Not in any voice module. The existing chat/conversation system manages message history. Voice modules are I/O adapters — TranscriptionPipeline is an input source, SpeechSynthesis is an output renderer. The LLM conversation doesn't know or care whether input came from keyboard or microphone.

**Inversion:** *What if the LLM takes 10 seconds to respond?*
Acceptable for complex queries. The system should indicate "thinking" state — perhaps a brief audio cue or visual indicator. SpeechSynthesis doesn't queue empty content. The UI shows the LLM is processing.

**Constraint Discovery:** *How to handle overlapping audio — mic picking up speakers?*
Echo cancellation. During SpeechSynthesis playback, either mute the microphone or enable acoustic echo cancellation (AEC). Simplest approach: pause TranscriptionPipeline while speaking, resume on queue-empty. More sophisticated: AEC via WebRTC's built-in processing.

**Precedent:** *How does the integration test from Sprint 3 (I.1) work?*
Sprint 3's integration test verifies the LLM intelligence circle (local → assess → gate → route). This test adds the voice perception/output layers on top. Same pattern: mock external dependencies, verify the flow end-to-end.

**Synthesis:** *What is the minimal viable voice conversation?*
User speaks → text transcribed → sent as chat message → LLM responds → response spoken → system listens again. That's the circle. Everything else (barge-in, echo cancellation, streaming) is optimization.

**Safety Gate:** *Does voice data ever leave the machine?*
STT is always local (Whisper). TTS is always local (Kokoro/Piper). The only data that MIGHT leave is the transcribed TEXT, and only if the LLM routes to cloud — which requires CloudGate consent from Sprint 3. Voice audio never leaves.

### Boundary Constraints
- Creates `tests/sprint-4/integration/voice-circle.test.ts` (~200-250 lines)
- Does NOT create new production modules — only integration test
- Does NOT implement echo cancellation (future optimization)
- Mocks: WhisperProvider, TTSEngine, LLM provider
- Tests the wiring, not the individual components

### Files to Read
1. `tests/sprint-3/integration/local-intelligence-circle.test.ts` — Integration test pattern
2. `src/main/voice/transcription-pipeline.ts` — Input event API
3. `src/main/voice/speech-synthesis.ts` — Output control API

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-l-phase-1.md` before closing.
Write `evolution/sprint-4-review.md` — this completes the fourth full sprint.
