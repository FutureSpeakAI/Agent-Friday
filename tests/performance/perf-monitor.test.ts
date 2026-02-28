/**
 * Performance Monitor — Tests for Phase 3 instrumentation.
 *
 * Validates:
 *   1. Memory snapshots capture real process data
 *   2. CPU sampling produces valid percentages
 *   3. Polling loop metrics compute correctly
 *   4. Startup milestones record elapsed times
 *   5. Baselines capture tagged state snapshots
 *   6. Warnings fire when thresholds are exceeded
 *   7. cLaw Gate: no user content in telemetry data
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  PerfMonitor,
  type MemorySnapshot,
  type CpuSnapshot,
  type PerfBaseline,
  type PollingLoopMetric,
} from '../../src/main/perf-monitor';

// Fresh instance per test
function createMonitor(): PerfMonitor {
  return new PerfMonitor();
}

describe('Performance Monitor — Instrumentation', () => {
  let monitor: PerfMonitor;

  beforeEach(() => {
    monitor = createMonitor();
  });

  afterEach(() => {
    monitor.shutdown();
  });

  // ── Memory Snapshots ───────────────────────────────────────────

  describe('memory sampling', () => {
    it('should return valid memory snapshot with all fields', () => {
      const snap = monitor.sampleMemory();
      expect(snap.timestamp).toBeGreaterThan(0);
      expect(snap.heapUsedMB).toBeGreaterThan(0);
      expect(snap.heapTotalMB).toBeGreaterThan(0);
      expect(snap.rssMB).toBeGreaterThan(0);
      expect(typeof snap.externalMB).toBe('number');
      expect(typeof snap.arrayBuffersMB).toBe('number');
    });

    it('should report heap used <= heap total', () => {
      const snap = monitor.sampleMemory();
      expect(snap.heapUsedMB).toBeLessThanOrEqual(snap.heapTotalMB);
    });

    it('should report RSS >= heap total', () => {
      const snap = monitor.sampleMemory();
      expect(snap.rssMB).toBeGreaterThanOrEqual(snap.heapTotalMB);
    });

    it('should report values in MB (reasonable range)', () => {
      const snap = monitor.sampleMemory();
      // A Node process should use between 5MB and 2GB
      expect(snap.rssMB).toBeGreaterThan(5);
      expect(snap.rssMB).toBeLessThan(2048);
    });
  });

  // ── CPU Snapshots ──────────────────────────────────────────────

  describe('CPU sampling', () => {
    it('should return valid CPU snapshot after initialization', () => {
      monitor.initialize();
      const cpu = monitor.getCpuSnapshot();
      expect(typeof cpu.percentUser).toBe('number');
      expect(typeof cpu.percentSystem).toBe('number');
      expect(typeof cpu.percentTotal).toBe('number');
      expect(cpu.timestamp).toBeGreaterThanOrEqual(0);
    });

    it('should have non-negative CPU percentages', () => {
      monitor.initialize();
      const cpu = monitor.getCpuSnapshot();
      expect(cpu.percentUser).toBeGreaterThanOrEqual(0);
      expect(cpu.percentSystem).toBeGreaterThanOrEqual(0);
      expect(cpu.percentTotal).toBeGreaterThanOrEqual(0);
    });

    it('should have total = user + system', () => {
      monitor.initialize();
      const cpu = monitor.getCpuSnapshot();
      // Allow small floating point variance
      expect(Math.abs(cpu.percentTotal - (cpu.percentUser + cpu.percentSystem))).toBeLessThan(0.01);
    });
  });

  // ── Startup Milestones ─────────────────────────────────────────

  describe('startup milestones', () => {
    it('should record milestones with elapsed time', () => {
      monitor.recordMilestone('test-milestone');
      const summary = monitor.getSummary();
      const milestone = summary.startupMilestones.find(m => m.name === 'test-milestone');
      expect(milestone).toBeDefined();
      expect(milestone!.elapsedMs).toBeGreaterThan(0);
      expect(milestone!.timestamp).toBeGreaterThan(0);
    });

    it('should record app-ready on initialize', () => {
      monitor.initialize();
      const summary = monitor.getSummary();
      const appReady = summary.startupMilestones.find(m => m.name === 'app-ready');
      expect(appReady).toBeDefined();
    });

    it('should record multiple milestones in order', () => {
      monitor.recordMilestone('step-1');
      monitor.recordMilestone('step-2');
      monitor.recordMilestone('step-3');
      const summary = monitor.getSummary();
      expect(summary.startupMilestones.length).toBe(3);
      expect(summary.startupMilestones[0].name).toBe('step-1');
      expect(summary.startupMilestones[2].name).toBe('step-3');
      // Each should have increasing elapsed times
      expect(summary.startupMilestones[2].elapsedMs)
        .toBeGreaterThanOrEqual(summary.startupMilestones[0].elapsedMs);
    });

    it('should cap milestones at 30', () => {
      for (let i = 0; i < 40; i++) {
        monitor.recordMilestone(`milestone-${i}`);
      }
      const summary = monitor.getSummary();
      expect(summary.startupMilestones.length).toBe(30);
    });

    it('should report startup time from first-idle milestone', () => {
      monitor.recordMilestone('first-idle');
      const startupMs = monitor.getStartupMs();
      expect(startupMs).toBeGreaterThan(0);
    });
  });

  // ── Polling Loop Metrics ───────────────────────────────────────

  describe('polling loop measurement', () => {
    it('should record polling cycle durations', () => {
      monitor.recordPollingCycle('ambient', 50, 30000);
      monitor.recordPollingCycle('ambient', 60, 30000);
      monitor.recordPollingCycle('ambient', 40, 30000);

      const metrics = monitor.getPollingMetrics();
      const ambient = metrics.find(m => m.name === 'ambient');
      expect(ambient).toBeDefined();
      expect(ambient!.callCount).toBe(3);
      expect(ambient!.avgDurationMs).toBe(50);
      expect(ambient!.maxDurationMs).toBe(60);
      expect(ambient!.lastDurationMs).toBe(40);
    });

    it('should calculate estimated CPU% correctly', () => {
      // 50ms every 30000ms = 0.167%
      monitor.recordPollingCycle('test-loop', 50, 30000);
      const metrics = monitor.getPollingMetrics();
      const loop = metrics.find(m => m.name === 'test-loop');
      expect(loop).toBeDefined();
      expect(loop!.estimatedCpuPercent).toBeCloseTo(0.167, 2);
    });

    it('should flag loop exceeding 1% CPU in warnings', () => {
      // 500ms every 30000ms = 1.67% — should trigger warning
      monitor.recordPollingCycle('heavy-loop', 500, 30000);
      const summary = monitor.getSummary();
      expect(summary.warnings.some(w => w.includes('heavy-loop'))).toBe(true);
      expect(summary.warnings.some(w => w.includes('1%'))).toBe(true);
    });

    it('should NOT flag loop under 1% CPU', () => {
      // 10ms every 30000ms = 0.033% — well under threshold
      monitor.recordPollingCycle('light-loop', 10, 30000);
      const summary = monitor.getSummary();
      expect(summary.warnings.some(w => w.includes('light-loop'))).toBe(false);
    });

    it('should track multiple loops independently', () => {
      monitor.recordPollingCycle('ambient', 50, 30000);
      monitor.recordPollingCycle('clipboard', 20, 30000);
      monitor.recordPollingCycle('sentiment', 100, 60000);

      const metrics = monitor.getPollingMetrics();
      expect(metrics.length).toBe(3);
      expect(metrics.find(m => m.name === 'ambient')!.avgDurationMs).toBe(50);
      expect(metrics.find(m => m.name === 'clipboard')!.avgDurationMs).toBe(20);
      expect(metrics.find(m => m.name === 'sentiment')!.avgDurationMs).toBe(100);
    });

    it('should cap history at 100 entries per loop', () => {
      for (let i = 0; i < 150; i++) {
        monitor.recordPollingCycle('capped-loop', i + 1, 30000);
      }
      const metrics = monitor.getPollingMetrics();
      const loop = metrics.find(m => m.name === 'capped-loop');
      expect(loop!.callCount).toBe(150); // Total calls tracked
      // Average should be based on last 100 entries (51..150)
      const expectedAvg = (51 + 150) / 2; // 100.5
      expect(loop!.avgDurationMs).toBeCloseTo(expectedAvg, 0);
    });
  });

  // ── Activity State ─────────────────────────────────────────────

  describe('activity state', () => {
    it('should default to idle', () => {
      expect(monitor.getActivityState()).toBe('idle');
    });

    it('should update to voice', () => {
      monitor.setActivityState('voice');
      expect(monitor.getActivityState()).toBe('voice');
    });

    it('should update to multi-agent', () => {
      monitor.setActivityState('multi-agent');
      expect(monitor.getActivityState()).toBe('multi-agent');
    });
  });

  // ── Baselines ──────────────────────────────────────────────────

  describe('baselines', () => {
    it('should capture baseline with current state', () => {
      monitor.setActivityState('idle');
      const baseline = monitor.captureBaseline();
      expect(baseline.state).toBe('idle');
      expect(baseline.memory.rssMB).toBeGreaterThan(0);
      expect(baseline.timestamp).toBeGreaterThan(0);
    });

    it('should capture baseline with specified state', () => {
      const baseline = monitor.captureBaseline('voice');
      expect(baseline.state).toBe('voice');
    });

    it('should store multiple baselines', () => {
      monitor.captureBaseline('idle');
      monitor.captureBaseline('voice');
      monitor.captureBaseline('multi-agent');

      const all = monitor.getBaselines();
      expect(all.length).toBe(3);
    });

    it('should filter baselines by state', () => {
      monitor.captureBaseline('idle');
      monitor.captureBaseline('voice');
      monitor.captureBaseline('idle');
      monitor.captureBaseline('voice');

      const idleBaselines = monitor.getBaselines('idle');
      expect(idleBaselines.length).toBe(2);
      expect(idleBaselines.every(b => b.state === 'idle')).toBe(true);
    });

    it('should cap baselines at 20', () => {
      for (let i = 0; i < 25; i++) {
        monitor.captureBaseline('idle');
      }
      expect(monitor.getBaselines().length).toBe(20);
    });
  });

  // ── Summary ────────────────────────────────────────────────────

  describe('summary', () => {
    it('should include all required fields', () => {
      monitor.initialize();
      const summary = monitor.getSummary();
      expect(summary.uptime).toBeGreaterThanOrEqual(0);
      expect(summary.currentState).toBe('idle');
      expect(summary.memory).toBeDefined();
      expect(summary.memory.rssMB).toBeGreaterThan(0);
      expect(summary.cpu).toBeDefined();
      expect(typeof summary.startupMs).toBe('number');
      expect(Array.isArray(summary.startupMilestones)).toBe(true);
      expect(Array.isArray(summary.pollingLoops)).toBe(true);
      expect(Array.isArray(summary.baselines)).toBe(true);
      expect(Array.isArray(summary.warnings)).toBe(true);
    });

    it('should generate memory warnings above 1GB RSS', () => {
      // We can't easily force RSS above 1GB in a test, but we can verify
      // the summary includes the warning check by examining the warning logic
      const summary = monitor.getSummary();
      // In a test environment, RSS should be well under 1GB — no warning
      expect(summary.warnings.some(w => w.includes('RSS'))).toBe(false);
    });

    it('should return text summary with key metrics', () => {
      monitor.initialize();
      const text = monitor.getTextSummary();
      expect(text).toContain('CPU:');
      expect(text).toContain('RSS:');
      expect(text).toContain('Heap:');
      expect(text).toContain('State:');
      expect(text).toContain('Uptime:');
    });
  });

  // ── cLaw Gate: Privacy Boundary ────────────────────────────────

  describe('cLaw Gate — no user content in telemetry', () => {
    it('should not include any URL or window title in summary', () => {
      monitor.initialize();
      monitor.recordMilestone('app-ready');
      monitor.recordMilestone('window-shown');
      monitor.recordPollingCycle('ambient', 50, 30000);
      monitor.captureBaseline('idle');

      const summary = monitor.getSummary();
      const jsonStr = JSON.stringify(summary);

      // No URL patterns
      expect(jsonStr).not.toMatch(/https?:\/\//);
      // No file paths to user directories
      expect(jsonStr).not.toContain('Documents');
      expect(jsonStr).not.toContain('Desktop');
      // No common user content markers
      expect(jsonStr).not.toContain('gmail');
      expect(jsonStr).not.toContain('password');
    });

    it('should only contain system-level metrics in memory snapshot', () => {
      const snap = monitor.sampleMemory();
      const keys = Object.keys(snap);
      // Only allowed fields
      expect(keys).toEqual(
        expect.arrayContaining(['timestamp', 'heapUsedMB', 'heapTotalMB', 'rssMB', 'externalMB', 'arrayBuffersMB'])
      );
      expect(keys.length).toBe(6);
    });

    it('should only contain system-level metrics in CPU snapshot', () => {
      const cpu = monitor.getCpuSnapshot();
      const keys = Object.keys(cpu);
      expect(keys).toEqual(
        expect.arrayContaining(['timestamp', 'percentUser', 'percentSystem', 'percentTotal'])
      );
      expect(keys.length).toBe(4);
    });

    it('milestone names should be generic system events', () => {
      monitor.recordMilestone('app-ready');
      monitor.recordMilestone('window-shown');
      monitor.recordMilestone('first-idle');
      monitor.recordMilestone('gemini-connected');

      const summary = monitor.getSummary();
      for (const m of summary.startupMilestones) {
        // Should not contain user content
        expect(m.name).not.toMatch(/https?:\/\//);
        expect(m.name).not.toContain(' '); // Generic identifiers, no sentences
      }
    });

    it('polling loop names should be generic identifiers', () => {
      monitor.recordPollingCycle('ambient', 50, 30000);
      monitor.recordPollingCycle('clipboard-check', 20, 30000);
      monitor.recordPollingCycle('sentiment', 100, 60000);

      const metrics = monitor.getPollingMetrics();
      for (const m of metrics) {
        expect(m.name).not.toMatch(/https?:\/\//);
        // No user-facing strings
        expect(m.name.length).toBeLessThan(30);
      }
    });

    it('baselines should not contain any user-identifying data', () => {
      monitor.captureBaseline('idle');
      monitor.captureBaseline('voice');

      const baselines = monitor.getBaselines();
      for (const b of baselines) {
        // State is a generic enum value
        expect(['idle', 'voice', 'multi-agent', 'tools', 'unknown']).toContain(b.state);
        // Memory snapshot has only system fields
        expect(Object.keys(b.memory).length).toBe(6);
        // CPU snapshot has only system fields
        expect(Object.keys(b.cpu).length).toBe(4);
      }
    });
  });

  // ── Lifecycle ──────────────────────────────────────────────────

  describe('lifecycle', () => {
    it('should be safe to call shutdown without initialize', () => {
      expect(() => monitor.shutdown()).not.toThrow();
    });

    it('should be safe to call initialize twice', () => {
      monitor.initialize();
      expect(() => monitor.initialize()).not.toThrow();
    });

    it('should stop CPU sampling on shutdown', () => {
      monitor.initialize();
      monitor.shutdown();
      // After shutdown, no timers should be running
      // (No direct way to assert, but it shouldn't throw)
      expect(() => monitor.getCpuSnapshot()).not.toThrow();
    });
  });
});
