/**
 * Tests for context-stream-bridge.ts — Source integration layer for
 * Track III Phase 1. Validates that ambient, clipboard, sentiment,
 * and notification engines are correctly bridged into the context stream.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock context stream ──────────────────────────────────────────────
const mockPush = vi.fn().mockReturnValue({ id: 'ctx-test', timestamp: Date.now() });
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    push: (...args: unknown[]) => mockPush(...args),
  },
}));

// ── Mock ambient engine (inline values — no external refs) ───────────
const mockGetAmbientState = vi.fn().mockReturnValue({
  activeApp: 'VS Code',
  windowTitle: 'index.ts — agent-friday',
  inferredTask: 'coding',
  focusStreak: 300,
  appDurations: {},
  lastUpdated: Date.now(),
});
vi.mock('../../src/main/ambient', () => ({
  ambientEngine: {
    getState: (...args: unknown[]) => mockGetAmbientState(...args),
  },
}));

// ── Mock clipboard intelligence (inline values) ──────────────────────
const mockGetClipboardCurrent = vi.fn().mockReturnValue({
  text: 'const x = 42;',
  type: 'code',
  timestamp: Date.now(),
  preview: 'const x = 42;',
});
vi.mock('../../src/main/clipboard-intelligence', () => ({
  clipboardIntelligence: {
    getCurrent: (...args: unknown[]) => mockGetClipboardCurrent(...args),
  },
}));

// ── Mock sentiment engine (inline values) ────────────────────────────
const mockGetSentimentState = vi.fn().mockReturnValue({
  currentMood: 'focused',
  confidence: 0.85,
  energyLevel: 0.7,
  moodStreak: 3,
  lastAnalysed: Date.now(),
});
vi.mock('../../src/main/sentiment', () => ({
  sentimentEngine: {
    getState: (...args: unknown[]) => mockGetSentimentState(...args),
  },
}));

// ── Mock notification engine (inline values) ─────────────────────────
const mockGetRecentNotifications = vi.fn().mockReturnValue([]);
vi.mock('../../src/main/notifications', () => ({
  notificationEngine: {
    getRecent: (...args: unknown[]) => mockGetRecentNotifications(...args),
  },
}));

import {
  startContextStreamBridge,
  stopContextStreamBridge,
  bridgeSentimentUpdate,
  bridgeToolInvocation,
  bridgeUserInput,
} from '../../src/main/context-stream-bridge';

describe('Context Stream Bridge — Source Integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    stopContextStreamBridge(); // Ensure clean state

    // Reset default mock return values
    mockGetAmbientState.mockReturnValue({
      activeApp: 'VS Code',
      windowTitle: 'index.ts — agent-friday',
      inferredTask: 'coding',
      focusStreak: 300,
      appDurations: {},
      lastUpdated: Date.now(),
    });
    mockGetClipboardCurrent.mockReturnValue({
      text: 'const x = 42;',
      type: 'code',
      timestamp: Date.now(),
      preview: 'const x = 42;',
    });
    mockGetSentimentState.mockReturnValue({
      currentMood: 'focused',
      confidence: 0.85,
      energyLevel: 0.7,
      moodStreak: 3,
      lastAnalysed: Date.now(),
    });
    mockGetRecentNotifications.mockReturnValue([]);
  });

  afterEach(() => {
    stopContextStreamBridge();
    vi.useRealTimers();
  });

  // ── Bridge Startup ──────────────────────────────────────────────
  describe('Bridge Startup', () => {
    it('pushes a system event on startup', () => {
      startContextStreamBridge();
      expect(mockPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'system',
          source: 'context-stream-bridge',
          summary: 'Context stream bridge started',
        }),
      );
    });
  });

  // ── Ambient Bridge ─────────────────────────────────────────────
  describe('Ambient Bridge', () => {
    it('polls ambient state every 10 seconds', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(10_000);
      expect(mockGetAmbientState).toHaveBeenCalled();
    });

    it('pushes ambient event when app changes', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(10_000);
      expect(mockPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'ambient',
          source: 'ambient-engine',
          dedupeKey: 'ambient-focus',
          data: expect.objectContaining({
            activeApp: 'VS Code',
            windowTitle: 'index.ts — agent-friday',
            inferredTask: 'coding',
          }),
        }),
      );
    });

    it('does NOT push duplicate ambient events when nothing changes', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(10_000); // First poll — push
      const pushCount1 = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'ambient',
      ).length;

      mockPush.mockClear();
      vi.advanceTimersByTime(10_000); // Second poll — no change, no push
      const pushCount2 = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'ambient',
      ).length;

      expect(pushCount1).toBe(1);
      expect(pushCount2).toBe(0);
    });

    it('pushes when window title changes', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(10_000); // First poll
      mockPush.mockClear();

      // Change window title
      mockGetAmbientState.mockReturnValue({
        activeApp: 'VS Code',
        windowTitle: 'test.ts — agent-friday',
        inferredTask: 'coding',
        focusStreak: 300,
        appDurations: {},
        lastUpdated: Date.now(),
      });

      vi.advanceTimersByTime(10_000); // Second poll
      const ambientPushes = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'ambient',
      );
      expect(ambientPushes.length).toBe(1);
    });

    it('includes inferred task in summary', () => {
      startContextStreamBridge();
      mockPush.mockClear();
      vi.advanceTimersByTime(10_000);

      const call = mockPush.mock.calls.find(
        (c) => (c[0] as Record<string, unknown>).type === 'ambient',
      );
      expect(call).toBeDefined();
      expect((call![0] as Record<string, unknown>).summary).toContain('coding');
    });
  });

  // ── Clipboard Bridge ───────────────────────────────────────────
  describe('Clipboard Bridge', () => {
    it('polls clipboard state every 3 seconds', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(3_000);
      expect(mockGetClipboardCurrent).toHaveBeenCalled();
    });

    it('pushes clipboard event when content changes', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(3_000);
      expect(mockPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'clipboard',
          source: 'clipboard-intelligence',
          dedupeKey: 'clipboard-content',
          data: expect.objectContaining({
            contentType: 'code',
            preview: 'const x = 42;',
          }),
        }),
      );
    });

    it('does NOT push duplicate clipboard events when nothing changes', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(3_000); // First poll
      const count1 = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'clipboard',
      ).length;

      mockPush.mockClear();
      vi.advanceTimersByTime(3_000); // Second poll
      const count2 = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'clipboard',
      ).length;

      expect(count1).toBe(1);
      expect(count2).toBe(0);
    });

    it('skips empty clipboard', () => {
      mockGetClipboardCurrent.mockReturnValue({
        text: '',
        type: 'empty',
        timestamp: Date.now(),
        preview: '',
      });

      startContextStreamBridge();
      mockPush.mockClear();
      vi.advanceTimersByTime(3_000);

      const clipPushes = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'clipboard',
      );
      expect(clipPushes.length).toBe(0);
    });
  });

  // ── Notification Bridge ────────────────────────────────────────
  describe('Notification Bridge', () => {
    it('polls notifications every 15 seconds', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(15_000);
      expect(mockGetRecentNotifications).toHaveBeenCalled();
    });

    it('pushes new notifications as events', () => {
      mockGetRecentNotifications.mockReturnValue([
        { app: 'Slack', title: 'New message', body: 'Hello world', timestamp: Date.now() },
      ]);

      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(15_000);
      expect(mockPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'notification',
          source: 'notification-engine',
          summary: 'Slack: New message',
          data: expect.objectContaining({
            app: 'Slack',
            title: 'New message',
          }),
        }),
      );
    });

    it('does NOT re-push old notifications', () => {
      const notifications = [
        { app: 'Slack', title: 'First', body: 'body1', timestamp: Date.now() },
      ];
      mockGetRecentNotifications.mockReturnValue(notifications);

      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(15_000); // First poll — push 1
      const count1 = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'notification',
      ).length;

      mockPush.mockClear();
      vi.advanceTimersByTime(15_000); // Same notifications — no push
      const count2 = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'notification',
      ).length;

      expect(count1).toBe(1);
      expect(count2).toBe(0);
    });
  });

  // ── Sentiment Bridge ───────────────────────────────────────────
  describe('Sentiment Bridge', () => {
    it('pushes sentiment event via bridgeSentimentUpdate', () => {
      bridgeSentimentUpdate();
      expect(mockPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'sentiment',
          source: 'sentiment-engine',
          dedupeKey: 'sentiment-mood',
          data: expect.objectContaining({
            mood: 'focused',
            confidence: 0.85,
            energyLevel: 0.7,
          }),
        }),
      );
    });

    it('includes confidence percentage in summary', () => {
      bridgeSentimentUpdate();
      const call = mockPush.mock.calls.find(
        (c) => (c[0] as Record<string, unknown>).type === 'sentiment',
      );
      expect((call![0] as Record<string, unknown>).summary).toContain('85%');
    });

    it('handles missing sentiment state gracefully', () => {
      mockGetSentimentState.mockReturnValue(null);
      expect(() => bridgeSentimentUpdate()).not.toThrow();
      expect(mockPush).not.toHaveBeenCalled();
    });
  });

  // ── Tool Invocation Bridge ─────────────────────────────────────
  describe('Tool Invocation Bridge', () => {
    it('pushes tool invocation event', () => {
      bridgeToolInvocation('search_web', true, 250);
      expect(mockPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'tool-invoke',
          source: 'tool-router',
          data: expect.objectContaining({
            toolName: 'search_web',
            success: true,
            durationMs: 250,
          }),
        }),
      );
    });

    it('includes failure in summary', () => {
      bridgeToolInvocation('dangerous_tool', false);
      const call = mockPush.mock.calls[0];
      expect((call[0] as Record<string, unknown>).summary).toContain('failed');
    });

    it('includes duration in summary when provided', () => {
      bridgeToolInvocation('read_file', true, 100);
      const call = mockPush.mock.calls[0];
      expect((call[0] as Record<string, unknown>).summary).toContain('100ms');
    });
  });

  // ── User Input Bridge ──────────────────────────────────────────
  describe('User Input Bridge', () => {
    it('pushes user input event with topic', () => {
      bridgeUserInput('asking about typescript generics');
      expect(mockPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'user-input',
          source: 'chat-handler',
          dedupeKey: 'user-input-latest',
          data: expect.objectContaining({
            topic: 'asking about typescript generics',
          }),
        }),
      );
    });

    it('truncates long topics', () => {
      const longTopic = 'a'.repeat(300);
      bridgeUserInput(longTopic);
      const call = mockPush.mock.calls[0];
      const data = (call[0] as Record<string, unknown>).data as Record<string, string>;
      expect(data.topic.length).toBeLessThanOrEqual(200);
    });

    it('truncates summary to 100 chars', () => {
      const longTopic = 'b'.repeat(200);
      bridgeUserInput(longTopic);
      const call = mockPush.mock.calls[0];
      const summary = (call[0] as Record<string, unknown>).summary as string;
      // "User: " prefix (6 chars) + 100 chars = 106
      expect(summary.length).toBeLessThanOrEqual(106);
    });
  });

  // ── Stop Bridge ────────────────────────────────────────────────
  describe('Stop Bridge', () => {
    it('stops all polling intervals', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      stopContextStreamBridge();

      // Advance time past all intervals — no new pushes should happen
      vi.advanceTimersByTime(20_000);
      // Only check for source-specific pushes (not the system startup event)
      const sourcePushes = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).source !== 'context-stream-bridge',
      );
      expect(sourcePushes.length).toBe(0);
    });

    it('can be called multiple times safely', () => {
      startContextStreamBridge();
      expect(() => stopContextStreamBridge()).not.toThrow();
      expect(() => stopContextStreamBridge()).not.toThrow();
    });

    it('resets state so restart pushes fresh events', () => {
      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(10_000); // Push ambient
      stopContextStreamBridge();
      mockPush.mockClear();

      startContextStreamBridge();
      mockPush.mockClear();

      vi.advanceTimersByTime(10_000); // Should push again (state reset)
      const ambientPushes = mockPush.mock.calls.filter(
        (c) => (c[0] as Record<string, unknown>).type === 'ambient',
      );
      expect(ambientPushes.length).toBe(1);
    });
  });

  // ── Error Resilience ───────────────────────────────────────────
  describe('Error Resilience', () => {
    it('survives ambient engine errors', () => {
      mockGetAmbientState.mockImplementation(() => {
        throw new Error('Engine not ready');
      });
      startContextStreamBridge();
      expect(() => vi.advanceTimersByTime(10_000)).not.toThrow();
    });

    it('survives clipboard engine errors', () => {
      mockGetClipboardCurrent.mockImplementation(() => {
        throw new Error('Engine not ready');
      });
      startContextStreamBridge();
      expect(() => vi.advanceTimersByTime(3_000)).not.toThrow();
    });

    it('survives notification engine errors', () => {
      mockGetRecentNotifications.mockImplementation(() => {
        throw new Error('Engine not ready');
      });
      startContextStreamBridge();
      expect(() => vi.advanceTimersByTime(15_000)).not.toThrow();
    });

    it('survives sentiment engine errors', () => {
      mockGetSentimentState.mockImplementation(() => {
        throw new Error('Engine not ready');
      });
      expect(() => bridgeSentimentUpdate()).not.toThrow();
    });
  });
});
