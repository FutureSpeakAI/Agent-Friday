/**
 * agent-trust.ts — Agent Trust State management.
 *
 * Tracks how much the USER trusts the AGENT, detecting frustration signals
 * and triggering recovery behaviors when trust is low.
 *
 * This is DIFFERENT from:
 * - trust-engine.ts (gateway access control — who can use what tools)
 * - trust-graph.ts (credibility scoring — people in the user's world)
 *
 * This module answers: "Is the user getting frustrated with me?"
 */

import type { AgentTrustState } from './settings';

/* ── Default State ── */

const DEFAULT_STATE: AgentTrustState = {
  score: 0.5,
  frustrationSignals: 0,
  corrections: 0,
  successStreak: 0,
  lastFrustration: 0,
  recoveryMode: false,
};

/* ── Signal Detection Patterns ── */

/** Regex patterns that indicate user frustration / negative trust */
const NEGATIVE_PATTERNS: { pattern: RegExp; weight: number; type: string }[] = [
  // Explicit correction / rejection
  { pattern: /\bthat'?s?\s+not\s+what\s+i\s+(asked|meant|wanted|said)\b/i, weight: 0.08, type: 'correction' },
  { pattern: /\b(no|nope|wrong|incorrect|that'?s?\s+wrong)\b/i, weight: 0.05, type: 'rejection' },
  { pattern: /\b(stop|quit|enough|shut\s+up)\b/i, weight: 0.10, type: 'frustration' },
  { pattern: /\bnever\s*mind\b/i, weight: 0.07, type: 'abandonment' },

  // Doubt / capability questioning
  { pattern: /\bcan\s+you\s+(actually|even|really)\b/i, weight: 0.06, type: 'doubt' },
  { pattern: /\bdo\s+you\s+(actually|even)\s+(understand|know|get)\b/i, weight: 0.07, type: 'doubt' },
  { pattern: /\byou\s+(already|just)\s+said\s+that\b/i, weight: 0.08, type: 'looping' },
  { pattern: /\byou'?re?\s+(not\s+listening|not\s+getting\s+it|useless|terrible|bad\s+at)\b/i, weight: 0.10, type: 'frustration' },

  // Repeat / re-request signals
  { pattern: /\bi\s+(already|just)\s+(said|told|asked|explained)\b/i, weight: 0.08, type: 'repeat' },
  { pattern: /\b(again|one\s+more\s+time|try\s+again|redo\s+this)\b/i, weight: 0.04, type: 'retry' },

  // Disengagement cues
  { pattern: /\b(whatever|fine|okay\s+then|forget\s+it|let'?s?\s+move\s+on)\b/i, weight: 0.04, type: 'disengagement' },
];

/** Regex patterns that indicate user satisfaction / positive trust */
const POSITIVE_PATTERNS: { pattern: RegExp; weight: number; type: string }[] = [
  { pattern: /\b(thanks?|thank\s+you|thx|ty)\b/i, weight: 0.03, type: 'gratitude' },
  { pattern: /\b(perfect|exactly|great|awesome|amazing|excellent|nice|wonderful|brilliant)\b/i, weight: 0.04, type: 'satisfaction' },
  { pattern: /\b(that'?s?\s+(exactly|precisely)\s+what\s+i\s+(wanted|needed|meant))\b/i, weight: 0.06, type: 'strong_satisfaction' },
  { pattern: /\b(good\s+job|well\s+done|nailed\s+it|spot\s+on)\b/i, weight: 0.05, type: 'praise' },
  { pattern: /\b(love\s+(it|this|that)|this\s+is\s+(great|perfect))\b/i, weight: 0.05, type: 'delight' },
  { pattern: /\b(yes|yeah|yep|yup|right|correct|exactly)\b/i, weight: 0.02, type: 'affirmation' },
];

/* ── Core Functions ── */

/**
 * Get a fresh default trust state (new sessions start here).
 */
export function getDefaultTrustState(): AgentTrustState {
  return { ...DEFAULT_STATE };
}

/**
 * Analyze a user message and return updated trust state.
 * Does NOT mutate the input — returns a new state object.
 */
export function processUserMessage(
  currentState: AgentTrustState | null,
  userMessage: string,
): AgentTrustState {
  const state: AgentTrustState = currentState
    ? { ...currentState }
    : { ...DEFAULT_STATE };

  const trimmed = userMessage.trim();
  if (!trimmed) return state;

  let deltaScore = 0;
  let frustrationDetected = false;

  // Check negative patterns
  for (const { pattern, weight, type } of NEGATIVE_PATTERNS) {
    if (pattern.test(trimmed)) {
      deltaScore -= weight;
      frustrationDetected = true;

      if (type === 'correction') {
        state.corrections++;
      }
    }
  }

  // Check positive patterns
  for (const { pattern, weight } of POSITIVE_PATTERNS) {
    if (pattern.test(trimmed)) {
      deltaScore += weight;
    }
  }

  // Short, curt responses after longer exchanges are a subtle frustration signal.
  // Only trigger if message is very short (< 20 chars) AND we had a recent frustration.
  if (trimmed.length < 20 && state.frustrationSignals > 0) {
    deltaScore -= 0.02;
  }

  // Session return bonus — if this is the first message and trust was previously low,
  // the user came back. That's a positive signal.
  if (state.successStreak === 0 && state.score < 0.5 && !frustrationDetected) {
    deltaScore += 0.03; // They came back — small trust recovery
  }

  // Update frustration tracking
  if (frustrationDetected) {
    state.frustrationSignals++;
    state.lastFrustration = Date.now();
    state.successStreak = 0;
  } else if (deltaScore > 0) {
    // Only count toward success streak if there was a positive signal
    state.successStreak++;
  }

  // Natural trust recovery from success streaks
  if (state.successStreak >= 5) {
    deltaScore += 0.02; // Bonus for sustained good interactions
  }

  // Apply score change with bounds
  state.score = Math.max(0, Math.min(1, state.score + deltaScore));

  // Update recovery mode
  state.recoveryMode = state.score < 0.3;

  return state;
}

/**
 * Called at session start — resets session-specific counters but keeps the trust score.
 * Also applies a small "return bonus" — the user came back, which is inherently positive.
 */
export function resetSessionCounters(state: AgentTrustState | null): AgentTrustState {
  if (!state) return { ...DEFAULT_STATE };

  return {
    ...state,
    frustrationSignals: 0,
    corrections: 0,
    successStreak: 0,
    // Small trust recovery for returning — they chose to come back
    score: Math.min(1, state.score + 0.02),
    recoveryMode: state.score < 0.3,
  };
}

/**
 * Build the trust-awareness block for the system prompt.
 * Returns empty string when trust is healthy — no need to burden the prompt.
 */
export function buildTrustAwarenessBlock(trustState: AgentTrustState | null): string {
  if (!trustState) return '';

  // High trust — no special handling needed
  if (trustState.score > 0.7) return '';

  // Recovery mode — trust is critically low
  if (trustState.score < 0.3) {
    return `\n\n[TRUST RECOVERY MODE — Your user's confidence in you is low right now.

Be precise. Be brief. Don't volunteer — respond. If you're unsure, ask ONE clarifying question. Demonstrate competence through action. Do not over-apologize.

Show them you can do this by DOING it, not by promising you can.

Current trust metrics: score ${trustState.score.toFixed(2)}, ${trustState.corrections} corrections this session, ${trustState.frustrationSignals} frustration signals detected.]`;
  }

  // Frustration detected — calibration alert
  if (trustState.frustrationSignals > 2) {
    return `\n\n[CALIBRATION ALERT — You've detected ${trustState.frustrationSignals} frustration signals in this session. Slow down. Make sure you understand what they actually want before acting. If you're about to repeat something you've already said, stop and try a different approach.]`;
  }

  // Moderate trust with some corrections — be mindful
  if (trustState.corrections > 1) {
    return `\n\n[ATTENTION — The user has corrected you ${trustState.corrections} times this session. Be more careful about confirming understanding before acting.]`;
  }

  return '';
}

/**
 * Get a human-readable trust level label.
 */
export function getTrustLabel(state: AgentTrustState | null): string {
  if (!state) return 'unknown';
  if (state.score > 0.8) return 'excellent';
  if (state.score > 0.6) return 'good';
  if (state.score > 0.4) return 'moderate';
  if (state.score > 0.2) return 'low';
  return 'critical';
}
