/**
 * Chat history persistence — stores raw chat messages to disk so
 * conversation survives app restart.
 *
 * Stores at: ${userData}/memory/chat-history.json
 * Caps at MAX_PERSISTED_MESSAGES to prevent unbounded growth.
 */
import { app } from 'electron';
import * as fs from 'fs/promises';
import * as path from 'path';

export interface PersistedChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  timestamp: number;
}

const MAX_PERSISTED_MESSAGES = 200;

class ChatHistoryStore {
  private filePath = '';
  private messages: PersistedChatMessage[] = [];
  private dirty = false;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  async initialize(): Promise<void> {
    const memoryDir = path.join(app.getPath('userData'), 'memory');
    await fs.mkdir(memoryDir, { recursive: true });
    this.filePath = path.join(memoryDir, 'chat-history.json');
    await this.load();
  }

  private async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        this.messages = parsed.slice(-MAX_PERSISTED_MESSAGES);
      }
    } catch {
      // File doesn't exist yet or is corrupt — start fresh
      this.messages = [];
    }
  }

  /** Get all persisted messages. */
  getMessages(): PersistedChatMessage[] {
    return this.messages;
  }

  /** Replace the full message list (from renderer state). Debounced save. */
  async setMessages(msgs: PersistedChatMessage[]): Promise<void> {
    this.messages = msgs.slice(-MAX_PERSISTED_MESSAGES);
    this.scheduleSave();
  }

  /** Append messages (e.g., a new user+assistant pair). Debounced save. */
  async appendMessages(msgs: PersistedChatMessage[]): Promise<void> {
    this.messages.push(...msgs);
    if (this.messages.length > MAX_PERSISTED_MESSAGES) {
      this.messages = this.messages.slice(-MAX_PERSISTED_MESSAGES);
    }
    this.scheduleSave();
  }

  /** Clear all chat history. */
  async clear(): Promise<void> {
    this.messages = [];
    await this.save();
  }

  /** Flush any pending save immediately (call on app quit). */
  async flush(): Promise<void> {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    if (this.dirty) {
      await this.save();
    }
  }

  private scheduleSave(): void {
    this.dirty = true;
    if (this.saveTimer) return; // already scheduled
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      this.save().catch((err) => {
        console.error('[ChatHistory] Save failed:', err instanceof Error ? err.message : 'Unknown');
      });
    }, 2000); // 2s debounce
  }

  private async save(): Promise<void> {
    this.dirty = false;
    try {
      await fs.writeFile(this.filePath, JSON.stringify(this.messages, null, 2), 'utf-8');
    } catch (err) {
      console.error('[ChatHistory] Write failed:', err instanceof Error ? err.message : 'Unknown');
    }
  }
}

export const chatHistoryStore = new ChatHistoryStore();
