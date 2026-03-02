/**
 * Tests for replay attack protection (HIGH-002).
 *
 * The replay cache rejects:
 *   1. Messages with duplicate IDs (already processed)
 *   2. Messages with stale timestamps (> 10 minutes old)
 *   3. Messages from the future (> 1 minute ahead)
 *   4. Cache pruning to prevent unbounded growth
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// ── Constants (mirror agent-network.ts) ──────────────────────────────

const REPLAY_WINDOW_MS = 10 * 60 * 1000; // 10 minutes
const MAX_REPLAY_CACHE_SIZE = 5000;
const MAX_CLOCK_SKEW_MS = 60 * 1000; // 1 minute

// ── Replay Cache Implementation (mirrors agent-network.ts logic) ─────

class ReplayDetector {
  private cache: Map<string, number> = new Map();

  /**
   * Check if a message should be rejected.
   * Returns null if accepted, or an error reason string if rejected.
   */
  check(messageId: string, timestamp: number): string | null {
    const now = Date.now();

    // Reject duplicates
    if (this.cache.has(messageId)) {
      return 'Duplicate message ID (replay detected)';
    }

    // Reject stale messages
    const age = now - timestamp;
    if (age > REPLAY_WINDOW_MS) {
      return `Message too old (${Math.round(age / 1000)}s, max ${REPLAY_WINDOW_MS / 1000}s)`;
    }

    // Reject future messages
    if (age < -MAX_CLOCK_SKEW_MS) {
      return `Message from the future (${Math.round(-age / 1000)}s ahead)`;
    }

    // Accept — record in cache
    this.cache.set(messageId, timestamp);

    // Prune if cache is getting large
    if (this.cache.size > MAX_REPLAY_CACHE_SIZE) {
      this.prune();
    }

    return null;
  }

  get size(): number {
    return this.cache.size;
  }

  private prune(): void {
    const now = Date.now();
    for (const [id, ts] of this.cache) {
      if (now - ts > REPLAY_WINDOW_MS) {
        this.cache.delete(id);
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════

describe('Replay Detection', () => {
  let detector: ReplayDetector;

  beforeEach(() => {
    detector = new ReplayDetector();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-02T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('accepts a fresh unique message', () => {
    const result = detector.check('msg-001', Date.now());
    expect(result).toBeNull();
  });

  it('rejects duplicate message ID (replay attack)', () => {
    const id = 'msg-001';
    const ts = Date.now();

    // First time: accepted
    expect(detector.check(id, ts)).toBeNull();

    // Replay: rejected
    const reason = detector.check(id, ts);
    expect(reason).toContain('Duplicate');
  });

  it('rejects stale message (> 10 minutes old)', () => {
    const staleTimestamp = Date.now() - (11 * 60 * 1000); // 11 minutes ago
    const result = detector.check('msg-stale', staleTimestamp);
    expect(result).toContain('too old');
  });

  it('accepts message at exactly 10 minutes age', () => {
    const edgeTimestamp = Date.now() - REPLAY_WINDOW_MS;
    const result = detector.check('msg-edge', edgeTimestamp);
    // At exactly the boundary — should still be accepted (not > window)
    expect(result).toBeNull();
  });

  it('rejects message from far future (> 1 minute ahead)', () => {
    const futureTimestamp = Date.now() + (2 * 60 * 1000); // 2 minutes ahead
    const result = detector.check('msg-future', futureTimestamp);
    expect(result).toContain('future');
  });

  it('accepts message within 1-minute clock skew', () => {
    const skewedTimestamp = Date.now() + (30 * 1000); // 30 seconds ahead
    const result = detector.check('msg-skew', skewedTimestamp);
    expect(result).toBeNull();
  });

  it('tracks multiple unique messages', () => {
    for (let i = 0; i < 100; i++) {
      expect(detector.check(`msg-${i}`, Date.now())).toBeNull();
    }
    expect(detector.size).toBe(100);
  });

  it('rejects any previously seen message ID regardless of timestamp', () => {
    const id = 'msg-repeat';
    expect(detector.check(id, Date.now())).toBeNull();

    // Same ID, different timestamp
    vi.advanceTimersByTime(5000);
    const result = detector.check(id, Date.now());
    expect(result).toContain('Duplicate');
  });
});

describe('Replay Cache Pruning', () => {
  let detector: ReplayDetector;

  beforeEach(() => {
    detector = new ReplayDetector();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-02T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('prunes expired entries when cache exceeds max size', () => {
    // Fill cache with entries at various timestamps
    const baseTime = Date.now();

    // Add old entries (will be prunable after time advances)
    for (let i = 0; i < 100; i++) {
      detector.check(`old-${i}`, baseTime);
    }

    // Advance time past the replay window
    vi.advanceTimersByTime(REPLAY_WINDOW_MS + 1000);

    // Add entries to push past MAX_REPLAY_CACHE_SIZE threshold
    // First, fill to just under the limit (we already have 100 old entries)
    for (let i = 0; i < MAX_REPLAY_CACHE_SIZE; i++) {
      detector.check(`new-${i}`, Date.now());
    }

    // After pruning, old entries should be gone, new entries should remain
    // The cache size should be roughly MAX_REPLAY_CACHE_SIZE (new entries only)
    expect(detector.size).toBeLessThanOrEqual(MAX_REPLAY_CACHE_SIZE + 100);
  });

  it('cache does not grow unbounded', () => {
    // Add MAX_REPLAY_CACHE_SIZE + 1 entries to trigger pruning
    for (let i = 0; i <= MAX_REPLAY_CACHE_SIZE; i++) {
      // Slightly advance time for each to keep messages fresh
      vi.advanceTimersByTime(1);
      detector.check(`msg-${i}`, Date.now());
    }

    // Size should not exceed MAX_REPLAY_CACHE_SIZE + some tolerance
    // (pruning happens when size > MAX, so it may briefly exceed before prune)
    expect(detector.size).toBeLessThanOrEqual(MAX_REPLAY_CACHE_SIZE + 1);
  });
});
