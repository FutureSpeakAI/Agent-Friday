/**
 * telemetry.ts — Lightweight, privacy-first local telemetry engine.
 * All data stays on disk. Nothing is sent externally.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';

export type TelemetryCategory = 'app-launch' | 'voice-path' | 'voice-fallback' | 'voice-transition' | 'renderer-error';

export interface TelemetryEvent {
  category: TelemetryCategory;
  action: string;
  label?: string;
  value?: number;
  timestamp: number;
}

interface AggregateCounter {
  count: number;
  firstSeen: number;
  lastSeen: number;
}

interface TelemetryData {
  events: TelemetryEvent[];
  aggregates: Record<string, AggregateCounter>;
}

const MAX_EVENTS = 1000;
const TELEMETRY_FILE = 'telemetry.json';

class TelemetryEngine {
  private data: TelemetryData = { events: [], aggregates: {} };
  private filePath = '';
  private dirty = false;
  private flushTimer: ReturnType<typeof setInterval> | null = null;

  async initialize(): Promise<void> {
    this.filePath = path.join(app.getPath('userData'), TELEMETRY_FILE);
    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      this.data = JSON.parse(raw);
      if (this.data.events.length > MAX_EVENTS) {
        this.data.events = this.data.events.slice(-MAX_EVENTS);
      }
      console.log(`[Telemetry] Loaded ${this.data.events.length} events`);
    } catch {
      // First run — empty data
    }
    this.flushTimer = setInterval(() => this.flush(), 30_000);
  }

  /** Record a telemetry event. Aggregates are updated automatically. */
  record(category: TelemetryCategory, action: string, label?: string, value?: number): void {
    const event: TelemetryEvent = {
      category,
      action,
      label,
      value,
      timestamp: Date.now(),
    };

    this.data.events.push(event);
    if (this.data.events.length > MAX_EVENTS) {
      this.data.events = this.data.events.slice(-MAX_EVENTS);
    }

    const key = `${category}:${action}${label ? ':' + label : ''}`;
    const existing = this.data.aggregates[key];
    if (existing) {
      existing.count++;
      existing.lastSeen = event.timestamp;
    } else {
      this.data.aggregates[key] = {
        count: 1,
        firstSeen: event.timestamp,
        lastSeen: event.timestamp,
      };
    }

    this.dirty = true;
  }

  /** Get aggregate counts, optionally filtered by category. */
  getAggregates(category?: TelemetryCategory): Record<string, AggregateCounter> {
    if (!category) return { ...this.data.aggregates };
    const filtered: Record<string, AggregateCounter> = {};
    for (const [key, val] of Object.entries(this.data.aggregates)) {
      if (key.startsWith(category + ':')) {
        filtered[key] = val;
      }
    }
    return filtered;
  }

  /** Get recent events, optionally filtered by category. */
  getRecentEvents(count = 50, category?: TelemetryCategory): TelemetryEvent[] {
    const events = category
      ? this.data.events.filter((e) => e.category === category)
      : this.data.events;
    return events.slice(-count);
  }

  /** Privacy wipe — clear all telemetry data. */
  async clear(): Promise<void> {
    this.data = { events: [], aggregates: {} };
    this.dirty = true;
    await this.flush();
  }

  async flush(): Promise<void> {
    if (!this.dirty || !this.filePath) return;
    try {
      await fs.writeFile(this.filePath, JSON.stringify(this.data, null, 2), 'utf-8');
      this.dirty = false;
    } catch {
      // Non-critical — data persists in memory
    }
  }

  async shutdown(): Promise<void> {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
    await this.flush();
  }
}

export const telemetryEngine = new TelemetryEngine();
