/**
 * EmbeddingPipeline — Unit tests for local embedding generation via Ollama.
 *
 * Tests singleton lifecycle, embedding generation, batch processing,
 * cosine similarity, determinism, semantic distance, and graceful degradation.
 *
 * All HTTP calls are mocked — no real Ollama dependency.
 *
 * Sprint 3 G.2: "The Inner Voice" — EmbeddingPipeline
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ── Mock fetch globally ──────────────────────────────────────────────

let mockFetch: ReturnType<typeof vi.fn>;

function setupFetch() {
  mockFetch = vi.fn();
  vi.stubGlobal('fetch', mockFetch);
}

// ── Mock embedding vectors ───────────────────────────────────────────
//
// We create deterministic mock vectors so that:
// - Same input always returns the same vector (determinism test)
// - "cat" and "kitten" return similar vectors (similarity > 0.7)
// - "cat" and "quantum physics" return distant vectors (similarity < 0.3)

/** A 768-dim vector pointing mostly along the first axis (animal-like) */
function animalVector(): number[] {
  const v = new Array(768).fill(0);
  v[0] = 0.9; v[1] = 0.3; v[2] = 0.2;
  return v;
}

/** A 768-dim vector close to animalVector (kitten ~ cat) */
function kittenVector(): number[] {
  const v = new Array(768).fill(0);
  v[0] = 0.85; v[1] = 0.35; v[2] = 0.25;
  return v;
}

/** A 768-dim vector orthogonal to animal vectors (quantum physics) */
function physicsVector(): number[] {
  const v = new Array(768).fill(0);
  v[400] = 0.9; v[401] = 0.3; v[402] = 0.2;
  return v;
}

/** Map input text to a deterministic mock vector */
function mockEmbedding(text: string): number[] {
  const lower = text.toLowerCase().trim();
  if (lower.includes('kitten') || lower.includes('puppy')) return kittenVector();
  if (lower.includes('quantum') || lower.includes('physics')) return physicsVector();
  // Default: animal-like (for "cat", "dog", etc.)
  return animalVector();
}

/**
 * Configure mockFetch to respond to Ollama /api/embed requests.
 * Returns deterministic embeddings based on input text.
 */
function mockOllamaEmbed() {
  mockFetch.mockImplementation(async (url: string, options?: RequestInit) => {
    const urlStr = typeof url === 'string' ? url : '';

    // Health check: /api/tags
    if (urlStr.includes('/api/tags')) {
      return {
        ok: true,
        json: async () => ({
          models: [
            { name: 'nomic-embed-text', model: 'nomic-embed-text', size: 275_000_000 },
          ],
        }),
      };
    }

    // Embed endpoint: /api/embed
    if (urlStr.includes('/api/embed')) {
      const body = JSON.parse(options?.body as string || '{}');
      const input = body.input;

      if (Array.isArray(input)) {
        // Batch: input is string[]
        const embeddings = input.map((text: string) => mockEmbedding(text));
        return {
          ok: true,
          json: async () => ({ embeddings }),
        };
      } else {
        // Single: input is string
        const embedding = mockEmbedding(input || '');
        return {
          ok: true,
          json: async () => ({ embeddings: [embedding] }),
        };
      }
    }

    // Fallback: 404
    return { ok: false, status: 404, text: async () => 'Not found' };
  });
}

/**
 * Configure mockFetch to simulate Ollama being unavailable.
 */
function mockOllamaDown() {
  mockFetch.mockImplementation(async () => {
    throw new Error('ECONNREFUSED');
  });
}

// ── Import after mocks ───────────────────────────────────────────────

import {
  embeddingPipeline,
  EmbeddingPipeline,
} from '../../src/main/embedding-pipeline';

// ── Tests ────────────────────────────────────────────────────────────

describe('EmbeddingPipeline — singleton lifecycle', () => {
  beforeEach(() => {
    setupFetch();
    embeddingPipeline.stop(); // Reset state between tests
  });

  afterEach(() => {
    embeddingPipeline.stop();
    vi.restoreAllMocks();
  });

  // Test 1: Singleton with start()/stop() lifecycle
  it('is a singleton with start() and stop() lifecycle methods', () => {
    expect(embeddingPipeline).toBeInstanceOf(EmbeddingPipeline);
    expect(typeof embeddingPipeline.start).toBe('function');
    expect(typeof embeddingPipeline.stop).toBe('function');
    expect(typeof embeddingPipeline.isReady).toBe('function');
  });

  // Test 2: isReady() returns false before start, true after successful start
  it('isReady() returns false before start, true after successful start', async () => {
    expect(embeddingPipeline.isReady()).toBe(false);

    mockOllamaEmbed();
    await embeddingPipeline.start();

    expect(embeddingPipeline.isReady()).toBe(true);
  });
});

describe('EmbeddingPipeline — embedding generation', () => {
  beforeEach(async () => {
    setupFetch();
    mockOllamaEmbed();
    await embeddingPipeline.start();
  });

  afterEach(() => {
    embeddingPipeline.stop();
    vi.restoreAllMocks();
  });

  // Test 3: embed(text) returns a number[] of fixed dimension
  it('embed(text) returns a number[] of fixed dimension', async () => {
    const vector = await embeddingPipeline.embed('cat');

    expect(vector).toBeDefined();
    expect(Array.isArray(vector)).toBe(true);
    expect(vector!.length).toBe(768);
    // All elements should be numbers
    expect(vector!.every((v) => typeof v === 'number')).toBe(true);
  });

  // Test 4: embedBatch(texts) returns array of vectors, same length as input
  it('embedBatch(texts) returns array of vectors matching input length', async () => {
    const texts = ['cat', 'dog', 'quantum physics'];
    const vectors = await embeddingPipeline.embedBatch(texts);

    expect(vectors).toBeDefined();
    expect(vectors!.length).toBe(texts.length);
    for (const vec of vectors!) {
      expect(Array.isArray(vec)).toBe(true);
      expect(vec.length).toBe(768);
    }
  });

  // Test 5: similarity(vecA, vecB) computes cosine similarity in range [-1, 1]
  it('similarity() computes cosine similarity in range [-1, 1]', () => {
    const vecA = [1, 0, 0];
    const vecB = [0, 1, 0];

    const sim = EmbeddingPipeline.similarity(vecA, vecB);

    expect(typeof sim).toBe('number');
    expect(sim).toBeGreaterThanOrEqual(-1);
    expect(sim).toBeLessThanOrEqual(1);
    // Orthogonal vectors should have similarity ~0
    expect(sim).toBeCloseTo(0, 5);

    // Identical vectors should have similarity 1
    const simSame = EmbeddingPipeline.similarity(vecA, vecA);
    expect(simSame).toBeCloseTo(1, 5);

    // Opposite vectors should have similarity -1
    const vecC = [-1, 0, 0];
    const simOpposite = EmbeddingPipeline.similarity(vecA, vecC);
    expect(simOpposite).toBeCloseTo(-1, 5);
  });

  // Test 6: Identical texts produce identical embeddings (deterministic)
  it('identical texts produce identical embeddings (deterministic)', async () => {
    const text = 'the quick brown fox';
    const vec1 = await embeddingPipeline.embed(text);
    const vec2 = await embeddingPipeline.embed(text);

    expect(vec1).toBeDefined();
    expect(vec2).toBeDefined();
    expect(vec1).toEqual(vec2);
  });

  // Test 7: Semantically similar texts produce similarity > 0.7
  it('semantically similar texts produce similarity > 0.7', async () => {
    const vecCat = await embeddingPipeline.embed('cat');
    const vecKitten = await embeddingPipeline.embed('kitten');

    expect(vecCat).toBeDefined();
    expect(vecKitten).toBeDefined();

    const sim = EmbeddingPipeline.similarity(vecCat!, vecKitten!);
    expect(sim).toBeGreaterThan(0.7);
  });

  // Test 8: Semantically unrelated texts produce similarity < 0.3
  it('semantically unrelated texts produce similarity < 0.3', async () => {
    const vecCat = await embeddingPipeline.embed('cat');
    const vecPhysics = await embeddingPipeline.embed('quantum physics');

    expect(vecCat).toBeDefined();
    expect(vecPhysics).toBeDefined();

    const sim = EmbeddingPipeline.similarity(vecCat!, vecPhysics!);
    expect(sim).toBeLessThan(0.3);
  });
});

describe('EmbeddingPipeline — error handling', () => {
  beforeEach(() => {
    setupFetch();
    embeddingPipeline.stop();
  });

  afterEach(() => {
    embeddingPipeline.stop();
    vi.restoreAllMocks();
  });

  // Test 9: embed() returns null when pipeline is not ready
  it('embed() returns null when pipeline is not ready', async () => {
    // Pipeline has not been started
    expect(embeddingPipeline.isReady()).toBe(false);

    const result = await embeddingPipeline.embed('test');
    expect(result).toBeNull();
  });

  // Test 10: Pipeline gracefully degrades when Ollama is unavailable
  it('gracefully degrades when Ollama is unavailable (isReady -> false, no crash)', async () => {
    mockOllamaDown();

    // start() should not throw
    await expect(embeddingPipeline.start()).resolves.not.toThrow();

    // Should report not ready
    expect(embeddingPipeline.isReady()).toBe(false);

    // embed should return null, not throw
    const result = await embeddingPipeline.embed('test');
    expect(result).toBeNull();
  });
});
