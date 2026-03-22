/**
 * Tests for WorkContextStore — Phase C.1 "The Loom"
 *
 * The WorkContextStore is the non-React state manager underlying
 * the useWorkContext() hook. It handles IPC subscription,
 * state management, and shared subscriber reference counting.
 *
 * Uses dependency injection: ContextIpcBridge is passed via constructor
 * for test isolation. No vi.mock('electron') needed.
 *
 * Validation criteria covered:
 *   1. Returns { activeStream, recentEntities, streamHistory }
 *   3. activeStream is null when no stream is active
 *   4. recentEntities returns entities from active stream, sorted by occurrence count
 *   5. streamHistory returns last 5 work streams in reverse chronological order
 *   8. Cleans up subscription on unmount (no memory leaks)
 *   9. Multiple consumers share the same subscription (no duplicates)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
  WorkContextStore,
  type ContextIpcBridge,
  type WorkContextState,
} from '../../src/renderer/hooks/useWorkContext';

// ── Mock Bridge Factory ─────────────────────────────────────────────

function makeMockBridge() {
  const handlers = new Map<string, (...args: any[]) => void>();

  const bridge: ContextIpcBridge = {
    invoke: vi.fn(async () => null),
    on: vi.fn((channel: string, handler: (...args: any[]) => void) => {
      handlers.set(channel, handler);
    }),
    removeListener: vi.fn((channel: string) => {
      handlers.delete(channel);
    }),
  };

  function simulatePush(payload: any) {
    const handler = handlers.get('context:stream-update');
    if (handler) {
      handler({}, payload); // IpcRendererEvent is first arg
    }
  }

  return { bridge, handlers, simulatePush };
}

// ── Helpers ─────────────────────────────────────────────────────────

function makeStreamPayload(id: string, name: string) {
  return {
    id,
    name,
    task: 'test',
    app: 'test-app',
    startedAt: Date.now(),
    lastActiveAt: Date.now(),
    eventCount: 1,
    entities: [],
    eventTypes: ['ambient'],
    summary: '',
  };
}

function makeEntityPayload(value: string, occurrences: number) {
  return {
    type: 'topic',
    value,
    normalizedValue: value.toLowerCase(),
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    occurrences,
    sourceStreamIds: ['s1'],
  };
}

// ── Tests ───────────────────────────────────────────────────────────

describe('WorkContextStore', () => {
  let store: WorkContextStore;
  let bridge: ContextIpcBridge;
  let handlers: Map<string, (...args: any[]) => void>;
  let simulatePush: (payload: any) => void;

  beforeEach(() => {
    vi.clearAllMocks();
    const mock = makeMockBridge();
    bridge = mock.bridge;
    handlers = mock.handlers;
    simulatePush = mock.simulatePush;
    store = new WorkContextStore(bridge);
  });

  // ── Criterion 1: returns { activeStream, recentEntities, streamHistory } ──

  it('returns initial state with correct shape', () => {
    const state = store.getSnapshot();
    expect(state).toHaveProperty('activeStream');
    expect(state).toHaveProperty('recentEntities');
    expect(state).toHaveProperty('streamHistory');
  });

  // ── Criterion 3: activeStream is null when no stream is active ──

  it('activeStream is null on initial state', () => {
    expect(store.getSnapshot().activeStream).toBeNull();
  });

  it('activeStream is null when push payload has null stream', () => {
    store.subscribe(() => {});
    simulatePush({
      activeStream: null,
      recentEntities: [],
      streamHistory: [],
    });
    expect(store.getSnapshot().activeStream).toBeNull();
  });

  // ── Criterion 4: recentEntities sorted by occurrence count ──

  it('recentEntities are sorted by occurrence count descending', () => {
    store.subscribe(() => {});
    const entities = [
      makeEntityPayload('React', 3),
      makeEntityPayload('TypeScript', 7),
      makeEntityPayload('Vitest', 1),
    ];
    simulatePush({
      activeStream: makeStreamPayload('s1', 'coding'),
      recentEntities: entities,
      streamHistory: [],
    });

    const result = store.getSnapshot().recentEntities;
    expect(result[0].value).toBe('TypeScript');
    expect(result[1].value).toBe('React');
    expect(result[2].value).toBe('Vitest');
  });

  // ── Criterion 5: streamHistory returns last 5 in reverse chronological ──

  it('streamHistory is limited to 5 entries', () => {
    store.subscribe(() => {});
    const streams = Array.from({ length: 8 }, (_, i) =>
      makeStreamPayload(`s${i}`, `stream-${i}`),
    );
    simulatePush({
      activeStream: streams[0],
      recentEntities: [],
      streamHistory: streams,
    });

    expect(store.getSnapshot().streamHistory.length).toBe(5);
  });

  it('streamHistory preserves order from push payload', () => {
    store.subscribe(() => {});
    const streams = [
      makeStreamPayload('s3', 'latest'),
      makeStreamPayload('s2', 'middle'),
      makeStreamPayload('s1', 'oldest'),
    ];
    simulatePush({
      activeStream: streams[0],
      recentEntities: [],
      streamHistory: streams,
    });

    const history = store.getSnapshot().streamHistory;
    expect(history[0].name).toBe('latest');
    expect(history[2].name).toBe('oldest');
  });

  // ── Criterion 8: cleanup on unmount (no memory leaks) ──

  it('unsubscribe cleans up IPC listener when last consumer leaves', () => {
    const unsub = store.subscribe(() => {});
    expect(handlers.has('context:stream-update')).toBe(true);

    unsub();
    expect(bridge.removeListener).toHaveBeenCalledWith(
      'context:stream-update',
      expect.any(Function),
    );
  });

  it('sends context:unsubscribe IPC on last consumer cleanup', () => {
    const unsub = store.subscribe(() => {});
    unsub();

    expect(bridge.invoke).toHaveBeenCalledWith('context:unsubscribe');
  });

  // ── Criterion 9: multiple consumers share same subscription ──

  it('multiple subscribers share a single IPC listener', () => {
    const unsub1 = store.subscribe(() => {});
    const unsub2 = store.subscribe(() => {});

    // Only one IPC listener registration
    expect(bridge.on).toHaveBeenCalledTimes(1);

    // First unsub doesn't remove the listener (ref count > 0)
    unsub1();
    expect(bridge.removeListener).not.toHaveBeenCalled();

    // Second unsub removes it (ref count = 0)
    unsub2();
    expect(bridge.removeListener).toHaveBeenCalledTimes(1);
  });

  it('notifies all subscribers on push event', () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    store.subscribe(cb1);
    store.subscribe(cb2);

    simulatePush({
      activeStream: makeStreamPayload('s1', 'test'),
      recentEntities: [],
      streamHistory: [],
    });

    expect(cb1).toHaveBeenCalledTimes(1);
    expect(cb2).toHaveBeenCalledTimes(1);
  });

  it('does not notify unsubscribed consumers', () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    const unsub1 = store.subscribe(cb1);
    store.subscribe(cb2);

    unsub1();

    simulatePush({
      activeStream: makeStreamPayload('s1', 'test'),
      recentEntities: [],
      streamHistory: [],
    });

    expect(cb1).not.toHaveBeenCalled();
    expect(cb2).toHaveBeenCalledTimes(1);
  });

  it('sends context:subscribe IPC only on first consumer', () => {
    store.subscribe(() => {});
    store.subscribe(() => {});

    const subscribeCalls = (bridge.invoke as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c: any[]) => c[0] === 'context:subscribe',
    );
    expect(subscribeCalls.length).toBe(1);
  });
});
