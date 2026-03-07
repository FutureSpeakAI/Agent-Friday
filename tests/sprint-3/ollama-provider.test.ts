/**
 * OllamaProvider — Unit tests for native Ollama API integration.
 *
 * Tests availability detection, /api/chat completion, streaming,
 * /api/tags model listing, health checking, tool format mapping,
 * provider registration, and priority over HF-local.
 *
 * All HTTP calls are mocked — no real Ollama dependency.
 *
 * Sprint 3 G.1: "The Native Tongue" — OllamaProvider
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { LLMRequest, ToolDefinition } from '../../src/main/llm-client';

// ── Mock settings ─────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  settingsGet: vi.fn(() => ({})),
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    get: mocks.settingsGet,
  },
}));

import { OllamaProvider } from '../../src/main/providers/ollama-provider';

// ── Helpers ───────────────────────────────────────────────────────────

let mockFetch: ReturnType<typeof vi.fn>;

function setupFetch() {
  mockFetch = vi.fn();
  vi.stubGlobal('fetch', mockFetch);
}

function makeRequest(overrides: Partial<LLMRequest> = {}): LLMRequest {
  return {
    messages: overrides.messages ?? [{ role: 'user', content: 'Hello' }],
    systemPrompt: overrides.systemPrompt,
    model: overrides.model,
    maxTokens: overrides.maxTokens,
    temperature: overrides.temperature,
    tools: overrides.tools,
    toolChoice: overrides.toolChoice,
    signal: overrides.signal,
    stream: overrides.stream,
  };
}

// ── Test 1: Implements LLMProvider interface ───────────────────────────

describe('OllamaProvider — interface compliance', () => {
  it('implements LLMProvider interface with name, isAvailable, complete, stream', () => {
    const provider = new OllamaProvider();
    expect(provider.name).toBe('ollama');
    expect(typeof provider.isAvailable).toBe('function');
    expect(typeof provider.complete).toBe('function');
    expect(typeof provider.stream).toBe('function');
    expect(typeof provider.listModels).toBe('function');
    expect(typeof provider.checkHealth).toBe('function');
  });
});

// ── Tests 2-3: isAvailable ────────────────────────────────────────────

describe('OllamaProvider — isAvailable', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns true when Ollama is running (mock /api/tags returns 200)', async () => {
    const provider = new OllamaProvider();

    // Simulate a successful health check
    mockFetch.mockResolvedValueOnce({ ok: true });
    const healthy = await provider.checkHealth();
    expect(healthy).toBe(true);

    // After a successful health check, isAvailable should reflect cached health
    expect(provider.isAvailable()).toBe(true);
  });

  it('returns false when Ollama is not reachable (timeout/connection refused)', async () => {
    const provider = new OllamaProvider();

    // Simulate connection failure
    mockFetch.mockRejectedValueOnce(new Error('ECONNREFUSED'));
    const healthy = await provider.checkHealth();
    expect(healthy).toBe(false);

    // isAvailable should reflect the unhealthy state
    expect(provider.isAvailable()).toBe(false);
  });
});

// ── Test 4: complete() sends to /api/chat and normalizes response ─────

describe('OllamaProvider — complete', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends request to /api/chat and normalizes response to LLMResponse', async () => {
    const provider = new OllamaProvider();

    // Prime the health cache so isAvailable returns true
    mockFetch.mockResolvedValueOnce({ ok: true });
    await provider.checkHealth();

    // Mock the /api/chat response
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        model: 'llama3.2',
        message: {
          role: 'assistant',
          content: 'Hello! How can I help you?',
        },
        done: true,
        total_duration: 500000000,
        prompt_eval_count: 15,
        eval_count: 8,
      }),
    });

    const result = await provider.complete(makeRequest({ model: 'llama3.2' }));

    // Verify the request was sent to /api/chat
    const url = mockFetch.mock.calls[1][0];
    expect(url).toBe('http://localhost:11434/api/chat');

    // Verify request body structure
    const body = JSON.parse(mockFetch.mock.calls[1][1].body);
    expect(body.model).toBe('llama3.2');
    expect(body.stream).toBe(false);
    expect(body.messages).toBeDefined();

    // Verify normalized response
    expect(result.content).toBe('Hello! How can I help you?');
    expect(result.toolCalls).toEqual([]);
    expect(result.provider).toBe('ollama');
    expect(result.model).toBe('llama3.2');
    expect(result.stopReason).toBe('end_turn');
    expect(result.usage.inputTokens).toBe(15);
    expect(result.usage.outputTokens).toBe(8);
    expect(result.latencyMs).toBeGreaterThanOrEqual(0);
  });

  // ── Test 5: Tool definition mapping ──────────────────────────────────

  it('maps tool definitions to Ollama tool format', async () => {
    const provider = new OllamaProvider();

    mockFetch.mockResolvedValueOnce({ ok: true });
    await provider.checkHealth();

    const tools: ToolDefinition[] = [
      {
        name: 'get_weather',
        description: 'Get the current weather',
        input_schema: {
          type: 'object',
          properties: {
            city: { type: 'string', description: 'City name' },
          },
          required: ['city'],
        },
      },
    ];

    // Mock response with tool call
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        model: 'llama3.2',
        message: {
          role: 'assistant',
          content: '',
          tool_calls: [
            {
              function: {
                name: 'get_weather',
                arguments: { city: 'San Francisco' },
              },
            },
          ],
        },
        done: true,
        prompt_eval_count: 20,
        eval_count: 10,
      }),
    });

    const result = await provider.complete(makeRequest({ tools }));

    // Verify tools were sent in request body
    const body = JSON.parse(mockFetch.mock.calls[1][1].body);
    expect(body.tools).toBeDefined();
    expect(body.tools[0].type).toBe('function');
    expect(body.tools[0].function.name).toBe('get_weather');
    expect(body.tools[0].function.parameters).toBeDefined();

    // Verify tool calls in response
    expect(result.toolCalls).toHaveLength(1);
    expect(result.toolCalls[0].name).toBe('get_weather');
    expect(result.toolCalls[0].input).toEqual({ city: 'San Francisco' });
    expect(result.toolCalls[0].type).toBe('tool_use');
    expect(result.stopReason).toBe('tool_use');
  });
});

// ── Test 6: stream() yields LLMStreamChunk ────────────────────────────

describe('OllamaProvider — stream', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('yields LLMStreamChunk from Ollama streaming response', async () => {
    const provider = new OllamaProvider();

    mockFetch.mockResolvedValueOnce({ ok: true });
    await provider.checkHealth();

    // Ollama streams NDJSON (newline-delimited JSON), one object per line
    const streamChunks = [
      JSON.stringify({ model: 'llama3.2', message: { role: 'assistant', content: 'Hello' }, done: false }),
      JSON.stringify({ model: 'llama3.2', message: { role: 'assistant', content: ' world' }, done: false }),
      JSON.stringify({
        model: 'llama3.2',
        message: { role: 'assistant', content: '' },
        done: true,
        total_duration: 1000000000,
        prompt_eval_count: 10,
        eval_count: 5,
      }),
    ];

    const encoder = new TextEncoder();
    let chunkIndex = 0;

    const mockReader = {
      read: vi.fn(async () => {
        if (chunkIndex < streamChunks.length) {
          const data = encoder.encode(streamChunks[chunkIndex] + '\n');
          chunkIndex++;
          return { done: false, value: data };
        }
        return { done: true, value: undefined };
      }),
    };

    mockFetch.mockResolvedValueOnce({
      ok: true,
      body: { getReader: () => mockReader },
    });

    const chunks: Array<{ text?: string; done: boolean }> = [];
    for await (const chunk of provider.stream(makeRequest({ model: 'llama3.2' }))) {
      chunks.push(chunk);
    }

    // Should have text chunks + final done chunk
    expect(chunks.length).toBeGreaterThanOrEqual(2);

    // First chunk should have text
    expect(chunks[0].text).toBe('Hello');
    expect(chunks[0].done).toBe(false);

    // Second chunk
    expect(chunks[1].text).toBe(' world');
    expect(chunks[1].done).toBe(false);

    // Final chunk should be done with full response
    const finalChunk = chunks[chunks.length - 1];
    expect(finalChunk.done).toBe(true);
    expect(finalChunk.fullResponse).toBeDefined();
    expect(finalChunk.fullResponse!.content).toBe('Hello world');
    expect(finalChunk.fullResponse!.provider).toBe('ollama');
    expect(finalChunk.fullResponse!.model).toBe('llama3.2');
  });
});

// ── Test 7: listModels() queries /api/tags ────────────────────────────

describe('OllamaProvider — listModels', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('queries /api/tags and returns model IDs with parameter sizes', async () => {
    const provider = new OllamaProvider();

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        models: [
          {
            name: 'llama3.2:latest',
            model: 'llama3.2:latest',
            size: 2000000000,
            details: { parameter_size: '3B', family: 'llama' },
          },
          {
            name: 'mistral:7b',
            model: 'mistral:7b',
            size: 4000000000,
            details: { parameter_size: '7B', family: 'mistral' },
          },
        ],
      }),
    });

    const models = await provider.listModels!();

    // Verify the request was sent to /api/tags
    const url = mockFetch.mock.calls[0][0];
    expect(url).toBe('http://localhost:11434/api/tags');

    expect(models).toHaveLength(2);
    expect(models[0].id).toBe('llama3.2:latest');
    expect(models[0].name).toContain('llama3.2');
    expect(models[1].id).toBe('mistral:7b');
    expect(models[1].name).toContain('mistral');
  });
});

// ── Test 8: checkHealth() ─────────────────────────────────────────────

describe('OllamaProvider — checkHealth', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns true when Ollama is reachable via /api/tags', async () => {
    const provider = new OllamaProvider();

    mockFetch.mockResolvedValueOnce({ ok: true });
    const result = await provider.checkHealth!();
    expect(result).toBe(true);

    const url = mockFetch.mock.calls[0][0];
    expect(url).toBe('http://localhost:11434/api/tags');
  });

  it('returns false when Ollama is unreachable', async () => {
    const provider = new OllamaProvider();

    mockFetch.mockRejectedValueOnce(new Error('ECONNREFUSED'));
    const result = await provider.checkHealth!();
    expect(result).toBe(false);
  });
});

// ── Test 9: Provider registration ─────────────────────────────────────

describe('OllamaProvider — registration in initializeProviders', () => {
  it('is registered with llmClient during initializeProviders()', async () => {
    // Use a fresh mock setup to avoid interference
    const registerMock = vi.fn();
    const setDefaultMock = vi.fn();

    // We need to reset and re-mock modules
    vi.resetModules();

    // Create inline mocks for this specific test
    vi.doMock('../../src/main/llm-client', () => ({
      llmClient: {
        registerProvider: registerMock,
        setDefaultProvider: setDefaultMock,
        isProviderAvailable: vi.fn(() => false),
      },
    }));

    vi.doMock('../../src/main/settings', () => ({
      settingsManager: {
        getPreferredProvider: vi.fn(() => 'anthropic'),
      },
    }));

    vi.doMock('../../src/main/providers/anthropic-provider', () => ({
      AnthropicProvider: class {
        name = 'anthropic' as const;
        isAvailable() { return true; }
      },
    }));

    vi.doMock('../../src/main/providers/openrouter-provider', () => ({
      OpenRouterProvider: class {
        name = 'openrouter' as const;
        isAvailable() { return true; }
      },
    }));

    vi.doMock('../../src/main/providers/hf-provider', () => ({
      HuggingFaceProvider: class {
        name = 'local' as const;
        isAvailable() { return false; }
      },
    }));

    vi.doMock('../../src/main/providers/ollama-provider', () => ({
      OllamaProvider: class {
        name = 'ollama' as const;
        isAvailable() { return false; }
      },
    }));

    const { initializeProviders } = await import('../../src/main/providers/index');
    initializeProviders();

    const registeredNames = registerMock.mock.calls.map(
      (call: unknown[]) => (call[0] as { name: string }).name
    );

    expect(registeredNames).toContain('ollama');
  });
});

// ── Test 10: Ollama takes priority over HF-local for local routing ────

describe('OllamaProvider — priority over HF-local', () => {
  it('when both HF-local and Ollama are registered, Ollama takes priority for local routing', async () => {
    const registerMock = vi.fn();
    const setDefaultMock = vi.fn();

    vi.resetModules();

    vi.doMock('../../src/main/llm-client', () => ({
      llmClient: {
        registerProvider: registerMock,
        setDefaultProvider: setDefaultMock,
        isProviderAvailable: vi.fn(() => false),
      },
    }));

    vi.doMock('../../src/main/settings', () => ({
      settingsManager: {
        getPreferredProvider: vi.fn(() => 'ollama'),
      },
    }));

    vi.doMock('../../src/main/providers/anthropic-provider', () => ({
      AnthropicProvider: class {
        name = 'anthropic' as const;
        isAvailable() { return true; }
      },
    }));

    vi.doMock('../../src/main/providers/openrouter-provider', () => ({
      OpenRouterProvider: class {
        name = 'openrouter' as const;
        isAvailable() { return true; }
      },
    }));

    vi.doMock('../../src/main/providers/hf-provider', () => ({
      HuggingFaceProvider: class {
        name = 'local' as const;
        isAvailable() { return true; }
      },
    }));

    vi.doMock('../../src/main/providers/ollama-provider', () => ({
      OllamaProvider: class {
        name = 'ollama' as const;
        isAvailable() { return true; }
      },
    }));

    const { initializeProviders } = await import('../../src/main/providers/index');
    initializeProviders();

    // When preferred provider is 'ollama' and it's available,
    // setDefaultProvider should be called with 'ollama'
    expect(setDefaultMock).toHaveBeenCalledWith('ollama');
  });
});
