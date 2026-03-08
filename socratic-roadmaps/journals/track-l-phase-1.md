# Track L Phase 1: The Dialogue -- Voice Circle Integration

**Date:** 2026-03-08
**Sprint:** 4
**Track:** L (Voice Integration)
**Phase:** L.1

## What Was Built

Integration test suite validating the full voice conversation circle:
speech recognition (TranscriptionPipeline) flows to LLM reasoning
(llmClient) flows to speech synthesis (SpeechSynthesisManager), with
barge-in interruption, turn-taking, and graceful degradation when
voice subsystems are unavailable.

This is an INTEGRATION TEST ONLY phase -- no new production modules were
created. The test validates that Sprint 4 voice modules (Tracks J and K)
compose correctly into a complete voice conversation loop.

### Files Created

- **Integration test:** tests/sprint-4/integration/voice-circle.test.ts (10 tests)

### Voice Circle Architecture

```
User speaks
    |
    v
AudioCapture  (voice-start / voice-end / audio-chunk events)
    |
    v
TranscriptionPipeline.on("transcript") --> transcript text
    |
    v
llmClient.complete({ messages: [{ role: "user", content: text }] })
    |
    v
LLMResponse.content
    |
    v
SpeechSynthesisManager.speak(response.content)
    |
    v
TTS Engine --> audio output --> user hears Agent Friday

--- Barge-in path ---
AudioCapture voice-start --> synth.stop() (interrupt speech)

--- Turn-taking path ---
SpeechSynthesisManager queue-empty --> pipeline.start() (resume listening)
```


### Integration Wiring (wireVoiceCircle)

- **Transcript to LLM to Speak**: pipeline.on('transcript') triggers
  llmClient.complete() then synth.speak() with the response
- **Barge-in**: audioCapture voice-start checks synth.isSpeaking() and
  calls synth.stop() to interrupt
- **Turn-taking**: synth queue-empty event restarts the pipeline if not
  already listening

### Architecture Decisions

- **Integration test wires real modules with mocked boundaries**: The test
  imports real TranscriptionPipeline and SpeechSynthesisManager singletons
  but mocks their dependencies (AudioCapture, WhisperProvider, TTSEngine,
  VoiceProfileManager, Electron). This validates the actual event flow
  and state management without requiring hardware.

- **wireVoiceCircle() as the integration glue**: A helper function wires
  the three event subscriptions (transcript->LLM->speak, barge-in,
  turn-taking). This function represents the production wiring that will
  exist in the main process initialization.

- **Error-tolerant speak path**: The wireVoiceCircle wraps synth.speak()
  in try/catch so TTS failures do not break the voice circle. The system
  degrades to text-only output without crashing.

- **Never-resolving mock for barge-in test**: The barge-in test uses a
  TTS synthesize mock that never resolves, keeping synth.isSpeaking()
  true until stop() is called. This accurately models a long utterance
  being interrupted mid-synthesis.

### Validation Results

All 10 tests pass:
1. Transcribed text flows to the LLM as a chat message
2. LLM text response flows to SpeechSynthesis for speaking
3. Barge-in: user voice-start interrupts Agent Friday speech
4. Turn-taking: system listens after finishing speaking
5. Voice circle works with local LLM (Ollama) when available
6. Voice circle works with cloud LLM when local unavailable (gated)
7. System degrades gracefully when STT unavailable (text-only input)
8. System degrades gracefully when TTS unavailable (text-only output)
9. System degrades gracefully when both STT and TTS unavailable
10. Full round-trip latency tracked: voice-end to speech-start < 3s target

### Safety Gate

- `npx tsc --noEmit`: 0 errors
- `npx vitest run`: 112 test files, 4165 tests passed, 0 failures