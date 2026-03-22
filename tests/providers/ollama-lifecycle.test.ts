/**
 * OllamaLifecycle -- Unit tests for health monitoring and model awareness.
 *
 * Tests singleton lifecycle, health checks, model listing, event emission,
 * pullModel streaming, and graceful degradation when Ollama is unreachable.
 *
 * All HTTP calls are mocked -- no real Ollama dependency.
 *
 * Sprint 3 G.3: "The Caretaker" -- OllamaLifecycle
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { OllamaLifecycle } from '../../src/main/ollama-lifecycle';
import type {
  HealthStatus,
  OllamaModelInfo,
  LoadedModelInfo,
  PullProgress,
  OllamaLifecycleEvent,
  LifecycleCallback,
} from '../../src/main/ollama-lifecycle';

// -- Mock fetch globally ------------------------------------------------------

let mockFetch: ReturnType<typeof vi.fn>;

function setupFetch() {
  mockFetch = vi.fn();
  vi.stubGlobal('fetch', mockFetch);
}

// -- Mock response helpers ----------------------------------------------------

const MOCK_TAGS_RESPONSE = {
  models: [
    {
      name: 'llama3.2:latest',
      model: 'llama3.2:latest',
      size: 2_000_000_000,
      digest: 'abc123',
      modified_at: '2024-01-15T10:00:00Z',
    },
    {
      name: 'nomic-embed-text:latest',
      model: 'nomic-embed-text:latest',
      size: 275_000_000,
      digest: 'def456',
      modified_at: '2024-01-15T09:00:00Z',
    },
  ],
};

const MOCK_PS_RESPONSE = {
  models: [
    {
      name: 'llama3.2:latest',
      model: 'llama3.2:latest',
      size: 2_000_000_000,
      size_vram: 1_800_000_000,
      expires_at: '2024-01-15T10:05:00Z',
    },
  ],
};

function mockOllamaOnline() {
  mockFetch.mockImplementation(async (url: string) => {
    const urlStr = typeof url === 'string' ? url : '';

    if (urlStr.includes('/api/tags')) {
      return {
        ok: true,
        json: async () => MOCK_TAGS_RESPONSE,
      };
    }

    if (urlStr.includes('/api/ps')) {
      return {
        ok: true,
        json: async () => MOCK_PS_RESPONSE,
      };
    }

    return { ok: false, status: 404 };
  });
}

function mockOllamaOffline() {
  mockFetch.mockImplementation(async () => {
    throw new Error('Connection refused');
  });
}

// -- Test Suite ----------------------------------------------------------------

describe('OllamaLifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setupFetch();
    OllamaLifecycle.resetInstance();
  });

  afterEach(() => {
    OllamaLifecycle.resetInstance();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  // Test 1: Singleton with start()/stop() lifecycle
  it('is a singleton with start()/stop() lifecycle', async () => {
    const a = OllamaLifecycle.getInstance();
    const b = OllamaLifecycle.getInstance();
    expect(a).toBe(b);

    mockOllamaOnline();
    await a.start();
    expect(a.getHealth().running).toBe(true);

    a.stop();
    expect(a.getHealth().running).toBe(false);
  });

  // Test 2: getHealth() returns running: true when Ollama responds
  it('getHealth() returns running: true when Ollama responds to /api/tags', async () => {
    mockOllamaOnline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const health = lifecycle.getHealth();
    expect(health.running).toBe(true);
    expect(health.modelsLoaded).toBe(1); // one model in /api/ps
    expect(health.vramUsed).toBe(1_800_000_000);
    expect(health.vramTotal).toBe(0);

    lifecycle.stop();
  });

  // Test 3: getHealth() returns running: false when Ollama is unreachable
  it('getHealth() returns running: false when Ollama is unreachable', async () => {
    mockOllamaOffline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const health = lifecycle.getHealth();
    expect(health.running).toBe(false);
    expect(health.modelsLoaded).toBe(0);
    expect(health.vramUsed).toBe(0);

    lifecycle.stop();
  });

  // Test 4: getAvailableModels() returns parsed model list from /api/tags
  it('getAvailableModels() returns parsed model list from /api/tags', async () => {
    mockOllamaOnline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const models = lifecycle.getAvailableModels();
    expect(models).toHaveLength(2);
    expect(models[0].name).toBe('llama3.2:latest');
    expect(models[1].name).toBe('nomic-embed-text:latest');
    expect(models[0].size).toBe(2_000_000_000);
    expect(models[0].digest).toBe('abc123');

    lifecycle.stop();
  });

  // Test 5: getLoadedModels() returns currently loaded models from /api/ps
  it('getLoadedModels() returns currently loaded models from /api/ps', async () => {
    mockOllamaOnline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const loaded = lifecycle.getLoadedModels();
    expect(loaded).toHaveLength(1);
    expect(loaded[0].name).toBe('llama3.2:latest');
    expect(loaded[0].sizeVram).toBe(1_800_000_000);

    lifecycle.stop();
  });

  // Test 6: Health polling emits health-change event when Ollama comes online
  it('emits health-change event when Ollama comes online', async () => {
    mockOllamaOffline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const events: OllamaLifecycleEvent[] = [];
    lifecycle.on('health-change', (event) => {
      events.push(event);
    });
    lifecycle.on('healthy', (event) => {
      events.push(event);
    });

    // Now Ollama comes online
    mockOllamaOnline();
    await vi.advanceTimersByTimeAsync(30_000);

    expect(events).toContain('health-change');
    expect(events).toContain('healthy');

    lifecycle.stop();
  });

  // Test 7: Health polling emits health-change event when Ollama goes offline
  it('emits health-change event when Ollama goes offline', async () => {
    mockOllamaOnline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const events: OllamaLifecycleEvent[] = [];
    lifecycle.on('health-change', (event) => {
      events.push(event);
    });
    lifecycle.on('unhealthy', (event) => {
      events.push(event);
    });

    // Now Ollama goes offline
    mockOllamaOffline();
    await vi.advanceTimersByTimeAsync(30_000);

    expect(events).toContain('health-change');
    expect(events).toContain('unhealthy');

    lifecycle.stop();
  });

  // Test 8: pullModel(name) streams progress events from /api/pull
  it('pullModel(name) streams progress events from /api/pull', async () => {
    const pullLines = [
      JSON.stringify({ status: 'pulling manifest' }),
      JSON.stringify({ status: 'downloading', digest: 'sha256:abc', total: 1000, completed: 500 }),
      JSON.stringify({ status: 'success' }),
    ].join('\n') + '\n';

    const encoder = new TextEncoder();
    const chunks = [encoder.encode(pullLines)];
    let chunkIndex = 0;

    mockFetch.mockImplementation(async (url: string) => {
      const urlStr = typeof url === 'string' ? url : '';

      if (urlStr.includes('/api/pull')) {
        return {
          ok: true,
          body: {
            getReader: () => ({
              read: async () => {
                if (chunkIndex < chunks.length) {
                  return { done: false, value: chunks[chunkIndex++] };
                }
                return { done: true, value: undefined };
              },
              releaseLock: () => {},
            }),
          },
        };
      }

      if (urlStr.includes('/api/tags')) {
        return { ok: true, json: async () => MOCK_TAGS_RESPONSE };
      }
      if (urlStr.includes('/api/ps')) {
        return { ok: true, json: async () => MOCK_PS_RESPONSE };
      }
      return { ok: false, status: 404 };
    });

    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const progress: PullProgress[] = [];
    for await (const p of lifecycle.pullModel('llama3.2')) {
      progress.push(p);
    }

    expect(progress).toHaveLength(3);
    expect(progress[0].status).toBe('pulling manifest');
    expect(progress[1].status).toBe('downloading');
    expect(progress[1].completed).toBe(500);
    expect(progress[2].status).toBe('success');

    lifecycle.stop();
  });

  // Test 9: isModelAvailable(name) returns true/false from cached model list
  it('isModelAvailable(name) returns true/false from cached model list', async () => {
    mockOllamaOnline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    expect(lifecycle.isModelAvailable('llama3.2:latest')).toBe(true);
    expect(lifecycle.isModelAvailable('llama3.2')).toBe(true); // prefix match
    expect(lifecycle.isModelAvailable('nomic-embed-text:latest')).toBe(true);
    expect(lifecycle.isModelAvailable('nonexistent-model')).toBe(false);

    lifecycle.stop();
  });

  // Test 10: stop() clears polling interval and removes listeners
  it('stop() clears polling interval and removes listeners', async () => {
    mockOllamaOnline();
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();

    const events: string[] = [];
    lifecycle.on('health-change', () => {
      events.push('change');
    });

    // Stop should clear everything
    lifecycle.stop();

    expect(lifecycle.getHealth().running).toBe(false);
    expect(lifecycle.getAvailableModels()).toHaveLength(0);
    expect(lifecycle.getLoadedModels()).toHaveLength(0);

    // After stop, advancing timers should not trigger more polls
    mockOllamaOnline();
    await vi.advanceTimersByTimeAsync(60_000);

    // No events should have been emitted after stop
    expect(events).toHaveLength(0);
  });
});
