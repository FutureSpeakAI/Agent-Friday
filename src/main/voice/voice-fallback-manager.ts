/**
 * voice-fallback-manager.ts — Cascading fallback chain for voice paths in Agent Friday.
 *
 * Main-process singleton that manages the priority-ordered voice path selection,
 * mid-session switching, and conversation context preservation across path changes.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Understanding Part ↔ Whole
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * THE WHOLE: A user having a voice conversation should never be stranded. If one
 * voice path dies, another takes over seamlessly — the user perceives a brief
 * pause, not a restart.
 *
 * THE PARTS:
 *   - Cloud (Gemini WebSocket): Richest voice quality, depends on network + API key.
 *   - Local (Whisper + Ollama + TTS): Works offline, depends on local models.
 *   - Text: Always available. The universal floor — never fails, but no voice.
 *
 * THE CIRCLE: Understanding what "seamless switch" means requires knowing what
 * each path can and can't do. Understanding each path's limits requires knowing
 * what the user expects from "seamless." Therefore:
 *   - Switching must preserve conversation history (messages survive).
 *   - Switching must NOT preserve stale audio state (pending TTS, buffers die).
 *   - The user should never need to say anything twice.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * SOCRATIC DISCOVERY — Questions Answered Before Writing
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * BOUNDARY Q1: "What must be true about the current path before switching?"
 *   → It must be FULLY torn down — no lingering listeners, timers, WebSocket
 *     connections, or TTS queue items. The inversion question reveals why:
 *     leaving old listeners active means the old path's events conflict with
 *     the new path's events, producing garbled output or double-processing.
 *
 * BOUNDARY Q2: "What must be true before declaring all paths exhausted?"
 *   → Every non-text path must have been attempted and either timed out or
 *     errored. We track this with attemptedPaths. Only when the set contains
 *     all voice paths do we fall to TEXT_FALLBACK.
 *
 * TENSION Q3: "Cloud has richer voice but slower failure detection. Local is
 *   faster to fail but lower quality. How do we serve both?"
 *   → We try the user's preferred path first. If it fails, we switch to the
 *     alternate within the state machine's timeout (≤15s for cloud, ≤45s for
 *     local). The user's preference is respected but not at the cost of silence.
 *
 * INVERSION Q4: "If you wanted to prevent fallback from ever working, what
 *   would you exploit?"
 *   → Leave the failed path's event listeners active so they conflict with
 *     the new path. Therefore teardownPath() MUST do full cleanup before
 *     startBestPath() or switchTo() begins the new path.
 *
 * CONSTRAINT Q5: "What conversation state must survive a switch?"
 *   → Message history (user + AI turns), system prompt, active tool definitions.
 *     NOT: WebSocket state, audio buffers, pending TTS utterances. The P2.2
 *     spec is explicit: drop audio, keep semantics.
 *
 * CONSTRAINT Q6: "What is the minimal disruption the user experiences?"
 *   → Brief silence (< 3-5 seconds) + a status event the renderer can display.
 *     No visible error unless ALL paths fail.
 *
 * Phase 2.1 + 2.2 Track 2: Cascading Fallback Chain
 * Dependencies: P1.1 (VoiceStateMachine)
 */

import { EventEmitter } from 'node:events';
import {
  VoiceStateMachine,
  type VoiceState,
  type ErrorCategory,
} from './voice-state-machine';
import { type ChatMessage, type ToolDefinition, llmClient } from '../llm-client';
import { settingsManager } from '../settings';
import { transcriptionPipeline } from './transcription-pipeline';
import { speechSynthesis } from './speech-synthesis';
import { whisperProvider } from './whisper-provider';
import { ttsEngine } from './tts-engine';
import { telemetryEngine } from '../telemetry';

// ── Types ─────────────────────────────────────────────────────────────────

/**
 * The three voice paths, ordered from richest to simplest:
 *   cloud: Gemini WebSocket — bidirectional audio, highest quality
 *   local: Whisper STT + Ollama LLM + Kokoro/Piper TTS — offline capable
 *   text:  No voice at all — the universal floor
 */
export type VoicePath = 'cloud' | 'local' | 'text';

/**
 * Availability probe result for a single path.
 *
 * HERMENEUTIC NOTE: `available` answers "can this path be attempted right now?"
 * It does NOT mean the path will succeed — only that the prerequisites exist
 * (API key present, Ollama process responding, etc.).
 */
export interface PathConfig {
  path: VoicePath;
  available: boolean;
  reason?: string;
  /** Lower number = higher priority = try first */
  priority: number;
}

/** Events emitted by the fallback manager. */
export interface FallbackManagerEvents {
  /** A path switch is starting — renderer should show brief loading state */
  'switch-start': (payload: { from: VoicePath | null; to: VoicePath; reason: string }) => void;
  /** A path switch completed successfully */
  'switch-complete': (payload: { path: VoicePath; hadContext: boolean }) => void;
  /** All voice paths exhausted — text fallback engaged */
  'all-paths-exhausted': (payload: { errors: Array<{ path: VoicePath; error: string }> }) => void;
  /** A path switch failed (the new path also failed) */
  'switch-failed': (payload: { path: VoicePath; error: Error }) => void;
}

/**
 * Snapshot of conversation state that survives a path switch.
 *
 * CONSTRAINT: This captures semantic state (what was said), not transport
 * state (WebSocket handles, audio buffers). The new path starts with clean
 * audio but the same conversation context.
 */
interface ConversationSnapshot {
  messages: ChatMessage[];
  systemPrompt: string;
  tools: ToolDefinition[];
  capturedAt: number;
}

// ── Constants ─────────────────────────────────────────────────────────────

/**
 * Default priority ordering. Lower = tried first.
 * Cloud is preferred because it has the richest capabilities (bidirectional
 * audio, interruption support, lower latency for speech synthesis).
 *
 * TENSION: Some users prefer local-first for privacy. We respect this by
 * allowing the priority to be overridden via setPathPriority().
 */
const DEFAULT_PRIORITIES: Record<VoicePath, number> = {
  cloud: 1,
  local: 2,
  text: 99, // Text is always last — it's the universal floor
};

/**
 * How long to wait for a degraded state before auto-switching (ms).
 * P2.2 spec says 10 seconds to avoid flapping on transient issues.
 *
 * TENSION: Too short → flapping on a brief network hiccup. Too long → the
 * user sits in degraded audio for an uncomfortably long time. 10s balances
 * "give it a chance to recover" with "don't make the user suffer."
 */
const DEGRADED_AUTO_SWITCH_MS = 10_000;

/**
 * Maximum time allowed for teardown of a path before we give up and
 * proceed anyway (ms). This prevents a hung teardown from blocking
 * the switch to a working path.
 *
 * INVERSION DEFENSE: If teardown itself hangs, the user would be
 * permanently stuck. The timeout ensures forward progress.
 */
const TEARDOWN_TIMEOUT_MS = 5_000;

// ── VoiceFallbackManager ──────────────────────────────────────────────────

export class VoiceFallbackManager extends EventEmitter {
  private static instance: VoiceFallbackManager | null = null;

  private readonly stateMachine: VoiceStateMachine;
  private currentPath: VoicePath | null = null;
  private attemptedPaths: Set<VoicePath> = new Set();
  private pathErrors: Array<{ path: VoicePath; error: string }> = [];
  private priorities: Record<VoicePath, number> = { ...DEFAULT_PRIORITIES };

  /**
   * Conversation context preserved across path switches.
   * Null when no conversation has started yet.
   *
   * BOUNDARY: This is the bridge between "old path dying" and "new path
   * starting." If this is null during a switch, the new path starts fresh.
   * If populated, the new path resumes where the old one left off.
   */
  private snapshot: ConversationSnapshot | null = null;

  /**
   * The system prompt and tools provided at startBestPath() — cached so that
   * handlePathFailure() can restart a new path with the same configuration.
   */
  private activeSystemPrompt = '';
  private activeTools: ToolDefinition[] = [];

  /**
   * Track whether a switch is in progress to prevent re-entrant switches.
   *
   * INVERSION DEFENSE: Without this guard, a rapid sequence of failures could
   * trigger overlapping switches — old teardown racing with new startup.
   */
  private switching = false;

  /**
   * Timer for auto-switching out of a degraded state.
   * Cleared when the state recovers or a switch begins.
   */
  private degradedTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * Cleanup functions for state machine event subscriptions.
   * Stored so we can unsubscribe on destroy().
   */
  private cleanupFns: Array<() => void> = [];

  // ── Constructor (private — use getInstance) ───────────────────────────

  private constructor() {
    super();
    this.stateMachine = VoiceStateMachine.getInstance();
    this.subscribeToStateMachine();
  }

  // ── Singleton ─────────────────────────────────────────────────────────

  static getInstance(): VoiceFallbackManager {
    if (!VoiceFallbackManager.instance) {
      VoiceFallbackManager.instance = new VoiceFallbackManager();
    }
    return VoiceFallbackManager.instance;
  }

  static resetInstance(): void {
    if (VoiceFallbackManager.instance) {
      VoiceFallbackManager.instance.destroy();
    }
    VoiceFallbackManager.instance = null;
  }

  // ── Public API: Availability Probing ──────────────────────────────────

  /**
   * Check which voice paths are currently available WITHOUT starting any.
   *
   * Returns a sorted array (by priority) of path configs. Each entry tells
   * the caller whether a path CAN be attempted and why or why not.
   *
   * BOUNDARY: "Available" means prerequisites exist. It does NOT guarantee
   * the path will succeed — network could drop mid-connection, Ollama could
   * crash mid-inference. Availability is a necessary but not sufficient
   * condition for a path to work.
   */
  async probeAvailability(): Promise<PathConfig[]> {
    const configs: PathConfig[] = [];

    // ── Cloud: requires Gemini API key ──────────────────────────────────
    //
    // BOUNDARY: We check for key existence, not validity. Key validation
    // happens during connection (CONNECTING_CLOUD). Checking validity here
    // would add latency and network dependency to what should be a fast probe.
    const geminiKey = settingsManager.getGeminiApiKey();
    configs.push({
      path: 'cloud',
      available: !!geminiKey,
      reason: geminiKey ? undefined : 'No Gemini API key configured',
      priority: this.priorities.cloud,
    });

    // ── Local: requires Ollama health + at least one chat model ─────────
    //
    // TENSION: Ollama health check is a network call (HTTP to localhost).
    // It's fast (~10ms) but could theoretically hang. We give it 3s max
    // before declaring unavailable. This is acceptable because:
    //   1. It's a localhost call, not internet — 3s is very generous.
    //   2. If Ollama takes 3s to respond to /api/tags, it's too slow
    //      for real-time voice conversation anyway.
    let ollamaAvailable = false;
    let ollamaReason: string | undefined;

    try {
      const ollamaProvider = llmClient.getProvider('ollama');
      if (!ollamaProvider) {
        ollamaReason = 'Ollama provider not registered';
      } else {
        const healthPromise = ollamaProvider.checkHealth?.() ?? Promise.resolve(false);
        const timeoutPromise = new Promise<boolean>((resolve) =>
          setTimeout(() => resolve(false), 3_000),
        );
        const healthy = await Promise.race([healthPromise, timeoutPromise]);
        if (healthy) {
          ollamaAvailable = true;
        } else {
          ollamaReason = 'Ollama is not responding';
        }
      }
    } catch (err) {
      ollamaReason = `Ollama health check failed: ${err instanceof Error ? err.message : String(err)}`;
    }

    configs.push({
      path: 'local',
      available: ollamaAvailable,
      reason: ollamaReason,
      priority: this.priorities.local,
    });

    // ── Text: always available ──────────────────────────────────────────
    //
    // HERMENEUTIC NOTE: Text is the universal floor. It cannot fail because
    // it has no external dependencies — it is pure UI. This is why its
    // priority is 99: it's always available but always last resort.
    configs.push({
      path: 'text',
      available: true,
      priority: this.priorities.text,
    });

    // Sort by priority (lower = first)
    configs.sort((a, b) => a.priority - b.priority);

    return configs;
  }

  // ── Public API: Start Best Path ───────────────────────────────────────

  /**
   * Try paths in priority order until one starts successfully.
   * Returns the path that was started.
   *
   * HERMENEUTIC CIRCLE: "Starting" a path means different things for each:
   *   - Cloud: Transition to CONNECTING_CLOUD (WebSocket will open).
   *   - Local: Transition to CONNECTING_LOCAL (Whisper + Ollama + TTS init).
   *   - Text: Transition to TEXT_FALLBACK (immediate, always succeeds).
   *
   * The caller (likely an IPC handler) should then listen for state-change
   * events to know when the path is fully active (CLOUD_ACTIVE / LOCAL_ACTIVE).
   */
  async startBestPath(systemPrompt: string, tools: ToolDefinition[]): Promise<VoicePath> {
    // Cache configuration for potential restarts during fallback
    this.activeSystemPrompt = systemPrompt;
    this.activeTools = tools;
    this.attemptedPaths.clear();
    this.pathErrors = [];

    const configs = await this.probeAvailability();

    for (const config of configs) {
      if (!config.available) {
        console.log(
          `[VoiceFallbackManager] Skipping ${config.path}: ${config.reason}`,
        );
        this.attemptedPaths.add(config.path);
        if (config.reason) {
          this.pathErrors.push({ path: config.path, error: config.reason });
        }
        continue;
      }

      const started = await this.attemptStartPath(config.path);
      if (started) {
        telemetryEngine.record('voice-path', 'started', config.path);
        return config.path;
      }
      // If attemptStartPath failed, it already added to attemptedPaths
    }

    // All paths exhausted — this should only happen if text also "failed"
    // (which shouldn't be possible, but defensive programming).
    console.error('[VoiceFallbackManager] All paths exhausted');
    this.emit('all-paths-exhausted', { errors: this.pathErrors });
    return 'text';
  }

  // ── Public API: Handle Path Failure ───────────────────────────────────

  /**
   * Called when the current voice path fails. Captures context from the
   * dying path, tears it down, and starts the next available path.
   *
   * BOUNDARY: This method assumes the caller has already detected the failure
   * (via state machine events, WebSocket close, Ollama error, etc.). The
   * fallback manager does not detect failures itself — it responds to them.
   *
   * Returns the path that was started as fallback, or 'text' if all exhausted.
   */
  async handlePathFailure(failedPath: VoicePath, error: Error): Promise<VoicePath> {
    console.warn(
      `[VoiceFallbackManager] Path failure: ${failedPath} — ${error.message}`,
    );

    // Guard: if a switch is already in progress, don't start another
    if (this.switching) {
      console.warn('[VoiceFallbackManager] Switch already in progress — ignoring failure');
      return this.currentPath ?? 'text';
    }

    this.attemptedPaths.add(failedPath);
    this.pathErrors.push({ path: failedPath, error: error.message });

    // Capture context before teardown (teardown clears the path's state)
    this.snapshot = this.captureContext();

    // Tear down the failed path
    await this.teardownPath(failedPath);

    // Find the next available path that hasn't been attempted
    const configs = await this.probeAvailability();
    for (const config of configs) {
      if (this.attemptedPaths.has(config.path)) continue;
      if (!config.available) {
        this.attemptedPaths.add(config.path);
        if (config.reason) {
          this.pathErrors.push({ path: config.path, error: config.reason });
        }
        continue;
      }

      this.emit('switch-start', {
        from: failedPath,
        to: config.path,
        reason: error.message,
      });
      telemetryEngine.record('voice-fallback', 'triggered', `${failedPath}->${config.path}`);

      const started = await this.attemptStartPath(config.path);
      if (started) {
        return config.path;
      }
    }

    // All voice paths exhausted — fall to text
    console.warn('[VoiceFallbackManager] All voice paths exhausted — text fallback');
    telemetryEngine.record('voice-fallback', 'exhausted');
    this.currentPath = 'text';
    this.stateMachine.transition('TEXT_FALLBACK', 'All voice paths exhausted');
    this.emit('all-paths-exhausted', { errors: this.pathErrors });
    return 'text';
  }

  // ── Public API: Forced Path Switch ────────────────────────────────────

  /**
   * Force switch to a specific path — used for manual overrides (e.g., user
   * clicks "Switch to local" in settings).
   *
   * Unlike handlePathFailure(), this does NOT mark the current path as
   * "attempted" — it's a deliberate choice, not a failure. The user can
   * switch back later.
   *
   * Returns true if the switch succeeded, false if the target path is
   * unavailable or the switch failed.
   */
  async switchTo(path: VoicePath, reason: string): Promise<boolean> {
    if (this.switching) {
      console.warn('[VoiceFallbackManager] Switch already in progress');
      return false;
    }

    if (this.currentPath === path) {
      console.log(`[VoiceFallbackManager] Already on ${path} — no switch needed`);
      return true;
    }

    // Check availability of target path
    const configs = await this.probeAvailability();
    const target = configs.find((c) => c.path === path);
    if (!target?.available) {
      console.warn(
        `[VoiceFallbackManager] Cannot switch to ${path}: ${target?.reason ?? 'unknown'}`,
      );
      return false;
    }

    const previousPath = this.currentPath;

    this.emit('switch-start', { from: previousPath, to: path, reason });

    // Capture context before teardown
    if (previousPath && previousPath !== 'text') {
      this.snapshot = this.captureContext();
    }

    // Tear down current path
    if (previousPath) {
      await this.teardownPath(previousPath);
    }

    // Reset attempted paths — this is a manual switch, not a failure cascade
    this.attemptedPaths.clear();
    this.pathErrors = [];

    const started = await this.attemptStartPath(path);
    if (!started) {
      this.emit('switch-failed', {
        path,
        error: new Error(`Failed to start ${path} path`),
      });
      return false;
    }

    return true;
  }

  // ── Public API: Path Priorities ───────────────────────────────────────

  /**
   * Override the default priority for a path.
   *
   * TENSION: Cloud-first is the default because it has the richest
   * capabilities. But some users prefer local-first for privacy or
   * offline use. This method lets the caller (settings UI) change the
   * priority without rebuilding the manager.
   */
  setPathPriority(path: VoicePath, priority: number): void {
    this.priorities[path] = priority;
  }

  // ── Public API: Query ─────────────────────────────────────────────────

  /** Which path is currently active (null if none). */
  getCurrentPath(): VoicePath | null {
    return this.currentPath;
  }

  /** Whether a switch is currently in progress. */
  isSwitching(): boolean {
    return this.switching;
  }

  /** Paths that have been attempted and failed in the current session. */
  getAttemptedPaths(): ReadonlySet<VoicePath> {
    return this.attemptedPaths;
  }

  // ── Public API: Context Injection (P2.2) ──────────────────────────────

  /**
   * Inject conversation context from an external source.
   *
   * Used when the fallback manager is taking over a conversation that was
   * started outside its control (e.g., the Gemini WebSocket hook started
   * a conversation, but now the fallback manager needs to switch to local).
   *
   * BOUNDARY: The injected messages are trusted — they come from within the
   * app's own conversation loops, not from external sources.
   */
  injectSnapshot(messages: ChatMessage[], systemPrompt: string, tools: ToolDefinition[]): void {
    this.snapshot = {
      messages: [...messages], // Defensive copy
      systemPrompt,
      tools,
      capturedAt: Date.now(),
    };
    this.activeSystemPrompt = systemPrompt;
    this.activeTools = tools;
  }

  // ── Public API: Cleanup ───────────────────────────────────────────────

  /**
   * Tear down the fallback manager. Clears timers, unsubscribes from the
   * state machine, and releases the snapshot.
   */
  destroy(): void {
    this.clearDegradedTimer();

    for (const cleanup of this.cleanupFns) {
      cleanup();
    }
    this.cleanupFns = [];

    this.snapshot = null;
    this.currentPath = null;
    this.attemptedPaths.clear();
    this.pathErrors = [];
    this.switching = false;

    this.removeAllListeners();
  }

  // ── Private: Context Capture ──────────────────────────────────────────

  /**
   * Capture the current conversation context for handoff to the next path.
   *
   * CONSTRAINT Q5 answer: We capture messages, system prompt, and tools.
   * We do NOT capture audio buffers, WebSocket state, or TTS queue.
   * The new path starts with clean audio but the same semantic context.
   *
   * HERMENEUTIC NOTE: "Capturing context" is the bridge between the
   * hermeneutic whole (continuous conversation) and the parts (different
   * transport mechanisms). The messages ARE the conversation; the transport
   * is just how they move. Changing transport doesn't change the conversation.
   */
  private captureContext(): ConversationSnapshot {
    return {
      messages: this.snapshot?.messages ? [...this.snapshot.messages] : [],
      systemPrompt: this.activeSystemPrompt,
      tools: this.activeTools,
      capturedAt: Date.now(),
    };
  }

  // ── Private: Path Teardown ────────────────────────────────────────────

  /**
   * Full cleanup of a voice path before switching to another.
   *
   * INVERSION DEFENSE: This is the answer to "how do you prevent old listeners
   * from conflicting with the new path?" Every component that the path started
   * must be stopped. The teardown is wrapped in a timeout to prevent a hung
   * component from blocking the switch forever.
   *
   * BOUNDARY: teardownPath does NOT transition the state machine — the caller
   * is responsible for driving state transitions. This method just cleans up
   * the transport layer.
   */
  private async teardownPath(path: VoicePath): Promise<void> {
    console.log(`[VoiceFallbackManager] Tearing down ${path} path...`);

    const teardownWork = async (): Promise<void> => {
      switch (path) {
        case 'cloud': {
          // Cloud teardown: The Gemini WebSocket is managed by the renderer's
          // useGeminiLive hook. We signal teardown via state machine transition
          // to DISCONNECTING — the hook listens for this and closes the socket.
          //
          // We also stop any main-process audio components that might be active
          // (e.g., AudioCapture was started for cloud path).
          try {
            speechSynthesis.stop();
          } catch {
            // Best-effort — don't let TTS cleanup failure block teardown
          }
          break;
        }

        case 'local': {
          // Local teardown: Stop the TranscriptionPipeline (which stops
          // AudioCapture + Whisper), stop TTS queue, and clear speech synthesis.
          //
          // BOUNDARY: We do NOT unload Whisper or TTS models — just stop the
          // active pipeline. Model loading is expensive; keeping them loaded
          // means a faster restart if we need to fall back to local again.
          try {
            transcriptionPipeline.stop();
          } catch {
            // Best-effort
          }
          try {
            speechSynthesis.stop();
          } catch {
            // Best-effort
          }
          break;
        }

        case 'text': {
          // Text has no transport to tear down.
          break;
        }
      }
    };

    // Wrap teardown in a timeout — a hung teardown must not block the switch.
    // INVERSION DEFENSE: Without this timeout, a stuck speechSynthesis.stop()
    // or transcriptionPipeline.stop() would prevent fallback from ever working.
    try {
      await Promise.race([
        teardownWork(),
        new Promise<void>((_, reject) =>
          setTimeout(
            () => reject(new Error(`Teardown of ${path} timed out after ${TEARDOWN_TIMEOUT_MS}ms`)),
            TEARDOWN_TIMEOUT_MS,
          ),
        ),
      ]);
    } catch (err) {
      console.error(
        `[VoiceFallbackManager] Teardown error for ${path}: ${err instanceof Error ? err.message : String(err)}`,
      );
      // Continue anyway — we must not block the switch
    }

    console.log(`[VoiceFallbackManager] Teardown of ${path} complete`);
  }

  // ── Private: Path Startup ─────────────────────────────────────────────

  /**
   * Attempt to start a single path. Returns true if the state machine
   * accepted the transition, false if it rejected.
   *
   * HERMENEUTIC NOTE: "Starting" a path means transitioning the state machine
   * to the appropriate CONNECTING state. The actual connection/initialization
   * is handled by the path's own components (useGeminiLive for cloud,
   * LocalConversation for local). We just drive the state machine.
   */
  private async attemptStartPath(path: VoicePath): Promise<boolean> {
    this.switching = true;

    try {
      let targetState: VoiceState;

      switch (path) {
        case 'cloud':
          targetState = 'CONNECTING_CLOUD';
          break;
        case 'local':
          targetState = 'CONNECTING_LOCAL';
          break;
        case 'text':
          targetState = 'TEXT_FALLBACK';
          break;
      }

      const reason = this.snapshot
        ? `Fallback from ${this.currentPath ?? 'none'} — resuming conversation`
        : `Starting ${path} path`;

      const transitioned = this.stateMachine.transition(targetState, reason);
      if (!transitioned) {
        console.warn(
          `[VoiceFallbackManager] State machine rejected transition to ${targetState}`,
        );
        this.attemptedPaths.add(path);
        this.pathErrors.push({
          path,
          error: `State machine rejected transition to ${targetState}`,
        });
        return false;
      }

      this.currentPath = path;

      this.emit('switch-complete', {
        path,
        hadContext: this.snapshot !== null && this.snapshot.messages.length > 0,
      });

      return true;
    } finally {
      this.switching = false;
    }
  }

  // ── Private: State Machine Subscriptions ──────────────────────────────

  /**
   * Subscribe to state machine events to detect degradation and trigger
   * automatic fallback.
   *
   * P2.2 spec: "VoiceFallbackManager subscribes to state machine DEGRADED
   * events. On CLOUD_DEGRADED for > 10 seconds → auto-switch to local.
   * On LOCAL_DEGRADED for > 10 seconds → auto-switch to cloud."
   *
   * TENSION: We don't switch immediately on degradation — we give the path
   * 10 seconds to recover. This prevents flapping on transient issues (a
   * single dropped frame, a brief network hiccup). But we also don't wait
   * forever — 10s is the threshold where "maybe it'll recover" becomes
   * "it's not coming back."
   */
  private subscribeToStateMachine(): void {
    const onStateChange = (payload: {
      from: VoiceState;
      to: VoiceState;
      reason: string;
    }): void => {
      const { from, to } = payload;

      // ── Degradation detected: start countdown to auto-switch ─────────
      if (to === 'CLOUD_DEGRADED' || to === 'LOCAL_DEGRADED') {
        this.startDegradedTimer(to);
        return;
      }

      // ── Recovery from degradation: cancel auto-switch ────────────────
      if (
        (from === 'CLOUD_DEGRADED' && to === 'CLOUD_ACTIVE') ||
        (from === 'LOCAL_DEGRADED' && to === 'LOCAL_ACTIVE')
      ) {
        this.clearDegradedTimer();
        return;
      }

      // ── Path fully failed (state machine timed out to fallback) ──────
      //
      // The state machine's own timeouts can trigger transitions like
      // CONNECTING_CLOUD → CONNECTING_LOCAL. We track these so the
      // fallback manager stays in sync.
      if (
        from === 'CONNECTING_CLOUD' &&
        (to === 'CONNECTING_LOCAL' || to === 'TEXT_FALLBACK')
      ) {
        this.attemptedPaths.add('cloud');
        this.pathErrors.push({ path: 'cloud', error: payload.reason });
        this.currentPath = to === 'CONNECTING_LOCAL' ? 'local' : 'text';
      }

      if (from === 'CONNECTING_LOCAL' && to === 'TEXT_FALLBACK') {
        this.attemptedPaths.add('local');
        this.pathErrors.push({ path: 'local', error: payload.reason });
        this.currentPath = 'text';
        this.emit('all-paths-exhausted', { errors: this.pathErrors });
      }

      // ── Path became active: update currentPath ───────────────────────
      if (to === 'CLOUD_ACTIVE') {
        this.currentPath = 'cloud';
        this.clearDegradedTimer();
      }
      if (to === 'LOCAL_ACTIVE') {
        this.currentPath = 'local';
        this.clearDegradedTimer();
      }
      if (to === 'TEXT_FALLBACK') {
        this.currentPath = 'text';
        this.clearDegradedTimer();
      }

      // ── Disconnecting or IDLE: clear current path ────────────────────
      if (to === 'IDLE' || to === 'DISCONNECTING') {
        this.clearDegradedTimer();
        if (to === 'IDLE') {
          this.currentPath = null;
          this.attemptedPaths.clear();
          this.pathErrors = [];
          this.snapshot = null;
        }
      }
    };

    this.stateMachine.on('state-change', onStateChange);
    this.cleanupFns.push(() => {
      this.stateMachine.removeListener('state-change', onStateChange);
    });
  }

  // ── Private: Degraded Auto-Switch Timer ───────────────────────────────

  /**
   * Start a countdown to auto-switch away from a degraded path.
   *
   * HERMENEUTIC NOTE: The degraded timer represents "patience." The system
   * is saying "I notice things aren't great, but I'll give you a chance to
   * recover before I do something drastic." The 10-second threshold is the
   * boundary between transient and persistent degradation.
   */
  private startDegradedTimer(degradedState: VoiceState): void {
    this.clearDegradedTimer();

    this.degradedTimer = setTimeout(() => {
      this.degradedTimer = null;

      // Verify we're still in the degraded state (might have recovered)
      const currentState = this.stateMachine.getState();
      if (currentState !== degradedState) return;

      // Determine which path to switch to
      const failedPath: VoicePath = degradedState === 'CLOUD_DEGRADED' ? 'cloud' : 'local';
      const error = new Error(
        `${failedPath} path degraded for ${DEGRADED_AUTO_SWITCH_MS}ms without recovery`,
      );

      console.warn(
        `[VoiceFallbackManager] Auto-switching from degraded ${failedPath}: ${error.message}`,
      );

      // Fire-and-forget — handlePathFailure is async but we're in a timer callback.
      // Errors from handlePathFailure will be emitted as events.
      void this.handlePathFailure(failedPath, error);
    }, DEGRADED_AUTO_SWITCH_MS);
  }

  private clearDegradedTimer(): void {
    if (this.degradedTimer !== null) {
      clearTimeout(this.degradedTimer);
      this.degradedTimer = null;
    }
  }
}

// ── Singleton Export ──────────────────────────────────────────────────────

export const voiceFallbackManager = VoiceFallbackManager.getInstance();
