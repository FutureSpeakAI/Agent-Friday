/**
 * Track C, Phase 1: "The Loom" — Context Push Handlers
 *
 * Subscribes to the context stream, detects work stream changes,
 * and pushes context:stream-update events to the renderer via
 * webContents.send(). This enables reactive UI updates without polling.
 *
 * The push handler does NOT modify context-graph.ts. It reads from
 * the context graph externally and bridges to the renderer.
 *
 * Hermeneutic note: The loom threads raw events into a fabric the
 * renderer can wear. Each stream change is a shuttle pass — the
 * pattern only emerges when parts connect to the whole.
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { contextStream } from '../context-stream';
import { contextGraph } from '../context-graph';

// ── Types ──────────────────────────────────────────────────────────

export type ContextPushCleanup = () => void;

interface StreamUpdatePayload {
  activeStream: SerializedStream | null;
  recentEntities: any[];
  streamHistory: SerializedStream[];
}

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

// ── Constants ──────────────────────────────────────────────────────

const MAX_STREAM_HISTORY = 5;

// ── Helpers ────────────────────────────────────────────────────────

function serializeStream(stream: any): SerializedStream | null {
  if (!stream) return null;
  return {
    ...stream,
    eventTypes: stream.eventTypes instanceof Set
      ? Array.from(stream.eventTypes)
      : stream.eventTypes,
    entities: Array.isArray(stream.entities)
      ? stream.entities
      : [],
  };
}

// ── Registration ───────────────────────────────────────────────────

/**
 * Register context push infrastructure. Returns a cleanup function
 * that unsubscribes from the context stream and removes IPC handlers.
 */
export function registerContextPushHandlers(
  mainWindow: BrowserWindow,
): ContextPushCleanup {
  let lastActiveStreamId: string | null = null;
  let cleaned = false;

  // Subscribe to context stream for change detection
  const unsubStream = contextStream.on(() => {
    if (cleaned) return;
    if (mainWindow.webContents.isDestroyed()) return;

    const active = contextGraph.getActiveStream();
    const currentId = active?.id ?? null;

    // Only push when the active stream actually changes
    if (currentId === lastActiveStreamId) return;
    lastActiveStreamId = currentId;

    const recentStreams = contextGraph.getRecentStreams(MAX_STREAM_HISTORY);
    const entities = contextGraph.getTopEntities(20);

    const payload: StreamUpdatePayload = {
      activeStream: serializeStream(active),
      recentEntities: entities,
      streamHistory: recentStreams
        .map(s => serializeStream(s))
        .filter((s): s is SerializedStream => s !== null)
        .slice(0, MAX_STREAM_HISTORY),
    };

    mainWindow.webContents.send('context:stream-update', payload);
  });

  // IPC handlers for subscribe/unsubscribe (renderer lifecycle)
  ipcMain.handle('context:subscribe', async () => {
    return { subscribed: true };
  });

  ipcMain.handle('context:unsubscribe', async () => {
    return { unsubscribed: true };
  });

  // Cleanup function
  return () => {
    if (cleaned) return;
    cleaned = true;
    unsubStream();
  };
}
