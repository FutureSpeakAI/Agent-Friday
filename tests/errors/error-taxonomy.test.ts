/**
 * Error Taxonomy — Tests for standardized error classification.
 *
 * Validates:
 *   1. Error categories are correct (transient, persistent, recoverable, fatal)
 *   2. classifyError correctly categorizes raw errors
 *   3. withRetry retries transient, throws persistent immediately
 *   4. Fail-closed helpers enforce cLaw safety boundaries
 */

import { describe, it, expect, vi } from 'vitest';
import {
  AgentFridayError,
  TransientError,
  PersistentError,
  RecoverableError,
  FatalIntegrityError,
  classifyError,
  withRetry,
  failClosedTrust,
  failClosedIntegrity,
} from '../../src/main/errors';

// ── Error Construction ───────────────────────────────────────────────

describe('Error Taxonomy', () => {
  describe('AgentFridayError base class', () => {
    it('should store category, source, and timestamp', () => {
      const err = new AgentFridayError({
        message: 'test error',
        category: 'transient',
        source: 'gemini',
      });
      expect(err.category).toBe('transient');
      expect(err.source).toBe('gemini');
      expect(err.timestamp).toBeGreaterThan(0);
      expect(err.message).toBe('test error');
    });

    it('should default retryable=true for transient errors', () => {
      const err = new AgentFridayError({
        message: 'blip',
        category: 'transient',
        source: 'network',
      });
      expect(err.retryable).toBe(true);
    });

    it('should default retryable=false for persistent errors', () => {
      const err = new AgentFridayError({
        message: 'bad key',
        category: 'persistent',
        source: 'claude',
      });
      expect(err.retryable).toBe(false);
    });

    it('should default userFacing=true for persistent errors', () => {
      const err = new AgentFridayError({
        message: 'config issue',
        category: 'persistent',
        source: 'openrouter',
      });
      expect(err.userFacing).toBe(true);
    });

    it('should serialize to JSON for IPC transport', () => {
      const err = new AgentFridayError({
        message: 'serialize me',
        category: 'fatal',
        source: 'integrity',
      });
      const json = err.toJSON();
      expect(json.name).toBe('AgentFridayError');
      expect(json.category).toBe('fatal');
      expect(json.source).toBe('integrity');
      expect(json.message).toBe('serialize me');
      expect(json.timestamp).toBeGreaterThan(0);
    });

    it('should provide user-friendly default messages per category', () => {
      const transient = new TransientError('gemini', 'ws close');
      expect(transient.userMessage).toContain('gemini');
      expect(transient.userMessage.toLowerCase()).toContain('temporary');

      const persistent = new PersistentError('claude', 'bad key');
      expect(persistent.userMessage).toContain('claude');

      const fatal = new FatalIntegrityError('integrity', 'tampered');
      expect(fatal.userMessage.toLowerCase()).toContain('safe mode');
    });
  });

  describe('Specialized error types', () => {
    it('TransientError should be retryable', () => {
      const err = new TransientError('gemini', 'connection lost');
      expect(err.category).toBe('transient');
      expect(err.retryable).toBe(true);
      expect(err.name).toBe('TransientError');
    });

    it('PersistentError should NOT be retryable', () => {
      const err = new PersistentError('claude', 'invalid API key');
      expect(err.category).toBe('persistent');
      expect(err.retryable).toBe(false);
      expect(err.name).toBe('PersistentError');
    });

    it('RecoverableError should have retryAfterMs', () => {
      const err = new RecoverableError('claude', 'rate limited', { retryAfterMs: 10000 });
      expect(err.category).toBe('recoverable');
      expect(err.retryable).toBe(true);
      expect(err.retryAfterMs).toBe(10000);
    });

    it('RecoverableError should default retryAfterMs to 5000', () => {
      const err = new RecoverableError('openrouter', 'quota exceeded');
      expect(err.retryAfterMs).toBe(5000);
    });

    it('FatalIntegrityError should mention safe mode in user message', () => {
      const err = new FatalIntegrityError('integrity', 'HMAC verification failed');
      expect(err.category).toBe('fatal');
      expect(err.retryable).toBe(false);
      expect(err.userFacing).toBe(true);
      expect(err.userMessage.toLowerCase()).toContain('safe mode');
    });
  });

  // ── Error Classification ─────────────────────────────────────────

  describe('classifyError', () => {
    it('should pass through existing AgentFridayError instances', () => {
      const original = new TransientError('gemini', 'existing');
      const classified = classifyError('gemini', original);
      expect(classified).toBe(original);
    });

    it('should classify "API key" errors as persistent', () => {
      const err = classifyError('claude', new Error('Invalid API key provided'));
      expect(err.category).toBe('persistent');
      expect(err).toBeInstanceOf(PersistentError);
    });

    it('should classify "unauthorized" errors as persistent', () => {
      const err = classifyError('openrouter', new Error('Unauthorized: check credentials'));
      expect(err.category).toBe('persistent');
    });

    it('should classify "403" errors as persistent', () => {
      const err = classifyError('claude', new Error('HTTP 403 Forbidden'));
      expect(err.category).toBe('persistent');
    });

    it('should classify "rate limit" errors as recoverable', () => {
      const err = classifyError('claude', new Error('Rate limit exceeded, retry after 30'));
      expect(err.category).toBe('recoverable');
      expect(err).toBeInstanceOf(RecoverableError);
    });

    it('should classify "429" errors as recoverable', () => {
      const err = classifyError('openrouter', new Error('HTTP 429 Too Many Requests'));
      expect(err.category).toBe('recoverable');
    });

    it('should classify "ECONNREFUSED" as transient', () => {
      const err = classifyError('gemini', new Error('connect ECONNREFUSED'));
      expect(err.category).toBe('transient');
      expect(err.retryable).toBe(true);
    });

    it('should classify "timeout" as transient', () => {
      const err = classifyError('mcp', new Error('Request timeout after 30s'));
      expect(err.category).toBe('transient');
    });

    it('should classify "fetch failed" as transient', () => {
      const err = classifyError('claude', new Error('fetch failed'));
      expect(err.category).toBe('transient');
    });

    it('should classify unknown errors as transient (optimistic)', () => {
      const err = classifyError('unknown', new Error('something weird happened'));
      expect(err.category).toBe('transient');
    });

    it('should handle string errors', () => {
      const err = classifyError('gemini', 'raw string error');
      expect(err).toBeInstanceOf(AgentFridayError);
      expect(err.message).toBe('raw string error');
    });
  });

  // ── Retry Logic ──────────────────────────────────────────────────

  describe('withRetry', () => {
    it('should return value on first success', async () => {
      const result = await withRetry('gemini', async () => 'hello');
      expect(result).toBe('hello');
    });

    it('should retry transient errors', async () => {
      let attempts = 0;
      const result = await withRetry('gemini', async () => {
        attempts++;
        if (attempts < 3) throw new Error('connect ECONNREFUSED');
        return 'recovered';
      }, { maxAttempts: 3, baseDelayMs: 10 });

      expect(result).toBe('recovered');
      expect(attempts).toBe(3);
    });

    it('should throw persistent errors IMMEDIATELY without retry', async () => {
      let attempts = 0;
      await expect(
        withRetry('claude', async () => {
          attempts++;
          throw new Error('Invalid API key');
        }, { maxAttempts: 5, baseDelayMs: 10 })
      ).rejects.toThrow();

      // Should have thrown on first attempt — no retries for persistent
      expect(attempts).toBe(1);
    });

    it('should call onRetry callback', async () => {
      const onRetry = vi.fn();
      let attempts = 0;

      await withRetry('gemini', async () => {
        attempts++;
        if (attempts < 2) throw new Error('timeout');
        return 'ok';
      }, { maxAttempts: 3, baseDelayMs: 10, onRetry });

      expect(onRetry).toHaveBeenCalledTimes(1);
      expect(onRetry).toHaveBeenCalledWith(1, expect.any(Number), expect.any(AgentFridayError));
    });

    it('should throw after maxAttempts exhausted', async () => {
      await expect(
        withRetry('network', async () => {
          throw new Error('connect ECONNREFUSED');
        }, { maxAttempts: 2, baseDelayMs: 10 })
      ).rejects.toThrow('connect ECONNREFUSED');
    });
  });

  // ── Fail-Closed Helpers (cLaw Safety Gate) ─────────────────────

  describe('failClosedTrust — cLaw enforcement', () => {
    it('should return the function result on success', () => {
      const result = failClosedTrust(() => 'owner-dm' as const, 'public' as const, 'test');
      expect(result).toBe('owner-dm');
    });

    it('should return MOST RESTRICTIVE fallback on ANY error', () => {
      const result = failClosedTrust(
        () => { throw new Error('lookup crash'); },
        'public' as const,
        'test-crash'
      );
      expect(result).toBe('public');
    });

    it('should never return a permissive tier on failure', () => {
      // Simulate: trust resolution throws, fallback is public
      const result = failClosedTrust(
        () => { throw new TypeError('cannot read properties of undefined'); },
        'public' as const,
        'undefined-owners'
      );
      expect(result).toBe('public');
      // NOT 'local', NOT 'owner-dm' — always the most restrictive
    });
  });

  describe('failClosedIntegrity — cLaw enforcement', () => {
    it('should return true when integrity check passes', () => {
      const result = failClosedIntegrity(() => true, 'hmac-check');
      expect(result).toBe(true);
    });

    it('should return false when integrity check fails normally', () => {
      const result = failClosedIntegrity(() => false, 'hmac-tampered');
      expect(result).toBe(false);
    });

    it('should return false (violation assumed) on ANY error', () => {
      const result = failClosedIntegrity(
        () => { throw new Error('crypto engine crashed'); },
        'hmac-crash'
      );
      // cLaw: error during integrity check → assume violated → safe mode
      expect(result).toBe(false);
    });
  });
});
