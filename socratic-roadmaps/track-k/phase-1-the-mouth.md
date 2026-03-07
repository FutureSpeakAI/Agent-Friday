# Phase K.1 — The Mouth
## TTSEngine: Local Text-to-Speech Core

### Hermeneutic Focus
*The system can hear and think, but it cannot speak. This phase creates the vocal apparatus — loading TTS models and synthesizing speech audio from text. Kokoro (Apache 2.0) provides natural-sounding neural TTS that runs entirely on CPU.*

### Current State (Post-J.3)
- TranscriptionPipeline delivers spoken text to the system
- No text-to-speech infrastructure exists
- Kokoro is a lightweight neural TTS engine (Apache 2.0)
- Piper is an even lighter alternative (MIT license)
- Both run on CPU — no VRAM required

### Architecture Context
```
TTSEngine (this phase)
├── loadEngine(backend)  — Initialize Kokoro or Piper
├── synthesize(text)     — Text → PCM audio buffer
├── synthesizeStream(text) — Text → streaming audio chunks
├── isReady()            — Engine loaded and functional
├── getAvailableVoices() — List available voice models
├── unloadEngine()       — Free resources
└── getInfo()            — Engine name, version, capabilities
```

### Validation Criteria (Test-First)
1. `TTSEngine.loadEngine('kokoro')` succeeds when Kokoro model exists
2. `TTSEngine.loadEngine('piper')` succeeds as fallback when Piper model exists
3. `TTSEngine.loadEngine()` returns graceful error when no TTS model found
4. `TTSEngine.synthesize(text)` returns PCM audio buffer for valid text
5. `TTSEngine.synthesize('')` returns empty buffer for empty input
6. `TTSEngine.synthesizeStream(text)` yields audio chunks progressively
7. `TTSEngine.isReady()` false before load, true after
8. `TTSEngine.getAvailableVoices()` lists downloaded voice models
9. Output format is 22050Hz or 24000Hz mono PCM float32 (standard TTS output)
10. All tests use mocked TTS backend — no native binary in CI

### Socratic Inquiry

**Boundary:** *What is the minimal interface for a TTS engine?*
`synthesize(text) → audio buffer`. Everything else (streaming, voice selection, SSML) builds on top. The core contract is text-in, audio-out.

**Inversion:** *What if neither Kokoro nor Piper is installed?*
`isReady()` returns false. The system continues text-only. The UI shows that TTS is unavailable. The setup wizard (S6) handles model installation.

**Constraint Discovery:** *Kokoro vs. Piper — when to use which?*
Kokoro: higher quality, larger model (~200-500MB), more natural prosody. Piper: faster, smaller (~50-100MB), more robotic but very low latency. Default to Kokoro, fall back to Piper if Kokoro unavailable or if user prefers speed.

**Precedent:** *How does OllamaProvider abstract the model backend?*
OllamaProvider wraps HTTP calls to Ollama. TTSEngine should similarly abstract whether Kokoro or Piper is running underneath. The consumer just calls `synthesize(text)`.

**Tension:** *Synchronous synthesis vs. streaming?*
Short text (<50 words) can synthesize fully then play — latency is acceptable. Long text benefits from streaming — start playing while still synthesizing. Support both: `synthesize()` for short, `synthesizeStream()` for long.

### Boundary Constraints
- Creates `src/main/voice/tts-engine.ts` (~150-180 lines)
- Creates `tests/sprint-4/voice/tts-engine.test.ts`
- Does NOT handle voice selection/profiles (that's K.2)
- Does NOT handle audio playback/queuing (that's K.3)
- Does NOT handle SSML or markup (that's K.3)
- Kokoro/Piper run as subprocesses, not native addons

### Files to Read
1. `src/main/voice/whisper-provider.ts` — Model loading pattern from J.1
2. `src/main/providers/ollama-provider.ts` — Backend abstraction pattern

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-k-phase-1.md` before closing.
