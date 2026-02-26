/**
 * ambient.ts — Ambient Context Engine.
 * Polls active window every 10s, tracks app usage patterns,
 * focus streaks, and infers current task context.
 * Feeds into personality.ts for natural context injection.
 */

import { callDesktopTool } from './desktop-tools';
import { settingsManager } from './settings';

export interface AmbientState {
  activeApp: string;
  windowTitle: string;
  /** Map of app name → cumulative seconds spent today */
  appDurations: Record<string, number>;
  /** Current focus streak on the same app (seconds) */
  focusStreak: number;
  /** Inferred task type based on active app */
  inferredTask: string;
  /** Timestamp of last context update */
  lastUpdated: number;
}

interface AppCategory {
  pattern: RegExp;
  task: string;
  label: string;
}

const APP_CATEGORIES: AppCategory[] = [
  { pattern: /visual studio code|vscode|code -/i, task: 'coding', label: 'VS Code' },
  { pattern: /cursor/i, task: 'coding', label: 'Cursor' },
  { pattern: /webstorm|intellij|pycharm|rider/i, task: 'coding', label: 'JetBrains IDE' },
  { pattern: /chrome|firefox|edge|brave|arc/i, task: 'browsing', label: 'Browser' },
  { pattern: /slack|teams|discord/i, task: 'communicating', label: 'Messaging' },
  { pattern: /outlook|thunderbird|gmail/i, task: 'email', label: 'Email' },
  { pattern: /notion|obsidian|logseq|roam/i, task: 'writing', label: 'Notes' },
  { pattern: /word|google docs|docs/i, task: 'writing', label: 'Document Editor' },
  { pattern: /excel|sheets|google sheets/i, task: 'spreadsheets', label: 'Spreadsheet' },
  { pattern: /powerpoint|slides|canva|figma/i, task: 'designing', label: 'Design' },
  { pattern: /spotify|youtube music|apple music/i, task: 'listening', label: 'Music' },
  { pattern: /youtube|netflix|vlc|mpv/i, task: 'watching', label: 'Media' },
  { pattern: /terminal|powershell|cmd|wt\.exe|hyper|iterm/i, task: 'terminal', label: 'Terminal' },
  { pattern: /postman|insomnia/i, task: 'api-testing', label: 'API Client' },
  { pattern: /docker|podman/i, task: 'devops', label: 'Containers' },
  { pattern: /explorer|finder/i, task: 'file-management', label: 'File Manager' },
];

const POLL_INTERVAL = 10_000; // 10s
const DAY_RESET_HOUR = 4; // Reset daily counters at 4am

class AmbientEngine {
  private state: AmbientState = {
    activeApp: '',
    windowTitle: '',
    appDurations: {},
    focusStreak: 0,
    inferredTask: '',
    lastUpdated: 0,
  };

  private interval: ReturnType<typeof setInterval> | null = null;
  private lastPollTime = 0;
  private lastResetDate = '';

  initialize(): void {
    this.lastPollTime = Date.now();
    this.lastResetDate = this.todayKey();
    this.interval = setInterval(() => this.poll(), POLL_INTERVAL);
    // Immediate first poll
    this.poll();
    console.log('[Ambient] Initialized — polling every 10s');
  }

  stop(): void {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
  }

  getState(): AmbientState {
    return { ...this.state, appDurations: { ...this.state.appDurations } };
  }

  /**
   * Build a context string for injection into the system prompt.
   * Returns empty string if no meaningful context exists.
   */
  getContextString(): string {
    if (!this.state.activeApp && !this.state.windowTitle) return '';

    const parts: string[] = ['## Ambient Context'];

    // Current activity
    if (this.state.inferredTask) {
      const userName = settingsManager.getAgentConfig().userName || 'The user';
      parts.push(`- ${userName} is currently ${this.state.inferredTask} (${this.state.activeApp})`);
    } else if (this.state.activeApp) {
      parts.push(`- Active app: ${this.state.activeApp}`);
    }

    // Window title (truncated, useful for project/file context)
    if (this.state.windowTitle && this.state.windowTitle !== this.state.activeApp) {
      const title = this.state.windowTitle.length > 100
        ? this.state.windowTitle.slice(0, 100) + '...'
        : this.state.windowTitle;
      parts.push(`- Window: "${title}"`);
    }

    // Focus streak (only mention if substantial)
    if (this.state.focusStreak > 120) {
      const mins = Math.floor(this.state.focusStreak / 60);
      parts.push(`- Focus streak: ${mins} min on ${this.state.activeApp}`);
    }

    // Top apps today (max 3)
    const sorted = Object.entries(this.state.appDurations)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 3);

    if (sorted.length > 0 && sorted[0][1] > 60) {
      const summary = sorted
        .filter(([, secs]) => secs > 30)
        .map(([app, secs]) => `${app}: ${Math.floor(secs / 60)}m`)
        .join(', ');
      if (summary) {
        parts.push(`- Today's usage: ${summary}`);
      }
    }

    return parts.length > 1 ? parts.join('\n') : '';
  }

  private async poll(): Promise<void> {
    const now = Date.now();

    // Daily reset check
    const today = this.todayKey();
    if (today !== this.lastResetDate) {
      this.state.appDurations = {};
      this.lastResetDate = today;
    }

    // Get active window
    let windowTitle = '';
    try {
      const result = await callDesktopTool('get_active_window', {});
      windowTitle = result.result || '';
    } catch {
      // Non-critical — keep last known state
      return;
    }

    const elapsed = (now - this.lastPollTime) / 1000;
    this.lastPollTime = now;

    // Classify app from window title
    const { app: appName, task } = this.classifyWindow(windowTitle);

    // Update focus streak
    if (appName === this.state.activeApp && appName !== '') {
      this.state.focusStreak += elapsed;
    } else {
      this.state.focusStreak = elapsed;
    }

    // Update app durations
    if (appName) {
      this.state.appDurations[appName] = (this.state.appDurations[appName] || 0) + elapsed;
    }

    // Update state
    this.state.activeApp = appName;
    this.state.windowTitle = windowTitle;
    this.state.inferredTask = task;
    this.state.lastUpdated = now;
  }

  private classifyWindow(title: string): { app: string; task: string } {
    if (!title) return { app: '', task: '' };

    for (const cat of APP_CATEGORIES) {
      if (cat.pattern.test(title)) {
        return { app: cat.label, task: cat.task };
      }
    }

    // Fallback: extract process-like name from title
    // Window titles often end with " - AppName"
    const parts = title.split(' - ');
    const appName = parts.length > 1 ? parts[parts.length - 1].trim() : title.trim();
    return { app: appName.slice(0, 40), task: '' };
  }

  private todayKey(): string {
    const d = new Date();
    // If before reset hour, count as previous day
    if (d.getHours() < DAY_RESET_HOUR) {
      d.setDate(d.getDate() - 1);
    }
    return d.toISOString().slice(0, 10);
  }
}

export const ambientEngine = new AmbientEngine();
