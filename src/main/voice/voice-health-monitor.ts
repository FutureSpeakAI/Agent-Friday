/**
 * voice-health-monitor.ts — Silent Failure Detection & Auto-Recovery for Agent Friday.
 *
 * Detects when the voice pipeline appears healthy (isConnected=true, isListening=true)
 * but has actually stopped working (suspended AudioContext, dead mic, silent WebSocket,
 * hung Ollama). Implements an escalation ladder that auto-recovers silently on first
 * failure, shows a subtle indicator on second, and surfaces a visible error on third.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Understanding Part ↔ Whole
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * THE WHOLE: The user expects voice to "just work." When it silently breaks —
 * AudioContext suspends, mic goes dead, AI stops responding — the user sits in
 * silence wondering if the app is broken. The health monitor exists to detect
 * these invisible failures and either fix them silently or tell the user clearly.
 *
 * THE PARTS: Four built-in health checks, each targeting a specific silent
 * failure mode discovered by inversion analysis (P5.2 spec):
 *   a) AudioContext suspended → no output despite "connected" status
 *   b) WebSocket open but Gemini not sending audio → silent connection
 *   c) Mic streaming but no audio chunks arriving → muted/dead mic
 *   d) Ollama processing but response never arrives → hung model
 *
 * THE CIRCLE: Understanding *when* to escalate requires knowing *how* the
 * user experiences failure. A single AudioContext suspension is invisible
 * (auto-resume fixes it). But three in a row means something is systemically
 * wrong, and silence is no longer the right response. The escalation ladder
 * encodes this: auto-fix → hint → speak up.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * SOCRATIC DISCOVERY — Questions Answered Before Writing
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * INVERSION Q1: "If you wanted the system to appear working while broken, how?"
 *   → Set isConnected=true without verifying audio actually flows. The health
 *     monitor exists specifically to catch this lie by checking for actual audio
 *     data flow, not just connection status flags.
 *
 * TENSION Q1: "Auto-recovery is good, but repeated silent recoveries hide
 *   systemic issues. When should we surface vs. silently fix?"
 *   → Escalation ladder. 1st failure = silent (log only, auto-recover).
 *     2nd = subtle (small indicator, auto-recover). 3rd = visible (message to
 *     user, offer path switch). This balances smooth UX with honesty.
 *
 * BOUNDARY Q1: "How often should health checks run?"
 *   → Depends on the failure mode's urgency. A dead mic is urgent (5s) because
 *     the user is actively speaking and getting no response. AudioContext
 *     suspension is less urgent (10s) because the user notices silence.
 *     LLM timeout is even less urgent (30s) because the user expects some
 *     latency from the model.
 *
 * PRECEDENT Q1: "Does the codebase already have health monitoring?"
 *   → Yes. VoiceStateMachine has a HealthMetrics interface and emits
 *     'health-update' events. But it only counts consecutive healthy/unhealthy
 *     ticks — it doesn't define what "healthy" means or what to do about it.
 *     This module defines the actual health checks and the recovery logic.
 *
 * Phase 5.2 Track 5: Silent Failure Detection & Auto-Recovery
 * Dependencies: P1.1 (VoiceStateMachine), P5.1 (VoiceErrorClassifier)
 */

import { EventEmitter } from 'node:events';

// ── Types ─────────────────────────────────────────────────────────────────

/**
 * Escalation levels. The escalation ladder is central to the health monitor's
 * philosophy: start gentle, get louder.
 *
 * SOCRATIC NOTE: Why three levels and not two or four?
 * - Two (silent/visible) gives no middle ground — the first visible error
 *   feels abrupt after silence.
 * - Four adds a level that's hard to distinguish from adjacent ones.
 * - Three maps cleanly to "auto-fix / hint / speak up", which matches how
 *   humans handle repeated equipment failures (ignore / glance / intervene).
 */
export type EscalationLevel = 'silent' | 'subtle' | 'visible';

/**
 * A single health check definition. The health monitor runs these on intervals
 * and tracks consecutive failures per check.
 *
 * DESIGN NOTE: `check` returns a Promise<boolean> rather than throwing because
 * a "failure" is not necessarily an error — it's the absence of expected behavior
 * (no audio chunks, no AI response). Throwing would conflate "check couldn't run"
 * with "check ran and found a problem."
 */
export interface HealthCheck {
  /** Unique name for this check (used as key in failure tracking). */
  name: string;

  /**
   * The actual health check. Returns true if healthy, false if not.
   * Must not throw — if the check itself fails, it should return false.
   */
  check: () => Promise<boolean>;

  /**
   * How often to run this check (ms). Only runs while the monitor is active.
   *
   * SOCRATIC NOTE on interval choice: Shorter intervals catch failures faster
   * but consume more resources. The built-in checks use:
   * - 5s for mic liveness (urgent — user is actively speaking)
   * - 10s for audio output (moderate — user notices silence)
   * - 30s for LLM response (patient — models are slow)
   */
  intervalMs: number;

  /**
   * How many consecutive failures before the `onFailure` callback fires.
   * Default: 1 (fire on first failure). Set higher for flaky checks.
   */
  failureThreshold: number;

  /**
   * Called when consecutive failures reach or exceed `failureThreshold`.
   * Receives the current consecutive failure count for escalation logic.
   */
  onFailure: (consecutiveFailures: number) => void;
}

/**
 * The health status of a single check, as returned by `getHealthReport()`.
 */
export interface CheckHealthStatus {
  healthy: boolean;
  consecutiveFailures: number;
  lastCheckAt: number | null;
  escalationLevel: EscalationLevel;
}

/**
 * Events emitted by VoiceHealthMonitor.
 *
 * DESIGN NOTE: These events are the public API for the renderer process to
 * react to health changes. The monitor itself doesn't touch the UI — it
 * emits events that the IPC bridge forwards to the renderer.
 */
export interface VoiceHealthMonitorEvents {
  /**
   * A specific health check failed. Emitted on every failure (not just
   * when crossing the threshold) so that logging captures the full picture.
   */
  'health-check-failed': (payload: {
    checkName: string;
    consecutiveFailures: number;
    escalationLevel: EscalationLevel;
  }) => void;

  /**
   * The escalation level for a check changed (e.g., silent → subtle).
   * The renderer uses this to show/hide status indicators.
   */
  'escalation': (payload: {
    checkName: string;
    from: EscalationLevel;
    to: EscalationLevel;
    consecutiveFailures: number;
    message: string;
  }) => void;

  /**
   * A check that was failing has recovered (passed after failing).
   * The renderer uses this to clear error indicators.
   */
  'auto-recovered': (payload: {
    checkName: string;
    previousFailures: number;
    previousEscalation: EscalationLevel;
  }) => void;
}

// ── Constants ─────────────────────────────────────────────────────────────

/**
 * Escalation thresholds: how many consecutive failures map to each level.
 *
 * SOCRATIC NOTE: Why 1/2/3 and not 1/3/5?
 * - Voice failures are immediately noticeable (silence is loud). The user
 *   shouldn't have to wait through 5 silent failures before being told.
 * - The first failure gets a free pass (auto-recover). The second is a
 *   warning signal. The third is a pattern — something is wrong.
 * - Longer ladders (1/3/5) make sense for background services where the
 *   user isn't actively waiting. Voice is real-time and demands urgency.
 */
const ESCALATION_THRESHOLDS: Record<EscalationLevel, number> = {
  silent: 1,
  subtle: 2,
  visible: 3,
};

// ── VoiceHealthMonitor ────────────────────────────────────────────────────

export class VoiceHealthMonitor extends EventEmitter {
  private static instance: VoiceHealthMonitor | null = null;

  private checks: HealthCheck[] = [];
  private intervals: Map<string, NodeJS.Timeout> = new Map();
  private failureCounts: Map<string, number> = new Map();
  private lastCheckTimestamps: Map<string, number> = new Map();
  private running = false;
  private destroyed = false;

  // ── Constructor (private — use getInstance) ───────────────────────────

  private constructor() {
    super();
    this.on('error', (err) => {
      console.error(`[VoiceHealthMonitor] Unhandled error event:`, err instanceof Error ? err.message : err);
    });
  }

  // ── Singleton ─────────────────────────────────────────────────────────

  static getInstance(): VoiceHealthMonitor {
    if (!VoiceHealthMonitor.instance) {
      VoiceHealthMonitor.instance = new VoiceHealthMonitor();
    }
    return VoiceHealthMonitor.instance;
  }

  /**
   * Reset the singleton (for testing or full app teardown).
   * Stops all checks and clears state.
   */
  static resetInstance(): void {
    if (VoiceHealthMonitor.instance) {
      VoiceHealthMonitor.instance.destroy();
    }
    VoiceHealthMonitor.instance = null;
  }

  // ── Public API: Registration ──────────────────────────────────────────

  /**
   * Register a health check. Can be called before or after start().
   * If the monitor is already running, the check starts immediately.
   *
   * DESIGN NOTE: Registration is separate from start() so that components
   * can register their checks at initialization time (before voice is active)
   * and the monitor starts them when voice starts.
   */
  registerCheck(check: HealthCheck): void {
    if (this.destroyed) {
      console.warn('[VoiceHealthMonitor] Cannot register check — monitor is destroyed');
      return;
    }

    // Prevent duplicate registrations
    const existing = this.checks.findIndex((c) => c.name === check.name);
    if (existing >= 0) {
      this.unregisterCheck(check.name);
    }

    this.checks.push(check);
    this.failureCounts.set(check.name, 0);

    // If already running, start this check immediately
    if (this.running) {
      this.startCheck(check);
    }
  }

  /**
   * Unregister a health check by name. Stops its interval if running.
   */
  unregisterCheck(name: string): void {
    const idx = this.checks.findIndex((c) => c.name === name);
    if (idx >= 0) {
      this.checks.splice(idx, 1);
    }
    this.stopCheckInterval(name);
    this.failureCounts.delete(name);
    this.lastCheckTimestamps.delete(name);
  }

  // ── Public API: Lifecycle ─────────────────────────────────────────────

  /**
   * Start all registered health checks. Idempotent — calling start() when
   * already running is a no-op.
   */
  start(): void {
    if (this.destroyed || this.running) return;
    this.running = true;

    for (const check of this.checks) {
      this.startCheck(check);
    }
  }

  /**
   * Stop all health checks. Does not clear registrations — calling start()
   * again will resume all previously registered checks.
   */
  stop(): void {
    if (!this.running) return;
    this.running = false;

    for (const [name] of this.intervals) {
      this.stopCheckInterval(name);
    }

    // Reset failure counts on stop — fresh escalation ladder on next start
    for (const name of this.failureCounts.keys()) {
      this.failureCounts.set(name, 0);
    }
  }

  /**
   * Permanently destroy the monitor. Stops all checks, clears registrations,
   * removes all listeners.
   */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.stop();
    this.checks = [];
    this.failureCounts.clear();
    this.lastCheckTimestamps.clear();
    this.removeAllListeners();
  }

  // ── Public API: Queries ───────────────────────────────────────────────

  /** Whether the monitor is currently running health checks. */
  isRunning(): boolean {
    return this.running;
  }

  /**
   * Get a full health report — the status of every registered check.
   * Useful for debugging (exposed via IPC) and for the renderer to show
   * a comprehensive health dashboard.
   */
  getHealthReport(): Map<string, CheckHealthStatus> {
    const report = new Map<string, CheckHealthStatus>();

    for (const check of this.checks) {
      const failures = this.failureCounts.get(check.name) ?? 0;
      report.set(check.name, {
        healthy: failures === 0,
        consecutiveFailures: failures,
        lastCheckAt: this.lastCheckTimestamps.get(check.name) ?? null,
        escalationLevel: this.getEscalationLevel(failures),
      });
    }

    return report;
  }

  /**
   * Manually record a failure for a check (useful when a component detects
   * its own failure outside of the scheduled interval — e.g., a WebSocket
   * close event).
   */
  recordFailure(checkName: string): void {
    const check = this.checks.find((c) => c.name === checkName);
    if (!check) return;

    const count = (this.failureCounts.get(checkName) ?? 0) + 1;
    this.failureCounts.set(checkName, count);
    this.lastCheckTimestamps.set(checkName, Date.now());
    this.handleFailure(check, count);
  }

  /**
   * Manually record a recovery for a check (useful when a component
   * recovers outside of the scheduled interval).
   */
  recordRecovery(checkName: string): void {
    const previousFailures = this.failureCounts.get(checkName) ?? 0;
    if (previousFailures === 0) return; // Already healthy

    const previousEscalation = this.getEscalationLevel(previousFailures);
    this.failureCounts.set(checkName, 0);

    this.emit('auto-recovered', {
      checkName,
      previousFailures,
      previousEscalation,
    });
  }

  // ── Internal: Check Execution ─────────────────────────────────────────

  private startCheck(check: HealthCheck): void {
    // Clear any existing interval for this check
    this.stopCheckInterval(check.name);

    const interval = setInterval(() => {
      void this.runCheck(check);
    }, check.intervalMs);

    // Prevent the interval from keeping the process alive
    if (typeof interval === 'object' && 'unref' in interval) {
      interval.unref();
    }

    this.intervals.set(check.name, interval);
  }

  private stopCheckInterval(name: string): void {
    const interval = this.intervals.get(name);
    if (interval) {
      clearInterval(interval);
      this.intervals.delete(name);
    }
  }

  /**
   * Run a single health check and handle the result.
   *
   * SOCRATIC NOTE on the escalation ladder:
   * - Failure 1 (silent): Auto-recover attempt only. The user doesn't know.
   *   Why? Because most failures are transient. A single AudioContext suspension
   *   or network hiccup fixes itself. Bothering the user would be noise.
   * - Failure 2 (subtle): Show a small status indicator. The user can see
   *   something is off but isn't interrupted. Why? Because two consecutive
   *   failures suggest a pattern, and the user deserves a heads-up.
   * - Failure 3+ (visible): Show a message with recovery options. The user
   *   must engage. Why? Because three failures means auto-recovery isn't
   *   working and the user needs to take action.
   */
  private async runCheck(check: HealthCheck): Promise<void> {
    if (!this.running || this.destroyed) return;

    let healthy: boolean;
    try {
      healthy = await check.check();
    } catch {
      // If the check itself throws, treat it as unhealthy.
      // SOCRATIC NOTE: We catch here rather than letting checks throw because
      // a broken check shouldn't crash the monitor. It should be treated as
      // the same as "check returned false" — something is wrong.
      healthy = false;
    }

    this.lastCheckTimestamps.set(check.name, Date.now());

    if (healthy) {
      // ── Recovery path ───────────────────────────────────────────────
      const previousFailures = this.failureCounts.get(check.name) ?? 0;
      if (previousFailures > 0) {
        const previousEscalation = this.getEscalationLevel(previousFailures);
        this.failureCounts.set(check.name, 0);

        this.emit('auto-recovered', {
          checkName: check.name,
          previousFailures,
          previousEscalation,
        });
      }
    } else {
      // ── Failure path ────────────────────────────────────────────────
      const count = (this.failureCounts.get(check.name) ?? 0) + 1;
      this.failureCounts.set(check.name, count);
      this.handleFailure(check, count);
    }
  }

  /**
   * Handle a failure by emitting events and potentially escalating.
   */
  private handleFailure(check: HealthCheck, consecutiveFailures: number): void {
    const escalationLevel = this.getEscalationLevel(consecutiveFailures);

    // Always emit the failure event (for logging / telemetry)
    this.emit('health-check-failed', {
      checkName: check.name,
      consecutiveFailures,
      escalationLevel,
    });

    // Check for escalation (did we cross a threshold?)
    const previousLevel = this.getEscalationLevel(consecutiveFailures - 1);
    if (escalationLevel !== previousLevel) {
      this.emit('escalation', {
        checkName: check.name,
        from: previousLevel,
        to: escalationLevel,
        consecutiveFailures,
        message: this.getEscalationMessage(check.name, escalationLevel),
      });
    }

    // Fire the check's onFailure callback if threshold is met
    if (consecutiveFailures >= check.failureThreshold) {
      try {
        check.onFailure(consecutiveFailures);
      } catch (err) {
        // onFailure callbacks should not crash the monitor
        console.error(
          `[VoiceHealthMonitor] onFailure callback for "${check.name}" threw:`,
          err instanceof Error ? err.message : err,
        );
      }
    }
  }

  // ── Internal: Escalation ──────────────────────────────────────────────

  /**
   * Map a consecutive failure count to an escalation level.
   *
   * Escalation thresholds:
   *   0 failures     → 'silent' (healthy — no escalation)
   *   1 failure      → 'silent' (first failure — auto-recover only)
   *   2 failures     → 'subtle' (pattern forming — hint to user)
   *   3+ failures    → 'visible' (confirmed problem — tell user)
   */
  private getEscalationLevel(failures: number): EscalationLevel {
    if (failures >= ESCALATION_THRESHOLDS.visible) return 'visible';
    if (failures >= ESCALATION_THRESHOLDS.subtle) return 'subtle';
    return 'silent';
  }

  /**
   * Human-readable escalation message for the renderer to display.
   */
  private getEscalationMessage(checkName: string, level: EscalationLevel): string {
    switch (level) {
      case 'silent':
        return `Health check "${checkName}" failed — attempting auto-recovery.`;
      case 'subtle':
        return `Voice is experiencing intermittent issues.`;
      case 'visible':
        return `Voice is having trouble. You may want to switch to text input.`;
    }
  }
}

// ── Built-in Health Check Factories ───────────────────────────────────────
//
// These factories create HealthCheck objects for common voice failure modes.
// They are not registered automatically — the voice pipeline registers them
// when appropriate (e.g., audioOutputLiveness only when in CLOUD_ACTIVE or
// LOCAL_ACTIVE state).
//
// DESIGN NOTE: Factories accept dependencies as parameters rather than
// importing singletons. This makes them testable (inject mocks) and avoids
// circular dependencies.

/**
 * Audio Output Liveness — detects when AudioContext is suspended or closed.
 *
 * Runs every 10s during active voice states. If AudioContext.state is not
 * 'running', attempts to resume it. If resume fails, reports failure.
 *
 * WHY THIS CHECK EXISTS (Inversion analysis):
 * Without it, the app can show "connected" and "listening" while AudioContext
 * is silently suspended (browser policy, tab backgrounding, OS audio reset).
 * The user speaks, the AI processes, but no audio comes out. The user thinks
 * the app is frozen.
 *
 * @param getAudioContextState - Returns the current AudioContext.state
 * @param resumeAudioContext - Attempts to resume a suspended AudioContext
 */
export function createAudioOutputLivenessCheck(
  getAudioContextState: () => AudioContextState,
  resumeAudioContext: () => Promise<void>,
): HealthCheck {
  return {
    name: 'audio-output-liveness',
    intervalMs: 10_000,
    failureThreshold: 1,
    check: async () => {
      const state = getAudioContextState();
      if (state === 'running') return true;

      // Auto-recovery attempt: try to resume
      try {
        await resumeAudioContext();
        // Check again after resume attempt
        return getAudioContextState() === 'running';
      } catch {
        return false;
      }
    },
    onFailure: (consecutiveFailures) => {
      console.warn(
        `[HealthCheck/audio-output-liveness] AudioContext not running ` +
        `(${consecutiveFailures} consecutive failures)`,
      );
    },
  };
}

/**
 * Audio Roundtrip — detects when the AI is connected but not responding.
 *
 * This is an event-driven check, not interval-based. The caller starts a timer
 * when user speech ends and expects AI audio within the timeout. If no audio
 * arrives, the check fails.
 *
 * WHY THIS CHECK EXISTS (Inversion analysis):
 * The WebSocket can be open and "setupComplete" received, but Gemini may have
 * stopped sending audio (server-side issue, model stuck, quota silently
 * exhausted). Without this check, the user finishes speaking and hears nothing
 * — with no indication that anything is wrong.
 *
 * DESIGN NOTE: Unlike other checks, this one uses a longer interval (10s)
 * that only ticks after user speech. It's not a periodic heartbeat — it's
 * a response-expected-within-deadline check.
 *
 * @param hasReceivedAudioSinceLastUtterance - Returns true if AI audio has been
 *   received since the last user utterance ended. Reset to false when user starts
 *   speaking; set to true when first AI audio frame arrives.
 */
export function createAudioRoundtripCheck(
  hasReceivedAudioSinceLastUtterance: () => boolean,
): HealthCheck {
  return {
    name: 'audio-roundtrip',
    intervalMs: 10_000,
    failureThreshold: 1,
    check: async () => {
      return hasReceivedAudioSinceLastUtterance();
    },
    onFailure: (consecutiveFailures) => {
      console.warn(
        `[HealthCheck/audio-roundtrip] No AI audio response after user speech ` +
        `(${consecutiveFailures} consecutive failures)`,
      );
    },
  };
}

/**
 * Mic Stream Liveness — detects when the microphone stops producing audio.
 *
 * Runs every 5s during active voice states. Checks if any audio chunk has been
 * received from the mic in the last 5 seconds.
 *
 * WHY THIS CHECK EXISTS (Inversion analysis):
 * The MediaStream can be "active" and the track can be "live" while producing
 * zero audio data (muted at OS level, hardware failure, driver crash). The
 * VAD sees silence and never triggers, so the app appears to be "listening"
 * but never hears anything.
 *
 * BOUNDARY NOTE: The 5s interval matches the built-in check frequency from
 * the P5.2 spec. It's aggressive because a dead mic is the most frustrating
 * silent failure — the user is actively speaking and nothing happens.
 *
 * @param hasReceivedMicDataRecently - Returns true if at least one audio chunk
 *   was received from the microphone in the last N milliseconds (caller defines N).
 */
export function createMicStreamLivenessCheck(
  hasReceivedMicDataRecently: () => boolean,
): HealthCheck {
  return {
    name: 'mic-stream-liveness',
    intervalMs: 5_000,
    failureThreshold: 1,
    check: async () => {
      return hasReceivedMicDataRecently();
    },
    onFailure: (consecutiveFailures) => {
      console.warn(
        `[HealthCheck/mic-stream-liveness] No mic audio data received recently ` +
        `(${consecutiveFailures} consecutive failures). Mic may be muted or disconnected.`,
      );
    },
  };
}

/**
 * LLM Response Liveness — detects when Ollama stops responding.
 *
 * Runs every 30s during LOCAL_ACTIVE state. Checks if Ollama has responded
 * to the most recent prompt within a reasonable time.
 *
 * WHY THIS CHECK EXISTS (Inversion analysis):
 * Ollama can accept a prompt via HTTP and then hang indefinitely (model OOM,
 * GPU lock, corrupted KV cache). The existing 90s timeout in the codebase is
 * far too generous — a user waiting 90 seconds in silence will have long since
 * given up. We check at 30s.
 *
 * TENSION NOTE: 30s is still long. But LLM inference on local hardware (CPU-only
 * with an 8B model) can legitimately take 10-20s for long responses. We don't
 * want false positives from slow-but-working inference. 30s balances patience
 * with detection speed.
 *
 * @param isWaitingForLlmResponse - Returns true if a prompt was sent and no
 *   response has arrived yet.
 * @param getTimeSincePromptMs - Returns milliseconds since the last prompt was sent.
 */
export function createLlmResponseLivenessCheck(
  isWaitingForLlmResponse: () => boolean,
  getTimeSincePromptMs: () => number,
): HealthCheck {
  return {
    name: 'llm-response-liveness',
    intervalMs: 30_000,
    failureThreshold: 1,
    check: async () => {
      // If we're not waiting for a response, the check passes trivially
      if (!isWaitingForLlmResponse()) return true;

      // If we are waiting, check how long
      const elapsed = getTimeSincePromptMs();
      // 30s timeout — see TENSION NOTE above for justification
      return elapsed < 30_000;
    },
    onFailure: (consecutiveFailures) => {
      console.warn(
        `[HealthCheck/llm-response-liveness] LLM has not responded within 30s ` +
        `(${consecutiveFailures} consecutive failures). Ollama may be hung.`,
      );
    },
  };
}
