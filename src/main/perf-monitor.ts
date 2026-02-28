/**
 * perf-monitor.ts — Performance instrumentation & resource monitoring.
 *
 * Phase 3 of Track VIII: The Foundation — Testing, Resilience & Performance.
 *
 * Measures:
 *   1. Process memory (heapUsed, heapTotal, rss, external)
 *   2. CPU usage (user + system microseconds → percentage)
 *   3. Startup timing (app-ready, window-shown, first-idle milestones)
 *   4. Polling loop costs (duration & frequency per loop)
 *   5. Active state detection (idle, voice, multi-agent)
 *
 * cLaw Safety Boundary:
 *   Performance telemetry captures system metrics ONLY.
 *   NO user content, NO URLs visited, NO window titles, NO message text.
 *   Metric names are generic (e.g., "gemini-ws-bytes") not content-bearing.
 *
 * Data stays LOCAL — never transmitted to FutureSpeak or any external service.
 */

// ── Types ────────────────────────────────────────────────────────────

export interface MemorySnapshot {
  timestamp: number;
  heapUsedMB: number;
  heapTotalMB: number;
  rssMB: number;
  externalMB: number;
  arrayBuffersMB: number;
}

export interface CpuSnapshot {
  timestamp: number;
  percentUser: number;
  percentSystem: number;
  percentTotal: number;
}

export type ActivityState = 'idle' | 'voice' | 'multi-agent' | 'tools' | 'unknown';

export interface StartupMilestone {
  name: string;
  timestamp: number;
  elapsedMs: number; // ms since process start
}

export interface PollingLoopMetric {
  name: string;
  lastDurationMs: number;
  avgDurationMs: number;
  maxDurationMs: number;
  callCount: number;
  /** Estimated CPU% = avgDurationMs / intervalMs * 100 */
  estimatedCpuPercent: number;
  intervalMs: number;
}

export interface PerfBaseline {
  state: ActivityState;
  memory: MemorySnapshot;
  cpu: CpuSnapshot;
  timestamp: number;
}

export interface PerfSummary {
  uptime: number;
  currentState: ActivityState;
  memory: MemorySnapshot;
  cpu: CpuSnapshot;
  startupMs: number;
  startupMilestones: StartupMilestone[];
  pollingLoops: PollingLoopMetric[];
  baselines: PerfBaseline[];
  warnings: string[];
}

// ── Constants ────────────────────────────────────────────────────────

const MB = 1024 * 1024;
const CPU_SAMPLE_INTERVAL_MS = 5000;
const MAX_BASELINES = 20;
const MAX_MILESTONES = 30;
const POLLING_LOOP_HISTORY = 100; // Keep last N durations for averaging

// cLaw: CPU threshold for polling loop warnings
const POLLING_CPU_WARN_THRESHOLD = 1.0; // 1% per the validation criteria

// ── Performance Monitor ──────────────────────────────────────────────

export class PerfMonitor {
  private processStartMs: number;
  private milestones: StartupMilestone[] = [];
  private baselines: PerfBaseline[] = [];
  private pollingLoops = new Map<string, {
    durations: number[];
    maxDurationMs: number;
    callCount: number;
    intervalMs: number;
  }>();

  // CPU tracking
  private lastCpuUsage: NodeJS.CpuUsage | null = null;
  private lastCpuTime: number = 0;
  private currentCpu: CpuSnapshot = {
    timestamp: 0,
    percentUser: 0,
    percentSystem: 0,
    percentTotal: 0,
  };

  // State
  private currentState: ActivityState = 'idle';
  private cpuSampleTimer: ReturnType<typeof setInterval> | null = null;
  private initialized = false;

  constructor() {
    // process.uptime() gives seconds since node process started
    this.processStartMs = Date.now() - process.uptime() * 1000;
  }

  // ── Initialization ──────────────────────────────────────────────

  /**
   * Initialize the performance monitor.
   * Starts periodic CPU sampling. Must be called after app.whenReady().
   */
  initialize(): void {
    if (this.initialized) return;
    this.initialized = true;

    // Record app-ready milestone
    this.recordMilestone('app-ready');

    // Start periodic CPU sampling
    this.lastCpuUsage = process.cpuUsage();
    this.lastCpuTime = Date.now();

    this.cpuSampleTimer = setInterval(() => {
      this.sampleCpu();
    }, CPU_SAMPLE_INTERVAL_MS);

    // Don't prevent process exit
    if (this.cpuSampleTimer.unref) {
      this.cpuSampleTimer.unref();
    }

    console.log('[PerfMonitor] Initialized — CPU sampling every 5s');
  }

  /**
   * Stop the performance monitor. Call on app quit.
   */
  shutdown(): void {
    if (this.cpuSampleTimer) {
      clearInterval(this.cpuSampleTimer);
      this.cpuSampleTimer = null;
    }
  }

  // ── Milestone Recording ─────────────────────────────────────────

  /**
   * Record a startup milestone (e.g., 'window-shown', 'first-gemini-connect').
   * Elapsed time is calculated from process start.
   *
   * cLaw: milestone names are generic system events — no user content.
   */
  recordMilestone(name: string): void {
    const now = Date.now();
    const elapsedMs = now - this.processStartMs;

    this.milestones.push({ name, timestamp: now, elapsedMs });

    // Cap storage
    if (this.milestones.length > MAX_MILESTONES) {
      this.milestones = this.milestones.slice(-MAX_MILESTONES);
    }
  }

  /**
   * Get the total startup time (process start to 'first-idle' milestone).
   * Returns -1 if first-idle hasn't been recorded yet.
   */
  getStartupMs(): number {
    const firstIdle = this.milestones.find(m => m.name === 'first-idle');
    if (firstIdle) return firstIdle.elapsedMs;

    // Fallback: time from process start to now
    return Date.now() - this.processStartMs;
  }

  // ── Memory Sampling ─────────────────────────────────────────────

  /**
   * Take a memory snapshot. Safe to call frequently.
   * Reports only system metrics — no user content.
   */
  sampleMemory(): MemorySnapshot {
    const mem = process.memoryUsage();
    return {
      timestamp: Date.now(),
      heapUsedMB: Math.round(mem.heapUsed / MB * 100) / 100,
      heapTotalMB: Math.round(mem.heapTotal / MB * 100) / 100,
      rssMB: Math.round(mem.rss / MB * 100) / 100,
      externalMB: Math.round(mem.external / MB * 100) / 100,
      arrayBuffersMB: Math.round((mem.arrayBuffers || 0) / MB * 100) / 100,
    };
  }

  // ── CPU Sampling ────────────────────────────────────────────────

  /**
   * Sample CPU usage. Called periodically by the internal timer.
   * Computes % CPU over the sampling interval.
   */
  private sampleCpu(): void {
    if (!this.lastCpuUsage) {
      this.lastCpuUsage = process.cpuUsage();
      this.lastCpuTime = Date.now();
      return;
    }

    const now = Date.now();
    const elapsedMs = now - this.lastCpuTime;
    if (elapsedMs < 100) return; // Too soon

    const currentUsage = process.cpuUsage(this.lastCpuUsage);

    // cpuUsage returns microseconds; convert to percentage of elapsed wall time
    const elapsedUs = elapsedMs * 1000;
    const percentUser = (currentUsage.user / elapsedUs) * 100;
    const percentSystem = (currentUsage.system / elapsedUs) * 100;

    this.currentCpu = {
      timestamp: now,
      percentUser: Math.round(percentUser * 100) / 100,
      percentSystem: Math.round(percentSystem * 100) / 100,
      percentTotal: Math.round((percentUser + percentSystem) * 100) / 100,
    };

    this.lastCpuUsage = process.cpuUsage();
    this.lastCpuTime = now;
  }

  /**
   * Get the latest CPU snapshot.
   */
  getCpuSnapshot(): CpuSnapshot {
    return { ...this.currentCpu };
  }

  // ── Polling Loop Measurement ────────────────────────────────────

  /**
   * Record the duration of a polling loop iteration.
   * Use this to wrap each poll cycle:
   *
   *   const start = Date.now();
   *   await doPolling();
   *   perfMonitor.recordPollingCycle('ambient', Date.now() - start, 30000);
   *
   * cLaw: loop names are generic (e.g. 'ambient', 'clipboard', 'sentiment').
   * Never include user content in loop names or duration data.
   */
  recordPollingCycle(loopName: string, durationMs: number, intervalMs: number): void {
    let loop = this.pollingLoops.get(loopName);
    if (!loop) {
      loop = { durations: [], maxDurationMs: 0, callCount: 0, intervalMs };
      this.pollingLoops.set(loopName, loop);
    }

    loop.durations.push(durationMs);
    loop.callCount++;
    loop.intervalMs = intervalMs;

    if (durationMs > loop.maxDurationMs) {
      loop.maxDurationMs = durationMs;
    }

    // Keep history bounded
    if (loop.durations.length > POLLING_LOOP_HISTORY) {
      loop.durations = loop.durations.slice(-POLLING_LOOP_HISTORY);
    }
  }

  /**
   * Get metrics for all tracked polling loops.
   */
  getPollingMetrics(): PollingLoopMetric[] {
    const metrics: PollingLoopMetric[] = [];

    for (const [name, loop] of this.pollingLoops) {
      const avg = loop.durations.length > 0
        ? loop.durations.reduce((a, b) => a + b, 0) / loop.durations.length
        : 0;

      // Estimated CPU% = avg duration / interval * 100
      // e.g., if a loop takes 50ms every 30000ms → 0.17%
      const estimatedCpuPercent = loop.intervalMs > 0
        ? (avg / loop.intervalMs) * 100
        : 0;

      metrics.push({
        name,
        lastDurationMs: loop.durations[loop.durations.length - 1] ?? 0,
        avgDurationMs: Math.round(avg * 100) / 100,
        maxDurationMs: loop.maxDurationMs,
        callCount: loop.callCount,
        estimatedCpuPercent: Math.round(estimatedCpuPercent * 1000) / 1000,
        intervalMs: loop.intervalMs,
      });
    }

    return metrics;
  }

  // ── Activity State ──────────────────────────────────────────────

  /**
   * Update the current activity state.
   * Used for context in baselines and summaries.
   *
   * cLaw: state is a generic label, not derived from user content.
   */
  setActivityState(state: ActivityState): void {
    this.currentState = state;
  }

  getActivityState(): ActivityState {
    return this.currentState;
  }

  // ── Baselines ───────────────────────────────────────────────────

  /**
   * Capture a baseline measurement for the current activity state.
   * Stores memory + CPU snapshot tagged with the state.
   */
  captureBaseline(state?: ActivityState): PerfBaseline {
    const effectiveState = state ?? this.currentState;
    const baseline: PerfBaseline = {
      state: effectiveState,
      memory: this.sampleMemory(),
      cpu: this.getCpuSnapshot(),
      timestamp: Date.now(),
    };

    this.baselines.push(baseline);

    // Cap storage
    if (this.baselines.length > MAX_BASELINES) {
      this.baselines = this.baselines.slice(-MAX_BASELINES);
    }

    return baseline;
  }

  /**
   * Get all captured baselines, optionally filtered by state.
   */
  getBaselines(state?: ActivityState): PerfBaseline[] {
    if (state) {
      return this.baselines.filter(b => b.state === state);
    }
    return [...this.baselines];
  }

  // ── Summary & Warnings ──────────────────────────────────────────

  /**
   * Get a full performance summary.
   * Includes current metrics, baselines, and any warnings.
   *
   * cLaw: All data is system-level telemetry.
   * No user content, URLs, window titles, or message text.
   */
  getSummary(): PerfSummary {
    const warnings: string[] = [];

    // Check polling loops for CPU violations
    const pollingMetrics = this.getPollingMetrics();
    for (const loop of pollingMetrics) {
      if (loop.estimatedCpuPercent > POLLING_CPU_WARN_THRESHOLD) {
        warnings.push(
          `Polling loop '${loop.name}' exceeds ${POLLING_CPU_WARN_THRESHOLD}% CPU ` +
          `(estimated ${loop.estimatedCpuPercent.toFixed(3)}%)`
        );
      }
    }

    // Check memory thresholds
    const mem = this.sampleMemory();
    if (mem.rssMB > 1024) {
      warnings.push(`RSS memory is ${mem.rssMB.toFixed(0)}MB (>1GB)`);
    }
    if (mem.heapUsedMB > 512) {
      warnings.push(`Heap used is ${mem.heapUsedMB.toFixed(0)}MB (>512MB)`);
    }

    return {
      uptime: Math.round(process.uptime()),
      currentState: this.currentState,
      memory: mem,
      cpu: this.getCpuSnapshot(),
      startupMs: this.getStartupMs(),
      startupMilestones: [...this.milestones],
      pollingLoops: pollingMetrics,
      baselines: [...this.baselines],
      warnings,
    };
  }

  /**
   * Get a compact text summary for logging.
   */
  getTextSummary(): string {
    const mem = this.sampleMemory();
    const cpu = this.getCpuSnapshot();
    const loops = this.getPollingMetrics();

    const parts = [
      `CPU: ${cpu.percentTotal.toFixed(1)}%`,
      `RSS: ${mem.rssMB.toFixed(0)}MB`,
      `Heap: ${mem.heapUsedMB.toFixed(0)}/${mem.heapTotalMB.toFixed(0)}MB`,
      `State: ${this.currentState}`,
      `Uptime: ${Math.round(process.uptime())}s`,
    ];

    if (loops.length > 0) {
      const loopSummary = loops
        .map(l => `${l.name}:${l.avgDurationMs.toFixed(0)}ms/${l.estimatedCpuPercent.toFixed(3)}%`)
        .join(', ');
      parts.push(`Loops: ${loopSummary}`);
    }

    return parts.join(' | ');
  }
}

// ── Singleton export ─────────────────────────────────────────────────

export const perfMonitor = new PerfMonitor();
