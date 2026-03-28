/**
 * HuggingFaceProvider — Unit tests for availability, config, health check, and response parsing.
 *
 * Mocks settingsManager and global fetch to test isAvailable(), setConfig(),
 * checkHealth(), listModels(), complete(), and response parsing without real HTTP.
 *
 * Phase A.3: "Many Tongues" — Provider Implementation Tests
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ── Mock dependencies ─────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  settingsGet: vi.fn(() => ({})),
  getHuggingfaceApiKey: vi.fn(() => ''),
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    get: mocks.settingsGet,
    getHuggingfaceApiKey: mocks.getHuggingfaceApiKey,
  },
}));

import { HuggingFaceProvider } from '../../src/main/providers/hf-provider';
import type { LLMRequest } from '../../src/main/llm-client';

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

function makeChatResponse(overrides: Record<string, any> = {}) {
  return {
    ok: true,
    json: async () => ({
      id: 'resp-1',
      model: overrides.model ?? 'meta-llama/Llama-3.3-70B-Instruct',
      choices: overrides.choices ?? [{
        index: 0,
        message: { role: 'assistant', content: overrides.content ?? 'Hi there!' },
        finish_reason: overrides.finish_reason ?? 'stop',
      }],
      usage: overrides.usage ?? { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    }),
    text: async () => '',
    status: 200,
    headers: new Map(),
  };
}

// ── Tests ─────────────────────────────────────────────────────────────

describe('HuggingFaceProvider — isAvailable', () => {
  const originalToken = process.env.HF_TOKEN;

  afterEach(() => {
    if (originalToken !== undefined) {
      process.env.HF_TOKEN = originalToken;
    } else {
      delete process.env.HF_TOKEN;
    }
  });

  it('returns true when huggingfaceApiKey is in settings via get()', () => {
    mocks.settingsGet.mockReturnValue({ huggingfaceApiKey: 'hf_settings_key' });
    mocks.getHuggingfaceApiKey.mockReturnValue('');
    const provider = new HuggingFaceProvider();
    expect(provider.isAvailable()).toBe(true);
  });

  it('returns true when huggingfaceApiKey is via getHuggingfaceApiKey()', () => {
    mocks.settingsGet.mockReturnValue({});
    mocks.getHuggingfaceApiKey.mockReturnValue('hf_from_getter');
    const provider = new HuggingFaceProvider();
    expect(provider.isAvailable()).toBe(true);
  });

  it('returns true when localModelEnabled is true (no API key)', () => {
    mocks.settingsGet.mockReturnValue({ localModelEnabled: true });
    mocks.getHuggingfaceApiKey.mockReturnValue('');
    const provider = new HuggingFaceProvider();
    expect(provider.isAvailable()).toBe(true);
  });

  it('returns false when no key and local not enabled', () => {
    mocks.settingsGet.mockReturnValue({});
    mocks.getHuggingfaceApiKey.mockReturnValue('');
    const provider = new HuggingFaceProvider();
    expect(provider.isAvailable()).toBe(false);
  });

  it('returns true when config.apiKey is set programmatically', () => {
    mocks.settingsGet.mockReturnValue({});
    mocks.getHuggingfaceApiKey.mockReturnValue('');
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_prog_key' });
    expect(provider.isAvailable()).toBe(true);
  });

  it('returns true when config.localEnabled is set programmatically', () => {
    mocks.settingsGet.mockReturnValue({});
    mocks.getHuggingfaceApiKey.mockReturnValue('');
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true });
    expect(provider.isAvailable()).toBe(true);
  });
});

describe('HuggingFaceProvider — setConfig', () => {
  it('merges config and returns it via getConfig', () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'key1' });
    expect(provider.getConfig().apiKey).toBe('key1');

    provider.setConfig({ localEndpoint: 'http://custom:8080' });
    expect(provider.getConfig().apiKey).toBe('key1');
    expect(provider.getConfig().localEndpoint).toBe('http://custom:8080');
  });

  it('invalidates health cache on config change', async () => {
    setupFetch();
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true, localEndpoint: 'http://localhost:8080/v1' });

    // First health check — returns healthy
    mockFetch.mockResolvedValueOnce({ ok: true });
    const healthy1 = await provider.checkHealth();
    expect(healthy1).toBe(true);

    // Change config — should invalidate cache
    provider.setConfig({ localEndpoint: 'http://localhost:9090/v1' });

    // Second health check — should call fetch again (cache invalidated)
    mockFetch.mockResolvedValueOnce({ ok: false });
    mockFetch.mockResolvedValueOnce({ ok: false });
    mockFetch.mockResolvedValueOnce({ ok: false });
    const healthy2 = await provider.checkHealth();
    expect(healthy2).toBe(false);

    vi.unstubAllGlobals();
  });
});

describe('HuggingFaceProvider — getInflightCount', () => {
  it('starts at 0', () => {
    const provider = new HuggingFaceProvider();
    expect(provider.getInflightCount()).toBe(0);
  });
});

describe('HuggingFaceProvider — checkHealth', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
    mocks.getHuggingfaceApiKey.mockReturnValue('');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns cached result within TTL', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true, localEndpoint: 'http://localhost:8080/v1' });

    // First check succeeds via /health
    mockFetch.mockResolvedValueOnce({ ok: true });
    const first = await provider.checkHealth();
    expect(first).toBe(true);

    // Second check should use cache — no additional fetch calls
    const callCount = mockFetch.mock.calls.length;
    const second = await provider.checkHealth();
    expect(second).toBe(true);
    expect(mockFetch.mock.calls.length).toBe(callCount); // No new calls
  });

  it('tries /health first for local endpoints (TGI strategy)', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true, localEndpoint: 'http://localhost:8080/v1' });

    mockFetch.mockResolvedValueOnce({ ok: true }); // /health succeeds
    const healthy = await provider.checkHealth();
    expect(healthy).toBe(true);
    expect(mockFetch.mock.calls[0][0]).toContain('/health');
  });

  it('falls back to root URL for Ollama when /health fails', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true, localEndpoint: 'http://localhost:11434/v1' });

    mockFetch.mockRejectedValueOnce(new Error('ECONNREFUSED')); // /health fails
    mockFetch.mockResolvedValueOnce({ ok: true }); // root succeeds
    const healthy = await provider.checkHealth();
    expect(healthy).toBe(true);
    expect(mockFetch.mock.calls.length).toBe(2);
  });

  it('falls back to /models when both /health and root fail', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true, localEndpoint: 'http://localhost:8080/v1' });

    mockFetch.mockRejectedValueOnce(new Error('fail')); // /health
    mockFetch.mockRejectedValueOnce(new Error('fail')); // root
    mockFetch.mockResolvedValueOnce({ ok: true }); // /models
    const healthy = await provider.checkHealth();
    expect(healthy).toBe(true);
  });

  it('returns false when all strategies fail', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true, localEndpoint: 'http://localhost:8080/v1' });

    mockFetch.mockRejectedValueOnce(new Error('fail')); // /health
    mockFetch.mockRejectedValueOnce(new Error('fail')); // root
    mockFetch.mockRejectedValueOnce(new Error('fail')); // /models
    const healthy = await provider.checkHealth();
    expect(healthy).toBe(false);
  });
});

describe('HuggingFaceProvider — listModels', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
    mocks.getHuggingfaceApiKey.mockReturnValue('');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('parses OpenAI-compatible model list (data array)', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ data: [{ id: 'llama-3' }, { id: 'mistral-7b' }] }),
    });

    const models = await provider.listModels();
    expect(models).toHaveLength(2);
    expect(models[0].id).toBe('llama-3');
    expect(models[1].id).toBe('mistral-7b');
  });

  it('parses flat array of model strings', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ['model-a', 'model-b'],
    });

    const models = await provider.listModels();
    expect(models).toHaveLength(2);
    expect(models[0].id).toBe('model-a');
  });

  it('returns empty array on HTTP error', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
    const models = await provider.listModels();
    expect(models).toEqual([]);
  });

  it('returns empty array on network error', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockRejectedValueOnce(new Error('ECONNREFUSED'));
    const models = await provider.listModels();
    expect(models).toEqual([]);
  });
});

describe('HuggingFaceProvider — complete', () => {
  beforeEach(() => {
    setupFetch();
    mocks.settingsGet.mockReturnValue({});
    mocks.getHuggingfaceApiKey.mockReturnValue('');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('parses a text completion response', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce(makeChatResponse());

    const result = await provider.complete(makeRequest());
    expect(result.content).toBe('Hi there!');
    expect(result.toolCalls).toEqual([]);
    expect(result.stopReason).toBe('end_turn'); // 'stop' → 'end_turn'
    expect(result.provider).toBe('local');
    expect(result.usage.inputTokens).toBe(10);
    expect(result.usage.outputTokens).toBe(5);
  });

  it('sends Authorization header with API key', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_my_key' });

    mockFetch.mockResolvedValueOnce(makeChatResponse());
    await provider.complete(makeRequest());

    const fetchOptions = mockFetch.mock.calls[0][1];
    expect(fetchOptions.headers['Authorization']).toBe('Bearer hf_my_key');
  });

  it('tracks inflight requests (increments and decrements)', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    let capturedInflight = -1;
    mockFetch.mockImplementationOnce(async () => {
      capturedInflight = provider.getInflightCount();
      return makeChatResponse();
    });

    expect(provider.getInflightCount()).toBe(0);
    await provider.complete(makeRequest());
    expect(capturedInflight).toBe(1); // Was 1 during fetch
    expect(provider.getInflightCount()).toBe(0); // Back to 0 after
  });

  it('decrements inflight on error', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
      headers: new Map(),
    });

    await expect(provider.complete(makeRequest())).rejects.toThrow();
    expect(provider.getInflightCount()).toBe(0);
  });

  it('parses tool_calls in response', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce(makeChatResponse({
      choices: [{
        index: 0,
        message: {
          role: 'assistant',
          content: null,
          tool_calls: [{
            id: 'tc_1',
            type: 'function',
            function: { name: 'get_weather', arguments: '{"city":"NYC"}' },
          }],
        },
        finish_reason: 'tool_calls',
      }],
    }));

    const result = await provider.complete(makeRequest());
    expect(result.toolCalls).toHaveLength(1);
    expect(result.toolCalls[0].name).toBe('get_weather');
    expect(result.toolCalls[0].input).toEqual({ city: 'NYC' });
    expect(result.stopReason).toBe('tool_use'); // 'tool_calls' → 'tool_use'
  });

  it('maps finish_reason "length" to "max_tokens"', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce(makeChatResponse({ finish_reason: 'length' }));
    const result = await provider.complete(makeRequest());
    expect(result.stopReason).toBe('max_tokens');
  });

  it('handles empty choices array', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce(makeChatResponse({ choices: [] }));
    const result = await provider.complete(makeRequest());
    expect(result.content).toBe('');
    expect(result.toolCalls).toEqual([]);
  });

  it('uses local endpoint when local mode is enabled', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ localEnabled: true, localEndpoint: 'http://localhost:11434/v1' });

    mockFetch.mockResolvedValueOnce(makeChatResponse());
    await provider.complete(makeRequest());

    const url = mockFetch.mock.calls[0][0];
    expect(url).toBe('http://localhost:11434/v1/chat/completions');
  });

  it('uses cloud endpoint when local mode is disabled', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce(makeChatResponse());
    await provider.complete(makeRequest());

    const url = mockFetch.mock.calls[0][0];
    expect(url).toContain('huggingface.co');
  });

  it('adds system prompt as first message', async () => {
    const provider = new HuggingFaceProvider();
    provider.setConfig({ apiKey: 'hf_test' });

    mockFetch.mockResolvedValueOnce(makeChatResponse());
    await provider.complete(makeRequest({ systemPrompt: 'Be concise.' }));

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.messages[0]).toEqual({ role: 'system', content: 'Be concise.' });
  });
});
