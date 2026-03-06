/**
 * Track C, Phase 3: "The Tapestry" — Per-App Context Hook
 *
 * Provides a useAppContext(appId) React hook that subscribes to
 * enriched context updates from the LiveContextBridge. Uses the
 * same DI pattern as WorkContextStore (C.1).
 *
 * Architecture:
 *   AppContextStore (pure JS, testable without React)
 *     └── useAppContext(appId) hook (thin useSyncExternalStore wrapper)
 */

import { useSyncExternalStore } from 'react';

// ── Types ──────────────────────────────────────────────────────────

interface AppContext {
  activeStream: any | null;
  entities: any[];
  briefingSummary: string | null;
}

export interface AppContextSnapshot {
  context: AppContext;
  briefing: string | null;
  entities: any[];
}

export interface AppContextIpcBridge {
  invoke(channel: string, ...args: any[]): Promise<any>;
  on(channel: string, handler: (...args: any[]) => void): void;
  removeListener(channel: string, handler: (...args: any[]) => void): void;
}

// ── AppContextStore ────────────────────────────────────────────────

export class AppContextStore {
  private state: AppContextSnapshot = {
    context: { activeStream: null, entities: [], briefingSummary: null },
    briefing: null,
    entities: [],
  };
  private subscribers = new Set<() => void>();
  private ipcHandler: ((_event: any, payload: any) => void) | null = null;
  private refCount = 0;
  private ipc: AppContextIpcBridge;
  private appId: string;

  constructor(appId: string, ipc: AppContextIpcBridge) {
    this.appId = appId;
    this.ipc = ipc;
  }

  getSnapshot(): AppContextSnapshot {
    return this.state;
  }

  subscribe(callback: () => void): () => void {
    this.subscribers.add(callback);
    this.refCount++;

    if (this.refCount === 1) {
      this.activate();
    }

    return () => {
      this.subscribers.delete(callback);
      this.refCount--;

      if (this.refCount === 0) {
        this.deactivate();
      }
    };
  }

  // ── Private ────────────────────────────────────────────────────

  private activate(): void {
    // Fetch initial context
    this.ipc.invoke('app-context:get', this.appId).then((ctx: any) => {
      if (ctx) this.applyUpdate(ctx);
    });

    // Listen for push updates
    this.ipcHandler = (_event: any, payload: any) => {
      this.applyUpdate(payload);
    };
    this.ipc.on('app-context:update', this.ipcHandler);
  }

  private deactivate(): void {
    if (this.ipcHandler) {
      this.ipc.removeListener('app-context:update', this.ipcHandler);
      this.ipcHandler = null;
    }
  }

  private applyUpdate(ctx: AppContext): void {
    if (!ctx || typeof ctx !== 'object') return;

    this.state = {
      context: ctx,
      briefing: ctx.briefingSummary ?? null,
      entities: Array.isArray(ctx.entities) ? ctx.entities : [],
    };

    this.notify();
  }

  private notify(): void {
    for (const cb of this.subscribers) {
      cb();
    }
  }
}

// ── Singleton cache (production) ────────────────────────────────────

const _stores = new Map<string, AppContextStore>();

function getStore(appId: string): AppContextStore {
  let store = _stores.get(appId);
  if (!store) {
    const eve = (window as any).eve;
    let unsub: (() => void) | null = null;

    const bridge: AppContextIpcBridge = {
      invoke: (channel: string, ...args: any[]) => {
        if (channel === 'app-context:get') return eve.appContext.get(args[0]);
        return Promise.resolve(null);
      },
      on: (_channel: string, handler: (...args: any[]) => void) => {
        unsub = eve.appContext.onUpdate((ctx: any) => handler(null, ctx));
      },
      removeListener: () => {
        if (unsub) {
          unsub();
          unsub = null;
        }
      },
    };
    store = new AppContextStore(appId, bridge);
    _stores.set(appId, store);
  }
  return store;
}

// ── React Hook ─────────────────────────────────────────────────────

/**
 * Subscribe to enriched context updates for a specific app.
 *
 * Returns { context, briefing, entities } where context includes
 * the active stream, briefing is a summary string or null, and
 * entities are filtered to the app's domain.
 */
export function useAppContext(appId: string): AppContextSnapshot {
  const store = getStore(appId);
  return useSyncExternalStore(
    (callback) => store.subscribe(callback),
    () => store.getSnapshot(),
  );
}
