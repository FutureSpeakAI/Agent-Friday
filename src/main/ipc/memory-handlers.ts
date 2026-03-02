/**
 * Memory IPC handlers — short/medium/long-term, episodic, semantic search, notifications.
 */
import { ipcMain } from 'electron';
import { memoryManager } from '../memory';
import { episodicMemory } from '../episodic-memory';
import { semanticSearch } from '../semantic-search';
import { notificationEngine } from '../notifications';
import { assertMessageArray, assertString, assertNumber, assertObject, assertArray } from './validate';

export function registerMemoryHandlers(): void {
  // ── Core memory ─────────────────────────────────────────────────────
  ipcMain.handle('memory:get-short-term', () => memoryManager.getShortTerm());
  ipcMain.handle('memory:get-medium-term', () => memoryManager.getMediumTerm());
  ipcMain.handle('memory:get-long-term', () => memoryManager.getLongTerm());

  // Crypto Sprint 8 (HIGH): Validate and cap messages array to prevent memory exhaustion.
  // A compromised renderer could send millions of message objects.
  ipcMain.handle(
    'memory:update-short-term',
    async (_event, messages: unknown) => {
      const validated = assertMessageArray(messages, 'memory:update-short-term messages');
      await memoryManager.updateShortTerm(validated);
    },
  );

  // Crypto Sprint 8 (HIGH): Validate and cap history array.
  ipcMain.handle(
    'memory:extract',
    async (_event, history: unknown) => {
      const validated = assertMessageArray(history, 'memory:extract history');
      await memoryManager.extractMemories(validated);
    },
  );

  // Crypto Sprint 20: Validate IPC inputs.
  ipcMain.handle(
    'memory:update-long-term',
    async (_event, id: unknown, updates: unknown) => {
      assertString(id, 'memory:update-long-term id', 500);
      assertObject(updates, 'memory:update-long-term updates');
      await memoryManager.updateLongTermEntry(id as string, updates as Record<string, unknown>);
    },
  );

  ipcMain.handle('memory:delete-long-term', async (_event, id: unknown) => {
    assertString(id, 'memory:delete-long-term id', 500);
    await memoryManager.deleteLongTermEntry(id as string);
  });

  ipcMain.handle('memory:delete-medium-term', async (_event, id: unknown) => {
    assertString(id, 'memory:delete-medium-term id', 500);
    await memoryManager.deleteMediumTermEntry(id as string);
  });

  ipcMain.handle('memory:add-immediate', async (_event, fact: unknown, category: unknown) => {
    assertString(fact, 'memory:add-immediate fact', 5_000);
    if (category !== undefined && category !== null) {
      assertString(category, 'memory:add-immediate category', 200);
    }
    await memoryManager.addImmediateMemory(fact as string, (category as string) || 'identity');
  });

  // ── Episodic memory ─────────────────────────────────────────────────
  ipcMain.handle(
    'episodic:create',
    async (
      _event,
      transcript: unknown,
      startTime: unknown,
      endTime: unknown,
    ) => {
      assertArray(transcript, 'episodic:create transcript', 10_000);
      assertNumber(startTime, 'episodic:create startTime', 0);
      assertNumber(endTime, 'episodic:create endTime', 0);
      return episodicMemory.createFromSession(
        transcript as Array<{ role: string; text: string }>,
        startTime as number,
        endTime as number,
      );
    },
  );

  ipcMain.handle('episodic:list', () => episodicMemory.getAll());

  ipcMain.handle('episodic:search', (_event, query: unknown) => {
    assertString(query, 'episodic:search query', 10_000);
    return episodicMemory.search(query as string);
  });

  ipcMain.handle('episodic:get', (_event, id: unknown) => {
    assertString(id, 'episodic:get id', 500);
    return episodicMemory.getById(id as string);
  });

  ipcMain.handle('episodic:delete', async (_event, id: unknown) => {
    assertString(id, 'episodic:delete id', 500);
    return episodicMemory.deleteEpisode(id as string);
  });

  ipcMain.handle('episodic:recent', (_event, count: unknown) => {
    assertNumber(count, 'episodic:recent count', 1, 10_000);
    return episodicMemory.getRecent(count as number);
  });

  // ── Semantic search ─────────────────────────────────────────────────
  ipcMain.handle(
    'search:query',
    async (_event, query: unknown, options?: unknown) => {
      assertString(query, 'search:query query', 50_000);
      if (options !== undefined && options !== null) {
        assertObject(options, 'search:query options');
      }
      return semanticSearch.search(query as string, options as Record<string, unknown> | undefined);
    },
  );

  ipcMain.handle('search:stats', () => semanticSearch.getStats());

  // ── Notifications ───────────────────────────────────────────────────
  ipcMain.handle('notifications:get-recent', () => notificationEngine.getRecent());
}
