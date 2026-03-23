/**
 * WhisperProvider -- Unit tests for local speech-to-text via whisper.cpp.
 *
 * Tests singleton lifecycle, model loading, transcription, readiness checks,
 * model listing, resource cleanup, audio format handling, and sequential
 * processing of concurrent requests.
 *
 * All whisper.cpp interactions are mocked -- no native binary required in CI.
 *
 * Sprint 4 J.1: "The Ear" -- WhisperProvider
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => ({
  whisperBinding: {
    loadModel: vi.fn(),
    transcribe: vi.fn(),
    freeModel: vi.fn(),
  },
  fsAccess: vi.fn(),
  fsReaddir: vi.fn(),
  fsStat: vi.fn(),
}));

vi.mock('../../src/main/voice/whisper-binding', () => ({
  default: mocks.whisperBinding,
  whisperBinding: mocks.whisperBinding,
}));

vi.mock('node:fs/promises', () => ({
  access: (...args: unknown[]) => mocks.fsAccess(...args),
  readdir: (...args: unknown[]) => mocks.fsReaddir(...args),
  stat: (...args: unknown[]) => mocks.fsStat(...args),
  mkdir: vi.fn().mockResolvedValue(undefined),
  writeFile: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('node:os', () => ({
  homedir: () => '/mock/home',
  platform: () => 'linux',
  arch: () => 'x64',
  tmpdir: () => '/tmp',
}));

import { WhisperProvider, whisperProvider } from '../../src/main/voice/whisper-provider';
import type {
  WhisperModelSize,
  TranscriptionResult,
  TranscriptionSegment,
  WhisperModelInfo,
} from '../../src/main/voice/whisper-provider';

// -- Helper: create audio buffers ---------------------------------------------

function createSilentBuffer(durationSec = 1): Float32Array {
  return new Float32Array(16000 * durationSec);
}

function createSpeechBuffer(durationSec = 1): Float32Array {
  const samples = 16000 * durationSec;
  const buffer = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    buffer[i] = Math.sin(2 * Math.PI * 440 * (i / 16000)) * 0.5;
  }
  return buffer;
}

// -- Mock response helpers ----------------------------------------------------

function mockModelFileExists(size: WhisperModelSize = 'tiny') {
  mocks.fsAccess.mockResolvedValue(undefined);
  mocks.whisperBinding.loadModel.mockResolvedValue({ handle: `mock-${size}-handle` });
}

function mockModelFileMissing() {
  mocks.fsAccess.mockRejectedValue(new Error('ENOENT: no such file or directory'));
}

function mockSilentTranscription(): void {
  mocks.whisperBinding.transcribe.mockResolvedValue({
    text: '',
    language: 'en',
    segments: [],
  });
}

function mockSpeechTranscription(): void {
  mocks.whisperBinding.transcribe.mockResolvedValue({
    text: 'Hello, this is a test transcription.',
    language: 'en',
    segments: [
      { text: 'Hello, this is', start: 0.0, end: 0.8 },
      { text: 'a test transcription.', start: 0.8, end: 1.5 },
    ],
  });
}

// -- Test Suite ---------------------------------------------------------------

describe('WhisperProvider', () => {
  beforeEach(() => {
    WhisperProvider.resetInstance();
    vi.clearAllMocks();
  });

  afterEach(() => {
    WhisperProvider.resetInstance();
    vi.restoreAllMocks();
  });

  it('loadModel("tiny") succeeds when model file exists', async () => {
    mockModelFileExists('tiny');
    const provider = WhisperProvider.getInstance();

    await provider.loadModel('tiny');

    expect(provider.isReady()).toBe(true);
    expect(mocks.whisperBinding.loadModel).toHaveBeenCalledOnce();
    const callArg = mocks.whisperBinding.loadModel.mock.calls[0][0] as string;
    expect(callArg).toContain('tiny');
  });

  it('loadModel("tiny") returns graceful error when model file is missing', async () => {
    mockModelFileMissing();
    const provider = WhisperProvider.getInstance();

    await expect(provider.loadModel('tiny')).rejects.toThrow(/model file not found/i);
    expect(provider.isReady()).toBe(false);
  });

  it('transcribe(silentBuffer) returns empty string for silence', async () => {
    mockModelFileExists('tiny');
    mockSilentTranscription();

    const provider = WhisperProvider.getInstance();
    await provider.loadModel('tiny');

    const result = await provider.transcribe(createSilentBuffer());

    expect(result.text).toBe('');
    expect(result.segments).toHaveLength(0);
    expect(result.duration).toBeCloseTo(1.0, 1);
    expect(result.processingTime).toBeGreaterThanOrEqual(0);
  });

  it('transcribe(audioBuffer) returns non-empty text for speech', async () => {
    mockModelFileExists('tiny');
    mockSpeechTranscription();

    const provider = WhisperProvider.getInstance();
    await provider.loadModel('tiny');

    const result = await provider.transcribe(createSpeechBuffer());

    expect(result.text).toBeTruthy();
    expect(result.text.length).toBeGreaterThan(0);
    expect(result.language).toBe('en');
    expect(result.segments.length).toBeGreaterThan(0);
    expect(result.segments[0].start).toBe(0.0);
    expect(result.duration).toBeCloseTo(1.0, 1);
    expect(result.processingTime).toBeGreaterThanOrEqual(0);
  });

  it('isReady() returns false before loadModel, true after', async () => {
    const provider = WhisperProvider.getInstance();

    expect(provider.isReady()).toBe(false);

    mockModelFileExists('tiny');
    await provider.loadModel('tiny');

    expect(provider.isReady()).toBe(true);
  });

  it('getAvailableModels() lists downloaded model files', async () => {
    mocks.fsReaddir.mockResolvedValue([
      'ggml-tiny.bin',
      'ggml-base.bin',
      'ggml-small.bin',
    ]);

    mocks.fsStat.mockImplementation(async (filePath: string) => {
      if (filePath.includes('tiny')) return { size: 75 * 1024 * 1024 };
      if (filePath.includes('base')) return { size: 142 * 1024 * 1024 };
      if (filePath.includes('small')) return { size: 466 * 1024 * 1024 };
      return { size: 0 };
    });

    const provider = WhisperProvider.getInstance();
    const models = await provider.getAvailableModels();

    expect(models).toHaveLength(3);
    expect(models.map((m) => m.size)).toContain('tiny');
    expect(models.map((m) => m.size)).toContain('base');
    expect(models.map((m) => m.size)).toContain('small');
    expect(models.every((m) => m.downloaded === true)).toBe(true);
    expect(models.find((m) => m.size === 'tiny')!.fileSizeMB).toBeCloseTo(75, 0);
  });

  it('unloadModel() frees resources and isReady() returns false', async () => {
    mockModelFileExists('tiny');
    const provider = WhisperProvider.getInstance();
    await provider.loadModel('tiny');

    expect(provider.isReady()).toBe(true);

    provider.unloadModel();

    expect(provider.isReady()).toBe(false);
    expect(mocks.whisperBinding.freeModel).toHaveBeenCalledOnce();
  });

  it('transcription works with 16kHz mono PCM float32 input format', async () => {
    mockModelFileExists('tiny');
    mockSpeechTranscription();

    const provider = WhisperProvider.getInstance();
    await provider.loadModel('tiny');

    const threeSecBuffer = createSpeechBuffer(3);
    expect(threeSecBuffer.length).toBe(48000);
    expect(threeSecBuffer).toBeInstanceOf(Float32Array);

    const result = await provider.transcribe(threeSecBuffer);

    expect(result.text).toBeTruthy();
    expect(result.duration).toBeCloseTo(3.0, 1);
    expect(mocks.whisperBinding.transcribe).toHaveBeenCalledOnce();
    const [passedAudio, passedOpts] = mocks.whisperBinding.transcribe.mock.calls[0];
    expect(passedAudio).toBeInstanceOf(Float32Array);
    expect(passedAudio.length).toBe(48000);
    expect(passedOpts).toHaveProperty('sampleRate', 16000);
  });

  it('handles concurrent transcription requests sequentially via queue', async () => {
    mockModelFileExists('tiny');

    const callOrder: number[] = [];
    let callCount = 0;

    mocks.whisperBinding.transcribe.mockImplementation(async () => {
      const myOrder = ++callCount;
      callOrder.push(myOrder);
      await new Promise((resolve) => setTimeout(resolve, 10));
      return {
        text: `Transcription ${myOrder}`,
        language: 'en',
        segments: [{ text: `Transcription ${myOrder}`, start: 0, end: 1 }],
      };
    });

    const provider = WhisperProvider.getInstance();
    await provider.loadModel('tiny');

    const [r1, r2, r3] = await Promise.all([
      provider.transcribe(createSpeechBuffer()),
      provider.transcribe(createSpeechBuffer()),
      provider.transcribe(createSpeechBuffer()),
    ]);

    expect(r1.text).toBe('Transcription 1');
    expect(r2.text).toBe('Transcription 2');
    expect(r3.text).toBe('Transcription 3');
    expect(callOrder).toEqual([1, 2, 3]);
    expect(mocks.whisperBinding.transcribe).toHaveBeenCalledTimes(3);
  });

  it('singleton pattern works correctly (getInstance + resetInstance)', () => {
    const a = WhisperProvider.getInstance();
    const b = WhisperProvider.getInstance();
    expect(a).toBe(b);

    WhisperProvider.resetInstance();

    const c = WhisperProvider.getInstance();
    expect(c).not.toBe(a);

    expect(whisperProvider).toBeInstanceOf(WhisperProvider);
  });
});
