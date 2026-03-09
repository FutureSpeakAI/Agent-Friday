/**
 * Chat history IPC handlers — persist and restore raw chat messages.
 */
import { ipcMain } from 'electron';
import { chatHistoryStore, type PersistedChatMessage } from '../chat-history';

export function registerChatHistoryHandlers(): void {
  ipcMain.handle('chat-history:load', () => {
    return chatHistoryStore.getMessages();
  });

  ipcMain.handle('chat-history:save', async (_event, messages: unknown) => {
    if (!Array.isArray(messages)) return;
    // Basic validation — each entry must have id, role, content, timestamp
    const validated: PersistedChatMessage[] = [];
    for (const msg of messages.slice(-200)) {
      if (
        msg && typeof msg === 'object' &&
        typeof msg.id === 'string' &&
        typeof msg.role === 'string' &&
        typeof msg.content === 'string' &&
        typeof msg.timestamp === 'number'
      ) {
        validated.push({
          id: msg.id,
          role: msg.role as 'user' | 'assistant',
          content: msg.content,
          model: typeof msg.model === 'string' ? msg.model : undefined,
          timestamp: msg.timestamp,
        });
      }
    }
    await chatHistoryStore.setMessages(validated);
  });

  ipcMain.handle('chat-history:clear', async () => {
    await chatHistoryStore.clear();
  });
}
