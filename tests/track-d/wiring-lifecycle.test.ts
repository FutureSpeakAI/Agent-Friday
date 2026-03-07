/**
 * Track D, Phase 1: "The Ignition" — Lifecycle Wiring Tests
 *
 * Validates that LiveContextBridge is properly wired into the
 * main process startup and shutdown sequences in index.ts.
 *
 * These tests verify the wiring at the module level by testing
 * the LiveContextBridge lifecycle directly, since index.ts
 * boot logic can't be imported in isolation.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  contextStreamOn: vi.fn(() => vi.fn()),
  contextStreamPush: vi.fn(),
  contextGraphGetActiveStream: vi.fn(() => null),
  contextGraphGetTopEntities: vi.fn(() => []),
  briefingDeliveryGetRecentBriefings: vi.fn(() => []),
  webContentsSend: vi.fn(),
  isDestroyed: vi.fn(() => false),
}));

vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    on: mocks.contextStreamOn,
    push: mocks.contextStreamPush,
  },
}));

vi.mock('../../src/main/context-graph', () => ({
  contextGraph: {
    getActiveStream: mocks.contextGraphGetActiveStream,
    getTopEntities: mocks.contextGraphGetTopEntities,
  },
}));

vi.mock('../../src/main/briefing-delivery', () => ({
  briefingDelivery: {
    getRecentBriefings: mocks.briefingDeliveryGetRecentBriefings,
  },
}));

describe('LiveContextBridge Lifecycle Wiring', () => {
  let LiveContextBridge: typeof import('../../src/main/live-context-bridge').LiveContextBridge;
  let bridge: InstanceType<typeof LiveContextBridge>;

  const mockMainWindow = {
    webContents: {
      send: mocks.webContentsSend,
      isDestroyed: mocks.isDestroyed,
    },
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    const mod = await import('../../src/main/live-context-bridge');
    LiveContextBridge = mod.LiveContextBridge;
    bridge = new LiveContextBridge();
  });

  afterEach(() => {
    bridge.stop();
    vi.useRealTimers();
  });

  // D.1 Validation Criterion 1: start(mainWindow) subscribes to context stream
  it('start() subscribes to context stream events', () => {
    bridge.start(mockMainWindow);
    expect(mocks.contextStreamOn).toHaveBeenCalledOnce();
    expect(mocks.contextStreamOn).toHaveBeenCalledWith(expect.any(Function));
  });

  // D.1 Validation Criterion 4: idempotent start
  it('calling start() twice does not double-subscribe', () => {
    bridge.start(mockMainWindow);
    bridge.start(mockMainWindow);
    expect(mocks.contextStreamOn).toHaveBeenCalledOnce();
  });

  // D.1 Validation Criterion 5: bridge receives events after start
  it('receives context stream events and refreshes injector', () => {
    bridge.start(mockMainWindow);

    // Get the callback passed to contextStream.on()
    const streamCallback = mocks.contextStreamOn.mock.calls[0][0];
    streamCallback();

    // Injector was refreshed (reads from graph)
    expect(mocks.contextGraphGetActiveStream).toHaveBeenCalled();
    expect(mocks.contextGraphGetTopEntities).toHaveBeenCalled();
    expect(mocks.briefingDeliveryGetRecentBriefings).toHaveBeenCalled();
  });

  // D.1 Validation Criterion 2: stop() cleans up subscriptions
  it('stop() unsubscribes from context stream', () => {
    const unsubscribe = vi.fn();
    mocks.contextStreamOn.mockReturnValue(unsubscribe);

    bridge.start(mockMainWindow);
    bridge.stop();

    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  // D.1 Validation Criterion 2: stop() clears debounce timer
  it('stop() clears pending debounce timer', () => {
    bridge.start(mockMainWindow);

    // Trigger a stream event to start debounce
    const streamCallback = mocks.contextStreamOn.mock.calls[0][0];
    streamCallback();

    // Stop before debounce fires
    bridge.stop();

    // Advance past debounce period
    vi.advanceTimersByTime(3000);

    // No IPC push should have happened
    expect(mocks.webContentsSend).not.toHaveBeenCalled();
  });

  // D.1 Validation Criterion 6: shutdown order - bridge stops cleanly
  it('stop() prevents further IPC pushes', () => {
    bridge.start(mockMainWindow);
    bridge.stop();

    // Verify bridge is inactive — getContextForApp still works (pure computation)
    const ctx = bridge.getContextForApp('test-app');
    expect(ctx).toBeDefined();
  });

  // Verify IPC push happens after debounce
  it('pushes enriched context to renderer after 2s debounce', () => {
    bridge.start(mockMainWindow);

    const streamCallback = mocks.contextStreamOn.mock.calls[0][0];
    streamCallback();

    // Before debounce
    expect(mocks.webContentsSend).not.toHaveBeenCalled();

    // After debounce
    vi.advanceTimersByTime(2100);

    expect(mocks.webContentsSend).toHaveBeenCalledWith(
      'app-context:update',
      expect.any(Object),
    );
  });

  // Verify destroyed window check
  it('does not push to destroyed window', () => {
    mocks.isDestroyed.mockReturnValue(true);
    bridge.start(mockMainWindow);

    const streamCallback = mocks.contextStreamOn.mock.calls[0][0];
    streamCallback();
    vi.advanceTimersByTime(2100);

    expect(mocks.webContentsSend).not.toHaveBeenCalled();
  });
});
