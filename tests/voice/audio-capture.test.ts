/**
 * AudioCapture -- Unit tests for microphone access, audio format conversion,
 * and energy-based voice activity detection (VAD).
 *
 * Tests the main-process side of the AudioCapture singleton. The renderer
 * (getUserMedia) is simulated via mocked IPC: audio chunks arrive through
 * the 'voice:audio-chunk' channel, and start/stop commands are sent via
 * 'voice:start-capture' / 'voice:stop-capture'.
 *
 * All tests use mocked MediaStream -- no real microphone in CI.
 *
 * Sprint 4 J.2: "The Listener" -- AudioCapture
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => {
  const ipcHandlers = new Map<string, (...args: unknown[]) => unknown>();
  const ipcListeners = new Map<string, (...args: unknown[]) => unknown>();
  const webContentsSend = vi.fn();

  return {
    ipcHandlers,
    ipcListeners,
    webContentsSend,
    ipcMain: {
      handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
        ipcHandlers.set(channel, handler);
      }),
      on: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
        ipcListeners.set(channel, handler);
      }),
      removeHandler: vi.fn((channel: string) => {
        ipcHandlers.delete(channel);
      }),
      removeListener: vi.fn((channel: string, _handler: unknown) => {
        ipcListeners.delete(channel);
      }),
      removeAllListeners: vi.fn((channel: string) => {
        ipcListeners.delete(channel);
      }),
    },
    BrowserWindow: {
      getAllWindows: vi.fn(() => [
        {
          isDestroyed: () => false,
          webContents: { send: webContentsSend },
        },
      ]),
    },
  };
});

vi.mock('electron', () => ({
  ipcMain: mocks.ipcMain,
  BrowserWindow: mocks.BrowserWindow,
}));

// -- Import after mocks -----------------------------------------------------

import { AudioCapture, audioCapture } from '../../../src/main/voice/audio-capture';
import type { AudioCaptureConfig } from '../../../src/main/voice/audio-capture';

// -- Helpers ----------------------------------------------------------------

/** Create a Float32Array of silence (near-zero values). */
function createSilentChunk(durationMs = 100, sampleRate = 16000): Float32Array {
  const samples = Math.floor(sampleRate * (durationMs / 1000));
  return new Float32Array(samples); // all zeros
}

/** Create a Float32Array of "speech" (sine wave with high RMS). */
function createSpeechChunk(durationMs = 100, sampleRate = 16000): Float32Array {
  const samples = Math.floor(sampleRate * (durationMs / 1000));
  const buffer = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    buffer[i] = Math.sin(2 * Math.PI * 440 * (i / sampleRate)) * 0.5;
  }
  return buffer;
}

/** Simulate renderer sending an audio chunk via IPC. */
function sendAudioChunk(chunk: Float32Array): void {
  const listener = mocks.ipcListeners.get('voice:audio-chunk');
  if (!listener) throw new Error('No listener registered for voice:audio-chunk');
  // IPC event object has a sender property; we pass a minimal mock
  listener({ sender: { send: vi.fn() } }, chunk);
}
// -- Test Suite --------------------------------------------------------------

describe('AudioCapture -- Sprint 4 J.2', () => {
  beforeEach(() => {
    AudioCapture.resetInstance();
    vi.clearAllMocks();
    mocks.ipcHandlers.clear();
    mocks.ipcListeners.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    AudioCapture.resetInstance();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  // -- Test 1: startCapture() requests microphone permission --------

  it('startCapture() registers IPC handler and sends start command to renderer', async () => {
    const capture = AudioCapture.getInstance();
    await capture.startCapture();

    // Should register the audio-chunk listener
    expect(mocks.ipcMain.on).toHaveBeenCalledWith(
      'voice:audio-chunk',
      expect.any(Function),
    );

    // Should send start-capture command to renderer
    expect(mocks.webContentsSend).toHaveBeenCalledWith('voice:start-capture');

    capture.stopCapture();
  });

  // -- Test 2: stopCapture() releases the microphone stream ---------

  it('stopCapture() sends stop command and removes IPC listeners', async () => {
    const capture = AudioCapture.getInstance();
    await capture.startCapture();
    capture.stopCapture();

    // Should send stop command to renderer
    expect(mocks.webContentsSend).toHaveBeenCalledWith('voice:stop-capture');

    // Should clean up IPC listeners
    expect(mocks.ipcMain.removeListener).toHaveBeenCalledWith('voice:audio-chunk', expect.any(Function));
    expect(mocks.ipcMain.removeListener).toHaveBeenCalledWith('voice:capture-error', expect.any(Function));

    // Should no longer be capturing
    expect(capture.isCapturing()).toBe(false);
  });

  // -- Test 3: isCapturing() tracks recording state -----------------

  it('isCapturing() returns false initially, true after start, false after stop', async () => {
    const capture = AudioCapture.getInstance();

    expect(capture.isCapturing()).toBe(false);

    await capture.startCapture();
    expect(capture.isCapturing()).toBe(true);

    capture.stopCapture();
    expect(capture.isCapturing()).toBe(false);
  });

  // -- Test 4: VAD fires voice-start on speech ----------------------

  it('fires voice-start when audio exceeds energy threshold', async () => {
    const capture = AudioCapture.getInstance();
    const voiceStartCb = vi.fn();
    capture.on('voice-start', voiceStartCb);

    await capture.startCapture();

    // Send a speech chunk (high RMS)
    sendAudioChunk(createSpeechChunk(100));

    expect(voiceStartCb).toHaveBeenCalledTimes(1);

    capture.stopCapture();
  });

  // -- Test 5: VAD fires voice-end after silence duration -----------

  it('fires voice-end after silence exceeds threshold (300ms default)', async () => {
    const capture = AudioCapture.getInstance();
    const voiceEndCb = vi.fn();
    capture.on('voice-end', voiceEndCb);

    await capture.startCapture();

    // Start speech
    sendAudioChunk(createSpeechChunk(100));

    // Send silence chunks (each 100ms) to exceed 300ms silence threshold
    sendAudioChunk(createSilentChunk(100));
    vi.advanceTimersByTime(100);
    sendAudioChunk(createSilentChunk(100));
    vi.advanceTimersByTime(100);
    sendAudioChunk(createSilentChunk(100));
    vi.advanceTimersByTime(100);
    sendAudioChunk(createSilentChunk(100));
    vi.advanceTimersByTime(100);

    expect(voiceEndCb).toHaveBeenCalledTimes(1);

    // Payload should be a Float32Array containing the buffered audio
    const payload = voiceEndCb.mock.calls[0][0] as Float32Array;
    expect(payload).toBeInstanceOf(Float32Array);
    expect(payload.length).toBeGreaterThan(0);

    capture.stopCapture();
  });
  // -- Test 6: Audio chunks are 16kHz mono PCM float32 ---------------

  it('emits audio-chunk events with 16kHz mono PCM float32 data', async () => {
    const capture = AudioCapture.getInstance();
    const chunkCb = vi.fn();
    capture.on('audio-chunk', chunkCb);

    await capture.startCapture();

    const chunk = createSpeechChunk(100, 16000);
    sendAudioChunk(chunk);

    expect(chunkCb).toHaveBeenCalledTimes(1);

    const received = chunkCb.mock.calls[0][0] as Float32Array;
    expect(received).toBeInstanceOf(Float32Array);
    // 100ms at 16kHz = 1600 samples
    expect(received.length).toBe(1600);

    capture.stopCapture();
  });

  // -- Test 7: getAudioLevel() returns 0-1 normalized ---------------

  it('getAudioLevel() returns 0 when idle, 0-1 normalized value during capture', async () => {
    const capture = AudioCapture.getInstance();

    // Before capturing, level is 0
    expect(capture.getAudioLevel()).toBe(0);

    await capture.startCapture();

    // Send silence -- level should be ~0
    sendAudioChunk(createSilentChunk(100));
    expect(capture.getAudioLevel()).toBeCloseTo(0, 2);

    // Send speech -- level should be > 0
    sendAudioChunk(createSpeechChunk(100));
    const level = capture.getAudioLevel();
    expect(level).toBeGreaterThan(0);
    expect(level).toBeLessThanOrEqual(1);

    capture.stopCapture();
  });

  // -- Test 8: Handles microphone permission denial gracefully ------

  it('handles microphone permission denial by emitting error event', async () => {
    const capture = AudioCapture.getInstance();
    const errorCb = vi.fn();
    capture.on('error', errorCb);

    // Simulate no windows available (permission denied scenario)
    mocks.BrowserWindow.getAllWindows.mockReturnValueOnce([]);

    await capture.startCapture();

    expect(errorCb).toHaveBeenCalledTimes(1);
    const error = errorCb.mock.calls[0][0] as Error;
    expect(error.message).toMatch(/no renderer|permission|window/i);
    expect(capture.isCapturing()).toBe(false);
  });

  // -- Test 9: Handles microphone disconnection during capture ------

  it('handles microphone disconnection during capture', async () => {
    const capture = AudioCapture.getInstance();
    const errorCb = vi.fn();
    capture.on('error', errorCb);

    await capture.startCapture();
    expect(capture.isCapturing()).toBe(true);

    // Simulate renderer sending a disconnection error via IPC
    const errorListener = mocks.ipcListeners.get('voice:capture-error');
    expect(errorListener).toBeDefined();
    errorListener!({ sender: { send: vi.fn() } }, 'Microphone disconnected');

    expect(errorCb).toHaveBeenCalledTimes(1);
    const error = errorCb.mock.calls[0][0] as Error;
    expect(error.message).toMatch(/disconnected/i);
    expect(capture.isCapturing()).toBe(false);
  });

  // -- Test 10: Singleton and config work correctly -----------------

  it('singleton pattern and custom config work correctly (no real microphone)', () => {
    const instance1 = AudioCapture.getInstance();
    const instance2 = AudioCapture.getInstance();
    expect(instance1).toBe(instance2);

    // Exported singleton should match
    expect(audioCapture).toBeInstanceOf(AudioCapture);

    // Reset creates a new instance
    AudioCapture.resetInstance();
    const instance3 = AudioCapture.getInstance();
    expect(instance3).not.toBe(instance1);

    // Custom config
    AudioCapture.resetInstance();
    const custom = AudioCapture.getInstance({
      sampleRate: 16000,
      vadThreshold: 0.3,
      silenceDuration: 500,
      maxBufferDuration: 60000,
    });
    expect(custom).toBeInstanceOf(AudioCapture);
  });
});
