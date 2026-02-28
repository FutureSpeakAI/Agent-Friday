/**
 * Tests for context-stream.ts — Track III Phase 1: Activity Ingestion.
 * The Nervous System event bus: unified context stream with throttling,
 * deduplication, snapshot aggregation, and context string generation.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ContextStream } from '../../src/main/context-stream';
import type {
  ContextEvent,
  ContextEventType,
  ContextSnapshot,
  ContextStreamConfig,
} from '../../src/main/context-stream';

// ── Helper ───────────────────────────────────────────────────────────

function makeEvent(
  type: ContextEventType,
  summary: string,
  data: Record<string, unknown> = {},
  opts: Partial<Pick<ContextEvent, 'dedupeKey' | 'ttlMs'>> = {},
): Omit<ContextEvent, 'id' | 'timestamp'> {
  return {
    type,
    source: `test-${type}`,
    summary,
    data,
    ...opts,
  };
}

describe('Context Stream — Track III Phase 1', () => {
  let stream: ContextStream;

  beforeEach(() => {
    // Disable throttling for tests (set all to 0)
    const noThrottle: Record<ContextEventType, number> = {
      ambient: 0, clipboard: 0, sentiment: 0, notification: 0,
      'tool-invoke': 0, calendar: 0, communication: 0, git: 0,
      'screen-text': 0, 'user-input': 0, system: 0,
    };
    stream = new ContextStream({ throttleMs: noThrottle });
  });

  // ── Core Push ────────────────────────────────────────────────────
  describe('Core Event Push', () => {
    it('pushes an event and returns it with id and timestamp', () => {
      const result = stream.push(makeEvent('ambient', 'User focused VS Code'));
      expect(result).not.toBeNull();
      expect(result!.id).toMatch(/^ctx-/);
      expect(result!.timestamp).toBeGreaterThan(0);
      expect(result!.summary).toBe('User focused VS Code');
    });

    it('increments buffer size on push', () => {
      expect(stream.getBufferSize()).toBe(0);
      stream.push(makeEvent('ambient', 'test'));
      expect(stream.getBufferSize()).toBe(1);
      stream.push(makeEvent('clipboard', 'test2'));
      expect(stream.getBufferSize()).toBe(2);
    });

    it('returns null when disabled', () => {
      stream.setEnabled(false);
      const result = stream.push(makeEvent('ambient', 'test'));
      expect(result).toBeNull();
      expect(stream.getBufferSize()).toBe(0);
    });

    it('truncates long summaries', () => {
      const longSummary = 'x'.repeat(500);
      const result = stream.push(makeEvent('ambient', longSummary));
      expect(result!.summary.length).toBeLessThanOrEqual(200);
    });

    it('generates unique IDs for consecutive events', () => {
      const ids = new Set<string>();
      for (let i = 0; i < 100; i++) {
        const result = stream.push(makeEvent('system', `event-${i}`));
        ids.add(result!.id);
      }
      expect(ids.size).toBe(100);
    });
  });

  // ── Throttling ──────────────────────────────────────────────────
  describe('Throttling', () => {
    it('throttles events that come too fast', () => {
      const throttled = new ContextStream({
        throttleMs: {
          ambient: 1000, clipboard: 0, sentiment: 0, notification: 0,
          'tool-invoke': 0, calendar: 0, communication: 0, git: 0,
          'screen-text': 0, 'user-input': 0, system: 0,
        },
      });

      const first = throttled.push(makeEvent('ambient', 'first'));
      expect(first).not.toBeNull();

      // Second push within throttle window should be rejected
      const second = throttled.push(makeEvent('ambient', 'second'));
      expect(second).toBeNull();
    });

    it('allows events after throttle window', async () => {
      const throttled = new ContextStream({
        throttleMs: {
          ambient: 50, clipboard: 0, sentiment: 0, notification: 0,
          'tool-invoke': 0, calendar: 0, communication: 0, git: 0,
          'screen-text': 0, 'user-input': 0, system: 0,
        },
      });

      throttled.push(makeEvent('ambient', 'first'));
      await new Promise(r => setTimeout(r, 60));
      const second = throttled.push(makeEvent('ambient', 'second'));
      expect(second).not.toBeNull();
    });

    it('throttles different types independently', () => {
      const throttled = new ContextStream({
        throttleMs: {
          ambient: 10000, clipboard: 10000, sentiment: 0, notification: 0,
          'tool-invoke': 0, calendar: 0, communication: 0, git: 0,
          'screen-text': 0, 'user-input': 0, system: 0,
        },
      });

      const ambient = throttled.push(makeEvent('ambient', 'app'));
      const clipboard = throttled.push(makeEvent('clipboard', 'clip'));
      expect(ambient).not.toBeNull();
      expect(clipboard).not.toBeNull();
    });

    it('events with throttle 0 are never throttled', () => {
      const results: ContextEvent[] = [];
      for (let i = 0; i < 10; i++) {
        const r = stream.push(makeEvent('tool-invoke', `tool-${i}`, { toolName: `t${i}` }));
        if (r) results.push(r);
      }
      expect(results.length).toBe(10);
    });
  });

  // ── Deduplication ──────────────────────────────────────────────
  describe('Deduplication', () => {
    it('replaces event with same dedupeKey within window', () => {
      stream.push(makeEvent('ambient', 'old state', { activeApp: 'Chrome' }, { dedupeKey: 'ambient-state' }));
      stream.push(makeEvent('ambient', 'new state', { activeApp: 'VS Code' }, { dedupeKey: 'ambient-state' }));

      expect(stream.getBufferSize()).toBe(1);
      const events = stream.getRecent({ limit: 10 });
      expect(events[0].summary).toBe('new state');
      expect(events[0].data.activeApp).toBe('VS Code');
    });

    it('does not deduplicate events without dedupeKey', () => {
      stream.push(makeEvent('notification', 'notif 1'));
      stream.push(makeEvent('notification', 'notif 2'));
      expect(stream.getBufferSize()).toBe(2);
    });

    it('does not deduplicate events with different dedupeKeys', () => {
      stream.push(makeEvent('ambient', 'a', {}, { dedupeKey: 'key-1' }));
      stream.push(makeEvent('ambient', 'b', {}, { dedupeKey: 'key-2' }));
      expect(stream.getBufferSize()).toBe(2);
    });
  });

  // ── Buffer Management ──────────────────────────────────────────
  describe('Buffer Management', () => {
    it('evicts oldest events when buffer exceeds maxBufferSize', () => {
      const small = new ContextStream({
        maxBufferSize: 5,
        throttleMs: {
          ambient: 0, clipboard: 0, sentiment: 0, notification: 0,
          'tool-invoke': 0, calendar: 0, communication: 0, git: 0,
          'screen-text': 0, 'user-input': 0, system: 0,
        },
      });

      for (let i = 0; i < 10; i++) {
        small.push(makeEvent('system', `event-${i}`));
      }

      expect(small.getBufferSize()).toBe(5);
      const events = small.getRecent({ limit: 10 });
      expect(events[0].summary).toBe('event-9'); // Most recent
      expect(events[4].summary).toBe('event-5'); // Oldest remaining
    });

    it('prunes expired events', async () => {
      const shortLived = new ContextStream({
        maxBufferAgeMs: 50,
        throttleMs: {
          ambient: 0, clipboard: 0, sentiment: 0, notification: 0,
          'tool-invoke': 0, calendar: 0, communication: 0, git: 0,
          'screen-text': 0, 'user-input': 0, system: 0,
        },
      });

      shortLived.push(makeEvent('system', 'old'));
      await new Promise(r => setTimeout(r, 70));
      shortLived.push(makeEvent('system', 'new'));

      const pruned = shortLived.prune();
      expect(pruned).toBe(1);
      expect(shortLived.getBufferSize()).toBe(1);
    });

    it('respects per-event ttlMs', async () => {
      stream.push({
        type: 'system',
        source: 'test',
        summary: 'short-lived',
        data: {},
        ttlMs: 30,
      });
      stream.push(makeEvent('system', 'normal'));

      await new Promise(r => setTimeout(r, 50));
      stream.prune();

      // Short-lived should be pruned, normal should remain
      const events = stream.getRecent({ limit: 10 });
      expect(events.length).toBe(1);
      expect(events[0].summary).toBe('normal');
    });

    it('clear empties buffer and resets snapshot', () => {
      stream.push(makeEvent('ambient', 'test', { activeApp: 'Chrome' }));
      stream.clear();

      expect(stream.getBufferSize()).toBe(0);
      expect(stream.getSnapshot().activeApp).toBe('');
    });
  });

  // ── Snapshot Aggregation ───────────────────────────────────────
  describe('Snapshot', () => {
    it('updates activeApp from ambient events', () => {
      stream.push(makeEvent('ambient', 'app focus', { activeApp: 'VS Code' }));
      expect(stream.getSnapshot().activeApp).toBe('VS Code');
    });

    it('updates windowTitle from ambient events', () => {
      stream.push(makeEvent('ambient', 'title', { windowTitle: 'main.ts — nexus-os' }));
      expect(stream.getSnapshot().windowTitle).toBe('main.ts — nexus-os');
    });

    it('updates inferredTask from ambient events', () => {
      stream.push(makeEvent('ambient', 'task', { inferredTask: 'coding' }));
      expect(stream.getSnapshot().inferredTask).toBe('coding');
    });

    it('updates focusStreak from ambient events', () => {
      stream.push(makeEvent('ambient', 'streak', { focusStreak: 180 }));
      expect(stream.getSnapshot().focusStreak).toBe(180);
    });

    it('updates mood from sentiment events', () => {
      stream.push(makeEvent('sentiment', 'mood shift', { mood: 'excited', confidence: 0.8, energyLevel: 0.9 }));
      const snap = stream.getSnapshot();
      expect(snap.currentMood).toBe('excited');
      expect(snap.moodConfidence).toBe(0.8);
      expect(snap.energyLevel).toBe(0.9);
    });

    it('updates clipboard from clipboard events', () => {
      stream.push(makeEvent('clipboard', 'clip', { contentType: 'url', preview: 'https://example.com' }));
      const snap = stream.getSnapshot();
      expect(snap.lastClipboardType).toBe('url');
      expect(snap.lastClipboardPreview).toBe('https://example.com');
    });

    it('tracks recent tool calls (max 5)', () => {
      for (let i = 0; i < 7; i++) {
        stream.push(makeEvent('tool-invoke', `tool ${i}`, { toolName: `tool-${i}` }));
      }
      const snap = stream.getSnapshot();
      expect(snap.recentToolCalls.length).toBe(5);
      expect(snap.recentToolCalls[0]).toBe('tool-6'); // Most recent first
    });

    it('tracks recent notifications (max 3)', () => {
      for (let i = 0; i < 5; i++) {
        stream.push(makeEvent('notification', `notif ${i}`));
      }
      const snap = stream.getSnapshot();
      expect(snap.recentNotifications.length).toBe(3);
      expect(snap.recentNotifications[0]).toBe('notif 4'); // Most recent first
    });

    it('returns a copy (not reference)', () => {
      stream.push(makeEvent('ambient', 'test', { activeApp: 'Chrome' }));
      const snap1 = stream.getSnapshot();
      const snap2 = stream.getSnapshot();
      expect(snap1).toEqual(snap2);
      expect(snap1).not.toBe(snap2);
    });
  });

  // ── Query Methods ──────────────────────────────────────────────
  describe('Query Methods', () => {
    beforeEach(() => {
      stream.push(makeEvent('ambient', 'ambient-1', { activeApp: 'Chrome' }));
      stream.push(makeEvent('clipboard', 'clip-1', { preview: 'hello' }));
      stream.push(makeEvent('tool-invoke', 'tool-1', { toolName: 'search' }));
      stream.push(makeEvent('ambient', 'ambient-2', { activeApp: 'VS Code' }));
      stream.push(makeEvent('notification', 'notif-1'));
    });

    it('getRecent returns events in reverse chronological order', () => {
      const events = stream.getRecent();
      expect(events[0].summary).toBe('notif-1');
      expect(events[4].summary).toBe('ambient-1');
    });

    it('getRecent respects limit', () => {
      const events = stream.getRecent({ limit: 2 });
      expect(events.length).toBe(2);
    });

    it('getRecent filters by type', () => {
      const events = stream.getRecent({ types: ['ambient'] });
      expect(events.length).toBe(2);
      expect(events.every(e => e.type === 'ambient')).toBe(true);
    });

    it('getByType returns events of specific type', () => {
      const events = stream.getByType('ambient');
      expect(events.length).toBe(2);
    });

    it('getByType respects limit', () => {
      const events = stream.getByType('ambient', 1);
      expect(events.length).toBe(1);
      expect(events[0].summary).toBe('ambient-2'); // Most recent
    });

    it('getLatestByType returns one event per type', () => {
      const latest = stream.getLatestByType();
      expect(latest.get('ambient')?.summary).toBe('ambient-2');
      expect(latest.get('clipboard')?.summary).toBe('clip-1');
      expect(latest.get('tool-invoke')?.summary).toBe('tool-1');
      expect(latest.get('notification')?.summary).toBe('notif-1');
    });
  });

  // ── Context String Generation ──────────────────────────────────
  describe('Context String Generation', () => {
    it('returns empty string when disabled', () => {
      stream.setEnabled(false);
      expect(stream.getContextString()).toBe('');
    });

    it('returns empty string when buffer is empty', () => {
      expect(stream.getContextString()).toBe('');
    });

    it('includes active app in context', () => {
      stream.push(makeEvent('ambient', 'coding', {
        activeApp: 'VS Code',
        inferredTask: 'coding',
        windowTitle: 'index.ts',
      }));
      const ctx = stream.getContextString();
      expect(ctx).toContain('VS Code');
      expect(ctx).toContain('coding');
    });

    it('includes mood when not neutral', () => {
      stream.push(makeEvent('sentiment', 'excited', { mood: 'excited', confidence: 0.8 }));
      const ctx = stream.getContextString();
      expect(ctx).toContain('excited');
    });

    it('excludes neutral mood', () => {
      stream.push(makeEvent('sentiment', 'neutral', { mood: 'neutral', confidence: 0.5 }));
      const ctx = stream.getContextString();
      expect(ctx).not.toContain('Mood');
    });

    it('includes clipboard context', () => {
      stream.push(makeEvent('clipboard', 'clip', { contentType: 'url', preview: 'https://github.com' }));
      const ctx = stream.getContextString();
      expect(ctx).toContain('url');
      expect(ctx).toContain('github.com');
    });

    it('includes recent tool calls', () => {
      stream.push(makeEvent('tool-invoke', 'search', { toolName: 'web-search' }));
      const ctx = stream.getContextString();
      expect(ctx).toContain('web-search');
    });

    it('includes recent notifications', () => {
      stream.push(makeEvent('notification', 'Email from John'));
      const ctx = stream.getContextString();
      expect(ctx).toContain('Email from John');
    });
  });

  // ── Prompt Context (short form) ────────────────────────────────
  describe('Prompt Context (short form)', () => {
    it('returns empty when disabled', () => {
      stream.setEnabled(false);
      expect(stream.getPromptContext()).toBe('');
    });

    it('includes active app and task', () => {
      stream.push(makeEvent('ambient', 'test', { activeApp: 'Chrome', inferredTask: 'browsing' }));
      const ctx = stream.getPromptContext();
      expect(ctx).toContain('[CONTEXT]');
      expect(ctx).toContain('browsing');
      expect(ctx).toContain('Chrome');
    });

    it('includes mood when not neutral', () => {
      stream.push(makeEvent('sentiment', 'test', { mood: 'stressed' }));
      const ctx = stream.getPromptContext();
      expect(ctx).toContain('stressed');
    });
  });

  // ── Listener System ────────────────────────────────────────────
  describe('Event Listeners', () => {
    it('notifies listeners on new events', () => {
      const received: ContextEvent[] = [];
      stream.on(e => received.push(e));

      stream.push(makeEvent('system', 'test'));
      expect(received.length).toBe(1);
      expect(received[0].summary).toBe('test');
    });

    it('unsubscribe stops notifications', () => {
      const received: ContextEvent[] = [];
      const unsub = stream.on(e => received.push(e));

      stream.push(makeEvent('system', 'first'));
      unsub();
      stream.push(makeEvent('system', 'second'));

      expect(received.length).toBe(1);
    });

    it('listener errors do not break the stream', () => {
      stream.on(() => { throw new Error('listener crash'); });
      const received: ContextEvent[] = [];
      stream.on(e => received.push(e));

      stream.push(makeEvent('system', 'test'));
      expect(received.length).toBe(1); // Second listener still works
    });

    it('notifies on dedup replacement', () => {
      const received: ContextEvent[] = [];
      stream.on(e => received.push(e));

      stream.push(makeEvent('ambient', 'old', {}, { dedupeKey: 'state' }));
      stream.push(makeEvent('ambient', 'new', {}, { dedupeKey: 'state' }));

      expect(received.length).toBe(2);
      expect(received[1].summary).toBe('new');
    });
  });

  // ── Data Sanitization ──────────────────────────────────────────
  describe('Data Sanitization', () => {
    it('strips sensitive-looking keys from data', () => {
      const result = stream.push(makeEvent('system', 'test', {
        username: 'alice',
        password: 'secret123',
        apiToken: 'tok_abc',
        authKey: 'key_xyz',
        safe: 'value',
      }));

      expect(result!.data).toHaveProperty('username');
      expect(result!.data).toHaveProperty('safe');
      expect(result!.data).not.toHaveProperty('password');
      expect(result!.data).not.toHaveProperty('apiToken');
      expect(result!.data).not.toHaveProperty('authKey');
    });

    it('truncates long string values in data', () => {
      const result = stream.push(makeEvent('system', 'test', {
        longValue: 'x'.repeat(5000),
      }));

      const val = result!.data.longValue as string;
      expect(val.length).toBeLessThan(5000);
      expect(val).toContain('truncated');
    });

    it('limits number of data keys', () => {
      const bigData: Record<string, unknown> = {};
      for (let i = 0; i < 30; i++) {
        bigData[`key${i}`] = `value${i}`;
      }
      const result = stream.push(makeEvent('system', 'test', bigData));
      expect(Object.keys(result!.data).length).toBeLessThanOrEqual(20);
    });
  });

  // ── Status ─────────────────────────────────────────────────────
  describe('Status', () => {
    it('reports correct initial status', () => {
      const status = stream.getStatus();
      expect(status.enabled).toBe(true);
      expect(status.bufferSize).toBe(0);
      expect(status.eventsPerMinute).toBe(0);
    });

    it('reports correct status after activity', () => {
      stream.push(makeEvent('ambient', 'a1'));
      stream.push(makeEvent('ambient', 'a2'));
      stream.push(makeEvent('clipboard', 'c1'));

      const status = stream.getStatus();
      expect(status.bufferSize).toBe(3);
      expect(status.eventCounts['ambient']).toBe(2);
      expect(status.eventCounts['clipboard']).toBe(1);
      expect(status.eventsPerMinute).toBe(3); // All within last 60s
    });

    it('estimates memory usage', () => {
      for (let i = 0; i < 100; i++) {
        stream.push(makeEvent('system', `event-${i}`));
      }
      const status = stream.getStatus();
      expect(status.memoryEstimateKb).toBeGreaterThan(0);
    });
  });

  // ── Configuration ──────────────────────────────────────────────
  describe('Configuration', () => {
    it('returns config', () => {
      const config = stream.getConfig();
      expect(config.enabled).toBe(true);
      expect(config.maxBufferSize).toBeGreaterThan(0);
    });

    it('setEnabled toggles stream', () => {
      stream.setEnabled(false);
      expect(stream.push(makeEvent('system', 'test'))).toBeNull();
      stream.setEnabled(true);
      expect(stream.push(makeEvent('system', 'test'))).not.toBeNull();
    });
  });

  // ── cLaw Gate: In-Memory Only ──────────────────────────────────
  describe('cLaw Gate: In-Memory Only', () => {
    it('has no persist/save/load methods', () => {
      const proto = Object.getOwnPropertyNames(Object.getPrototypeOf(stream));
      expect(proto).not.toContain('save');
      expect(proto).not.toContain('load');
      expect(proto).not.toContain('persist');
      expect(proto).not.toContain('serialize');
    });

    it('sensitive data keys are filtered from events', () => {
      const result = stream.push(makeEvent('system', 'test', {
        secretKey: 'abc',
        credential: 'xyz',
        normalField: 'ok',
      }));
      expect(result!.data).not.toHaveProperty('secretKey');
      expect(result!.data).not.toHaveProperty('credential');
      expect(result!.data).toHaveProperty('normalField');
    });

    it('clear fully removes all data', () => {
      for (let i = 0; i < 50; i++) {
        stream.push(makeEvent('system', `event-${i}`));
      }
      stream.clear();
      expect(stream.getBufferSize()).toBe(0);
      expect(stream.getRecent().length).toBe(0);
      expect(stream.getSnapshot().activeApp).toBe('');
    });
  });
});
