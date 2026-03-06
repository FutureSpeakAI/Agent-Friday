/**
 * Track C, Phase 1: "The Loom" — Renderer-Side Context Subscriptions
 *
 * Provides a useWorkContext() React hook that subscribes to context
 * graph push updates from the main process. Multiple components
 * share a single IPC subscription via reference counting.
 *
 * Architecture:
 *   WorkContextStore (pure JS, testable without React)
 *     └── useWorkContext() hook (thin useSyncExternalStore wrapper)
 *
 * Hermeneutic note: The hook is the lens through which the renderer
 * sees the main process's understanding of the user's work. It
 * transforms raw context into React-consumable state — the parts
 * (entities, streams) become accessible to the whole (the UI).
 */

import { useSyncExternalStore } from 'react';

// ── Types ──────────────────────────────────────────────────────────

interface SerializedStream {
  id: string;
  name: string;
  task: string;
  app: string;
  startedAt: number;
  lastActiveAt: number;
  eventCount: number;
  entities: any[];
  eventTypes: string[];
  summary: string;
}

interface EntityRef {
  type: string;
  value: string;
  normalizedValue: string;
  firstSeen: number;
  lastSeen: number;
  occurrences: number;
  sourceStreamIds: string[];
}

export interface WorkContextState {
  activeStream: SerializedStream | null;
  recentEntities: EntityRef[];
  streamHistory: SerializedStream[];
}

/**
 * IPC bridge interface — abstraction over electron's ipcRenderer.
 * Injected via constructor for testability. In production, wired
 * from the preload-exposed window.eve API.
 */
export interface ContextIpcBridge {
  invoke(channel: string, ...args: any[]): Promise<any>;
  on(channel: string, handler: (...args: any[]) => void): void;
  removeListener(channel: string, handler: (...args: any[]) => void): void;
}

// ── Constants ──────────────────────────────────────────────────────

const MAX_STREAM_HISTORY = 5;

// ── WorkContextStore ───────────────────────────────────────────────

/**
 * External store for work context state. Manages IPC subscription
 * lifecycle and shared state across multiple React consumers.
 *
 * Exported for test isolation. Production code uses the singleton.
 */
export class WorkContextStore {
  private state: WorkContextState = {
    activeStream: null,
    recentEntities: [],
    streamHistory: [],
  };
  private subscribers = new Set<() => void>();
  private ipcHandler: ((_event: any, payload: any) => void) | null = null;
  private refCount = 0;
  private ipc: ContextIpcBridge;

  constructor(ipc: ContextIpcBridge) {
    this.ipc = ipc;
  }

  getSnapshot(): WorkContextState {
    return this.state;
  }

  /**
   * Subscribe to state changes. Returns an unsubscribe function.
   * First subscriber activates the IPC listener; last unsubscribe
   * cleans it up (reference counting).
   */
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
    // Register for push updates
    this.ipc.invoke('context:subscribe');

    // Listen for push events
    this.ipcHandler = (_event: any, payload: any) => {
      this.applyUpdate(payload);
    };
    this.ipc.on('context:stream-update', this.ipcHandler);
  }

  private deactivate(): void {
    if (this.ipcHandler) {
      this.ipc.removeListener('context:stream-update', this.ipcHandler);
      this.ipcHandler = null;
    }

    this.ipc.invoke('context:unsubscribe');
  }

  private applyUpdate(payload: any): void {
    if (!payload || typeof payload !== 'object') return;

    const entities = Array.isArray(payload.recentEntities)
      ? [...payload.recentEntities].sort(
          (a: EntityRef, b: EntityRef) => b.occurrences - a.occurrences,
        )
      : [];

    const streamHistory = Array.isArray(payload.streamHistory)
      ? payload.streamHistory.slice(0, MAX_STREAM_HISTORY)
      : [];

    this.state = {
      activeStream: payload.activeStream ?? null,
      recentEntities: entities,
      streamHistory,
    };

    this.notify();
  }

  private notify(): void {
    for (const cb of this.subscribers) {
      cb();
    }
  }
}

// ── Singleton (production) ─────────────────────────────────────────

// In production, window.eve is exposed via preload contextBridge.
// The singleton is created lazily on first hook call.
let _store: WorkContextStore | null = null;

function getStore(): WorkContextStore {
  if (!_store) {
    // Access the preload-injected ipcRenderer
    const { ipcRenderer } = require('electron');
    _store = new WorkContextStore(ipcRenderer);
  }
  return _store;
}

// ── React Hook ─────────────────────────────────────────────────────

/**
 * Subscribe to live work context updates from the main process.
 *
 * Returns the current active stream, recent entities (sorted by
 * occurrence count), and stream history (last 5, reverse chronological).
 *
 * Multiple components calling this hook share a single IPC subscription.
 * The subscription is cleaned up when the last consumer unmounts.
 */
export function useWorkContext(): WorkContextState {
  const store = getStore();
  return useSyncExternalStore(
    (callback) => store.subscribe(callback),
    () => store.getSnapshot(),
  );
}
