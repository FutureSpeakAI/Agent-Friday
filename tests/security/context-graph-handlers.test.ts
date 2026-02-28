/**
 * Tests for context-graph-handlers.ts — IPC layer for Track III Phase 2.
 * Validates handler registration, input validation, and delegation
 * to the ContextGraph singleton.
 *
 * cLaw Gate assertion: no persist/export operations are exposed via IPC.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock electron ────────────────────────────────────────────────────
const handlers = new Map<string, (...args: unknown[]) => unknown>();
vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    }),
  },
  BrowserWindow: vi.fn(),
}));

// ── Mock context stream (required by context-graph) ──────────────────
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    on: vi.fn(() => vi.fn()),
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
}));

// ── Mock context graph ──────────────────────────────────────────────
vi.mock('../../src/main/context-graph', () => ({
  contextGraph: {
    getSnapshot: vi.fn().mockReturnValue({
      activeStream: null,
      recentStreams: [],
      topEntities: [],
      activeEntities: [],
      streamCount: 0,
      entityCount: 0,
    }),
    getActiveStream: vi.fn().mockReturnValue(null),
    getRecentStreams: vi.fn().mockReturnValue([]),
    getStreamsByTask: vi.fn().mockReturnValue([]),
    getEntitiesByType: vi.fn().mockReturnValue([]),
    getTopEntities: vi.fn().mockReturnValue([]),
    getActiveEntities: vi.fn().mockReturnValue([]),
    getRelatedEntities: vi.fn().mockReturnValue(null),
    getContextString: vi.fn().mockReturnValue('## Work Context\n- Active: Testing'),
    getPromptContext: vi.fn().mockReturnValue('[WORK] stream: Testing'),
    getStatus: vi.fn().mockReturnValue({
      activeStreamId: null,
      streamCount: 0,
      entityCount: 0,
      totalEventsProcessed: 0,
      memoryEstimateKb: 0,
    }),
  },
}));

import { contextGraph } from '../../src/main/context-graph';
const mockGraph = vi.mocked(contextGraph);

import { registerContextGraphHandlers } from '../../src/main/ipc/context-graph-handlers';

// ── Helper ───────────────────────────────────────────────────────────
function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Context Graph Handlers — Track III Phase 2 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerContextGraphHandlers();
  });

  // ── Handler Registration ─────────────────────────────────────────
  describe('Handler Registration', () => {
    it('registers all expected IPC channels', () => {
      const expected = [
        'context-graph:snapshot',
        'context-graph:active-stream',
        'context-graph:recent-streams',
        'context-graph:streams-by-task',
        'context-graph:entities-by-type',
        'context-graph:top-entities',
        'context-graph:active-entities',
        'context-graph:related-entities',
        'context-graph:context-string',
        'context-graph:prompt-context',
        'context-graph:status',
      ];
      for (const channel of expected) {
        expect(handlers.has(channel), `Missing handler for ${channel}`).toBe(true);
      }
    });

    it('registers exactly 11 handlers', () => {
      expect(handlers.size).toBe(11);
    });
  });

  // ── Snapshot ──────────────────────────────────────────────────────
  describe('Snapshot', () => {
    it('delegates to contextGraph.getSnapshot', () => {
      invoke('context-graph:snapshot');
      expect(mockGraph.getSnapshot).toHaveBeenCalled();
    });
  });

  // ── Active Stream ────────────────────────────────────────────────
  describe('Active Stream', () => {
    it('delegates to contextGraph.getActiveStream', () => {
      invoke('context-graph:active-stream');
      expect(mockGraph.getActiveStream).toHaveBeenCalled();
    });
  });

  // ── Recent Streams ───────────────────────────────────────────────
  describe('Recent Streams', () => {
    it('delegates with default limit', () => {
      invoke('context-graph:recent-streams');
      expect(mockGraph.getRecentStreams).toHaveBeenCalledWith(10);
    });

    it('passes custom limit', () => {
      invoke('context-graph:recent-streams', 5);
      expect(mockGraph.getRecentStreams).toHaveBeenCalledWith(5);
    });

    it('caps limit at 50', () => {
      invoke('context-graph:recent-streams', 100);
      expect(mockGraph.getRecentStreams).toHaveBeenCalledWith(50);
    });

    it('ignores non-number limit', () => {
      invoke('context-graph:recent-streams', 'invalid');
      expect(mockGraph.getRecentStreams).toHaveBeenCalledWith(10);
    });
  });

  // ── Streams by Task ──────────────────────────────────────────────
  describe('Streams by Task', () => {
    it('delegates with valid task', () => {
      invoke('context-graph:streams-by-task', 'coding');
      expect(mockGraph.getStreamsByTask).toHaveBeenCalledWith('coding');
    });

    it('throws on empty task', () => {
      expect(() => invoke('context-graph:streams-by-task', '')).toThrow('requires a string task');
    });

    it('throws on non-string task', () => {
      expect(() => invoke('context-graph:streams-by-task', 42)).toThrow('requires a string task');
    });

    it('throws on missing task', () => {
      expect(() => invoke('context-graph:streams-by-task')).toThrow('requires a string task');
    });
  });

  // ── Entities by Type ─────────────────────────────────────────────
  describe('Entities by Type', () => {
    it('delegates with valid type', () => {
      invoke('context-graph:entities-by-type', 'file', 10);
      expect(mockGraph.getEntitiesByType).toHaveBeenCalledWith('file', 10);
    });

    it('validates all 8 entity types', () => {
      const types = ['file', 'app', 'person', 'topic', 'url', 'tool', 'project', 'channel'];
      for (const type of types) {
        vi.clearAllMocks();
        invoke('context-graph:entities-by-type', type);
        expect(mockGraph.getEntitiesByType).toHaveBeenCalled();
      }
    });

    it('throws on invalid entity type', () => {
      expect(() => invoke('context-graph:entities-by-type', 'invalid')).toThrow('valid entity type');
    });

    it('throws on empty type', () => {
      expect(() => invoke('context-graph:entities-by-type', '')).toThrow('valid entity type');
    });

    it('caps limit at 100', () => {
      invoke('context-graph:entities-by-type', 'tool', 200);
      expect(mockGraph.getEntitiesByType).toHaveBeenCalledWith('tool', 100);
    });
  });

  // ── Top Entities ─────────────────────────────────────────────────
  describe('Top Entities', () => {
    it('delegates with default limit', () => {
      invoke('context-graph:top-entities');
      expect(mockGraph.getTopEntities).toHaveBeenCalledWith(15);
    });

    it('passes custom limit', () => {
      invoke('context-graph:top-entities', 5);
      expect(mockGraph.getTopEntities).toHaveBeenCalledWith(5);
    });
  });

  // ── Active Entities ──────────────────────────────────────────────
  describe('Active Entities', () => {
    it('delegates with default window', () => {
      invoke('context-graph:active-entities');
      expect(mockGraph.getActiveEntities).toHaveBeenCalledWith(5 * 60 * 1000);
    });

    it('passes custom window', () => {
      invoke('context-graph:active-entities', 10 * 60 * 1000);
      expect(mockGraph.getActiveEntities).toHaveBeenCalledWith(10 * 60 * 1000);
    });

    it('caps window at 30 minutes', () => {
      invoke('context-graph:active-entities', 60 * 60 * 1000); // 1 hour
      expect(mockGraph.getActiveEntities).toHaveBeenCalledWith(30 * 60 * 1000);
    });
  });

  // ── Related Entities ─────────────────────────────────────────────
  describe('Related Entities', () => {
    it('delegates with valid parameters', () => {
      invoke('context-graph:related-entities', 'tool', 'read_file', 5);
      expect(mockGraph.getRelatedEntities).toHaveBeenCalledWith('tool', 'read_file', 5);
    });

    it('throws on invalid type', () => {
      expect(() =>
        invoke('context-graph:related-entities', 'invalid', 'value'),
      ).toThrow('valid entity type');
    });

    it('throws on empty value', () => {
      expect(() =>
        invoke('context-graph:related-entities', 'tool', ''),
      ).toThrow('requires a string value');
    });

    it('throws on non-string value', () => {
      expect(() =>
        invoke('context-graph:related-entities', 'tool', 42),
      ).toThrow('requires a string value');
    });
  });

  // ── Context String ───────────────────────────────────────────────
  describe('Context String', () => {
    it('delegates to getContextString', () => {
      const result = invoke('context-graph:context-string');
      expect(mockGraph.getContextString).toHaveBeenCalled();
      expect(result).toContain('Work Context');
    });
  });

  // ── Prompt Context ───────────────────────────────────────────────
  describe('Prompt Context', () => {
    it('delegates to getPromptContext', () => {
      const result = invoke('context-graph:prompt-context');
      expect(mockGraph.getPromptContext).toHaveBeenCalled();
      expect(result).toContain('[WORK]');
    });
  });

  // ── Status ───────────────────────────────────────────────────────
  describe('Status', () => {
    it('delegates to getStatus', () => {
      const result = invoke('context-graph:status') as Record<string, unknown>;
      expect(mockGraph.getStatus).toHaveBeenCalled();
      expect(result).toHaveProperty('streamCount', 0);
    });
  });

  // ── cLaw Gate: No Persistence via IPC ────────────────────────────
  describe('cLaw Gate: No Persistence via IPC', () => {
    it('does NOT expose any save/persist/export channels', () => {
      const allChannels = Array.from(handlers.keys());
      const forbidden = ['save', 'persist', 'export', 'write', 'dump'];
      for (const keyword of forbidden) {
        const matches = allChannels.filter(c => c.includes(keyword));
        expect(
          matches,
          `Found forbidden persistence channel(s): ${matches.join(', ')}`,
        ).toHaveLength(0);
      }
    });

    it('does NOT expose import/load channels', () => {
      const allChannels = Array.from(handlers.keys());
      const forbidden = ['import', 'load', 'restore'];
      for (const keyword of forbidden) {
        const matches = allChannels.filter(c => c.includes(keyword));
        expect(
          matches,
          `Found forbidden import channel(s): ${matches.join(', ')}`,
        ).toHaveLength(0);
      }
    });

    it('all channels are read-only queries', () => {
      const allChannels = Array.from(handlers.keys());
      // All context-graph channels should be read-only (queries only)
      for (const ch of allChannels) {
        expect(ch).toMatch(/^context-graph:/);
        // None should contain write-like verbs
        expect(ch).not.toMatch(/push|write|set|clear|delete|update|create/);
      }
    });
  });
});
