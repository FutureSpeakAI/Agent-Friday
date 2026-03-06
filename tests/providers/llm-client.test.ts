/**
 * LLMClient — Unit tests for the unified LLM abstraction layer.
 *
 * Tests the singleton LLMClient's registration, routing, fallback,
 * and convenience methods WITHOUT calling real LLM APIs.
 *
 * Phase A.1: "First Words" — Provider Core
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { LLMProvider, LLMRequest, LLMResponse, LLMStreamChunk } from '../../src/main/llm-client';
import type { ProviderName } from '../../src/main/intelligence-router';

// ── Helpers ─────────────────────────────────────────────────────────

function makeResponse(overrides: Partial<LLMResponse> = {}): LLMResponse {
  return {
    content: overrides.content ?? 'Hello from mock',
    toolCalls: overrides.toolCalls ?? [],
    usage: overrides.usage ?? { inputTokens: 10, outputTokens: 5 },
    model: overrides.model ?? 'mock-model',
    provider: overrides.provider ?? 'anthropic',
    stopReason: overrides.stopReason ?? 'end_turn',
    latencyMs: overrides.latencyMs ?? 42,
  };
}

function makeMockProvider(name: ProviderName, opts: {
  available?: boolean;
  response?: LLMResponse;
  failOnComplete?: boolean;
  failOnStream?: boolean;
} = {}): LLMProvider {
  const response = opts.response ?? makeResponse({ provider: name });
  return {
    name,
    isAvailable: () => opts.available ?? true,
    complete: opts.failOnComplete
      ? vi.fn().mockRejectedValue(new Error(`${name} failed`))
      : vi.fn().mockResolvedValue(response),
    stream: opts.failOnStream
      ? vi.fn(async function* (): AsyncGenerator<LLMStreamChunk> {
          throw new Error(`${name} stream failed`);
        })
      : vi.fn(async function* (): AsyncGenerator<LLMStreamChunk> {
          yield { text: response.content, done: false };
          yield { text: '', done: true, fullResponse: response };
        }),
  };
}

function makeRequest(prompt = 'Hello'): LLMRequest {
  return {
    messages: [{ role: 'user', content: prompt }],
    maxTokens: 100,
  };
}

// ── We need a fresh LLMClient for each test ─────────────────────────
// The module exports a singleton, so we import the class freshly
// by re-importing the module. Instead, we'll construct via the module's
// internal class by using a factory approach.

// Since LLMClient is not exported as a class, we re-import the module
// to get a fresh singleton per test file. We'll use vi.resetModules().

describe('LLMClient', () => {
  let llmClient: typeof import('../../src/main/llm-client').llmClient;

  beforeEach(async () => {
    vi.resetModules();
    const mod = await import('../../src/main/llm-client');
    llmClient = mod.llmClient;
  });

  // ── Registration ──────────────────────────────────────────────────

  describe('registerProvider', () => {
    it('registers a provider by name', () => {
      const provider = makeMockProvider('anthropic');
      llmClient.registerProvider(provider);
      expect(llmClient.getProvider('anthropic')).toBe(provider);
    });

    it('overwrites a previously registered provider with the same name', () => {
      const first = makeMockProvider('anthropic');
      const second = makeMockProvider('anthropic', { response: makeResponse({ content: 'v2' }) });
      llmClient.registerProvider(first);
      llmClient.registerProvider(second);
      expect(llmClient.getProvider('anthropic')).toBe(second);
    });

    it('registers multiple providers', () => {
      const a = makeMockProvider('anthropic');
      const b = makeMockProvider('openrouter');
      const c = makeMockProvider('local');
      llmClient.registerProvider(a);
      llmClient.registerProvider(b);
      llmClient.registerProvider(c);
      expect(llmClient.getProvider('anthropic')).toBe(a);
      expect(llmClient.getProvider('openrouter')).toBe(b);
      expect(llmClient.getProvider('local')).toBe(c);
    });
  });

  // ── Availability ──────────────────────────────────────────────────

  describe('isProviderAvailable', () => {
    it('returns true when provider is registered and available', () => {
      llmClient.registerProvider(makeMockProvider('anthropic', { available: true }));
      expect(llmClient.isProviderAvailable('anthropic')).toBe(true);
    });

    it('returns false when provider is registered but unavailable', () => {
      llmClient.registerProvider(makeMockProvider('openrouter', { available: false }));
      expect(llmClient.isProviderAvailable('openrouter')).toBe(false);
    });

    it('returns false when provider is not registered', () => {
      expect(llmClient.isProviderAvailable('local')).toBe(false);
    });
  });

  // ── Default Provider ──────────────────────────────────────────────

  describe('setDefaultProvider', () => {
    it('routes requests to the default provider', async () => {
      const anthropic = makeMockProvider('anthropic');
      const openrouter = makeMockProvider('openrouter');
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);
      llmClient.setDefaultProvider('openrouter');

      const result = await llmClient.complete(makeRequest());
      expect(openrouter.complete).toHaveBeenCalled();
      expect(anthropic.complete).not.toHaveBeenCalled();
      expect(result.provider).toBe('openrouter');
    });
  });

  // ── complete() ────────────────────────────────────────────────────

  describe('complete', () => {
    it('sends request to the default provider', async () => {
      const provider = makeMockProvider('anthropic');
      llmClient.registerProvider(provider);

      const result = await llmClient.complete(makeRequest('Test prompt'));
      expect(provider.complete).toHaveBeenCalledWith(
        expect.objectContaining({ messages: [{ role: 'user', content: 'Test prompt' }] })
      );
      expect(result.content).toBe('Hello from mock');
    });

    it('sends request to an explicitly specified provider', async () => {
      const anthropic = makeMockProvider('anthropic');
      const openrouter = makeMockProvider('openrouter', {
        response: makeResponse({ content: 'From OpenRouter', provider: 'openrouter' }),
      });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);

      const result = await llmClient.complete(makeRequest(), 'openrouter');
      expect(openrouter.complete).toHaveBeenCalled();
      expect(anthropic.complete).not.toHaveBeenCalled();
      expect(result.content).toBe('From OpenRouter');
    });

    it('falls back to another provider when the primary fails', async () => {
      const anthropic = makeMockProvider('anthropic', { failOnComplete: true });
      const openrouter = makeMockProvider('openrouter', {
        response: makeResponse({ content: 'Fallback OK', provider: 'openrouter' }),
      });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);

      const result = await llmClient.complete(makeRequest());
      expect(result.content).toBe('Fallback OK');
      expect(result.provider).toBe('openrouter');
    });

    it('skips unavailable providers during fallback', async () => {
      const anthropic = makeMockProvider('anthropic', { failOnComplete: true });
      const openrouter = makeMockProvider('openrouter', { available: false });
      const local = makeMockProvider('local', {
        response: makeResponse({ content: 'Local fallback', provider: 'local' }),
      });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);
      llmClient.registerProvider(local);

      const result = await llmClient.complete(makeRequest());
      expect(openrouter.complete).not.toHaveBeenCalled();
      expect(result.content).toBe('Local fallback');
    });

    it('throws when all providers fail', async () => {
      const anthropic = makeMockProvider('anthropic', { failOnComplete: true });
      const openrouter = makeMockProvider('openrouter', { failOnComplete: true });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);

      await expect(llmClient.complete(makeRequest())).rejects.toThrow('anthropic failed');
    });

    it('throws when no providers are registered', async () => {
      await expect(llmClient.complete(makeRequest())).rejects.toThrow(/No LLM provider available/);
    });

    it('falls back when default provider is unavailable', async () => {
      const anthropic = makeMockProvider('anthropic', { available: false });
      const openrouter = makeMockProvider('openrouter', {
        response: makeResponse({ content: 'Fallback', provider: 'openrouter' }),
      });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);

      const result = await llmClient.complete(makeRequest());
      expect(result.content).toBe('Fallback');
    });

    it('falls back when explicitly requested provider is not registered', async () => {
      const anthropic = makeMockProvider('anthropic');
      llmClient.registerProvider(anthropic);

      // Request 'local' which isn't registered — should fallback to anthropic
      const result = await llmClient.complete(makeRequest(), 'local');
      expect(result.content).toBe('Hello from mock');
    });
  });

  // ── stream() ──────────────────────────────────────────────────────

  describe('stream', () => {
    it('yields chunks from the default provider', async () => {
      const provider = makeMockProvider('anthropic');
      llmClient.registerProvider(provider);

      const chunks: LLMStreamChunk[] = [];
      for await (const chunk of llmClient.stream(makeRequest())) {
        chunks.push(chunk);
      }
      expect(chunks.length).toBe(2);
      expect(chunks[0].text).toBe('Hello from mock');
      expect(chunks[1].done).toBe(true);
    });

    it('falls back when primary stream provider fails', async () => {
      const anthropic = makeMockProvider('anthropic', { failOnStream: true });
      const openrouter = makeMockProvider('openrouter', {
        response: makeResponse({ content: 'Stream fallback', provider: 'openrouter' }),
      });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);

      const chunks: LLMStreamChunk[] = [];
      for await (const chunk of llmClient.stream(makeRequest())) {
        chunks.push(chunk);
      }
      expect(chunks.some(c => c.text === 'Stream fallback')).toBe(true);
    });

    it('throws when all stream providers fail', async () => {
      const anthropic = makeMockProvider('anthropic', { failOnStream: true });
      const openrouter = makeMockProvider('openrouter', { failOnStream: true });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);

      const chunks: LLMStreamChunk[] = [];
      await expect(async () => {
        for await (const chunk of llmClient.stream(makeRequest())) {
          chunks.push(chunk);
        }
      }).rejects.toThrow(/All providers failed/);
    });
  });

  // ── text() convenience method ─────────────────────────────────────

  describe('text', () => {
    it('returns just the text content from a completion', async () => {
      llmClient.registerProvider(
        makeMockProvider('anthropic', {
          response: makeResponse({ content: 'Simple text response' }),
        })
      );

      const result = await llmClient.text('What is 2+2?');
      expect(result).toBe('Simple text response');
    });

    it('passes options through to complete', async () => {
      const provider = makeMockProvider('anthropic');
      llmClient.registerProvider(provider);

      await llmClient.text('Hello', {
        systemPrompt: 'You are helpful',
        maxTokens: 500,
        temperature: 0.7,
      });

      expect(provider.complete).toHaveBeenCalledWith(
        expect.objectContaining({
          systemPrompt: 'You are helpful',
          maxTokens: 500,
          temperature: 0.7,
        })
      );
    });

    it('defaults maxTokens to 1024 when not specified', async () => {
      const provider = makeMockProvider('anthropic');
      llmClient.registerProvider(provider);

      await llmClient.text('Hello');
      expect(provider.complete).toHaveBeenCalledWith(
        expect.objectContaining({ maxTokens: 1024 })
      );
    });

    it('routes to specified provider', async () => {
      const anthropic = makeMockProvider('anthropic');
      const openrouter = makeMockProvider('openrouter', {
        response: makeResponse({ content: 'From OR' }),
      });
      llmClient.registerProvider(anthropic);
      llmClient.registerProvider(openrouter);

      const result = await llmClient.text('Hello', { provider: 'openrouter' });
      expect(result).toBe('From OR');
      expect(anthropic.complete).not.toHaveBeenCalled();
    });
  });
});
