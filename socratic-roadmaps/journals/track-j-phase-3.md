# Session Journal: Track J, Phase 3 -- "The Stream"

**Date:** 2026-03-07
**Tests added:** 10 (total: 4,125 across 108 files)
**New lines:** ~150 (`src/main/voice/transcription-pipeline.ts`)

## What Was Built

`TranscriptionPipeline` -- the nerve fiber connecting AudioCapture (perception) to WhisperProvider (cognition). Wires VAD events to Whisper transcription, managing audio buffering, partial transcription for long utterances, sequential queue processing, and performance statistics.

### Architecture Decision: Queue-Based Sequential Processing

When voice-end fires, the audio buffer is pushed to a `transcriptionQueue`. A `processQueue()` method processes items sequentially (same pattern as WhisperProvider's internal queue). This prevents concurrent transcription calls from overwhelming the CPU and ensures order-preserving delivery of transcript events.

### Key Design Choices

1. **Partial transcription at 2s intervals** -- During long utterances, the growing audio buffer is re-transcribed every 2s and emitted as `partial` events. Uses `Date.now()` delta tracking (both elapsed since speech start and since last partial). The `>=` check ensures the first partial fires exactly at the 2s mark, not after.

2. **voice-end buffer is authoritative** -- AudioCapture's `finishUtterance()` already concatenates all speech chunks into a single Float32Array. The pipeline uses this directly rather than re-merging its own internal buffer, avoiding redundant work.

3. **Error isolation** -- WhisperProvider failure at start() emits error and returns (pipeline stays stopped). AudioCapture error during operation emits error AND stops the pipeline (microphone loss is unrecoverable). Transcription errors for individual utterances emit error but the pipeline keeps listening for the next utterance.

4. **Stats via processingTime** -- Latency is tracked from WhisperProvider's `processingTime` field (not wall-clock delta), giving accurate CPU-time measurement independent of queue wait time.

## Patterns Established

- **vi.advanceTimersByTimeAsync(0)** for flushing async operations under fake timers -- avoids the common trap where `new Promise(resolve => setTimeout(resolve, 0))` never resolves because setTimeout is faked.
- **resetMockImplementations()** helper after `vi.clearAllMocks()` -- because clearAllMocks wipes mock implementations set in `vi.hoisted()`. The helper re-applies all default implementations including the `audioCapture.on()` event registration system.
- **Singleton pattern with resetInstance()** -- consistent with WhisperProvider and AudioCapture.

## Bug Found and Fixed

**Fake timer starvation**: Tests using `new Promise(resolve => setTimeout(resolve, 0))` as `flushPromises()` caused 15s timeouts under `vi.useFakeTimers()`. The setTimeout was being faked and never resolved. Fixed by switching to `vi.advanceTimersByTimeAsync(0)` which properly advances fake time and flushes the microtask queue.

**Mock implementation loss**: `vi.clearAllMocks()` in beforeEach wiped the `audioCapture.on()` implementation that registers event listeners in the test's acListeners map. Without this, voice-start/voice-end/audio-chunk callbacks were never registered, so `emitAC()` had no effect. Fixed by adding a `resetMocks()` helper that re-applies all mock implementations after clearing.

## What Phase J.4 Should Know

1. `TranscriptionPipeline` exports `TranscriptEvent` and `TranscriptionStats` interfaces -- import them directly.
2. The `transcript` event fires with a `TranscriptEvent` containing `text`, `language`, `duration`, `latencyMs`, and `segments`.
3. The singleton is at `transcriptionPipeline` (lowercase export).
4. To subscribe: `const unsub = transcriptionPipeline.on('transcript', (event) => { ... })` -- returns an unsubscribe function.
5. Pipeline auto-queues rapid utterances. Downstream consumers receive events in order.

## Interface Changes

### New Exports (src/main/voice/transcription-pipeline.ts)
- `TranscriptionPipeline` -- class (singleton via getInstance/resetInstance)
- `transcriptionPipeline` -- singleton instance
- `TranscriptEvent` -- interface { text, language, duration, latencyMs, segments }
- `TranscriptionStats` -- interface { totalTranscriptions, averageLatencyMs, totalAudioDurationSec }

### No IPC Channels Added
### No Modifications to Existing Files
