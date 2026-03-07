# Session Journal: Track J, Phase 2 -- "The Listener"

**Date:** 2026-03-07
**Tests added:** 10
**New lines:** ~160 (src/main/voice/audio-capture.ts)

## What Was Built

AudioCapture -- a singleton that manages microphone access coordination via IPC and performs energy-based voice activity detection (VAD) on incoming audio chunks.

### Architecture Decision: Main/Renderer IPC Split

Electron requires getUserMedia() to run in the renderer process. AudioCapture lives in main and coordinates via IPC:

1. Main sends voice:start-capture -> renderer starts mic + AudioWorklet
2. Renderer sends voice:audio-chunk -> main receives Float32Array PCM data
3. Main sends voice:stop-capture -> renderer releases mic
4. Renderer sends voice:capture-error -> main handles mic errors

This split keeps all audio processing logic (VAD, buffering) in main where it can directly feed WhisperProvider.

### Key Design Choices

1. **Energy-based VAD** -- Simple RMS energy computation compared to a configurable threshold. No external VAD library needed. Silero VAD can be swapped in later by replacing the computeRms comparison with a model inference call.

2. **Silence duration tracking** -- Uses Date.now() timestamps (compatible with vi.useFakeTimers) to measure silence. After silenceDuration ms of below-threshold audio, voice-end fires with the concatenated speech buffer.

3. **Speech buffering** -- During an active utterance, all audio chunks are collected into an array. On voice-end, they are concatenated into a single Float32Array for WhisperProvider.transcribe(). Max buffer duration prevents runaway allocations.

4. **Audio level metering** -- getAudioLevel() returns a 0-1 normalized value computed from the latest chunk RMS. Used by the renderer for waveform visualization.

5. **Arrow function IPC handlers** -- handleAudioChunk and handleCaptureError are arrow functions to preserve this binding when passed to ipcMain.on().

## Patterns Established

- **IPC coordination for hardware access**: Main process singleton + renderer-side handler, connected via named IPC channels.
- **Energy-based VAD with configurable threshold**: Simple, testable, swappable.
- **Event emitter with unsubscribe**: on() returns a cleanup function, matching React useEffect patterns.

## What Phase J.3 Should Know

1. AudioCapture emits voice-end with a Float32Array payload ready for WhisperProvider.transcribe().
2. The audio format is 16kHz mono PCM float32 -- exactly what Whisper expects.
3. getAudioLevel() can drive a waveform visualization component.
4. The renderer-side counterpart needs to implement getUserMedia + AudioWorklet + IPC send.
5. Import via: import { audioCapture } from '../voice/audio-capture'; for the singleton.
