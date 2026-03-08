# Track K Phase 3: The Utterance -- SpeechSynthesis

**Date:** 2026-03-07
**Sprint:** 4
**Track:** K (Voice)
**Phase:** K.3

## What Was Built

SpeechSynthesisManager singleton that manages utterance queuing, interrupts,
pause/resume, and audio output coordination for Agent Friday.

### Files Created

- **Implementation:** src/main/voice/speech-synthesis.ts (~245 lines)
- **Tests:** tests/sprint-4/voice/speech-synthesis.test.ts (10 tests)

### API Surface

- speak(text, opts?) - queue text for synthesis, starts if idle
- speakImmediate(text) - interrupt current speech, play this immediately
- stop() - halt playback and clear the queue
- pause() - suspend processing (current synthesis completes)
- resume() - continue processing after pause
- isSpeaking() - whether speech is actively being synthesized
- getQueueLength() - number of queued utterances (excluding active)
- on(event, cb) - subscribe to events, returns unsubscribe function

### Events

- utterance-start - utterance synthesis began (payload: UtteranceEvent)
- utterance-end - utterance synthesis finished (payload: UtteranceEvent)
- queue-empty - all queued utterances have been processed
- interrupted - speech was interrupted by speakImmediate or stop

### Architecture Decisions

- **Generation counter for cancellation**: Instead of trying to abort
  in-flight promises, a generation counter is bumped on interrupt/stop.
  Each processLoop captures the generation at start and exits early when
  it detects a mismatch. This prevents stale loops from interfering with
  new ones.

- **Sentence chunking**: Long text is split at sentence boundaries
  (`.`, `?`, `!` followed by whitespace) for faster time-to-first-audio.
  Each sentence is synthesized independently via TTSEngine.

- **Event loop yield between queue items**: A macrotask yield
  (setTimeout(0)) between processing queue items allows external code
  (pause/stop) to take effect between utterances.

- **Queue depth limit**: Max 5 queued utterances. Overflow drops the
  oldest item (FIFO), resolving its promise silently.

- **IPC audio delivery**: Synthesized PCM buffers are sent to all
  renderer windows via BrowserWindow.getAllWindows() and
  webContents.send('voice:play-chunk', audio). Mocked in tests.

### Validation Results

All 10 tests pass:
1. speak(text) queues text and begins synthesis if idle
2. Multiple speak() calls queue utterances in FIFO order
3. speakImmediate(text) interrupts current and plays new text
4. stop() halts current playback and clears the queue
5. pause() suspends processing and resume() continues
6. isSpeaking() reflects actual state
7. utterance-start and utterance-end events fire correctly
8. queue-empty fires when last queued utterance completes
9. Long text is chunked at sentence boundaries
10. Queue max depth of 5 is enforced, oldest dropped

### Safety Gate

- `npx tsc --noEmit`: 0 errors
- `npx vitest run`: 111 test files, 4155 tests passed, 0 failures
