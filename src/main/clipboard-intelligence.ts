/**
 * clipboard-intelligence.ts — Clipboard Intelligence for EVE OS.
 *
 * Polls the clipboard every 2 seconds, classifies content type
 * (URL, code, email, JSON, text, file path), and emits events
 * to the renderer so EVE can reference clipboard context naturally.
 */

import { clipboard, BrowserWindow } from 'electron';
import { settingsManager } from './settings';

export type ClipboardContentType =
  | 'url'
  | 'code'
  | 'email'
  | 'json'
  | 'path'
  | 'text'
  | 'empty';

export interface ClipboardEntry {
  text: string;
  type: ClipboardContentType;
  timestamp: number;
  /** First 200 chars for context injection */
  preview: string;
}

const POLL_INTERVAL_MS = 2000;
const HISTORY_SIZE = 10;

class ClipboardIntelligence {
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastText = '';
  private history: ClipboardEntry[] = [];
  private mainWindow: BrowserWindow | null = null;

  initialize(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;

    // Initial read
    this.lastText = clipboard.readText() || '';

    this.timer = setInterval(() => {
      if (!settingsManager.get().clipboardIntelligenceEnabled) return;
      this.poll();
    }, POLL_INTERVAL_MS);

    console.log('[ClipboardIntelligence] Initialized — polling every 2s');
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  getRecent(count = 5): ClipboardEntry[] {
    return this.history.slice(-count);
  }

  getCurrent(): ClipboardEntry | null {
    return this.history.length > 0 ? this.history[this.history.length - 1] : null;
  }

  /**
   * Build context string for injection into personality.ts.
   * Shows only the most recent clipboard entry if it's recent enough (<60s).
   */
  getContextString(): string {
    const current = this.getCurrent();
    if (!current) return '';

    const age = Date.now() - current.timestamp;
    if (age > 60_000) return ''; // Stale — don't inject

    return [
      '## Clipboard Context',
      `- Type: ${current.type}`,
      `- Content: ${current.preview}`,
    ].join('\n');
  }

  private poll(): void {
    const text = clipboard.readText() || '';

    // Skip if same as last check or empty
    if (text === this.lastText || text.trim().length === 0) return;

    this.lastText = text;

    const type = this.classify(text);
    const entry: ClipboardEntry = {
      text,
      type,
      timestamp: Date.now(),
      preview: text.length > 200 ? text.slice(0, 200) + '...' : text,
    };

    this.history.push(entry);
    if (this.history.length > HISTORY_SIZE) {
      this.history = this.history.slice(-HISTORY_SIZE);
    }

    // Emit to renderer
    this.mainWindow?.webContents.send('clipboard:changed', {
      type: entry.type,
      preview: entry.preview,
      timestamp: entry.timestamp,
    });

    console.log(`[ClipboardIntelligence] New ${type}: "${entry.preview.slice(0, 60)}..."`);
  }

  private classify(text: string): ClipboardContentType {
    const trimmed = text.trim();

    // URL
    if (/^https?:\/\/\S+$/i.test(trimmed) || /^www\.\S+$/i.test(trimmed)) {
      return 'url';
    }

    // Email address
    if (/^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(trimmed)) {
      return 'email';
    }

    // JSON
    if (
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    ) {
      try {
        JSON.parse(trimmed);
        return 'json';
      } catch {
        // Not valid JSON
      }
    }

    // File path (Windows or Unix)
    if (
      /^[A-Z]:\\[\w\\.-]+/i.test(trimmed) ||
      /^\/[\w/.-]+/.test(trimmed) ||
      /^~\/[\w/.-]+/.test(trimmed)
    ) {
      return 'path';
    }

    // Code detection heuristics
    const codeSignals = [
      /^(import|export|const|let|var|function|class|interface|type|enum)\s/m,
      /^(def|async def|from|import)\s/m,
      /[{};]\s*$/m,
      /=>\s*[{(]/m,
      /^\s*(if|else|for|while|switch|case|return|try|catch)\s*[({]/m,
      /<\/?\w+[\s>]/m,
    ];

    const codeMatches = codeSignals.filter((r) => r.test(trimmed)).length;
    if (codeMatches >= 2 || (trimmed.includes('\n') && codeMatches >= 1)) {
      return 'code';
    }

    return 'text';
  }
}

export const clipboardIntelligence = new ClipboardIntelligence();
