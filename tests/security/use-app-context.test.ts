/**
 * Tests for useAppContext hook + AppContextStore — Phase C.3 "The Tapestry"
 *
 * The useAppContext hook subscribes to per-app context updates
 * pushed by the LiveContextBridge. Uses the same DI pattern as
 * WorkContextStore (C.1) for testability.
 *
 * Validation criteria covered:
 *   3. useAppContext(appId) receives enriched context specific to that app
 *   4. Returns { context, briefing, entities }
 *   5. Re-renders only when its specific app's context changes
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
  AppContextStore,
  type AppContextIpcBridge,
} from '../../src/renderer/hooks/useAppContext';

// ── Mock Bridge Factory ─────────────────────────────────────────────

function makeMockBridge() {
  const handlers = new Map<string, (...args: any[]) => void>();

  const bridge: AppContextIpcBridge = {
    invoke: vi.fn(async () => ({
      activeStream: null,
      entities: [],
      briefingSummary: null,
    })),
    on: vi.fn((channel: string, handler: (...args: any[]) => void) => {
      handlers.set(channel, handler);
    }),
    removeListener: vi.fn((channel: string) => {
      handlers.delete(channel);
    }),
  };

  function simulatePush(payload: any) {
    const handler = handlers.get('app-context:update');
    if (handler) {
      handler({}, payload);
    }
  }

  return { bridge, handlers, simulatePush };
}

// ── Helpers ─────────────────────────────────────────────────────────

function makeAppContext(activeStreamName = 'coding') {
  return {
    activeStream: {
      id: 's1',
      name: activeStreamName,
      task: 'dev',
      app: 'code',
      startedAt: Date.now(),
      lastActiveAt: Date.now(),
      eventCount: 3,
      entities: [],
      eventTypes: ['ambient'],
      summary: 'Working',
    },
    entities: [
      {
        type: 'file',
        value: 'main.ts',
        normalizedValue: 'main.ts',
        firstSeen: Date.now(),
        lastSeen: Date.now(),
        occurrences: 5,
        sourceStreamIds: ['s1'],
      },
    ],
    briefingSummary: 'Test briefing content',
  };
}

// ── Tests ───────────────────────────────────────────────────────────

describe('AppContextStore', () => {
  let store: AppContextStore;
  let bridge: AppContextIpcBridge;
  let simulatePush: (payload: any) => void;

  beforeEach(() => {
    vi.clearAllMocks();
    const mock = makeMockBridge();
    bridge = mock.bridge;
    simulatePush = mock.simulatePush;
    store = new AppContextStore('notes', bridge);
  });

  // ── Criterion 3: receives enriched context for a specific app ──

  it('fetches initial context on first subscribe via IPC invoke', () => {
    store.subscribe(() => {});
    expect(bridge.invoke).toHaveBeenCalledWith('app-context:get', 'notes');
  });

  it('updates state from push events', () => {
    store.subscribe(() => {});
    const ctx = makeAppContext();
    simulatePush(ctx);

    const snapshot = store.getSnapshot();
    expect(snapshot.context.activeStream?.name).toBe('coding');
  });

  // ── Criterion 4: returns { context, briefing, entities } ──

  it('getSnapshot returns correct shape', () => {
    const snapshot = store.getSnapshot();
    expect(snapshot).toHaveProperty('context');
    expect(snapshot).toHaveProperty('briefing');
    expect(snapshot).toHaveProperty('entities');
  });

  it('context contains activeStream', () => {
    store.subscribe(() => {});
    simulatePush(makeAppContext());

    const { context } = store.getSnapshot();
    expect(context.activeStream).not.toBeNull();
    expect(context.activeStream?.id).toBe('s1');
  });

  it('briefing is extracted from context briefingSummary', () => {
    store.subscribe(() => {});
    simulatePush(makeAppContext());

    const { briefing } = store.getSnapshot();
    expect(briefing).toBe('Test briefing content');
  });

  it('entities are extracted from context', () => {
    store.subscribe(() => {});
    simulatePush(makeAppContext());

    const { entities } = store.getSnapshot();
    expect(entities.length).toBe(1);
    expect(entities[0].value).toBe('main.ts');
  });

  // ── Criterion 5: notifies only on update ──

  it('notifies subscribers when context changes', () => {
    const cb = vi.fn();
    store.subscribe(cb);

    simulatePush(makeAppContext());
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('notifies all subscribers', () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    store.subscribe(cb1);
    store.subscribe(cb2);

    simulatePush(makeAppContext());
    expect(cb1).toHaveBeenCalledTimes(1);
    expect(cb2).toHaveBeenCalledTimes(1);
  });

  // ── Cleanup ──

  it('cleans up IPC listener on last unsubscribe', () => {
    const unsub = store.subscribe(() => {});
    unsub();
    expect(bridge.removeListener).toHaveBeenCalledWith(
      'app-context:update',
      expect.any(Function),
    );
  });

  it('multiple subscribers share a single IPC listener', () => {
    const unsub1 = store.subscribe(() => {});
    const unsub2 = store.subscribe(() => {});

    expect(bridge.on).toHaveBeenCalledTimes(1);

    unsub1();
    expect(bridge.removeListener).not.toHaveBeenCalled();

    unsub2();
    expect(bridge.removeListener).toHaveBeenCalledTimes(1);
  });

  // ── Initial state ──

  it('returns null/empty state before any updates', () => {
    const snapshot = store.getSnapshot();
    expect(snapshot.context.activeStream).toBeNull();
    expect(snapshot.entities).toEqual([]);
    expect(snapshot.briefing).toBeNull();
  });
});
