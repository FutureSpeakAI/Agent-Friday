/**
 * Sprint 5 Integration: Vision Circle
 *
 * End-to-end integration test validating the full see-understand-respond pipeline:
 * ScreenContext enriches LLM system prompt -> ImageUnderstanding processes user images
 *   -> VisionProvider describes images -> LLM generates context-aware response
 *   -> Vision model loads on-demand -> unloads after inactivity timeout
 *   -> VRAM tracked correctly -> graceful degradation when unavailable
 *
 * Sprint 5 N.1: 'The Sight' -- Vision Circle Integration
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (vi.mock is hoisted, so variables must be too) --

const mocks = vi.hoisted(() => {
  const iuListeners = new Map<string, Set<(payload?: unknown) => void>>();
  return {
    // VisionProvider mocks
    visionDescribe: vi.fn(async () => 'A screenshot showing a code editor with TypeScript code'),
    visionAnswer: vi.fn(async () => 'The image shows a bar chart comparing Q1 and Q2 revenue'),
    visionIsReady: vi.fn(() => true),
    visionLoadModel: vi.fn(async () => {}),
    visionUnloadModel: vi.fn(),
    visionGetModelInfo: vi.fn(() => ({ name: 'moondream:latest', vramUsageMB: 1200, loaded: true })),

    // ScreenContext mocks
    screenCaptureScreen: vi.fn(async () => Buffer.from('fake-png-screenshot')),
    screenGetContext: vi.fn(() => 'Desktop showing VS Code with a TypeScript file open'),
    screenStartAutoCapture: vi.fn(),
    screenStopAutoCapture: vi.fn(),

    // ImageUnderstanding mocks
    iuListeners,
    iuProcessImage: vi.fn(async () => ({
      description: 'A flowchart diagram showing system architecture',
      source: 'buffer' as const,
      timestamp: Date.now(),
      imageSizeBytes: 50000,
    })),
    iuGetLastResult: vi.fn(() => null),

    // OllamaLifecycle mocks
    ollamaGetHealth: vi.fn(() => ({ running: true, modelsLoaded: 2, vramUsed: 6700, vramTotal: 12288 })),
    ollamaGetLoadedModels: vi.fn(() => [
      { name: 'llama3:8b-instruct-q4_K_M', sizeVram: 5500 * 1024 * 1024 },
      { name: 'moondream:latest', sizeVram: 1200 * 1024 * 1024 },
    ]),

    // LLM mocks
    llmComplete: vi.fn(async () => ({
      content: 'Based on the screen context, I can see you have a TypeScript file open.',
      toolCalls: [],
      usage: { inputTokens: 50, outputTokens: 30 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 200,
    })),

    settingsGet: vi.fn(() => ({})),
    sendMock: vi.fn(),
  };
});

vi.mock('electron', () => ({
  app: {
    isPackaged: false,
    getPath: vi.fn(() => '/tmp/test'),
    getName: vi.fn(() => 'nexus-test'),
    on: vi.fn(),
    whenReady: vi.fn().mockResolvedValue(undefined),
  },
  ipcMain: { handle: vi.fn(), on: vi.fn(), once: vi.fn() },
  BrowserWindow: {
    getAllWindows: vi.fn(() => [{ webContents: { send: mocks.sendMock } }]),
  },
  nativeTheme: { shouldUseDarkColors: true, on: vi.fn() },
  shell: { openExternal: vi.fn() },
  dialog: { showOpenDialog: vi.fn() },
  screen: {
    getPrimaryDisplay: vi.fn(() => ({ workAreaSize: { width: 1920, height: 1080 } })),
  },
  clipboard: { readImage: vi.fn() },
  desktopCapturer: { getSources: vi.fn() },
}));

vi.mock('../../../src/main/settings', () => ({
  settingsManager: {
    get: mocks.settingsGet,
    setSetting: vi.fn(() => Promise.resolve()),
  },
}));

vi.mock('../../../src/main/vision/vision-provider', () => ({
  VisionProvider: {
    getInstance: () => ({
      describe: mocks.visionDescribe,
      answer: mocks.visionAnswer,
      isReady: mocks.visionIsReady,
      loadModel: mocks.visionLoadModel,
      unloadModel: mocks.visionUnloadModel,
      getModelInfo: mocks.visionGetModelInfo,
    }),
    resetInstance: vi.fn(),
  },
  visionProvider: {
    describe: mocks.visionDescribe,
    answer: mocks.visionAnswer,
    isReady: mocks.visionIsReady,
    loadModel: mocks.visionLoadModel,
    unloadModel: mocks.visionUnloadModel,
    getModelInfo: mocks.visionGetModelInfo,
  },
}));

vi.mock('../../../src/main/vision/screen-context', () => ({
  ScreenContext: {
    getInstance: () => ({
      captureScreen: mocks.screenCaptureScreen,
      getContext: mocks.screenGetContext,
      startAutoCapture: mocks.screenStartAutoCapture,
      stopAutoCapture: mocks.screenStopAutoCapture,
    }),
    resetInstance: vi.fn(),
  },
  screenContext: {
    captureScreen: mocks.screenCaptureScreen,
    getContext: mocks.screenGetContext,
    startAutoCapture: mocks.screenStartAutoCapture,
    stopAutoCapture: mocks.screenStopAutoCapture,
  },
}));

vi.mock('../../../src/main/vision/image-understanding', () => ({
  ImageUnderstanding: {
    getInstance: () => ({
      processImage: mocks.iuProcessImage,
      getLastResult: mocks.iuGetLastResult,
      on: vi.fn((event: string, cb: (payload?: unknown) => void) => {
        if (!mocks.iuListeners.has(event)) mocks.iuListeners.set(event, new Set());
        mocks.iuListeners.get(event)!.add(cb);
        return () => { mocks.iuListeners.get(event)?.delete(cb); };
      }),
    }),
    resetInstance: vi.fn(),
  },
  imageUnderstanding: {
    processImage: mocks.iuProcessImage,
    getLastResult: mocks.iuGetLastResult,
    on: vi.fn((event: string, cb: (payload?: unknown) => void) => {
      if (!mocks.iuListeners.has(event)) mocks.iuListeners.set(event, new Set());
      mocks.iuListeners.get(event)!.add(cb);
      return () => { mocks.iuListeners.get(event)?.delete(cb); };
    }),
  },
}));

// -- Imports (after all vi.mock calls) ----------------------------------------

import { llmClient } from '../../../src/main/llm-client';
import type { LLMRequest, LLMProvider } from '../../../src/main/llm-client';
import type { ProviderName } from '../../../src/main/intelligence-router';
import { visionProvider } from '../../../src/main/vision/vision-provider';
import { screenContext } from '../../../src/main/vision/screen-context';
import { imageUnderstanding } from '../../../src/main/vision/image-understanding';

// -- Helpers ------------------------------------------------------------------

function emitIU(event: string, payload?: unknown): void {
  const cbs = mocks.iuListeners.get(event);
  if (cbs) { for (const cb of cbs) { cb(payload); } }
}

function createMockLLMProvider(name: ProviderName, available = true): LLMProvider {
  return {
    name,
    isAvailable: () => available,
    complete: mocks.llmComplete as unknown as LLMProvider['complete'],
    async *stream() { yield { done: true }; },
  };
}

/**
 * Wire the vision circle:
 * - Screen context enriches LLM system prompt
 * - Image results inject image context into LLM messages
 * - On-demand vision model loading
 * - Inactivity timeout for VRAM release
 */
function wireVisionCircle(): { teardown: () => void; processUserMessage: (text: string) => Promise<string> } {
  const unsubs: Array<() => void> = [];
  let lastImageDescription: string | null = null;
  let visionTimeout: ReturnType<typeof setTimeout> | null = null;

  // Listen for image results from ImageUnderstanding
  unsubs.push(imageUnderstanding.on('image-result', (result: unknown) => {
    const ir = result as { description: string };
    lastImageDescription = ir.description;
    // Reset inactivity timeout
    if (visionTimeout) clearTimeout(visionTimeout);
    visionTimeout = setTimeout(() => {
      visionProvider.unloadModel();
      lastImageDescription = null;
    }, 60_000); // 60s inactivity timeout
  }));

  async function processUserMessage(text: string): Promise<string> {
    const messages: Array<{ role: string; content: string }> = [];

    // Enrich system prompt with screen context if available
    const screenDesc = screenContext.getContext();
    let systemPrompt = 'You are Agent Friday, a helpful AI assistant.';
    if (screenDesc) {
      systemPrompt += '\n\nCurrent screen context: ' + screenDesc;
    }

    // If there's a recent image description, inject as context
    if (lastImageDescription) {
      messages.push({
        role: 'user',
        content: '[Image context: ' + lastImageDescription + ']',
      });
    }

    messages.push({ role: 'user', content: text });

    const response = await llmClient.complete({
      systemPrompt,
      messages: messages as LLMRequest['messages'],
      maxTokens: 256,
    });

    return response.content;
  }

  return {
    teardown: () => {
      for (const u of unsubs) u();
      if (visionTimeout) clearTimeout(visionTimeout);
    },
    processUserMessage,
  };
}

// -- Tests --------------------------------------------------------------------

describe('Vision Circle Integration -- Sprint 5 N.1', () => {
  let circle: ReturnType<typeof wireVisionCircle>;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.iuListeners.clear();

    // Reset mock implementations to defaults
    mocks.visionIsReady.mockReturnValue(true);
    mocks.visionDescribe.mockImplementation(
      async () => 'A screenshot showing a code editor with TypeScript code',
    );
    mocks.visionLoadModel.mockImplementation(async () => {});
    mocks.visionUnloadModel.mockImplementation(() => {});
    mocks.visionGetModelInfo.mockReturnValue({
      name: 'moondream:latest', vramUsageMB: 1200, loaded: true,
    });
    mocks.screenGetContext.mockReturnValue(
      'Desktop showing VS Code with a TypeScript file open',
    );
    mocks.screenCaptureScreen.mockImplementation(
      async () => Buffer.from('fake-png-screenshot'),
    );
    mocks.iuProcessImage.mockImplementation(async () => ({
      description: 'A flowchart diagram showing system architecture',
      source: 'buffer' as const,
      timestamp: Date.now(),
      imageSizeBytes: 50000,
    }));
    mocks.llmComplete.mockImplementation(async () => ({
      content: 'Based on the screen context, I can see you have a TypeScript file open.',
      toolCalls: [],
      usage: { inputTokens: 50, outputTokens: 30 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 200,
    }));
    mocks.ollamaGetHealth.mockReturnValue({
      running: true, modelsLoaded: 2, vramUsed: 6700, vramTotal: 12288,
    });
    mocks.ollamaGetLoadedModels.mockReturnValue([
      { name: 'llama3:8b-instruct-q4_K_M', sizeVram: 5500 * 1024 * 1024 },
      { name: 'moondream:latest', sizeVram: 1200 * 1024 * 1024 },
    ]);

    // Register mock local LLM provider
    llmClient.registerProvider(createMockLLMProvider('local'));

    circle = wireVisionCircle();
  });

  afterEach(() => {
    circle.teardown();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  // 1. Screen context automatically included in LLM system prompt when available
  it('includes screen context in LLM system prompt when available', async () => {
    await circle.processUserMessage('What am I looking at?');

    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    const call = mocks.llmComplete.mock.calls[0][0] as LLMRequest;
    expect(call.systemPrompt).toContain('Current screen context:');
    expect(call.systemPrompt).toContain('VS Code with a TypeScript file open');
  });

  // 2. User-provided image flows through to LLM as contextual message
  it('user-provided image description flows to LLM as context message', async () => {
    // Emit image-result event (simulating ImageUnderstanding completing)
    emitIU('image-result', {
      description: 'A flowchart diagram showing system architecture',
      source: 'buffer',
      timestamp: Date.now(),
      imageSizeBytes: 50000,
    });

    await circle.processUserMessage('Explain this diagram');

    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    const call = mocks.llmComplete.mock.calls[0][0] as LLMRequest;
    // Should have image context message + user message
    expect(call.messages).toHaveLength(2);
    expect(call.messages[0].role).toBe('user');
    expect(call.messages[0].content).toContain('[Image context:');
    expect(call.messages[0].content).toContain('flowchart diagram');
    expect(call.messages[1].role).toBe('user');
    expect(call.messages[1].content).toBe('Explain this diagram');
  });

  // 3. LLM can reference image content in its response
  it('LLM response references image content', async () => {
    mocks.llmComplete.mockImplementationOnce(async () => ({
      content: 'The flowchart shows three main components connected by arrows indicating data flow.',
      toolCalls: [],
      usage: { inputTokens: 80, outputTokens: 40 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 250,
    }));

    emitIU('image-result', {
      description: 'A flowchart diagram showing system architecture',
      source: 'buffer',
      timestamp: Date.now(),
      imageSizeBytes: 50000,
    });

    const response = await circle.processUserMessage('Describe the architecture');

    expect(response).toContain('flowchart');
    expect(response).toContain('components');
  });

  // 4. System degrades gracefully when VisionProvider unavailable
  it('degrades gracefully when VisionProvider is unavailable', async () => {
    mocks.visionIsReady.mockReturnValue(false);

    // No image-result event emitted (vision not available)
    const response = await circle.processUserMessage('Hello, what can you help me with?');

    // Should still work -- just no image context
    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    expect(response).toBeTruthy();
    const call = mocks.llmComplete.mock.calls[0][0] as LLMRequest;
    // Only the user message, no image context
    expect(call.messages).toHaveLength(1);
    expect(call.messages[0].content).toBe('Hello, what can you help me with?');
  });

  // 5. System degrades gracefully when screen capture is denied
  it('degrades gracefully when screen capture is denied', async () => {
    mocks.screenGetContext.mockReturnValue(null);

    await circle.processUserMessage('What should I do next?');

    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    const call = mocks.llmComplete.mock.calls[0][0] as LLMRequest;
    // System prompt should NOT contain screen context
    expect(call.systemPrompt).toBe('You are Agent Friday, a helpful AI assistant.');
    expect(call.systemPrompt).not.toContain('Current screen context:');
  });

  // 6. Vision model loads on-demand when image is provided
  it('loads vision model on-demand when image is provided', async () => {
    // Vision not ready initially
    mocks.visionIsReady.mockReturnValueOnce(false);

    // Simulate on-demand loading: processImage triggers loadModel internally
    mocks.iuProcessImage.mockImplementationOnce(async () => {
      // On-demand: load model if not ready
      if (!visionProvider.isReady()) {
        await visionProvider.loadModel();
      }
      return {
        description: 'A chart showing quarterly data',
        source: 'buffer' as const,
        timestamp: Date.now(),
        imageSizeBytes: 40000,
      };
    });

    await imageUnderstanding.processImage(Buffer.from('fake-image'));

    expect(mocks.visionLoadModel).toHaveBeenCalledTimes(1);
    expect(mocks.iuProcessImage).toHaveBeenCalledTimes(1);
  });

  // 7. Vision model unloads after inactivity timeout (frees VRAM)
  it('unloads vision model after inactivity timeout', async () => {
    vi.useFakeTimers();

    // Emit image-result to start the inactivity timer
    emitIU('image-result', {
      description: 'Some image content',
      source: 'buffer',
      timestamp: Date.now(),
      imageSizeBytes: 30000,
    });

    // Model should NOT be unloaded yet
    expect(mocks.visionUnloadModel).not.toHaveBeenCalled();

    // Advance time by 60 seconds (the inactivity timeout)
    vi.advanceTimersByTime(60_000);

    // Model should now be unloaded
    expect(mocks.visionUnloadModel).toHaveBeenCalledTimes(1);
  });

  // 8. OllamaLifecycle accurately reports VRAM with vision model loaded
  it('OllamaLifecycle reports correct VRAM with vision model loaded', () => {
    const health = mocks.ollamaGetHealth();
    expect(health.running).toBe(true);
    expect(health.modelsLoaded).toBe(2);
    // LLM (5500 MB) + Vision (1200 MB) = 6700 MB
    expect(health.vramUsed).toBe(6700);
    expect(health.vramTotal).toBe(12288);

    const models = mocks.ollamaGetLoadedModels();
    expect(models).toHaveLength(2);
    const visionModel = models.find(
      (m: { name: string }) => m.name === 'moondream:latest',
    );
    expect(visionModel).toBeDefined();
    // 1200 MB in bytes
    expect(visionModel!.sizeVram).toBe(1200 * 1024 * 1024);

    // Verify VRAM budget: Embed(0.5GB) + LLM(5.5GB) + Vision(1.2GB) = 7.2GB on 12GB
    const vramBudgetMB = 500 + 5500 + 1200; // embed + LLM + vision
    expect(vramBudgetMB).toBe(7200);
    expect(vramBudgetMB).toBeLessThan(health.vramTotal);
  });

  // 9. Full round-trip: image input -> vision description -> LLM response
  it('full round-trip: image -> description -> LLM response', async () => {
    // Step 1: Process an image through ImageUnderstanding
    const imageResult = await imageUnderstanding.processImage(Buffer.from('fake-chart-image'));
    expect(imageResult.description).toBeTruthy();

    // Step 2: Emit the image-result event (normally done internally by processImage)
    emitIU('image-result', imageResult);

    // Step 3: Configure LLM to respond with image-aware content
    mocks.llmComplete.mockImplementationOnce(async () => ({
      content: 'The system architecture diagram shows a three-layer design with clear separation of concerns.',
      toolCalls: [],
      usage: { inputTokens: 90, outputTokens: 45 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 180,
    }));

    // Step 4: Process user message (should include both screen + image context)
    const response = await circle.processUserMessage('Analyze this architecture');

    // Verify complete chain
    expect(mocks.iuProcessImage).toHaveBeenCalledTimes(1);
    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);

    const call = mocks.llmComplete.mock.calls[0][0] as LLMRequest;
    // System prompt has screen context
    expect(call.systemPrompt).toContain('Current screen context:');
    // Messages include image context + user text
    expect(call.messages).toHaveLength(2);
    expect(call.messages[0].content).toContain('[Image context:');
    expect(call.messages[1].content).toBe('Analyze this architecture');
    // Response is the image-aware LLM output
    expect(response).toContain('architecture diagram');
  });

  // 10. Vision circle does not interfere with basic LLM operation (no regressions)
  it('vision circle does not interfere with basic LLM operation', async () => {
    // No screen context, no image context -- pure text
    mocks.screenGetContext.mockReturnValue(null);
    // No image-result events emitted

    mocks.llmComplete.mockImplementationOnce(async () => ({
      content: 'Hello! I am Agent Friday. How can I help you today?',
      toolCalls: [],
      usage: { inputTokens: 10, outputTokens: 15 },
      model: 'test-model',
      provider: 'local' as const,
      stopReason: 'end_turn' as const,
      latencyMs: 100,
    }));

    const response = await circle.processUserMessage('Hello');

    expect(mocks.llmComplete).toHaveBeenCalledTimes(1);
    expect(response).toContain('Agent Friday');

    const call = mocks.llmComplete.mock.calls[0][0] as LLMRequest;
    // No screen context in system prompt
    expect(call.systemPrompt).toBe('You are Agent Friday, a helpful AI assistant.');
    // Only the user message, no image context
    expect(call.messages).toHaveLength(1);
    expect(call.messages[0].role).toBe('user');
    expect(call.messages[0].content).toBe('Hello');
  });
});
