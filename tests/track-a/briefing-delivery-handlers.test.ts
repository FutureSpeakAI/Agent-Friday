/**
 * Track A, Phase 3: IPC Handler Tests for BriefingDelivery
 *
 * Tests criteria 8 (handler registration pattern) and 9 (input validation).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Hoisted mocks ─────────────────────────────────────────────────────

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, (...args: any[]) => any>();
  return {
    handlers,
    handle: vi.fn((channel: string, handler: (...args: any[]) => any) => {
      handlers.set(channel, handler);
    }),
    getRecentBriefings: vi.fn(() => []),
    dismissBriefing: vi.fn(() => true),
  };
});

vi.mock('electron', () => ({
  ipcMain: { handle: mocks.handle },
}));

vi.mock('../../src/main/briefing-delivery', () => ({
  briefingDelivery: {
    getRecentBriefings: mocks.getRecentBriefings,
    dismissBriefing: mocks.dismissBriefing,
  },
}));

import { registerBriefingDeliveryHandlers } from '../../src/main/ipc/briefing-delivery-handlers';

// ── Test Suite ────────────────────────────────────────────────────────

describe('BriefingDelivery IPC Handlers — Track A Phase 3', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.handlers.clear();
    registerBriefingDeliveryHandlers();
  });

  // ── Criterion 8: follows registerXxxHandlers pattern ──────────────

  describe('Criterion 8: handler registration pattern', () => {
    it('should register briefing:list handler', () => {
      expect(mocks.handlers.has('briefing:list')).toBe(true);
    });

    it('should register briefing:dismiss handler', () => {
      expect(mocks.handlers.has('briefing:dismiss')).toBe(true);
    });

    it('briefing:list should delegate to getRecentBriefings', async () => {
      const handler = mocks.handlers.get('briefing:list')!;
      await handler({});
      expect(mocks.getRecentBriefings).toHaveBeenCalledOnce();
    });

    it('briefing:dismiss should delegate to dismissBriefing', async () => {
      const handler = mocks.handlers.get('briefing:dismiss')!;
      await handler({}, 'b-1');
      expect(mocks.dismissBriefing).toHaveBeenCalledWith('b-1');
    });
  });

  // ── Criterion 9: IPC input validation ─────────────────────────────

  describe('Criterion 9: IPC input validation', () => {
    it('briefing:dismiss should reject non-string id', async () => {
      const handler = mocks.handlers.get('briefing:dismiss')!;
      await expect(handler({}, 42)).rejects.toThrow();
    });

    it('briefing:dismiss should reject empty string id', async () => {
      const handler = mocks.handlers.get('briefing:dismiss')!;
      await expect(handler({}, '')).rejects.toThrow();
    });
  });
});
