/**
 * connection-stage-monitor.ts — Granular timeout tracking for the voice
 * connection lifecycle in Agent Friday.
 *
 * Phase 3.1, Track 3: Sub-stage Timeouts & Mic Permission
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Understanding Part ↔ Whole
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * THE WHOLE: The user's first voice experience must not waste their time.
 * Every second of waiting should show meaningful progress, and every failure
 * should be diagnosed precisely — not generically.
 *
 * THE PARTS: Six connection stages, each with its own timeout, user-facing
 * message, and failure guidance. Each stage represents a real checkpoint
 * the user implicitly waits through: mic permission, backend reachability,
 * model availability, connection handshake, setup confirmation, audio proof.
 *
 * THE CIRCLE: Understanding what "meaningful progress" means requires knowing
 * which stage the user is in. Understanding each stage requires knowing what
 * the user expects to see. A timeout on "mic-permission" means "check if a
 * dialog is hiding behind the window." A timeout on "model-validation" means
 * "run `ollama pull llama3.1:8b`." Same 5s wait, entirely different guidance.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * SOCRATIC DISCOVERY — Questions Answered Before Writing
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * BOUNDARY Q1: "What must be true before transitioning from mic-permission
 * to backend-probe?"
 *   → The renderer must have confirmed that getUserMedia succeeded (stream
 *     obtained and released). Not just "no error" — affirmative proof.
 *     This is the enterStage('backend-probe') precondition.
 *
 * INVERSION Q2: "If you wanted to waste 15 seconds of user time, what
 * would you do?"
 *   → Leave mic permission undetected behind the main window. Use a single
 *     blanket timeout with a generic "connection failed" message. This is
 *     exactly what the old code does. The monitor defends against this by
 *     giving mic-permission its own 30s budget with a specific message:
 *     "Looking for the microphone permission dialog? It may be behind
 *     this window."
 *
 * PRECEDENT Q3: "How does VoiceStateMachine handle timeouts?"
 *   → STATE_TIMEOUTS maps each VoiceState to a { durationMs, target, reason }.
 *     We follow the same pattern but at a finer granularity: sub-stages within
 *     a single VoiceState transition (e.g., the six steps within CONNECTING_CLOUD).
 *
 * CONSTRAINT Q4: "Why EventEmitter and not callback props?"
 *   → Multiple consumers need stage events: the UI status bar, the fallback
 *     manager, and the analytics logger. EventEmitter decouples producers
 *     from consumers, matching AudioCapture and VoiceStateMachine patterns.
 */

import { EventEmitter } from 'node:events';

// ── Types ─────────────────────────────────────────────────────────────────

/**
 * The six stages of establishing a voice connection. Ordered chronologically
 * from the user's perspective — each must complete before the next begins.
 *
 * HERMENEUTIC NOTE: Each stage name answers "what is the system doing right
 * now?" in terms the user could understand if we showed it to them.
 */
export type ConnectionStage =
  | 'mic-permission'       // Waiting for getUserMedia permission dialog
  | 'backend-probe'        // Checking if the backend (Ollama / Gemini) is reachable
  | 'model-validation'     // Verifying that the requested model is actually downloaded
  | 'connection-open'      // Opening the WebSocket or starting the local pipeline
  | 'setup-confirmation'   // Waiting for setup handshake (Gemini setupComplete)
  | 'first-audio-frame';   // Waiting for proof of audio flow (liveness check)

/**
 * Configuration for a single connection stage. Defines the timeout budget,
 * user-facing messaging for both progress and failure, and an optional
 * recovery action.
 *
 * INVERSION DEFENSE: failureMessage and failureAction ensure that every
 * timeout produces actionable guidance, not a generic "connection failed."
 */
export interface StageConfig {
  stage: ConnectionStage;
  timeoutMs: number;
  userMessage: string;       // What to show during this stage
  failureMessage: string;    // What to show if this stage times out
  failureAction?: string;    // Recovery suggestion (e.g., "Open System Settings")
}

/**
 * Progress snapshot for UI consumption. Provides the current stage,
 * how long we've been in it, and the total timeout budget.
 */
export interface StageProgress {
  stage: ConnectionStage;
  elapsed: number;
  timeout: number;
}

// ── Stage Configuration ───────────────────────────────────────────────────

/**
 * Per-stage timeout configuration. Each timeout duration answers:
 * "How long would a user wait at THIS step before feeling something is wrong?"
 *
 * mic-permission: 30s — OS dialogs can be slow, hidden behind windows, or
 *   require the user to alt-tab to find them. This is the longest budget
 *   because it depends on human interaction with the OS, not network speed.
 *
 * backend-probe: 5s — If Ollama or Gemini can't respond to a health ping
 *   in 5 seconds, something is wrong. Network latency + cold start should
 *   still be under 5s.
 *
 * model-validation: 5s — Listing models is a local disk operation (Ollama)
 *   or a key format check (Gemini). 5s is generous.
 *
 * connection-open: 5s — WebSocket open or pipeline start. If the backend
 *   is healthy and the model exists, the connection should open quickly.
 *
 * setup-confirmation: 5s — Gemini's setupComplete acknowledgment or local
 *   pipeline readiness signal. Depends on model loading, but the model
 *   was already validated in the previous stage.
 *
 * first-audio-frame: 5s — Proof of life. If the connection is open and
 *   setup is confirmed, audio should flow within seconds. If not, the
 *   connection is "connected but silent" — the exact failure we're defending
 *   against (see INVERSION Q4 in voice-state-machine.ts).
 */
const STAGE_CONFIGS: StageConfig[] = [
  {
    stage: 'mic-permission',
    timeoutMs: 30_000,
    userMessage: 'Requesting microphone access…',
    failureMessage: 'Microphone permission timed out. The permission dialog may be hidden behind this window.',
    failureAction: 'Check for a browser permission dialog, or open System Settings → Privacy → Microphone.',
  },
  {
    stage: 'backend-probe',
    timeoutMs: 5_000,
    userMessage: 'Checking backend availability…',
    failureMessage: 'Could not reach the voice backend.',
    failureAction: 'For local voice: ensure Ollama is running (`ollama serve`). For cloud: check your internet connection.',
  },
  {
    stage: 'model-validation',
    timeoutMs: 5_000,
    userMessage: 'Verifying model availability…',
    failureMessage: 'The requested model is not available.',
    failureAction: 'For local voice: run `ollama pull <model>` to download the model. For cloud: verify your API key in Settings.',
  },
  {
    stage: 'connection-open',
    timeoutMs: 5_000,
    userMessage: 'Opening voice connection…',
    failureMessage: 'Voice connection failed to open.',
    failureAction: 'Try again. If the problem persists, switch to the other voice path (local ↔ cloud) in Settings.',
  },
  {
    stage: 'setup-confirmation',
    timeoutMs: 5_000,
    userMessage: 'Completing voice setup…',
    failureMessage: 'Voice setup did not complete.',
    failureAction: 'The backend connected but did not confirm setup. Try disconnecting and reconnecting.',
  },
  {
    stage: 'first-audio-frame',
    timeoutMs: 5_000,
    userMessage: 'Waiting for audio…',
    failureMessage: 'Connected but no audio detected. The voice connection may be silent.',
    failureAction: 'Check that your microphone is not muted and that audio output is working.',
  },
];

/**
 * O(1) lookup from stage name to its configuration.
 */
const STAGE_CONFIG_MAP: Map<ConnectionStage, StageConfig> = new Map(
  STAGE_CONFIGS.map((cfg) => [cfg.stage, cfg])
);

// ── ConnectionStageMonitor ────────────────────────────────────────────────

/**
 * Tracks granular sub-stage progress during voice connection establishment.
 *
 * Unlike VoiceStateMachine (which tracks high-level states like CONNECTING_CLOUD),
 * this monitor tracks the six fine-grained steps WITHIN a connection attempt.
 * It provides per-stage timeouts with specific failure messages, enabling the
 * UI to show exactly what went wrong and how to fix it.
 *
 * SINGLETON PATTERN: Matches AudioCapture and VoiceStateMachine. Only one
 * connection attempt can be in progress at a time.
 *
 * LIFECYCLE:
 *   1. Caller enters stages sequentially via enterStage()
 *   2. Each stage auto-times-out if completeStage() is not called in time
 *   3. After all six stages complete, 'all-complete' fires
 *   4. destroy() cleans up all timers (no orphaned timeouts)
 *
 * EVENTS:
 *   'stage-enter':   { stage, userMessage }
 *   'stage-complete': { stage, durationMs }
 *   'stage-timeout':  { stage, failureMessage, failureAction }
 *   'all-complete':   (no payload) — all six stages completed successfully
 */
export class ConnectionStageMonitor extends EventEmitter {
  private static instance: ConnectionStageMonitor | null = null;

  /** The stage currently being tracked, or null if no connection attempt is active. */
  private currentStage: ConnectionStage | null = null;

  /** Timer for the current stage's timeout. Cleared on completeStage or enterStage. */
  private stageTimer: NodeJS.Timeout | null = null;

  /** Timestamp (Date.now()) when the current stage was entered. */
  private stageStartTime = 0;

  /** Set of stages that have been completed in this connection attempt. */
  private completedStages: Set<ConnectionStage> = new Set();

  /** Whether this monitor has been destroyed. Guards against post-destroy calls. */
  private destroyed = false;

  // ── Singleton ─────────────────────────────────────────────────────────

  private constructor() {
    super();
    this.on('error', (err) => {
      console.error(`[ConnectionStageMonitor] Unhandled error event:`, err instanceof Error ? err.message : err);
    });
  }

  /**
   * Get the singleton instance. Creates one if it doesn't exist.
   *
   * SOCRATIC NOTE: Why singleton? Because only one connection attempt can
   * be in-flight at a time. If a second attempt starts, it should reset
   * the monitor rather than creating a parallel tracker.
   */
  static getInstance(): ConnectionStageMonitor {
    if (!ConnectionStageMonitor.instance) {
      ConnectionStageMonitor.instance = new ConnectionStageMonitor();
    }
    return ConnectionStageMonitor.instance;
  }

  /**
   * Reset the singleton (for testing). Destroys the current instance
   * and allows a fresh one to be created.
   */
  static resetInstance(): void {
    if (ConnectionStageMonitor.instance) {
      ConnectionStageMonitor.instance.destroy();
      ConnectionStageMonitor.instance = null;
    }
  }

  // ── Public API ────────────────────────────────────────────────────────

  /**
   * Enter a new connection stage. Clears any previous stage timer and
   * starts the timeout clock for the new stage.
   *
   * BOUNDARY DEFENSE: The caller is responsible for ensuring preconditions
   * are met before entering a stage. This monitor tracks time, not logic.
   * For example, 'backend-probe' should only be entered after mic-permission
   * has been completed or skipped.
   *
   * Calling enterStage() while already in a stage implicitly completes the
   * previous stage (with duration measured) before entering the new one.
   */
  enterStage(stage: ConnectionStage): void {
    if (this.destroyed) {
      console.warn('[ConnectionStageMonitor] enterStage called after destroy — ignoring');
      return;
    }

    const config = STAGE_CONFIG_MAP.get(stage);
    if (!config) {
      console.error(`[ConnectionStageMonitor] Unknown stage: ${stage}`);
      return;
    }

    // If we're already in a stage, implicitly complete it before entering the new one
    if (this.currentStage !== null) {
      this.completeCurrentStageInternal();
    }

    this.currentStage = stage;
    this.stageStartTime = Date.now();

    // Start the timeout timer for this stage
    this.stageTimer = setTimeout(() => {
      this.onStageTimeout(stage);
    }, config.timeoutMs);

    this.emit('stage-enter', {
      stage,
      userMessage: config.userMessage,
    });
  }

  /**
   * Mark the current stage as successfully completed. Clears the timeout
   * timer and records the stage duration.
   *
   * If all six stages have been completed, emits 'all-complete'.
   */
  completeStage(): void {
    if (this.destroyed) return;
    if (this.currentStage === null) {
      console.warn('[ConnectionStageMonitor] completeStage called with no active stage');
      return;
    }

    this.completeCurrentStageInternal();

    // Check if all stages are done
    if (this.completedStages.size === STAGE_CONFIGS.length) {
      this.emit('all-complete');
    }
  }

  /**
   * Get a progress snapshot for the current stage. Returns null if no
   * stage is active.
   *
   * Used by the UI to show a progress indicator with elapsed time and
   * the total timeout budget for the current stage.
   */
  getProgress(): StageProgress | null {
    if (this.currentStage === null) return null;

    const config = STAGE_CONFIG_MAP.get(this.currentStage);
    if (!config) return null;

    return {
      stage: this.currentStage,
      elapsed: Date.now() - this.stageStartTime,
      timeout: config.timeoutMs,
    };
  }

  /**
   * Get the current stage, or null if no connection attempt is active.
   */
  getCurrentStage(): ConnectionStage | null {
    return this.currentStage;
  }

  /**
   * Get the set of stages completed in this connection attempt.
   */
  getCompletedStages(): ReadonlySet<ConnectionStage> {
    return this.completedStages;
  }

  /**
   * Reset the monitor for a new connection attempt. Clears all timers,
   * completed stages, and current stage. Does NOT destroy the instance.
   *
   * Call this at the start of each new connection attempt to ensure
   * clean state.
   */
  reset(): void {
    this.clearTimer();
    this.currentStage = null;
    this.stageStartTime = 0;
    this.completedStages.clear();
  }

  /**
   * Destroy the monitor. Clears all timers, removes all listeners, and
   * prevents future calls from having any effect.
   *
   * INVERSION DEFENSE: This is the answer to "what if the monitor is
   * abandoned mid-connection?" No orphaned timeouts, no ghost events.
   */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.clearTimer();
    this.currentStage = null;
    this.completedStages.clear();
    this.removeAllListeners();
  }

  // ── Private ───────────────────────────────────────────────────────────

  /**
   * Internal: complete the current stage, emit event, record it.
   */
  private completeCurrentStageInternal(): void {
    if (this.currentStage === null) return;

    const durationMs = Date.now() - this.stageStartTime;
    const completedStage = this.currentStage;

    this.clearTimer();
    this.completedStages.add(completedStage);

    // Clear current stage AFTER reading it for the event
    this.currentStage = null;

    this.emit('stage-complete', {
      stage: completedStage,
      durationMs,
    });
  }

  /**
   * Called when a stage's timeout expires. Emits 'stage-timeout' with
   * the stage-specific failure message and recovery action.
   *
   * HERMENEUTIC NOTE: This is where the "meaningful progress" promise
   * pays off. Instead of "connection timed out," the user sees exactly
   * which stage failed and what to do about it.
   */
  private onStageTimeout(stage: ConnectionStage): void {
    // Guard: only fire if we're still in the stage that timed out.
    // A race between completeStage() and the timer is possible.
    if (this.currentStage !== stage) return;
    if (this.destroyed) return;

    const config = STAGE_CONFIG_MAP.get(stage);
    if (!config) return;

    // Clear state — the stage failed, not completed
    this.clearTimer();
    this.currentStage = null;

    this.emit('stage-timeout', {
      stage,
      failureMessage: config.failureMessage,
      failureAction: config.failureAction,
    });
  }

  /**
   * Clear the current stage timer if one exists.
   */
  private clearTimer(): void {
    if (this.stageTimer !== null) {
      clearTimeout(this.stageTimer);
      this.stageTimer = null;
    }
  }
}

// ── Exports ───────────────────────────────────────────────────────────────

export { STAGE_CONFIGS, STAGE_CONFIG_MAP };
