/**
 * Tests for LiveContextBridge — Phase C.3 "The Tapestry"
 *
 * The LiveContextBridge subscribes to context graph and briefing updates,
 * runs the ContextInjector, and pushes enriched per-app context to the
 * renderer via IPC. Includes debouncing, cleanup, and feedback loop
 * circuit breaker.
 *
 * Validation criteria covered:
 *   1. start() subscribes to context graph and briefing pipeline events
 *   2. Runs injector and pushes app-context:update on changes
 *   3. (useAppContext hook — covered in separate test)
 *   4. (hook return shape — covered in separate test)
 *   5. (selective re-render — covered in separate test)
 *   6. Execution results feed back into context graph as tool-execution events
 *   7. stop() cleans up all subscriptions
 *   8. app-context:get IPC handler returns current context on demand
 *   9. Debounces updates — max one push per 2 seconds per app
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mocks (hoisted to avoid reference-before-init with vi.mock) ──

const {
  mockContextStreamOn,
  mockGetActiveStream,
  mockGetTopEntities,
  mockGetRecentBriefings,
  mockWebContentsSend,
  mockContextStreamPush,
} = vi.hoisted(() => ({
  mockContextStreamOn: vi.fn<[() => void], () => void>(() => vi.fn()),
  mockGetActiveStream: vi.fn(() => null),
  mockGetTopEntities: vi.fn(() => []),
  mockGetRecentBriefings: vi.fn(() => []),
  mockWebContentsSend: vi.fn(),
  mockContextStreamPush: vi.fn(),
}));

vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    on: mockContextStreamOn,
    push: mockContextStreamPush,
  },
}));

vi.mock('../../src/main/context-graph', () => ({
  contextGraph: {
    getActiveStream: mockGetActiveStream,
    getTopEntities: mockGetTopEntities,
  },
}));

vi.mock('../../src/main/briefing-delivery', () => ({
  briefingDelivery: {
    getRecentBriefings: mockGetRecentBriefings,
  },
}));

import { LiveContextBridge } from '../../src/main/live-context-bridge';

// ── Helpers ─────────────────────────────────────────────────────────

function makeMockWindow() {
  return {
    webContents: {
      isDestroyed: () => false,
      send: mockWebContentsSend,
    },
  } as any;
}

function makeStream(id = 's1', name = 'coding') {
  return {
    id,
    name,
    task: 'dev',
    app: 'code',
    startedAt: Date.now(),
    lastActiveAt: Date.now(),
    eventCount: 3,
    entities: [],
    eventTypes: ['ambient'],
    summary: 'Working',
  };
}

function makeBriefing(topic = 'test', priority: 'urgent' | 'relevant' | 'informational' = 'relevant') {
  return {
    id: `b-${topic}`,
    topic,
    content: `Briefing about ${topic}`,
    priority,
    timestamp: Date.now(),
    dismissed: false,
  };
}

// ── Tests ───────────────────────────────────────────────────────────

describe('LiveContextBridge', () => {
  let bridge: LiveContextBridge;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    bridge = new LiveContextBridge();
  });

  afterEach(() => {
    bridge.stop();
    vi.useRealTimers();
  });

  // ── Criterion 1: start subscribes to context and briefing events ──

  it('start() subscribes to context stream events', () => {
    bridge.start(makeMockWindow());
    expect(mockContextStreamOn).toHaveBeenCalledTimes(1);
    expect(mockContextStreamOn).toHaveBeenCalledWith(expect.any(Function));
  });

  it('start() is idempotent — does not double-subscribe', () => {
    const win = makeMockWindow();
    bridge.start(win);
    bridge.start(win);
    expect(mockContextStreamOn).toHaveBeenCalledTimes(1);
  });

  // ── Criterion 2: runs injector and pushes app-context:update ──

  it('pushes app-context:update when context stream fires', () => {
    const stream = makeStream();
    mockGetActiveStream.mockReturnValue(stream);
    mockGetTopEntities.mockReturnValue([]);
    mockGetRecentBriefings.mockReturnValue([]);

    bridge.start(makeMockWindow());

    // Trigger the stream callback
    const streamCallback = mockContextStreamOn.mock.calls[0][0];
    streamCallback();

    // Advance past debounce
    vi.advanceTimersByTime(2100);

    expect(mockWebContentsSend).toHaveBeenCalledWith(
      'app-context:update',
      expect.objectContaining({
        activeStream: stream,
      }),
    );
  });

  // ── Criterion 6: execution results feed back to context graph ──

  it('feedExecutionResult pushes tool-invoke event to context stream', () => {
    bridge.start(makeMockWindow());

    bridge.feedExecutionResult({
      tool_use_id: 'tu-1',
      content: 'File created',
      is_error: false,
    });

    expect(mockContextStreamPush).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'tool-invoke',
      }),
    );
  });

  it('feedExecutionResult respects cooldown — no double-push within 5 seconds', () => {
    bridge.start(makeMockWindow());

    bridge.feedExecutionResult({
      tool_use_id: 'tu-1',
      content: 'Success',
      is_error: false,
    });
    bridge.feedExecutionResult({
      tool_use_id: 'tu-2',
      content: 'Another success',
      is_error: false,
    });

    // Only one push (second blocked by cooldown)
    expect(mockContextStreamPush).toHaveBeenCalledTimes(1);

    // After cooldown
    vi.advanceTimersByTime(5100);
    bridge.feedExecutionResult({
      tool_use_id: 'tu-3',
      content: 'Third call',
      is_error: false,
    });
    expect(mockContextStreamPush).toHaveBeenCalledTimes(2);
  });

  // ── Criterion 7: stop cleans up all subscriptions ──

  it('stop() unsubscribes from context stream', () => {
    const unsub = vi.fn();
    mockContextStreamOn.mockReturnValue(unsub);

    bridge.start(makeMockWindow());
    bridge.stop();

    expect(unsub).toHaveBeenCalledTimes(1);
  });

  it('stop() clears debounce timers', () => {
    mockGetActiveStream.mockReturnValue(makeStream());
    bridge.start(makeMockWindow());

    // Trigger an update (starts debounce timer)
    const streamCallback = mockContextStreamOn.mock.calls[0][0];
    streamCallback();

    bridge.stop();

    // Advance time — should NOT push after stop
    vi.advanceTimersByTime(5000);
    expect(mockWebContentsSend).not.toHaveBeenCalled();
  });

  // ── Criterion 8: app-context:get returns current context ──

  it('getContextForApp returns injected context for a specific app', () => {
    const stream = makeStream();
    mockGetActiveStream.mockReturnValue(stream);
    mockGetTopEntities.mockReturnValue([
      { type: 'file', value: 'main.ts', normalizedValue: 'main.ts', firstSeen: 0, lastSeen: 0, occurrences: 5, sourceStreamIds: ['s1'] },
    ]);
    mockGetRecentBriefings.mockReturnValue([]);

    bridge.start(makeMockWindow());

    // Trigger to populate injector
    const streamCallback = mockContextStreamOn.mock.calls[0][0];
    streamCallback();

    const ctx = bridge.getContextForApp('files');
    expect(ctx).toHaveProperty('activeStream');
    expect(ctx).toHaveProperty('entities');
    expect(ctx).toHaveProperty('briefingSummary');
    expect(ctx.activeStream).toEqual(stream);
  });

  it('getContextForApp returns empty context before start', () => {
    const ctx = bridge.getContextForApp('notes');
    expect(ctx.activeStream).toBeNull();
    expect(ctx.entities).toEqual([]);
    expect(ctx.briefingSummary).toBeNull();
  });

  // ── Criterion 9: debounce — max 1 push per 2 seconds ──

  it('debounces rapid updates to max 1 push per 2 seconds', () => {
    mockGetActiveStream.mockReturnValue(makeStream());
    mockGetTopEntities.mockReturnValue([]);
    mockGetRecentBriefings.mockReturnValue([]);

    bridge.start(makeMockWindow());
    const streamCallback = mockContextStreamOn.mock.calls[0][0];

    // Fire 5 rapid updates
    streamCallback();
    streamCallback();
    streamCallback();
    streamCallback();
    streamCallback();

    // Before debounce window
    expect(mockWebContentsSend).not.toHaveBeenCalled();

    // After debounce window
    vi.advanceTimersByTime(2100);
    expect(mockWebContentsSend).toHaveBeenCalledTimes(1);
  });

  it('does not push to destroyed webContents', () => {
    const win = {
      webContents: {
        isDestroyed: () => true,
        send: mockWebContentsSend,
      },
    } as any;

    mockGetActiveStream.mockReturnValue(makeStream());
    bridge.start(win);

    const streamCallback = mockContextStreamOn.mock.calls[0][0];
    streamCallback();
    vi.advanceTimersByTime(2100);

    expect(mockWebContentsSend).not.toHaveBeenCalled();
  });
});
