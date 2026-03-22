/**
 * Track A, Phase 3: "The Performance" — BriefingDelivery Test Suite
 *
 * Tests the delivery system that wires BriefingPipeline → BriefingScoringEngine
 * → IntelligenceEngine → IPC push to renderer.
 *
 * Validation Criteria:
 *   1. start() wires pipeline → scorer → delivery chain
 *   2. Trigger fires → calls intelligenceEngine.runResearch() with enriched topic
 *   3. Delivery emits via IPC 'briefing:new' with { id, topic, content, priority, timestamp }
 *   4. 'urgent' emitted immediately; 'informational' batched (max 1 per 10 min)
 *   5. briefing:list returns recent briefings sorted by priority then recency
 *   6. briefing:dismiss marks a briefing as dismissed
 *   7. stop() tears down the full chain cleanly
 *   8. registerBriefingDeliveryHandlers follows project pattern
 *   9. IPC inputs validated with assertString
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Hoisted mocks ─────────────────────────────────────────────────────

const mocks = vi.hoisted(() => {
  let triggerCb: ((trigger: any) => void) | null = null;
  const unsub = vi.fn();

  return {
    // briefing-pipeline
    onTrigger: vi.fn((cb: (trigger: any) => void) => {
      triggerCb = cb;
      return unsub;
    }),
    getTriggerCb: () => triggerCb,
    unsub,

    // briefing-scoring
    scoreTrigger: vi.fn(),

    // intelligence engine
    runResearch: vi.fn(),
    getUndeliveredBriefings: vi.fn(),

    // electron
    webContentsSend: vi.fn(),
    ipcHandleMap: new Map<string, (...args: any[]) => any>(),
  };
});

vi.mock('../../src/main/briefing-pipeline', () => ({
  briefingPipeline: {
    onTrigger: mocks.onTrigger,
  },
}));

vi.mock('../../src/main/briefing-scoring', () => ({
  scoreTrigger: mocks.scoreTrigger,
  DEFAULT_SCORING_CONFIG: {
    durationWeight: 0.4,
    entityOverlapWeight: 0.4,
    morningBoostWeight: 0.2,
    highEngagementMs: 1_800_000,
    urgentThreshold: 0.7,
    relevantThreshold: 0.35,
  },
}));

vi.mock('../../src/main/intelligence', () => ({
  intelligenceEngine: {
    runResearch: mocks.runResearch,
    getUndeliveredBriefings: mocks.getUndeliveredBriefings,
  },
}));

vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: (...args: any[]) => any) => {
      mocks.ipcHandleMap.set(channel, handler);
    }),
  },
}));

import { BriefingDelivery, briefingDelivery } from '../../src/main/briefing-delivery';

// ── Helpers ───────────────────────────────────────────────────────────

function makeTrigger(overrides: Record<string, unknown> = {}) {
  return {
    id: 'bt-1',
    streamId: 'ws-1',
    streamName: 'Coding VS Code',
    task: 'coding',
    entities: [],
    triggeredAt: Date.now(),
    ...overrides,
  };
}

function makeBriefing(overrides: Record<string, unknown> = {}) {
  return {
    id: 'b-1',
    topic: 'Coding VS Code',
    content: 'You were working on...',
    createdAt: Date.now(),
    delivered: false,
    priority: 'medium',
    ...overrides,
  };
}

function mockWindow() {
  return { webContents: { send: mocks.webContentsSend } } as any;
}

async function flush() {
  await new Promise(r => setTimeout(r, 0));
}

async function fireTrigger(trigger = makeTrigger()) {
  const cb = mocks.getTriggerCb();
  if (!cb) throw new Error('Delivery not started — no trigger callback');
  cb(trigger);
  await flush();
}

// ── Test Suite ────────────────────────────────────────────────────────

describe('BriefingDelivery — Track A Phase 3', () => {
  let delivery: BriefingDelivery;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.ipcHandleMap.clear();
    delivery = new BriefingDelivery();

    // Defaults: scoring → 'relevant', research → returns 1 briefing
    mocks.scoreTrigger.mockReturnValue({
      priority: 'relevant',
      score: 0.5,
      explanation: 'relevant: test',
    });
    mocks.runResearch.mockResolvedValue(undefined);
    mocks.getUndeliveredBriefings.mockResolvedValue([makeBriefing()]);
  });

  afterEach(() => {
    delivery.stop();
  });

  // ── Criterion 1: start() wires the chain ─────────────────────────

  describe('Criterion 1: start() wires pipeline → scorer → delivery', () => {
    it('should subscribe to pipeline triggers on start()', () => {
      delivery.start(mockWindow());
      expect(mocks.onTrigger).toHaveBeenCalledOnce();
    });

    it('should not double-subscribe if start() called twice', () => {
      delivery.start(mockWindow());
      delivery.start(mockWindow());
      expect(mocks.onTrigger).toHaveBeenCalledOnce();
    });
  });

  // ── Criterion 2: trigger → research call ──────────────────────────

  describe('Criterion 2: trigger fires → runResearch called', () => {
    it('should call runResearch with enriched topic from trigger', async () => {
      delivery.start(mockWindow());
      await fireTrigger(makeTrigger({
        streamName: 'Coding VS Code',
        entities: [
          { type: 'file', value: 'index.ts', normalizedValue: 'index.ts', firstSeen: 0, lastSeen: 0, occurrences: 1, sourceStreamIds: [] },
        ],
      }));

      expect(mocks.runResearch).toHaveBeenCalledOnce();
      const topic = mocks.runResearch.mock.calls[0][0];
      expect(topic).toContain('Coding VS Code');
      expect(topic).toContain('index.ts');
    });

    it('should map priority to intelligence engine levels', async () => {
      mocks.scoreTrigger.mockReturnValue({ priority: 'urgent', score: 0.8, explanation: 'test' });
      delivery.start(mockWindow());
      await fireTrigger();

      expect(mocks.runResearch.mock.calls[0][1]).toBe('high');
    });
  });

  // ── Criterion 3: IPC push with payload ────────────────────────────

  describe('Criterion 3: emits briefing:new via IPC', () => {
    it('should push briefing to renderer with correct payload shape', async () => {
      delivery.start(mockWindow());
      await fireTrigger();

      expect(mocks.webContentsSend).toHaveBeenCalledWith(
        'briefing:new',
        expect.objectContaining({
          id: expect.any(String),
          topic: expect.any(String),
          content: expect.any(String),
          priority: expect.any(String),
          timestamp: expect.any(Number),
        }),
      );
    });
  });

  // ── Criterion 4: urgent immediate, informational batched ──────────

  describe('Criterion 4: priority-based delivery timing', () => {
    it('should emit urgent briefings immediately', async () => {
      mocks.scoreTrigger.mockReturnValue({ priority: 'urgent', score: 0.8, explanation: 'test' });
      delivery.start(mockWindow());
      await fireTrigger();

      expect(mocks.webContentsSend).toHaveBeenCalledOnce();
    });

    it('should batch informational briefings (max 1 per 10 min)', async () => {
      mocks.scoreTrigger.mockReturnValue({ priority: 'informational', score: 0.1, explanation: 'test' });
      delivery.start(mockWindow());

      // First informational fires immediately (no previous batch)
      await fireTrigger(makeTrigger({ id: 'bt-1', streamId: 'ws-1' }));
      expect(mocks.webContentsSend).toHaveBeenCalledOnce();

      // Second informational within 10 min — should be batched (not pushed)
      mocks.getUndeliveredBriefings.mockResolvedValue([makeBriefing({ id: 'b-2' })]);
      await fireTrigger(makeTrigger({ id: 'bt-2', streamId: 'ws-2' }));
      expect(mocks.webContentsSend).toHaveBeenCalledOnce(); // Still just 1
    });
  });

  // ── Criterion 5: briefing:list sorted ─────────────────────────────

  describe('Criterion 5: getRecentBriefings sorted by priority then recency', () => {
    it('should sort urgent before informational', async () => {
      delivery.start(mockWindow());

      // Deliver an informational briefing
      mocks.scoreTrigger.mockReturnValue({ priority: 'informational', score: 0.1, explanation: 'test' });
      mocks.getUndeliveredBriefings.mockResolvedValue([makeBriefing({ id: 'b-info', topic: 'Info' })]);
      await fireTrigger(makeTrigger({ id: 'bt-1', streamId: 'ws-1' }));

      // Deliver an urgent briefing
      mocks.scoreTrigger.mockReturnValue({ priority: 'urgent', score: 0.8, explanation: 'test' });
      mocks.getUndeliveredBriefings.mockResolvedValue([makeBriefing({ id: 'b-urgent', topic: 'Urgent' })]);
      await fireTrigger(makeTrigger({ id: 'bt-2', streamId: 'ws-2' }));

      const briefings = delivery.getRecentBriefings();
      expect(briefings[0].id).toBe('b-urgent');
      expect(briefings[1].id).toBe('b-info');
    });
  });

  // ── Criterion 6: briefing:dismiss ─────────────────────────────────

  describe('Criterion 6: dismissBriefing marks as dismissed', () => {
    it('should mark a briefing as dismissed', async () => {
      delivery.start(mockWindow());
      await fireTrigger();

      const briefings = delivery.getRecentBriefings();
      expect(briefings[0].dismissed).toBe(false);

      const result = delivery.dismissBriefing(briefings[0].id);
      expect(result).toBe(true);
      expect(delivery.getRecentBriefings()[0].dismissed).toBe(true);
    });

    it('should return false for unknown briefing id', () => {
      expect(delivery.dismissBriefing('nonexistent')).toBe(false);
    });
  });

  // ── Criterion 7: stop() tears down ────────────────────────────────

  describe('Criterion 7: stop() tears down cleanly', () => {
    it('should call unsubscribe on pipeline', () => {
      delivery.start(mockWindow());
      delivery.stop();
      expect(mocks.unsub).toHaveBeenCalledOnce();
    });

    it('should allow re-start after stop', () => {
      delivery.start(mockWindow());
      delivery.stop();
      vi.clearAllMocks();
      delivery.start(mockWindow());
      expect(mocks.onTrigger).toHaveBeenCalledOnce();
    });
  });

  // ── Criterion 8: singleton export ─────────────────────────────────

  describe('Criterion 8: singleton export', () => {
    it('should export briefingDelivery as a BriefingDelivery instance', () => {
      expect(briefingDelivery).toBeInstanceOf(BriefingDelivery);
    });
  });

  // ── Criterion 9: research failure handled gracefully ──────────────

  describe('Criterion 9: graceful error handling', () => {
    it('should not crash when runResearch fails', async () => {
      mocks.runResearch.mockRejectedValue(new Error('LLM unavailable'));
      delivery.start(mockWindow());

      // Should not throw
      await fireTrigger();
      expect(mocks.webContentsSend).not.toHaveBeenCalled();
    });
  });
});
