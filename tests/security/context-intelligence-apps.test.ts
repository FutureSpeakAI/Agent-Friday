/**
 * Sprint 2, Phase E.2: "The Synapses" — Intelligence App Context Tests
 *
 * Validates that intelligence apps (Browser, Code, Forge, Comms)
 * consume useAppContext() and receive context-aware updates.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AppContextStore, AppContextSnapshot, AppContextIpcBridge } from '../../src/renderer/hooks/useAppContext';

const INTELLIGENCE_APPS = [
  'friday-browser',
  'friday-code',
  'friday-forge',
  'friday-comms',
] as const;

describe('Intelligence App Context — The Synapses', () => {
  let mockBridge: AppContextIpcBridge;

  beforeEach(() => {
    vi.clearAllMocks();
    mockBridge = {
      invoke: vi.fn().mockResolvedValue(null),
      on: vi.fn(),
      removeListener: vi.fn(),
    };
  });

  // E.2 Criterion 1-4: Each intelligence app receives enriched context
  it.each(INTELLIGENCE_APPS)('%s receives enriched context on initial fetch', async (appId) => {
    const appContext = {
      activeStream: { id: 'ws-intel', name: 'Research Pipeline' },
      entities: [{ type: 'url', name: 'https://example.com' }],
      briefingSummary: 'Active research session',
    };
    mockBridge.invoke = vi.fn().mockResolvedValue(appContext);

    const store = new AppContextStore(appId, mockBridge);

    let snapshot: AppContextSnapshot | null = null;
    store.subscribe(() => {
      snapshot = store.getSnapshot();
    });

    await vi.waitFor(() => {
      expect(snapshot).not.toBeNull();
    });

    expect(snapshot!.context.activeStream).toEqual({ id: 'ws-intel', name: 'Research Pipeline' });
    expect(snapshot!.briefing).toBe('Active research session');
    expect(mockBridge.invoke).toHaveBeenCalledWith('app-context:get', appId);
  });

  // E.2 Criterion 5-6: active stream and briefing available for intelligence apps
  it('exposes active stream name for code analysis context', async () => {
    const appContext = {
      activeStream: { id: 'ws-code', name: 'Refactoring Sprint' },
      entities: [],
      briefingSummary: 'Large diff detected in main branch',
    };
    mockBridge.invoke = vi.fn().mockResolvedValue(appContext);

    const store = new AppContextStore('friday-code', mockBridge);
    store.subscribe(() => {});

    await vi.waitFor(() => {
      expect(store.getSnapshot().context.activeStream?.name).toBe('Refactoring Sprint');
    });
    expect(store.getSnapshot().briefing).toBe('Large diff detected in main branch');
  });

  // E.2 Criterion 7: push updates propagate to intelligence stores
  it('propagates push updates to intelligence app stores', () => {
    const store = new AppContextStore('friday-forge', mockBridge);
    const callback = vi.fn();
    store.subscribe(callback);

    const onCall = (mockBridge.on as ReturnType<typeof vi.fn>).mock.calls.find(
      (c: any[]) => c[0] === 'app-context:update'
    );
    expect(onCall).toBeDefined();

    const pushHandler = onCall![1] as (event: any, payload: any) => void;
    pushHandler(null, {
      activeStream: { id: 'ws-forge', name: 'Capability Scan' },
      entities: [{ type: 'superpower', name: 'web-search' }],
      briefingSummary: '2 new superpowers available',
    });

    expect(callback).toHaveBeenCalled();
    expect(store.getSnapshot().context.activeStream?.name).toBe('Capability Scan');
    expect(store.getSnapshot().entities).toHaveLength(1);
  });

  // E.2 Criterion 8: graceful degradation
  it('degrades gracefully when no context is available', () => {
    const store = new AppContextStore('friday-browser', mockBridge);
    const snap = store.getSnapshot();

    expect(snap.context.activeStream).toBeNull();
    expect(snap.briefing).toBeNull();
    expect(snap.entities).toEqual([]);
  });

  // Intelligence apps get independent stores
  it('creates independent stores for each intelligence app', () => {
    INTELLIGENCE_APPS.forEach((appId) => {
      const store = new AppContextStore(appId, mockBridge);
      store.subscribe(() => {});
    });

    expect(mockBridge.invoke).toHaveBeenCalledTimes(4);
    INTELLIGENCE_APPS.forEach((appId) => {
      expect(mockBridge.invoke).toHaveBeenCalledWith('app-context:get', appId);
    });
  });

  // Comms-specific: briefing with communication context
  it('delivers communication-specific briefing to comms app', async () => {
    const appContext = {
      activeStream: { id: 'ws-comms', name: 'Client Outreach' },
      entities: [{ type: 'email', name: 'draft-001' }],
      briefingSummary: '3 pending drafts awaiting approval',
    };
    mockBridge.invoke = vi.fn().mockResolvedValue(appContext);

    const store = new AppContextStore('friday-comms', mockBridge);
    store.subscribe(() => {});

    await vi.waitFor(() => {
      expect(store.getSnapshot().briefing).toBe('3 pending drafts awaiting approval');
    });
    expect(store.getSnapshot().entities).toHaveLength(1);
  });
});
