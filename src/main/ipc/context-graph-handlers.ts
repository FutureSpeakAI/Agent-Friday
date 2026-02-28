/**
 * IPC handlers for Track III Phase 2: Context Graph.
 *
 * Exposes context graph queries to the renderer. All operations
 * are read-only or ephemeral (no persistence).
 *
 * cLaw Gate: No save/persist/export operations exposed.
 */
import { ipcMain } from 'electron';
import { contextGraph } from '../context-graph';
import type { EntityType } from '../context-graph';

const VALID_ENTITY_TYPES = new Set<string>([
  'file', 'app', 'person', 'topic', 'url', 'tool', 'project', 'channel',
]);

/**
 * Serialize a WorkStream for IPC transport (Set → Array).
 */
function serializeStream(stream: any): any {
  if (!stream) return null;
  return {
    ...stream,
    eventTypes: stream.eventTypes instanceof Set
      ? Array.from(stream.eventTypes)
      : stream.eventTypes,
  };
}

export function registerContextGraphHandlers(): void {
  // ── Snapshot ─────────────────────────────────────────────────────
  ipcMain.handle('context-graph:snapshot', () => {
    const snap = contextGraph.getSnapshot();
    return {
      ...snap,
      activeStream: serializeStream(snap.activeStream),
      recentStreams: snap.recentStreams.map(s => serializeStream(s)),
    };
  });

  // ── Active Stream ───────────────────────────────────────────────
  ipcMain.handle('context-graph:active-stream', () => {
    return serializeStream(contextGraph.getActiveStream());
  });

  // ── Recent Streams ──────────────────────────────────────────────
  ipcMain.handle('context-graph:recent-streams', (_event, limit?: unknown) => {
    const l = typeof limit === 'number' && limit > 0 ? Math.min(limit, 50) : 10;
    return contextGraph.getRecentStreams(l).map(s => serializeStream(s));
  });

  // ── Streams by Task ─────────────────────────────────────────────
  ipcMain.handle('context-graph:streams-by-task', (_event, task: unknown) => {
    if (typeof task !== 'string' || !task.trim()) {
      throw new Error('context-graph:streams-by-task requires a string task');
    }
    return contextGraph.getStreamsByTask(task).map(s => serializeStream(s));
  });

  // ── Entity Queries ──────────────────────────────────────────────
  ipcMain.handle('context-graph:entities-by-type', (_event, type: unknown, limit?: unknown) => {
    if (typeof type !== 'string' || !VALID_ENTITY_TYPES.has(type)) {
      throw new Error(
        `context-graph:entities-by-type requires a valid entity type. Got: "${type}"`,
      );
    }
    const l = typeof limit === 'number' && limit > 0 ? Math.min(limit, 100) : 20;
    return contextGraph.getEntitiesByType(type as EntityType, l);
  });

  ipcMain.handle('context-graph:top-entities', (_event, limit?: unknown) => {
    const l = typeof limit === 'number' && limit > 0 ? Math.min(limit, 50) : 15;
    return contextGraph.getTopEntities(l);
  });

  ipcMain.handle('context-graph:active-entities', (_event, windowMs?: unknown) => {
    const w = typeof windowMs === 'number' && windowMs > 0
      ? Math.min(windowMs, 30 * 60 * 1000)
      : 5 * 60 * 1000;
    return contextGraph.getActiveEntities(w);
  });

  ipcMain.handle('context-graph:related-entities', (_event, type: unknown, value: unknown, limit?: unknown) => {
    if (typeof type !== 'string' || !VALID_ENTITY_TYPES.has(type)) {
      throw new Error(
        `context-graph:related-entities requires a valid entity type. Got: "${type}"`,
      );
    }
    if (typeof value !== 'string' || !value.trim()) {
      throw new Error('context-graph:related-entities requires a string value');
    }
    const l = typeof limit === 'number' && limit > 0 ? Math.min(limit, 50) : 10;
    return contextGraph.getRelatedEntities(type as EntityType, value, l);
  });

  // ── Context Strings ─────────────────────────────────────────────
  ipcMain.handle('context-graph:context-string', () => {
    return contextGraph.getContextString();
  });

  ipcMain.handle('context-graph:prompt-context', () => {
    return contextGraph.getPromptContext();
  });

  // ── Status ──────────────────────────────────────────────────────
  ipcMain.handle('context-graph:status', () => {
    return contextGraph.getStatus();
  });
}
