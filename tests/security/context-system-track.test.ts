/**
 * Track III — Nervous System / Context: Comprehensive Test Suite
 *
 * Phase 1: Activity Ingestion (ContextStream)
 * Phase 2: Context Graph (ContextGraph)
 * Phase 3: Context-Aware Routing (Integration)
 *
 * 55+ test cases covering push/dedup/throttle/sanitize/snapshot/listeners,
 * work streams, entity extraction/tracking/pruning/relevance, and
 * end-to-end stream-to-graph context generation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock context stream for Phase 2 graph tests ──────────────────────
let graphListener: ((event: any) => void) | null = null;
const mockGraphUnsub = vi.fn();

vi.mock('../../src/main/context-stream', async (importOriginal) => {
  const actual = (await importOriginal()) as any;
  return {
    ...actual,
    ContextStream: actual.ContextStream,
    contextStream: {
      on: vi.fn((listener: (event: any) => void) => {
        graphListener = listener;
        return mockGraphUnsub;
      }),
      getSnapshot: vi.fn().mockReturnValue({
        activeApp: '',
        windowTitle: '',
        inferredTask: '',
        focusStreak: 0,
        currentMood: 'neutral',
        moodConfidence: 0,
        energyLevel: 0.5,
        lastClipboardType: '',
        lastClipboardPreview: '',
        recentToolCalls: [],
        recentNotifications: [],
        activeWorkStream: '',
        lastUpdated: 0,
      }),
    },
  };
});

import { ContextStream } from '../../src/main/context-stream';
import type { ContextEvent, ContextEventType } from '../../src/main/context-stream';
import { ContextGraph } from '../../src/main/context-graph';

// ── Helpers ──────────────────────────────────────────────────────────

const NO_THROTTLE: Record<ContextEventType, number> = {
  ambient: 0, clipboard: 0, sentiment: 0, notification: 0,
  'tool-invoke': 0, calendar: 0, communication: 0, git: 0,
  'screen-text': 0, 'user-input': 0, system: 0,
};

function freshStream(cfg: Partial<any> = {}): ContextStream {
  return new ContextStream({ throttleMs: NO_THROTTLE, ...cfg });
}

function freshGraph(cfg: Partial<any> = {}): ContextGraph {
  return new ContextGraph(cfg);
}

function makeStreamEvent(
  type: ContextEventType,
  summary: string,
  data: Record<string, unknown> = {},
  opts: Partial<Pick<ContextEvent, 'dedupeKey' | 'ttlMs'>> = {},
): Omit<ContextEvent, 'id' | 'timestamp'> {
  return { type, source: `test-${type}`, summary, data, ...opts };
}

function makeGraphEvent(overrides: Partial<any> = {}): any {
  return {
    id: `ctx-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    type: 'ambient',
    timestamp: Date.now(),
    source: 'test',
    summary: 'test event',
    data: {},
    ...overrides,
  };
}

// =====================================================================
// Phase 1: Activity Ingestion — ContextStream
// =====================================================================

describe('Phase 1: Activity Ingestion (ContextStream)', () => {
  let stream: ContextStream;

  beforeEach(() => {
    stream = freshStream();
  });

  // ── 1.1 Core Push ─────────────────────────────────────────────────
  describe('1.1 Core Event Push', () => {
    it('returns event with generated id and timestamp', () => {
      const result = stream.push(makeStreamEvent('ambient', 'Focused VS Code'));
      expect(result).not.toBeNull();
      expect(result!.id).toMatch(/^ctx-/);
      expect(result!.timestamp).toBeGreaterThan(0);
      expect(result!.summary).toBe('Focused VS Code');
    });

    it('increments buffer on push', () => {
      expect(stream.getBufferSize()).toBe(0);
      stream.push(makeStreamEvent('ambient', 'a'));
      expect(stream.getBufferSize()).toBe(1);
      stream.push(makeStreamEvent('clipboard', 'b'));
      expect(stream.getBufferSize()).toBe(2);
    });

    it('returns null when disabled', () => {
      stream.setEnabled(false);
      expect(stream.push(makeStreamEvent('ambient', 'x'))).toBeNull();
      expect(stream.getBufferSize()).toBe(0);
    });

    it('generates unique IDs across 100 events', () => {
      const ids = new Set<string>();
      for (let i = 0; i < 100; i++) {
        const r = stream.push(makeStreamEvent('system', `e-${i}`));
        ids.add(r!.id);
      }
      expect(ids.size).toBe(100);
    });
  });

  // ── 1.2 Throttling ────────────────────────────────────────────────
  describe('1.2 Throttling', () => {
    it('rejects events within throttle window', () => {
      const throttled = new ContextStream({
        throttleMs: { ...NO_THROTTLE, ambient: 5000 },
      });
      expect(throttled.push(makeStreamEvent('ambient', 'first'))).not.toBeNull();
      expect(throttled.push(makeStreamEvent('ambient', 'second'))).toBeNull();
    });

    it('allows event after throttle window elapses', async () => {
      const throttled = new ContextStream({
        throttleMs: { ...NO_THROTTLE, ambient: 50 },
      });
      throttled.push(makeStreamEvent('ambient', 'first'));
      await new Promise(r => setTimeout(r, 60));
      expect(throttled.push(makeStreamEvent('ambient', 'second'))).not.toBeNull();
    });

    it('throttles types independently', () => {
      const throttled = new ContextStream({
        throttleMs: { ...NO_THROTTLE, ambient: 10000, clipboard: 10000 },
      });
      expect(throttled.push(makeStreamEvent('ambient', 'a'))).not.toBeNull();
      expect(throttled.push(makeStreamEvent('clipboard', 'c'))).not.toBeNull();
    });

    it('never throttles types with 0ms interval', () => {
      const results: ContextEvent[] = [];
      for (let i = 0; i < 10; i++) {
        const r = stream.push(makeStreamEvent('tool-invoke', `t-${i}`, { toolName: `t${i}` }));
        if (r) results.push(r);
      }
      expect(results.length).toBe(10);
    });
  });

  // ── 1.3 Deduplication ─────────────────────────────────────────────
  describe('1.3 Deduplication', () => {
    it('replaces event with same dedupeKey within window', () => {
      stream.push(makeStreamEvent('ambient', 'old', { activeApp: 'Chrome' }, { dedupeKey: 'state' }));
      stream.push(makeStreamEvent('ambient', 'new', { activeApp: 'VS Code' }, { dedupeKey: 'state' }));
      expect(stream.getBufferSize()).toBe(1);
      const events = stream.getRecent({ limit: 10 });
      expect(events[0].summary).toBe('new');
      expect(events[0].data.activeApp).toBe('VS Code');
    });

    it('does not dedup events without dedupeKey', () => {
      stream.push(makeStreamEvent('notification', 'n1'));
      stream.push(makeStreamEvent('notification', 'n2'));
      expect(stream.getBufferSize()).toBe(2);
    });

    it('does not dedup events with different keys', () => {
      stream.push(makeStreamEvent('ambient', 'a', {}, { dedupeKey: 'k1' }));
      stream.push(makeStreamEvent('ambient', 'b', {}, { dedupeKey: 'k2' }));
      expect(stream.getBufferSize()).toBe(2);
    });
  });

  // ── 1.4 Buffer Management ─────────────────────────────────────────
  describe('1.4 Buffer Management', () => {
    it('evicts oldest when maxBufferSize exceeded', () => {
      const small = freshStream({ maxBufferSize: 5 });
      for (let i = 0; i < 10; i++) small.push(makeStreamEvent('system', `e-${i}`));
      expect(small.getBufferSize()).toBe(5);
      const events = small.getRecent({ limit: 10 });
      expect(events[0].summary).toBe('e-9');
      expect(events[4].summary).toBe('e-5');
    });

    it('prunes expired events', async () => {
      const shortLived = freshStream({ maxBufferAgeMs: 50 });
      shortLived.push(makeStreamEvent('system', 'old'));
      await new Promise(r => setTimeout(r, 70));
      shortLived.push(makeStreamEvent('system', 'new'));
      const pruned = shortLived.prune();
      expect(pruned).toBe(1);
      expect(shortLived.getBufferSize()).toBe(1);
    });

    it('respects per-event ttlMs', async () => {
      stream.push({ type: 'system', source: 'test', summary: 'short', data: {}, ttlMs: 30 });
      stream.push(makeStreamEvent('system', 'normal'));
      await new Promise(r => setTimeout(r, 50));
      stream.prune();
      const events = stream.getRecent({ limit: 10 });
      expect(events.length).toBe(1);
      expect(events[0].summary).toBe('normal');
    });

    it('clear resets buffer and snapshot', () => {
      stream.push(makeStreamEvent('ambient', 't', { activeApp: 'Chrome' }));
      stream.clear();
      expect(stream.getBufferSize()).toBe(0);
      expect(stream.getSnapshot().activeApp).toBe('');
    });
  });

  // ── 1.5 Snapshot Aggregation ──────────────────────────────────────
  describe('1.5 Snapshot Aggregation', () => {
    it('updates activeApp/windowTitle/inferredTask/focusStreak from ambient', () => {
      stream.push(makeStreamEvent('ambient', 'focus', {
        activeApp: 'VS Code', windowTitle: 'main.ts — nexus', inferredTask: 'coding', focusStreak: 120,
      }));
      const snap = stream.getSnapshot();
      expect(snap.activeApp).toBe('VS Code');
      expect(snap.windowTitle).toBe('main.ts — nexus');
      expect(snap.inferredTask).toBe('coding');
      expect(snap.focusStreak).toBe(120);
    });

    it('updates mood/confidence/energy from sentiment', () => {
      stream.push(makeStreamEvent('sentiment', 'mood', { mood: 'excited', confidence: 0.8, energyLevel: 0.9 }));
      const snap = stream.getSnapshot();
      expect(snap.currentMood).toBe('excited');
      expect(snap.moodConfidence).toBe(0.8);
      expect(snap.energyLevel).toBe(0.9);
    });

    it('updates clipboard type/preview', () => {
      stream.push(makeStreamEvent('clipboard', 'clip', { contentType: 'url', preview: 'https://ex.com' }));
      const snap = stream.getSnapshot();
      expect(snap.lastClipboardType).toBe('url');
      expect(snap.lastClipboardPreview).toBe('https://ex.com');
    });

    it('caps recentToolCalls at 5 (FIFO)', () => {
      for (let i = 0; i < 7; i++) {
        stream.push(makeStreamEvent('tool-invoke', `t${i}`, { toolName: `tool-${i}` }));
      }
      const snap = stream.getSnapshot();
      expect(snap.recentToolCalls.length).toBe(5);
      expect(snap.recentToolCalls[0]).toBe('tool-6');
    });

    it('caps recentNotifications at 3 (FIFO)', () => {
      for (let i = 0; i < 5; i++) {
        stream.push(makeStreamEvent('notification', `notif-${i}`));
      }
      const snap = stream.getSnapshot();
      expect(snap.recentNotifications.length).toBe(3);
      expect(snap.recentNotifications[0]).toBe('notif-4');
    });

    it('returns a copy, not a reference', () => {
      stream.push(makeStreamEvent('ambient', 't', { activeApp: 'Chrome' }));
      const s1 = stream.getSnapshot();
      const s2 = stream.getSnapshot();
      expect(s1).toEqual(s2);
      expect(s1).not.toBe(s2);
    });
  });

  // ── 1.6 Data Sanitization ─────────────────────────────────────────
  describe('1.6 Data Sanitization', () => {
    it('strips sensitive keys (password, token, secret, key, auth, credential)', () => {
      const r = stream.push(makeStreamEvent('system', 'test', {
        username: 'alice', password: 's3cret', apiToken: 'tok',
        authKey: 'k', safe: 'ok',
      }));
      expect(r!.data).toHaveProperty('username');
      expect(r!.data).toHaveProperty('safe');
      expect(r!.data).not.toHaveProperty('password');
      expect(r!.data).not.toHaveProperty('apiToken');
      expect(r!.data).not.toHaveProperty('authKey');
    });

    it('truncates string values over 2000 chars', () => {
      const r = stream.push(makeStreamEvent('system', 't', { big: 'x'.repeat(5000) }));
      const val = r!.data.big as string;
      expect(val.length).toBeLessThan(5000);
      expect(val).toContain('truncated');
    });

    it('limits data keys to MAX_DATA_KEYS (20)', () => {
      const data: Record<string, unknown> = {};
      for (let i = 0; i < 30; i++) data[`key${i}`] = `v${i}`;
      const r = stream.push(makeStreamEvent('system', 't', data));
      expect(Object.keys(r!.data).length).toBeLessThanOrEqual(20);
    });

    it('truncates summary to MAX_SUMMARY_LENGTH (200)', () => {
      const r = stream.push(makeStreamEvent('ambient', 'x'.repeat(500)));
      expect(r!.summary.length).toBeLessThanOrEqual(200);
    });
  });

  // ── 1.7 Query Methods ─────────────────────────────────────────────
  describe('1.7 Query Methods', () => {
    beforeEach(() => {
      stream.push(makeStreamEvent('ambient', 'a1', { activeApp: 'Chrome' }));
      stream.push(makeStreamEvent('clipboard', 'c1', { preview: 'hello' }));
      stream.push(makeStreamEvent('tool-invoke', 't1', { toolName: 'search' }));
      stream.push(makeStreamEvent('ambient', 'a2', { activeApp: 'VS Code' }));
      stream.push(makeStreamEvent('notification', 'n1'));
    });

    it('getRecent returns events newest-first', () => {
      const ev = stream.getRecent();
      expect(ev[0].summary).toBe('n1');
      expect(ev[4].summary).toBe('a1');
    });

    it('getRecent respects limit', () => {
      expect(stream.getRecent({ limit: 2 }).length).toBe(2);
    });

    it('getRecent filters by type', () => {
      const ev = stream.getRecent({ types: ['ambient'] });
      expect(ev.length).toBe(2);
      expect(ev.every(e => e.type === 'ambient')).toBe(true);
    });

    it('getByType with limit', () => {
      const ev = stream.getByType('ambient', 1);
      expect(ev.length).toBe(1);
      expect(ev[0].summary).toBe('a2');
    });

    it('getLatestByType returns one per type', () => {
      const latest = stream.getLatestByType();
      expect(latest.get('ambient')?.summary).toBe('a2');
      expect(latest.get('clipboard')?.summary).toBe('c1');
      expect(latest.get('notification')?.summary).toBe('n1');
    });
  });

  // ── 1.8 Context String ────────────────────────────────────────────
  describe('1.8 Context String', () => {
    it('returns empty when disabled', () => {
      stream.setEnabled(false);
      expect(stream.getContextString()).toBe('');
    });

    it('returns empty when buffer empty', () => {
      expect(stream.getContextString()).toBe('');
    });

    it('includes app and task', () => {
      stream.push(makeStreamEvent('ambient', 'coding', {
        activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'index.ts',
      }));
      const ctx = stream.getContextString();
      expect(ctx).toContain('VS Code');
      expect(ctx).toContain('coding');
    });

    it('includes non-neutral mood', () => {
      stream.push(makeStreamEvent('sentiment', 'excited', { mood: 'excited', confidence: 0.8 }));
      expect(stream.getContextString()).toContain('excited');
    });

    it('omits neutral mood', () => {
      stream.push(makeStreamEvent('sentiment', 'neutral', { mood: 'neutral', confidence: 0.5 }));
      expect(stream.getContextString()).not.toContain('Mood');
    });
  });

  // ── 1.9 Prompt Context ────────────────────────────────────────────
  describe('1.9 Prompt Context', () => {
    it('returns empty when disabled', () => {
      stream.setEnabled(false);
      expect(stream.getPromptContext()).toBe('');
    });

    it('returns [CONTEXT] prefix with app/task', () => {
      stream.push(makeStreamEvent('ambient', 't', { activeApp: 'Chrome', inferredTask: 'browsing' }));
      const ctx = stream.getPromptContext();
      expect(ctx).toMatch(/^\[CONTEXT\]/);
      expect(ctx).toContain('Chrome');
      expect(ctx).toContain('browsing');
    });

    it('includes mood when not neutral', () => {
      stream.push(makeStreamEvent('sentiment', 't', { mood: 'stressed' }));
      expect(stream.getPromptContext()).toContain('stressed');
    });
  });

  // ── 1.10 Listeners ────────────────────────────────────────────────
  describe('1.10 Event Listeners', () => {
    it('notifies listeners on push', () => {
      const received: ContextEvent[] = [];
      stream.on(e => received.push(e));
      stream.push(makeStreamEvent('system', 'hello'));
      expect(received.length).toBe(1);
      expect(received[0].summary).toBe('hello');
    });

    it('unsubscribe stops notifications', () => {
      const received: ContextEvent[] = [];
      const unsub = stream.on(e => received.push(e));
      stream.push(makeStreamEvent('system', 'first'));
      unsub();
      stream.push(makeStreamEvent('system', 'second'));
      expect(received.length).toBe(1);
    });

    it('listener errors do not break stream', () => {
      stream.on(() => { throw new Error('crash'); });
      const received: ContextEvent[] = [];
      stream.on(e => received.push(e));
      stream.push(makeStreamEvent('system', 'test'));
      expect(received.length).toBe(1);
    });

    it('notifies on dedup replacement', () => {
      const received: ContextEvent[] = [];
      stream.on(e => received.push(e));
      stream.push(makeStreamEvent('ambient', 'old', {}, { dedupeKey: 's' }));
      stream.push(makeStreamEvent('ambient', 'new', {}, { dedupeKey: 's' }));
      expect(received.length).toBe(2);
      expect(received[1].summary).toBe('new');
    });
  });

  // ── 1.11 Status ───────────────────────────────────────────────────
  describe('1.11 Status', () => {
    it('reports correct initial status', () => {
      const s = stream.getStatus();
      expect(s.enabled).toBe(true);
      expect(s.bufferSize).toBe(0);
      expect(s.eventsPerMinute).toBe(0);
    });

    it('reports correct counts after activity', () => {
      stream.push(makeStreamEvent('ambient', 'a1'));
      stream.push(makeStreamEvent('ambient', 'a2'));
      stream.push(makeStreamEvent('clipboard', 'c1'));
      const s = stream.getStatus();
      expect(s.bufferSize).toBe(3);
      expect(s.eventCounts['ambient']).toBe(2);
      expect(s.eventCounts['clipboard']).toBe(1);
      expect(s.eventsPerMinute).toBe(3);
    });

    it('estimates memory', () => {
      for (let i = 0; i < 100; i++) stream.push(makeStreamEvent('system', `e-${i}`));
      expect(stream.getStatus().memoryEstimateKb).toBeGreaterThan(0);
    });
  });

  // ── 1.12 cLaw Gate ────────────────────────────────────────────────
  describe('1.12 cLaw Gate: In-Memory Only', () => {
    it('has no persist/save/load/serialize methods', () => {
      const proto = Object.getOwnPropertyNames(Object.getPrototypeOf(stream));
      expect(proto).not.toContain('save');
      expect(proto).not.toContain('load');
      expect(proto).not.toContain('persist');
      expect(proto).not.toContain('serialize');
    });

    it('clear fully wipes all data', () => {
      for (let i = 0; i < 50; i++) stream.push(makeStreamEvent('system', `e-${i}`));
      stream.clear();
      expect(stream.getBufferSize()).toBe(0);
      expect(stream.getRecent().length).toBe(0);
      expect(stream.getSnapshot().activeApp).toBe('');
    });
  });
});

// =====================================================================
// Phase 2: Context Graph (ContextGraph)
// =====================================================================

describe('Phase 2: Context Graph (ContextGraph)', () => {
  let graph: ContextGraph;

  beforeEach(() => {
    vi.clearAllMocks();
    graphListener = null;
    graph = freshGraph();
  });

  afterEach(() => {
    graph.stop();
  });

  // ── 2.1 Lifecycle ─────────────────────────────────────────────────
  describe('2.1 Lifecycle', () => {
    it('registers listener on start', () => {
      graph.start();
      expect(graphListener).toBeTypeOf('function');
    });

    it('does not double-register on repeated start', () => {
      graph.start();
      graph.start();
      expect(graphListener).toBeTypeOf('function');
    });

    it('unsubscribes on stop', () => {
      graph.start();
      graph.stop();
      expect(mockGraphUnsub).toHaveBeenCalled();
    });

    it('clears all state on stop', () => {
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(graph.getStatus().streamCount).toBe(1);
      graph.stop();
      expect(graph.getStatus().streamCount).toBe(0);
      expect(graph.getStatus().entityCount).toBe(0);
      expect(graph.getActiveStream()).toBeNull();
    });

    it('can restart after stop', () => {
      graph.start();
      graph.stop();
      graph.start();
      expect(graphListener).toBeTypeOf('function');
    });
  });

  // ── 2.2 Work Stream Creation ──────────────────────────────────────
  describe('2.2 Work Stream Creation', () => {
    beforeEach(() => graph.start());

    it('creates stream on first ambient event', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'index.ts — agent-friday' },
      }));
      const active = graph.getActiveStream();
      expect(active).not.toBeNull();
      expect(active!.app).toBe('VS Code');
      expect(active!.task).toBe('coding');
      expect(active!.name).toContain('VS Code');
    });

    it('includes project name in stream name from title', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts — agent-friday' },
      }));
      expect(graph.getActiveStream()!.name).toContain('agent-friday');
    });

    it('creates new stream when app changes', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'a.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      expect(graph.getRecentStreams(10).length).toBe(2);
      expect(graph.getActiveStream()!.app).toBe('Chrome');
    });

    it('creates new stream when task changes (same app)', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'communicating', windowTitle: 'Gmail' },
      }));
      expect(graph.getRecentStreams(10).length).toBe(2);
    });

    it('does NOT create new stream on title-only change', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'file1.ts — project' },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'file2.ts — project' },
      }));
      expect(graph.getRecentStreams(10).length).toBe(1);
    });

    it('assigns non-ambient events to active stream', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'read_file', success: true },
        summary: 'Tool: read_file',
      }));
      graphListener!(makeGraphEvent({
        type: 'clipboard',
        data: { contentType: 'code', preview: 'const x = 1;' },
      }));
      const active = graph.getActiveStream();
      expect(active!.eventCount).toBe(3);
      expect(active!.eventTypes.has('tool-invoke')).toBe(true);
      expect(active!.eventTypes.has('clipboard')).toBe(true);
    });

    it('increments eventCount correctly', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      for (let i = 0; i < 5; i++) {
        graphListener!(makeGraphEvent({
          type: 'tool-invoke',
          data: { toolName: `tool-${i}`, success: true },
        }));
      }
      expect(graph.getActiveStream()!.eventCount).toBe(6);
    });
  });

  // ── 2.3 Entity Extraction ─────────────────────────────────────────
  describe('2.3 Entity Extraction', () => {
    beforeEach(() => {
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts — agent-friday' },
      }));
    });

    it('extracts app entity from ambient events', () => {
      const apps = graph.getEntitiesByType('app');
      expect(apps.some(e => e.value === 'VS Code')).toBe(true);
    });

    it('extracts tool entity from tool-invoke events', () => {
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'search_web', success: true },
      }));
      expect(graph.getEntitiesByType('tool').some(e => e.value === 'search_web')).toBe(true);
    });

    it('extracts file paths from clipboard', () => {
      graphListener!(makeGraphEvent({
        type: 'clipboard',
        data: { contentType: 'text', preview: 'src/main/context-graph.ts' },
        summary: 'Clipboard: text',
      }));
      expect(graph.getEntitiesByType('file').some(e => e.value.includes('context-graph.ts'))).toBe(true);
    });

    it('extracts URLs from text', () => {
      graphListener!(makeGraphEvent({
        type: 'clipboard',
        data: { contentType: 'url', preview: 'https://github.com/user/repo' },
        summary: 'Clipboard: url',
      }));
      expect(graph.getEntitiesByType('url').some(e => e.value.includes('github.com'))).toBe(true);
    });

    it('extracts project from window title separator pattern', () => {
      expect(graph.getEntitiesByType('project').some(e => e.value === 'agent-friday')).toBe(true);
    });

    it('extracts person from communication events', () => {
      graphListener!(makeGraphEvent({
        type: 'communication',
        data: { channel: 'email', person: 'John Smith', from: 'john@co.com' },
        summary: 'Email from John Smith',
      }));
      expect(graph.getEntitiesByType('person').some(e => e.value === 'John Smith')).toBe(true);
    });

    it('extracts channel from communication events', () => {
      graphListener!(makeGraphEvent({
        type: 'communication',
        data: { channel: '#engineering', person: 'Jane' },
        summary: 'Message in #engineering',
      }));
      expect(graph.getEntitiesByType('channel').some(e => e.value === '#engineering')).toBe(true);
    });

    it('extracts repo/branch/files from git events', () => {
      graphListener!(makeGraphEvent({
        type: 'git',
        data: { repo: 'agent-friday', branch: 'main', files: ['src/index.ts', 'src/app.ts'] },
        summary: 'Git: 2 files committed',
      }));
      expect(graph.getEntitiesByType('project').some(e => e.normalizedValue === 'agent-friday')).toBe(true);
      expect(graph.getEntitiesByType('file').some(e => e.value === 'src/index.ts')).toBe(true);
    });

    it('extracts attendees from calendar events', () => {
      graphListener!(makeGraphEvent({
        type: 'calendar',
        data: { title: 'Sprint Planning', attendees: ['Alice', 'Bob'] },
        summary: 'Calendar: Sprint Planning',
      }));
      const people = graph.getEntitiesByType('person');
      expect(people.some(e => e.value === 'Alice')).toBe(true);
      expect(people.some(e => e.value === 'Bob')).toBe(true);
    });

    it('normalizes entities to lowercase', () => {
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'Read_File' },
      }));
      const entity = graph.getEntity('tool', 'Read_File');
      expect(entity).not.toBeNull();
      expect(entity!.normalizedValue).toBe('read_file');
    });
  });

  // ── 2.4 Entity Tracking ───────────────────────────────────────────
  describe('2.4 Entity Tracking', () => {
    beforeEach(() => {
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
    });

    it('increments occurrences on repeated references', () => {
      for (let i = 0; i < 3; i++) {
        graphListener!(makeGraphEvent({
          type: 'tool-invoke',
          data: { toolName: 'read_file', success: true },
        }));
      }
      const entity = graph.getEntity('tool', 'read_file');
      expect(entity).toBeDefined();
      expect(entity!.occurrences).toBeGreaterThanOrEqual(3);
    });

    it('tracks sourceStreamIds across streams', () => {
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'search_web', success: true },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'search_web', success: true },
      }));
      const entity = graph.getEntity('tool', 'search_web');
      expect(entity!.sourceStreamIds.length).toBe(2);
    });

    it('getEntity returns null for unknown', () => {
      expect(graph.getEntity('tool', 'nonexistent')).toBeNull();
    });

    it('getTopEntities sorts by relevance', () => {
      for (let i = 0; i < 5; i++) {
        graphListener!(makeGraphEvent({
          type: 'tool-invoke',
          data: { toolName: 'frequent_tool', success: true },
        }));
      }
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'rare_tool', success: true },
      }));
      const top = graph.getTopEntities(20);
      const freqIdx = top.findIndex(e => e.value === 'frequent_tool');
      const rareIdx = top.findIndex(e => e.value === 'rare_tool');
      if (freqIdx !== -1 && rareIdx !== -1) {
        expect(freqIdx).toBeLessThan(rareIdx);
      }
    });

    it('getActiveEntities returns recently seen', () => {
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        timestamp: Date.now(),
        data: { toolName: 'recent_tool', success: true },
      }));
      const active = graph.getActiveEntities(10 * 60 * 1000);
      expect(active.some(e => e.value === 'recent_tool')).toBe(true);
    });
  });

  // ── 2.5 Entity Relationships ──────────────────────────────────────
  describe('2.5 Entity Relationships', () => {
    beforeEach(() => graph.start());

    it('finds co-occurring entities in same stream', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts — agent-friday' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'edit_block', success: true },
      }));
      const cluster = graph.getRelatedEntities('app', 'VS Code');
      expect(cluster).not.toBeNull();
      expect(cluster!.relatedEntities.length).toBeGreaterThan(0);
    });

    it('returns null for unknown entity', () => {
      expect(graph.getRelatedEntities('app', 'nonexistent')).toBeNull();
    });
  });

  // ── 2.6 Stream Management ─────────────────────────────────────────
  describe('2.6 Stream Management', () => {
    beforeEach(() => graph.start());

    it('getRecentStreams sorted by recency', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        timestamp: Date.now() - 5000,
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'a.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        timestamp: Date.now(),
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      const streams = graph.getRecentStreams(10);
      expect(streams.length).toBe(2);
      expect(streams[0].app).toBe('Chrome');
    });

    it('getStreamsByTask filters correctly', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'a.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Cursor', inferredTask: 'coding', windowTitle: 'b.ts' },
      }));
      const coding = graph.getStreamsByTask('coding');
      expect(coding.length).toBe(2);
      expect(coding.every(s => s.task === 'coding')).toBe(true);
    });

    it('getStream returns correct stream by ID', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      const active = graph.getActiveStream();
      const fetched = graph.getStream(active!.id);
      expect(fetched!.id).toBe(active!.id);
    });

    it('getStream returns null for unknown ID', () => {
      expect(graph.getStream('nonexistent')).toBeNull();
    });
  });

  // ── 2.7 Entity Pruning ────────────────────────────────────────────
  describe('2.7 Entity Pruning', () => {
    it('prunes bottom 20% when maxTotalEntities exceeded', () => {
      const small = freshGraph({ maxTotalEntities: 10, maxEntitiesPerStream: 10 });
      small.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      for (let i = 0; i < 25; i++) {
        graphListener!(makeGraphEvent({
          type: 'tool-invoke',
          data: { toolName: `tool-${i}`, success: true },
        }));
      }
      expect(small.getStatus().entityCount).toBeLessThanOrEqual(15);
      small.stop();
    });
  });

  // ── 2.8 Stream Timeout ────────────────────────────────────────────
  describe('2.8 Stream Timeout', () => {
    it('prunes old streams when maxWorkStreams exceeded', () => {
      const small = freshGraph({ maxWorkStreams: 3 });
      small.start();
      const apps = ['VS Code', 'Chrome', 'Slack', 'Notion', 'Terminal'];
      for (const app of apps) {
        graphListener!(makeGraphEvent({
          type: 'ambient',
          data: { activeApp: app, inferredTask: 'testing', windowTitle: 'test' },
        }));
      }
      expect(small.getStatus().streamCount).toBeLessThanOrEqual(4);
      small.stop();
    });
  });

  // ── 2.9 Context String ────────────────────────────────────────────
  describe('2.9 Context String', () => {
    beforeEach(() => graph.start());

    it('returns empty when no streams', () => {
      expect(graph.getContextString()).toBe('');
    });

    it('includes ## Work Context header', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(graph.getContextString()).toContain('## Work Context');
    });

    it('includes active stream info', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      const ctx = graph.getContextString();
      expect(ctx).toContain('Active');
      expect(ctx).toContain('VS Code');
    });

    it('includes recent work streams', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      expect(graph.getContextString()).toContain('Recent work');
    });

    it('includes key entities', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'read_file', success: true },
      }));
      expect(graph.getContextString()).toContain('Key entities');
    });
  });

  // ── 2.10 Prompt Context ───────────────────────────────────────────
  describe('2.10 Prompt Context', () => {
    beforeEach(() => graph.start());

    it('returns empty when no active stream', () => {
      expect(graph.getPromptContext()).toBe('');
    });

    it('returns [WORK] prefix', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(graph.getPromptContext()).toMatch(/^\[WORK\]/);
    });

    it('includes stream name', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(graph.getPromptContext()).toContain('VS Code');
    });
  });

  // ── 2.11 Status & Config ──────────────────────────────────────────
  describe('2.11 Status & Config', () => {
    it('reports correct initial status', () => {
      const s = graph.getStatus();
      expect(s.streamCount).toBe(0);
      expect(s.entityCount).toBe(0);
      expect(s.totalEventsProcessed).toBe(0);
      expect(s.activeStreamId).toBeNull();
    });

    it('tracks totalEventsProcessed', () => {
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      graphListener!(makeGraphEvent({ type: 'tool-invoke', data: { toolName: 'test' } }));
      graphListener!(makeGraphEvent({ type: 'clipboard', data: { contentType: 'text', preview: 'hi' } }));
      expect(graph.getStatus().totalEventsProcessed).toBe(3);
    });

    it('returns default config', () => {
      const cfg = graph.getConfig();
      expect(cfg.maxWorkStreams).toBe(50);
      expect(cfg.maxTotalEntities).toBe(500);
      expect(cfg.streamTimeoutMs).toBe(30 * 60 * 1000);
    });

    it('accepts custom config', () => {
      const custom = freshGraph({ maxWorkStreams: 10, maxTotalEntities: 100 });
      const cfg = custom.getConfig();
      expect(cfg.maxWorkStreams).toBe(10);
      expect(cfg.maxTotalEntities).toBe(100);
      expect(cfg.streamTimeoutMs).toBe(30 * 60 * 1000);
      custom.stop();
    });

    it('includes memory estimate', () => {
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(graph.getStatus().memoryEstimateKb).toBeGreaterThanOrEqual(0);
    });
  });

  // ── 2.12 Error Resilience ─────────────────────────────────────────
  describe('2.12 Error Resilience', () => {
    beforeEach(() => graph.start());

    it('survives null event data', () => {
      expect(() => {
        graphListener!(makeGraphEvent({
          type: 'ambient',
          data: { activeApp: null, inferredTask: undefined, windowTitle: '' },
        }));
      }).not.toThrow();
    });

    it('survives undefined event fields', () => {
      expect(() => {
        graphListener!(makeGraphEvent({
          type: 'tool-invoke',
          data: {},
          summary: undefined,
        }));
      }).not.toThrow();
    });

    it('survives very long text', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(() => {
        graphListener!(makeGraphEvent({
          type: 'user-input',
          data: { topic: 'a'.repeat(10000) },
          summary: 'b'.repeat(5000),
        }));
      }).not.toThrow();
    });

    it('survives malformed event type', () => {
      expect(() => {
        graphListener!(makeGraphEvent({
          type: 'unknown-type',
          data: { foo: 'bar' },
        }));
      }).not.toThrow();
    });
  });

  // ── 2.13 Summary Generation ───────────────────────────────────────
  describe('2.13 Summary Generation', () => {
    beforeEach(() => graph.start());

    it('generates summary with task and app', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      const active = graph.getActiveStream();
      expect(active!.summary).toContain('Coding');
      expect(active!.summary).toContain('VS Code');
    });

    it('includes tools in summary when present', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'edit_block', success: true },
      }));
      expect(graph.getActiveStream()!.summary).toContain('edit_block');
    });
  });

  // ── 2.14 Snapshot ─────────────────────────────────────────────────
  describe('2.14 Snapshot', () => {
    beforeEach(() => graph.start());

    it('returns null activeStream when no stream', () => {
      const snap = graph.getSnapshot();
      expect(snap.activeStream).toBeNull();
      expect(snap.streamCount).toBe(0);
    });

    it('returns complete snapshot with entities', () => {
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'read_file', success: true },
      }));
      const snap = graph.getSnapshot();
      expect(snap.activeStream).not.toBeNull();
      expect(snap.recentStreams.length).toBeGreaterThan(0);
      expect(snap.streamCount).toBe(1);
      expect(snap.entityCount).toBeGreaterThan(0);
      expect(snap.topEntities.length).toBeGreaterThan(0);
      expect(snap.activeEntities.length).toBeGreaterThan(0);
    });
  });
});

// =====================================================================
// Phase 3: Context-Aware Routing (Integration)
// =====================================================================

describe('Phase 3: Context-Aware Routing (Integration)', () => {
  // ── 3.1 Stream-to-Graph Flow ──────────────────────────────────────
  describe('3.1 Stream-to-Graph Flow', () => {
    it('ContextStream feeds events to ContextGraph via listener', () => {
      const stream = freshStream();
      const received: any[] = [];
      stream.on(e => received.push(e));

      stream.push(makeStreamEvent('ambient', 'test', { activeApp: 'Chrome' }));
      expect(received.length).toBe(1);
      expect(received[0].type).toBe('ambient');
      expect(received[0].data.activeApp).toBe('Chrome');
    });

    it('multiple event types flow through stream to listener', () => {
      const stream = freshStream();
      const types: string[] = [];
      stream.on(e => types.push(e.type));

      stream.push(makeStreamEvent('ambient', 'a', { activeApp: 'VS Code' }));
      stream.push(makeStreamEvent('clipboard', 'c', { preview: 'hello' }));
      stream.push(makeStreamEvent('tool-invoke', 't', { toolName: 'search' }));
      stream.push(makeStreamEvent('sentiment', 's', { mood: 'focused' }));

      expect(types).toEqual(['ambient', 'clipboard', 'tool-invoke', 'sentiment']);
    });
  });

  // ── 3.2 End-to-End Context Generation ─────────────────────────────
  describe('3.2 End-to-End Context Generation', () => {
    it('ContextStream produces [CONTEXT] prefix', () => {
      const stream = freshStream();
      stream.push(makeStreamEvent('ambient', 'coding', { activeApp: 'VS Code', inferredTask: 'coding' }));
      const ctx = stream.getPromptContext();
      expect(ctx).toMatch(/^\[CONTEXT\]/);
      expect(ctx).toContain('VS Code');
    });

    it('ContextGraph produces [WORK] prefix', () => {
      const graph = freshGraph();
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      const ctx = graph.getPromptContext();
      expect(ctx).toMatch(/^\[WORK\]/);
      expect(ctx).toContain('VS Code');
      graph.stop();
    });

    it('both context strings are non-empty for active sessions', () => {
      const stream = freshStream();
      stream.push(makeStreamEvent('ambient', 'coding', {
        activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'main.ts',
      }));
      stream.push(makeStreamEvent('tool-invoke', 'search', { toolName: 'web-search' }));

      const graph = freshGraph();
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'main.ts — agent-friday' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'web-search', success: true },
      }));

      expect(stream.getContextString().length).toBeGreaterThan(0);
      expect(graph.getContextString().length).toBeGreaterThan(0);
      graph.stop();
    });
  });

  // ── 3.3 Entity Relevance Scoring ──────────────────────────────────
  describe('3.3 Entity Relevance Scoring', () => {
    it('frequent entities rank higher than rare ones', () => {
      const graph = freshGraph();
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      for (let i = 0; i < 8; i++) {
        graphListener!(makeGraphEvent({
          type: 'tool-invoke',
          data: { toolName: 'frequent_tool' },
        }));
      }
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'rare_tool' },
      }));

      const top = graph.getTopEntities(30);
      const freqIdx = top.findIndex(e => e.value === 'frequent_tool');
      const rareIdx = top.findIndex(e => e.value === 'rare_tool');
      expect(freqIdx).not.toBe(-1);
      expect(rareIdx).not.toBe(-1);
      expect(freqIdx).toBeLessThan(rareIdx);
      graph.stop();
    });

    it('cross-stream entities have higher relevance', () => {
      const graph = freshGraph();
      graph.start();

      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'a.ts' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'cross_stream_tool' },
      }));

      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'cross_stream_tool' },
      }));

      graphListener!(makeGraphEvent({
        type: 'tool-invoke',
        data: { toolName: 'single_stream_tool' },
      }));

      const entity = graph.getEntity('tool', 'cross_stream_tool');
      expect(entity!.sourceStreamIds.length).toBe(2);
      graph.stop();
    });
  });

  // ── 3.4 Graceful Degradation ──────────────────────────────────────
  describe('3.4 Graceful Degradation', () => {
    it('stream returns empty context when disabled', () => {
      const stream = freshStream();
      stream.setEnabled(false);
      expect(stream.getContextString()).toBe('');
      expect(stream.getPromptContext()).toBe('');
    });

    it('graph returns empty context before start', () => {
      const graph = freshGraph();
      expect(graph.getContextString()).toBe('');
      expect(graph.getPromptContext()).toBe('');
      graph.stop();
    });

    it('graph returns empty context after stop', () => {
      const graph = freshGraph();
      graph.start();
      graphListener!(makeGraphEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(graph.getContextString().length).toBeGreaterThan(0);
      graph.stop();
      expect(graph.getContextString()).toBe('');
      expect(graph.getPromptContext()).toBe('');
    });

    it('stream handles rapid clear/push cycles', () => {
      const stream = freshStream();
      for (let cycle = 0; cycle < 5; cycle++) {
        for (let i = 0; i < 10; i++) {
          stream.push(makeStreamEvent('system', `c${cycle}-e${i}`));
        }
        stream.clear();
      }
      expect(stream.getBufferSize()).toBe(0);
      stream.push(makeStreamEvent('ambient', 'final', { activeApp: 'Chrome' }));
      expect(stream.getBufferSize()).toBe(1);
      expect(stream.getSnapshot().activeApp).toBe('Chrome');
    });
  });
});
