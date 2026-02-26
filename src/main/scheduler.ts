/**
 * scheduler.ts — Persistent task scheduler with cron support.
 * Tasks can be one-time or recurring, and fire via IPC to the renderer
 * where EVE speaks them naturally.
 */

import { app, BrowserWindow, Notification } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { intelligenceEngine } from './intelligence';

export interface ScheduledTask {
  id: string;
  description: string;
  type: 'once' | 'recurring';
  triggerTime?: number;      // Unix ms for one-time tasks
  cronPattern?: string;      // "minute hour dayOfMonth month dayOfWeek" for recurring
  action: 'remind' | 'launch_app' | 'run_command' | 'research' | 'gateway_message';
  payload: string;
  enabled: boolean;
  createdAt: number;
  lastTriggered?: number;
}

// Gemini tool declarations for task scheduling
export const SCHEDULER_TOOL_DECLARATIONS = [
  {
    name: 'create_task',
    description:
      'Create a scheduled task or reminder. For one-time tasks, set trigger_time as Unix timestamp in milliseconds. For recurring tasks, set cron_pattern (minute hour dayOfMonth month dayOfWeek). Examples: "remind me in 30 minutes" → once with trigger_time, "every weekday at 9am" → recurring with cron "0 9 * * 1-5".',
    parameters: {
      type: 'object',
      properties: {
        description: {
          type: 'string',
          description: 'What to remind or do (e.g. "stretch break", "check email").',
        },
        type: {
          type: 'string',
          description: '"once" for one-time, "recurring" for repeating tasks.',
        },
        trigger_time: {
          type: 'number',
          description: 'Unix timestamp in milliseconds for one-time tasks. Calculate from current time.',
        },
        cron_pattern: {
          type: 'string',
          description: 'Cron pattern for recurring tasks: "minute hour dayOfMonth month dayOfWeek". Use * for any. Days: 0=Sun, 1=Mon...6=Sat.',
        },
        action: {
          type: 'string',
          description: '"remind" (speak reminder), "launch_app" (open an app), "run_command" (execute PowerShell), "research" (run background research), or "gateway_message" (send via messaging gateway).',
        },
        payload: {
          type: 'string',
          description: 'For remind: the reminder text. For launch_app: app name. For run_command: the command. For gateway_message: JSON with { channel, recipientId, text }.',
        },
      },
      required: ['description', 'type', 'action', 'payload'],
    },
  },
  {
    name: 'list_tasks',
    description: 'List all scheduled tasks and reminders.',
    parameters: { type: 'object', properties: {} },
  },
  {
    name: 'delete_task',
    description: 'Delete a scheduled task by its ID.',
    parameters: {
      type: 'object',
      properties: {
        task_id: {
          type: 'string',
          description: 'The ID of the task to delete.',
        },
      },
      required: ['task_id'],
    },
  },
];

class TaskScheduler {
  private tasks: ScheduledTask[] = [];
  private filePath: string = '';
  private checkInterval: ReturnType<typeof setInterval> | null = null;
  private mainWindow: BrowserWindow | null = null;

  async initialize(win: BrowserWindow): Promise<void> {
    this.mainWindow = win;
    this.filePath = path.join(app.getPath('userData'), 'scheduled-tasks.json');

    try {
      const data = await fs.readFile(this.filePath, 'utf-8');
      this.tasks = JSON.parse(data);
    } catch {
      this.tasks = [];
    }

    // Check every 30 seconds for tasks to fire
    this.checkInterval = setInterval(() => this.checkTasks(), 30000);
    console.log(`[Scheduler] Initialized with ${this.tasks.length} tasks`);
  }

  stop(): void {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
  }

  async createTask(params: {
    description: string;
    type: string;
    trigger_time?: number;
    cron_pattern?: string;
    action: string;
    payload: string;
  }): Promise<ScheduledTask> {
    const task: ScheduledTask = {
      id: crypto.randomUUID().slice(0, 8),
      description: params.description,
      type: params.type === 'recurring' ? 'recurring' : 'once',
      triggerTime: params.trigger_time,
      cronPattern: params.cron_pattern,
      action: (['remind', 'launch_app', 'run_command', 'research', 'gateway_message'].includes(params.action)
        ? params.action
        : 'remind') as ScheduledTask['action'],
      payload: params.payload,
      enabled: true,
      createdAt: Date.now(),
    };

    this.tasks.push(task);
    await this.save();
    console.log(`[Scheduler] Created task: ${task.description} (${task.id})`);
    return task;
  }

  listTasks(): ScheduledTask[] {
    return this.tasks.filter((t) => t.enabled);
  }

  async deleteTask(id: string): Promise<boolean> {
    const before = this.tasks.length;
    this.tasks = this.tasks.filter((t) => t.id !== id);
    if (this.tasks.length < before) {
      await this.save();
      return true;
    }
    return false;
  }

  private checkTasks(): void {
    const now = Date.now();
    const nowDate = new Date(now);

    for (const task of this.tasks) {
      if (!task.enabled) continue;

      let shouldFire = false;

      if (task.type === 'once' && task.triggerTime) {
        shouldFire = now >= task.triggerTime && !task.lastTriggered;
      } else if (task.type === 'recurring' && task.cronPattern) {
        shouldFire = this.matchesCron(task.cronPattern, nowDate);
        // Don't re-fire within the same minute
        if (shouldFire && task.lastTriggered) {
          const lastFired = new Date(task.lastTriggered);
          if (
            lastFired.getMinutes() === nowDate.getMinutes() &&
            lastFired.getHours() === nowDate.getHours() &&
            lastFired.getDate() === nowDate.getDate()
          ) {
            shouldFire = false;
          }
        }
      }

      if (shouldFire) {
        this.fireTask(task);
        task.lastTriggered = now;

        // Disable one-time tasks after firing
        if (task.type === 'once') {
          task.enabled = false;
        }

        this.save().catch(() => {});
      }
    }
  }

  private fireTask(task: ScheduledTask): void {
    console.log(`[Scheduler] Firing task: ${task.description}`);

    // Research tasks run silently in the background
    if (task.action === 'research') {
      intelligenceEngine.runResearch(task.payload).catch((err) => {
        console.warn(`[Scheduler] Research task failed: ${task.description}`, err);
      });
      return;
    }

    // Gateway message tasks send through the messaging gateway
    if (task.action === 'gateway_message') {
      try {
        const { channel, recipientId, text } = JSON.parse(task.payload);
        if (channel && recipientId && text) {
          // Lazy import to avoid circular dependency at module load time
          const { gatewayManager } = require('./gateway/gateway-manager');
          gatewayManager.sendProactiveMessage(channel, recipientId, text).catch((err: any) => {
            console.warn(`[Scheduler] Gateway message failed: ${task.description}`, err?.message);
          });
        } else {
          console.warn(`[Scheduler] Gateway message missing fields: ${task.payload}`);
        }
      } catch (err) {
        console.warn(`[Scheduler] Invalid gateway_message payload: ${task.payload}`);
      }
      return;
    }

    // System notification for non-research tasks
    if (Notification.isSupported()) {
      new Notification({
        title: 'EVE Reminder',
        body: task.description,
      }).show();
    }

    // Send to renderer so EVE speaks it
    this.mainWindow?.webContents.send('scheduler:task-fired', {
      id: task.id,
      description: task.description,
      action: task.action,
      payload: task.payload,
    });
  }

  /** Simple cron matcher: "minute hour dayOfMonth month dayOfWeek" */
  private matchesCron(pattern: string, date: Date): boolean {
    const parts = pattern.trim().split(/\s+/);
    if (parts.length !== 5) return false;

    const [minP, hourP, domP, monP, dowP] = parts;
    const minute = date.getMinutes();
    const hour = date.getHours();
    const dayOfMonth = date.getDate();
    const month = date.getMonth() + 1; // 1-12
    const dayOfWeek = date.getDay(); // 0=Sun

    return (
      this.matchField(minP, minute) &&
      this.matchField(hourP, hour) &&
      this.matchField(domP, dayOfMonth) &&
      this.matchField(monP, month) &&
      this.matchField(dowP, dayOfWeek)
    );
  }

  /** Match a single cron field: *, number, comma-separated, or range (e.g. 1-5) */
  private matchField(pattern: string, value: number): boolean {
    if (pattern === '*') return true;

    // Handle comma-separated values
    const parts = pattern.split(',');
    for (const part of parts) {
      // Handle range (e.g., 1-5)
      if (part.includes('-')) {
        const [start, end] = part.split('-').map(Number);
        if (value >= start && value <= end) return true;
      } else if (part.includes('/')) {
        // Handle step (e.g., */15)
        const [, step] = part.split('/');
        if (value % Number(step) === 0) return true;
      } else {
        if (Number(part) === value) return true;
      }
    }

    return false;
  }

  private async save(): Promise<void> {
    await fs.writeFile(this.filePath, JSON.stringify(this.tasks, null, 2), 'utf-8');
  }
}

export const taskScheduler = new TaskScheduler();
