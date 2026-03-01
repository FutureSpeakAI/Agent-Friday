/**
 * Track XI, Phase 7 — Symbiont Protocol
 *
 * Self-improving agent performance system that provides:
 *   - Execution metrics capture (duration, success rate, quality)
 *   - Historical performance profiles per agent type
 *   - Routing score enhancement for capability-based selection
 *   - Anomaly detection (underperforming agents, latency spikes)
 *   - Self-healing triggers (disable failing agents, log corrective actions)
 *   - Orchestrator prompt enrichment with live performance data
 *   - Feedback loops: task outcomes refine future routing decisions
 *
 * The Symbiont sits alongside the Capability Map and Awareness Mesh,
 * forming the intelligence layer of Track XI's agent coordination stack:
 *
 *   Awareness Mesh  → WHO is doing WHAT right now
 *   Capability Map   → WHO CAN do what (static + dynamic)
 *   Symbiont Protocol → WHO SHOULD do what (learned from history)
 *
 * cLaw compliance:
 *   First Law:  Performance data never bypasses trust-tier restrictions
 *   Second Law: Self-healing actions respect delegation depth limits
 *   Third Law:  All queries are synchronous and complete ≤500ms
 */

import type { TrustTier } from './delegation-engine';

/* ── Types ──────────────────────────────────────────────────────────── */

export interface ExecutionRecord {
  id: string;
  agentType: string;
  taskId: string;
  /** Terminal status */
  outcome: 'completed' | 'failed' | 'cancelled';
  /** Execution duration in milliseconds */
  durationMs: number;
  /** Whether an error was thrown */
  hadError: boolean;
  /** Error message (if failed) */
  errorMessage?: string;
  /** Agent role in the execution */
  role: string;
  /** Team context (if team member) */
  teamId?: string;
  /** Trust tier at execution time */
  trustTier: TrustTier;
  /** Timestamp of completion */
  completedAt: number;
}

export interface PerformanceProfile {
  agentType: string;
  /** Total number of recorded executions */
  totalExecutions: number;
  /** Successful completions / total executions (0-1) */
  successRate: number;
  /** Failure count */
  failures: number;
  /** Cancellation count */
  cancellations: number;
  /** Median execution time in ms */
  p50LatencyMs: number;
  /** 95th percentile execution time in ms */
  p95LatencyMs: number;
  /** Average execution time in ms */
  avgLatencyMs: number;
  /** Most recent error messages (last 5) */
  recentErrors: string[];
  /** Health status derived from metrics */
  health: AgentHealth;
  /** Last execution timestamp */
  lastExecutedAt: number;
  /** Trend direction over last 10 executions */
  trend: 'improving' | 'stable' | 'degrading';
}

export type AgentHealth = 'healthy' | 'degraded' | 'critical' | 'unknown';

export interface AnomalyReport {
  agentType: string;
  anomalyType: AnomalyType;
  severity: 'warning' | 'critical';
  description: string;
  detectedAt: number;
  /** Suggested corrective action */
  suggestedAction: CorrectionAction;
}

export type AnomalyType =
  | 'high-failure-rate'
  | 'latency-spike'
  | 'consecutive-failures'
  | 'no-recent-success';

export type CorrectionAction =
  | 'monitor'       // Keep watching, no action yet
  | 'log-warning'   // Log for operator review
  | 'disable-agent' // Disable in capability map
  | 'reduce-score'; // Reduce routing preference

export interface SymbiontConfig {
  /** Max execution records to retain per agent type (default: 100) */
  maxRecordsPerAgent: number;
  /** Failure rate threshold for 'degraded' health (default: 0.3) */
  degradedThreshold: number;
  /** Failure rate threshold for 'critical' health (default: 0.6) */
  criticalThreshold: number;
  /** Consecutive failures before anomaly trigger (default: 3) */
  consecutiveFailureLimit: number;
  /** Latency multiplier over p50 for spike detection (default: 3.0) */
  latencySpikeMultiplier: number;
  /** Performance score weight in routing (0-1, default: 0.3) */
  performanceWeight: number;
  /** Whether self-healing actions are enabled (default: true) */
  selfHealingEnabled: boolean;
}

export interface SymbiontSnapshot {
  profiles: PerformanceProfile[];
  anomalies: AnomalyReport[];
  totalRecordsStored: number;
  agentTypesTracked: number;
  config: SymbiontConfig;
  timestamp: number;
}

export interface HealthReport {
  overallHealth: AgentHealth;
  agentCount: number;
  healthyCount: number;
  degradedCount: number;
  criticalCount: number;
  unknownCount: number;
  activeAnomalies: AnomalyReport[];
  topPerformers: Array<{ agentType: string; successRate: number }>;
  underperformers: Array<{ agentType: string; successRate: number; health: AgentHealth }>;
}

/* ── Default Config ──────────────────────────────────────────────────── */

const DEFAULT_CONFIG: SymbiontConfig = {
  maxRecordsPerAgent: 100,
  degradedThreshold: 0.3,
  criticalThreshold: 0.6,
  consecutiveFailureLimit: 3,
  latencySpikeMultiplier: 3.0,
  performanceWeight: 0.3,
  selfHealingEnabled: true,
};

/* ── Symbiont Protocol ───────────────────────────────────────────────── */

class SymbiontProtocol {
  private records: Map<string, ExecutionRecord[]> = new Map();
  private anomalies: AnomalyReport[] = [];
  private config: SymbiontConfig = { ...DEFAULT_CONFIG };
  private correctionLog: Array<{ action: CorrectionAction; agentType: string; timestamp: number }> = [];

  /* ── Configuration ─────────────────────────────────────────────────── */

  configure(overrides: Partial<SymbiontConfig>): void {
    this.config = { ...this.config, ...overrides };
  }

  getConfig(): SymbiontConfig {
    return { ...this.config };
  }

  /* ── Execution Recording ───────────────────────────────────────────── */

  /**
   * Record a completed agent execution.
   * Called by agent-runner after task reaches terminal state.
   * Triggers anomaly detection and self-healing if warranted.
   */
  recordExecution(record: ExecutionRecord): void {
    const { agentType } = record;

    if (!this.records.has(agentType)) {
      this.records.set(agentType, []);
    }

    const agentRecords = this.records.get(agentType)!;
    agentRecords.push(record);

    // Cap records per agent type
    if (agentRecords.length > this.config.maxRecordsPerAgent) {
      agentRecords.splice(0, agentRecords.length - this.config.maxRecordsPerAgent);
    }

    // Run anomaly detection on this agent type
    this.detectAnomaliesFor(agentType);
  }

  /* ── Performance Profiles ──────────────────────────────────────────── */

  /**
   * Get the aggregated performance profile for an agent type.
   * Returns null if no executions recorded.
   */
  getProfile(agentType: string): PerformanceProfile | null {
    const records = this.records.get(agentType);
    if (!records || records.length === 0) return null;

    const total = records.length;
    const successes = records.filter((r) => r.outcome === 'completed').length;
    const failures = records.filter((r) => r.outcome === 'failed').length;
    const cancellations = records.filter((r) => r.outcome === 'cancelled').length;
    const successRate = successes / total;

    // Latency calculations (only from completed executions)
    const durations = records
      .filter((r) => r.outcome === 'completed' && r.durationMs > 0)
      .map((r) => r.durationMs)
      .sort((a, b) => a - b);

    const p50 = durations.length > 0 ? this.percentile(durations, 50) : 0;
    const p95 = durations.length > 0 ? this.percentile(durations, 95) : 0;
    const avg = durations.length > 0
      ? durations.reduce((s, d) => s + d, 0) / durations.length
      : 0;

    // Recent errors
    const recentErrors = records
      .filter((r) => r.hadError && r.errorMessage)
      .slice(-5)
      .map((r) => r.errorMessage!);

    // Health assessment
    const health = this.assessHealth(successRate, total);

    // Trend (last 10 executions)
    const trend = this.computeTrend(records);

    // Last execution
    const lastExecutedAt = Math.max(...records.map((r) => r.completedAt));

    return {
      agentType,
      totalExecutions: total,
      successRate,
      failures,
      cancellations,
      p50LatencyMs: Math.round(p50),
      p95LatencyMs: Math.round(p95),
      avgLatencyMs: Math.round(avg),
      recentErrors,
      health,
      lastExecutedAt,
      trend,
    };
  }

  /**
   * Get performance profiles for all tracked agent types.
   */
  getAllProfiles(): PerformanceProfile[] {
    const profiles: PerformanceProfile[] = [];
    for (const agentType of this.records.keys()) {
      const profile = this.getProfile(agentType);
      if (profile) profiles.push(profile);
    }
    return profiles.sort((a, b) => b.totalExecutions - a.totalExecutions);
  }

  /* ── Routing Score Enhancement ─────────────────────────────────────── */

  /**
   * Calculate a performance-based score boost for capability routing.
   * Returns a value between -0.3 and +0.3 that modifies the base
   * capability match score in findCapable().
   *
   * Positive boost: agent has proven track record
   * Negative boost: agent has been underperforming
   * Zero: insufficient data or unknown agent
   */
  getPerformanceBoost(agentType: string): number {
    const profile = this.getProfile(agentType);
    if (!profile || profile.totalExecutions < 3) return 0; // Need minimum data

    const weight = this.config.performanceWeight;

    // Success rate component (0-1)
    const successComponent = profile.successRate;

    // Latency component (0-1, faster = higher)
    // Normalize: <5s = 1.0, >60s = 0.0
    const latencyNorm = Math.max(0, Math.min(1, 1 - (profile.avgLatencyMs - 5000) / 55000));

    // Trend component: improving = +0.1, stable = 0, degrading = -0.1
    const trendBonus = profile.trend === 'improving' ? 0.1
      : profile.trend === 'degrading' ? -0.1
      : 0;

    // Composite: success rate (70%) + latency (20%) + trend (10%)
    const composite = (successComponent * 0.7 + latencyNorm * 0.2 + (0.5 + trendBonus) * 0.1);

    // Scale to [-weight, +weight] range (centered at 0)
    const boost = (composite - 0.5) * 2 * weight;

    return Math.max(-weight, Math.min(weight, boost));
  }

  /* ── Anomaly Detection ─────────────────────────────────────────────── */

  /**
   * Detect anomalies for a specific agent type.
   * Called automatically after each execution record.
   */
  private detectAnomaliesFor(agentType: string): void {
    const records = this.records.get(agentType);
    if (!records || records.length < 3) return; // Need minimum data

    // Clear old anomalies for this agent type
    this.anomalies = this.anomalies.filter((a) => a.agentType !== agentType);

    const profile = this.getProfile(agentType);
    if (!profile) return;

    // ── High failure rate ───────────────────────────────────────────
    if (profile.successRate < (1 - this.config.criticalThreshold)) {
      this.reportAnomaly({
        agentType,
        anomalyType: 'high-failure-rate',
        severity: 'critical',
        description: `Success rate ${(profile.successRate * 100).toFixed(0)}% is below critical threshold (${((1 - this.config.criticalThreshold) * 100).toFixed(0)}%)`,
        suggestedAction: this.config.selfHealingEnabled ? 'disable-agent' : 'log-warning',
      });
    } else if (profile.successRate < (1 - this.config.degradedThreshold)) {
      this.reportAnomaly({
        agentType,
        anomalyType: 'high-failure-rate',
        severity: 'warning',
        description: `Success rate ${(profile.successRate * 100).toFixed(0)}% is below degraded threshold (${((1 - this.config.degradedThreshold) * 100).toFixed(0)}%)`,
        suggestedAction: 'reduce-score',
      });
    }

    // ── Consecutive failures ────────────────────────────────────────
    const recent = records.slice(-this.config.consecutiveFailureLimit);
    if (recent.length >= this.config.consecutiveFailureLimit &&
        recent.every((r) => r.outcome === 'failed')) {
      this.reportAnomaly({
        agentType,
        anomalyType: 'consecutive-failures',
        severity: 'critical',
        description: `${this.config.consecutiveFailureLimit} consecutive failures detected`,
        suggestedAction: this.config.selfHealingEnabled ? 'disable-agent' : 'log-warning',
      });
    }

    // ── Latency spike ───────────────────────────────────────────────
    if (profile.p50LatencyMs > 0) {
      const lastCompleted = records
        .filter((r) => r.outcome === 'completed')
        .slice(-1)[0];
      if (lastCompleted && lastCompleted.durationMs > profile.p50LatencyMs * this.config.latencySpikeMultiplier) {
        this.reportAnomaly({
          agentType,
          anomalyType: 'latency-spike',
          severity: 'warning',
          description: `Last execution took ${Math.round(lastCompleted.durationMs / 1000)}s vs p50 of ${Math.round(profile.p50LatencyMs / 1000)}s (${this.config.latencySpikeMultiplier}x threshold)`,
          suggestedAction: 'monitor',
        });
      }
    }

    // ── No recent success ───────────────────────────────────────────
    const recentFive = records.slice(-5);
    if (recentFive.length >= 5 && recentFive.every((r) => r.outcome !== 'completed')) {
      this.reportAnomaly({
        agentType,
        anomalyType: 'no-recent-success',
        severity: 'critical',
        description: 'No successful execution in last 5 attempts',
        suggestedAction: this.config.selfHealingEnabled ? 'disable-agent' : 'log-warning',
      });
    }
  }

  private reportAnomaly(anomaly: Omit<AnomalyReport, 'detectedAt'>): void {
    this.anomalies.push({
      ...anomaly,
      detectedAt: Date.now(),
    });

    // Log corrective action
    this.correctionLog.push({
      action: anomaly.suggestedAction,
      agentType: anomaly.agentType,
      timestamp: Date.now(),
    });

    // Cap correction log at 200 entries
    if (this.correctionLog.length > 200) {
      this.correctionLog.splice(0, this.correctionLog.length - 200);
    }
  }

  /**
   * Get all active anomalies.
   */
  getAnomalies(): AnomalyReport[] {
    return [...this.anomalies];
  }

  /**
   * Get anomalies for a specific agent type.
   */
  getAnomaliesFor(agentType: string): AnomalyReport[] {
    return this.anomalies.filter((a) => a.agentType === agentType);
  }

  /**
   * Get corrective actions that should be applied.
   * Returns agents that the Symbiont recommends disabling or score-reducing.
   * The caller (agent-runner integration) is responsible for executing these actions.
   */
  getPendingCorrections(): Array<{ agentType: string; action: CorrectionAction; reason: string }> {
    const corrections: Array<{ agentType: string; action: CorrectionAction; reason: string }> = [];
    const seen = new Set<string>();

    for (const anomaly of this.anomalies) {
      if (seen.has(anomaly.agentType)) continue;
      if (anomaly.suggestedAction === 'monitor') continue;

      seen.add(anomaly.agentType);
      corrections.push({
        agentType: anomaly.agentType,
        action: anomaly.suggestedAction,
        reason: anomaly.description,
      });
    }

    return corrections;
  }

  /* ── Health Report ─────────────────────────────────────────────────── */

  /**
   * Generate a comprehensive health report for the agent system.
   */
  getHealthReport(): HealthReport {
    const profiles = this.getAllProfiles();
    const healthCounts = { healthy: 0, degraded: 0, critical: 0, unknown: 0 };

    for (const p of profiles) {
      healthCounts[p.health]++;
    }

    // Overall health = worst health in the system
    const overallHealth: AgentHealth =
      healthCounts.critical > 0 ? 'critical'
      : healthCounts.degraded > 0 ? 'degraded'
      : profiles.length === 0 ? 'unknown'
      : 'healthy';

    const topPerformers = profiles
      .filter((p) => p.health === 'healthy' && p.totalExecutions >= 3)
      .sort((a, b) => b.successRate - a.successRate)
      .slice(0, 3)
      .map((p) => ({ agentType: p.agentType, successRate: p.successRate }));

    const underperformers = profiles
      .filter((p) => p.health === 'degraded' || p.health === 'critical')
      .map((p) => ({ agentType: p.agentType, successRate: p.successRate, health: p.health }));

    return {
      overallHealth,
      agentCount: profiles.length,
      healthyCount: healthCounts.healthy,
      degradedCount: healthCounts.degraded,
      criticalCount: healthCounts.critical,
      unknownCount: healthCounts.unknown,
      activeAnomalies: this.getAnomalies(),
      topPerformers,
      underperformers,
    };
  }

  /* ── Orchestrator Prompt Enhancement ───────────────────────────────── */

  /**
   * Generate performance context string for the orchestrator's planning prompt.
   * This enriches Claude's task decomposition with live performance data.
   */
  getPromptEnhancement(): string {
    const profiles = this.getAllProfiles();
    if (profiles.length === 0) return '';

    const lines = profiles
      .filter((p) => p.totalExecutions >= 2) // Only report on agents with enough data
      .map((p) => {
        const rate = `${(p.successRate * 100).toFixed(0)}% success`;
        const latency = p.avgLatencyMs > 0 ? `, avg ${Math.round(p.avgLatencyMs / 1000)}s` : '';
        const health = p.health !== 'healthy' ? ` ⚠ ${p.health}` : '';
        const trend = p.trend !== 'stable' ? ` (${p.trend})` : '';
        return `  "${p.agentType}": ${rate}${latency}${health}${trend}`;
      });

    if (lines.length === 0) return '';

    return `\nAGENT PERFORMANCE (live metrics):\n${lines.join('\n')}`;
  }

  /* ── Snapshot ───────────────────────────────────────────────────────── */

  /**
   * Get a full snapshot of the Symbiont state (for UI/debugging).
   */
  getSnapshot(): SymbiontSnapshot {
    let totalRecords = 0;
    for (const records of this.records.values()) {
      totalRecords += records.length;
    }

    return {
      profiles: this.getAllProfiles(),
      anomalies: this.getAnomalies(),
      totalRecordsStored: totalRecords,
      agentTypesTracked: this.records.size,
      config: this.getConfig(),
      timestamp: Date.now(),
    };
  }

  /* ── Cleanup ────────────────────────────────────────────────────────── */

  /**
   * Full cleanup — removes all state. Used for testing.
   */
  cleanup(): void {
    this.records.clear();
    this.anomalies = [];
    this.correctionLog = [];
  }

  /**
   * Clear records for a specific agent type.
   */
  clearRecords(agentType: string): void {
    this.records.delete(agentType);
    this.anomalies = this.anomalies.filter((a) => a.agentType !== agentType);
  }

  /* ── Private Helpers ────────────────────────────────────────────────── */

  private percentile(sorted: number[], p: number): number {
    if (sorted.length === 0) return 0;
    const idx = (p / 100) * (sorted.length - 1);
    const lower = Math.floor(idx);
    const upper = Math.ceil(idx);
    if (lower === upper) return sorted[lower];
    return sorted[lower] + (sorted[upper] - sorted[lower]) * (idx - lower);
  }

  private assessHealth(successRate: number, totalExecutions: number): AgentHealth {
    if (totalExecutions < 3) return 'unknown';
    if (successRate >= (1 - this.config.degradedThreshold)) return 'healthy';
    if (successRate >= (1 - this.config.criticalThreshold)) return 'degraded';
    return 'critical';
  }

  private computeTrend(records: ExecutionRecord[]): 'improving' | 'stable' | 'degrading' {
    if (records.length < 6) return 'stable'; // Need enough data

    // Compare success rate of first half vs second half of recent records
    const recent = records.slice(-10);
    const mid = Math.floor(recent.length / 2);
    const firstHalf = recent.slice(0, mid);
    const secondHalf = recent.slice(mid);

    const firstRate = firstHalf.filter((r) => r.outcome === 'completed').length / firstHalf.length;
    const secondRate = secondHalf.filter((r) => r.outcome === 'completed').length / secondHalf.length;

    const delta = secondRate - firstRate;
    if (delta > 0.15) return 'improving';
    if (delta < -0.15) return 'degrading';
    return 'stable';
  }
}

/* ── Singleton Export ───────────────────────────────────────────────── */

export const symbiontProtocol = new SymbiontProtocol();
