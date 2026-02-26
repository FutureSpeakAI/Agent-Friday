/**
 * Memory IPC handlers — short/medium/long-term, episodic, semantic search, notifications.
 */
import { ipcMain } from 'electron';
import { memoryManager } from '../memory';
import { episodicMemory } from '../episodic-memory';
import { semanticSearch } from '../semantic-search';
import { notificationEngine } from '../notifications';

export function registerMemoryHandlers(): void {
  // ── Core memory ─────────────────────────────────────────────────────
  ipcMain.handle('memory:get-short-term', () => memoryManager.getShortTerm());
  ipcMain.handle('memory:get-medium-term', () => memoryManager.getMediumTerm());
  ipcMain.handle('memory:get-long-term', () => memoryManager.getLongTerm());

  ipcMain.handle(
    'memory:update-short-term',
    async (_event, messages: Array<{ role: string; content: string }>) => {
      await memoryManager.updateShortTerm(messages);
    },
  );

  ipcMain.handle(
    'memory:extract',
    async (_event, history: Array<{ role: string; content: string }>) => {
      await memoryManager.extractMemories(history);
    },
  );

  ipcMain.handle(
    'memory:update-long-term',
    async (_event, id: string, updates: Record<string, unknown>) => {
      await memoryManager.updateLongTermEntry(id, updates);
    },
  );

  ipcMain.handle('memory:delete-long-term', async (_event, id: string) => {
    await memoryManager.deleteLongTermEntry(id);
  });

  ipcMain.handle('memory:delete-medium-term', async (_event, id: string) => {
    await memoryManager.deleteMediumTermEntry(id);
  });

  ipcMain.handle('memory:add-immediate', async (_event, fact: string, category: string) => {
    await memoryManager.addImmediateMemory(fact, category);
  });

  // ── Episodic memory ─────────────────────────────────────────────────
  ipcMain.handle(
    'episodic:create',
    async (
      _event,
      transcript: Array<{ role: string; text: string }>,
      startTime: number,
      endTime: number,
    ) => {
      return episodicMemory.createFromSession(transcript, startTime, endTime);
    },
  );

  ipcMain.handle('episodic:list', () => episodicMemory.getAll());
  ipcMain.handle('episodic:search', (_event, query: string) => episodicMemory.search(query));
  ipcMain.handle('episodic:get', (_event, id: string) => episodicMemory.getById(id));
  ipcMain.handle('episodic:delete', async (_event, id: string) => episodicMemory.deleteEpisode(id));
  ipcMain.handle('episodic:recent', (_event, count: number) => episodicMemory.getRecent(count));

  // ── Semantic search ─────────────────────────────────────────────────
  ipcMain.handle(
    'search:query',
    async (_event, query: string, options?: Record<string, unknown>) => {
      return semanticSearch.search(query, options);
    },
  );

  ipcMain.handle('search:stats', () => semanticSearch.getStats());

  // ── Notifications ───────────────────────────────────────────────────
  ipcMain.handle('notifications:get-recent', () => notificationEngine.getRecent());
}
