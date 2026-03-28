/**
 * results-ledger.ts — Structured experiment logging for Agent Friday's iteration engine.
 *
 * Inspired by autoresearch's results.tsv: every iteration cycle is logged with
 * its run tag, metric value, changes made, duration, and outcome. The ledger
 * supports both TSV export (for quick terminal inspection) and JSON (for dashboards).
 *
 * Each directive gets its own ledger file in userData/autoresearch/
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';

// ── Types ───────────────────────────────────────────────────────────

export interface LedgerEntry {
  /** ISO timestamp */
  timestamp: string;
  /** Run tag (directive name + cycle number) */
  runTag: string;
  /** Cycle number within this session */
  cycle: number;
  /** Primary metric value (NaN if measurement failed) */
  metricValue: number;
  /** Previous metric value for comparison */
  previousMetric: number;
  /** Delta (improvement is negative if lowerIsBetter) */
  delta: number;
  /** Whether this iteration was kept or discarded */
  outcome: 'kept' | 'discarded' | 'error' | 'halted';
  /** Brief description of changes made */
  changes: string;
  /** Wall-clock duration in milliseconds */
  durationMs: number;
  /** Git commit hash if committed */
  commitHash?: string;
  /** Error message if failed */
  error?: string;
}

export interface LedgerSummary {
  /** Directive title */
  directive: string;
  /** Total cycles run */
  totalCycles: number;
  /** Cycles that improved the metric */
  improvements: number;
  /** Cycles that were discarded */
  discards: number;
  /** Cycles that errored */
  errors: number;
  /** Best metric value achieved */
  bestMetric: number;
  /** Starting metric value */
  startMetric: number;
  /** Total improvement */
  totalImprovement: number;
  /** Total wall-clock time */
  totalDurationMs: number;
  /** All entries */
  entries: LedgerEntry[];
}

// ── ResultsLedger ───────────────────────────────────────────────────

class ResultsLedger {
  private basePath: string = '';
  private entries: Map<string, LedgerEntry[]> = new Map();

  initialize(): void {
    this.basePath = path.join(app.getPath('userData'), 'autoresearch');
    fs.mkdir(this.basePath, { recursive: true }).catch(() => {});
    console.log('[ResultsLedger] Initialized at', this.basePath);
  }

  /**
   * Record an iteration result.
   */
  async record(directive: string, entry: LedgerEntry): Promise<void> {
    // In-memory
    if (!this.entries.has(directive)) {
      this.entries.set(directive, []);
    }
    this.entries.get(directive)!.push(entry);

    // Persist as append-only TSV
    const tsvPath = this.getTsvPath(directive);
    const tsvLine = [
      entry.timestamp,
      entry.runTag,
      entry.cycle,
      isNaN(entry.metricValue) ? 'NaN' : entry.metricValue.toFixed(6),
      isNaN(entry.delta) ? 'NaN' : entry.delta.toFixed(6),
      entry.outcome,
      entry.durationMs,
      entry.commitHash || '-',
      entry.changes.replace(/\t/g, ' ').replace(/\n/g, ' '),
      entry.error?.replace(/\t/g, ' ').replace(/\n/g, ' ') || '-',
    ].join('\t');

    try {
      // Create header if file doesn't exist
      try {
        await fs.access(tsvPath);
      } catch {
        const header = 'timestamp\trunTag\tcycle\tmetric\tdelta\toutcome\tdurationMs\tcommit\tchanges\terror\n';
        await fs.writeFile(tsvPath, header, 'utf-8');
      }
      await fs.appendFile(tsvPath, tsvLine + '\n', 'utf-8');
    } catch (err) {
      console.warn('[ResultsLedger] Failed to persist entry:', err instanceof Error ? err.message : err);
    }

    // Also persist full JSON for dashboard
    await this.persistJson(directive);
  }

  /**
   * Get a summary of all runs for a directive.
   */
  getSummary(directive: string): LedgerSummary {
    const entries = this.entries.get(directive) || [];
    const improvements = entries.filter((e) => e.outcome === 'kept').length;
    const discards = entries.filter((e) => e.outcome === 'discarded').length;
    const errors = entries.filter((e) => e.outcome === 'error').length;
    const metrics = entries.map((e) => e.metricValue).filter((v) => !isNaN(v));

    return {
      directive,
      totalCycles: entries.length,
      improvements,
      discards,
      errors,
      bestMetric: metrics.length > 0 ? Math.min(...metrics) : NaN,
      startMetric: metrics.length > 0 ? metrics[0] : NaN,
      totalImprovement: metrics.length >= 2 ? metrics[0] - Math.min(...metrics) : 0,
      totalDurationMs: entries.reduce((sum, e) => sum + e.durationMs, 0),
      entries,
    };
  }

  /**
   * Get all entries for a directive.
   */
  getEntries(directive: string): LedgerEntry[] {
    return this.entries.get(directive) || [];
  }

  /**
   * List all directives that have ledger data.
   */
  listDirectives(): string[] {
    return [...this.entries.keys()];
  }

  /**
   * Export ledger as TSV string (for terminal display).
   */
  toTsv(directive: string): string {
    const entries = this.entries.get(directive) || [];
    const header = 'cycle\tmetric\tdelta\toutcome\tduration\tchanges';
    const rows = entries.map((e) =>
      [
        e.cycle,
        isNaN(e.metricValue) ? 'NaN' : e.metricValue.toFixed(4),
        isNaN(e.delta) ? '-' : (e.delta > 0 ? '+' : '') + e.delta.toFixed(4),
        e.outcome,
        `${Math.round(e.durationMs / 1000)}s`,
        e.changes.slice(0, 60),
      ].join('\t')
    );
    return [header, ...rows].join('\n');
  }

  // ── Private ─────────────────────────────────────────────────────

  private getTsvPath(directive: string): string {
    const safe = directive.replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
    return path.join(this.basePath, `${safe}.tsv`);
  }

  private async persistJson(directive: string): Promise<void> {
    const safe = directive.replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
    const jsonPath = path.join(this.basePath, `${safe}.json`);
    try {
      await fs.writeFile(jsonPath, JSON.stringify(this.getSummary(directive), null, 2), 'utf-8');
    } catch {
      // Non-critical — TSV is the primary log
    }
  }
}

export const resultsLedger = new ResultsLedger();
