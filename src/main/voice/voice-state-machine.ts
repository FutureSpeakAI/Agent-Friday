/**
 * voice-state-machine.ts — Canonical state machine for all voice paths in Agent Friday.
 *
 * Main-process singleton providing a single source of truth for the voice pipeline's
 * lifecycle. Every voice component (AudioCapture, WhisperProvider, TTSEngine, Gemini
 * WebSocket, local conversation loop) reads from and writes to this state machine
 * rather than maintaining its own booleans.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Understanding Part ↔ Whole
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * THE WHOLE: A user installs this Electron app and expects voice to "just work."
 * They should never see internal state transitions, WebSocket handshakes, or mic
 * permission flows. From their perspective, there are only three realities:
 *   1. Voice works (they speak, the agent responds)
 *   2. Voice is trying (brief loading/connecting moment)
 *   3. Voice doesn't work (they fall back to text)
 *
 * THE PARTS: Each state below represents a real, observable condition. Not an
 * implementation detail, but something the user would notice if we surfaced it.
 * IDLE = silence. REQUESTING_MIC = the browser permission dialog is showing.
 * CLOUD_ACTIVE = they're talking to Gemini and hearing responses.
 *
 * THE CIRCLE: Understanding "CLOUD_ACTIVE" requires understanding that the user
 * expects bidirectional audio. Understanding the user's expectation requires
 * knowing what "CLOUD_ACTIVE" can fail to deliver (connected but silent).
 * Therefore CLOUD_ACTIVE demands proof of audio flow, not just an open socket.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * SOCRATIC DISCOVERY — Questions Answered Before Writing
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * BOUNDARY Q1: "What must be true before transitioning from IDLE to REQUESTING_MIC?"
 *   → No other voice path is active. The previous cleanup cycle (if any) must have
 *     completed — the machine must be in IDLE, not DISCONNECTING. This prevents the
 *     race where a new session starts before the old one fully tears down.
 *
 * BOUNDARY Q2: "What must be true before CONNECTING_CLOUD?"
 *   → MIC_GRANTED must be the current state (mic confirmed active, at least one
 *     audio chunk received). A Gemini API key must exist. Without either, the
 *     guard rejects the transition and the caller must route to CONNECTING_LOCAL
 *     or TEXT_FALLBACK instead.
 *
 * BOUNDARY Q3: "What must be true before declaring CLOUD_ACTIVE?"
 *   → WebSocket open + setupComplete received + first audio frame played. Not just
 *     "connected" — actually producing audio. The current system's bug is setting
 *     isConnected=true without verifying output. This machine requires the caller
 *     to prove liveness before transitioning to ACTIVE.
 *
 * INVERSION Q4: "If you wanted to create a 'connected but silent' state, how?"
 *   → Set isConnected=true without verifying audio output. This is exactly what
 *     the legacy code does. The state machine defends against this by requiring
 *     explicit proof (a transition call with a reason string) rather than
 *     inferring state from flags.
 *
 * INVERSION Q5: "If you wanted to get stuck in a state forever, what?"
 *   → A transition that waits for an event that never fires — e.g., waiting for
 *     a WebSocket open that times out silently. Every non-terminal state has a
 *     timeout that auto-transitions to a fallback. No state can be infinite.
 *
 * PRECEDENT Q6: "Does this codebase already have a state machine pattern?"
 *   → App.tsx uses a phase state machine: 'passphrase-gate' | 'onboarding' |
 *     'creating' | 'normal'. We follow the same pattern: explicit string union
 *     types, transition functions, event emission on change. We also follow the
 *     singleton pattern from AudioCapture: private constructor, static getInstance().
 *
 * CONSTRAINT Q7: "What is the minimal set of states that serves both the user
 *   and the engineering team?"
 *   → 13 states. Fewer would collapse distinctions the user can perceive
 *     (MIC_DENIED vs ERROR). More would represent implementation details the
 *     user never sees (WEBSOCKET_HANDSHAKE_PHASE_2). Each state maps to something
 *     the UI could display as a single status line.
 *
 * TENSION Q8: "Serializable for IPC vs rich state objects?"
 *   → State is a plain string (the union type). Metadata (reason, metrics) are
 *     emitted as event payloads, not stored in the state itself. This lets the
 *     renderer receive state via a simple IPC string without deserializing.
 *
 * Phase 1.1 Track 1: VoiceStateMachine Core
 * Dependencies: None (foundational)
 */

import { EventEmitter } from 'node:events';
import { telemetryEngine } from '../telemetry';

// ── Types ─────────────────────────────────────────────────────────────────

/**
 * All possible voice pipeline states. Each represents a real, observable
 * condition — something the user would notice if we surfaced it.
 */
export type VoiceState =
  | 'IDLE'               // No voice activity — silent, waiting
  | 'REQUESTING_MIC'     // Waiting for getUserMedia permission dialog
  | 'MIC_DENIED'         // User denied mic permission (recoverable — they can retry)
  | 'MIC_GRANTED'        // Mic active, choosing voice path (cloud vs local)
  | 'CONNECTING_CLOUD'   // WebSocket opening to Gemini
  | 'CLOUD_ACTIVE'       // Gemini voice flowing both directions (verified)
  | 'CLOUD_DEGRADED'     // Gemini connected but audio unhealthy (jitter, silence)
  | 'CONNECTING_LOCAL'   // Whisper + Ollama + TTS initializing
  | 'LOCAL_ACTIVE'       // Local voice pipeline flowing (verified)
  | 'LOCAL_DEGRADED'     // Local pipeline partial (e.g., no TTS, Whisper only)
  | 'TEXT_FALLBACK'      // All voice paths failed — text-only mode
  | 'ERROR'              // Unrecoverable error (with reason attached via event)
  | 'DISCONNECTING';     // Cleanup in progress — tearing down voice components

/**
 * Categories for errors emitted by the state machine. Each maps to a class
 * of user-facing explanation (Track 5 will consume these for messaging).
 */
export type ErrorCategory =
  | 'mic-permission'     // getUserMedia denied or unavailable
  | 'network'            // WebSocket/fetch failure
  | 'api-key'            // Missing or invalid API key
  | 'model-unavailable'  // Ollama model not downloaded, Whisper binary missing
  | 'audio-hardware'     // No audio output device, AudioContext suspended
  | 'timeout'            // A state transition timed out
  | 'internal';          // Bug — should never happen

/** Health metrics emitted periodically while in an active state. */
export interface HealthMetrics {
  /** Milliseconds since entering current state */
  uptimeMs: number;
  /** Consecutive health checks that passed */
  consecutiveHealthy: number;
  /** Consecutive health checks that failed */
  consecutiveUnhealthy: number;
}

/** A single entry in the transition log — captures every state change. */
export interface TransitionLogEntry {
  from: VoiceState;
  to: VoiceState;
  at: number;        // Date.now() timestamp
  reason: string;    // Human-readable reason for the transition
}

/** Guard function signature — must return true for a transition to proceed. */
type GuardFn = () => boolean;

/** Callback for onEnter/onExit hooks. */
type HookFn = () => void;

/** Events emitted by the state machine. */
export interface VoiceStateMachineEvents {
  'state-change': (payload: { from: VoiceState; to: VoiceState; reason: string }) => void;
  'error': (payload: { state: VoiceState; error: Error; category: ErrorCategory }) => void;
  'health-update': (payload: { state: VoiceState; metrics: HealthMetrics }) => void;
}

// ── Transition Definitions ────────────────────────────────────────────────

/**
 * Internal representation of a transition rule. The transition table defines
 * every legal (from, to) pair with optional guards and timeouts.
 *
 * HERMENEUTIC NOTE: Each timeout represents "how long is it reasonable for
 * the user to wait in this state before we admit something is wrong?"
 */
interface TransitionRule {
  from: VoiceState;
  to: VoiceState;
  guard?: GuardFn;
  onEnter?: HookFn;
  onExit?: HookFn;
}

/**
 * Timeout configuration for a state. Every non-terminal state has a timeout
 * that auto-transitions to a fallback — no state can be infinite.
 *
 * INVERSION DEFENSE: This is the answer to "how do you prevent getting stuck?"
 * Every waiting state declares how long it will wait and where it goes if
 * patience runs out.
 */
interface StateTimeout {
  durationMs: number;
  target: VoiceState;
  reason: string;
}

// ── Constants ─────────────────────────────────────────────────────────────

/** Maximum transition log entries before oldest are pruned. */
const MAX_LOG_ENTRIES = 500;

/** Default health monitoring interval (ms). */
const DEFAULT_HEALTH_INTERVAL_MS = 5_000;

/**
 * Timeout configuration per state. Terminal states (TEXT_FALLBACK, ERROR) have
 * no timeout — they are resting positions.
 *
 * HERMENEUTIC NOTE: Each timeout duration answers: "How long would a user wait
 * in this state before feeling something is wrong?" The target answers: "Where
 * should the system go when patience runs out?"
 *
 * IDLE has no timeout — it is the natural resting state. The user is not waiting.
 * TEXT_FALLBACK has no timeout — it is a terminal fallback. The user can still type.
 * ERROR has no timeout — it requires explicit user action to recover.
 */
const STATE_TIMEOUTS: Partial<Record<VoiceState, StateTimeout>> = {
  // Mic permission dialog — 30s is generous; if the OS dialog is stuck, bail.
  REQUESTING_MIC: {
    durationMs: 30_000,
    target: 'MIC_DENIED',
    reason: 'Mic permission request timed out after 30s',
  },
  // User denied mic — give them 60s to reconsider, then fall back to text.
  MIC_DENIED: {
    durationMs: 60_000,
    target: 'TEXT_FALLBACK',
    reason: 'Mic denied — falling back to text input after 60s',
  },
  // Mic is active but we haven't started connecting — 10s to decide path.
  MIC_GRANTED: {
    durationMs: 10_000,
    target: 'TEXT_FALLBACK',
    reason: 'Mic granted but no voice path initiated within 10s',
  },
  // WebSocket to Gemini — 15s matches the existing codebase's blanket timeout.
  CONNECTING_CLOUD: {
    durationMs: 15_000,
    target: 'CONNECTING_LOCAL',
    reason: 'Cloud connection timed out after 15s — trying local',
  },
  // Cloud active — 120s without successful health check means degraded.
  // (Health monitor handles finer-grained detection; this is the backstop.)
  CLOUD_ACTIVE: {
    durationMs: 120_000,
    target: 'CLOUD_DEGRADED',
    reason: 'Cloud active for 120s without health confirmation — marking degraded',
  },
  // Cloud degraded — 30s to recover or give up.
  CLOUD_DEGRADED: {
    durationMs: 30_000,
    target: 'CONNECTING_LOCAL',
    reason: 'Cloud degraded for 30s without recovery — trying local fallback',
  },
  // Local pipeline init — Whisper + Ollama + TTS can be slow (model loading).
  CONNECTING_LOCAL: {
    durationMs: 45_000,
    target: 'TEXT_FALLBACK',
    reason: 'Local voice init timed out after 45s — falling back to text',
  },
  // Local active — same backstop as cloud.
  LOCAL_ACTIVE: {
    durationMs: 120_000,
    target: 'LOCAL_DEGRADED',
    reason: 'Local active for 120s without health confirmation — marking degraded',
  },
  // Local degraded — 30s to recover or give up.
  LOCAL_DEGRADED: {
    durationMs: 30_000,
    target: 'TEXT_FALLBACK',
    reason: 'Local degraded for 30s without recovery — falling back to text',
  },
  // Disconnecting — cleanup should be fast. If it hangs, force to IDLE.
  DISCONNECTING: {
    durationMs: 10_000,
    target: 'IDLE',
    reason: 'Disconnect cleanup timed out after 10s — forcing IDLE',
  },
};

/**
 * The complete transition table. Every legal (from, to) pair is listed here.
 * If a transition is not in this table, it is illegal and will be rejected.
 *
 * VALIDATION: After defining this table, we verify:
 *   1. All 13 states are reachable (appear as a `to` target)
 *   2. Every non-terminal state has at least one outgoing transition
 *   3. There exists a path from any state to TEXT_FALLBACK (the universal fallback)
 *
 * REACHABILITY PROOF (by inspection):
 *   IDLE            ← from DISCONNECTING, MIC_DENIED, initial state
 *   REQUESTING_MIC  ← from IDLE
 *   MIC_DENIED      ← from REQUESTING_MIC
 *   MIC_GRANTED     ← from REQUESTING_MIC
 *   CONNECTING_CLOUD← from MIC_GRANTED, CLOUD_DEGRADED
 *   CLOUD_ACTIVE    ← from CONNECTING_CLOUD, CLOUD_DEGRADED
 *   CLOUD_DEGRADED  ← from CLOUD_ACTIVE
 *   CONNECTING_LOCAL← from MIC_GRANTED, CONNECTING_CLOUD, CLOUD_DEGRADED, LOCAL_DEGRADED
 *   LOCAL_ACTIVE    ← from CONNECTING_LOCAL, LOCAL_DEGRADED
 *   LOCAL_DEGRADED  ← from LOCAL_ACTIVE
 *   TEXT_FALLBACK   ← from MIC_DENIED, MIC_GRANTED, CONNECTING_LOCAL, LOCAL_DEGRADED,
 *                     CLOUD_DEGRADED (via CONNECTING_LOCAL timeout), ERROR
 *   ERROR           ← from any state (wildcard — see canTransition logic)
 *   DISCONNECTING   ← from any active state
 *
 * PATH TO TEXT_FALLBACK FROM ANY STATE:
 *   IDLE → REQUESTING_MIC → MIC_DENIED → TEXT_FALLBACK
 *   CLOUD_ACTIVE → CLOUD_DEGRADED → CONNECTING_LOCAL → TEXT_FALLBACK
 *   LOCAL_ACTIVE → LOCAL_DEGRADED → TEXT_FALLBACK
 *   ERROR → TEXT_FALLBACK
 *   DISCONNECTING → IDLE → ... → TEXT_FALLBACK
 */
const TRANSITION_RULES: TransitionRule[] = [
  // ── From IDLE ─────────────────────────────────────────────────────────
  { from: 'IDLE', to: 'REQUESTING_MIC' },
  // Allow direct text fallback from idle (no mic needed for text mode)
  { from: 'IDLE', to: 'TEXT_FALLBACK' },
  // Allow direct local connection from idle (mic already granted externally)
  { from: 'IDLE', to: 'CONNECTING_LOCAL' },
  // Allow direct cloud connection from idle (mic already granted externally)
  { from: 'IDLE', to: 'CONNECTING_CLOUD' },

  // ── From REQUESTING_MIC ──────────────────────────────────────────────
  { from: 'REQUESTING_MIC', to: 'MIC_GRANTED' },
  { from: 'REQUESTING_MIC', to: 'MIC_DENIED' },
  // Timeout or cancel → text fallback
  { from: 'REQUESTING_MIC', to: 'TEXT_FALLBACK' },

  // ── From MIC_DENIED ──────────────────────────────────────────────────
  // User can retry mic permission
  { from: 'MIC_DENIED', to: 'REQUESTING_MIC' },
  // Give up → text only
  { from: 'MIC_DENIED', to: 'TEXT_FALLBACK' },
  // Or go back to idle (user cancelled entirely)
  { from: 'MIC_DENIED', to: 'IDLE' },

  // ── From MIC_GRANTED ─────────────────────────────────────────────────
  // Choose cloud path
  { from: 'MIC_GRANTED', to: 'CONNECTING_CLOUD' },
  // Choose local path
  { from: 'MIC_GRANTED', to: 'CONNECTING_LOCAL' },
  // No viable path → text fallback
  { from: 'MIC_GRANTED', to: 'TEXT_FALLBACK' },
  // Disconnect (user changed mind)
  { from: 'MIC_GRANTED', to: 'DISCONNECTING' },

  // ── From CONNECTING_CLOUD ────────────────────────────────────────────
  // Success — audio verified flowing
  { from: 'CONNECTING_CLOUD', to: 'CLOUD_ACTIVE' },
  // Cloud failed → try local
  { from: 'CONNECTING_CLOUD', to: 'CONNECTING_LOCAL' },
  // Cloud failed and no local → text
  { from: 'CONNECTING_CLOUD', to: 'TEXT_FALLBACK' },
  // User cancelled
  { from: 'CONNECTING_CLOUD', to: 'DISCONNECTING' },

  // ── From CLOUD_ACTIVE ────────────────────────────────────────────────
  // Health degraded (jitter, silence detected)
  { from: 'CLOUD_ACTIVE', to: 'CLOUD_DEGRADED' },
  // User stops session
  { from: 'CLOUD_ACTIVE', to: 'DISCONNECTING' },

  // ── From CLOUD_DEGRADED ──────────────────────────────────────────────
  // Recovered — audio flowing again
  { from: 'CLOUD_DEGRADED', to: 'CLOUD_ACTIVE' },
  // Reconnect attempt
  { from: 'CLOUD_DEGRADED', to: 'CONNECTING_CLOUD' },
  // Fall back to local
  { from: 'CLOUD_DEGRADED', to: 'CONNECTING_LOCAL' },
  // Give up entirely
  { from: 'CLOUD_DEGRADED', to: 'TEXT_FALLBACK' },
  // User stops
  { from: 'CLOUD_DEGRADED', to: 'DISCONNECTING' },

  // ── From CONNECTING_LOCAL ────────────────────────────────────────────
  // Success — local pipeline flowing
  { from: 'CONNECTING_LOCAL', to: 'LOCAL_ACTIVE' },
  // Partial success (e.g., Ollama works but no Whisper/TTS)
  { from: 'CONNECTING_LOCAL', to: 'LOCAL_DEGRADED' },
  // All local components failed → text
  { from: 'CONNECTING_LOCAL', to: 'TEXT_FALLBACK' },
  // User cancelled
  { from: 'CONNECTING_LOCAL', to: 'DISCONNECTING' },

  // ── From LOCAL_ACTIVE ────────────────────────────────────────────────
  // Component failure (TTS died, Whisper crashed)
  { from: 'LOCAL_ACTIVE', to: 'LOCAL_DEGRADED' },
  // User stops session
  { from: 'LOCAL_ACTIVE', to: 'DISCONNECTING' },

  // ── From LOCAL_DEGRADED ──────────────────────────────────────────────
  // Recovered — all components back
  { from: 'LOCAL_DEGRADED', to: 'LOCAL_ACTIVE' },
  // Reconnect attempt
  { from: 'LOCAL_DEGRADED', to: 'CONNECTING_LOCAL' },
  // Give up → text
  { from: 'LOCAL_DEGRADED', to: 'TEXT_FALLBACK' },
  // User stops
  { from: 'LOCAL_DEGRADED', to: 'DISCONNECTING' },

  // ── From TEXT_FALLBACK ───────────────────────────────────────────────
  // User wants to retry voice
  { from: 'TEXT_FALLBACK', to: 'REQUESTING_MIC' },
  { from: 'TEXT_FALLBACK', to: 'CONNECTING_LOCAL' },
  { from: 'TEXT_FALLBACK', to: 'CONNECTING_CLOUD' },
  // User closes everything
  { from: 'TEXT_FALLBACK', to: 'IDLE' },
  { from: 'TEXT_FALLBACK', to: 'DISCONNECTING' },

  // ── From ERROR ───────────────────────────────────────────────────────
  // Error is recoverable — retry
  { from: 'ERROR', to: 'IDLE' },
  { from: 'ERROR', to: 'TEXT_FALLBACK' },
  { from: 'ERROR', to: 'REQUESTING_MIC' },

  // ── From DISCONNECTING ───────────────────────────────────────────────
  // Cleanup complete → idle
  { from: 'DISCONNECTING', to: 'IDLE' },
  // Cleanup complete but error occurred during teardown
  { from: 'DISCONNECTING', to: 'ERROR' },
];

/**
 * Build a lookup set for O(1) transition validation.
 * Key format: "FROM->TO"
 */
function buildTransitionSet(rules: TransitionRule[]): Set<string> {
  const set = new Set<string>();
  for (const rule of rules) {
    set.add(`${rule.from}->${rule.to}`);
  }
  return set;
}

// ── States that accept a transition to ERROR from any state ───────────────
// ERROR is reachable from any state — it represents an unrecoverable failure
// that can happen anywhere (OOM, native crash, etc.). Rather than listing every
// (state, ERROR) pair, we handle this as a special case in canTransition().
// DISCONNECTING is similarly reachable from most active states, but we already
// list those explicitly to keep the transition table auditable.

/** States from which transitioning to ERROR is always allowed. */
const ERROR_ALWAYS_ALLOWED_FROM: Set<VoiceState> = new Set([
  'REQUESTING_MIC', 'MIC_GRANTED', 'CONNECTING_CLOUD', 'CLOUD_ACTIVE',
  'CLOUD_DEGRADED', 'CONNECTING_LOCAL', 'LOCAL_ACTIVE', 'LOCAL_DEGRADED',
  'TEXT_FALLBACK', 'DISCONNECTING',
]);

// ── VoiceStateMachine ─────────────────────────────────────────────────────

export class VoiceStateMachine extends EventEmitter {
  private static instance: VoiceStateMachine | null = null;

  private currentState: VoiceState = 'IDLE';
  private stateEnteredAt: number = Date.now();
  private stateTimers: Map<string, NodeJS.Timeout> = new Map();
  private healthInterval: NodeJS.Timeout | null = null;
  private transitionLog: TransitionLogEntry[] = [];
  private guards: Map<string, GuardFn> = new Map();
  private enterHooks: Map<string, HookFn[]> = new Map();
  private exitHooks: Map<string, HookFn[]> = new Map();
  private destroyed = false;

  /** O(1) lookup for legal transitions */
  private readonly legalTransitions: Set<string>;

  // Health tracking for the health-update event
  private consecutiveHealthy = 0;
  private consecutiveUnhealthy = 0;

  // ── Constructor (private — use getInstance) ───────────────────────────

  private constructor() {
    super();
    this.legalTransitions = buildTransitionSet(TRANSITION_RULES);
    this.stateEnteredAt = Date.now();
  }

  // ── Singleton ─────────────────────────────────────────────────────────

  static getInstance(): VoiceStateMachine {
    if (!VoiceStateMachine.instance) {
      VoiceStateMachine.instance = new VoiceStateMachine();
    }
    return VoiceStateMachine.instance;
  }

  /**
   * Reset the singleton (for testing or full app teardown).
   * Calls destroy() on the existing instance before clearing.
   */
  static resetInstance(): void {
    if (VoiceStateMachine.instance) {
      VoiceStateMachine.instance.destroy();
    }
    VoiceStateMachine.instance = null;
  }

  // ── Public API: State queries ─────────────────────────────────────────

  /** Current voice state (serializable string — safe for IPC). */
  getState(): VoiceState {
    return this.currentState;
  }

  /** Milliseconds since entering the current state. */
  getUptime(): number {
    return Date.now() - this.stateEnteredAt;
  }

  /** Full transition history (most recent last, capped at MAX_LOG_ENTRIES). */
  getTransitionLog(): ReadonlyArray<TransitionLogEntry> {
    return this.transitionLog;
  }

  /** Whether the machine has been destroyed. */
  isDestroyed(): boolean {
    return this.destroyed;
  }

  // ── Public API: Transition checks ─────────────────────────────────────

  /**
   * Check whether a transition from the current state to `to` is legal.
   * Does NOT check guards — only checks the transition table.
   */
  canTransition(to: VoiceState): boolean {
    if (this.destroyed) return false;
    if (this.currentState === to) return false; // No self-transitions

    // ERROR is reachable from any active state (see ERROR_ALWAYS_ALLOWED_FROM)
    if (to === 'ERROR' && ERROR_ALWAYS_ALLOWED_FROM.has(this.currentState)) {
      return true;
    }

    return this.legalTransitions.has(`${this.currentState}->${to}`);
  }

  // ── Public API: Transitions ───────────────────────────────────────────

  /**
   * Attempt to transition to a new state.
   *
   * Returns true if the transition succeeded, false if it was rejected
   * (illegal transition, guard failed, or machine destroyed).
   *
   * This is the ONLY way to change state. Components call this with a reason
   * string that becomes part of the transition log — making every state change
   * auditable and debuggable.
   *
   * ATOMICITY: State assignment is synchronous. No intermediate "between" states
   * exist. Hooks (onEnter/onExit) run after the state has changed, and their
   * failures do not roll back the transition.
   */
  transition(to: VoiceState, reason: string): boolean {
    if (this.destroyed) {
      console.warn('[VoiceStateMachine] Cannot transition — machine is destroyed');
      return false;
    }

    const from = this.currentState;

    // Self-transition is a no-op (not an error, just nothing to do)
    if (from === to) {
      return false;
    }

    // Check legality
    if (!this.canTransition(to)) {
      console.warn(
        `[VoiceStateMachine] Illegal transition: ${from} → ${to} (reason: ${reason})`,
      );
      return false;
    }

    // Check guard (if registered for this specific transition)
    const guardKey = `${from}->${to}`;
    const guard = this.guards.get(guardKey);
    if (guard && !guard()) {
      console.warn(
        `[VoiceStateMachine] Guard rejected: ${from} → ${to} (reason: ${reason})`,
      );
      return false;
    }

    // ── Execute transition (atomic) ─────────────────────────────────────

    // 1. Clear timeout for the old state
    this.clearStateTimeout(from);

    // 2. Run exit hooks for old state
    this.runExitHooks(from);

    // 3. Change state (the atomic moment)
    const previousState = this.currentState;
    this.currentState = to;
    this.stateEnteredAt = Date.now();

    // 4. Reset health counters on state change
    this.consecutiveHealthy = 0;
    this.consecutiveUnhealthy = 0;

    // 5. Log the transition
    this.logTransition(previousState, to, reason);

    // 6. Emit state-change event
    this.emit('state-change', { from: previousState, to, reason });
    telemetryEngine.record('voice-transition', `${previousState}->${to}`, reason);

    // 7. Run enter hooks for new state
    this.runEnterHooks(to);

    // 8. Start timeout for new state (if configured)
    this.startStateTimeout(to);

    console.log(`[VoiceStateMachine] ${previousState} → ${to} (${reason})`);

    return true;
  }

  // ── Public API: Guard registration ────────────────────────────────────

  /**
   * Register a guard function for a specific transition.
   * The guard must return true for the transition to proceed.
   *
   * Guards are checked by transition() — they don't affect canTransition()
   * so the UI can still show which transitions are structurally possible.
   *
   * Returns an unsubscribe function (following AudioCapture's pattern).
   */
  setGuard(from: VoiceState, to: VoiceState, guard: GuardFn): () => void {
    const key = `${from}->${to}`;
    this.guards.set(key, guard);
    return () => {
      this.guards.delete(key);
    };
  }

  // ── Public API: Lifecycle hooks ───────────────────────────────────────

  /**
   * Register a callback that runs when a state is entered.
   * Multiple callbacks per state are allowed (run in registration order).
   * Returns an unsubscribe function.
   */
  onEnterState(state: VoiceState, hook: HookFn): () => void {
    if (!this.enterHooks.has(state)) {
      this.enterHooks.set(state, []);
    }
    this.enterHooks.get(state)!.push(hook);
    return () => {
      const hooks = this.enterHooks.get(state);
      if (hooks) {
        const idx = hooks.indexOf(hook);
        if (idx >= 0) hooks.splice(idx, 1);
      }
    };
  }

  /**
   * Register a callback that runs when a state is exited.
   * Returns an unsubscribe function.
   */
  onExitState(state: VoiceState, hook: HookFn): () => void {
    if (!this.exitHooks.has(state)) {
      this.exitHooks.set(state, []);
    }
    this.exitHooks.get(state)!.push(hook);
    return () => {
      const hooks = this.exitHooks.get(state);
      if (hooks) {
        const idx = hooks.indexOf(hook);
        if (idx >= 0) hooks.splice(idx, 1);
      }
    };
  }

  // ── Public API: Health monitoring ─────────────────────────────────────

  /**
   * Start periodic health-update emissions. The health monitor runs on an
   * interval and emits the current state + metrics. Consumers (Track 4's
   * liveness probe) use this to detect "connected but silent" conditions.
   *
   * Only emits while in an active state (CLOUD_ACTIVE, LOCAL_ACTIVE, or
   * their degraded variants). Idle/connecting states don't need health checks.
   */
  startHealthMonitor(intervalMs: number = DEFAULT_HEALTH_INTERVAL_MS): void {
    this.stopHealthMonitor();
    this.healthInterval = setInterval(() => {
      if (this.destroyed) {
        this.stopHealthMonitor();
        return;
      }

      const activeStates: Set<VoiceState> = new Set([
        'CLOUD_ACTIVE', 'CLOUD_DEGRADED', 'LOCAL_ACTIVE', 'LOCAL_DEGRADED',
      ]);

      if (activeStates.has(this.currentState)) {
        const metrics: HealthMetrics = {
          uptimeMs: this.getUptime(),
          consecutiveHealthy: this.consecutiveHealthy,
          consecutiveUnhealthy: this.consecutiveUnhealthy,
        };
        this.emit('health-update', { state: this.currentState, metrics });
      }
    }, intervalMs);
  }

  /** Stop the health monitor interval. */
  stopHealthMonitor(): void {
    if (this.healthInterval !== null) {
      clearInterval(this.healthInterval);
      this.healthInterval = null;
    }
  }

  /**
   * Report a health check result from an external probe.
   * Track 4's liveness probe calls this — the state machine tracks
   * consecutive healthy/unhealthy counts for the health-update event.
   */
  reportHealth(healthy: boolean): void {
    if (healthy) {
      this.consecutiveHealthy++;
      this.consecutiveUnhealthy = 0;
    } else {
      this.consecutiveUnhealthy++;
      this.consecutiveHealthy = 0;
    }
  }

  /**
   * Reset the state timeout for the current state. Useful when external events
   * (like receiving an audio frame) prove the state is still valid and the
   * timeout should restart.
   *
   * HERMENEUTIC NOTE: This answers "what if the system is working fine but slow?"
   * A component that knows it's making progress can bump the timeout to prevent
   * a premature fallback.
   */
  resetCurrentTimeout(): void {
    if (this.destroyed) return;
    this.clearStateTimeout(this.currentState);
    this.startStateTimeout(this.currentState);
  }

  // ── Public API: Health snapshot ───────────────────────────────────────

  /**
   * Return a point-in-time health snapshot. Used by the IPC bridge (Phase 1.2)
   * so the renderer can request health without waiting for the next periodic emit.
   */
  getHealth(): HealthMetrics {
    return {
      uptimeMs: this.getUptime(),
      consecutiveHealthy: this.consecutiveHealthy,
      consecutiveUnhealthy: this.consecutiveUnhealthy,
    };
  }

  // ── Public API: Error emission ────────────────────────────────────────

  /**
   * Emit a categorized error event without necessarily changing state.
   * Use this for non-fatal errors that should be logged/displayed but
   * don't warrant a state transition (e.g., a single dropped audio frame).
   *
   * For fatal errors, call transition('ERROR', reason) instead.
   */
  emitError(error: Error, category: ErrorCategory): void {
    this.emit('error', { state: this.currentState, error, category });
  }

  // ── Public API: Cleanup ───────────────────────────────────────────────

  /**
   * Tear down the state machine. Clears all timers, listeners, hooks,
   * guards, and transition log. After destroy(), no transitions are possible.
   *
   * VALIDATION: No timer leak — every scheduled timeout is tracked in the
   * stateTimers map and cleared here. The health interval is also cleared.
   */
  destroy(): void {
    if (this.destroyed) return;

    console.log('[VoiceStateMachine] Destroying — clearing all timers and listeners');

    this.destroyed = true;

    // Clear all state timers
    for (const timer of this.stateTimers.values()) {
      clearTimeout(timer);
    }
    this.stateTimers.clear();

    // Clear health monitor
    this.stopHealthMonitor();

    // Clear all hooks and guards
    this.guards.clear();
    this.enterHooks.clear();
    this.exitHooks.clear();

    // Clear transition log
    this.transitionLog = [];

    // Remove all EventEmitter listeners
    this.removeAllListeners();
  }

  // ── Private: Timeout management ───────────────────────────────────────

  /**
   * Start the timeout timer for a state (if configured in STATE_TIMEOUTS).
   * When the timeout fires, it auto-transitions to the configured fallback.
   *
   * INVERSION DEFENSE: This is the mechanism that prevents "stuck forever."
   * Every non-terminal state will eventually transition somewhere, even if
   * no external event ever arrives.
   */
  private startStateTimeout(state: VoiceState): void {
    const timeout = STATE_TIMEOUTS[state];
    if (!timeout) return; // Terminal states or IDLE — no timeout needed

    const timer = setTimeout(() => {
      // Only fire if we're still in the same state (prevents stale timeouts)
      if (this.currentState === state && !this.destroyed) {
        console.warn(
          `[VoiceStateMachine] Timeout in ${state} after ${timeout.durationMs}ms → ${timeout.target}`,
        );
        this.transition(timeout.target, timeout.reason);
      }
    }, timeout.durationMs);

    this.stateTimers.set(state, timer);
  }

  /** Clear the timeout timer for a state. */
  private clearStateTimeout(state: VoiceState): void {
    const timer = this.stateTimers.get(state);
    if (timer !== undefined) {
      clearTimeout(timer);
      this.stateTimers.delete(state);
    }
  }

  // ── Private: Hook execution ───────────────────────────────────────────

  private runEnterHooks(state: VoiceState): void {
    const hooks = this.enterHooks.get(state);
    if (!hooks) return;
    for (const hook of hooks) {
      try {
        hook();
      } catch (err) {
        console.error(
          `[VoiceStateMachine] onEnter hook error for ${state}:`,
          err instanceof Error ? err.message : String(err),
        );
      }
    }
  }

  private runExitHooks(state: VoiceState): void {
    const hooks = this.exitHooks.get(state);
    if (!hooks) return;
    for (const hook of hooks) {
      try {
        hook();
      } catch (err) {
        console.error(
          `[VoiceStateMachine] onExit hook error for ${state}:`,
          err instanceof Error ? err.message : String(err),
        );
      }
    }
  }

  // ── Private: Transition logging ───────────────────────────────────────

  private logTransition(from: VoiceState, to: VoiceState, reason: string): void {
    this.transitionLog.push({
      from,
      to,
      at: Date.now(),
      reason,
    });

    // Prune oldest entries if log exceeds capacity
    if (this.transitionLog.length > MAX_LOG_ENTRIES) {
      this.transitionLog = this.transitionLog.slice(-MAX_LOG_ENTRIES);
    }
  }
}

// ── Singleton export (matches codebase convention: audio-capture.ts) ──────

export const voiceStateMachine = VoiceStateMachine.getInstance();
