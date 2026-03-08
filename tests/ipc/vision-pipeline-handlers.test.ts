/**
 * Tests for vision-pipeline-handlers.ts — IPC layer for VisionProvider,
 * ScreenContext, and ImageUnderstanding.
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
  // VisionProvider
  loadModel: vi.fn().mockResolvedValue(undefined),
  unloadModel: vi.fn(),
  isReady: vi.fn().mockReturnValue(false),
  getModelInfo: vi.fn().mockReturnValue({}),
  describe: vi.fn().mockResolvedValue({ description: 'a cat' }),
  answer: vi.fn().mockResolvedValue({ answer: 'yes' }),
  // ScreenContext
  captureScreen: vi.fn().mockResolvedValue(Buffer.from('png-data')),
  captureWindow: vi.fn().mockResolvedValue(Buffer.from('window-data')),
  captureRegion: vi.fn().mockResolvedValue(Buffer.from('region-data')),
  getContext: vi.fn().mockReturnValue({}),
  startAutoCapture: vi.fn(),
  stopAutoCapture: vi.fn(),
  screenOn: vi.fn(),
  // ImageUnderstanding
  processImage: vi.fn().mockResolvedValue({ result: 'ok' }),
  processClipboardImage: vi.fn().mockResolvedValue({ result: 'clipboard' }),
  handleDrop: vi.fn().mockResolvedValue({ result: 'drop' }),
  handleFileSelect: vi.fn().mockResolvedValue({ result: 'selected' }),
  getLastResult: vi.fn().mockReturnValue(null),
  understandingOn: vi.fn(),
}));

vi.mock('../../src/main/vision/vision-provider', () => ({
  VisionProvider: {
    getInstance: () => ({
      loadModel: mocks.loadModel,
      unloadModel: mocks.unloadModel,
      isReady: mocks.isReady,
      getModelInfo: mocks.getModelInfo,
      describe: mocks.describe,
      answer: mocks.answer,
    }),
  },
}));

vi.mock('../../src/main/vision/screen-context', () => ({
  ScreenContext: {
    getInstance: () => ({
      captureScreen: mocks.captureScreen,
      captureWindow: mocks.captureWindow,
      captureRegion: mocks.captureRegion,
      getContext: mocks.getContext,
      startAutoCapture: mocks.startAutoCapture,
      stopAutoCapture: mocks.stopAutoCapture,
      on: mocks.screenOn,
    }),
  },
}));

vi.mock('../../src/main/vision/image-understanding', () => ({
  ImageUnderstanding: {
    getInstance: () => ({
      processImage: mocks.processImage,
      processClipboardImage: mocks.processClipboardImage,
      handleDrop: mocks.handleDrop,
      handleFileSelect: mocks.handleFileSelect,
      getLastResult: mocks.getLastResult,
      on: mocks.understandingOn,
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
  assertStringArray: vi.fn((val: unknown, label: string) => {
    if (!Array.isArray(val)) throw new Error(`${label} requires an array`);
  }),
}));

import { registerVisionPipelineHandlers } from '../../src/main/ipc/vision-pipeline-handlers';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Vision Pipeline Handlers — Sprint 7 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerVisionPipelineHandlers({
      getMainWindow: () => ({ webContents: { send: mockSend } } as any),
    });
  });

  describe('Handler Registration', () => {
    it('registers all expected channels', () => {
      const expected = [
        // Vision Provider
        'vision:load-model', 'vision:unload-model', 'vision:is-ready',
        'vision:get-model-info', 'vision:describe', 'vision:answer',
        // Screen Context
        'vision:screen:capture', 'vision:screen:capture-window',
        'vision:screen:capture-region', 'vision:screen:get-context',
        'vision:screen:start-auto-capture', 'vision:screen:stop-auto-capture',
        // Image Understanding
        'vision:understand:process', 'vision:understand:clipboard',
        'vision:understand:drop', 'vision:understand:file-select',
        'vision:understand:get-last-result',
      ];
      for (const ch of expected) {
        expect(handlers.has(ch), `Missing handler: ${ch}`).toBe(true);
      }
    });

    it('registers exactly 17 handlers', () => {
      expect(handlers.size).toBe(17);
    });
  });

  describe('Vision Provider', () => {
    it('loadModel delegates with optional name', async () => {
      await invoke('vision:load-model', 'llava');
      expect(mocks.loadModel).toHaveBeenCalledWith('llava');
    });

    it('loadModel works without name', async () => {
      await invoke('vision:load-model');
      expect(mocks.loadModel).toHaveBeenCalledWith(undefined);
    });

    it('describe delegates with base64 image', async () => {
      const b64 = Buffer.from('test-image').toString('base64');
      await invoke('vision:describe', b64);
      expect(mocks.describe).toHaveBeenCalledWith(expect.any(Buffer));
    });

    it('describe rejects non-string', async () => {
      await expect(invoke('vision:describe', 42)).rejects.toThrow();
    });

    it('answer delegates with image and question', async () => {
      const b64 = Buffer.from('test-image').toString('base64');
      await invoke('vision:answer', b64, 'What is this?');
      expect(mocks.answer).toHaveBeenCalledWith(expect.any(Buffer), 'What is this?');
    });

    it('answer rejects non-string question', async () => {
      const b64 = Buffer.from('test-image').toString('base64');
      await expect(invoke('vision:answer', b64, 42)).rejects.toThrow();
    });
  });

  describe('Screen Context', () => {
    it('capture returns base64 string', async () => {
      const result = await invoke('vision:screen:capture');
      expect(mocks.captureScreen).toHaveBeenCalled();
      expect(typeof result).toBe('string');
    });

    it('captureWindow delegates with optional windowId', async () => {
      await invoke('vision:screen:capture-window', 123);
      expect(mocks.captureWindow).toHaveBeenCalledWith(123);
    });

    it('captureRegion delegates with rect', async () => {
      await invoke('vision:screen:capture-region', { x: 0, y: 0, width: 100, height: 100 });
      expect(mocks.captureRegion).toHaveBeenCalled();
    });

    it('captureRegion rejects non-object', async () => {
      await expect(invoke('vision:screen:capture-region', 'not-object')).rejects.toThrow();
    });

    it('startAutoCapture delegates with interval', () => {
      invoke('vision:screen:start-auto-capture', 5000);
      expect(mocks.startAutoCapture).toHaveBeenCalledWith(5000);
    });
  });

  describe('Image Understanding', () => {
    it('processImage delegates with image and question', async () => {
      const b64 = Buffer.from('test').toString('base64');
      await invoke('vision:understand:process', b64, 'describe this');
      expect(mocks.processImage).toHaveBeenCalledWith(expect.any(Buffer), 'describe this');
    });

    it('processClipboard delegates', async () => {
      await invoke('vision:understand:clipboard');
      expect(mocks.processClipboardImage).toHaveBeenCalled();
    });

    it('handleDrop delegates with file array', async () => {
      await invoke('vision:understand:drop', ['/path/a.png', '/path/b.jpg']);
      expect(mocks.handleDrop).toHaveBeenCalledWith(['/path/a.png', '/path/b.jpg']);
    });

    it('handleDrop rejects non-array', async () => {
      await expect(invoke('vision:understand:drop', 'not-array')).rejects.toThrow();
    });

    it('getLastResult delegates', () => {
      const result = invoke('vision:understand:get-last-result');
      expect(result).toBe(null);
    });
  });

  describe('Event Forwarding', () => {
    it('registers screen context-update event', () => {
      expect(mocks.screenOn).toHaveBeenCalledWith('context-update', expect.any(Function));
    });

    it('registers image-result event', () => {
      expect(mocks.understandingOn).toHaveBeenCalledWith('image-result', expect.any(Function));
    });
  });
});
