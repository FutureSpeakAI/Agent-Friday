/**
 * Tests for context-stream-handlers.ts — IPC layer for Track III Phase 1:
 * Activity Ingestion. Validates input validation, handler registration,
 * and correct delegation to the ContextStream singleton.
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

// ── Mock context stream ──────────────────────────────────────────────
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    push: vi.fn().mockReturnValue({ id: 'ctx-test', timestamp: Date.now(), type: 'system', source: 'test', summary: 'test', data: {} }),
    getSnapshot: vi.fn().mockReturnValue({
      activeApp: 'VS Code',
      windowTitle: 'test.ts',
      inferredTask: 'coding',
      focusStreak: 120,
      currentMood: 'focused',
      moodConfidence: 0.8,
      energyLevel: 0.7,
      lastClipboardType: 'code',
      lastClipboardPreview: 'const x = 1',
      recentToolCalls: ['search', 'read_file'],
      recentNotifications: ['Slack: new message'],
      activeWorkStream: '',
      lastUpdated: Date.now(),
    }),
    getRecent: vi.fn().mockReturnValue([]),
    getByType: vi.fn().mockReturnValue([]),
    getLatestByType: vi.fn().mockReturnValue(new Map()),
    getContextString: vi.fn().mockReturnValue('## Activity Stream\n- Using VS Code'),
    getPromptContext: vi.fn().mockReturnValue('[CONTEXT] coding VS Code | mood: focused'),
    getStatus: vi.fn().mockReturnValue({
      enabled: true,
      bufferSize: 42,
      maxBufferSize: 2000,
      oldestEventAge: 30000,
      eventCounts: { ambient: 10, clipboard: 5 },
      eventsPerMinute: 3,
      memoryEstimateKb: 12,
    }),
    prune: vi.fn().mockReturnValue(5),
    setEnabled: vi.fn(),
    clear: vi.fn(),
  },
}));

import { contextStream } from '../../src/main/context-stream';
const mockStream = vi.mocked(contextStream);

import { registerContextStreamHandlers } from '../../src/main/ipc/context-stream-handlers';

// ── Helper ───────────────────────────────────────────────────────────
function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Context Stream Handlers — Track III Phase 1 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerContextStreamHandlers();
  });

  // ── Handler Registration ─────────────────────────────────────────
  describe('Handler Registration', () => {
    it('registers all expected IPC channels', () => {
      const expected = [
        'context-stream:push',
        'context-stream:snapshot',
        'context-stream:recent',
        'context-stream:by-type',
        'context-stream:latest-by-type',
        'context-stream:context-string',
        'context-stream:prompt-context',
        'context-stream:status',
        'context-stream:prune',
        'context-stream:set-enabled',
        'context-stream:clear',
      ];
      for (const channel of expected) {
        expect(handlers.has(channel), `Missing handler for ${channel}`).toBe(true);
      }
    });

    it('registers exactly 11 handlers', () => {
      expect(handlers.size).toBe(11);
    });
  });

  // ── Push Event ──────────────────────────────────────────────────
  describe('Push Event', () => {
    it('delegates valid event to contextStream.push', () => {
      invoke('context-stream:push', {
        type: 'ambient',
        source: 'test-source',
        summary: 'Testing push',
        data: { app: 'VS Code' },
      });

      expect(mockStream.push).toHaveBeenCalledWith({
        type: 'ambient',
        source: 'test-source',
        summary: 'Testing push',
        data: { app: 'VS Code' },
        dedupeKey: undefined,
        ttlMs: undefined,
      });
    });

    it('passes optional dedupeKey and ttlMs', () => {
      invoke('context-stream:push', {
        type: 'clipboard',
        source: 'test',
        summary: 'clip',
        data: {},
        dedupeKey: 'clip-1',
        ttlMs: 60000,
      });

      expect(mockStream.push).toHaveBeenCalledWith(
        expect.objectContaining({
          dedupeKey: 'clip-1',
          ttlMs: 60000,
        }),
      );
    });

    it('throws on null payload', () => {
      expect(() => invoke('context-stream:push', null)).toThrow('requires an event object');
    });

    it('throws on non-object payload', () => {
      expect(() => invoke('context-stream:push', 'not-an-object')).toThrow('requires an event object');
    });

    it('throws on invalid event type', () => {
      expect(() =>
        invoke('context-stream:push', {
          type: 'invalid-type',
          source: 'test',
          summary: 'test',
        }),
      ).toThrow('valid event type');
    });

    it('throws on empty event type', () => {
      expect(() =>
        invoke('context-stream:push', {
          type: '',
          source: 'test',
          summary: 'test',
        }),
      ).toThrow('valid event type');
    });

    it('throws on missing source', () => {
      expect(() =>
        invoke('context-stream:push', {
          type: 'ambient',
          summary: 'test',
        }),
      ).toThrow('requires a string source');
    });

    it('throws on empty source', () => {
      expect(() =>
        invoke('context-stream:push', {
          type: 'ambient',
          source: '',
          summary: 'test',
        }),
      ).toThrow('requires a string source');
    });

    it('throws on missing summary', () => {
      expect(() =>
        invoke('context-stream:push', {
          type: 'ambient',
          source: 'test',
        }),
      ).toThrow('requires a string summary');
    });

    it('handles missing data gracefully (defaults to empty object)', () => {
      invoke('context-stream:push', {
        type: 'system',
        source: 'test',
        summary: 'test',
      });

      expect(mockStream.push).toHaveBeenCalledWith(
        expect.objectContaining({ data: {} }),
      );
    });

    it('validates all 11 event types as valid', () => {
      const validTypes = [
        'ambient', 'clipboard', 'sentiment', 'notification', 'tool-invoke',
        'calendar', 'communication', 'git', 'screen-text', 'user-input', 'system',
      ];
      for (const type of validTypes) {
        vi.clearAllMocks();
        invoke('context-stream:push', { type, source: 'test', summary: 'test' });
        expect(mockStream.push).toHaveBeenCalledTimes(1);
      }
    });
  });

  // ── Snapshot ────────────────────────────────────────────────────
  describe('Snapshot', () => {
    it('delegates to getSnapshot', () => {
      const result = invoke('context-stream:snapshot');
      expect(mockStream.getSnapshot).toHaveBeenCalled();
      expect(result).toHaveProperty('activeApp', 'VS Code');
    });
  });

  // ── Recent Events ──────────────────────────────────────────────
  describe('Recent Events', () => {
    it('delegates with no options', () => {
      invoke('context-stream:recent');
      expect(mockStream.getRecent).toHaveBeenCalledWith({
        limit: undefined,
        types: undefined,
        sinceMs: undefined,
      });
    });

    it('passes limit option', () => {
      invoke('context-stream:recent', { limit: 10 });
      expect(mockStream.getRecent).toHaveBeenCalledWith(
        expect.objectContaining({ limit: 10 }),
      );
    });

    it('filters invalid event types from types array', () => {
      invoke('context-stream:recent', { types: ['ambient', 'invalid', 'clipboard'] });
      expect(mockStream.getRecent).toHaveBeenCalledWith(
        expect.objectContaining({ types: ['ambient', 'clipboard'] }),
      );
    });

    it('passes sinceMs option', () => {
      invoke('context-stream:recent', { sinceMs: 60000 });
      expect(mockStream.getRecent).toHaveBeenCalledWith(
        expect.objectContaining({ sinceMs: 60000 }),
      );
    });
  });

  // ── By Type ────────────────────────────────────────────────────
  describe('By Type', () => {
    it('delegates with valid type', () => {
      invoke('context-stream:by-type', 'ambient', 5);
      expect(mockStream.getByType).toHaveBeenCalledWith('ambient', 5);
    });

    it('works without limit', () => {
      invoke('context-stream:by-type', 'clipboard');
      expect(mockStream.getByType).toHaveBeenCalledWith('clipboard', undefined);
    });

    it('throws on invalid type', () => {
      expect(() => invoke('context-stream:by-type', 'invalid')).toThrow('valid event type');
    });

    it('throws on empty type', () => {
      expect(() => invoke('context-stream:by-type', '')).toThrow('valid event type');
    });

    it('throws on non-string type', () => {
      expect(() => invoke('context-stream:by-type', 42)).toThrow('valid event type');
    });
  });

  // ── Latest By Type ─────────────────────────────────────────────
  describe('Latest By Type', () => {
    it('delegates and converts Map to plain object', () => {
      const result = invoke('context-stream:latest-by-type');
      expect(mockStream.getLatestByType).toHaveBeenCalled();
      expect(typeof result).toBe('object');
    });
  });

  // ── Context String ─────────────────────────────────────────────
  describe('Context String', () => {
    it('delegates to getContextString', () => {
      const result = invoke('context-stream:context-string');
      expect(mockStream.getContextString).toHaveBeenCalled();
      expect(result).toContain('Activity Stream');
    });
  });

  // ── Prompt Context ─────────────────────────────────────────────
  describe('Prompt Context', () => {
    it('delegates to getPromptContext', () => {
      const result = invoke('context-stream:prompt-context');
      expect(mockStream.getPromptContext).toHaveBeenCalled();
      expect(result).toContain('[CONTEXT]');
    });
  });

  // ── Status ─────────────────────────────────────────────────────
  describe('Status', () => {
    it('delegates to getStatus', () => {
      const result = invoke('context-stream:status') as Record<string, unknown>;
      expect(mockStream.getStatus).toHaveBeenCalled();
      expect(result).toHaveProperty('bufferSize', 42);
    });
  });

  // ── Prune ──────────────────────────────────────────────────────
  describe('Prune', () => {
    it('delegates to prune', () => {
      const result = invoke('context-stream:prune');
      expect(mockStream.prune).toHaveBeenCalled();
      expect(result).toBe(5);
    });
  });

  // ── Set Enabled ────────────────────────────────────────────────
  describe('Set Enabled', () => {
    it('enables the stream', () => {
      const result = invoke('context-stream:set-enabled', true) as Record<string, unknown>;
      expect(mockStream.setEnabled).toHaveBeenCalledWith(true);
      expect(result).toEqual({ enabled: true });
    });

    it('disables the stream', () => {
      const result = invoke('context-stream:set-enabled', false) as Record<string, unknown>;
      expect(mockStream.setEnabled).toHaveBeenCalledWith(false);
      expect(result).toEqual({ enabled: false });
    });

    it('throws on non-boolean', () => {
      expect(() => invoke('context-stream:set-enabled', 'yes')).toThrow('requires a boolean');
    });

    it('throws on missing argument', () => {
      expect(() => invoke('context-stream:set-enabled')).toThrow('requires a boolean');
    });
  });

  // ── Clear ──────────────────────────────────────────────────────
  describe('Clear', () => {
    it('delegates to clear', () => {
      const result = invoke('context-stream:clear') as Record<string, unknown>;
      expect(mockStream.clear).toHaveBeenCalled();
      expect(result).toEqual({ cleared: true });
    });
  });

  // ── cLaw Gate: No Persistence via IPC ──────────────────────────
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

    it('all channels are read-only or ephemeral-write', () => {
      // The only write operations are: push (ephemeral), clear (ephemeral),
      // set-enabled (toggle), prune (maintenance). None persist to disk.
      const writeChannels = [
        'context-stream:push',
        'context-stream:clear',
        'context-stream:set-enabled',
        'context-stream:prune',
      ];
      const allChannels = Array.from(handlers.keys());
      const nonReadChannels = allChannels.filter(c =>
        !c.includes('snapshot') && !c.includes('recent') && !c.includes('by-type') &&
        !c.includes('latest') && !c.includes('context-string') && !c.includes('prompt-context') &&
        !c.includes('status'),
      );
      for (const ch of nonReadChannels) {
        expect(
          writeChannels,
          `Channel ${ch} is not in the allowed write list`,
        ).toContain(ch);
      }
    });
  });
});
