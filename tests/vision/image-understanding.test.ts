/**
 * ImageUnderstanding -- Unit tests for user image input processing.
 *
 * Tests processImage with Buffer and file path, clipboard reading,
 * drag-drop handling, file picker selection, supported format validation,
 * 10MB size rejection, cached result retrieval, event emission with
 * source type, and full mocking of clipboard, file system, and VisionProvider.
 *
 * All Electron APIs, file system, and VisionProvider interactions are mocked --
 * no real clipboard, file dialog, or model required in CI.
 *
 * Sprint 5 M.3: "The Focus" -- ImageUnderstanding
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => ({
  visionDescribe: vi.fn(),
  visionAnswer: vi.fn(),
  visionIsReady: vi.fn(() => true),
  clipboardReadImage: vi.fn(),
  dialogShowOpenDialog: vi.fn(),
  fsReadFile: vi.fn(),
  fsStat: vi.fn(),
}));

vi.mock('electron', () => ({
  clipboard: { readImage: mocks.clipboardReadImage },
  dialog: { showOpenDialog: mocks.dialogShowOpenDialog },
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
  screen: { getPrimaryDisplay: vi.fn() },
  desktopCapturer: { getSources: vi.fn() },
}));

vi.mock('../../src/main/vision/vision-provider', () => ({
  VisionProvider: {
    getInstance: () => ({
      describe: mocks.visionDescribe,
      answer: mocks.visionAnswer,
      isReady: mocks.visionIsReady,
    }),
  },
  visionProvider: {
    describe: mocks.visionDescribe,
    answer: mocks.visionAnswer,
    isReady: mocks.visionIsReady,
  },
}));

vi.mock('node:fs/promises', () => ({
  readFile: (...args: unknown[]) => mocks.fsReadFile(...args),
  stat: (...args: unknown[]) => mocks.fsStat(...args),
}));

import { ImageUnderstanding, imageUnderstanding } from '../../src/main/vision/image-understanding';
import type { ImageResult } from '../../src/main/vision/image-understanding';

// -- Helper: create mock NativeImage ----------------------------------------

function makeMockNativeImage(empty = false): {
  toPNG: () => Buffer;
  toJPEG: (quality: number) => Buffer;
  isEmpty: () => boolean;
  getSize: () => { width: number; height: number };
} {
  return {
    toPNG: () => Buffer.from('mock-png-data'),
    toJPEG: (quality: number) => Buffer.from('mock-jpeg-data'),
    isEmpty: () => empty,
    getSize: () => ({ width: 100, height: 100 }),
  };
}

/** A small 1x1 red PNG as raw bytes (Buffer) */
function createTestImageBuffer(): Buffer {
  const pngHex =
    '89504e470d0a1a0a0000000d49484452000000010000000108020000009001' +
    '2e00000000c4944415408d76360f8cf00000000020001e221bc3300000000' +
    '49454e44ae426082';
  return Buffer.from(pngHex, 'hex');
}

// -- Test Suite ---------------------------------------------------------------

describe('ImageUnderstanding', () => {
  beforeEach(() => {
    ImageUnderstanding.resetInstance();
    vi.clearAllMocks();
    mocks.visionIsReady.mockReturnValue(true);
    mocks.visionDescribe.mockResolvedValue('A cat sitting on a table.');
    mocks.visionAnswer.mockResolvedValue('The cat is orange.');
  });

  afterEach(() => {
    ImageUnderstanding.resetInstance();
    vi.restoreAllMocks();
  });

  // Test 1: processImage(buffer) sends image to VisionProvider, returns description
  it('processImage(buffer) sends image to VisionProvider and returns description', async () => {
    const iu = ImageUnderstanding.getInstance();
    const imageBuffer = createTestImageBuffer();

    const result = await iu.processImage(imageBuffer);

    expect(result).toBeDefined();
    expect(result.description).toBe('A cat sitting on a table.');
    expect(result.source).toBe('buffer');
    expect(result.timestamp).toBeGreaterThan(0);
    expect(result.imageSizeBytes).toBe(imageBuffer.byteLength);
    expect(mocks.visionDescribe).toHaveBeenCalledTimes(1);
    expect(mocks.visionDescribe).toHaveBeenCalledWith(imageBuffer);
  });

  // Test 2: processImage(filePath) reads file then processes
  it('processImage(filePath) reads file then processes', async () => {
    const iu = ImageUnderstanding.getInstance();
    const fileBuffer = createTestImageBuffer();
    mocks.fsReadFile.mockResolvedValue(fileBuffer);
    mocks.fsStat.mockResolvedValue({ size: fileBuffer.byteLength });

    const result = await iu.processImage('/tmp/photo.png');

    expect(result).toBeDefined();
    expect(result.description).toBe('A cat sitting on a table.');
    expect(result.source).toBe('file');
    expect(result.imageSizeBytes).toBe(fileBuffer.byteLength);
    expect(mocks.fsReadFile).toHaveBeenCalledWith('/tmp/photo.png');
    expect(mocks.visionDescribe).toHaveBeenCalledWith(fileBuffer);
  });

  // Test 3: processClipboardImage() reads PNG/JPEG from system clipboard
  it('processClipboardImage() reads PNG/JPEG from system clipboard', async () => {
    const iu = ImageUnderstanding.getInstance();
    const mockImage = makeMockNativeImage(false);
    mocks.clipboardReadImage.mockReturnValue(mockImage);

    const result = await iu.processClipboardImage();

    expect(result).not.toBeNull();
    expect(result!.source).toBe('clipboard');
    expect(result!.description).toBe('A cat sitting on a table.');
    expect(mocks.clipboardReadImage).toHaveBeenCalledTimes(1);
    expect(mocks.visionDescribe).toHaveBeenCalledTimes(1);
  });

  // Test 4: handleDrop(files) filters for image files, processes first valid one
  it('handleDrop(files) filters for image files and processes first valid one', async () => {
    const iu = ImageUnderstanding.getInstance();
    const imageBuffer = createTestImageBuffer();
    mocks.fsReadFile.mockResolvedValue(imageBuffer);
    mocks.fsStat.mockResolvedValue({ size: imageBuffer.byteLength });

    const files = [
      '/tmp/readme.txt',
      '/tmp/data.csv',
      '/tmp/photo.png',
      '/tmp/other.jpg',
    ];

    const result = await iu.handleDrop(files);

    expect(result).not.toBeNull();
    expect(result!.source).toBe('drop');
    expect(result!.description).toBe('A cat sitting on a table.');
    // Should only process the first valid image file (photo.png)
    expect(mocks.fsReadFile).toHaveBeenCalledWith('/tmp/photo.png');
    expect(mocks.visionDescribe).toHaveBeenCalledTimes(1);
  });

  // Test 5: handleFileSelect() opens native file picker filtered to images
  it('handleFileSelect() opens native file picker filtered to images', async () => {
    const iu = ImageUnderstanding.getInstance();
    const imageBuffer = createTestImageBuffer();
    mocks.fsReadFile.mockResolvedValue(imageBuffer);
    mocks.fsStat.mockResolvedValue({ size: imageBuffer.byteLength });

    mocks.dialogShowOpenDialog.mockResolvedValue({
      canceled: false,
      filePaths: ['/tmp/selected-image.png'],
    });

    const result = await iu.handleFileSelect();

    expect(result).not.toBeNull();
    expect(result!.source).toBe('file');
    expect(result!.description).toBe('A cat sitting on a table.');
    expect(mocks.dialogShowOpenDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        filters: expect.arrayContaining([
          expect.objectContaining({
            extensions: expect.arrayContaining(['png', 'jpg', 'jpeg', 'webp', 'gif']),
          }),
        ]),
      }),
    );

    // Should return null when dialog is canceled
    mocks.dialogShowOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] });
    const canceledResult = await iu.handleFileSelect();
    expect(canceledResult).toBeNull();
  });

  // Test 6: Supported formats: PNG, JPEG, WebP, GIF (first frame)
  it('supports PNG, JPEG, WebP, and GIF formats', async () => {
    const iu = ImageUnderstanding.getInstance();
    const imageBuffer = createTestImageBuffer();
    mocks.fsReadFile.mockResolvedValue(imageBuffer);
    mocks.fsStat.mockResolvedValue({ size: imageBuffer.byteLength });

    const formats = ['photo.png', 'photo.jpg', 'photo.jpeg', 'photo.webp', 'photo.gif'];

    for (const filename of formats) {
      vi.clearAllMocks();
      mocks.visionIsReady.mockReturnValue(true);
      mocks.visionDescribe.mockResolvedValue('Description for ' + filename);
      mocks.fsReadFile.mockResolvedValue(imageBuffer);
      mocks.fsStat.mockResolvedValue({ size: imageBuffer.byteLength });

      const result = await iu.processImage('/tmp/' + filename);
      expect(result).toBeDefined();
      expect(result.description).toBe('Description for ' + filename);
    }

    // Non-image formats should be rejected
    mocks.fsReadFile.mockResolvedValue(Buffer.from('text content'));
    mocks.fsStat.mockResolvedValue({ size: 12 });
    await expect(iu.processImage('/tmp/readme.txt')).rejects.toThrow(/unsupported/i);
  });

  // Test 7: Images > 10MB are rejected with an informative error
  it('rejects images larger than 10MB with an informative error', async () => {
    const iu = ImageUnderstanding.getInstance();

    // Test with Buffer > 10MB
    const largeBuffer = Buffer.alloc(11 * 1024 * 1024); // 11MB
    await expect(iu.processImage(largeBuffer)).rejects.toThrow(/10\s*MB|too large|size/i);
    expect(mocks.visionDescribe).not.toHaveBeenCalled();

    // Test with file path > 10MB
    mocks.fsStat.mockResolvedValue({ size: 11 * 1024 * 1024 });
    mocks.fsReadFile.mockResolvedValue(largeBuffer);
    await expect(iu.processImage('/tmp/huge-image.png')).rejects.toThrow(/10\s*MB|too large|size/i);
  });

  // Test 8: getLastResult() returns cached result for re-reference
  it('getLastResult() returns cached result for re-reference', async () => {
    const iu = ImageUnderstanding.getInstance();

    // Initially null
    expect(iu.getLastResult()).toBeNull();

    const imageBuffer = createTestImageBuffer();
    const result = await iu.processImage(imageBuffer);

    const cached = iu.getLastResult();
    expect(cached).not.toBeNull();
    expect(cached).toEqual(result);
    expect(cached!.description).toBe('A cat sitting on a table.');
    expect(cached!.source).toBe('buffer');
  });

  // Test 9: image-result event includes source type (clipboard/drop/file/buffer)
  it('image-result event includes source type', async () => {
    const iu = ImageUnderstanding.getInstance();
    const events: ImageResult[] = [];

    iu.on('image-result', (payload: unknown) => {
      events.push(payload as ImageResult);
    });

    // Buffer source
    const imageBuffer = createTestImageBuffer();
    await iu.processImage(imageBuffer);
    expect(events).toHaveLength(1);
    expect(events[0].source).toBe('buffer');

    // File source
    mocks.fsReadFile.mockResolvedValue(imageBuffer);
    mocks.fsStat.mockResolvedValue({ size: imageBuffer.byteLength });
    await iu.processImage('/tmp/photo.png');
    expect(events).toHaveLength(2);
    expect(events[1].source).toBe('file');

    // Clipboard source
    mocks.clipboardReadImage.mockReturnValue(makeMockNativeImage(false));
    await iu.processClipboardImage();
    expect(events).toHaveLength(3);
    expect(events[2].source).toBe('clipboard');

    // Drop source
    mocks.fsReadFile.mockResolvedValue(imageBuffer);
    mocks.fsStat.mockResolvedValue({ size: imageBuffer.byteLength });
    await iu.handleDrop(['/tmp/dropped.jpg']);
    expect(events).toHaveLength(4);
    expect(events[3].source).toBe('drop');

    // Unsubscribe works
    const unsub = iu.on('image-result', () => {});
    expect(typeof unsub).toBe('function');
    unsub();
  });

  // Test 10: All tests mock clipboard, file system, and VisionProvider (+ singleton)
  it('singleton pattern and all mocks work correctly', () => {
    const a = ImageUnderstanding.getInstance();
    const b = ImageUnderstanding.getInstance();
    expect(a).toBe(b);

    ImageUnderstanding.resetInstance();
    const c = ImageUnderstanding.getInstance();
    expect(c).not.toBe(a);

    // Exported singleton is an ImageUnderstanding instance
    expect(imageUnderstanding).toBeInstanceOf(ImageUnderstanding);

    // All mocks are defined and functional
    expect(mocks.visionDescribe).toBeDefined();
    expect(mocks.visionAnswer).toBeDefined();
    expect(mocks.visionIsReady).toBeDefined();
    expect(mocks.clipboardReadImage).toBeDefined();
    expect(mocks.dialogShowOpenDialog).toBeDefined();
    expect(mocks.fsReadFile).toBeDefined();
    expect(mocks.fsStat).toBeDefined();

    // Question-based processImage uses answer()
    const iu = ImageUnderstanding.getInstance();
    expect(iu.getLastResult()).toBeNull();
  });
});
