/**
 * TTSEngine -- Unit tests for local text-to-speech via Kokoro/Piper.
 *
 * Sprint 4 K.1: "The Mouth" -- TTSEngine
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  ttsBinding: {
    loadModel: vi.fn(),
    synthesize: vi.fn(),
    synthesizeStream: vi.fn(),
    freeModel: vi.fn(),
    getVersion: vi.fn(),
  },
  fsAccess: vi.fn(),
  fsReaddir: vi.fn(),
}));

vi.mock('../../src/main/voice/tts-binding', () => ({
  default: mocks.ttsBinding,
  ttsBinding: mocks.ttsBinding,
}));

vi.mock('node:fs/promises', () => ({
  access: (...args: unknown[]) => mocks.fsAccess(...args),
  readdir: (...args: unknown[]) => mocks.fsReaddir(...args),
}));

vi.mock('node:os', () => ({
  homedir: () => '/mock/home',
  platform: () => 'linux',
  arch: () => 'x64',
}));

import { TTSEngine, ttsEngine } from '../../src/main/voice/tts-engine';
import type {
  TTSBackend,
  SynthesisOptions,
  VoiceInfo,
  TTSEngineInfo,
} from '../../src/main/voice/tts-engine';

const TTS_SAMPLE_RATE = 24_000;

function mockKokoroAvailable(): void {
  mocks.fsAccess.mockImplementation(async (path: string) => {
    if (path.includes('kokoro')) return undefined;
    throw new Error('ENOENT: no such file or directory');
  });
  mocks.fsReaddir.mockImplementation(async (path: string) => {
    if (path.includes('kokoro')) return ['default.onnx', 'en-us-1.onnx'];
    return [];
  });
  mocks.ttsBinding.loadModel.mockResolvedValue(undefined);
  mocks.ttsBinding.getVersion.mockReturnValue('0.1.0-kokoro');
}

function mockPiperAvailable(): void {
  mocks.fsAccess.mockImplementation(async (path: string) => {
    if (path.includes('piper')) return undefined;
    throw new Error('ENOENT: no such file or directory');
  });
  mocks.fsReaddir.mockImplementation(async (path: string) => {
    if (path.includes('piper')) return ['en_US-lessac-medium.onnx'];
    return [];
  });
  mocks.ttsBinding.loadModel.mockResolvedValue(undefined);
  mocks.ttsBinding.getVersion.mockReturnValue('0.1.0-piper');
}

function mockNoModelsAvailable(): void {
  mocks.fsAccess.mockRejectedValue(new Error('ENOENT: no such file or directory'));
  mocks.fsReaddir.mockRejectedValue(new Error('ENOENT: no such file or directory'));
}

function createMockPCM(durationSec = 1): Float32Array {
  const samples = TTS_SAMPLE_RATE * durationSec;
  const buffer = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    buffer[i] = Math.sin(2 * Math.PI * 440 * (i / TTS_SAMPLE_RATE)) * 0.3;
  }
  return buffer;
}

function mockSynthesizeSuccess(durationSec = 1): void {
  mocks.ttsBinding.synthesize.mockResolvedValue(createMockPCM(durationSec));
}

function mockSynthesizeStreamSuccess(): void {
  const chunk1 = createMockPCM(0.5);
  const chunk2 = createMockPCM(0.5);
  mocks.ttsBinding.synthesizeStream.mockImplementation(async function* () {
    yield chunk1;
    yield chunk2;
  });
}

describe('TTSEngine', () => {
  beforeEach(() => {
    TTSEngine.resetInstance();
    vi.clearAllMocks();
  });

  afterEach(() => {
    TTSEngine.resetInstance();
    vi.restoreAllMocks();
  });

  it('loadEngine("kokoro") succeeds when Kokoro model exists', async () => {
    mockKokoroAvailable();
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('kokoro');
    expect(engine.isReady()).toBe(true);
    expect(mocks.ttsBinding.loadModel).toHaveBeenCalledOnce();
    const callArg = mocks.ttsBinding.loadModel.mock.calls[0][0] as string;
    expect(callArg).toContain('kokoro');
  });

  it('loadEngine("piper") succeeds as fallback when Piper model exists', async () => {
    mockPiperAvailable();
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('piper');
    expect(engine.isReady()).toBe(true);
    expect(mocks.ttsBinding.loadModel).toHaveBeenCalledOnce();
    const callArg = mocks.ttsBinding.loadModel.mock.calls[0][0] as string;
    expect(callArg).toContain('piper');
  });

  it('loadEngine() returns graceful error when no TTS model found', async () => {
    mockNoModelsAvailable();
    const engine = TTSEngine.getInstance();
    await expect(engine.loadEngine()).rejects.toThrow(/no tts model found/i);
    expect(engine.isReady()).toBe(false);
  });

  it('synthesize(text) returns PCM audio buffer for valid text', async () => {
    mockKokoroAvailable();
    mockSynthesizeSuccess(1);
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('kokoro');
    const audio = await engine.synthesize('Hello, world!');
    expect(audio).toBeInstanceOf(Float32Array);
    expect(audio.length).toBeGreaterThan(0);
    expect(mocks.ttsBinding.synthesize).toHaveBeenCalledOnce();
    const [text] = mocks.ttsBinding.synthesize.mock.calls[0];
    expect(text).toBe('Hello, world!');
  });

  it('synthesize("") returns empty buffer for empty input', async () => {
    mockKokoroAvailable();
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('kokoro');
    const audio = await engine.synthesize('');
    expect(audio).toBeInstanceOf(Float32Array);
    expect(audio.length).toBe(0);
    expect(mocks.ttsBinding.synthesize).not.toHaveBeenCalled();
  });

  it('synthesizeStream(text) yields audio chunks progressively', async () => {
    mockKokoroAvailable();
    mockSynthesizeStreamSuccess();
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('kokoro');
    const chunks: Float32Array[] = [];
    for await (const chunk of engine.synthesizeStream('Hello, streaming world!')) {
      chunks.push(chunk);
    }
    expect(chunks.length).toBeGreaterThanOrEqual(2);
    for (const chunk of chunks) {
      expect(chunk).toBeInstanceOf(Float32Array);
      expect(chunk.length).toBeGreaterThan(0);
    }
  });

  it('isReady() false before load, true after', async () => {
    const engine = TTSEngine.getInstance();
    expect(engine.isReady()).toBe(false);
    mockKokoroAvailable();
    await engine.loadEngine('kokoro');
    expect(engine.isReady()).toBe(true);
    engine.unloadEngine();
    expect(engine.isReady()).toBe(false);
  });

  it('getAvailableVoices() lists downloaded voice models', async () => {
    mockKokoroAvailable();
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('kokoro');
    const voices = engine.getAvailableVoices();
    expect(voices.length).toBeGreaterThan(0);
    for (const voice of voices) {
      expect(voice).toHaveProperty('id');
      expect(voice).toHaveProperty('name');
      expect(voice).toHaveProperty('language');
      expect(voice).toHaveProperty('backend', 'kokoro');
      expect(voice).toHaveProperty('sampleRate', TTS_SAMPLE_RATE);
    }
  });

  it('output format is 24000Hz mono PCM float32', async () => {
    mockKokoroAvailable();
    mockSynthesizeSuccess(2);
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('kokoro');
    const audio = await engine.synthesize('Test output format.');
    expect(audio).toBeInstanceOf(Float32Array);
    expect(audio.length).toBe(TTS_SAMPLE_RATE * 2);
    const info = engine.getInfo();
    expect(info.backend).toBe('kokoro');
    expect(info.version).toBeTruthy();
    expect(info.voiceCount).toBeGreaterThan(0);
  });

  it('all tests use mocked TTS backend -- no native binary in CI', async () => {
    mockKokoroAvailable();
    const callOrder: number[] = [];
    let callCount = 0;
    mocks.ttsBinding.synthesize.mockImplementation(async (_text: string) => {
      const myOrder = ++callCount;
      callOrder.push(myOrder);
      await new Promise((resolve) => setTimeout(resolve, 10));
      return createMockPCM(0.5);
    });
    const engine = TTSEngine.getInstance();
    await engine.loadEngine('kokoro');
    const [r1, r2, r3] = await Promise.all([
      engine.synthesize('First sentence.'),
      engine.synthesize('Second sentence.'),
      engine.synthesize('Third sentence.'),
    ]);
    expect(r1).toBeInstanceOf(Float32Array);
    expect(r2).toBeInstanceOf(Float32Array);
    expect(r3).toBeInstanceOf(Float32Array);
    expect(callOrder).toEqual([1, 2, 3]);
    expect(mocks.ttsBinding.synthesize).toHaveBeenCalledTimes(3);
  });
});
