/**
 * Track VIII — Foundation: Error Resilience & Safety Boundary Tests
 *
 * Extended tests beyond the baseline error-taxonomy.test.ts.
 * Validates: exhaustive classifyError patterns, advanced retry logic,
 * fail-closed safety invariants, error serialization, and integration patterns.
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

// ── classifyError — Exhaustive Pattern Coverage ─────────────────────

describe('classifyError — exhaustive pattern coverage', () => {
  const persistentPatterns = [
    'Invalid API key',
    'API key expired',
    'unauthorized access denied',
    'Unauthorized: check credentials',
    'HTTP 403 Forbidden',
    'authentication failed',
    'AUTHENTICATION required',
  ];

  for (const msg of persistentPatterns) {
    it(`classifies "${msg}" as persistent`, () => {
      const err = classifyError('claude', new Error(msg));
      expect(err.category).toBe('persistent');
      expect(err.retryable).toBe(false);
    });
  }

  const recoverablePatterns = [
    'rate limit exceeded',
    'Rate Limit hit',
    'HTTP 429 Too Many Requests',
    'too many requests',
    'quota exceeded',
  ];

  for (const msg of recoverablePatterns) {
    it(`classifies "${msg}" as recoverable`, () => {
      const err = classifyError('openrouter', new Error(msg));
      expect(err.category).toBe('recoverable');
      expect(err).toBeInstanceOf(RecoverableError);
    });
  }

  const transientPatterns = [
    'connect ECONNREFUSED',
    'connect ECONNRESET',
    'ETIMEDOUT',
    'ENOTFOUND',
    'fetch failed',
    'Request timeout after 30s',
    'socket hang up',
    'network error',
  ];

  for (const msg of transientPatterns) {
    it(`classifies "${msg}" as transient`, () => {
      const err = classifyError('gemini', new Error(msg));
      expect(err.category).toBe('transient');
      expect(err.retryable).toBe(true);
    });
  }

  it('case-insensitive matching: "UNAUTHORIZED" → persistent', () => {
    const err = classifyError('claude', new Error('UNAUTHORIZED'));
    expect(err.category).toBe('persistent');
  });

  it('case-insensitive matching: "Socket Hang Up" → transient', () => {
    const err = classifyError('gemini', new Error('Socket Hang Up'));
    expect(err.category).toBe('transient');
  });

  it('extracts retry-after seconds from message', () => {
    const err = classifyError('claude', new Error('rate limit hit, retry after: 60'));
    expect(err).toBeInstanceOf(RecoverableError);
    expect((err as RecoverableError).retryAfterMs).toBe(60_000);
  });

  it('defaults retryAfterMs to 30000 when no retry hint', () => {
    const err = classifyError('claude', new Error('rate limit exceeded'));
    expect(err).toBeInstanceOf(RecoverableError);
    expect((err as RecoverableError).retryAfterMs).toBe(30_000);
  });

  it('classifies unknown messages as transient (optimistic default)', () => {
    const err = classifyError('unknown', new Error('xyzzy gobbledygook'));
    expect(err.category).toBe('transient');
    expect(err.retryable).toBe(true);
  });

  it('handles null/undefined/number errors', () => {
    expect(classifyError('x', null as any)).toBeInstanceOf(AgentFridayError);
    expect(classifyError('x', undefined as any)).toBeInstanceOf(AgentFridayError);
    expect(classifyError('x', 42 as any)).toBeInstanceOf(AgentFridayError);
  });

  it('passes through existing AgentFridayError instances', () => {
    const original = new TransientError('gemini', 'already classified');
    const classified = classifyError('gemini', original);
    expect(classified).toBe(original);
  });
});

// ── withRetry — Advanced Retry Logic ────────────────────────────────

describe('withRetry — advanced retry logic', () => {
  it('returns value on first success without delay', async () => {
    const start = Date.now();
    const result = await withRetry('gemini', async () => 'instant', {
      maxAttempts: 3, baseDelayMs: 5000,
    });
    expect(result).toBe('instant');
    expect(Date.now() - start).toBeLessThan(1000);
  });

  it('retries transient errors up to maxAttempts', async () => {
    let attempts = 0;
    const result = await withRetry('gemini', async () => {
      attempts++;
      if (attempts < 3) throw new Error('ECONNREFUSED');
      return 'recovered';
    }, { maxAttempts: 3, baseDelayMs: 10 });

    expect(result).toBe('recovered');
    expect(attempts).toBe(3);
  });

  it('throws persistent errors IMMEDIATELY without retry', async () => {
    let attempts = 0;
    await expect(
      withRetry('claude', async () => {
        attempts++;
        throw new Error('Invalid API key');
      }, { maxAttempts: 5, baseDelayMs: 10 })
    ).rejects.toThrow();
    expect(attempts).toBe(1);
  });

  it('does NOT retry FatalIntegrityError', async () => {
    let attempts = 0;
    await expect(
      withRetry('integrity', async () => {
        attempts++;
        throw new FatalIntegrityError('integrity', 'HMAC tampered');
      }, { maxAttempts: 5, baseDelayMs: 10 })
    ).rejects.toThrow(FatalIntegrityError);
    expect(attempts).toBe(1);
  });

  it('exponential backoff: delays increase on each retry', async () => {
    const delays: number[] = [];
    try {
      await withRetry('network', async () => {
        throw new Error('timeout');
      }, {
        maxAttempts: 4, baseDelayMs: 100, maxDelayMs: 100_000,
        onRetry: (_attempt, delay) => { delays.push(delay); },
      });
    } catch { /* expected */ }
    expect(delays.length).toBe(3);
    expect(delays[1]).toBeGreaterThan(delays[0]);
    expect(delays[2]).toBeGreaterThan(delays[1]);
  });

  it('respects maxDelayMs cap', async () => {
    const delays: number[] = [];
    try {
      await withRetry('gemini', async () => {
        throw new Error('fetch failed');
      }, {
        maxAttempts: 6, baseDelayMs: 1000, maxDelayMs: 3000,
        onRetry: (_attempt, delay) => { delays.push(delay); },
      });
    } catch { /* expected */ }
    for (const d of delays) {
      expect(d).toBeLessThanOrEqual(3000);
    }
  });

  it('uses RecoverableError.retryAfterMs when larger than backoff', async () => {
    const delays: number[] = [];
    try {
      await withRetry('claude', async () => {
        throw new RecoverableError('claude', 'rate limited', { retryAfterMs: 5000 });
      }, {
        maxAttempts: 2, baseDelayMs: 10, maxDelayMs: 100_000,
        onRetry: (_attempt, delay) => { delays.push(delay); },
      });
    } catch { /* expected */ }
    expect(delays[0]).toBeGreaterThanOrEqual(5000);
  });

  it('calls onRetry with correct arguments', async () => {
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

  it('throws after maxAttempts exhausted', async () => {
    await expect(
      withRetry('network', async () => {
        throw new Error('ECONNREFUSED');
      }, { maxAttempts: 2, baseDelayMs: 10 })
    ).rejects.toThrow('ECONNREFUSED');
  });

  it('with maxAttempts=1, no retry and no onRetry call', async () => {
    const onRetry = vi.fn();
    await expect(
      withRetry('gemini', async () => { throw new Error('fetch failed'); },
        { maxAttempts: 1, baseDelayMs: 10, onRetry })
    ).rejects.toThrow();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it('mixed retryable then non-retryable stops at non-retryable', async () => {
    const errors = [new Error('ECONNRESET'), new Error('Invalid API key')];
    let call = 0;
    await expect(
      withRetry('claude', async () => { throw errors[call++]; },
        { maxAttempts: 10, baseDelayMs: 10 })
    ).rejects.toThrow(PersistentError);
    expect(call).toBe(2);
  });

  it('returns correct generic type', async () => {
    expect(await withRetry('g', async () => 42)).toBe(42);
    expect(await withRetry('g', async () => null)).toBeNull();
    expect(await withRetry('g', async () => ({ ok: true }))).toEqual({ ok: true });
  });
});

// ── failClosedTrust — Comprehensive cLaw Enforcement ────────────────

describe('failClosedTrust — comprehensive cLaw enforcement', () => {
  it('returns computed value on success', () => {
    expect(failClosedTrust(() => 'owner-dm', 'public', 'test')).toBe('owner-dm');
  });

  it('returns fallback on TypeError', () => {
    const result = failClosedTrust(() => {
      const obj: any = undefined;
      return obj.nonExistent.prop;
    }, 'public' as const, 'typeerror');
    expect(result).toBe('public');
  });

  it('returns fallback on RangeError', () => {
    const result = failClosedTrust(() => { throw new RangeError('out of range'); }, 'restricted', 'range');
    expect(result).toBe('restricted');
  });

  it('returns fallback on thrown string', () => {
    const result = failClosedTrust(() => { throw 'oops'; }, 'deny-all', 'string-throw');
    expect(result).toBe('deny-all');
  });

  it('returns fallback on thrown null', () => {
    const result = failClosedTrust(() => { throw null; }, 'fallback', 'null-throw');
    expect(result).toBe('fallback');
  });

  it('returns actual value (null) when fn returns null without error', () => {
    expect(failClosedTrust(() => null, 'fallback', 'null-return')).toBeNull();
  });

  it('returns fallback on FatalIntegrityError', () => {
    const result = failClosedTrust(
      () => { throw new FatalIntegrityError('integrity', 'hmac'); },
      'public', 'fatal'
    );
    expect(result).toBe('public');
  });

  it('supports complex fallback types', () => {
    const fallback = { tier: 'public', perms: [] as string[] };
    const result = failClosedTrust(() => { throw new Error('crash'); }, fallback, 'complex');
    expect(result).toBe(fallback);
  });
});

// ── failClosedIntegrity — Comprehensive cLaw Enforcement ────────────

describe('failClosedIntegrity — comprehensive cLaw enforcement', () => {
  it('returns true when integrity check passes', () => {
    expect(failClosedIntegrity(() => true, 'hmac-ok')).toBe(true);
  });

  it('returns false when integrity check fails', () => {
    expect(failClosedIntegrity(() => false, 'hmac-fail')).toBe(false);
  });

  it('returns false on ANY thrown Error', () => {
    expect(failClosedIntegrity(() => { throw new Error('crash'); }, 'err')).toBe(false);
  });

  it('returns false on TypeError', () => {
    expect(failClosedIntegrity(() => {
      const x: any = null; return x.verify();
    }, 'type')).toBe(false);
  });

  it('NEVER returns true on error — safety sweep', () => {
    const errorFns = [
      () => { throw new Error('generic'); },
      () => { throw new TypeError('type'); },
      () => { throw new RangeError('range'); },
      () => { throw new FatalIntegrityError('integrity', 'fatal'); },
      () => { throw null; },
      () => { throw undefined; },
      () => { throw 0; },
      () => { throw ''; },
    ];
    for (const fn of errorFns) {
      expect(failClosedIntegrity(fn as () => boolean, 'sweep')).toBe(false);
    }
  });
});

// ── Error Serialization ─────────────────────────────────────────────

describe('Error serialization — toJSON', () => {
  it('produces complete JSON with all fields', () => {
    const err = new AgentFridayError({ message: 'test', category: 'transient', source: 'gemini' });
    const json = err.toJSON();
    expect(json).toHaveProperty('name');
    expect(json).toHaveProperty('message');
    expect(json).toHaveProperty('category');
    expect(json).toHaveProperty('source');
    expect(json).toHaveProperty('retryable');
    expect(json).toHaveProperty('userFacing');
    expect(json).toHaveProperty('userMessage');
    expect(json).toHaveProperty('timestamp');
  });

  it('JSON.stringify round-trips without circular reference', () => {
    const err = new TransientError('network', 'socket hang up');
    const jsonStr = JSON.stringify(err.toJSON());
    const parsed = JSON.parse(jsonStr);
    expect(parsed.name).toBe('TransientError');
    expect(parsed.category).toBe('transient');
  });

  it('all error types produce distinct name fields', () => {
    const names = new Set([
      new AgentFridayError({ message: 'a', category: 'transient', source: 'x' }).toJSON().name,
      new TransientError('x', 'b').toJSON().name,
      new PersistentError('x', 'c').toJSON().name,
      new RecoverableError('x', 'd').toJSON().name,
      new FatalIntegrityError('x', 'e').toJSON().name,
    ]);
    expect(names.size).toBe(5);
  });

  it('userMessage is always defined and non-empty', () => {
    const errors = [
      new TransientError('gemini', 'a'),
      new PersistentError('claude', 'b'),
      new RecoverableError('openrouter', 'c'),
      new FatalIntegrityError('integrity', 'd'),
    ];
    for (const err of errors) {
      expect(err.userMessage.length).toBeGreaterThan(0);
    }
  });

  it('TransientError userMessage mentions "temporary"', () => {
    const err = new TransientError('gemini', 'connection lost');
    expect(err.userMessage.toLowerCase()).toContain('temporary');
  });

  it('FatalIntegrityError userMessage mentions "safe mode"', () => {
    const err = new FatalIntegrityError('integrity', 'HMAC failure');
    expect(err.userMessage.toLowerCase()).toContain('safe mode');
  });
});

// ── Taxonomy Consistency ────────────────────────────────────────────

describe('Error taxonomy consistency', () => {
  it('all error types extend AgentFridayError', () => {
    expect(new TransientError('x', 'a')).toBeInstanceOf(AgentFridayError);
    expect(new PersistentError('x', 'b')).toBeInstanceOf(AgentFridayError);
    expect(new RecoverableError('x', 'c')).toBeInstanceOf(AgentFridayError);
    expect(new FatalIntegrityError('x', 'd')).toBeInstanceOf(AgentFridayError);
  });

  it('all error types extend native Error', () => {
    expect(new TransientError('x', 'a')).toBeInstanceOf(Error);
    expect(new PersistentError('x', 'b')).toBeInstanceOf(Error);
    expect(new RecoverableError('x', 'c')).toBeInstanceOf(Error);
    expect(new FatalIntegrityError('x', 'd')).toBeInstanceOf(Error);
  });

  it('TransientError is always retryable', () => {
    expect(new TransientError('x', 'a').retryable).toBe(true);
  });

  it('PersistentError is always non-retryable and userFacing', () => {
    const err = new PersistentError('x', 'a');
    expect(err.retryable).toBe(false);
    expect(err.userFacing).toBe(true);
  });

  it('RecoverableError has retryAfterMs > 0', () => {
    const err = new RecoverableError('x', 'a');
    expect(err.retryable).toBe(true);
    expect(err.retryAfterMs).toBeGreaterThan(0);
  });

  it('FatalIntegrityError is non-retryable + userFacing + fatal', () => {
    const err = new FatalIntegrityError('x', 'a');
    expect(err.retryable).toBe(false);
    expect(err.userFacing).toBe(true);
    expect(err.category).toBe('fatal');
  });

  it('AgentFridayError defaults retryable based on category', () => {
    expect(new AgentFridayError({ message: 'x', category: 'transient', source: 'x' }).retryable).toBe(true);
    expect(new AgentFridayError({ message: 'x', category: 'persistent', source: 'x' }).retryable).toBe(false);
    expect(new AgentFridayError({ message: 'x', category: 'fatal', source: 'x' }).retryable).toBe(false);
  });
});
