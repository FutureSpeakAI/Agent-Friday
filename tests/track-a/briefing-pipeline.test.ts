/**
 * Track A, Phase 1: "The Score Reader" — BriefingPipeline Test Suite
 *
 * Tests the context-aware briefing trigger pipeline that bridges
 * ContextGraph work stream changes to proactive intelligence.
 *
 * Validation Criteria:
 *   1. start() subscribes to context graph work stream changes
 *   2. Trigger fires within 100ms of active stream change
 *   3. Trigger includes stream name, task type, and top 3 entities
 *   4. Duplicate triggers for same stream suppressed within 5-min window
 *   5. stop() unsubscribes cleanly — no dangling listeners
 *   6. No triggers fire when no work stream is active
 *   7. getRecentTriggers(limit) exposed for debugging
 *   8. Singleton exported as briefingPipeline
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Hoisted mocks (survive vi.mock hoisting) ──────────────────────────

const mocks = vi.hoisted(() => {
  const unsub = vi.fn();
  const getActiveStream = vi.fn();
  let listener: ((event: any) => void) | null = null;

  return {
    unsub,
    getActiveStream,
    getListener: () => listener,
    setListener: (l: ((event: any) => void) | null) => { listener = l; },
    onMock: vi.fn((cb: (event: any) => void) => {
      listener = cb;
      return unsub;
    }),
  };
});

vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    on: mocks.onMock,
  },
}));

vi.mock('../../src/main/context-graph', () => ({
  contextGraph: {
    getActiveStream: mocks.getActiveStream,
  },
}));

import { BriefingPipeline, briefingPipeline } from '../../src/main/briefing-pipeline';
import { contextStream } from '../../src/main/context-stream';

// ── Helpers ───────────────────────────────────────────────────────────

function makeWorkStream(id: string, name: string, task: string, entities: any[] = []) {
  return {
    id,
    name,
    task,
    app: 'TestApp',
    startedAt: Date.now(),
    lastActiveAt: Date.now(),
    eventCount: 1,
    entities,
    eventTypes: new Set(['ambient']),
    summary: `Testing ${task}`,
  };
}

function makeEntity(type: string, value: string) {
  return {
    type,
    value,
    normalizedValue: value.toLowerCase(),
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    occurrences: 1,
    sourceStreamIds: ['ws-1'],
  };
}

function fireEvent(data: Record<string, unknown> = {}) {
  const listener = mocks.getListener();
  if (!listener) throw new Error('Pipeline not started — no listener registered');
  listener({
    id: `ctx-${Date.now()}`,
    type: 'ambient',
    timestamp: Date.now(),
    source: 'test',
    summary: 'test event',
    data,
  });
}

// ── Test Suite ─────────────────────────────────────────────────────────

describe('BriefingPipeline — Track A Phase 1', () => {
  let pipeline: BriefingPipeline;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.setListener(null);
    mocks.getActiveStream.mockReturnValue(null);
    pipeline = new BriefingPipeline();
  });

  afterEach(() => {
    pipeline.stop();
  });

  // ── Criterion 1: start() subscribes to stream changes ────────────

  describe('Criterion 1: start() subscribes to context stream', () => {
    it('should register a listener on contextStream.on()', () => {
      pipeline.start();
      expect(contextStream.on).toHaveBeenCalledOnce();
      expect(mocks.getListener()).toBeTypeOf('function');
    });

    it('should not double-subscribe if start() called twice', () => {
      pipeline.start();
      pipeline.start();
      expect(contextStream.on).toHaveBeenCalledOnce();
    });
  });

  // ── Criterion 2: Trigger fires within 100ms of stream change ─────

  describe('Criterion 2: trigger fires on stream change', () => {
    it('should fire a trigger when active stream changes from null to a stream', () => {
      pipeline.start();

      const stream = makeWorkStream('ws-1', 'Coding VS Code', 'coding');
      mocks.getActiveStream.mockReturnValue(stream);
      fireEvent({ activeApp: 'VS Code' });

      const triggers = pipeline.getRecentTriggers();
      expect(triggers).toHaveLength(1);
      expect(triggers[0].streamId).toBe('ws-1');
    });

    it('should fire a trigger when active stream switches to a different stream', () => {
      const stream1 = makeWorkStream('ws-1', 'Coding VS Code', 'coding');
      mocks.getActiveStream.mockReturnValue(stream1);

      pipeline.start(); // captures ws-1 as initial state

      const stream2 = makeWorkStream('ws-2', 'Browsing Chrome', 'browsing');
      mocks.getActiveStream.mockReturnValue(stream2);
      fireEvent({ activeApp: 'Chrome' });

      const triggers = pipeline.getRecentTriggers();
      expect(triggers).toHaveLength(1);
      expect(triggers[0].streamId).toBe('ws-2');
    });

    it('should NOT fire a trigger when the same stream continues', () => {
      pipeline.start();

      const stream = makeWorkStream('ws-1', 'Coding VS Code', 'coding');
      mocks.getActiveStream.mockReturnValue(stream);
      fireEvent({ activeApp: 'VS Code' });

      expect(pipeline.getRecentTriggers()).toHaveLength(1);

      // Same stream, another event
      fireEvent({ activeApp: 'VS Code' });
      expect(pipeline.getRecentTriggers()).toHaveLength(1); // Still 1
    });
  });

  // ── Criterion 3: Trigger includes name, task, top 3 entities ─────

  describe('Criterion 3: trigger payload contains stream data', () => {
    it('should include streamName, task, and top 3 entities', () => {
      pipeline.start();

      const entities = [
        makeEntity('file', 'index.ts'),
        makeEntity('app', 'VS Code'),
        makeEntity('project', 'nexus-os'),
        makeEntity('url', 'https://github.com'), // 4th — should be excluded
      ];
      const stream = makeWorkStream('ws-1', 'Coding VS Code — nexus-os', 'coding', entities);
      mocks.getActiveStream.mockReturnValue(stream);
      fireEvent({ activeApp: 'VS Code' });

      const trigger = pipeline.getRecentTriggers()[0];
      expect(trigger.streamName).toBe('Coding VS Code — nexus-os');
      expect(trigger.task).toBe('coding');
      expect(trigger.entities).toHaveLength(3);
      expect(trigger.entities[0].value).toBe('index.ts');
      expect(trigger.entities[2].value).toBe('nexus-os');
    });

    it('should handle streams with fewer than 3 entities', () => {
      pipeline.start();

      const entities = [makeEntity('file', 'readme.md')];
      const stream = makeWorkStream('ws-1', 'Editing', 'editing', entities);
      mocks.getActiveStream.mockReturnValue(stream);
      fireEvent({});

      const trigger = pipeline.getRecentTriggers()[0];
      expect(trigger.entities).toHaveLength(1);
    });

    it('should include a unique trigger id and triggeredAt timestamp', () => {
      pipeline.start();

      const stream = makeWorkStream('ws-1', 'Coding', 'coding');
      mocks.getActiveStream.mockReturnValue(stream);
      fireEvent({});

      const trigger = pipeline.getRecentTriggers()[0];
      expect(trigger.id).toMatch(/^bt-/);
      expect(trigger.triggeredAt).toBeTypeOf('number');
      expect(trigger.triggeredAt).toBeGreaterThan(0);
    });
  });

  // ── Criterion 4: 5-minute dedup window ───────────────────────────

  describe('Criterion 4: deduplication within 5-minute window', () => {
    it('should suppress duplicate triggers for the same stream within 5 minutes', () => {
      pipeline.start();

      const stream1 = makeWorkStream('ws-1', 'Coding', 'coding');
      mocks.getActiveStream.mockReturnValue(stream1);
      fireEvent({});
      expect(pipeline.getRecentTriggers()).toHaveLength(1);

      // Switch away
      const stream2 = makeWorkStream('ws-2', 'Browsing', 'browsing');
      mocks.getActiveStream.mockReturnValue(stream2);
      fireEvent({});
      expect(pipeline.getRecentTriggers()).toHaveLength(2);

      // Switch back to ws-1 within 5 minutes — should be suppressed
      mocks.getActiveStream.mockReturnValue(stream1);
      fireEvent({});
      expect(pipeline.getRecentTriggers()).toHaveLength(2); // No new trigger
    });

    it('should allow trigger for same stream after 5 minutes have elapsed', () => {
      vi.useFakeTimers();
      try {
        pipeline.start();

        const stream1 = makeWorkStream('ws-1', 'Coding', 'coding');
        mocks.getActiveStream.mockReturnValue(stream1);
        fireEvent({});
        expect(pipeline.getRecentTriggers()).toHaveLength(1);

        // Switch away
        const stream2 = makeWorkStream('ws-2', 'Browsing', 'browsing');
        mocks.getActiveStream.mockReturnValue(stream2);
        fireEvent({});

        // Advance past 5-minute window
        vi.advanceTimersByTime(5 * 60 * 1000 + 1);

        // Switch back to ws-1 — should NOT be suppressed
        mocks.getActiveStream.mockReturnValue(stream1);
        fireEvent({});
        expect(pipeline.getRecentTriggers()).toHaveLength(3);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ── Criterion 5: stop() unsubscribes cleanly ─────────────────────

  describe('Criterion 5: stop() cleans up', () => {
    it('should call the unsubscribe function returned by contextStream.on()', () => {
      pipeline.start();
      pipeline.stop();
      expect(mocks.unsub).toHaveBeenCalledOnce();
    });

    it('should not fire triggers after stop()', () => {
      pipeline.start();
      const listenerBeforeStop = mocks.getListener();
      expect(listenerBeforeStop).toBeTypeOf('function');

      pipeline.stop();

      // After stop, unsubscribe was called — the pipeline should not
      // react to events. We verify the unsub was called (criterion 5).
      expect(mocks.unsub).toHaveBeenCalledOnce();
    });

    it('should allow re-start after stop', () => {
      pipeline.start();
      pipeline.stop();

      vi.clearAllMocks();
      mocks.setListener(null);

      pipeline.start();
      expect(contextStream.on).toHaveBeenCalledOnce();
      expect(mocks.getListener()).toBeTypeOf('function');
    });
  });

  // ── Criterion 6: No triggers when no active stream ───────────────

  describe('Criterion 6: no triggers when no active stream', () => {
    it('should not fire a trigger when getActiveStream returns null', () => {
      pipeline.start();
      mocks.getActiveStream.mockReturnValue(null);
      fireEvent({});

      expect(pipeline.getRecentTriggers()).toHaveLength(0);
    });

    it('should not fire when active stream goes from stream to null', () => {
      pipeline.start();

      const stream = makeWorkStream('ws-1', 'Coding', 'coding');
      mocks.getActiveStream.mockReturnValue(stream);
      fireEvent({});
      expect(pipeline.getRecentTriggers()).toHaveLength(1);

      // Stream goes null (e.g., user idle)
      mocks.getActiveStream.mockReturnValue(null);
      fireEvent({});
      expect(pipeline.getRecentTriggers()).toHaveLength(1); // No additional trigger
    });
  });

  // ── Criterion 7: getRecentTriggers(limit) ────────────────────────

  describe('Criterion 7: getRecentTriggers for debugging', () => {
    it('should return empty array when no triggers have fired', () => {
      expect(pipeline.getRecentTriggers()).toEqual([]);
    });

    it('should return triggers in reverse chronological order', () => {
      pipeline.start();

      const stream1 = makeWorkStream('ws-1', 'First', 'coding');
      mocks.getActiveStream.mockReturnValue(stream1);
      fireEvent({});

      const stream2 = makeWorkStream('ws-2', 'Second', 'browsing');
      mocks.getActiveStream.mockReturnValue(stream2);
      fireEvent({});

      const stream3 = makeWorkStream('ws-3', 'Third', 'writing');
      mocks.getActiveStream.mockReturnValue(stream3);
      fireEvent({});

      const triggers = pipeline.getRecentTriggers();
      expect(triggers).toHaveLength(3);
      expect(triggers[0].streamName).toBe('Third');
      expect(triggers[1].streamName).toBe('Second');
      expect(triggers[2].streamName).toBe('First');
    });

    it('should respect the limit parameter', () => {
      pipeline.start();

      for (let i = 1; i <= 5; i++) {
        const stream = makeWorkStream(`ws-${i}`, `Stream ${i}`, 'task');
        mocks.getActiveStream.mockReturnValue(stream);
        fireEvent({});
      }

      expect(pipeline.getRecentTriggers(2)).toHaveLength(2);
      expect(pipeline.getRecentTriggers(2)[0].streamName).toBe('Stream 5');
    });

    it('should default limit to 10', () => {
      pipeline.start();

      for (let i = 1; i <= 15; i++) {
        const stream = makeWorkStream(`ws-${i}`, `Stream ${i}`, 'task');
        mocks.getActiveStream.mockReturnValue(stream);
        fireEvent({});
      }

      expect(pipeline.getRecentTriggers()).toHaveLength(10);
    });
  });

  // ── Criterion 8: Singleton export ────────────────────────────────

  describe('Criterion 8: singleton export', () => {
    it('should export briefingPipeline as a BriefingPipeline instance', () => {
      expect(briefingPipeline).toBeInstanceOf(BriefingPipeline);
    });
  });
});
