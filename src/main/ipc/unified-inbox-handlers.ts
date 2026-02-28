/**
 * Unified Inbox IPC handlers — message queries, actions, stats, config.
 */
import { ipcMain } from 'electron';
import { unifiedInbox } from '../unified-inbox';
import type { UrgencyLevel, MessageCategory, InboxConfig } from '../unified-inbox';

export function registerUnifiedInboxHandlers(): void {
  // ── Queries ──────────────────────────────────────────────────────────

  ipcMain.handle(
    'inbox:get-messages',
    (_event, opts?: {
      unreadOnly?: boolean;
      channel?: string;
      urgencyLevel?: UrgencyLevel;
      category?: MessageCategory;
      limit?: number;
      offset?: number;
      includeArchived?: boolean;
    }) => {
      return unifiedInbox.getMessages(opts);
    },
  );

  ipcMain.handle('inbox:get-message', (_event, id: string) => {
    return unifiedInbox.getMessage(id);
  });

  ipcMain.handle('inbox:get-stats', () => {
    return unifiedInbox.getStats();
  });

  // ── Actions ──────────────────────────────────────────────────────────

  ipcMain.handle('inbox:mark-read', (_event, ids: string | string[]) => {
    unifiedInbox.markRead(ids);
    return { ok: true };
  });

  ipcMain.handle('inbox:mark-unread', (_event, ids: string | string[]) => {
    unifiedInbox.markUnread(ids);
    return { ok: true };
  });

  ipcMain.handle('inbox:archive', (_event, ids: string | string[]) => {
    unifiedInbox.archive(ids);
    return { ok: true };
  });

  ipcMain.handle('inbox:unarchive', (_event, ids: string | string[]) => {
    unifiedInbox.unarchive(ids);
    return { ok: true };
  });

  ipcMain.handle('inbox:delete', (_event, ids: string | string[]) => {
    const removed = unifiedInbox.deleteMessages(ids);
    return { ok: true, removed };
  });

  ipcMain.handle('inbox:mark-all-read', () => {
    const count = unifiedInbox.markAllRead();
    return { ok: true, count };
  });

  // ── Configuration ────────────────────────────────────────────────────

  ipcMain.handle('inbox:get-config', () => {
    return unifiedInbox.getConfig();
  });

  ipcMain.handle('inbox:update-config', (_event, partial: Partial<InboxConfig>) => {
    unifiedInbox.updateConfig(partial);
    return { ok: true };
  });
}
