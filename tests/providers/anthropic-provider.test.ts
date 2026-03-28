/**
 * AnthropicProvider — Unit tests for message formatting, tool conversion, and response parsing.
 *
 * Mocks the @anthropic-ai/sdk to test complete(), isAvailable(), formatMessages,
 * formatTools, and parseResponse without real API calls.
 *
 * Phase A.3: "Many Tongues" — Provider Implementation Tests
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { LLMRequest, ToolDefinition } from '../../src/main/llm-client';

// ── Mock SDK ──────────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  messagesCreate: vi.fn(),
  getAnthropicApiKey: vi.fn(() => ''),
}));

vi.mock('@anthropic-ai/sdk', () => {
  class MockAnthropic {
    messages = { create: mocks.messagesCreate };
  }
  return {
    default: MockAnthropic,
    __esModule: true,
  };
});

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    getAnthropicApiKey: mocks.getAnthropicApiKey,
  },
}));

// ── Helpers ───────────────────────────────────────────────────────────

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

const TEXT_RESPONSE = {
  content: [{ type: 'text', text: 'Hello back!' }],
  stop_reason: 'end_turn',
  usage: { input_tokens: 10, output_tokens: 5 },
  model: 'claude-sonnet-4-20250514',
};

const TOOL_USE_RESPONSE = {
  content: [
    { type: 'text', text: 'Let me look that up.' },
    { type: 'tool_use', id: 'tu_1', name: 'search', input: { query: 'weather' } },
  ],
  stop_reason: 'tool_use',
  usage: { input_tokens: 20, output_tokens: 30 },
  model: 'claude-sonnet-4-20250514',
};

// ── Module-level state for dynamic imports ────────────────────────────

let providerModule: typeof import('../../src/main/providers/anthropic-provider');

// ── Tests ─────────────────────────────────────────────────────────────

describe('AnthropicProvider — isAvailable', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    providerModule = await import('../../src/main/providers/anthropic-provider');
  });

  it('returns true when API key is configured', () => {
    mocks.getAnthropicApiKey.mockReturnValue('sk-test-key');
    const provider = new providerModule.AnthropicProvider();
    expect(provider.isAvailable()).toBe(true);
  });

  it('returns false when API key is empty', () => {
    mocks.getAnthropicApiKey.mockReturnValue('');
    const provider = new providerModule.AnthropicProvider();
    expect(provider.isAvailable()).toBe(false);
  });

  it('returns false when API key is unset', () => {
    mocks.getAnthropicApiKey.mockReturnValue(null);
    const provider = new providerModule.AnthropicProvider();
    expect(provider.isAvailable()).toBe(false);
  });
});

describe('AnthropicProvider — complete', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    providerModule = await import('../../src/main/providers/anthropic-provider');
    mocks.getAnthropicApiKey.mockReturnValue('sk-test-key');
  });

  it('throws when API key is not set', async () => {
    mocks.getAnthropicApiKey.mockReturnValue('');
    const provider = new providerModule.AnthropicProvider();
    await expect(provider.complete(makeRequest())).rejects.toThrow('ANTHROPIC_API_KEY not configured');
  });

  it('parses a text response', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    const result = await provider.complete(makeRequest());
    expect(result.content).toBe('Hello back!');
    expect(result.toolCalls).toEqual([]);
    expect(result.stopReason).toBe('end_turn');
    expect(result.provider).toBe('anthropic');
    expect(result.usage.inputTokens).toBe(10);
    expect(result.usage.outputTokens).toBe(5);
    expect(result.latencyMs).toBeGreaterThanOrEqual(0);
  });

  it('parses a tool_use response', async () => {
    mocks.messagesCreate.mockResolvedValue(TOOL_USE_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    const result = await provider.complete(makeRequest());
    expect(result.content).toBe('Let me look that up.');
    expect(result.toolCalls).toHaveLength(1);
    expect(result.toolCalls[0].name).toBe('search');
    expect(result.toolCalls[0].input).toEqual({ query: 'weather' });
    expect(result.toolCalls[0].type).toBe('tool_use');
    expect(result.stopReason).toBe('tool_use');
  });

  it('uses custom model when specified', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({ model: 'claude-opus-4-20250514' }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.model).toBe('claude-opus-4-20250514');
  });

  it('uses default model when not specified', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest());
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.model).toBe('claude-sonnet-4-20250514');
  });

  it('passes maxTokens and temperature', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({ maxTokens: 2048, temperature: 0.7 }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.max_tokens).toBe(2048);
    expect(createParams.temperature).toBe(0.7);
  });

  it('extracts system messages to separate system field', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({
      messages: [
        { role: 'system', content: 'You are helpful.' },
        { role: 'user', content: 'Hi' },
      ],
    }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.system).toBe('You are helpful.');
    expect(createParams.messages).toHaveLength(1);
    expect(createParams.messages[0].role).toBe('user');
  });

  it('concatenates systemPrompt and system messages', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({
      systemPrompt: 'Base prompt.',
      messages: [
        { role: 'system', content: 'Extra context.' },
        { role: 'user', content: 'Hi' },
      ],
    }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.system).toContain('Base prompt.');
    expect(createParams.system).toContain('Extra context.');
  });

  it('formats tool results as user messages with tool_result content', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({
      messages: [
        { role: 'user', content: 'Search for weather' },
        { role: 'assistant', content: '', tool_calls: [{ id: 'tu_1', type: 'tool_use', name: 'search', input: { q: 'weather' } }] },
        { role: 'tool', content: 'Sunny, 72F', tool_call_id: 'tu_1' },
      ],
    }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    // Tool result should be a user message with tool_result content block
    const toolResultMsg = createParams.messages.find((m: any) =>
      m.role === 'user' && Array.isArray(m.content) && m.content[0]?.type === 'tool_result'
    );
    expect(toolResultMsg).toBeDefined();
    expect(toolResultMsg.content[0].tool_use_id).toBe('tu_1');
    expect(toolResultMsg.content[0].content).toBe('Sunny, 72F');
  });

  it('converts assistant tool_calls to content blocks', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({
      messages: [
        { role: 'user', content: 'Do something' },
        {
          role: 'assistant',
          content: 'Using tool',
          tool_calls: [{ id: 'tc_1', type: 'tool_use', name: 'calc', input: { x: 1 } }],
        },
      ],
    }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    const assistantMsg = createParams.messages.find((m: any) =>
      m.role === 'assistant' && Array.isArray(m.content)
    );
    expect(assistantMsg).toBeDefined();
    expect(assistantMsg.content).toContainEqual(expect.objectContaining({ type: 'text', text: 'Using tool' }));
    expect(assistantMsg.content).toContainEqual(expect.objectContaining({ type: 'tool_use', id: 'tc_1', name: 'calc' }));
  });
});

describe('AnthropicProvider — formatTools', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    providerModule = await import('../../src/main/providers/anthropic-provider');
    mocks.getAnthropicApiKey.mockReturnValue('sk-test-key');
  });

  it('converts Anthropic-format tools (name + input_schema)', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();
    const tools: ToolDefinition[] = [{
      name: 'get_weather',
      description: 'Get weather',
      input_schema: { type: 'object', properties: { city: { type: 'string' } } },
    }];

    await provider.complete(makeRequest({ tools }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.tools[0].name).toBe('get_weather');
    expect(createParams.tools[0].input_schema).toEqual(tools[0].input_schema);
  });

  it('converts OpenAI-format tools (function.name + function.parameters)', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();
    const tools: ToolDefinition[] = [{
      name: '',
      function: {
        name: 'search',
        description: 'Search the web',
        parameters: { type: 'object', properties: { q: { type: 'string' } } },
      },
    }];

    await provider.complete(makeRequest({ tools }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.tools[0].name).toBe('search');
    expect(createParams.tools[0].description).toBe('Search the web');
  });

  it('handles tool_choice auto', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({ tools: [{ name: 'test' }], toolChoice: 'auto' }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.tool_choice).toEqual({ type: 'auto' });
  });

  it('handles specific tool_choice', async () => {
    mocks.messagesCreate.mockResolvedValue(TEXT_RESPONSE);
    const provider = new providerModule.AnthropicProvider();

    await provider.complete(makeRequest({
      tools: [{ name: 'search' }],
      toolChoice: { name: 'search' },
    }));
    const createParams = mocks.messagesCreate.mock.calls[0][0];
    expect(createParams.tool_choice).toEqual({ type: 'tool', name: 'search' });
  });
});

describe('AnthropicProvider — parseResponse edge cases', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    providerModule = await import('../../src/main/providers/anthropic-provider');
    mocks.getAnthropicApiKey.mockReturnValue('sk-test-key');
  });

  it('handles empty content array', async () => {
    mocks.messagesCreate.mockResolvedValue({
      content: [],
      stop_reason: 'end_turn',
      usage: { input_tokens: 5, output_tokens: 0 },
    });
    const provider = new providerModule.AnthropicProvider();

    const result = await provider.complete(makeRequest());
    expect(result.content).toBe('');
    expect(result.toolCalls).toEqual([]);
  });

  it('maps max_tokens stop reason', async () => {
    mocks.messagesCreate.mockResolvedValue({
      content: [{ type: 'text', text: 'truncated...' }],
      stop_reason: 'max_tokens',
      usage: { input_tokens: 5, output_tokens: 100 },
    });
    const provider = new providerModule.AnthropicProvider();

    const result = await provider.complete(makeRequest());
    expect(result.stopReason).toBe('max_tokens');
  });

  it('handles missing usage gracefully', async () => {
    mocks.messagesCreate.mockResolvedValue({
      content: [{ type: 'text', text: 'ok' }],
      stop_reason: 'end_turn',
    });
    const provider = new providerModule.AnthropicProvider();

    const result = await provider.complete(makeRequest());
    expect(result.usage.inputTokens).toBe(0);
    expect(result.usage.outputTokens).toBe(0);
  });
});
