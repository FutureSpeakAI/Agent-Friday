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
 */

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
  private currentSinkId: string | null = null;
  private isResuming = false;
  private sessionStartTime = 0;
  private totalChunksPlayed = 0;
  private droppedChunks = 0;

  /** Minimum chunks buffered before playback starts */
  private readonly PRE_BUFFER = 2;
  /** How often the drain loop checks for new chunks (ms) */
  private readonly DRAIN_INTERVAL = 50;
  /** Output sample rate — Gemini sends 24kHz PCM */
  private readonly SAMPLE_RATE = 24000;
  /** Maximum queue depth before dropping oldest chunks (prevents buffer bloat → audio lag) */
  private readonly MAX_QUEUE_SIZE = 50;

  constructor() {
    this.ctx = new AudioContext({ sampleRate: this.SAMPLE_RATE });
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.8;
    this.analyserData = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.connect(this.ctx.destination);
    this.sessionStartTime = Date.now();
  }

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

  /** Enqueue a decoded Float32 PCM chunk for playback */
  enqueue(pcm: Float32Array) {
    // Buffer overflow protection — if queue is too deep, the audio is lagging
    // behind real-time. Drop oldest chunks to catch up (better to skip than lag).
    if (this.queue.length >= this.MAX_QUEUE_SIZE) {
      const toDrop = this.queue.length - this.MAX_QUEUE_SIZE + 5; // Drop 5 extra to create breathing room
      this.queue.splice(0, toDrop);
      this.droppedChunks += toDrop;
      console.warn(`[AudioPlayback] Buffer overflow — dropped ${toDrop} oldest chunks (total dropped: ${this.droppedChunks})`);
    }

    this.queue.push(pcm);
    this.totalChunksPlayed++;

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

      source.onended = () => {
        this.activeSourceCount--;
        if (this.activeSourceCount === 0 && this.queue.length === 0) {
          this.stopDrain();
        }
      };
    }
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
    this.stopDrain();
    this.activeSourceCount = 0;
    this.isResuming = false;

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
   */
  async resumeIfSuspended(): Promise<void> {
    if (this.ctx.state === 'suspended') {
      try {
        await this.ctx.resume();
        console.log('[AudioPlayback] Context resumed from suspended state');
      } catch (err) {
        console.warn('[AudioPlayback] Failed to resume context:', err);
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
  } {
    return {
      queueDepth: this.queue.length,
      activeSourceCount: this.activeSourceCount,
      contextState: this.ctx.state,
      totalChunksPlayed: this.totalChunksPlayed,
      droppedChunks: this.droppedChunks,
      sessionDurationMs: Date.now() - this.sessionStartTime,
      isPlaying: this.isPlaying,
    };
  }

  /** Check if audio health is degraded (high dropped chunks, suspended context) */
  isDegraded(): boolean {
    // Degraded if: context is suspended, or we've dropped >10 chunks recently
    if (this.ctx.state === 'suspended') return true;
    if (this.droppedChunks > 10) return true;
    return false;
  }

  /** Reset dropped chunk counter (call after reconnect to start fresh) */
  resetHealthCounters() {
    this.droppedChunks = 0;
    this.totalChunksPlayed = 0;
    this.sessionStartTime = Date.now();
  }

  /** Clean shutdown */
  destroy() {
    this.flush();
    this.ctx.close().catch(() => {});
  }
}
