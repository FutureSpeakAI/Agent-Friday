# Track K Phase 1: The Mouth -- TTSEngine

## Session Journal

**Date:** 2026-03-07
**Track:** K -- Text-to-Speech
**Phase:** K.1 -- TTSEngine (local TTS synthesis)
**Status:** Complete

## What Was Built

### New Files
- `src/main/voice/tts-binding.ts` (~60 lines) -- Abstraction layer for TTS native bindings
- `src/main/voice/tts-engine.ts` (~180 lines) -- Singleton TTS engine with Kokoro/Piper backend support
- `tests/sprint-4/voice/tts-engine.test.ts` (~240 lines, 10 tests) -- Full test coverage

### Architecture Decisions
1. **Binding pattern:** Mirrors `whisper-binding.ts` -- thin interface, fully mockable in tests.
2. **Backend fallback:** `loadEngine()` tries Kokoro first, then Piper.
3. **Sequential queue:** Same promise-based queue pattern as WhisperProvider.
4. **24kHz mono PCM Float32:** Standard output format matching common TTS model outputs.
5. **Voice discovery:** Scans model directory for .onnx files, builds VoiceInfo list.

### Validation Criteria (10/10 passing)
1. loadEngine(kokoro) succeeds when Kokoro model exists
2. loadEngine(piper) succeeds as fallback when Piper model exists
3. loadEngine() returns graceful error when no TTS model found
4. synthesize(text) returns PCM audio buffer for valid text
5. synthesize empty returns empty buffer for empty input
6. synthesizeStream(text) yields audio chunks progressively
7. isReady() false before load, true after
8. getAvailableVoices() lists downloaded voice models
9. Output format is 24000Hz mono PCM float32
10. All tests use mocked TTS backend -- no native binary in CI

## Methodology
- Test-first: All 10 tests written before implementation
- Safety gate: npx tsc --noEmit and npx vitest run passes with 0 errors
