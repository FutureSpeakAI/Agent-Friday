/**
 * TranscriptionPipeline -- Unit tests.
 * Sprint 4 J.3: "The Stream" -- TranscriptionPipeline
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const acListeners = new Map<string, Set<(payload?: unknown) => void>>();
  return {
    acListeners,
    audioCapture: {
      startCapture: vi.fn(async () => {}),
      stopCapture: vi.fn(),
      isCapturing: vi.fn(() => false),
      on: vi.fn((event: string, cb: (payload?: unknown) => void) => {
        if (!acListeners.has(event)) { acListeners.set(event, new Set()); }
        acListeners.get(event)!.add(cb);
        return () => { acListeners.get(event)?.delete(cb); };
      }),
    },
    whisperProvider: {
      isReady: vi.fn(() => true),
      loadModel: vi.fn(async () => {}),
      transcribe: vi.fn(async (audio: Float32Array) => ({
        text: 'test transcription',
        language: 'en',
        segments: [{ text: 'test transcription', start: 0, end: 1.5 }],
        duration: audio.length / 16000,
        processingTime: 200,
      })),
    },
  };
});

vi.mock('../../src/main/voice/audio-capture', () => ({ audioCapture: mocks.audioCapture }));
vi.mock('../../src/main/voice/whisper-provider', () => ({ whisperProvider: mocks.whisperProvider }));

import { TranscriptionPipeline } from '../../src/main/voice/transcription-pipeline';
import type { TranscriptEvent, TranscriptionStats } from '../../src/main/voice/transcription-pipeline';

function emitAC(event: string, payload?: unknown): void {
  const cbs = mocks.acListeners.get(event);
  if (cbs) { for (const cb of cbs) { cb(payload); } }
}

function buf(sec = 1): Float32Array {
  const n = 16000 * sec;
  const b = new Float32Array(n);
  for (let i = 0; i < n; i++) b[i] = Math.sin(2 * Math.PI * 440 * (i / 16000)) * 0.5;
  return b;
}

function resetMocks(): void {
  mocks.whisperProvider.loadModel.mockImplementation(async () => {});
  mocks.whisperProvider.isReady.mockReturnValue(true);
  mocks.whisperProvider.transcribe.mockImplementation(async (audio: Float32Array) => ({
    text: 'test transcription',
    language: 'en',
    segments: [{ text: 'test transcription', start: 0, end: 1.5 }],
    duration: audio.length / 16000,
    processingTime: 200,
  }));
  mocks.audioCapture.startCapture.mockImplementation(async () => {});
  mocks.audioCapture.stopCapture.mockImplementation(() => {});
  mocks.audioCapture.isCapturing.mockReturnValue(false);
  mocks.audioCapture.on.mockImplementation((event: string, cb: (payload?: unknown) => void) => {
    if (!mocks.acListeners.has(event)) { mocks.acListeners.set(event, new Set()); }
    mocks.acListeners.get(event)!.add(cb);
    return () => { mocks.acListeners.get(event)?.delete(cb); };
  });
}

describe('TranscriptionPipeline -- Sprint 4 J.3', () => {
  let pipeline: TranscriptionPipeline;
  beforeEach(() => {
    TranscriptionPipeline.resetInstance();
    vi.clearAllMocks();
    mocks.acListeners.clear();
    resetMocks();
    vi.useFakeTimers();
    pipeline = TranscriptionPipeline.getInstance();
  });
  afterEach(() => {
    TranscriptionPipeline.resetInstance();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('start() initializes AudioCapture and WhisperProvider', async () => {
    await pipeline.start();
    expect(mocks.whisperProvider.loadModel).toHaveBeenCalledOnce();
    expect(mocks.audioCapture.startCapture).toHaveBeenCalledOnce();
    expect(mocks.audioCapture.on).toHaveBeenCalledWith('voice-start', expect.any(Function));
    expect(mocks.audioCapture.on).toHaveBeenCalledWith('voice-end', expect.any(Function));
    expect(mocks.audioCapture.on).toHaveBeenCalledWith('audio-chunk', expect.any(Function));
    expect(mocks.audioCapture.on).toHaveBeenCalledWith('error', expect.any(Function));
    expect(pipeline.isListening()).toBe(true);
    pipeline.stop();
  });

  it('stop() cleanly shuts down both subsystems', async () => {
    await pipeline.start();
    expect(pipeline.isListening()).toBe(true);
    pipeline.stop();
    expect(mocks.audioCapture.stopCapture).toHaveBeenCalledOnce();
    expect(pipeline.isListening()).toBe(false);
  });

  it('VAD voice-start begins buffering audio chunks', async () => {
    await pipeline.start();
    emitAC('voice-start');
    emitAC('audio-chunk', buf(0.1));
    emitAC('audio-chunk', buf(0.1));
    expect(mocks.whisperProvider.transcribe).not.toHaveBeenCalled();
    pipeline.stop();
  });

  it('VAD voice-end triggers transcription of buffered audio', async () => {
    await pipeline.start();
    emitAC('voice-start');
    emitAC('audio-chunk', buf(0.5));
    emitAC('audio-chunk', buf(0.5));
    emitAC('voice-end', buf(1));
    await vi.advanceTimersByTimeAsync(0);
    expect(mocks.whisperProvider.transcribe).toHaveBeenCalledOnce();
    expect(mocks.whisperProvider.transcribe.mock.calls[0][0]).toBeInstanceOf(Float32Array);
    pipeline.stop();
  });

  it('onTranscript fires with final text after voice-end', async () => {
    const cb = vi.fn();
    pipeline.on('transcript', cb);
    await pipeline.start();
    emitAC('voice-start');
    emitAC('audio-chunk', buf(0.5));
    emitAC('voice-end', buf(1));
    await vi.advanceTimersByTimeAsync(0);
    expect(cb).toHaveBeenCalledOnce();
    const ev = cb.mock.calls[0][0] as TranscriptEvent;
    expect(ev.text).toBe('test transcription');
    expect(ev.language).toBe('en');
    expect(ev.segments).toHaveLength(1);
    expect(ev.duration).toBeGreaterThan(0);
    expect(ev.latencyMs).toBeGreaterThanOrEqual(0);
    pipeline.stop();
  });

  it('onPartialTranscript fires periodically during long utterances (>2s)', async () => {
    const partialCb = vi.fn();
    pipeline.on('partial', partialCb);
    mocks.whisperProvider.transcribe.mockImplementation(async (audio: Float32Array) => ({
      text: 'partial result',
      language: 'en',
      segments: [{ text: 'partial result', start: 0, end: 1 }],
      duration: audio.length / 16000,
      processingTime: 50,
    }));
    await pipeline.start();
    emitAC('voice-start');
    for (let i = 0; i < 5; i++) {
      emitAC('audio-chunk', buf(0.5));
      vi.advanceTimersByTime(500);
      await vi.advanceTimersByTimeAsync(0);
    }
    expect(partialCb).toHaveBeenCalled();
    const pe = partialCb.mock.calls[0][0] as TranscriptEvent;
    expect(pe.text).toBe('partial result');
    pipeline.stop();
  });

  it('pipeline handles WhisperProvider unavailability gracefully', async () => {
    const errorCb = vi.fn();
    pipeline.on('error', errorCb);
    mocks.whisperProvider.isReady.mockReturnValue(false);
    mocks.whisperProvider.loadModel.mockRejectedValue(new Error('Model file not found'));
    await pipeline.start();
    expect(errorCb).toHaveBeenCalled();
    const err = errorCb.mock.calls[0][0] as Error;
    expect(err.message).toMatch(/model|whisper|unavailable/i);
    expect(() => pipeline.stop()).not.toThrow();
  });

  it('pipeline handles AudioCapture failure gracefully', async () => {
    const errorCb = vi.fn();
    pipeline.on('error', errorCb);
    await pipeline.start();
    expect(pipeline.isListening()).toBe(true);
    emitAC('error', new Error('Microphone disconnected'));
    expect(errorCb).toHaveBeenCalledOnce();
    const err = errorCb.mock.calls[0][0] as Error;
    expect(err.message).toMatch(/microphone|audio|capture/i);
    expect(pipeline.isListening()).toBe(false);
    pipeline.stop();
  });

  it('getStats() tracks average transcription latency', async () => {
    let cc = 0;
    mocks.whisperProvider.transcribe.mockImplementation(async (audio: Float32Array) => {
      cc++;
      return {
        text: 't' + cc, language: 'en',
        segments: [{ text: 't' + cc, start: 0, end: 1 }],
        duration: audio.length / 16000, processingTime: cc * 100,
      };
    });
    await pipeline.start();
    emitAC('voice-start'); emitAC('audio-chunk', buf(1)); emitAC('voice-end', buf(1));
    await vi.advanceTimersByTimeAsync(0);
    emitAC('voice-start'); emitAC('audio-chunk', buf(1)); emitAC('voice-end', buf(1));
    await vi.advanceTimersByTimeAsync(0);
    const stats = pipeline.getStats();
    expect(stats.totalTranscriptions).toBe(2);
    expect(stats.averageLatencyMs).toBeGreaterThan(0);
    expect(stats.totalAudioDurationSec).toBeGreaterThan(0);
    pipeline.stop();
  });

  it('multiple rapid utterances queue correctly (no dropped audio)', async () => {
    const cb = vi.fn();
    pipeline.on('transcript', cb);
    let cc = 0;
    mocks.whisperProvider.transcribe.mockImplementation(async (audio: Float32Array) => {
      cc++;
      return {
        text: 'utterance ' + cc, language: 'en',
        segments: [{ text: 'utterance ' + cc, start: 0, end: 1 }],
        duration: audio.length / 16000, processingTime: 100,
      };
    });
    await pipeline.start();
    for (let i = 0; i < 3; i++) {
      emitAC('voice-start'); emitAC('audio-chunk', buf(0.5)); emitAC('voice-end', buf(0.5));
    }
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    expect(mocks.whisperProvider.transcribe).toHaveBeenCalledTimes(3);
    expect(cb).toHaveBeenCalledTimes(3);
    expect((cb.mock.calls[0][0] as TranscriptEvent).text).toBe('utterance 1');
    expect((cb.mock.calls[1][0] as TranscriptEvent).text).toBe('utterance 2');
    expect((cb.mock.calls[2][0] as TranscriptEvent).text).toBe('utterance 3');
    pipeline.stop();
  });
});
