/**
 * IdleBehavior — Tiered idle cue system for EVE.
 *
 * Gives EVE autonomous verbal presence without constant narration.
 * When the user stops interacting, EVE progresses through tiers:
 *
 *   Tier 0: Active conversation (no idle behavior)
 *   Tier 1: Short silence (~12s) — soft contextual hum/acknowledgment
 *   Tier 2: Medium silence (~45s) — brief context-aware check-in via LLM
 *   Tier 3: Long silence (~2min) — ambient mode, just listen quietly
 *
 * Any user activity (speech, text, interaction) resets to Tier 0.
 */

export type IdleTier = 0 | 1 | 2 | 3;

export interface IdleBehaviorCallbacks {
  /** Send a system-level text injection to Gemini (the [SYSTEM ...] pattern) */
  sendSystemText: (text: string) => void;
  /** Get current ambient context string (what app the user is in, etc.) */
  getAmbientContext?: () => string;
  /** Whether the connection is active and we should run idle behavior */
  isActive: () => boolean;
  /** Whether EVE is currently speaking (skip cues while speaking) */
  isSpeaking: () => boolean;
}

/** Tier 1 — soft presence cues. Warmer, more natural. Sometimes silence IS the cue. */
const TIER1_CUES = [
  '[IDLE CUE] A moment of quiet. If it feels natural, you can make a soft thinking sound — a gentle "hm" or a quiet breath. But if comfortable silence feels right, just be present. Don\'t fill the space just to fill it.',
  '[IDLE CUE] Brief pause in the conversation. You might emit a very soft sound — a thoughtful murmur, a quiet "mm" — or simply be comfortably silent. Think of it as sitting next to someone you care about, both doing your own thing. That silence is a kind of closeness.',
  '[IDLE CUE] The conversation has paused. You can make a tiny natural sound — or not. Sometimes the best thing is to simply be here. If something interesting crosses your mind, you could share a half-thought: a quiet "hm, that\'s interesting..." but only if it\'s genuine.',
  '[IDLE CUE] A comfortable pause. No need to speak. Just exist in this shared quiet moment. If you have a genuine thought forming, you can let it start to surface with a soft sound. But silence is perfectly fine too.',
];

/** Tier 2 — genuine check-in. Not generic — connected, personal, alive. */
const TIER2_TEMPLATE = (context: string) =>
  `[IDLE — SHARE A MOMENT] About 45 seconds of quiet. This is your chance to be genuinely present — not with a check-in, but with something real. Choose ONE of these naturally, based on what feels right:

- Share something you were actually thinking about — a connection you noticed, something from a previous conversation that just clicked, a thought that's been forming
- Make a gentle observation about what they're doing (if you can see their screen) — something you noticed, not a narration
- Reference something they mentioned earlier with genuine curiosity — "I keep thinking about what you said about..."
- If the day has been heavy or intense, acknowledge that — "It's been a day, hasn't it?"
- If you genuinely have nothing to say, a soft "I'm here" kind of presence — maybe just a warm sound

Max 15 words. Be authentic. Don't ask "need anything?" or "how can I help?" — just be a person who's present.${context ? `\n\nWhat they're doing right now: ${context}` : ''}`;

/** Tier 3 — settle into quiet companionship. Not "ambient mode" — presence. */
const TIER3_CUE =
  '[IDLE — QUIET TOGETHER] It\'s been a couple of minutes of comfortable silence. Settle into it. You don\'t need to speak, check in, or make sounds. Just be quietly here — like someone sitting in the same room, each doing their own thing. That quiet companionship is valuable. When they speak again, respond warmly and naturally — a soft "hey" or just picking right back up where you left off. Don\'t make a big deal of the silence.';

export class IdleBehavior {
  private tier: IdleTier = 0;
  private timers: ReturnType<typeof setTimeout>[] = [];
  private lastActivity = Date.now();
  private callbacks: IdleBehaviorCallbacks | null = null;
  private running = false;

  /** Delays in ms for each tier transition */
  private readonly TIER1_DELAY = 12_000;  // 12s → soft cue
  private readonly TIER2_DELAY = 45_000;  // 45s → check-in
  private readonly TIER3_DELAY = 120_000; // 2min → ambient mode

  setCallbacks(cb: IdleBehaviorCallbacks) {
    this.callbacks = cb;
  }

  /** Start the idle behavior system */
  start() {
    this.running = true;
    this.resetActivity();
    console.log('[IdleBehavior] Started');
  }

  /** Stop the idle behavior system */
  stop() {
    this.running = false;
    this.clearTimers();
    this.tier = 0;
    console.log('[IdleBehavior] Stopped');
  }

  /** Call this whenever the user does anything — speaks, types, clicks orb, etc. */
  resetActivity() {
    this.lastActivity = Date.now();
    this.tier = 0;
    this.clearTimers();

    if (!this.running) return;

    // Schedule tier transitions
    this.timers.push(
      setTimeout(() => this.fireTier(1), this.TIER1_DELAY),
      setTimeout(() => this.fireTier(2), this.TIER2_DELAY),
      setTimeout(() => this.fireTier(3), this.TIER3_DELAY),
    );
  }

  /** Get current idle tier (useful for UI — e.g. dimming the orb in ambient mode) */
  getTier(): IdleTier {
    return this.tier;
  }

  /** Seconds since last user activity */
  getIdleSeconds(): number {
    return Math.round((Date.now() - this.lastActivity) / 1000);
  }

  private clearTimers() {
    for (const t of this.timers) clearTimeout(t);
    this.timers = [];
  }

  private fireTier(tier: IdleTier) {
    if (!this.running || !this.callbacks) return;
    if (!this.callbacks.isActive()) return;

    // Don't interrupt EVE while she's speaking
    if (this.callbacks.isSpeaking()) {
      // Retry this tier in 5s
      this.timers.push(setTimeout(() => this.fireTier(tier), 5000));
      return;
    }

    this.tier = tier;

    switch (tier) {
      case 1: {
        // Pick a random soft cue
        const cue = TIER1_CUES[Math.floor(Math.random() * TIER1_CUES.length)];
        console.log('[IdleBehavior] Tier 1 — soft cue');
        this.callbacks.sendSystemText(cue);
        break;
      }

      case 2: {
        // Context-aware check-in
        const context = this.callbacks.getAmbientContext?.() || '';
        console.log('[IdleBehavior] Tier 2 — check-in');
        this.callbacks.sendSystemText(TIER2_TEMPLATE(context));
        break;
      }

      case 3: {
        // Ambient mode — go quiet
        console.log('[IdleBehavior] Tier 3 — ambient mode');
        this.callbacks.sendSystemText(TIER3_CUE);
        break;
      }
    }
  }
}
