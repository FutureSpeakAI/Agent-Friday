/**
 * Context Stream IPC handlers — expose the unified context stream
 * to the renderer process for real-time activity awareness.
 *
 * Track III, Phase 1: Activity Ingestion.
 *
 * cLaw Gate: Context stream is in-memory only. No persist/export
 * operations are exposed. Sensitive data keys are stripped at the
 * engine level before events reach this handler.
 */
import { ipcMain, BrowserWindow } from 'electron';
import { contextStream } from '../context-stream';
import type { ContextEventType } from '../context-stream';

const VALID_EVENT_TYPES = new Set<string>([
  'ambient', 'clipboard', 'sentiment', 'notification', 'tool-invoke',
  'calendar', 'communication', 'git', 'screen-text', 'user-input', 'system',
]);

export function registerContextStreamHandlers(): void {
  // ── Push an event into the context stream ──────────────────────────
  ipcMain.handle(
    'context-stream:push',
    (_event, payload: unknown) => {
      if (!payload || typeof payload !== 'object') {
        throw new Error('context-stream:push requires an event object');
      }
      const p = payload as Record<string, unknown>;

      if (!p.type || typeof p.type !== 'string' || !VALID_EVENT_TYPES.has(p.type)) {
        throw new Error(
          `context-stream:push requires a valid event type (${Array.from(VALID_EVENT_TYPES).join(', ')})`
        );
      }
      if (!p.source || typeof p.source !== 'string') {
        throw new Error('context-stream:push requires a string source');
      }
      if (!p.summary || typeof p.summary !== 'string') {
        throw new Error('context-stream:push requires a string summary');
      }

      return contextStream.push({
        type: p.type as ContextEventType,
        source: String(p.source),
        summary: String(p.summary),
        data: (p.data && typeof p.data === 'object' ? p.data : {}) as Record<string, unknown>,
        dedupeKey: typeof p.dedupeKey === 'string' ? p.dedupeKey : undefined,
        ttlMs: typeof p.ttlMs === 'number' ? p.ttlMs : undefined,
      });
    },
  );

  // ── Get the current snapshot ───────────────────────────────────────
  ipcMain.handle('context-stream:snapshot', () => {
    return contextStream.getSnapshot();
  });

  // ── Get recent events ──────────────────────────────────────────────
  ipcMain.handle(
    'context-stream:recent',
    (_event, opts?: { limit?: number; types?: string[]; sinceMs?: number }) => {
      const validTypes = opts?.types?.filter(t => VALID_EVENT_TYPES.has(t)) as ContextEventType[] | undefined;
      return contextStream.getRecent({
        limit: typeof opts?.limit === 'number' ? opts.limit : undefined,
        types: validTypes,
        sinceMs: typeof opts?.sinceMs === 'number' ? opts.sinceMs : undefined,
      });
    },
  );

  // ── Get events by type ─────────────────────────────────────────────
  ipcMain.handle(
    'context-stream:by-type',
    (_event, type: string, limit?: number) => {
      if (!type || typeof type !== 'string' || !VALID_EVENT_TYPES.has(type)) {
        throw new Error('context-stream:by-type requires a valid event type');
      }
      return contextStream.getByType(
        type as ContextEventType,
        typeof limit === 'number' ? limit : undefined,
      );
    },
  );

  // ── Get latest event of each type ──────────────────────────────────
  ipcMain.handle('context-stream:latest-by-type', () => {
    const map = contextStream.getLatestByType();
    // Convert Map to plain object for IPC serialization
    const result: Record<string, unknown> = {};
    for (const [key, value] of map) {
      result[key] = value;
    }
    return result;
  });

  // ── Get context string (for prompt injection) ──────────────────────
  ipcMain.handle('context-stream:context-string', () => {
    return contextStream.getContextString();
  });

  // ── Get prompt context (shorter, budget-aware) ─────────────────────
  ipcMain.handle('context-stream:prompt-context', () => {
    return contextStream.getPromptContext();
  });

  // ── Get status ─────────────────────────────────────────────────────
  ipcMain.handle('context-stream:status', () => {
    return contextStream.getStatus();
  });

  // ── Prune expired events ───────────────────────────────────────────
  ipcMain.handle('context-stream:prune', () => {
    return contextStream.prune();
  });

  // ── Enable/disable the stream ──────────────────────────────────────
  ipcMain.handle('context-stream:set-enabled', (_event, enabled: unknown) => {
    if (typeof enabled !== 'boolean') {
      throw new Error('context-stream:set-enabled requires a boolean');
    }
    contextStream.setEnabled(enabled);
    return { enabled };
  });

  // ── Clear the buffer (debugging/testing only) ──────────────────────
  ipcMain.handle('context-stream:clear', () => {
    contextStream.clear();
    return { cleared: true };
  });
}
