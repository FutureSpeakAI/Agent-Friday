# Phase K.3 — The Utterance
## SpeechSynthesis: Queue, Interrupts, and Audio Output

### Hermeneutic Focus
*The mouth can form words and the timbre gives them character, but there's no way to manage the flow of speech. This phase adds the coordination layer — queuing multiple utterances, handling interruptions when the user speaks, and routing audio to the system speakers.*

### Current State (Post-K.2)
- TTSEngine synthesizes audio from text
- VoiceProfileManager manages voice preferences
- No utterance queue exists
- No interrupt handling exists
- No audio output routing exists

### Architecture Context
```
SpeechSynthesis (this phase)
├── speak(text, opts?)     — Queue text for speaking
├── speakImmediate(text)   — Interrupt current speech, speak this
├── stop()                 — Stop all speech, clear queue
├── pause()                — Pause current utterance
├── resume()               — Resume paused utterance
├── isSpeaking()           — Currently producing audio
├── getQueueLength()       — Pending utterances
├── onUtteranceStart(cb)   — Utterance began playing
├── onUtteranceEnd(cb)     — Utterance finished playing
└── onQueueEmpty(cb)       — All queued speech complete
```

### Validation Criteria (Test-First)
1. `speak(text)` queues text and begins synthesis if idle
2. Multiple `speak()` calls queue utterances in FIFO order
3. `speakImmediate(text)` interrupts current speech and plays new text
4. `stop()` halts current playback and clears the queue
5. `pause()` suspends audio output, `resume()` continues from pause point
6. `isSpeaking()` reflects actual audio output state
7. `onUtteranceStart/End` events fire at correct times
8. `onQueueEmpty` fires when last queued utterance completes
9. Long text is chunked at sentence boundaries for streaming playback
10. Audio output uses Electron's audio APIs (Web Audio API in renderer)

### Socratic Inquiry

**Boundary:** *Where does audio playback happen — main or renderer?*
Web Audio API runs in the renderer. SpeechSynthesis in main generates PCM buffers, sends them via IPC to the renderer, where a playback worker handles output. This mirrors AudioCapture's architecture (renderer↔main split).

**Inversion:** *What if the user starts speaking while Agent Friday is talking?*
This is the "barge-in" problem. The TranscriptionPipeline detects voice-start → SpeechSynthesis receives an interrupt signal → current speech stops → system listens to user. This is wired in L.1, but the interrupt API must exist here.

**Constraint Discovery:** *How to chunk long responses for streaming TTS?*
Split at sentence boundaries (period, question mark, exclamation). Send each sentence to TTSEngine individually. Start playing the first sentence's audio while synthesizing the second. This gives <500ms time-to-first-audio for long responses.

**Precedent:** *How does the existing IPC handle streaming data from main to renderer?*
Use `webContents.send()` for audio chunks, same pattern as the event bridge. The renderer accumulates chunks in an AudioBuffer and feeds them to an AudioContext for playback.

**Tension:** *Queue depth — should there be a limit?*
Yes. If 10+ utterances are queued, something is wrong (LLM generating faster than TTS can speak). Cap at ~5 utterances, drop oldest if exceeded. Log a warning.

### Boundary Constraints
- Creates `src/main/voice/speech-synthesis.ts` (~140-170 lines)
- Creates `src/renderer/voice/audio-playback.ts` (~80-100 lines)
- Creates `tests/sprint-4/voice/speech-synthesis.test.ts`
- Does NOT modify TTSEngine or VoiceProfileManager
- Does NOT handle conversation turn-taking logic (that's L.1)
- IPC channels: `voice:play-chunk`, `voice:stop-playback`, `voice:playback-state`

### Files to Read
1. `src/main/voice/tts-engine.ts` — Synthesis API
2. `src/main/voice/voice-profile-manager.ts` — Active profile for synthesis
3. `src/renderer/voice/audio-capture-renderer.ts` — Renderer audio pattern from J.2

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-k-phase-3.md` before closing.
