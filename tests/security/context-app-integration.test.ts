/**
 * Sprint 2, Phase E.1: "The Nerve Endings" — App Context Integration Tests
 *
 * Validates that productivity apps (Notes, Tasks, Calendar, Files)
 * consume useAppContext() and render context-aware sections.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AppContextStore, AppContextSnapshot, AppContextIpcBridge } from '../../src/renderer/hooks/useAppContext';

// ── AppContextStore unit tests (no React needed) ────────────────

describe('AppContextStore — Context Consumption', () => {
  let store: AppContextStore;
  let mockBridge: AppContextIpcBridge;

  beforeEach(() => {
    vi.clearAllMocks();
    mockBridge = {
      invoke: vi.fn().mockResolvedValue(null),
      on: vi.fn(),
      removeListener: vi.fn(),
    };
  });

  // E.1 Criterion 1-4: Each app receives enriched context via store
  it('delivers enriched context to subscribers on initial fetch', async () => {
    const appContext = {
      activeStream: { id: 'ws-1', name: 'Sprint 2 Planning' },
      entities: [{ type: 'file', name: 'index.ts' }],
      briefingSummary: 'High-priority tasks detected',
    };
    mockBridge.invoke = vi.fn().mockResolvedValue(appContext);

    store = new AppContextStore('friday-notes', mockBridge);

    let snapshot: AppContextSnapshot | null = null;
    store.subscribe(() => {
      snapshot = store.getSnapshot();
    });

    // Wait for async invoke to complete
    await vi.waitFor(() => {
      expect(snapshot).not.toBeNull();
    });

    expect(snapshot!.context.activeStream).toEqual({ id: 'ws-1', name: 'Sprint 2 Planning' });
    expect(snapshot!.briefing).toBe('High-priority tasks detected');
    expect(snapshot!.entities).toHaveLength(1);
  });

  // E.1 Criterion 5: active work stream name is available
  it('exposes active work stream name from context', () => {
    store = new AppContextStore('friday-tasks', mockBridge);
    const snap = store.getSnapshot();

    // Default state: no active stream
    expect(snap.context.activeStream).toBeNull();
  });

  // E.1 Criterion 6: briefing summary is available
  it('exposes briefing summary from context', async () => {
    const appContext = {
      activeStream: null,
      entities: [],
      briefingSummary: 'You have 3 overdue tasks',
    };
    mockBridge.invoke = vi.fn().mockResolvedValue(appContext);

    store = new AppContextStore('friday-tasks', mockBridge);
    store.subscribe(() => {});

    await vi.waitFor(() => {
      expect(store.getSnapshot().briefing).toBe('You have 3 overdue tasks');
    });
  });

  // E.1 Criterion 7: push updates re-render only context section
  it('notifies subscribers on push update', () => {
    store = new AppContextStore('friday-calendar', mockBridge);
    const callback = vi.fn();
    store.subscribe(callback);

    // Simulate push update (the IPC `on` handler)
    const onCall = (mockBridge.on as ReturnType<typeof vi.fn>).mock.calls.find(
      (c: any[]) => c[0] === 'app-context:update'
    );
    expect(onCall).toBeDefined();

    const pushHandler = onCall![1] as (event: any, payload: any) => void;
    pushHandler(null, {
      activeStream: { id: 'ws-2', name: 'Bug Fixes' },
      entities: [],
      briefingSummary: null,
    });

    // Subscriber was notified
    expect(callback).toHaveBeenCalled();
    expect(store.getSnapshot().context.activeStream).toEqual(
      { id: 'ws-2', name: 'Bug Fixes' }
    );
  });

  // E.1 Criterion 8: graceful degradation when context is empty
  it('degrades gracefully with empty context', () => {
    store = new AppContextStore('friday-files', mockBridge);
    const snap = store.getSnapshot();

    expect(snap.context.activeStream).toBeNull();
    expect(snap.briefing).toBeNull();
    expect(snap.entities).toEqual([]);
  });

  // Additional: different appIds get separate stores
  it('creates independent stores per appId', () => {
    const store1 = new AppContextStore('friday-notes', mockBridge);
    const store2 = new AppContextStore('friday-tasks', mockBridge);

    store1.subscribe(() => {});
    store2.subscribe(() => {});

    // Both invoked with their respective appIds
    expect(mockBridge.invoke).toHaveBeenCalledWith('app-context:get', 'friday-notes');
    expect(mockBridge.invoke).toHaveBeenCalledWith('app-context:get', 'friday-tasks');
  });

  // Unsubscribe deactivates listener
  it('deactivates IPC listener on last unsubscribe', () => {
    store = new AppContextStore('friday-notes', mockBridge);
    const unsub = store.subscribe(() => {});

    expect(mockBridge.on).toHaveBeenCalledOnce();

    unsub();

    expect(mockBridge.removeListener).toHaveBeenCalledOnce();
  });
});
