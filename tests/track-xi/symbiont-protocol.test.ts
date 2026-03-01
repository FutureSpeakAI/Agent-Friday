/**
 * Track XI, Phase 7 — Symbiont Protocol Tests
 *
 * Tests for the self-improving agent performance system:
 *   - Execution recording and metric aggregation
 *   - Performance profile computation (success rate, latency, trends)
 *   - Routing score enhancement (performance boosts)
 *   - Anomaly detection (failure rates, latency spikes, consecutive failures)
 *   - Self-healing corrections
 *   - Health reports
 *   - Orchestrator prompt enhancement
 *   - cLaw compliance
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  symbiontProtocol,
  ExecutionRecord,
} from '../../src/main/agents/symbiont-protocol';

/* ── Helpers ──────────────────────────────────────────────────────────── */

function makeRecord(overrides: Partial<ExecutionRecord> = {}): ExecutionRecord {
  return {
    id: overrides.id || `rec-${Math.random().toString(36).slice(2, 8)}`,
    agentType: overrides.agentType || 'test-agent',
    taskId: overrides.taskId || `task-${Math.random().toString(36).slice(2, 8)}`,
    outcome: overrides.outcome || 'completed',
    durationMs: overrides.durationMs ?? 5000,
    hadError: overrides.hadError ?? false,
    errorMessage: overrides.errorMessage,
    role: overrides.role || 'solo',
    teamId: overrides.teamId,
    trustTier: overrides.trustTier || 'local',
    completedAt: overrides.completedAt || Date.now(),
  };
}

function recordMany(
  agentType: string,
  count: number,
  outcome: 'completed' | 'failed' | 'cancelled' = 'completed',
  durationMs = 5000
): void {
  for (let i = 0; i < count; i++) {
    symbiontProtocol.recordExecution(makeRecord({
      agentType,
      outcome,
      durationMs,
      hadError: outcome === 'failed',
      errorMessage: outcome === 'failed' ? `Error ${i}` : undefined,
    }));
  }
}

/* ── Test Suite ────────────────────────────────────────────────────────── */

describe('SymbiontProtocol', () => {
  beforeEach(() => {
    symbiontProtocol.cleanup();
    symbiontProtocol.configure({
      maxRecordsPerAgent: 100,
      degradedThreshold: 0.3,
      criticalThreshold: 0.6,
      consecutiveFailureLimit: 3,
      latencySpikeMultiplier: 3.0,
      performanceWeight: 0.3,
      selfHealingEnabled: true,
    });
  });

  /* ── Execution Recording ───────────────────────────────────────────── */

  describe('Execution Recording', () => {
    it('records an execution', () => {
      symbiontProtocol.recordExecution(makeRecord({ agentType: 'alpha' }));

      const profile = symbiontProtocol.getProfile('alpha');
      expect(profile).not.toBeNull();
      expect(profile!.totalExecutions).toBe(1);
    });

    it('accumulates records for the same agent type', () => {
      recordMany('beta', 5);

      const profile = symbiontProtocol.getProfile('beta');
      expect(profile!.totalExecutions).toBe(5);
    });

    it('caps records at maxRecordsPerAgent', () => {
      symbiontProtocol.configure({ maxRecordsPerAgent: 10 });
      recordMany('capped', 20);

      const profile = symbiontProtocol.getProfile('capped');
      expect(profile!.totalExecutions).toBe(10); // Capped at 10
    });

    it('tracks different agent types independently', () => {
      recordMany('agent-a', 3);
      recordMany('agent-b', 7);

      expect(symbiontProtocol.getProfile('agent-a')!.totalExecutions).toBe(3);
      expect(symbiontProtocol.getProfile('agent-b')!.totalExecutions).toBe(7);
    });

    it('returns null profile for unknown agent', () => {
      expect(symbiontProtocol.getProfile('nonexistent')).toBeNull();
    });
  });

  /* ── Performance Profiles ──────────────────────────────────────────── */

  describe('Performance Profiles', () => {
    it('computes success rate correctly', () => {
      recordMany('mixed', 7, 'completed');
      recordMany('mixed', 3, 'failed');

      const profile = symbiontProtocol.getProfile('mixed');
      expect(profile!.successRate).toBeCloseTo(0.7, 1);
      expect(profile!.failures).toBe(3);
    });

    it('tracks cancellations separately from failures', () => {
      recordMany('cancels', 5, 'completed');
      recordMany('cancels', 2, 'cancelled');
      recordMany('cancels', 1, 'failed');

      const profile = symbiontProtocol.getProfile('cancels');
      expect(profile!.cancellations).toBe(2);
      expect(profile!.failures).toBe(1);
      expect(profile!.successRate).toBeCloseTo(5 / 8, 2);
    });

    it('computes latency percentiles from completed executions only', () => {
      // Record 10 completed with varying durations
      for (let i = 1; i <= 10; i++) {
        symbiontProtocol.recordExecution(makeRecord({
          agentType: 'latency-test',
          outcome: 'completed',
          durationMs: i * 1000,
        }));
      }
      // Record 2 failed (should not affect latency)
      recordMany('latency-test', 2, 'failed', 99999);

      const profile = symbiontProtocol.getProfile('latency-test');
      expect(profile!.p50LatencyMs).toBeGreaterThan(0);
      expect(profile!.p95LatencyMs).toBeGreaterThanOrEqual(profile!.p50LatencyMs);
      expect(profile!.avgLatencyMs).toBeGreaterThan(0);
    });

    it('captures recent errors', () => {
      for (let i = 0; i < 8; i++) {
        symbiontProtocol.recordExecution(makeRecord({
          agentType: 'errors',
          outcome: 'failed',
          hadError: true,
          errorMessage: `Error ${i}`,
        }));
      }

      const profile = symbiontProtocol.getProfile('errors');
      expect(profile!.recentErrors.length).toBeLessThanOrEqual(5);
      // Should contain the most recent errors
      expect(profile!.recentErrors[profile!.recentErrors.length - 1]).toBe('Error 7');
    });

    it('getAllProfiles returns all tracked agents', () => {
      recordMany('a', 2);
      recordMany('b', 3);
      recordMany('c', 1);

      const profiles = symbiontProtocol.getAllProfiles();
      expect(profiles).toHaveLength(3);
    });

    it('getAllProfiles sorted by total executions (most first)', () => {
      recordMany('few', 2);
      recordMany('many', 10);
      recordMany('some', 5);

      const profiles = symbiontProtocol.getAllProfiles();
      expect(profiles[0].agentType).toBe('many');
      expect(profiles[1].agentType).toBe('some');
      expect(profiles[2].agentType).toBe('few');
    });
  });

  /* ── Health Assessment ─────────────────────────────────────────────── */

  describe('Health Assessment', () => {
    it('healthy when success rate is above degraded threshold', () => {
      recordMany('healthy', 10, 'completed');

      const profile = symbiontProtocol.getProfile('healthy');
      expect(profile!.health).toBe('healthy');
    });

    it('degraded when failure rate exceeds degraded threshold', () => {
      // 0.3 degraded threshold → success rate < 70% is degraded
      recordMany('degraded', 6, 'completed');
      recordMany('degraded', 4, 'failed');

      const profile = symbiontProtocol.getProfile('degraded');
      expect(profile!.health).toBe('degraded');
    });

    it('critical when failure rate exceeds critical threshold', () => {
      // 0.6 critical threshold → success rate < 40% is critical
      recordMany('critical', 3, 'completed');
      recordMany('critical', 7, 'failed');

      const profile = symbiontProtocol.getProfile('critical');
      expect(profile!.health).toBe('critical');
    });

    it('unknown when fewer than 3 executions', () => {
      recordMany('unknown', 2, 'completed');

      const profile = symbiontProtocol.getProfile('unknown');
      expect(profile!.health).toBe('unknown');
    });
  });

  /* ── Trend Computation ──────────────────────────────────────────────── */

  describe('Trend Computation', () => {
    it('stable when insufficient data', () => {
      recordMany('short', 3, 'completed');

      const profile = symbiontProtocol.getProfile('short');
      expect(profile!.trend).toBe('stable');
    });

    it('improving when recent executions succeed more', () => {
      // First half: mostly failures
      recordMany('improving', 4, 'failed');
      // Second half: all successes
      recordMany('improving', 6, 'completed');

      const profile = symbiontProtocol.getProfile('improving');
      expect(profile!.trend).toBe('improving');
    });

    it('degrading when recent executions fail more', () => {
      // First half: all successes
      recordMany('degrading', 6, 'completed');
      // Second half: mostly failures
      recordMany('degrading', 4, 'failed');

      const profile = symbiontProtocol.getProfile('degrading');
      expect(profile!.trend).toBe('degrading');
    });

    it('stable when performance is consistent', () => {
      // Same success rate in both halves: 3/5 each
      const outcomes: Array<'completed' | 'failed'> = [
        'completed', 'failed', 'completed', 'failed', 'completed', // first half: 3/5
        'completed', 'failed', 'completed', 'failed', 'completed', // second half: 3/5
      ];
      for (const outcome of outcomes) {
        symbiontProtocol.recordExecution(makeRecord({
          agentType: 'stable',
          outcome,
          hadError: outcome === 'failed',
        }));
      }

      const profile = symbiontProtocol.getProfile('stable');
      expect(profile!.trend).toBe('stable');
    });
  });

  /* ── Routing Score Enhancement ──────────────────────────────────────── */

  describe('Routing Score Enhancement', () => {
    it('returns 0 boost for unknown agent', () => {
      expect(symbiontProtocol.getPerformanceBoost('unknown')).toBe(0);
    });

    it('returns 0 boost for agent with fewer than 3 executions', () => {
      recordMany('too-few', 2, 'completed');
      expect(symbiontProtocol.getPerformanceBoost('too-few')).toBe(0);
    });

    it('returns positive boost for high-performing agent', () => {
      recordMany('star', 10, 'completed', 3000);

      const boost = symbiontProtocol.getPerformanceBoost('star');
      expect(boost).toBeGreaterThan(0);
    });

    it('returns negative boost for poorly-performing agent', () => {
      recordMany('poor', 10, 'failed', 5000);

      const boost = symbiontProtocol.getPerformanceBoost('poor');
      expect(boost).toBeLessThan(0);
    });

    it('boost magnitude is bounded by performanceWeight config', () => {
      symbiontProtocol.configure({ performanceWeight: 0.3 });

      recordMany('bounded-good', 10, 'completed', 1000);
      recordMany('bounded-bad', 10, 'failed', 50000);

      const goodBoost = symbiontProtocol.getPerformanceBoost('bounded-good');
      const badBoost = symbiontProtocol.getPerformanceBoost('bounded-bad');

      expect(goodBoost).toBeLessThanOrEqual(0.3);
      expect(goodBoost).toBeGreaterThanOrEqual(-0.3);
      expect(badBoost).toBeLessThanOrEqual(0.3);
      expect(badBoost).toBeGreaterThanOrEqual(-0.3);
    });
  });

  /* ── Anomaly Detection ─────────────────────────────────────────────── */

  describe('Anomaly Detection', () => {
    it('detects high failure rate (critical)', () => {
      recordMany('failing', 2, 'completed');
      recordMany('failing', 8, 'failed');

      const anomalies = symbiontProtocol.getAnomaliesFor('failing');
      expect(anomalies.length).toBeGreaterThan(0);

      const highFailure = anomalies.find((a) => a.anomalyType === 'high-failure-rate');
      expect(highFailure).toBeDefined();
      expect(highFailure!.severity).toBe('critical');
    });

    it('detects high failure rate (warning)', () => {
      recordMany('warning', 6, 'completed');
      recordMany('warning', 4, 'failed');

      const anomalies = symbiontProtocol.getAnomaliesFor('warning');
      const highFailure = anomalies.find((a) => a.anomalyType === 'high-failure-rate');
      expect(highFailure).toBeDefined();
      expect(highFailure!.severity).toBe('warning');
    });

    it('detects consecutive failures', () => {
      recordMany('consec', 5, 'completed'); // Good history
      recordMany('consec', 3, 'failed');    // 3 consecutive failures

      const anomalies = symbiontProtocol.getAnomaliesFor('consec');
      const consecutive = anomalies.find((a) => a.anomalyType === 'consecutive-failures');
      expect(consecutive).toBeDefined();
      expect(consecutive!.severity).toBe('critical');
    });

    it('detects latency spike', () => {
      // Build baseline of fast executions
      recordMany('spiked', 5, 'completed', 2000);
      // Add one slow execution (3x spike threshold)
      symbiontProtocol.recordExecution(makeRecord({
        agentType: 'spiked',
        outcome: 'completed',
        durationMs: 20000, // 10x the baseline
      }));

      const anomalies = symbiontProtocol.getAnomaliesFor('spiked');
      const spike = anomalies.find((a) => a.anomalyType === 'latency-spike');
      expect(spike).toBeDefined();
      expect(spike!.severity).toBe('warning');
    });

    it('detects no recent success', () => {
      recordMany('no-success', 5, 'failed');

      const anomalies = symbiontProtocol.getAnomaliesFor('no-success');
      const noSuccess = anomalies.find((a) => a.anomalyType === 'no-recent-success');
      expect(noSuccess).toBeDefined();
      expect(noSuccess!.severity).toBe('critical');
    });

    it('no anomalies for healthy agent', () => {
      recordMany('healthy', 10, 'completed', 3000);

      const anomalies = symbiontProtocol.getAnomaliesFor('healthy');
      expect(anomalies).toHaveLength(0);
    });

    it('clears old anomalies when agent recovers', () => {
      // First: trigger anomalies
      recordMany('recovery', 5, 'failed');
      expect(symbiontProtocol.getAnomaliesFor('recovery').length).toBeGreaterThan(0);

      // Then: agent recovers with many successes
      recordMany('recovery', 20, 'completed');

      // Anomalies should be cleared on re-detection
      const anomalies = symbiontProtocol.getAnomaliesFor('recovery');
      const highFailure = anomalies.find((a) => a.anomalyType === 'high-failure-rate');
      // Success rate is now 20/25 = 80%, above degraded threshold
      expect(highFailure).toBeUndefined();
    });
  });

  /* ── Self-Healing Corrections ──────────────────────────────────────── */

  describe('Self-Healing Corrections', () => {
    it('suggests disable-agent for critical anomalies when self-healing enabled', () => {
      recordMany('doomed', 10, 'failed');

      const corrections = symbiontProtocol.getPendingCorrections();
      const doomed = corrections.find((c) => c.agentType === 'doomed');
      expect(doomed).toBeDefined();
      expect(doomed!.action).toBe('disable-agent');
    });

    it('suggests log-warning instead of disable when self-healing disabled', () => {
      symbiontProtocol.configure({ selfHealingEnabled: false });
      recordMany('safe', 10, 'failed');

      const corrections = symbiontProtocol.getPendingCorrections();
      const safe = corrections.find((c) => c.agentType === 'safe');
      expect(safe).toBeDefined();
      expect(safe!.action).toBe('log-warning');
    });

    it('suggests reduce-score for warning-level anomalies', () => {
      recordMany('degraded', 6, 'completed');
      recordMany('degraded', 4, 'failed');

      const corrections = symbiontProtocol.getPendingCorrections();
      const degraded = corrections.find((c) => c.agentType === 'degraded');
      expect(degraded).toBeDefined();
      expect(degraded!.action).toBe('reduce-score');
    });

    it('no corrections for healthy agents', () => {
      recordMany('good', 10, 'completed');

      const corrections = symbiontProtocol.getPendingCorrections();
      expect(corrections.find((c) => c.agentType === 'good')).toBeUndefined();
    });

    it('deduplicates corrections per agent type', () => {
      recordMany('dup', 10, 'failed'); // triggers multiple anomaly types

      const corrections = symbiontProtocol.getPendingCorrections();
      const dupCorrections = corrections.filter((c) => c.agentType === 'dup');
      expect(dupCorrections).toHaveLength(1); // Only one correction per agent
    });
  });

  /* ── Health Report ─────────────────────────────────────────────────── */

  describe('Health Report', () => {
    it('reports overall system health', () => {
      recordMany('healthy-a', 10, 'completed');
      recordMany('healthy-b', 8, 'completed');
      recordMany('degraded-c', 5, 'completed');
      recordMany('degraded-c', 5, 'failed');

      const report = symbiontProtocol.getHealthReport();
      expect(report.agentCount).toBe(3);
      expect(report.healthyCount).toBe(2);
      expect(report.degradedCount).toBe(1);
      expect(report.overallHealth).toBe('degraded'); // Worst health in system
    });

    it('identifies top performers', () => {
      recordMany('star', 10, 'completed');
      recordMany('okay', 8, 'completed');
      recordMany('okay', 2, 'failed');

      const report = symbiontProtocol.getHealthReport();
      expect(report.topPerformers.length).toBeGreaterThanOrEqual(1);
      expect(report.topPerformers[0].agentType).toBe('star');
    });

    it('identifies underperformers', () => {
      recordMany('good', 10, 'completed');
      recordMany('bad', 3, 'completed');
      recordMany('bad', 7, 'failed');

      const report = symbiontProtocol.getHealthReport();
      expect(report.underperformers.length).toBe(1);
      expect(report.underperformers[0].agentType).toBe('bad');
    });

    it('returns unknown overall health when no data', () => {
      const report = symbiontProtocol.getHealthReport();
      expect(report.overallHealth).toBe('unknown');
      expect(report.agentCount).toBe(0);
    });
  });

  /* ── Prompt Enhancement ────────────────────────────────────────────── */

  describe('Prompt Enhancement', () => {
    it('returns empty string when no data', () => {
      expect(symbiontProtocol.getPromptEnhancement()).toBe('');
    });

    it('returns empty when all agents have fewer than 2 executions', () => {
      recordMany('single', 1, 'completed');
      expect(symbiontProtocol.getPromptEnhancement()).toBe('');
    });

    it('generates performance context for orchestrator', () => {
      recordMany('research', 5, 'completed', 15000);
      recordMany('summarize', 3, 'completed', 3000);

      const enhancement = symbiontProtocol.getPromptEnhancement();
      expect(enhancement).toContain('AGENT PERFORMANCE');
      expect(enhancement).toContain('"research"');
      expect(enhancement).toContain('"summarize"');
      expect(enhancement).toContain('100% success');
    });

    it('flags degraded agents in prompt', () => {
      recordMany('flaky', 5, 'completed');
      recordMany('flaky', 5, 'failed');

      const enhancement = symbiontProtocol.getPromptEnhancement();
      expect(enhancement).toContain('degraded');
    });
  });

  /* ── Snapshot ────────────────────────────────────────────────────────── */

  describe('Snapshot', () => {
    it('returns comprehensive state snapshot', () => {
      recordMany('a', 5, 'completed');
      recordMany('b', 10, 'failed'); // Will trigger anomalies

      const snap = symbiontProtocol.getSnapshot();
      expect(snap.agentTypesTracked).toBe(2);
      expect(snap.totalRecordsStored).toBe(15);
      expect(snap.profiles).toHaveLength(2);
      expect(snap.anomalies.length).toBeGreaterThan(0);
      expect(snap.config).toBeDefined();
      expect(snap.timestamp).toBeGreaterThan(0);
    });
  });

  /* ── Cleanup ─────────────────────────────────────────────────────────── */

  describe('Cleanup', () => {
    it('removes all state', () => {
      recordMany('a', 5);
      recordMany('b', 10, 'failed');

      symbiontProtocol.cleanup();

      expect(symbiontProtocol.getAllProfiles()).toHaveLength(0);
      expect(symbiontProtocol.getAnomalies()).toHaveLength(0);
    });

    it('clearRecords removes specific agent type', () => {
      recordMany('keep', 5);
      recordMany('remove', 5, 'failed');

      symbiontProtocol.clearRecords('remove');

      expect(symbiontProtocol.getProfile('keep')).not.toBeNull();
      expect(symbiontProtocol.getProfile('remove')).toBeNull();
      expect(symbiontProtocol.getAnomaliesFor('remove')).toHaveLength(0);
    });
  });

  /* ── Configuration ─────────────────────────────────────────────────── */

  describe('Configuration', () => {
    it('allows partial configuration overrides', () => {
      symbiontProtocol.configure({ maxRecordsPerAgent: 50 });

      const config = symbiontProtocol.getConfig();
      expect(config.maxRecordsPerAgent).toBe(50);
      // Other defaults preserved
      expect(config.degradedThreshold).toBe(0.3);
    });

    it('getConfig returns a copy (not reference)', () => {
      const config = symbiontProtocol.getConfig();
      config.maxRecordsPerAgent = 999;

      expect(symbiontProtocol.getConfig().maxRecordsPerAgent).not.toBe(999);
    });
  });

  /* ── cLaw Compliance ─────────────────────────────────────────────────── */

  describe('cLaw Compliance', () => {
    it('First Law: performance data preserves trust-tier metadata', () => {
      symbiontProtocol.recordExecution(makeRecord({
        agentType: 'trusted',
        trustTier: 'local',
      }));
      symbiontProtocol.recordExecution(makeRecord({
        agentType: 'public',
        trustTier: 'public',
      }));

      // Performance profiles are per-agent-type, trust tier is preserved in records
      // The capability map's findCapable() still applies trust filtering independently
      expect(symbiontProtocol.getProfile('trusted')).not.toBeNull();
      expect(symbiontProtocol.getProfile('public')).not.toBeNull();
    });

    it('Third Law: all operations complete under 500ms with heavy load', () => {
      // Generate heavy load
      for (let i = 0; i < 20; i++) {
        recordMany(`agent-${i}`, 50, i % 3 === 0 ? 'failed' : 'completed', 5000 + i * 100);
      }

      const start = Date.now();

      // Run all query operations
      symbiontProtocol.getAllProfiles();
      symbiontProtocol.getHealthReport();
      symbiontProtocol.getPromptEnhancement();
      symbiontProtocol.getSnapshot();
      symbiontProtocol.getPendingCorrections();
      for (let i = 0; i < 20; i++) {
        symbiontProtocol.getPerformanceBoost(`agent-${i}`);
      }

      const elapsed = Date.now() - start;
      expect(elapsed).toBeLessThan(500);
    });
  });

  /* ── Integration Readiness ───────────────────────────────────────────── */

  describe('Integration Readiness', () => {
    it('recordExecution matches the data agent-runner captures', () => {
      // Simulate what agent-runner would send
      const record: ExecutionRecord = {
        id: 'task-abc123',
        agentType: 'research',
        taskId: 'task-abc123',
        outcome: 'completed',
        durationMs: 12500,
        hadError: false,
        role: 'solo',
        trustTier: 'local',
        completedAt: Date.now(),
      };

      symbiontProtocol.recordExecution(record);

      const profile = symbiontProtocol.getProfile('research');
      expect(profile).not.toBeNull();
      expect(profile!.totalExecutions).toBe(1);
      expect(profile!.successRate).toBe(1);
    });

    it('getPerformanceBoost is compatible with capability-map findCapable scoring', () => {
      recordMany('research', 10, 'completed', 3000);

      const boost = symbiontProtocol.getPerformanceBoost('research');
      // Should be a number that can be added to capability match scores
      expect(typeof boost).toBe('number');
      expect(boost).toBeGreaterThanOrEqual(-0.3);
      expect(boost).toBeLessThanOrEqual(0.3);
    });

    it('getPromptEnhancement is compatible with orchestrator prompt injection', () => {
      recordMany('research', 5, 'completed', 10000);
      recordMany('summarize', 3, 'completed', 2000);

      const enhancement = symbiontProtocol.getPromptEnhancement();
      // Should be a string that can be concatenated into the orchestrator prompt
      expect(typeof enhancement).toBe('string');
      // Should start with newline for clean injection
      if (enhancement) {
        expect(enhancement.startsWith('\n')).toBe(true);
      }
    });

    it('getPendingCorrections returns actionable items for agent-runner', () => {
      recordMany('failing', 10, 'failed');

      const corrections = symbiontProtocol.getPendingCorrections();
      expect(corrections.length).toBeGreaterThan(0);

      for (const c of corrections) {
        expect(c.agentType).toBeDefined();
        expect(['monitor', 'log-warning', 'disable-agent', 'reduce-score']).toContain(c.action);
        expect(c.reason).toBeDefined();
      }
    });
  });
});
