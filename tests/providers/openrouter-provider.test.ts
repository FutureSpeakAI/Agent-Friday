/**
 * OpenRouterProvider — Unit tests for delegation, message formatting, and response parsing.
 *
 * Mocks the openRouter singleton and settingsManager to test complete(),
 * isAvailable(), formatMessages, formatTools, and parseResponse.
 *
 * Phase A.3: "Many Tongues" — Provider Implementation Tests
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mock dependencies ─────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  isConfigured: vi.fn(() => true),
  chat: vi.fn(),
  chatStream: vi.fn(),
  getOpenrouterModel: vi.fn(() => null),
}));

vi.mock('../../src/main/openrouter', () => ({
  openRouter: {
    isConfigured: mocks.isConfigured,
    chat: mocks.chat,
    chatStream: mocks.chatStream,
  },
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    getOpenrouterModel: mocks.getOpenrouterModel,
  },
}));

import { OpenRouterProvider } from '../../src/main/providers/openrouter-provider';
import type { LLMRequest, ToolDefinition } from '../../src/main/llm-client';

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
  choices: [{
    message: { content: 'Hello back!' },
    finish_reason: 'stop',
  }],
  usage: { prompt_tokens: 10, completion_tokens: 5 },
  model: 'anthropic/claude-sonnet-4',
};

const TOOL_RESPONSE = {
  choices: [{
    message: {
      content: '',
      tool_calls: [{
        id: 'call_1',
        type: 'function',
        function: { name: 'search', arguments: '{"query":"weather"}' },
      }],
    },
    finish_reason: 'tool_calls',
  }],
  usage: { prompt_tokens: 20, completion_tokens: 15 },
  model: 'anthropic/claude-sonnet-4',
};

// ── Tests ─────────────────────────────────────────────────────────────

describe('OpenRouterProvider — isAvailable', () => {
  const provider = new OpenRouterProvider();

  it('delegates to openRouter.isConfigured()', () => {
    mocks.isConfigured.mockReturnValue(true);
    expect(provider.isAvailable()).toBe(true);

    mocks.isConfigured.mockReturnValue(false);
    expect(provider.isAvailable()).toBe(false);
  });
});

describe('OpenRouterProvider — complete', () => {
  const provider = new OpenRouterProvider();

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isConfigured.mockReturnValue(true);
  });

  it('throws when not configured', async () => {
    mocks.isConfigured.mockReturnValue(false);
    await expect(provider.complete(makeRequest())).rejects.toThrow('OpenRouter API key not configured');
  });

  it('parses a text response with stop reason mapping', async () => {
    mocks.chat.mockResolvedValue(TEXT_RESPONSE);

    const result = await provider.complete(makeRequest());
    expect(result.content).toBe('Hello back!');
    expect(result.toolCalls).toEqual([]);
    expect(result.stopReason).toBe('end_turn'); // 'stop' → 'end_turn'
    expect(result.provider).toBe('openrouter');
    expect(result.usage.inputTokens).toBe(10);
    expect(result.usage.outputTokens).toBe(5);
  });

  it('parses a tool_calls response', async () => {
    mocks.chat.mockResolvedValue(TOOL_RESPONSE);

    const result = await provider.complete(makeRequest());
    expect(result.toolCalls).toHaveLength(1);
    expect(result.toolCalls[0].name).toBe('search');
    expect(result.toolCalls[0].input).toEqual({ query: 'weather' });
    expect(result.toolCalls[0].type).toBe('tool_use');
    expect(result.stopReason).toBe('tool_use'); // 'tool_calls' → 'tool_use'
  });

  it('uses model from settingsManager when available', async () => {
    mocks.chat.mockResolvedValue(TEXT_RESPONSE);
    mocks.getOpenrouterModel.mockReturnValue('google/gemini-2.0-flash');

    await provider.complete(makeRequest());
    expect(mocks.chat).toHaveBeenCalledWith(expect.objectContaining({
      model: 'google/gemini-2.0-flash',
    }));
  });

  it('uses request model over settings model', async () => {
    mocks.chat.mockResolvedValue(TEXT_RESPONSE);
    mocks.getOpenrouterModel.mockReturnValue('google/gemini-2.0-flash');

    await provider.complete(makeRequest({ model: 'openai/gpt-4o' }));
    expect(mocks.chat).toHaveBeenCalledWith(expect.objectContaining({
      model: 'openai/gpt-4o',
    }));
  });

  it('falls back to default model', async () => {
    mocks.chat.mockResolvedValue(TEXT_RESPONSE);
    mocks.getOpenrouterModel.mockReturnValue(null);

    await provider.complete(makeRequest());
    expect(mocks.chat).toHaveBeenCalledWith(expect.objectContaining({
      model: 'anthropic/claude-sonnet-4',
    }));
  });

  it('adds system prompt as first message', async () => {
    mocks.chat.mockResolvedValue(TEXT_RESPONSE);

    await provider.complete(makeRequest({ systemPrompt: 'Be helpful' }));
    const callArgs = mocks.chat.mock.calls[0][0];
    expect(callArgs.messages[0]).toEqual({ role: 'system', content: 'Be helpful' });
  });

  it('handles empty response (no choices)', async () => {
    mocks.chat.mockResolvedValue({ choices: [] });

    const result = await provider.complete(makeRequest());
    expect(result.content).toBe('');
    expect(result.toolCalls).toEqual([]);
    expect(result.stopReason).toBe('end_turn');
  });

  it('uses model from response when available', async () => {
    mocks.chat.mockResolvedValue({
      ...TEXT_RESPONSE,
      model: 'anthropic/claude-3-haiku-20240307',
    });

    const result = await provider.complete(makeRequest());
    expect(result.model).toBe('anthropic/claude-3-haiku-20240307');
  });

  it('parses tool call arguments from JSON strings', async () => {
    mocks.chat.mockResolvedValue({
      choices: [{
        message: {
          content: null,
          tool_calls: [{
            id: 'call_2',
            type: 'function',
            function: { name: 'calc', arguments: '{"expression":"2+2"}' },
          }],
        },
        finish_reason: 'tool_calls',
      }],
      usage: { prompt_tokens: 5, completion_tokens: 10 },
    });

    const result = await provider.complete(makeRequest());
    expect(result.toolCalls[0].input).toEqual({ expression: '2+2' });
  });

  it('handles malformed tool call JSON gracefully', async () => {
    mocks.chat.mockResolvedValue({
      choices: [{
        message: {
          content: null,
          tool_calls: [{
            id: 'call_3',
            type: 'function',
            function: { name: 'broken', arguments: '{not valid json' },
          }],
        },
        finish_reason: 'tool_calls',
      }],
      usage: { prompt_tokens: 5, completion_tokens: 10 },
    });

    const result = await provider.complete(makeRequest());
    expect(result.toolCalls[0].input).toBe('{not valid json');
  });
});

describe('OpenRouterProvider — formatTools', () => {
  const provider = new OpenRouterProvider();

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isConfigured.mockReturnValue(true);
  });

  it('converts Anthropic-format tools to OpenAI function format', async () => {
    mocks.chat.mockResolvedValue(TEXT_RESPONSE);
    const tools: ToolDefinition[] = [{
      name: 'search',
      description: 'Search things',
      input_schema: { type: 'object', properties: { q: { type: 'string' } } },
    }];

    await provider.complete(makeRequest({ tools }));
    expect(mocks.chat).toHaveBeenCalledWith(expect.objectContaining({
      tools: [{
        type: 'function',
        function: {
          name: 'search',
          description: 'Search things',
          parameters: { type: 'object', properties: { q: { type: 'string' } } },
        },
      }],
    }));
  });

  it('passes through OpenAI-format tools', async () => {
    mocks.chat.mockResolvedValue(TEXT_RESPONSE);
    const tools: ToolDefinition[] = [{
      name: '',
      function: {
        name: 'calc',
        description: 'Calculate',
        parameters: { type: 'object', properties: {} },
      },
    }];

    await provider.complete(makeRequest({ tools }));
    const callArgs = mocks.chat.mock.calls[0][0];
    expect(callArgs.tools[0].function.name).toBe('calc');
  });
});
