/**
 * useVoiceState.ts — React hook for consuming voice state from the
 * main-process VoiceStateMachine via the IPC bridge.
 *
 * Phase 1.2, Track 1: Renderer Bindings
 *
 * ─────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Understanding Part ↔ Whole
 * ─────────────────────────────────────────────────────────────────
 *
 * THE WHOLE: A React component needs to show the user what voice is
 * doing — "connecting…", "listening…", "voice unavailable." The
 * component doesn't care about WebSockets or Whisper models. It
 * cares about one question: "What should I render right now?"
 *
 * THE PART: This hook provides a single `state` string plus derived
 * booleans (`isActive`, `isDegraded`, `isConnecting`). The component
 * switches on these to pick the right icon/label. No business logic
 * leaks into the renderer.
 *
 * THE CIRCLE: Understanding what `isActive` means requires knowing
 * that CLOUD_ACTIVE and LOCAL_ACTIVE both represent "voice is
 * flowing." Understanding why they're separate requires knowing the
 * two voice paths. But the renderer doesn't care which path — just
 * whether voice works. Hence `isActive` collapses both.
 *
 * SOCRATIC QUESTIONS:
 *   Q: "What if getState() resolves after the component unmounts?"
 *   A: The useEffect cleanup runs first, setting the `cancelled` flag.
 *      The stale promise result is ignored.
 *
 *   Q: "What if two state-change events arrive in the same tick?"
 *   A: React batches setState calls. Both run; the last one wins.
 *      This is correct — we always want the latest state.
 *
 *   Q: "Should health auto-refresh or be on-demand?"
 *   A: On-demand via refreshHealth(). Components that need it (the
 *      debug panel) call it explicitly. Most components only need
 *      state, and polling health would waste IPC bandwidth.
 * ─────────────────────────────────────────────────────────────────
 */

import { useState, useEffect, useCallback } from 'react';

/** Voice state type — mirrors the main-process VoiceState union. */
type VoiceState =
  | 'IDLE'
  | 'REQUESTING_MIC'
  | 'MIC_DENIED'
  | 'MIC_GRANTED'
  | 'CONNECTING_CLOUD'
  | 'CLOUD_ACTIVE'
  | 'CLOUD_DEGRADED'
  | 'CONNECTING_LOCAL'
  | 'LOCAL_ACTIVE'
  | 'LOCAL_DEGRADED'
  | 'TEXT_FALLBACK'
  | 'ERROR'
  | 'DISCONNECTING';

/** Health metrics snapshot from the state machine. */
interface VoiceHealthMetrics {
  uptimeMs: number;
  consecutiveHealthy: number;
  consecutiveUnhealthy: number;
}

/** State-change event pushed from main process. */
interface VoiceStateChangeEvent {
  from: VoiceState;
  to: VoiceState;
  reason: string;
}

/** Return type of useVoiceState(). */
export interface UseVoiceStateResult {
  /** Current voice pipeline state. */
  state: VoiceState;
  /** Latest health metrics (null until explicitly fetched). */
  health: VoiceHealthMetrics | null;
  /** True when voice audio is actively flowing (cloud or local). */
  isActive: boolean;
  /** True when voice is connected but unhealthy. */
  isDegraded: boolean;
  /** True when a connection attempt is in progress. */
  isConnecting: boolean;
  /** Fetch fresh health metrics from the state machine. */
  refreshHealth: () => Promise<void>;
}

// ── Active / degraded / connecting sets ─────────────────────────

const ACTIVE_STATES: ReadonlySet<VoiceState> = new Set([
  'CLOUD_ACTIVE',
  'LOCAL_ACTIVE',
]);

const DEGRADED_STATES: ReadonlySet<VoiceState> = new Set([
  'CLOUD_DEGRADED',
  'LOCAL_DEGRADED',
]);

const CONNECTING_STATES: ReadonlySet<VoiceState> = new Set([
  'REQUESTING_MIC',
  'MIC_GRANTED',
  'CONNECTING_CLOUD',
  'CONNECTING_LOCAL',
]);

// ── Hook ────────────────────────────────────────────────────────

export function useVoiceState(): UseVoiceStateResult {
  const [state, setState] = useState<VoiceState>('IDLE');
  const [health, setHealth] = useState<VoiceHealthMetrics | null>(null);

  // Fetch current state on mount + subscribe to changes
  useEffect(() => {
    let cancelled = false;

    // Seed with current state (in case we mount after voice is already active)
    window.eve.voiceState.getState().then((s) => {
      if (!cancelled) setState(s);
    });

    // Subscribe to state-change events from main process
    const cleanup = window.eve.voiceState.onStateChange(
      (event: VoiceStateChangeEvent) => {
        setState(event.to);
      },
    );

    return () => {
      cancelled = true;
      cleanup();
    };
  }, []);

  // On-demand health refresh
  const refreshHealth = useCallback(async () => {
    const h = await window.eve.voiceState.getHealth();
    setHealth(h);
  }, []);

  return {
    state,
    health,
    isActive: ACTIVE_STATES.has(state),
    isDegraded: DEGRADED_STATES.has(state),
    isConnecting: CONNECTING_STATES.has(state),
    refreshHealth,
  };
}
