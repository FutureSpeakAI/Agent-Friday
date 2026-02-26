/**
 * predictor.ts — Predictive intelligence engine.
 * Polls context signals (time, active window, idle, tasks, memory patterns)
 * and sends conservative suggestions to the renderer when relevant.
 */

import { app, BrowserWindow } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { taskScheduler } from './scheduler';
import { memoryManager } from './memory';
import { ambientEngine } from './ambient';
import { sentimentEngine } from './sentiment';

export interface PredictionSuggestion {
  type: 'morning_briefing' | 'app_context' | 'idle_prompt' | 'task_reminder' | 'emotional_checkin' | 'celebration' | 'reflection';
  message: string;
  confidence: number;
}

interface ContextSnapshot {
  hour: number;
  dayOfWeek: number; // 0=Sun
  activeWindow: string;
  idleSec: number;
  isFirstSessionToday: boolean;
  pendingTaskCount: number;
  sustainedStress: boolean;   // mood has been frustrated/stressed for multiple entries
  recentMoodPositive: boolean; // mood recently shifted to excited/positive
}

const POLL_INTERVAL = 30_000; // 30s
const COOLDOWN = 5 * 60_000; // 5 min between suggestions
const MIN_CONFIDENCE = 0.5;

class Predictor {
  private mainWindow: BrowserWindow | null = null;
  private interval: ReturnType<typeof setInterval> | null = null;
  private lastSuggestionTime = 0;
  private lastInteractionTime = Date.now();
  private todaySessionFlag: string = '';
  private lastActiveWindow = '';
  private readonly flagPath: string;

  constructor() {
    try {
      this.flagPath = path.join(app.getPath('userData'), 'predictor-state.json');
    } catch {
      this.flagPath = '';
    }
    this.loadFlag();
  }

  private loadFlag(): void {
    try {
      if (this.flagPath && fs.existsSync(this.flagPath)) {
        const data = JSON.parse(fs.readFileSync(this.flagPath, 'utf-8'));
        this.todaySessionFlag = data.todaySessionFlag || '';
      }
    } catch {
      // Non-critical — will just re-trigger morning briefing once
    }
  }

  private saveFlag(): void {
    try {
      if (this.flagPath) {
        fs.writeFileSync(this.flagPath, JSON.stringify({ todaySessionFlag: this.todaySessionFlag }));
      }
    } catch {
      // Non-critical
    }
  }

  initialize(win: BrowserWindow): void {
    this.mainWindow = win;
    this.lastInteractionTime = Date.now();

    this.interval = setInterval(() => this.poll(), POLL_INTERVAL);
    console.log('[Predictor] Initialized — polling every 30s');
  }

  stop(): void {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
  }

  /** Called by renderer (via IPC) whenever user speaks or interacts */
  recordInteraction(): void {
    this.lastInteractionTime = Date.now();
  }

  private async poll(): Promise<void> {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) return;

    // Respect cooldown
    if (Date.now() - this.lastSuggestionTime < COOLDOWN) return;

    try {
      const ctx = await this.gatherContext();
      const suggestion = this.evaluate(ctx);

      if (suggestion && suggestion.confidence >= MIN_CONFIDENCE) {
        this.lastSuggestionTime = Date.now();
        this.mainWindow.webContents.send('predictor:suggestion', suggestion);
        console.log(`[Predictor] Sent suggestion: ${suggestion.type} (${suggestion.confidence})`);
      }
    } catch (err) {
      // Silent — predictor failing should never crash the app
    }
  }

  private async gatherContext(): Promise<ContextSnapshot> {
    const now = new Date();
    const today = now.toISOString().slice(0, 10);
    const isFirstSessionToday = this.todaySessionFlag !== today;

    // Get active window from ambient engine (avoids duplicate polling)
    const ambient = ambientEngine.getState();
    const activeWindow = ambient.windowTitle || '';

    // Count pending tasks
    let pendingTaskCount = 0;
    try {
      const tasks = taskScheduler.listTasks();
      pendingTaskCount = tasks.filter((t) => t.enabled).length;
    } catch {
      // Non-critical
    }

    // Check mood patterns from sentiment engine
    const moodLog = sentimentEngine.getMoodLog();
    const recentMoods = moodLog.slice(-8);
    const stressMoods = recentMoods.filter(
      (m) => m.mood === 'frustrated' || m.mood === 'stressed'
    );
    const sustainedStress = stressMoods.length >= 4; // half or more of last 8 entries
    const lastMood = recentMoods[recentMoods.length - 1];
    const recentMoodPositive = lastMood
      ? lastMood.mood === 'excited' || lastMood.mood === 'positive'
      : false;

    return {
      hour: now.getHours(),
      dayOfWeek: now.getDay(),
      activeWindow,
      idleSec: Math.floor((Date.now() - this.lastInteractionTime) / 1000),
      isFirstSessionToday,
      pendingTaskCount,
      sustainedStress,
      recentMoodPositive,
    };
  }

  private evaluate(ctx: ContextSnapshot): PredictionSuggestion | null {
    // --- Morning briefing (first session, morning hours) — warm, personal, like waking up with someone ---
    if (ctx.isFirstSessionToday && ctx.hour >= 6 && ctx.hour <= 11) {
      this.todaySessionFlag = new Date().toISOString().slice(0, 10);
      this.saveFlag();

      const greetings = [
        'Hey, good morning.',
        'Morning!',
        'Hey there. New day.',
      ];
      const parts: string[] = [greetings[Math.floor(Math.random() * greetings.length)]];

      // Pull a relevant memory observation — weave it in naturally
      const mediumTerm = memoryManager.getMediumTerm();
      const topPattern = mediumTerm.sort((a, b) => b.occurrences - a.occurrences)[0];

      if (ctx.pendingTaskCount > 0 && topPattern) {
        parts.push(`You\'ve got ${ctx.pendingTaskCount} thing${ctx.pendingTaskCount > 1 ? 's' : ''} on the schedule today. Also — ${topPattern.observation.toLowerCase()}.`);
      } else if (ctx.pendingTaskCount > 0) {
        parts.push(`You\'ve got ${ctx.pendingTaskCount} thing${ctx.pendingTaskCount > 1 ? 's' : ''} queued up today.`);
      } else if (topPattern) {
        parts.push(`Something I was thinking about — ${topPattern.observation.toLowerCase()}.`);
      } else {
        parts.push('Blank slate today. What are you feeling?');
      }

      return {
        type: 'morning_briefing',
        message: parts.join(' '),
        confidence: 0.8,
      };
    }

    // Mark today's session even if not morning
    if (ctx.isFirstSessionToday) {
      this.todaySessionFlag = new Date().toISOString().slice(0, 10);
      this.saveFlag();
    }

    // --- App context (VS Code detected) ---
    const win = ctx.activeWindow.toLowerCase();
    if (
      win.includes('visual studio code') ||
      win.includes('code -') ||
      win.includes('vscode')
    ) {
      // Only suggest if this is a new window transition
      if (!this.lastActiveWindow.toLowerCase().includes('code')) {
        this.lastActiveWindow = ctx.activeWindow;

        // Extract project name from title (VS Code: "filename - ProjectName - Visual Studio Code")
        const titleParts = ctx.activeWindow.split(' - ');
        const projectName = titleParts.length >= 2 ? titleParts[titleParts.length - 2].trim() : '';

        return {
          type: 'app_context',
          message: projectName
            ? `I see you've opened ${projectName} in VS Code. Want to pick up where we left off, or need any help?`
            : `Looks like you're diving into some code. Shall I help with anything?`,
          confidence: 0.55,
        };
      }
    }

    this.lastActiveWindow = ctx.activeWindow;

    // --- Task reminder (upcoming tasks in the next hour) ---
    if (ctx.pendingTaskCount > 0 && ctx.idleSec > 60) {
      try {
        const tasks = taskScheduler.listTasks();
        const soonTasks = tasks.filter((t) => {
          if (t.type === 'once' && t.triggerTime) {
            const diff = t.triggerTime - Date.now();
            return diff > 0 && diff < 60 * 60_000; // Within the next hour
          }
          return false;
        });

        if (soonTasks.length > 0) {
          const next = soonTasks[0];
          const mins = Math.round((next.triggerTime! - Date.now()) / 60_000);
          return {
            type: 'task_reminder',
            message: `Just a heads-up — you have a reminder in about ${mins} minute${mins !== 1 ? 's' : ''}: "${next.description}".`,
            confidence: 0.6,
          };
        }
      } catch {
        // Non-critical
      }
    }

    // --- Emotional check-in (sustained stress/frustration detected) ---
    if (ctx.sustainedStress && ctx.idleSec > 30) {
      const checkins = [
        'Hey — things have been pretty intense lately. How are you actually doing?',
        'I\'ve noticed things have been stressful. Just checking in — genuinely.',
        'You\'ve been pushing through a lot. How are you holding up?',
      ];
      return {
        type: 'emotional_checkin',
        message: checkins[Math.floor(Math.random() * checkins.length)],
        confidence: 0.65,
      };
    }

    // --- Celebration (positive mood shift, especially after struggle) ---
    if (ctx.recentMoodPositive && ctx.idleSec > 15 && ctx.idleSec < 60) {
      const celebrations = [
        'That felt like a win. Nice.',
        'There\'s a shift in your energy. Something clicked, didn\'t it?',
        'You sound good right now. I like it.',
      ];
      return {
        type: 'celebration',
        message: celebrations[Math.floor(Math.random() * celebrations.length)],
        confidence: 0.5,
      };
    }

    // --- Idle reflection (2+ minutes, share a thought from memory) ---
    if (ctx.idleSec > 120 && ctx.idleSec < 300) {
      // Try to share something interesting from medium-term patterns
      const mediumTerm = memoryManager.getMediumTerm();
      const patterns = mediumTerm.filter((p) => p.occurrences >= 2);
      const randomPattern = patterns.length > 0
        ? patterns[Math.floor(Math.random() * patterns.length)]
        : null;

      if (randomPattern) {
        return {
          type: 'reflection',
          message: `I was just thinking about something — ${randomPattern.observation.toLowerCase()}. Funny how patterns emerge when you pay attention.`,
          confidence: 0.5,
        };
      }

      // Fallback: gentle task nudge if there are pending tasks
      if (ctx.pendingTaskCount > 0) {
        return {
          type: 'idle_prompt',
          message: `You\'ve got ${ctx.pendingTaskCount} thing${ctx.pendingTaskCount > 1 ? 's' : ''} queued up whenever you\'re ready. No rush.`,
          confidence: 0.45,
        };
      }
    }

    return null;
  }
}

export const predictor = new Predictor();
