/**
 * audio-reactive.js — Voice/Audio Sensitivity & Emotional Arc Engine
 * Source: Agent-Friday/src/main/voice/voice-state-machine.ts
 *         Agent-Friday/src/main/voice/voice-fallback-manager.ts
 *         asimovs-radio/ (emotional arc engine)
 *
 * Three systems consolidated:
 * 1. Audio level synthesis (mic/output → low/mid/high frequency bands)
 * 2. Idle detection with fade-out
 * 3. Emotional arc engine from Asimov's Radio (mirror/shift/celebration modes)
 *
 * The audio-reactive system drives Three.js animations:
 * - low band → cube breathing, dome scaling, mandelbrot height, grid waves
 * - mid band → astrolabe speed, mobius flow, network connections, particles
 * - high band → quantum ring deformation, energy line flashes, network distance
 * - total → idle detection threshold
 */

// ── Audio Level Synthesizer ─────────────────────────────────────────
// Converts raw mic/output levels into frequency-like bands for visuals

export class AudioReactive {
  constructor() {
    this.low = 0;
    this.mid = 0;
    this.high = 0;
    this.total = 0;
    this.lastSoundTime = -10;
    this.idleFactor = 0.4;
    this.isSpeaking = false;
    this.isListening = false;
  }

  /**
   * Update audio data from raw mic/output levels.
   * Call this every frame.
   *
   * @param {number} micLevel - Microphone input level (0-1)
   * @param {number} outputLevel - Speaker output level (0-1)
   * @param {number} elapsed - Time elapsed in seconds
   * @param {number} delta - Frame delta time
   */
  update(micLevel, outputLevel, elapsed, delta) {
    const activeLevel = this.isSpeaking ? outputLevel : this.isListening ? micLevel : 0;

    // Synthesize frequency bands from the monolithic level
    const heartbeat = (Math.sin(elapsed * Math.PI) + 1) / 2;
    this.low = activeLevel * 0.8 + heartbeat * 0.05;
    this.mid = activeLevel * 0.5;
    this.high = activeLevel * 0.3;
    this.total = (this.low + this.mid + this.high) / 3;

    // Idle detection
    if (this.total > 0.02) this.lastSoundTime = elapsed;
    const isQuiet = elapsed - this.lastSoundTime > 6.0;
    const targetIdle = isQuiet ? 0.2 : 1.0;
    this.idleFactor += (targetIdle - this.idleFactor) * delta * (isQuiet ? 0.3 : 2.0);
  }

  getData() {
    return {
      low: this.low,
      mid: this.mid,
      high: this.high,
      total: this.total,
      idleFactor: this.idleFactor,
    };
  }
}

// ── Emotional Arc Engine (from Asimov's Radio) ──────────────────────
// Tracks emotional trajectory across a session

export const ARC_MODES = Object.freeze({
  MIRROR: 'mirror',       // Reflects current emotional state
  SHIFT: 'shift',         // Leans toward resolution during frustration
  CELEBRATION: 'celebration', // Fires on success/milestone
  AUTO: 'auto',           // Automatic mode transitions
});

export class EmotionalArc {
  constructor() {
    this.mode = ARC_MODES.AUTO;
    this.frustrationScore = 0;
    this.consecutiveFailures = 0;
    this.moodHistory = [];
    this.injectionCount = 0;
    this.lastMilestone = null;
  }

  /**
   * Process an event signal from the agent system.
   *
   * @param {string} type - Event type: 'mood_change', 'completion', 'failure', 'error'
   * @param {object} data - Event data (varies by type)
   */
  signal(type, data = {}) {
    switch (type) {
      case 'failure':
      case 'error':
        this.consecutiveFailures++;
        this.frustrationScore = Math.min(1.0, this.frustrationScore + 0.15);
        break;

      case 'completion':
      case 'success':
        this.consecutiveFailures = 0;
        this.frustrationScore = Math.max(0, this.frustrationScore - 0.3);
        this.lastMilestone = Date.now();
        break;

      case 'mood_change':
        this.moodHistory.push({ mood: data.mood, timestamp: Date.now() });
        if (this.moodHistory.length > 50) this.moodHistory.shift();
        break;
    }

    // Auto-transition logic
    if (this.mode === ARC_MODES.AUTO) {
      if (this.consecutiveFailures >= 3 && this.frustrationScore > 0.5) {
        this._currentAutoMode = ARC_MODES.SHIFT;
      } else if (this.lastMilestone && Date.now() - this.lastMilestone < 30000) {
        this._currentAutoMode = ARC_MODES.CELEBRATION;
      } else {
        this._currentAutoMode = ARC_MODES.MIRROR;
      }
    }
  }

  /**
   * Get the current effective mode.
   */
  getEffectiveMode() {
    if (this.mode !== ARC_MODES.AUTO) return this.mode;
    return this._currentAutoMode || ARC_MODES.MIRROR;
  }

  /**
   * Get full arc state for visualization.
   */
  getState() {
    return {
      mode: this.mode,
      effectiveMode: this.getEffectiveMode(),
      frustrationScore: this.frustrationScore,
      consecutiveFailures: this.consecutiveFailures,
      injectionCount: this.injectionCount,
      lastMilestone: this.lastMilestone,
      moodHistoryLength: this.moodHistory.length,
    };
  }

  setMode(mode) {
    if (Object.values(ARC_MODES).includes(mode)) {
      this.mode = mode;
    }
  }
}

// ── Voice Pipeline States (reference from voice-state-machine.ts) ───
// 16 states with explicit timeouts and fallback targets

export const VOICE_STATES = Object.freeze({
  IDLE: 'IDLE',
  REQUESTING_MIC: 'REQUESTING_MIC',
  MIC_GRANTED: 'MIC_GRANTED',
  CONNECTING_CLOUD: 'CONNECTING_CLOUD',
  CLOUD_ACTIVE: 'CLOUD_ACTIVE',
  CLOUD_DEGRADED: 'CLOUD_DEGRADED',
  CONNECTING_PERSONAPLEX: 'CONNECTING_PERSONAPLEX',
  PERSONAPLEX_ACTIVE: 'PERSONAPLEX_ACTIVE',
  PERSONAPLEX_DEGRADED: 'PERSONAPLEX_DEGRADED',
  CONNECTING_LOCAL: 'CONNECTING_LOCAL',
  LOCAL_ACTIVE: 'LOCAL_ACTIVE',
  LOCAL_DEGRADED: 'LOCAL_DEGRADED',
  TEXT_FALLBACK: 'TEXT_FALLBACK',
  ERROR: 'ERROR',
  DISCONNECTING: 'DISCONNECTING',
});

// ── Voice Fallback Priorities ───────────────────────────────────────
// Cascading priority system: personaplex → cloud → local → text

export const VOICE_PATH_PRIORITIES = [
  { path: 'personaplex', priority: 0, label: 'Local GPU (PersonaPlex)' },
  { path: 'cloud', priority: 1, label: 'Cloud (Gemini WebSocket)' },
  { path: 'local', priority: 2, label: 'Local (Whisper+Ollama+TTS)' },
  { path: 'text', priority: 99, label: 'Text Fallback (universal)' },
];
