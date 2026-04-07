/**
 * cost-tracker.ts — Per-turn and session cost tracking for Agent Friday.
 *
 * Tracks token usage and USD cost per LLM call, aggregated by session,
 * provider, and model. Critical for demonstrating zero-cost local inference
 * via Ollama/Gemma 4 vs. cloud provider costs.
 *
 * Persists daily/monthly aggregates to disk for historical reporting.
 */

import { app } from 'electron';
import * as fs from 'fs/promises';
import * as path from 'path';

// ── Types ─────────────────────────────────────────────────────────────

export interface CostEntry {
  id: string;
  timestamp: number;
  model: string;
  provider: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  latencyMs: number;
  taskHint?: string;
}

export interface SessionCost {
  sessionId: string;
  startTime: number;
  entries: CostEntry[];
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCostUsd: number;
  totalLatencyMs: number;
  /** Cost breakdown by provider */
  byProvider: Record<string, { tokens: number; costUsd: number; calls: number }>;
  /** Cost breakdown by model */
  byModel: Record<string, { tokens: number; costUsd: number; calls: number }>;
}

export interface DailyAggregate {
  date: string; // YYYY-MM-DD
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCostUsd: number;
  callCount: number;
  byProvider: Record<string, { tokens: number; costUsd: number; calls: number }>;
}

// ── Model Pricing (fallback if router unavailable) ────────────────────

const MODEL_PRICING: Record<string, { input: number; output: number }> = {
  // Cloud models (per million tokens)
  'claude-opus-4-6': { input: 15, output: 75 },
  'claude-sonnet-4-20250514': { input: 3, output: 15 },
  'claude-haiku-4-5-20251001': { input: 0.8, output: 4 },
  'gpt-4o': { input: 2.5, output: 10 },
  'gpt-4o-mini': { input: 0.15, output: 0.6 },
  'gemini-2.5-pro': { input: 1.25, output: 10 },
  'gemini-2.5-flash': { input: 0.15, output: 0.6 },
  // Local models (zero cost)
  'gemma4': { input: 0, output: 0 },
  'gemma4:e2b': { input: 0, output: 0 },
  'gemma4:e4b': { input: 0, output: 0 },
  'gemma4:26b': { input: 0, output: 0 },
  'gemma4:31b': { input: 0, output: 0 },
  'llama3.2': { input: 0, output: 0 },
};

// ── Cost Tracker Class ────────────────────────────────────────────────

class CostTracker {
  private currentSession: SessionCost;
  private dailyAggregates: Map<string, DailyAggregate> = new Map();
  private persistDir = '';
  private dirty = false;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners: Array<(entry: CostEntry, session: SessionCost) => void> = [];

  constructor() {
    this.currentSession = this.createSession();
  }

  async initialize(): Promise<void> {
    this.persistDir = path.join(app.getPath('userData'), 'memory', 'cost-tracking');
    await fs.mkdir(this.persistDir, { recursive: true });
    await this.loadDailyAggregates();
  }

  /** Record a completed LLM call */
  record(params: {
    model: string;
    provider: string;
    inputTokens: number;
    outputTokens: number;
    latencyMs: number;
    taskHint?: string;
  }): CostEntry {
    const costUsd = this.calculateCost(
      params.model,
      params.inputTokens,
      params.outputTokens
    );

    const entry: CostEntry = {
      id: crypto.randomUUID(),
      timestamp: Date.now(),
      model: params.model,
      provider: params.provider,
      inputTokens: params.inputTokens,
      outputTokens: params.outputTokens,
      costUsd,
      latencyMs: params.latencyMs,
      taskHint: params.taskHint,
    };

    // Update session
    this.currentSession.entries.push(entry);
    this.currentSession.totalInputTokens += entry.inputTokens;
    this.currentSession.totalOutputTokens += entry.outputTokens;
    this.currentSession.totalCostUsd += entry.costUsd;
    this.currentSession.totalLatencyMs += entry.latencyMs;

    // Update provider breakdown
    const provKey = entry.provider;
    if (!this.currentSession.byProvider[provKey]) {
      this.currentSession.byProvider[provKey] = { tokens: 0, costUsd: 0, calls: 0 };
    }
    this.currentSession.byProvider[provKey].tokens += entry.inputTokens + entry.outputTokens;
    this.currentSession.byProvider[provKey].costUsd += entry.costUsd;
    this.currentSession.byProvider[provKey].calls++;

    // Update model breakdown
    const modelKey = entry.model;
    if (!this.currentSession.byModel[modelKey]) {
      this.currentSession.byModel[modelKey] = { tokens: 0, costUsd: 0, calls: 0 };
    }
    this.currentSession.byModel[modelKey].tokens += entry.inputTokens + entry.outputTokens;
    this.currentSession.byModel[modelKey].costUsd += entry.costUsd;
    this.currentSession.byModel[modelKey].calls++;

    // Update daily aggregate
    this.updateDailyAggregate(entry);

    // Notify listeners
    for (const listener of this.listeners) {
      try { listener(entry, this.currentSession); } catch {}
    }

    // Schedule persist
    this.scheduleSave();

    return entry;
  }

  /** Calculate USD cost for a request */
  calculateCost(model: string, inputTokens: number, outputTokens: number): number {
    // Check exact match first, then partial match
    let pricing = MODEL_PRICING[model];
    if (!pricing) {
      const lowerModel = model.toLowerCase();
      for (const [key, value] of Object.entries(MODEL_PRICING)) {
        if (lowerModel.includes(key.toLowerCase())) {
          pricing = value;
          break;
        }
      }
    }
    if (!pricing) {
      // Unknown model — assume moderate cloud pricing as conservative estimate
      pricing = { input: 3, output: 15 };
    }

    return (inputTokens * pricing.input + outputTokens * pricing.output) / 1_000_000;
  }

  /** Get current session stats */
  getSession(): SessionCost {
    return { ...this.currentSession };
  }

  /** Get cost savings: how much cloud would have cost vs. what local actually cost */
  getSavings(): { localCost: number; cloudEquivalent: number; savedUsd: number } {
    let localCost = 0;
    let cloudEquivalent = 0;

    for (const entry of this.currentSession.entries) {
      localCost += entry.costUsd;
      // Estimate what Claude Sonnet would have cost for the same tokens
      const cloudCost = (entry.inputTokens * 3 + entry.outputTokens * 15) / 1_000_000;
      cloudEquivalent += cloudCost;
    }

    return {
      localCost,
      cloudEquivalent,
      savedUsd: cloudEquivalent - localCost,
    };
  }

  /** Get daily aggregates for a date range */
  getDailyStats(days = 30): DailyAggregate[] {
    const result: DailyAggregate[] = [];
    const now = new Date();
    for (let i = 0; i < days; i++) {
      const date = new Date(now);
      date.setDate(date.getDate() - i);
      const key = date.toISOString().slice(0, 10);
      const agg = this.dailyAggregates.get(key);
      if (agg) result.push(agg);
    }
    return result.reverse();
  }

  /** Get monthly total spend */
  getMonthlySpend(): number {
    const now = new Date();
    const monthPrefix = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    let total = 0;
    for (const [key, agg] of this.dailyAggregates) {
      if (key.startsWith(monthPrefix)) {
        total += agg.totalCostUsd;
      }
    }
    return total;
  }

  /** Subscribe to cost events (for real-time UI updates) */
  onChange(listener: (entry: CostEntry, session: SessionCost) => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  /** Start a new session (e.g., on app restart or explicit reset) */
  resetSession(): void {
    this.currentSession = this.createSession();
  }

  /** Flush pending writes (call on app quit) */
  async flush(): Promise<void> {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    if (this.dirty) {
      await this.saveDailyAggregates();
    }
  }

  // ── Private ─────────────────────────────────────────────────────────

  private createSession(): SessionCost {
    return {
      sessionId: crypto.randomUUID(),
      startTime: Date.now(),
      entries: [],
      totalInputTokens: 0,
      totalOutputTokens: 0,
      totalCostUsd: 0,
      totalLatencyMs: 0,
      byProvider: {},
      byModel: {},
    };
  }

  private updateDailyAggregate(entry: CostEntry): void {
    const dateKey = new Date(entry.timestamp).toISOString().slice(0, 10);
    let agg = this.dailyAggregates.get(dateKey);
    if (!agg) {
      agg = {
        date: dateKey,
        totalInputTokens: 0,
        totalOutputTokens: 0,
        totalCostUsd: 0,
        callCount: 0,
        byProvider: {},
      };
      this.dailyAggregates.set(dateKey, agg);
    }

    agg.totalInputTokens += entry.inputTokens;
    agg.totalOutputTokens += entry.outputTokens;
    agg.totalCostUsd += entry.costUsd;
    agg.callCount++;

    if (!agg.byProvider[entry.provider]) {
      agg.byProvider[entry.provider] = { tokens: 0, costUsd: 0, calls: 0 };
    }
    agg.byProvider[entry.provider].tokens += entry.inputTokens + entry.outputTokens;
    agg.byProvider[entry.provider].costUsd += entry.costUsd;
    agg.byProvider[entry.provider].calls++;

    this.dirty = true;
  }

  private scheduleSave(): void {
    this.dirty = true;
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(async () => {
      this.saveTimer = null;
      await this.saveDailyAggregates();
    }, 5000);
  }

  private async saveDailyAggregates(): Promise<void> {
    if (!this.persistDir) return;
    try {
      const data = Object.fromEntries(this.dailyAggregates);
      const filePath = path.join(this.persistDir, 'daily-aggregates.json');
      await fs.writeFile(filePath, JSON.stringify(data, null, 2), 'utf-8');
      this.dirty = false;
    } catch (err) {
      console.warn('[CostTracker] Failed to save daily aggregates:', err);
    }
  }

  private async loadDailyAggregates(): Promise<void> {
    if (!this.persistDir) return;
    try {
      const filePath = path.join(this.persistDir, 'daily-aggregates.json');
      const raw = await fs.readFile(filePath, 'utf-8');
      const parsed = JSON.parse(raw);
      for (const [key, value] of Object.entries(parsed)) {
        this.dailyAggregates.set(key, value as DailyAggregate);
      }
    } catch {
      // File doesn't exist yet — start fresh
    }
  }
}

/** Singleton cost tracker */
export const costTracker = new CostTracker();
