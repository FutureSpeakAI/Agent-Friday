/**
 * Track F, Phase 1: "The Proof" — End-to-End Hermeneutic Circle
 *
 * Integration test validating the full context loop:
 * OS events → ContextStream → ContextGraph → LiveContextBridge
 *   → ContextInjector → AppContext → Renderer
 *   → Execution → FeedbackWire → ContextStream (cycle)
 *
 * These tests exercise the REAL module implementations (not mocks)
 * for the main-process context pipeline, only mocking Electron IPC
 * and the BrowserWindow boundary.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock Electron (transitive dep via intelligence → soc-bridge) ─
vi.mock('electron', () => ({
  app: {
    isPackaged: false,
    getPath: vi.fn(() => '/tmp/test'),
    getName: vi.fn(() => 'nexus-test'),
    on: vi.fn(),
    whenReady: vi.fn().mockResolvedValue(undefined),
  },
  ipcMain: { handle: vi.fn(), on: vi.fn() },
  BrowserWindow: vi.fn(),
  nativeTheme: { shouldUseDarkColors: true, on: vi.fn() },
  shell: { openExternal: vi.fn() },
  dialog: { showOpenDialog: vi.fn(), showSaveDialog: vi.fn() },
  screen: { getPrimaryDisplay: vi.fn(() => ({ workAreaSize: { width: 1920, height: 1080 } })) },
}));

// ── Real modules under test ──────────────────────────────────────
import { contextStream, type ContextEvent } from '../../../src/main/context-stream';
import { contextGraph } from '../../../src/main/context-graph';
import { liveContextBridge } from '../../../src/main/live-context-bridge';
import { briefingDelivery } from '../../../src/main/briefing-delivery';

// ── Helpers ──────────────────────────────────────────────────────

function makeAmbientEvent(app: string, task: string): Omit<ContextEvent, 'id' | 'timestamp'> {
  return {
    type: 'ambient',
    source: 'test-harness',
    summary: `${task} in ${app}`,
    data: { activeApp: app, windowTitle: `${app} - ${task}`, inferredTask: task },
  };
}

function makeFakeWindow() {
  return {
    webContents: {
      isDestroyed: vi.fn(() => false),
      send: vi.fn(),
    },
  };
}

// Monotonically increasing epoch so singleton throttle never blocks
// across tests. Each test starts 60s after the last one.
let testEpoch = 2_000_000_000_000;

describe('Hermeneutic Circle — End-to-End Integration', () => {
  let fakeWindow: ReturnType<typeof makeFakeWindow>;

  beforeEach(() => {
    testEpoch += 60_000;
    vi.useFakeTimers({ now: testEpoch });
    fakeWindow = makeFakeWindow();

    // Start the real pipeline: graph listens to stream
    contextGraph.start();
  });

  afterEach(() => {
    // Tear down in reverse order
    liveContextBridge.stop();
    contextGraph.stop();
    vi.useRealTimers();
  });

  // F.1 Criterion 1: Context flow — push event → graph sees active stream
  it('propagates a context event from stream to graph', () => {
    const pushed = contextStream.push(makeAmbientEvent('VS Code', 'editing'));
    expect(pushed).not.toBeNull();

    const active = contextGraph.getActiveStream();
    expect(active).not.toBeNull();
    expect(active!.app).toBe('VS Code');
    expect(active!.task).toBe('editing');
  });

  // F.1 Criterion 2: Briefing flow — briefing delivery provides data to injector
  it('includes briefing data in app context via bridge', () => {
    // Inject briefing into the private array (no public deliver())
    (briefingDelivery as any).briefings.push({
      id: 'b-1',
      topic: 'Calendar',
      content: 'Meeting in 15 minutes',
      priority: 'urgent',
      timestamp: Date.now(),
      dismissed: false,
    });

    // Start bridge BEFORE push so the listener triggers refreshInjector
    liveContextBridge.start(fakeWindow);
    contextStream.push(makeAmbientEvent('Notes', 'writing'));

    const appCtx = liveContextBridge.getContextForApp('dashboard');
    expect(appCtx.briefingSummary).toBeTruthy();
    expect(appCtx.activeStream).not.toBeNull();
  });

  // F.1 Criterion 3: Injection flow — getContextForApp returns enriched data
  it('returns enriched per-app context through the bridge', () => {
    liveContextBridge.start(fakeWindow);
    contextStream.push(makeAmbientEvent('Terminal', 'debugging'));

    const ctx = liveContextBridge.getContextForApp('friday-terminal');
    expect(ctx).toHaveProperty('activeStream');
    expect(ctx).toHaveProperty('entities');
    expect(ctx).toHaveProperty('briefingSummary');
    expect(ctx.activeStream).not.toBeNull();
  });

  // F.1 Criterion 4: Bridge flow — push to renderer on stream update
  it('pushes context to renderer via IPC on stream update', () => {
    liveContextBridge.start(fakeWindow);

    contextStream.push(makeAmbientEvent('Browser', 'researching'));

    // Advance past debounce (2000ms)
    vi.advanceTimersByTime(2500);

    expect(fakeWindow.webContents.send).toHaveBeenCalledWith(
      'app-context:update',
      expect.objectContaining({
        activeStream: expect.any(Object),
        entities: expect.any(Array),
      }),
    );
  });

  // F.1 Criterion 5: Execution feedback — feedExecutionResult pushes to stream
  it('feeds execution result back into context stream', () => {
    liveContextBridge.start(fakeWindow);

    const listener = vi.fn();
    const unsub = contextStream.on(listener);

    liveContextBridge.feedExecutionResult({
      tool_use_id: 'tc-integration-1',
      content: 'File saved successfully',
    });

    const toolEvent = listener.mock.calls.find(
      (c: any[]) => c[0]?.type === 'tool-invoke',
    );
    expect(toolEvent).toBeDefined();
    expect(toolEvent![0].source).toBe('execution-delegate');

    unsub();
  });

  // F.1 Criterion 6: Circuit breaker — rapid feedback is throttled
  it('throttles rapid execution feedback via circuit breaker', () => {
    liveContextBridge.start(fakeWindow);

    const listener = vi.fn();
    const unsub = contextStream.on(listener);

    // First call goes through
    liveContextBridge.feedExecutionResult({
      tool_use_id: 'tc-rapid-1',
      content: 'Result 1',
    });

    // Second call within cooldown (5s) is dropped
    vi.advanceTimersByTime(1000);
    liveContextBridge.feedExecutionResult({
      tool_use_id: 'tc-rapid-2',
      content: 'Result 2',
    });

    const toolEvents = listener.mock.calls.filter(
      (c: any[]) => c[0]?.type === 'tool-invoke',
    );
    expect(toolEvents.length).toBe(1);
    expect(toolEvents[0][0].data.toolUseId).toBe('tc-rapid-1');

    // After cooldown, third call goes through
    vi.advanceTimersByTime(5000);
    liveContextBridge.feedExecutionResult({
      tool_use_id: 'tc-rapid-3',
      content: 'Result 3',
    });

    const allToolEvents = listener.mock.calls.filter(
      (c: any[]) => c[0]?.type === 'tool-invoke',
    );
    expect(allToolEvents.length).toBe(2);

    unsub();
  });

  // F.1 Criterion 7: Full circle — event → graph → bridge → renderer → feedback → stream
  it('completes the full hermeneutic circle', () => {
    liveContextBridge.start(fakeWindow);

    // 1. Push ambient event into stream
    const pushed = contextStream.push(makeAmbientEvent('IDE', 'coding'));
    expect(pushed).not.toBeNull();

    // 2. Graph should have active stream
    const stream = contextGraph.getActiveStream();
    expect(stream).not.toBeNull();
    expect(stream!.name).toBeTruthy();

    // 3. Bridge should provide enriched context
    const ctx = liveContextBridge.getContextForApp('friday-code');
    expect(ctx.activeStream).not.toBeNull();

    // 4. Advance past debounce — renderer gets push
    vi.advanceTimersByTime(2500);
    expect(fakeWindow.webContents.send).toHaveBeenCalled();

    // 5. Feed execution result back — closing the loop
    const feedListener = vi.fn();
    const unsub = contextStream.on(feedListener);

    liveContextBridge.feedExecutionResult({
      tool_use_id: 'tc-full-circle',
      content: 'Code compiled successfully',
    });

    const toolEvent = feedListener.mock.calls.find(
      (c: any[]) => c[0]?.type === 'tool-invoke',
    );
    expect(toolEvent).toBeDefined();

    unsub();
  });

  // F.1 Criterion 8: Shutdown safety — stop() cleans up without throwing
  it('shuts down cleanly without errors', () => {
    liveContextBridge.start(fakeWindow);
    contextStream.push(makeAmbientEvent('App', 'working'));
    vi.advanceTimersByTime(500); // mid-debounce

    expect(() => {
      liveContextBridge.stop();
      contextGraph.stop();
    }).not.toThrow();

    // After stop, bridge should not push new updates
    fakeWindow.webContents.send.mockClear();
    vi.advanceTimersByTime(5000);
    expect(fakeWindow.webContents.send).not.toHaveBeenCalled();
  });

  // F.1 Criterion 9: Error isolation — stream works after graph stop
  it('isolates errors so one module failure does not break the pipeline', () => {
    liveContextBridge.start(fakeWindow);

    // Push a valid event — pipeline is healthy
    const result = contextStream.push(makeAmbientEvent('Safe App', 'normal'));
    expect(result).not.toBeNull();

    // Stop graph, advance past ambient throttle, push again
    contextGraph.stop();
    vi.advanceTimersByTime(10_000);
    const result2 = contextStream.push(makeAmbientEvent('Post Stop', 'testing'));
    // Stream itself doesn't throw — event still enters buffer
    expect(result2).not.toBeNull();
  });

  // F.1 Criterion 10: Destroyed window — bridge handles gracefully
  it('handles destroyed window without crashing', () => {
    liveContextBridge.start(fakeWindow);
    contextStream.push(makeAmbientEvent('App', 'working'));

    // Simulate window destruction
    fakeWindow.webContents.isDestroyed.mockReturnValue(true);

    // Advance past debounce — should not throw
    expect(() => {
      vi.advanceTimersByTime(3000);
    }).not.toThrow();

    // send() should NOT have been called after isDestroyed returns true
    expect(fakeWindow.webContents.send).not.toHaveBeenCalled();
  });
});
