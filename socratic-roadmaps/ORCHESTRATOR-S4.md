# Orchestrator — Agent Friday v2.2 Sprint 4: "The Voice"

## Execution Order

```
J.1 ──→ J.2 ──→ J.3 ──→ K.1 ──→ K.2 ──→ K.3 ──→ L.1
 │       │       │       │       │       │       │
 │       │       │       │       │       │       └─ The Dialogue (voice circle integration)
 │       │       │       │       │       └─ The Utterance (speech synthesis + queue)
 │       │       │       │       └─ The Timbre (voice profile management)
 │       │       │       └─ The Mouth (TTS engine core)
 │       │       └─ The Stream (transcription pipeline)
 │       └─ The Listener (audio capture + VAD)
 └─ The Ear (Whisper provider)
```

### Rationale for Sequential Order

- **J.1 → J.2 → J.3** strictly sequential: J.1 creates WhisperProvider (model loading + transcription), J.2 builds AudioCapture (mic access + VAD), J.3 wires both into a streaming pipeline
- **K.1 → K.2 → K.3** strictly sequential: K.1 creates TTSEngine (synthesis core), K.2 adds voice profile selection on top, K.3 adds queue management and audio output routing
- **J before K**: The ear before the mouth — transcription pipeline validates the audio infrastructure patterns that TTS also needs (model loading, audio buffer handling)
- **L.1** last: Integration test verifies the full hear→think→speak circle

## Dependency Graph

```
                    ┌──────────┐
                    │   J.1    │  WhisperProvider
                    │  The Ear │  whisper.cpp model
                    │          │  load + transcribe
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   J.2    │  AudioCapture
                    │   The    │  Mic access, VAD,
                    │ Listener │  audio buffering
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   J.3    │  TranscriptionPipeline
                    │   The    │  VAD → buffer →
                    │  Stream  │  transcribe → emit
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   K.1    │  TTSEngine
                    │   The    │  Kokoro/Piper model
                    │  Mouth   │  load + synthesize
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   K.2    │  VoiceProfileManager
                    │   The    │  Voice selection,
                    │  Timbre  │  speed/pitch prefs
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   K.3    │  SpeechSynthesis
                    │   The    │  Queue, SSML-lite,
                    │Utterance │  interrupt, output
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │   L.1    │  Full voice circle
                    │   The    │  hear → think → speak
                    │Dialogue  │  integration tests
                    └──────────┘
```

## Launch Prompts

### Phase J.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/04-GAP-MAP.md
3. socratic-roadmaps/track-j/phase-1-the-ear.md
4. socratic-roadmaps/evolution/sprint-3-review.md
5. socratic-roadmaps/contracts/whisper-provider.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate (npx tsc --noEmit && npx vitest run).
Write a session journal to socratic-roadmaps/journals/track-j-phase-1.md before closing.
```

### Phase J.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/04-GAP-MAP.md
3. socratic-roadmaps/track-j/phase-2-the-listener.md
4. socratic-roadmaps/journals/track-j-phase-1.md
5. socratic-roadmaps/contracts/audio-capture.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-j-phase-2.md before closing.
```

### Phase J.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/04-GAP-MAP.md
3. socratic-roadmaps/track-j/phase-3-the-stream.md
4. socratic-roadmaps/journals/track-j-phase-2.md
5. socratic-roadmaps/contracts/whisper-provider.md
6. socratic-roadmaps/contracts/audio-capture.md
7. socratic-roadmaps/contracts/transcription-pipeline.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-j-phase-3.md before closing.
```

### Phase K.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/04-GAP-MAP.md
3. socratic-roadmaps/track-k/phase-1-the-mouth.md
4. socratic-roadmaps/journals/track-j-phase-3.md
5. socratic-roadmaps/contracts/tts-engine.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-k-phase-1.md before closing.
```

### Phase K.2

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/04-GAP-MAP.md
3. socratic-roadmaps/track-k/phase-2-the-timbre.md
4. socratic-roadmaps/journals/track-k-phase-1.md
5. socratic-roadmaps/contracts/tts-engine.md
6. socratic-roadmaps/contracts/voice-profile.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-k-phase-2.md before closing.
```

### Phase K.3

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/04-GAP-MAP.md
3. socratic-roadmaps/track-k/phase-3-the-utterance.md
4. socratic-roadmaps/journals/track-k-phase-2.md
5. socratic-roadmaps/contracts/tts-engine.md
6. socratic-roadmaps/contracts/voice-profile.md
7. socratic-roadmaps/contracts/voice-circle.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-k-phase-3.md before closing.
```

### Phase L.1

```
Read these files in order, then begin implementation:
1. socratic-roadmaps/00-SOCRATIC-METHODOLOGY.md
2. socratic-roadmaps/04-GAP-MAP.md
3. socratic-roadmaps/track-l/phase-1-the-dialogue.md
4. socratic-roadmaps/journals/track-k-phase-3.md
5. socratic-roadmaps/contracts/whisper-provider.md
6. socratic-roadmaps/contracts/tts-engine.md
7. socratic-roadmaps/contracts/voice-circle.md

Write failing tests for the validation criteria first.
Then answer the Socratic questions by making the tests pass.
End by verifying the Safety Gate.
Write a session journal to socratic-roadmaps/journals/track-l-phase-1.md before closing.
Write evolution/sprint-4-review.md — this completes the fourth full sprint.
```

## Context Budget Verification

Each launch prompt reads at most 7 files:

| File | Est. Lines |
|------|-----------|
| Methodology (pruned) | ~80 |
| Gap map (focused) | ~80 |
| Phase file | ~80 |
| Previous journal | ~40 |
| Contract 1 | ~30 |
| Contract 2 | ~30 |
| Contract 3 (if needed) | ~30 |
| **Total** | **~370** |

Within the ~430 line ceiling. Leaves ~300+ lines for code reading and test output.

## Verification Checkpoints

After each phase:
1. `npx tsc --noEmit` — 0 errors
2. `npx vitest run` — all tests pass
3. New tests added by the phase also pass
4. Git commit checkpoint with descriptive message
5. Session journal written

After all 7 phases:
1. Full test suite green (~4,250+ tests expected)
2. WhisperProvider loads models and transcribes audio buffers
3. AudioCapture accesses microphone with VAD
4. TranscriptionPipeline streams partial transcription results
5. TTSEngine synthesizes speech from text via Kokoro/Piper
6. VoiceProfileManager persists voice preferences
7. SpeechSynthesis handles queuing, interrupts, and audio output
8. Voice circle integration: hear → think → speak verified
9. System works fully without voice models (graceful degradation)
10. No regressions in Sprint 1-3 test suites
