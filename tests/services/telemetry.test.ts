/**
 * telemetry.ts — Unit tests for the local telemetry engine.
 *
 * Tests event recording, aggregation, category filtering, MAX_EVENTS cap,
 * recent-event retrieval, disk persistence, and privacy clear.
 * Electron and fs/promises are fully mocked.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ── Mocks ──────────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  readFile: vi.fn(),
  writeFile: vi.fn(),
  mkdir: vi.fn(),
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: mocks.readFile,
    writeFile: mocks.writeFile,
    mkdir: mocks.mkdir,
  },
  readFile: mocks.readFile,
  writeFile: mocks.writeFile,
  mkdir: mocks.mkdir,
}));

vi.mock('electron', () => ({
  app: { getPath: () => '/mock/userData' },
}));

// ── Import helper ──────────────────────────────────────────────────

type TelemetryEngine = typeof import('../../src/main/telemetry')['telemetryEngine'];

let telemetryEngine: TelemetryEngine;

async function freshEngine(): Promise<TelemetryEngine> {
  vi.resetModules();
  const mod = await import('../../src/main/telemetry');
  return mod.telemetryEngine;
}

// ── Tests ──────────────────────────────────────────────────────────

describe('TelemetryEngine', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    mocks.readFile.mockRejectedValue(new Error('ENOENT'));
    mocks.writeFile.mockResolvedValue(undefined);
    mocks.mkdir.mockResolvedValue(undefined);
    telemetryEngine = await freshEngine();
  });

  afterEach(async () => {
    // Shut down to clear the flush interval
    await telemetryEngine.shutdown();
    vi.useRealTimers();
  });

  // ── Recording events ──────────────────────────────────────────

  describe('record()', () => {
    it('adds an event with the correct category, action, label, value, and timestamp', async () => {
      await telemetryEngine.initialize();

      vi.setSystemTime(new Date('2026-03-22T10:00:00Z'));
      telemetryEngine.record('app-launch', 'started', 'cold-boot', 42);

      const events = telemetryEngine.getRecentEvents(10);
      expect(events).toHaveLength(1);
      expect(events[0]).toEqual({
        category: 'app-launch',
        action: 'started',
        label: 'cold-boot',
        value: 42,
        timestamp: Date.now(),
      });
    });

    it('records events without optional label and value', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('renderer-error', 'crash');

      const events = telemetryEngine.getRecentEvents(10);
      expect(events).toHaveLength(1);
      expect(events[0].category).toBe('renderer-error');
      expect(events[0].action).toBe('crash');
      expect(events[0].label).toBeUndefined();
      expect(events[0].value).toBeUndefined();
    });

    it('records multiple events in order', async () => {
      await telemetryEngine.initialize();

      vi.setSystemTime(new Date('2026-03-22T10:00:00Z'));
      telemetryEngine.record('app-launch', 'started');
      vi.setSystemTime(new Date('2026-03-22T10:01:00Z'));
      telemetryEngine.record('voice-path', 'gemini');
      vi.setSystemTime(new Date('2026-03-22T10:02:00Z'));
      telemetryEngine.record('voice-fallback', 'local');

      const events = telemetryEngine.getRecentEvents(10);
      expect(events).toHaveLength(3);
      expect(events[0].category).toBe('app-launch');
      expect(events[1].category).toBe('voice-path');
      expect(events[2].category).toBe('voice-fallback');
      expect(events[0].timestamp).toBeLessThan(events[1].timestamp);
      expect(events[1].timestamp).toBeLessThan(events[2].timestamp);
    });

    it('marks data as dirty so flush writes to disk', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');
      await telemetryEngine.flush();

      expect(mocks.writeFile).toHaveBeenCalledTimes(1);
    });
  });

  // ── Aggregation ───────────────────────────────────────────────

  describe('getAggregates()', () => {
    it('returns counts, firstSeen, and lastSeen per category:action key', async () => {
      await telemetryEngine.initialize();

      vi.setSystemTime(new Date('2026-03-22T08:00:00Z'));
      const t1 = Date.now();
      telemetryEngine.record('app-launch', 'started');

      vi.setSystemTime(new Date('2026-03-22T09:00:00Z'));
      telemetryEngine.record('app-launch', 'started');

      vi.setSystemTime(new Date('2026-03-22T10:00:00Z'));
      const t3 = Date.now();
      telemetryEngine.record('app-launch', 'started');

      const agg = telemetryEngine.getAggregates();
      const entry = agg['app-launch:started'];

      expect(entry).toBeDefined();
      expect(entry.count).toBe(3);
      expect(entry.firstSeen).toBe(t1);
      expect(entry.lastSeen).toBe(t3);
    });

    it('creates separate aggregate keys for different actions', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('voice-path', 'gemini');
      telemetryEngine.record('voice-path', 'local');
      telemetryEngine.record('voice-path', 'gemini');

      const agg = telemetryEngine.getAggregates();
      expect(agg['voice-path:gemini'].count).toBe(2);
      expect(agg['voice-path:local'].count).toBe(1);
    });

    it('includes label in the aggregate key when provided', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('voice-fallback', 'timeout', 'stt-slow');
      telemetryEngine.record('voice-fallback', 'timeout', 'stt-slow');
      telemetryEngine.record('voice-fallback', 'timeout');

      const agg = telemetryEngine.getAggregates();
      expect(agg['voice-fallback:timeout:stt-slow'].count).toBe(2);
      expect(agg['voice-fallback:timeout'].count).toBe(1);
    });

    it('returns a shallow copy so external mutation does not affect internals', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');
      const agg = telemetryEngine.getAggregates();
      delete agg['app-launch:started'];

      const agg2 = telemetryEngine.getAggregates();
      expect(agg2['app-launch:started']).toBeDefined();
    });
  });

  // ── Category filtering ────────────────────────────────────────

  describe('category filtering', () => {
    beforeEach(async () => {
      await telemetryEngine.initialize();
      telemetryEngine.record('app-launch', 'started');
      telemetryEngine.record('voice-path', 'gemini');
      telemetryEngine.record('voice-path', 'local');
      telemetryEngine.record('voice-fallback', 'timeout');
      telemetryEngine.record('renderer-error', 'crash');
    });

    it('getAggregates(category) returns only matching entries', () => {
      const agg = telemetryEngine.getAggregates('voice-path');
      const keys = Object.keys(agg);

      expect(keys).toHaveLength(2);
      expect(keys.every((k) => k.startsWith('voice-path:'))).toBe(true);
    });

    it('getAggregates(category) returns empty object for category with no events', () => {
      const agg = telemetryEngine.getAggregates('voice-transition');
      expect(Object.keys(agg)).toHaveLength(0);
    });

    it('getRecentEvents(count, category) returns only events for that category', () => {
      const events = telemetryEngine.getRecentEvents(50, 'voice-path');

      expect(events).toHaveLength(2);
      expect(events.every((e) => e.category === 'voice-path')).toBe(true);
    });

    it('getRecentEvents(count, category) returns empty array for category with no events', () => {
      const events = telemetryEngine.getRecentEvents(50, 'voice-transition');
      expect(events).toHaveLength(0);
    });
  });

  // ── MAX_EVENTS cap ────────────────────────────────────────────

  describe('MAX_EVENTS cap (1000)', () => {
    it('trims oldest events when exceeding 1000', async () => {
      await telemetryEngine.initialize();

      for (let i = 0; i < 1005; i++) {
        vi.setSystemTime(new Date(2026, 0, 1, 0, 0, i));
        telemetryEngine.record('app-launch', `action-${i}`);
      }

      const events = telemetryEngine.getRecentEvents(2000);
      expect(events).toHaveLength(1000);
      // The oldest surviving event should be action-5 (indices 0-4 trimmed)
      expect(events[0].action).toBe('action-5');
      expect(events[events.length - 1].action).toBe('action-1004');
    });

    it('trims loaded events that exceed MAX_EVENTS during initialize', async () => {
      // Prepare a saved state with 1200 events
      const savedEvents = Array.from({ length: 1200 }, (_, i) => ({
        category: 'app-launch' as const,
        action: `loaded-${i}`,
        timestamp: 1000 + i,
      }));
      mocks.readFile.mockResolvedValue(
        JSON.stringify({ events: savedEvents, aggregates: {} }),
      );

      telemetryEngine = await freshEngine();
      await telemetryEngine.initialize();

      const events = telemetryEngine.getRecentEvents(2000);
      expect(events).toHaveLength(1000);
      // The oldest surviving should be loaded-200 (indices 0-199 trimmed)
      expect(events[0].action).toBe('loaded-200');
    });
  });

  // ── Recent events ─────────────────────────────────────────────

  describe('getRecentEvents()', () => {
    it('returns the N most recent events', async () => {
      await telemetryEngine.initialize();

      for (let i = 0; i < 10; i++) {
        vi.setSystemTime(new Date(2026, 0, 1, 0, 0, i));
        telemetryEngine.record('app-launch', `evt-${i}`);
      }

      const recent = telemetryEngine.getRecentEvents(3);
      expect(recent).toHaveLength(3);
      expect(recent[0].action).toBe('evt-7');
      expect(recent[1].action).toBe('evt-8');
      expect(recent[2].action).toBe('evt-9');
    });

    it('returns all events when count exceeds total', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'a');
      telemetryEngine.record('app-launch', 'b');

      const recent = telemetryEngine.getRecentEvents(100);
      expect(recent).toHaveLength(2);
    });

    it('defaults to 50 when no count is provided', async () => {
      await telemetryEngine.initialize();

      for (let i = 0; i < 60; i++) {
        telemetryEngine.record('app-launch', `evt-${i}`);
      }

      const recent = telemetryEngine.getRecentEvents();
      expect(recent).toHaveLength(50);
      expect(recent[0].action).toBe('evt-10');
    });
  });

  // ── Persistence ───────────────────────────────────────────────

  describe('persistence', () => {
    it('initialize() loads events from disk', async () => {
      const savedData = {
        events: [
          { category: 'app-launch', action: 'started', timestamp: 1000 },
          { category: 'voice-path', action: 'gemini', timestamp: 2000 },
        ],
        aggregates: {
          'app-launch:started': { count: 1, firstSeen: 1000, lastSeen: 1000 },
        },
      };
      mocks.readFile.mockResolvedValue(JSON.stringify(savedData));

      telemetryEngine = await freshEngine();
      await telemetryEngine.initialize();

      const events = telemetryEngine.getRecentEvents(10);
      expect(events).toHaveLength(2);
      expect(events[0].category).toBe('app-launch');
      expect(events[1].category).toBe('voice-path');
    });

    it('initialize() loads aggregates from disk', async () => {
      const savedData = {
        events: [],
        aggregates: {
          'app-launch:started': { count: 5, firstSeen: 1000, lastSeen: 5000 },
        },
      };
      mocks.readFile.mockResolvedValue(JSON.stringify(savedData));

      telemetryEngine = await freshEngine();
      await telemetryEngine.initialize();

      const agg = telemetryEngine.getAggregates();
      expect(agg['app-launch:started'].count).toBe(5);
    });

    it('initialize() starts with empty data when file does not exist', async () => {
      mocks.readFile.mockRejectedValue(new Error('ENOENT'));

      telemetryEngine = await freshEngine();
      await telemetryEngine.initialize();

      expect(telemetryEngine.getRecentEvents(10)).toHaveLength(0);
      expect(Object.keys(telemetryEngine.getAggregates())).toHaveLength(0);
    });

    it('shutdown() flushes dirty data to disk', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');
      await telemetryEngine.shutdown();

      expect(mocks.writeFile).toHaveBeenCalledTimes(1);
      const writtenPath = mocks.writeFile.mock.calls[0][0];
      expect(writtenPath).toContain('telemetry.json');

      const writtenData = JSON.parse(mocks.writeFile.mock.calls[0][1]);
      expect(writtenData.events).toHaveLength(1);
      expect(writtenData.events[0].category).toBe('app-launch');
    });

    it('shutdown() clears the periodic flush timer', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');
      await telemetryEngine.shutdown();
      mocks.writeFile.mockClear();

      // Advance past the 30s flush interval — should NOT trigger another write
      vi.advanceTimersByTime(60_000);
      // Give any pending promises time to settle
      await vi.runAllTimersAsync();

      expect(mocks.writeFile).not.toHaveBeenCalled();
    });

    it('flush() is a no-op when data is not dirty', async () => {
      await telemetryEngine.initialize();
      await telemetryEngine.flush();

      expect(mocks.writeFile).not.toHaveBeenCalled();
    });

    it('flush() is a no-op when filePath is not set (before initialize)', async () => {
      // Do not call initialize — filePath stays empty
      telemetryEngine.record('app-launch', 'started');
      await telemetryEngine.flush();

      expect(mocks.writeFile).not.toHaveBeenCalled();
    });

    it('flush() silently handles write errors', async () => {
      await telemetryEngine.initialize();

      mocks.writeFile.mockRejectedValue(new Error('EACCES'));
      telemetryEngine.record('app-launch', 'started');

      // Should not throw
      await expect(telemetryEngine.flush()).resolves.toBeUndefined();
    });

    it('periodic flush fires every 30 seconds', async () => {
      mocks.writeFile.mockResolvedValue(undefined);
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'tick');

      // Advance 30 seconds to trigger the first periodic flush
      await vi.advanceTimersByTimeAsync(30_000);

      expect(mocks.writeFile).toHaveBeenCalledTimes(1);
    });
  });

  // ── Clear ─────────────────────────────────────────────────────

  describe('clear()', () => {
    it('resets events to empty', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');
      telemetryEngine.record('voice-path', 'gemini');
      expect(telemetryEngine.getRecentEvents(10)).toHaveLength(2);

      await telemetryEngine.clear();

      expect(telemetryEngine.getRecentEvents(10)).toHaveLength(0);
    });

    it('resets aggregates to empty', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');
      expect(Object.keys(telemetryEngine.getAggregates())).toHaveLength(1);

      await telemetryEngine.clear();

      expect(Object.keys(telemetryEngine.getAggregates())).toHaveLength(0);
    });

    it('flushes the cleared state to disk immediately', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');
      mocks.writeFile.mockClear();

      await telemetryEngine.clear();

      expect(mocks.writeFile).toHaveBeenCalledTimes(1);
      const writtenData = JSON.parse(mocks.writeFile.mock.calls[0][1]);
      expect(writtenData.events).toHaveLength(0);
      expect(writtenData.aggregates).toEqual({});
    });
  });

  // ── Aggregate key format ──────────────────────────────────────

  describe('aggregate key format', () => {
    it('uses category:action when no label is provided', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('app-launch', 'started');

      const keys = Object.keys(telemetryEngine.getAggregates());
      expect(keys).toEqual(['app-launch:started']);
    });

    it('uses category:action:label when label is provided', async () => {
      await telemetryEngine.initialize();

      telemetryEngine.record('voice-fallback', 'timeout', 'stt-slow');

      const keys = Object.keys(telemetryEngine.getAggregates());
      expect(keys).toEqual(['voice-fallback:timeout:stt-slow']);
    });
  });
});
