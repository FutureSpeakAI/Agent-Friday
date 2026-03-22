/**
 * VisionProvider -- Unit tests for local vision-language model via Ollama.
 *
 * Tests singleton lifecycle, model loading, image description, visual
 * question answering, readiness checks, image input formats, VRAM
 * tracking, resource cleanup, and malformed image handling.
 *
 * All Ollama interactions are mocked -- no model required in CI.
 *
 * Sprint 5 M.1: "The Gaze" -- VisionProvider
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  fsReadFile: vi.fn(),
}));

vi.stubGlobal('fetch', mocks.fetchMock);

vi.mock('node:fs/promises', () => ({
  readFile: (...args: unknown[]) => mocks.fsReadFile(...args),
}));

import {
  VisionProvider,
  visionProvider,
} from '../../../src/main/vision/vision-provider';
import type {
  ImageInput,
  VisionModelInfo,
} from '../../../src/main/vision/vision-provider';

// -- Helper: create mock fetch responses ------------------------------------

function mockFetchResponse(body: Record<string, unknown>, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Not Found',
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ 'Content-Type': 'application/json' }),
  } as unknown as Response;
}

/** A small 1x1 red PNG as raw bytes (Buffer) */
function createTestImageBuffer(): Buffer {
  const pngHex =
    '89504e470d0a1a0a0000000d49484452000000010000000108020000009001' +
    '2e00000000c4944415408d76360f8cf00000000020001e221bc3300000000' +
    '49454e44ae426082';
  return Buffer.from(pngHex, 'hex');
}

/** Base64-encoded version of the test image */
function createTestImageBase64(): string {
  return createTestImageBuffer().toString('base64');
}

// -- Mock setup helpers ------------------------------------------------------

function mockOllamaShowSuccess(): void {
  mocks.fetchMock.mockImplementation(async (url: string) => {
    if (url.includes('/api/show')) {
      return mockFetchResponse({
        name: 'moondream:latest',
        model_info: { 'general.parameter_count': 1_800_000_000 },
        size: 1_200_000_000,
      });
    }
    if (url.includes('/api/ps')) {
      return mockFetchResponse({
        models: [
          {
            name: 'moondream:latest',
            size: 1_200_000_000,
            size_vram: 1_258_291_200,
          },
        ],
      });
    }
    if (url.includes('/api/generate')) {
      return mockFetchResponse({
        model: 'moondream:latest',
        response: 'Model loaded successfully.',
        done: true,
      });
    }
    return mockFetchResponse({ error: 'unknown endpoint' }, 404);
  });
}

function mockOllamaShowNotFound(): void {
  mocks.fetchMock.mockImplementation(async (url: string) => {
    if (url.includes('/api/show')) {
      return mockFetchResponse(
        { error: "model not found in Ollama" },
        404,
      );
    }
    return mockFetchResponse({ error: 'unknown endpoint' }, 404);
  });
}

function mockOllamaDescribeResponse(description: string): void {
  mocks.fetchMock.mockImplementation(async (url: string) => {
    if (url.includes('/api/show')) {
      return mockFetchResponse({
        name: 'moondream:latest',
        model_info: { 'general.parameter_count': 1_800_000_000 },
        size: 1_200_000_000,
      });
    }
    if (url.includes('/api/ps')) {
      return mockFetchResponse({
        models: [
          {
            name: 'moondream:latest',
            size: 1_200_000_000,
            size_vram: 1_258_291_200,
          },
        ],
      });
    }
    if (url.includes('/api/generate')) {
      return mockFetchResponse({
        model: 'moondream:latest',
        response: description,
        done: true,
      });
    }
    return mockFetchResponse({ error: 'unknown endpoint' }, 404);
  });
}

function mockOllamaAnswerResponse(answer: string): void {
  mocks.fetchMock.mockImplementation(async (url: string) => {
    if (url.includes('/api/show')) {
      return mockFetchResponse({
        name: 'moondream:latest',
        model_info: { 'general.parameter_count': 1_800_000_000 },
        size: 1_200_000_000,
      });
    }
    if (url.includes('/api/ps')) {
      return mockFetchResponse({
        models: [
          {
            name: 'moondream:latest',
            size: 1_200_000_000,
            size_vram: 1_258_291_200,
          },
        ],
      });
    }
    if (url.includes('/api/generate')) {
      return mockFetchResponse({
        model: 'moondream:latest',
        response: answer,
        done: true,
      });
    }
    return mockFetchResponse({ error: 'unknown endpoint' }, 404);
  });
}

function mockOllamaMalformedImageResponse(): void {
  mocks.fetchMock.mockImplementation(async (url: string) => {
    if (url.includes('/api/show')) {
      return mockFetchResponse({
        name: 'moondream:latest',
        model_info: { 'general.parameter_count': 1_800_000_000 },
        size: 1_200_000_000,
      });
    }
    if (url.includes('/api/ps')) {
      return mockFetchResponse({
        models: [
          {
            name: 'moondream:latest',
            size: 1_200_000_000,
            size_vram: 1_258_291_200,
          },
        ],
      });
    }
    if (url.includes('/api/generate')) {
      return mockFetchResponse(
        { error: 'invalid image data' },
        400,
      );
    }
    return mockFetchResponse({ error: 'unknown endpoint' }, 404);
  });
}

// -- Test Suite ---------------------------------------------------------------

describe('VisionProvider', () => {
  beforeEach(() => {
    VisionProvider.resetInstance();
    vi.clearAllMocks();
  });

  afterEach(() => {
    VisionProvider.resetInstance();
    vi.restoreAllMocks();
  });

  // Test 1: loadModel() succeeds when Moondream available in Ollama
  it('loadModel() succeeds when Moondream available in Ollama', async () => {
    mockOllamaShowSuccess();
    const provider = VisionProvider.getInstance();

    await provider.loadModel();

    expect(provider.isReady()).toBe(true);
    expect(mocks.fetchMock).toHaveBeenCalled();

    // Verify /api/show was called with moondream:latest
    const showCall = mocks.fetchMock.mock.calls.find(
      (call: unknown[]) => (call[0] as string).includes('/api/show'),
    );
    expect(showCall).toBeDefined();
    const showBody = JSON.parse((showCall![1] as RequestInit).body as string);
    expect(showBody.name).toBe('moondream:latest');
  });

  // Test 2: loadModel() returns graceful error when model missing
  it('loadModel() returns graceful error when model missing', async () => {
    mockOllamaShowNotFound();
    const provider = VisionProvider.getInstance();

    await expect(provider.loadModel()).rejects.toThrow(/not found/i);
    expect(provider.isReady()).toBe(false);
  });

  // Test 3: describe(image) returns text description for valid image
  it('describe(image) returns text description for valid image', async () => {
    const expectedDescription =
      'A fluffy orange cat sitting on a wooden table near a window.';
    mockOllamaDescribeResponse(expectedDescription);

    const provider = VisionProvider.getInstance();
    await provider.loadModel();

    const description = await provider.describe(createTestImageBuffer());

    expect(description).toBe(expectedDescription);
    expect(description.length).toBeGreaterThan(0);

    // Verify /api/generate was called with images array
    const generateCalls = mocks.fetchMock.mock.calls.filter(
      (call: unknown[]) => (call[0] as string).includes('/api/generate'),
    );
    expect(generateCalls.length).toBeGreaterThanOrEqual(1);

    const lastGenerateCall = generateCalls[generateCalls.length - 1];
    const generateBody = JSON.parse(
      (lastGenerateCall[1] as RequestInit).body as string,
    );
    expect(generateBody.model).toBe('moondream:latest');
    expect(generateBody.images).toBeDefined();
    expect(generateBody.images).toHaveLength(1);
    expect(generateBody.stream).toBe(false);
  });

  // Test 4: answer(image, question) returns answer to visual question
  it('answer(image, question) returns answer to visual question', async () => {
    const question = 'What color is the cat?';
    const expectedAnswer = 'The cat is orange.';
    mockOllamaAnswerResponse(expectedAnswer);

    const provider = VisionProvider.getInstance();
    await provider.loadModel();

    const answer = await provider.answer(createTestImageBuffer(), question);

    expect(answer).toBe(expectedAnswer);

    // Verify the question was sent as the prompt
    const generateCalls = mocks.fetchMock.mock.calls.filter(
      (call: unknown[]) => (call[0] as string).includes('/api/generate'),
    );
    const lastGenerateCall = generateCalls[generateCalls.length - 1];
    const generateBody = JSON.parse(
      (lastGenerateCall[1] as RequestInit).body as string,
    );
    expect(generateBody.prompt).toBe(question);
    expect(generateBody.images).toHaveLength(1);
  });

  // Test 5: isReady() false before load, true after
  it('isReady() returns false before loadModel, true after', async () => {
    const provider = VisionProvider.getInstance();

    expect(provider.isReady()).toBe(false);

    mockOllamaShowSuccess();
    await provider.loadModel();

    expect(provider.isReady()).toBe(true);
  });

  // Test 6: Image input accepts Buffer and file paths (string)
  it('image input accepts Buffer, base64 string, and file paths', async () => {
    const expectedDescription = 'A test image showing a red pixel.';
    mockOllamaDescribeResponse(expectedDescription);

    const provider = VisionProvider.getInstance();
    await provider.loadModel();

    // Test with Buffer input
    const bufferResult = await provider.describe(createTestImageBuffer());
    expect(bufferResult).toBe(expectedDescription);

    // Test with base64 string input
    const base64Result = await provider.describe(createTestImageBase64());
    expect(base64Result).toBe(expectedDescription);

    // Test with file path input (mocked fs.readFile)
    mocks.fsReadFile.mockResolvedValue(createTestImageBuffer());
    const filePathResult = await provider.describe('/tmp/test-image.png');
    expect(filePathResult).toBe(expectedDescription);
    expect(mocks.fsReadFile).toHaveBeenCalledWith('/tmp/test-image.png');

    // Test with Windows-style file path
    mocks.fsReadFile.mockResolvedValue(createTestImageBuffer());
    const winPathResult = await provider.describe(
      'C:\\Users\\test\\image.png',
    );
    expect(winPathResult).toBe(expectedDescription);
    expect(mocks.fsReadFile).toHaveBeenCalledWith('C:\\Users\\test\\image.png');
  });

  // Test 7: unloadModel() frees VRAM, isReady() returns false
  it('unloadModel() frees VRAM and isReady() returns false', async () => {
    mockOllamaShowSuccess();
    const provider = VisionProvider.getInstance();
    await provider.loadModel();

    expect(provider.isReady()).toBe(true);

    provider.unloadModel();

    expect(provider.isReady()).toBe(false);

    // After unload, getModelInfo should show loaded: false
    const info = provider.getModelInfo();
    expect(info.loaded).toBe(false);
  });

  // Test 8: getModelInfo() reports VRAM usage (~1.2GB for Moondream Q4)
  it('getModelInfo() reports VRAM usage for loaded model', async () => {
    mockOllamaShowSuccess();
    const provider = VisionProvider.getInstance();
    await provider.loadModel();

    const info = provider.getModelInfo();

    expect(info.name).toBe('moondream:latest');
    expect(info.loaded).toBe(true);
    // VRAM should be around 1200 MB (1258291200 bytes / 1024 / 1024 ~ 1200)
    expect(info.vramUsageMB).toBeGreaterThan(1000);
    expect(info.vramUsageMB).toBeLessThan(1500);
  });

  // Test 9: Provider handles malformed/corrupt images gracefully
  it('handles malformed/corrupt images gracefully', async () => {
    // Load model first with success mock
    mockOllamaShowSuccess();
    const provider = VisionProvider.getInstance();
    await provider.loadModel();

    // Now switch to malformed image response mock
    mockOllamaMalformedImageResponse();

    const corruptBuffer = Buffer.from('not-a-valid-image-at-all');

    await expect(provider.describe(corruptBuffer)).rejects.toThrow(
      /invalid image|error/i,
    );

    // Provider should still be ready (model not crashed)
    expect(provider.isReady()).toBe(true);
  });

  // Test 10: Singleton pattern + all tests mock Ollama (no model required in CI)
  it('singleton pattern works correctly (getInstance + resetInstance)', () => {
    const a = VisionProvider.getInstance();
    const b = VisionProvider.getInstance();
    expect(a).toBe(b);

    VisionProvider.resetInstance();

    const c = VisionProvider.getInstance();
    expect(c).not.toBe(a);

    // Exported singleton is a VisionProvider instance
    expect(visionProvider).toBeInstanceOf(VisionProvider);
  });
});
