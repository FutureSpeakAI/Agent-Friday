/**
 * Tests for context-push-handlers — Phase C.1 "The Loom"
 *
 * Validates: main-process context push infrastructure.
 * The push handler subscribes to the context stream, detects
 * stream changes, and pushes updates to the renderer via
 * webContents.send('context:stream-update', payload).
 *
 * Validation criteria covered:
 *   2. context:stream-update push on stream change
 *   6. context:subscribe registers renderer for push updates
 *   7. context:unsubscribe deregisters the renderer
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Hoisted mocks ──────────────────────────────────────────────────

const mocks = vi.hoisted(() => {
  const streamListeners: ((event: any) => void)[] = [];
  return {
    streamListeners,
    mockContextStream: {
      on: vi.fn((cb: (event: any) => void) => {
        streamListeners.push(cb);
        return () => {
          const idx = streamListeners.indexOf(cb);
          if (idx >= 0) streamListeners.splice(idx, 1);
        };
      }),
    },
    mockContextGraph: {
      getActiveStream: vi.fn(() => null),
      getRecentStreams: vi.fn(() => []),
      getTopEntities: vi.fn(() => []),
    },
    mockIpcMain: {
      handle: vi.fn(),
      removeHandler: vi.fn(),
    },
    mockWebContents: {
      send: vi.fn(),
      isDestroyed: vi.fn(() => false),
    },
  };
});

vi.mock('electron', () => ({
  ipcMain: mocks.mockIpcMain,
}));

vi.mock('../../src/main/context-stream', () => ({
  contextStream: mocks.mockContextStream,
}));

vi.mock('../../src/main/context-graph', () => ({
  contextGraph: mocks.mockContextGraph,
}));

// ── Import under test ──────────────────────────────────────────────

import {
  registerContextPushHandlers,
  type ContextPushCleanup,
} from '../../src/main/ipc/context-push-handlers';

// ── Helpers ────────────────────────────────────────────────────────

function makeMockWindow() {
  return { webContents: mocks.mockWebContents } as any;
}

function makeStream(id: string, name: string) {
  return {
    id,
    name,
    task: 'test',
    app: 'test-app',
    startedAt: Date.now(),
    lastActiveAt: Date.now(),
    eventCount: 1,
    entities: [],
    eventTypes: new Set(['ambient']),
    summary: '',
  };
}

function makeEntity(value: string, occurrences: number) {
  return {
    type: 'topic' as const,
    value,
    normalizedValue: value.toLowerCase(),
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    occurrences,
    sourceStreamIds: ['s1'],
  };
}

function emitStreamEvent(source = 'test', summary = 'test event') {
  for (const cb of [...mocks.streamListeners]) {
    cb({ type: 'ambient', source, summary, data: {} });
  }
}

// ── Tests ──────────────────────────────────────────────────────────

describe('context-push-handlers', () => {
  let cleanup: ContextPushCleanup;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.streamListeners.length = 0;
    mocks.mockContextGraph.getActiveStream.mockReturnValue(null);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([]);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);
    mocks.mockWebContents.isDestroyed.mockReturnValue(false);
  });

  afterEach(() => {
    cleanup?.();
  });

  // ── Criterion 6: context:subscribe registers for push updates ──

  it('registers context:subscribe and context:unsubscribe IPC handlers', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());
    const channels = mocks.mockIpcMain.handle.mock.calls.map(
      (c: any[]) => c[0],
    );
    expect(channels).toContain('context:subscribe');
    expect(channels).toContain('context:unsubscribe');
  });

  it('subscribes to context stream on registration', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());
    expect(mocks.mockContextStream.on).toHaveBeenCalledOnce();
  });

  // ── Criterion 2: push on stream change ──

  it('pushes context:stream-update when active stream changes', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());

    const stream = makeStream('s1', 'coding project');
    const entities = [makeEntity('React', 5), makeEntity('TypeScript', 3)];

    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream);
    mocks.mockContextGraph.getTopEntities.mockReturnValue(entities);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream]);

    emitStreamEvent();

    expect(mocks.mockWebContents.send).toHaveBeenCalledWith(
      'context:stream-update',
      expect.objectContaining({
        activeStream: expect.objectContaining({ id: 's1', name: 'coding project' }),
        recentEntities: expect.arrayContaining([
          expect.objectContaining({ value: 'React' }),
        ]),
        streamHistory: expect.any(Array),
      }),
    );
  });

  it('does NOT push when active stream stays the same', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());

    const stream = makeStream('s1', 'coding');
    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream]);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);

    // First event: triggers push (stream changed from null to s1)
    emitStreamEvent();
    expect(mocks.mockWebContents.send).toHaveBeenCalledTimes(1);

    // Second event: same stream — no push
    emitStreamEvent();
    expect(mocks.mockWebContents.send).toHaveBeenCalledTimes(1);
  });

  it('pushes when stream changes from one to another', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());

    const stream1 = makeStream('s1', 'coding');
    const stream2 = makeStream('s2', 'email');

    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream1);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream1]);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);
    emitStreamEvent();

    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream2);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream2, stream1]);
    emitStreamEvent();

    expect(mocks.mockWebContents.send).toHaveBeenCalledTimes(2);
    const secondPayload = mocks.mockWebContents.send.mock.calls[1][1];
    expect(secondPayload.activeStream.id).toBe('s2');
  });

  it('pushes when stream goes from active to null', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());

    const stream = makeStream('s1', 'coding');
    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream]);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);
    emitStreamEvent();

    mocks.mockContextGraph.getActiveStream.mockReturnValue(null);
    emitStreamEvent();

    expect(mocks.mockWebContents.send).toHaveBeenCalledTimes(2);
    const secondPayload = mocks.mockWebContents.send.mock.calls[1][1];
    expect(secondPayload.activeStream).toBeNull();
  });

  // ── Criterion 7: context:unsubscribe deregisters ──

  it('stops pushing after cleanup is called', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());

    const stream = makeStream('s1', 'coding');
    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream]);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);

    cleanup();

    emitStreamEvent();
    expect(mocks.mockWebContents.send).not.toHaveBeenCalled();
  });

  it('does not push when webContents is destroyed', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());
    mocks.mockWebContents.isDestroyed.mockReturnValue(true);

    const stream = makeStream('s1', 'coding');
    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream]);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);

    emitStreamEvent();
    expect(mocks.mockWebContents.send).not.toHaveBeenCalled();
  });

  // ── Payload shape ──

  it('serializes stream eventTypes Set to Array in payload', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());

    const stream = makeStream('s1', 'coding');
    stream.eventTypes = new Set(['ambient', 'clipboard']);
    mocks.mockContextGraph.getActiveStream.mockReturnValue(stream);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue([stream]);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);

    emitStreamEvent();

    const payload = mocks.mockWebContents.send.mock.calls[0][1];
    expect(Array.isArray(payload.activeStream.eventTypes)).toBe(true);
    expect(payload.activeStream.eventTypes).toContain('ambient');
  });

  it('limits streamHistory to 5 entries', () => {
    cleanup = registerContextPushHandlers(makeMockWindow());

    const streams = Array.from({ length: 8 }, (_, i) =>
      makeStream(`s${i}`, `stream-${i}`),
    );
    mocks.mockContextGraph.getActiveStream.mockReturnValue(streams[0]);
    mocks.mockContextGraph.getRecentStreams.mockReturnValue(streams);
    mocks.mockContextGraph.getTopEntities.mockReturnValue([]);

    emitStreamEvent();

    const payload = mocks.mockWebContents.send.mock.calls[0][1];
    expect(payload.streamHistory.length).toBe(5);
  });
});
