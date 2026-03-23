/**
 * Tests for voice-pipeline-handlers.ts — IPC layer for WhisperProvider,
 * AudioCapture, TranscriptionPipeline, TTSEngine, VoiceProfileManager,
 * and SpeechSynthesisManager.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

type IpcHandler = (...args: unknown[]) => unknown;
const handlers = new Map<string, IpcHandler>();
const mockSend = vi.fn();
vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: IpcHandler) => {
      handlers.set(channel, handler);
    }),
  },
}));

const mocks = vi.hoisted(() => ({
  // WhisperProvider
  loadModel: vi.fn().mockResolvedValue(undefined),
  unloadModel: vi.fn(),
  isReady: vi.fn().mockReturnValue(false),
  transcribe: vi.fn().mockResolvedValue({ text: 'hello' }),
  getAvailableModels: vi.fn().mockResolvedValue([]),
  isModelDownloaded: vi.fn().mockResolvedValue(false),
  downloadModel: vi.fn().mockResolvedValue(undefined),
  // AudioCapture
  startCapture: vi.fn().mockResolvedValue(undefined),
  stopCapture: vi.fn(),
  isCapturing: vi.fn().mockReturnValue(false),
  getAudioLevel: vi.fn().mockReturnValue(0),
  captureOn: vi.fn().mockReturnValue(() => {}),
  // TranscriptionPipeline
  pipelineStart: vi.fn().mockResolvedValue(undefined),
  pipelineStop: vi.fn(),
  isListening: vi.fn().mockReturnValue(false),
  getStats: vi.fn().mockReturnValue({}),
  pipelineOn: vi.fn().mockReturnValue(() => {}),
  // TTSEngine
  loadEngine: vi.fn().mockResolvedValue(undefined),
  unloadEngine: vi.fn(),
  ttsIsReady: vi.fn().mockReturnValue(false),
  synthesize: vi.fn().mockResolvedValue(new Float32Array([0.1, 0.2])),
  getAvailableVoices: vi.fn().mockReturnValue([]),
  getInfo: vi.fn().mockReturnValue({}),
  // VoiceProfileManager
  getActiveProfile: vi.fn().mockReturnValue(null),
  setActiveProfile: vi.fn(),
  listProfiles: vi.fn().mockReturnValue([]),
  createProfile: vi.fn().mockReturnValue({ id: 'vp-1' }),
  deleteProfile: vi.fn(),
  previewVoice: vi.fn().mockResolvedValue(new Float32Array([0.3])),
  // SpeechSynthesisManager
  speak: vi.fn().mockResolvedValue(undefined),
  speakImmediate: vi.fn().mockResolvedValue(undefined),
  synthStop: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  isSpeaking: vi.fn().mockReturnValue(false),
  getQueueLength: vi.fn().mockReturnValue(0),
  synthesisOn: vi.fn().mockReturnValue(() => {}),
}));

vi.mock('../../src/main/voice/whisper-provider', () => ({
  WhisperProvider: {
    getInstance: () => ({
      loadModel: mocks.loadModel,
      unloadModel: mocks.unloadModel,
      isReady: mocks.isReady,
      transcribe: mocks.transcribe,
      getAvailableModels: mocks.getAvailableModels,
      isModelDownloaded: mocks.isModelDownloaded,
      downloadModel: mocks.downloadModel,
    }),
  },
}));

vi.mock('../../src/main/voice/audio-capture', () => ({
  AudioCapture: {
    getInstance: () => ({
      startCapture: mocks.startCapture,
      stopCapture: mocks.stopCapture,
      isCapturing: mocks.isCapturing,
      getAudioLevel: mocks.getAudioLevel,
      on: mocks.captureOn,
    }),
  },
}));

vi.mock('../../src/main/voice/transcription-pipeline', () => ({
  TranscriptionPipeline: {
    getInstance: () => ({
      start: mocks.pipelineStart,
      stop: mocks.pipelineStop,
      isListening: mocks.isListening,
      getStats: mocks.getStats,
      on: mocks.pipelineOn,
    }),
  },
}));

vi.mock('../../src/main/voice/tts-engine', () => ({
  TTSEngine: {
    getInstance: () => ({
      loadEngine: mocks.loadEngine,
      unloadEngine: mocks.unloadEngine,
      isReady: mocks.ttsIsReady,
      synthesize: mocks.synthesize,
      getAvailableVoices: mocks.getAvailableVoices,
      getInfo: mocks.getInfo,
    }),
  },
}));

vi.mock('../../src/main/voice/voice-profile-manager', () => ({
  VoiceProfileManager: {
    getInstance: () => ({
      getActiveProfile: mocks.getActiveProfile,
      setActiveProfile: mocks.setActiveProfile,
      listProfiles: mocks.listProfiles,
      createProfile: mocks.createProfile,
      deleteProfile: mocks.deleteProfile,
      previewVoice: mocks.previewVoice,
    }),
  },
}));

vi.mock('../../src/main/voice/speech-synthesis', () => ({
  SpeechSynthesisManager: {
    getInstance: () => ({
      speak: mocks.speak,
      speakImmediate: mocks.speakImmediate,
      stop: mocks.synthStop,
      pause: mocks.pause,
      resume: mocks.resume,
      isSpeaking: mocks.isSpeaking,
      getQueueLength: mocks.getQueueLength,
      on: mocks.synthesisOn,
    }),
  },
}));

vi.mock('../../src/main/ipc/validate', () => ({
  assertString: vi.fn((val: unknown, label: string) => {
    if (typeof val !== 'string' || val.length === 0) throw new Error(`${label} requires a string`);
  }),
  assertObject: vi.fn((val: unknown, label: string) => {
    if (!val || typeof val !== 'object' || Array.isArray(val)) throw new Error(`${label} requires an object`);
  }),
  assertNumber: vi.fn((val: unknown, label: string) => {
    if (typeof val !== 'number') throw new Error(`${label} requires a number`);
  }),
}));

import { registerVoicePipelineHandlers } from '../../src/main/ipc/voice-pipeline-handlers';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Voice Pipeline Handlers — Sprint 7 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerVoicePipelineHandlers({
      getMainWindow: () => ({ webContents: { send: mockSend } } as any),
    });
  });

  describe('Handler Registration', () => {
    it('registers all expected channels', () => {
      const expected = [
        // Whisper
        'voice:whisper:load-model', 'voice:whisper:unload-model', 'voice:whisper:is-ready',
        'voice:whisper:transcribe', 'voice:whisper:get-available-models',
        'voice:whisper:is-model-downloaded', 'voice:whisper:download-model',
        // Capture
        'voice:capture:start', 'voice:capture:stop', 'voice:capture:is-capturing',
        'voice:capture:get-audio-level',
        // Pipeline
        'voice:pipeline:start', 'voice:pipeline:stop', 'voice:pipeline:is-listening',
        'voice:pipeline:get-stats',
        // TTS
        'voice:tts:load-engine', 'voice:tts:unload-engine', 'voice:tts:is-ready',
        'voice:tts:synthesize', 'voice:tts:get-available-voices', 'voice:tts:get-info',
        // Voice Profiles
        'voice:profiles:get-active', 'voice:profiles:set-active', 'voice:profiles:list',
        'voice:profiles:create', 'voice:profiles:delete', 'voice:profiles:preview',
        // Speech Synthesis
        'voice:speech:speak', 'voice:speech:speak-immediate', 'voice:speech:stop',
        'voice:speech:pause', 'voice:speech:resume', 'voice:speech:is-speaking',
        'voice:speech:get-queue-length',
        // Binary downloads
        'voice:ensure-whisper-binary', 'voice:ensure-tts-binary', 'voice:ensure-tts-model',
      ];
      for (const ch of expected) {
        expect(handlers.has(ch), `Missing handler: ${ch}`).toBe(true);
      }
    });

    it('registers exactly 37 handlers', () => {
      expect(handlers.size).toBe(37);
    });
  });

  describe('Whisper Provider', () => {
    it('loadModel delegates', async () => {
      await invoke('voice:whisper:load-model', 'base');
      expect(mocks.loadModel).toHaveBeenCalledWith('base');
    });

    it('loadModel works without size arg', async () => {
      await invoke('voice:whisper:load-model');
      expect(mocks.loadModel).toHaveBeenCalledWith(undefined);
    });

    it('transcribe delegates with array', async () => {
      const result = await invoke('voice:whisper:transcribe', [0.1, 0.2]);
      expect(mocks.transcribe).toHaveBeenCalled();
      expect(result).toEqual({ text: 'hello' });
    });

    it('transcribe rejects non-array', async () => {
      await expect(invoke('voice:whisper:transcribe', 'not-array')).rejects.toThrow();
    });
  });

  describe('Audio Capture', () => {
    it('startCapture delegates', async () => {
      await invoke('voice:capture:start');
      expect(mocks.startCapture).toHaveBeenCalled();
    });

    it('isCapturing delegates', () => {
      const result = invoke('voice:capture:is-capturing');
      expect(result).toBe(false);
    });
  });

  describe('Transcription Pipeline', () => {
    it('start delegates', async () => {
      await invoke('voice:pipeline:start');
      expect(mocks.pipelineStart).toHaveBeenCalled();
    });

    it('getStats delegates', () => {
      invoke('voice:pipeline:get-stats');
      expect(mocks.getStats).toHaveBeenCalled();
    });
  });

  describe('TTS Engine', () => {
    it('synthesize delegates and converts to array', async () => {
      const result = await invoke('voice:tts:synthesize', 'Hello world');
      expect(mocks.synthesize).toHaveBeenCalledWith('Hello world', undefined);
      // Should return regular array, not Float32Array
      expect(Array.isArray(result)).toBe(true);
    });

    it('synthesize rejects non-string text', async () => {
      await expect(invoke('voice:tts:synthesize', 42)).rejects.toThrow();
    });
  });

  describe('Voice Profiles', () => {
    it('createProfile delegates with opts', () => {
      const opts = { name: 'Test', voiceId: 'v1' };
      invoke('voice:profiles:create', opts);
      expect(mocks.createProfile).toHaveBeenCalledWith(opts);
    });

    it('createProfile rejects missing name', () => {
      expect(() => invoke('voice:profiles:create', { voiceId: 'v1' })).toThrow();
    });

    it('setActiveProfile rejects non-string', () => {
      expect(() => invoke('voice:profiles:set-active', 42)).toThrow();
    });

    it('preview converts Float32Array to array', async () => {
      const result = await invoke('voice:profiles:preview', 'vp-1');
      expect(mocks.previewVoice).toHaveBeenCalledWith('vp-1');
      expect(Array.isArray(result)).toBe(true);
    });
  });

  describe('Speech Synthesis', () => {
    it('speak delegates with text', async () => {
      await invoke('voice:speech:speak', 'Hello');
      expect(mocks.speak).toHaveBeenCalledWith('Hello', undefined);
    });

    it('speak rejects non-string', async () => {
      await expect(invoke('voice:speech:speak', 42)).rejects.toThrow();
    });

    it('isSpeaking delegates', () => {
      const result = invoke('voice:speech:is-speaking');
      expect(result).toBe(false);
    });
  });

  describe('Event Forwarding', () => {
    it('registers capture events', () => {
      expect(mocks.captureOn).toHaveBeenCalledWith('voice-start', expect.any(Function));
      expect(mocks.captureOn).toHaveBeenCalledWith('voice-end', expect.any(Function));
      expect(mocks.captureOn).toHaveBeenCalledWith('audio-chunk', expect.any(Function));
      expect(mocks.captureOn).toHaveBeenCalledWith('error', expect.any(Function));
    });

    it('registers pipeline events', () => {
      expect(mocks.pipelineOn).toHaveBeenCalledWith('transcript', expect.any(Function));
      expect(mocks.pipelineOn).toHaveBeenCalledWith('partial', expect.any(Function));
      expect(mocks.pipelineOn).toHaveBeenCalledWith('error', expect.any(Function));
    });

    it('registers synthesis events', () => {
      expect(mocks.synthesisOn).toHaveBeenCalledWith('utterance-start', expect.any(Function));
      expect(mocks.synthesisOn).toHaveBeenCalledWith('utterance-end', expect.any(Function));
      expect(mocks.synthesisOn).toHaveBeenCalledWith('queue-empty', expect.any(Function));
      expect(mocks.synthesisOn).toHaveBeenCalledWith('interrupted', expect.any(Function));
    });
  });
});
