/**
 * ScreenContext -- Unit tests for screenshot capture and UI analysis.
 *
 * Tests full-screen capture, window capture, region capture, VisionProvider
 * integration for description, cached context retrieval, auto-capture with
 * interval, auto-capture stopping, context-update event emission,
 * multi-monitor handling, and full Electron/VisionProvider mocking.
 *
 * All Electron desktopCapturer and VisionProvider interactions are mocked --
 * no real screen capture or model required in CI.
 *
 * Sprint 5 M.2: "The Glance" -- ScreenContext
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => ({
  getSources: vi.fn(),
  getPrimaryDisplay: vi.fn(),
  visionDescribe: vi.fn(),
  visionIsReady: vi.fn(),
}));

vi.mock('electron', () => ({
  desktopCapturer: { getSources: mocks.getSources },
  screen: { getPrimaryDisplay: mocks.getPrimaryDisplay },
  BrowserWindow: vi.fn(),
  app: {
    isPackaged: false,
    getPath: vi.fn(() => '/tmp'),
    getName: vi.fn(() => 'test'),
    on: vi.fn(),
    whenReady: vi.fn().mockResolvedValue(undefined),
  },
  ipcMain: { handle: vi.fn(), on: vi.fn() },
  nativeTheme: { shouldUseDarkColors: true, on: vi.fn() },
  shell: { openExternal: vi.fn() },
  dialog: { showOpenDialog: vi.fn(), showSaveDialog: vi.fn() },
}));

vi.mock('../../src/main/vision/vision-provider', () => ({
  visionProvider: {
    describe: mocks.visionDescribe,
    isReady: mocks.visionIsReady,
  },
}));

import { ScreenContext, screenContext } from '../../src/main/vision/screen-context';

// -- Helper: create mock NativeImage ----------------------------------------

function makeMockNativeImage(width = 768, height = 432): any {
  const pngBuffer = Buffer.from('mock-png-data');
  const self = {
    toPNG: () => pngBuffer,
    getSize: () => ({ width, height }),
    crop: vi.fn().mockReturnValue({
      toPNG: () => Buffer.from('mock-cropped-png'),
      getSize: () => ({ width: 200, height: 200 }),
      resize: vi.fn().mockReturnThis(),
    }),
    resize: vi.fn().mockReturnThis(),
    isEmpty: () => false,
  };
  return self;
}

// -- Default mock setup ------------------------------------------------------

function setupDefaultMocks(): void {
  mocks.getSources.mockResolvedValue([
    {
      id: 'screen:0:0',
      name: 'Entire Screen',
      thumbnail: makeMockNativeImage(),
      display_id: '1',
      appIcon: null,
    },
  ]);

  mocks.getPrimaryDisplay.mockReturnValue({
    id: 1,
    bounds: { x: 0, y: 0, width: 1920, height: 1080 },
    scaleFactor: 1,
    size: { width: 1920, height: 1080 },
  });

  mocks.visionDescribe.mockResolvedValue(
    'A desktop showing a code editor with TypeScript file open.',
  );
  mocks.visionIsReady.mockReturnValue(true);
}

// -- Test Suite ---------------------------------------------------------------

describe('ScreenContext', () => {
  beforeEach(() => {
    ScreenContext.resetInstance();
    vi.clearAllMocks();
    vi.useFakeTimers();
    setupDefaultMocks();
  });

  afterEach(() => {
    ScreenContext.resetInstance();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  // Test 1: captureScreen() returns a PNG buffer of the display
  it('captureScreen() returns a PNG buffer of the display', async () => {
    const ctx = ScreenContext.getInstance();

    const buffer = await ctx.captureScreen();

    expect(buffer).toBeInstanceOf(Buffer);
    expect(buffer).not.toBeNull();
    expect(buffer!.length).toBeGreaterThan(0);

    expect(mocks.getSources).toHaveBeenCalledWith(
      expect.objectContaining({ types: ['screen'] }),
    );
  });

  // Test 2: captureWindow() returns a PNG buffer of a specific window
  it('captureWindow() returns a PNG buffer of a specific window', async () => {
    const windowImage = makeMockNativeImage(800, 600);
    mocks.getSources.mockResolvedValue([
      {
        id: 'window:42:0',
        name: 'My Editor',
        thumbnail: windowImage,
        display_id: '',
        appIcon: null,
      },
      {
        id: 'window:99:0',
        name: 'Browser',
        thumbnail: makeMockNativeImage(1024, 768),
        display_id: '',
        appIcon: null,
      },
    ]);

    const ctx = ScreenContext.getInstance();
    const buffer = await ctx.captureWindow(42);

    expect(buffer).toBeInstanceOf(Buffer);
    expect(buffer).not.toBeNull();

    expect(mocks.getSources).toHaveBeenCalledWith(
      expect.objectContaining({ types: ['window'] }),
    );
  });

  // Test 3: captureRegion(rect) captures a defined rectangular area
  it('captureRegion(rect) captures a defined rectangular area', async () => {
    const ctx = ScreenContext.getInstance();

    const rect = { x: 100, y: 100, width: 200, height: 200 };
    const buffer = await ctx.captureRegion(rect);

    expect(buffer).toBeInstanceOf(Buffer);
    expect(buffer).not.toBeNull();

    const source = (await mocks.getSources.mock.results[0].value)[0];
    expect(source.thumbnail.crop).toHaveBeenCalledWith(rect);
  });

  // Test 4: Captured image is passed to VisionProvider for description
  it('captured image is passed to VisionProvider for description', async () => {
    const ctx = ScreenContext.getInstance();

    await ctx.captureScreen();

    expect(mocks.visionDescribe).toHaveBeenCalledTimes(1);
    expect(mocks.visionDescribe).toHaveBeenCalledWith(
      expect.any(Buffer),
    );
  });

  // Test 5: getContext() returns cached description (no redundant vision calls)
  it('getContext() returns cached description without redundant vision calls', async () => {
    const ctx = ScreenContext.getInstance();

    expect(ctx.getContext()).toBeNull();

    await ctx.captureScreen();

    const context = ctx.getContext();
    expect(context).toBe(
      'A desktop showing a code editor with TypeScript file open.',
    );

    ctx.getContext();
    ctx.getContext();
    expect(mocks.visionDescribe).toHaveBeenCalledTimes(1);
  });

  // Test 6: startAutoCapture(30000) captures every 30 seconds
  it('startAutoCapture(30000) captures every 30 seconds', async () => {
    const ctx = ScreenContext.getInstance();

    ctx.startAutoCapture(30_000);

    expect(mocks.getSources).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(30_000);
    expect(mocks.getSources).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(mocks.getSources).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(mocks.getSources).toHaveBeenCalledTimes(3);

    ctx.stopAutoCapture();
  });

  // Test 7: stopAutoCapture() clears the interval
  it('stopAutoCapture() clears the interval', async () => {
    const ctx = ScreenContext.getInstance();

    ctx.startAutoCapture(30_000);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(mocks.getSources).toHaveBeenCalledTimes(1);

    ctx.stopAutoCapture();

    await vi.advanceTimersByTimeAsync(60_000);
    expect(mocks.getSources).toHaveBeenCalledTimes(1);
  });

  // Test 8: context-update event fires when new description differs from previous
  it('context-update event fires when new description differs from previous', async () => {
    const ctx = ScreenContext.getInstance();
    const eventPayloads: string[] = [];

    ctx.on('context-update', (description: string) => {
      eventPayloads.push(description);
    });

    mocks.visionDescribe.mockResolvedValueOnce('Desktop with code editor.');
    await ctx.captureScreen();
    expect(eventPayloads).toHaveLength(1);
    expect(eventPayloads[0]).toBe('Desktop with code editor.');

    mocks.visionDescribe.mockResolvedValueOnce('Desktop with code editor.');
    await ctx.captureScreen();
    expect(eventPayloads).toHaveLength(1);

    mocks.visionDescribe.mockResolvedValueOnce('Browser showing documentation page.');
    await ctx.captureScreen();
    expect(eventPayloads).toHaveLength(2);
    expect(eventPayloads[1]).toBe('Browser showing documentation page.');
  });

  // Test 9: Screen capture handles multi-monitor setups (returns primary display)
  it('handles multi-monitor setups by returning primary display', async () => {
    const primaryImage = makeMockNativeImage(768, 432);
    const secondaryImage = makeMockNativeImage(768, 432);

    mocks.getSources.mockResolvedValue([
      {
        id: 'screen:0:0',
        name: 'Primary Display',
        thumbnail: primaryImage,
        display_id: '1',
        appIcon: null,
      },
      {
        id: 'screen:1:0',
        name: 'Secondary Display',
        thumbnail: secondaryImage,
        display_id: '2',
        appIcon: null,
      },
    ]);

    mocks.getPrimaryDisplay.mockReturnValue({
      id: 1,
      bounds: { x: 0, y: 0, width: 1920, height: 1080 },
      scaleFactor: 1,
      size: { width: 1920, height: 1080 },
    });

    const ctx = ScreenContext.getInstance();
    const buffer = await ctx.captureScreen();

    expect(buffer).not.toBeNull();
    expect(buffer).toEqual(primaryImage.toPNG());
  });

  // Test 10: All tests mock desktopCapturer and VisionProvider (+ singleton pattern)
  it('singleton pattern and mocks work correctly', () => {
    const a = ScreenContext.getInstance();
    const b = ScreenContext.getInstance();
    expect(a).toBe(b);

    ScreenContext.resetInstance();
    const c = ScreenContext.getInstance();
    expect(c).not.toBe(a);

    expect(screenContext).toBeInstanceOf(ScreenContext);

    expect(mocks.getSources).toBeDefined();
    expect(mocks.visionDescribe).toBeDefined();
    expect(mocks.visionIsReady).toBeDefined();
    expect(mocks.getPrimaryDisplay).toBeDefined();

    const unsub = c.on('context-update', () => {});
    expect(typeof unsub).toBe('function');
    unsub();
  });
});
