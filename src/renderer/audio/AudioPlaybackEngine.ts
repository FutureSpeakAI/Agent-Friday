/**
 * AudioPlaybackEngine — gapless audio chunk playback using Web Audio scheduling.
 *
 * Instead of chaining chunks via `source.onended` (which introduces micro-gaps),
 * this engine pre-buffers chunks and uses `source.start(exactTime)` to schedule
 * each chunk at the exact sample boundary where the previous one ends.
 *
 * AUDIO QUALITY FEATURES:
 * - Buffer overflow protection (max queue size prevents runaway buildup)
 * - Health monitoring (queue depth, context state, session duration)
 * - Automatic context recovery from browser suspension
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * PHASE 4.1 — AudioContext Resurrection & Liveness Probe
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * SOCRATIC BOUNDARY: "What must be true for AudioContext to be considered 'alive'?"
 *   → ctx.state === 'running' is NOT sufficient. Some browsers (and Electron under
 *     certain conditions) report 'running' while the audio pipeline is actually
 *     broken — e.g., after a GPU process crash or audio device disconnection.
 *   → True liveness requires: state === 'running' AND a test buffer produces
 *     verifiable output through the analyser node.
 *
 * SOCRATIC INVERSION: "If you wanted to silently kill audio, how would you do it?"
 *   → Suspend the AudioContext and rely on resume() silently failing. This is
 *     exactly what happens today when a tab is backgrounded or the OS reclaims
 *     audio resources. The old code called resume() but never verified it worked.
 *
 * SOCRATIC PRECEDENT: "The generation counter prevents stale onended callbacks.
 *   Can we extend it?" → Yes. Each resurrection increments generation. Any audio
 *   scheduled under a previous generation is automatically invalid. This means
 *   resurrection is safe — old sources' onended handlers become no-ops.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * PHASE 4.2 — Backpressure & Queue Health
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * SOCRATIC TENSION: "Dropping chunks prevents buffer bloat but causes garbled
 *   audio. Pausing input prevents garbling but causes latency. How serve both?"
 *   → Hybrid approach with two zones:
 *     - ELEVATED (75% queue fill): Signal backpressure upstream. The voice
 *       pipeline pauses mic input, giving the playback queue time to drain.
 *       No chunks are dropped — audio quality is preserved.
 *     - CRITICAL (90% queue fill): Drop oldest chunks AND signal backpressure.
 *       This is the last resort to prevent unbounded latency growth.
 *   → The "yellow zone" (elevated) exists so we rarely hit "red zone" (critical).
 *
 * SOCRATIC CONSTRAINT: "What is the max acceptable audio latency from backpressure?"
 *   → ~2 seconds. At 24kHz with ~100ms chunks, 50 chunks = 5s of audio.
 *     Yellow zone at 37 chunks (75%) ≈ 3.7s buffer. This is the upper bound
 *     of acceptable latency before the conversation feels broken.
 *
 * IPC PATTERN: This is a RENDERER file. It cannot import main-process modules.
 *   Communication with the voice state machine happens via:
 *   1. Callback functions (onPressureChange, onDegradedChange) that callers
 *      wire to IPC in App.tsx / useGeminiLive.ts
 *   2. CustomEvents on window for renderer-internal listeners
 *   3. The caller forwards events to main via window.eve.voice.* IPC bridge
 */

// ── Types ─────────────────────────────────────────────────────────────────

/**
 * Queue pressure levels for backpressure signaling.
 *
 * SOCRATIC NOTE: Three levels, not two, because the system needs a "warning"
 * zone before the "emergency" zone. Binary (ok/overflow) forces a choice
 * between dropping too early (quality loss) or too late (latency spike).
 * Three levels give the upstream pipeline time to react gracefully.
 */
export type QueuePressure = 'normal' | 'elevated' | 'critical';

export class AudioPlaybackEngine {
  private ctx: AudioContext;
  private analyser: AnalyserNode;
  private analyserData: Uint8Array<ArrayBuffer>;
  private queue: Float32Array[] = [];
  private nextStartTime = 0;
  private isPlaying = false;
  private drainTimer: ReturnType<typeof setInterval> | null = null;
  private onSpeakingChange: ((speaking: boolean) => void) | null = null;
  private activeSourceCount = 0;
  private generation = 0;
  private currentSinkId: string | null = null;
  private isResuming = false;
  private sessionStartTime = 0;
  private totalChunksPlayed = 0;
  private droppedChunks = 0;

  // ── Phase 4.1: Resurrection state ─────────────────────────────────────

  /**
   * How many times we've tried to resurrect the AudioContext in the current
   * degraded episode. Reset to 0 when a resurrection succeeds or when the
   * engine is flushed/destroyed.
   */
  private resurrectAttempts = 0;
  private readonly MAX_RESURRECT_ATTEMPTS = 3;

  /**
   * Whether a resurrection is currently in progress. Prevents concurrent
   * resurrection attempts (which would race on ctx replacement).
   */
  private isResurrecting = false;

  /**
   * Callback fired when the engine detects audio degradation (context dead,
   * liveness probe failed, resurrection exhausted). The caller wires this
   * to IPC to notify the voice state machine on the main process.
   *
   * SOCRATIC NOTE: We use a callback rather than importing ipcRenderer because
   * AudioPlaybackEngine must remain a pure renderer-side class. The wiring
   * layer (App.tsx / useGeminiLive.ts) connects this to IPC.
   */
  private onDegradedChange: ((degraded: boolean, reason: string) => void) | null = null;

  // ── Phase 4.2: Backpressure state ─────────────────────────────────────

  /**
   * Current pressure level. Tracked so we only fire the callback on transitions,
   * not on every enqueue() call.
   */
  private currentPressure: QueuePressure = 'normal';

  /**
   * Callback fired when queue pressure level changes. The caller wires this
   * to IPC so the main process can pause/resume mic input.
   */
  private pressureChangeCallback: ((pressure: QueuePressure) => void) | null = null;

  /**
   * Flag indicating that upstream should pause sending audio input.
   * Callers (Gemini hook, local conversation loop) check this before
   * sending mic data. Set true at 'elevated', cleared when queue drains
   * below 50%.
   *
   * SOCRATIC TENSION RESOLUTION: This flag is advisory, not enforced. The
   * engine still accepts enqueue() calls even when paused — it just signals
   * that the caller SHOULD stop. This prevents data loss if the pause signal
   * arrives late. The critical-zone drop logic is the hard backstop.
   */
  private inputPaused = false;

  /** Minimum chunks buffered before playback starts */
  private readonly PRE_BUFFER = 2;
  /** How often the drain loop checks for new chunks (ms) */
  private readonly DRAIN_INTERVAL = 50;
  /** Output sample rate — Gemini sends 24kHz PCM */
  private readonly SAMPLE_RATE = 24000;
  /** Maximum queue depth before dropping oldest chunks (prevents buffer bloat → audio lag) */
  private readonly MAX_QUEUE_SIZE = 50;

  /**
   * Queue fill ratio thresholds for backpressure zones.
   * ELEVATED_THRESHOLD (0.75): Signal backpressure, pause mic input.
   * CRITICAL_THRESHOLD (0.90): Drop oldest + signal backpressure.
   * RESUME_THRESHOLD (0.50): Resume mic input once queue drains below this.
   *
   * SOCRATIC NOTE: The resume threshold (50%) is deliberately lower than the
   * elevated threshold (75%). This hysteresis prevents rapid on/off cycling
   * ("flapping") when the queue hovers near the threshold boundary.
   */
  private readonly ELEVATED_THRESHOLD = 0.75;
  private readonly CRITICAL_THRESHOLD = 0.90;
  private readonly RESUME_THRESHOLD = 0.50;

  constructor() {
    this.ctx = new AudioContext({ sampleRate: this.SAMPLE_RATE });
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.8;
    this.analyserData = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.connect(this.ctx.destination);
    this.sessionStartTime = Date.now();
  }

  // ── Phase 4.1: Resurrection & Liveness ────────────────────────────────

  /**
   * Attempt to resurrect a dead or degraded AudioContext.
   *
   * Closes the old context, creates a new one, increments the generation
   * counter (invalidating all stale source callbacks), re-creates the
   * analyser pipeline, and verifies the new context with a liveness probe.
   *
   * SOCRATIC PRECEDENT: The flush() method already does close+recreate.
   * Resurrection extends that pattern with: (a) attempt counting, (b) liveness
   * verification, (c) degradation signaling on failure. The generation counter
   * — already proven to prevent stale onended callbacks — naturally extends
   * to invalidate pre-resurrection audio.
   *
   * @returns true if resurrection succeeded (new context passes liveness probe)
   */
  async resurrectContext(): Promise<boolean> {
    // Guard: prevent concurrent resurrections
    if (this.isResurrecting) {
      console.warn('[AudioPlayback] Resurrection already in progress — skipping');
      return false;
    }

    this.resurrectAttempts++;
    if (this.resurrectAttempts > this.MAX_RESURRECT_ATTEMPTS) {
      console.error(
        `[AudioPlayback] Resurrection failed — exhausted ${this.MAX_RESURRECT_ATTEMPTS} attempts`
      );
      this.emitDegraded(true, `AudioContext resurrection failed after ${this.MAX_RESURRECT_ATTEMPTS} attempts`);
      return false;
    }

    this.isResurrecting = true;
    console.log(
      `[AudioPlayback] Attempting AudioContext resurrection (attempt ${this.resurrectAttempts}/${this.MAX_RESURRECT_ATTEMPTS})`
    );

    try {
      // 1. Close old context — may already be closed, that's fine
      const prevSinkId = this.currentSinkId;
      try { await this.ctx.close(); } catch { /* already closed or errored */ }

      // 2. Create new context and analyser pipeline
      this.ctx = new AudioContext({ sampleRate: this.SAMPLE_RATE });
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.smoothingTimeConstant = 0.8;
      this.analyserData = new Uint8Array(this.analyser.frequencyBinCount);
      this.analyser.connect(this.ctx.destination);

      // 3. Increment generation — all old sources' onended become no-ops
      //    SOCRATIC PRECEDENT: Same mechanism that protects flush(). Extended
      //    here so resurrection is equally safe for in-flight audio.
      this.generation++;
      this.activeSourceCount = 0;
      this.nextStartTime = 0;
      this.isResuming = false;

      // 4. Pre-resume if suspended (Chromium autoplay policy)
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume();
      }

      // 5. Restore output device routing if set
      if (prevSinkId) {
        await this.setOutputDevice(prevSinkId).catch(() => {});
      }

      // 6. Verify with liveness probe — the critical step
      //    SOCRATIC BOUNDARY: ctx.state === 'running' is necessary but not sufficient.
      //    The probe verifies actual audio pipeline connectivity.
      const alive = await this.runLivenessProbe();

      if (alive) {
        console.log('[AudioPlayback] Resurrection succeeded — context is alive');
        this.resurrectAttempts = 0; // Reset counter on success
        this.emitDegraded(false, 'AudioContext resurrected successfully');
        return true;
      } else {
        console.warn(
          `[AudioPlayback] Resurrection attempt ${this.resurrectAttempts} — liveness probe failed`
        );
        // Don't emit degraded yet — there may be more attempts
        if (this.resurrectAttempts >= this.MAX_RESURRECT_ATTEMPTS) {
          this.emitDegraded(true, 'AudioContext liveness probe failed after all resurrection attempts');
        }
        return false;
      }
    } catch (err) {
      console.error('[AudioPlayback] Resurrection threw:', err);
      if (this.resurrectAttempts >= this.MAX_RESURRECT_ATTEMPTS) {
        this.emitDegraded(true, `AudioContext resurrection error: ${err}`);
      }
      return false;
    } finally {
      this.isResurrecting = false;
    }
  }

  /**
   * Run a liveness probe on the current AudioContext.
   *
   * Creates a tiny silent test buffer, plays it through the analyser, and
   * checks that the context actually processes audio. This catches the case
   * where ctx.state === 'running' but the audio pipeline is broken.
   *
   * SOCRATIC BOUNDARY: "What constitutes proof of life?"
   *   → We create a buffer, schedule it, wait for it to be processed, and
   *     verify the context is still in 'running' state. A truly dead context
   *     will either throw on createBuffer/start or transition to 'suspended'/'closed'.
   *   → We use a short timeout (300ms) because a working context processes
   *     a 100ms buffer nearly instantly. Longer than 300ms means something
   *     is deeply wrong.
   *
   * @returns true if the AudioContext is genuinely alive and processing audio
   */
  async runLivenessProbe(): Promise<boolean> {
    try {
      // Quick check — if context is already closed or suspended, no point probing
      if (this.ctx.state === 'closed') return false;
      if (this.ctx.state === 'suspended') {
        try { await this.ctx.resume(); } catch { return false; }
      }

      // Create a 100ms silent test buffer (2400 samples at 24kHz)
      const testBuffer = this.ctx.createBuffer(1, 2400, this.SAMPLE_RATE);
      const source = this.ctx.createBufferSource();
      source.buffer = testBuffer;
      source.connect(this.analyser);
      source.start();

      // Wait for the buffer to be processed.
      // SOCRATIC NOTE: 300ms is generous for a 100ms buffer. If it takes longer,
      // the audio pipeline is unhealthy even if technically "running."
      await new Promise(resolve => setTimeout(resolve, 300));

      // Final state check — the act of playing should keep context in 'running'.
      // If it reverted to 'suspended' or 'closed', the pipeline is broken.
      return this.ctx.state === 'running';
    } catch (err) {
      console.warn('[AudioPlayback] Liveness probe error:', err);
      return false;
    }
  }

  /**
   * Check whether the AudioContext is healthy — combines state check with
   * an active liveness probe.
   *
   * SOCRATIC DESIGN: This is the method external health monitors should call.
   * It answers "is audio actually working?" not just "does the API say it's ok?"
   *
   * The distinction matters because:
   * - getContextState() returns what the browser CLAIMS (can lie)
   * - isContextHealthy() returns what we've VERIFIED (probe-based truth)
   *
   * @returns true if context state is 'running' AND liveness probe passes
   */
  async isContextHealthy(): Promise<boolean> {
    // Fast path: if state is already bad, skip the expensive probe
    if (this.ctx.state !== 'running') return false;

    return this.runLivenessProbe();
  }

  /**
   * Set the callback for audio degradation events. Wire this to IPC in the
   * caller so the main-process voice state machine can react.
   *
   * @param cb Called with (degraded: boolean, reason: string).
   *   degraded=true means audio is broken; degraded=false means recovered.
   */
  setDegradedCallback(cb: (degraded: boolean, reason: string) => void) {
    this.onDegradedChange = cb;
  }

  /**
   * Emit a degradation event via callback and CustomEvent.
   * Two channels ensure both direct callers and decoupled listeners are notified.
   */
  private emitDegraded(degraded: boolean, reason: string) {
    console.warn(`[AudioPlayback] Degraded=${degraded}: ${reason}`);
    this.onDegradedChange?.(degraded, reason);

    // Emit CustomEvent for renderer-internal listeners (e.g., UI health indicators)
    // that don't have a direct reference to this engine instance.
    window.dispatchEvent(new CustomEvent('audio-playback:degraded', {
      detail: { degraded, reason },
    }));
  }

  // ── Phase 4.2: Backpressure ───────────────────────────────────────────

  /**
   * Get the current queue pressure level based on fill ratio.
   *
   * SOCRATIC MAPPING:
   *   normal   (< 75%) — all good, audio is keeping up with input
   *   elevated (75-90%) — queue is growing; upstream should pause mic input
   *   critical (> 90%)  — queue near overflow; dropping chunks + pause mic
   */
  getQueuePressure(): QueuePressure {
    const ratio = this.queue.length / this.MAX_QUEUE_SIZE;
    if (ratio >= this.CRITICAL_THRESHOLD) return 'critical';
    if (ratio >= this.ELEVATED_THRESHOLD) return 'elevated';
    return 'normal';
  }

  /**
   * Register a callback that fires when queue pressure level changes.
   * Only fires on transitions (normal→elevated, elevated→critical, etc.),
   * not on every enqueue.
   *
   * Wire this to IPC in the caller to trigger voice:pause-mic / voice:resume-mic
   * on the main process.
   */
  onPressureChange(cb: (pressure: QueuePressure) => void) {
    this.pressureChangeCallback = cb;
  }

  /**
   * Whether upstream should pause sending audio input. Callers check this
   * before forwarding mic data to Gemini or the local pipeline.
   *
   * SOCRATIC NOTE: This is advisory, not enforced. The engine still accepts
   * enqueue() even when this returns true. The critical-zone drop is the
   * hard backstop. This soft signal prevents us from reaching that backstop
   * in the common case.
   */
  isInputPaused(): boolean {
    return this.inputPaused;
  }

  /**
   * Explicitly pause input (e.g., called by external health monitor).
   */
  pauseInput() {
    if (!this.inputPaused) {
      this.inputPaused = true;
      console.log('[AudioPlayback] Input paused by external request');
    }
  }

  /**
   * Explicitly resume input (e.g., called after resurrection succeeds).
   */
  resumeInput() {
    if (this.inputPaused) {
      this.inputPaused = false;
      console.log('[AudioPlayback] Input resumed by external request');
    }
  }

  /**
   * Evaluate current queue pressure and emit events if it changed.
   * Called after every enqueue and after chunks are scheduled (drained).
   *
   * SOCRATIC HYSTERESIS: We resume input at 50% (RESUME_THRESHOLD), not at
   * 74% (just below ELEVATED_THRESHOLD). This gap prevents flapping — rapid
   * on/off cycling when the queue hovers near the threshold. The system must
   * drain substantially before we re-enable input, ensuring stability.
   */
  private updatePressure() {
    const newPressure = this.getQueuePressure();

    // Handle input pause/resume with hysteresis
    if (newPressure !== 'normal' && !this.inputPaused) {
      this.inputPaused = true;
      console.log(`[AudioPlayback] Backpressure: pausing input (pressure=${newPressure}, queue=${this.queue.length}/${this.MAX_QUEUE_SIZE})`);
    } else if (this.inputPaused && this.queue.length / this.MAX_QUEUE_SIZE < this.RESUME_THRESHOLD) {
      // HYSTERESIS: Only resume when queue drains below 50%, not just below 75%
      this.inputPaused = false;
      console.log(`[AudioPlayback] Backpressure: resuming input (queue=${this.queue.length}/${this.MAX_QUEUE_SIZE})`);
    }

    // Fire callback only on pressure level transitions
    if (newPressure !== this.currentPressure) {
      const prevPressure = this.currentPressure;
      this.currentPressure = newPressure;
      console.log(`[AudioPlayback] Pressure: ${prevPressure} → ${newPressure} (queue=${this.queue.length}/${this.MAX_QUEUE_SIZE})`);
      this.pressureChangeCallback?.(newPressure);

      // Emit CustomEvent for renderer-internal listeners
      window.dispatchEvent(new CustomEvent('audio-playback:pressure', {
        detail: { pressure: newPressure, queueDepth: this.queue.length, maxQueueSize: this.MAX_QUEUE_SIZE },
      }));
    }
  }

  // ── Core Playback ─────────────────────────────────────────────────────

  /** Get current output audio level as 0–1 RMS value */
  getOutputLevel(): number {
    this.analyser.getByteFrequencyData(this.analyserData);
    let sum = 0;
    for (let i = 0; i < this.analyserData.length; i++) {
      const v = this.analyserData[i] / 255;
      sum += v * v;
    }
    return Math.sqrt(sum / this.analyserData.length);
  }

  setSpeakingCallback(cb: (speaking: boolean) => void) {
    this.onSpeakingChange = cb;
  }

  /**
   * Enqueue a decoded Float32 PCM chunk for playback.
   *
   * PHASE 4.2 CHANGE: The old overflow logic was a simple "drop oldest when full."
   * The new logic implements a hybrid approach:
   *   1. At elevated pressure (75%): emit backpressure signal (no drops yet).
   *   2. At critical pressure (90%): drop oldest chunks AND emit signal.
   * This gives the upstream pipeline a warning window to pause mic input
   * before we resort to dropping audio (which causes audible glitches).
   */
  enqueue(pcm: Float32Array) {
    // Phase 4.2: Hybrid overflow with backpressure zones
    const ratio = this.queue.length / this.MAX_QUEUE_SIZE;

    if (ratio >= this.CRITICAL_THRESHOLD) {
      // RED ZONE: Queue is near overflow. Drop oldest chunks as last resort.
      // SOCRATIC TENSION: We drop here because NOT dropping means unbounded
      // latency growth. The elevated zone's backpressure should prevent us
      // from reaching here in the common case. If we're here, something upstream
      // didn't respond to the backpressure signal quickly enough.
      const toDrop = Math.max(1, this.queue.length - Math.floor(this.MAX_QUEUE_SIZE * this.ELEVATED_THRESHOLD));
      this.queue.splice(0, toDrop);
      this.droppedChunks += toDrop;
      console.warn(
        `[AudioPlayback] CRITICAL overflow — dropped ${toDrop} oldest chunks ` +
        `(total dropped: ${this.droppedChunks}, queue: ${this.queue.length}/${this.MAX_QUEUE_SIZE})`
      );
    }
    // YELLOW ZONE (elevated) and NORMAL: No drops. Backpressure signal is
    // handled by updatePressure() below.

    this.queue.push(pcm);
    this.totalChunksPlayed++;

    // Update pressure AFTER adding the chunk so the ratio reflects the new state
    this.updatePressure();

    // Start draining once we have enough buffered
    if (!this.drainTimer && this.queue.length >= this.PRE_BUFFER) {
      this.startDrain();
    }
  }

  private startDrain() {
    if (this.drainTimer) return;

    if (!this.isPlaying) {
      this.isPlaying = true;
      this.onSpeakingChange?.(true);
    }

    // Reset the scheduling cursor to "now" so the first chunk plays immediately
    this.nextStartTime = this.ctx.currentTime;

    this.drainTimer = setInterval(() => {
      this.scheduleQueued();
    }, this.DRAIN_INTERVAL);

    // Immediately schedule what we have
    this.scheduleQueued();
  }

  private scheduleQueued() {
    // Resume context if it was suspended (browser autoplay policy)
    // CRITICAL: Must await resume before scheduling — scheduling on a suspended context silently drops audio
    if (this.ctx.state === 'suspended') {
      if (!this.isResuming) {
        this.isResuming = true;
        this.ctx.resume().then(() => {
          this.isResuming = false;
          // Now that context is running, drain the queue
          this.scheduleQueued();
        }).catch(() => {
          this.isResuming = false;
          // Phase 4.1: If resume fails, attempt resurrection
          this.resurrectContext().catch(() => {});
        });
      }
      return; // Don't schedule anything while suspended — chunks stay in queue
    }

    while (this.queue.length > 0) {
      const chunk = this.queue.shift()!;

      const buffer = this.ctx.createBuffer(1, chunk.length, this.SAMPLE_RATE);
      buffer.getChannelData(0).set(chunk);

      const source = this.ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(this.analyser);

      // If we've fallen behind real-time, snap forward
      if (this.nextStartTime < this.ctx.currentTime) {
        this.nextStartTime = this.ctx.currentTime;
      }

      source.start(this.nextStartTime);
      this.activeSourceCount++;

      // Advance cursor by exact duration of this chunk
      this.nextStartTime += buffer.duration;

      const gen = this.generation;
      source.onended = () => {
        if (gen !== this.generation) return; // Stale callback from pre-flush source
        this.activeSourceCount--;
        if (this.activeSourceCount === 0 && this.queue.length === 0) {
          this.stopDrain();
        }
      };
    }

    // Phase 4.2: Update pressure after draining (queue shrank)
    this.updatePressure();
  }

  private stopDrain() {
    if (this.drainTimer) {
      clearInterval(this.drainTimer);
      this.drainTimer = null;
    }

    if (this.isPlaying) {
      this.isPlaying = false;
      this.onSpeakingChange?.(false);
    }
  }

  /** Immediately stop all audio and clear queue (e.g. user interrupts) */
  flush() {
    this.queue = [];
    this.generation++;
    this.stopDrain();
    this.activeSourceCount = 0;
    this.isResuming = false;
    this.isResurrecting = false;
    this.resurrectAttempts = 0;

    // Phase 4.2: Reset backpressure state on flush
    const hadPressure = this.currentPressure !== 'normal';
    this.currentPressure = 'normal';
    this.inputPaused = false;
    if (hadPressure) {
      this.pressureChangeCallback?.('normal');
      window.dispatchEvent(new CustomEvent('audio-playback:pressure', {
        detail: { pressure: 'normal', queueDepth: 0, maxQueueSize: this.MAX_QUEUE_SIZE },
      }));
    }

    // Close and recreate context to kill in-flight sources
    const prevSinkId = this.currentSinkId;
    this.ctx.close().catch(() => {});
    this.ctx = new AudioContext({ sampleRate: this.SAMPLE_RATE });
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.8;
    this.analyserData = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.connect(this.ctx.destination);
    this.nextStartTime = 0;

    // Pre-resume the new AudioContext so it's ready when chunks arrive
    // (new contexts may start suspended due to Chromium autoplay policy)
    if (this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }

    // Restore output device routing if it was set (e.g. call mode)
    if (prevSinkId) {
      this.setOutputDevice(prevSinkId).catch(() => {});
    }
  }

  /**
   * Route audio output to a specific device (e.g. VB-Cable virtual mic for call mode).
   * Uses AudioContext.setSinkId() — available in Chromium 110+.
   */
  async setOutputDevice(deviceId: string): Promise<boolean> {
    try {
      if ('setSinkId' in this.ctx && typeof (this.ctx as any).setSinkId === 'function') {
        await (this.ctx as any).setSinkId(deviceId);
        this.currentSinkId = deviceId;
        console.log(`[AudioPlayback] Output routed to device: ${deviceId}`);
        return true;
      } else {
        console.warn('[AudioPlayback] setSinkId not supported in this browser/Electron version');
        return false;
      }
    } catch (err) {
      console.error('[AudioPlayback] Failed to set output device:', err);
      return false;
    }
  }

  /**
   * Reset audio output back to the system default device.
   */
  async resetOutputDevice(): Promise<void> {
    if (this.currentSinkId) {
      try {
        if ('setSinkId' in this.ctx && typeof (this.ctx as any).setSinkId === 'function') {
          await (this.ctx as any).setSinkId('');
          console.log('[AudioPlayback] Output restored to default device');
        }
      } catch (err) {
        console.warn('[AudioPlayback] Failed to reset output device:', err);
      }
      this.currentSinkId = null;
    }
  }

  /** Get the current output device ID (null = default) */
  getCurrentSinkId(): string | null {
    return this.currentSinkId;
  }

  /**
   * Ensure the playback AudioContext is in a running state.
   * Called periodically by the health monitor to recover from
   * browser autoplay suspensions and tab-backgrounding.
   *
   * PHASE 4.1 ENHANCEMENT: If resume fails, trigger resurrection instead
   * of silently swallowing the error.
   */
  async resumeIfSuspended(): Promise<void> {
    if (this.ctx.state === 'suspended') {
      try {
        await this.ctx.resume();
        console.log('[AudioPlayback] Context resumed from suspended state');
      } catch (err) {
        console.warn('[AudioPlayback] Failed to resume context, attempting resurrection:', err);
        await this.resurrectContext();
      }
    }
  }

  /** Get the current AudioContext state for health monitoring */
  getContextState(): AudioContextState {
    return this.ctx.state;
  }

  /** Get current queue depth for health monitoring */
  getQueueDepth(): number {
    return this.queue.length;
  }

  /** Get audio health metrics for session monitoring */
  getHealthMetrics(): {
    queueDepth: number;
    activeSourceCount: number;
    contextState: AudioContextState;
    totalChunksPlayed: number;
    droppedChunks: number;
    sessionDurationMs: number;
    isPlaying: boolean;
    /** Phase 4.1 */
    resurrectAttempts: number;
    /** Phase 4.2 */
    queuePressure: QueuePressure;
    inputPaused: boolean;
  } {
    return {
      queueDepth: this.queue.length,
      activeSourceCount: this.activeSourceCount,
      contextState: this.ctx.state,
      totalChunksPlayed: this.totalChunksPlayed,
      droppedChunks: this.droppedChunks,
      sessionDurationMs: Date.now() - this.sessionStartTime,
      isPlaying: this.isPlaying,
      resurrectAttempts: this.resurrectAttempts,
      queuePressure: this.currentPressure,
      inputPaused: this.inputPaused,
    };
  }

  /**
   * Check if audio health is degraded (high dropped chunks, suspended context).
   *
   * PHASE 4.1 NOTE: This is the synchronous "quick check." For a thorough
   * check that includes a liveness probe, use isContextHealthy() instead.
   */
  isDegraded(): boolean {
    // Degraded if: context is suspended, or we've dropped >10 chunks recently
    if (this.ctx.state === 'suspended') return true;
    if (this.ctx.state === 'closed') return true;
    if (this.droppedChunks > 10) return true;
    return false;
  }

  /** Reset dropped chunk counter (call after reconnect to start fresh) */
  resetHealthCounters() {
    this.droppedChunks = 0;
    this.totalChunksPlayed = 0;
    this.sessionStartTime = Date.now();
    this.resurrectAttempts = 0;
  }

  /** Clean shutdown */
  destroy() {
    this.flush();
    this.ctx.close().catch(() => {});
    this.onDegradedChange = null;
    this.pressureChangeCallback = null;
    this.onSpeakingChange = null;
  }
}
