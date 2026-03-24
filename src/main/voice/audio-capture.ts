/**
 * audio-capture.ts -- Microphone access and voice activity detection for Agent Friday.
 *
 * Main-process singleton that coordinates with the renderer via IPC to capture
 * audio from the microphone. Audio chunks arrive as Float32Array at 16kHz mono
 * PCM and are processed through an energy-based VAD to detect speech boundaries.
 *
 * The renderer handles getUserMedia(); this module handles:
 * - IPC coordination (start/stop capture commands)
 * - Energy-based VAD (voice-start / voice-end detection)
 * - Audio buffering during speech for WhisperProvider
 * - Audio level metering for UI visualization
 *
 * Sprint 4 J.2: "The Listener" -- AudioCapture
 */

import { ipcMain, BrowserWindow } from 'electron';

// -- Types --------------------------------------------------------------------

export interface AudioCaptureConfig {
  sampleRate: number;         // Default: 16000
  vadThreshold: number;       // Default: 0.01 (energy RMS threshold)
  silenceDuration: number;    // Default: 300 (ms of silence before voice-end)
  maxBufferDuration: number;  // Default: 30000 (ms, max single utterance)
}

type AudioCaptureEvent = 'voice-start' | 'voice-end' | 'audio-chunk' | 'error';
type EventCallback = (payload?: unknown) => void;

// -- Constants ----------------------------------------------------------------

const DEFAULT_CONFIG: AudioCaptureConfig = {
  sampleRate: 16_000,
  vadThreshold: 0.01,
  silenceDuration: 300,
  maxBufferDuration: 30_000,
};

/** Maximum allowed size per audio chunk (1MB). Prevents memory exhaustion from oversized IPC buffers. */
const MAX_AUDIO_CHUNK_SIZE = 1024 * 1024;

// -- Helpers ------------------------------------------------------------------

/** Compute RMS (root mean square) energy of an audio buffer. */
function computeRms(buffer: Float32Array): number {
  if (buffer.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < buffer.length; i++) {
    sum += buffer[i] * buffer[i];
  }
  return Math.sqrt(sum / buffer.length);
}

// -- AudioCapture -------------------------------------------------------------

export class AudioCapture {
  private static instance: AudioCapture | null = null;

  private config: AudioCaptureConfig;
  private capturing = false;
  private currentLevel = 0;
  private inSpeech = false;
  private silenceStart = 0;
  private speechBuffer: Float32Array[] = [];
  private speechBufferDuration = 0;
  private lookbackBuffer: Float32Array[] = [];
  private readonly LOOKBACK_CHUNKS = 2;
  private listeners = new Map<AudioCaptureEvent, Set<EventCallback>>();

  private constructor(config?: Partial<AudioCaptureConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  static getInstance(config?: Partial<AudioCaptureConfig>): AudioCapture {
    if (!AudioCapture.instance) {
      AudioCapture.instance = new AudioCapture(config);
    }
    return AudioCapture.instance;
  }

  static resetInstance(): void {
    if (AudioCapture.instance) {
      AudioCapture.instance.stopCapture();
      AudioCapture.instance.listeners.clear();
    }
    AudioCapture.instance = null;
  }
  // -- Public API -------------------------------------------------------------

  async startCapture(): Promise<void> {
    if (this.capturing) return;

    // Find a renderer window to send the capture command to
    const windows = BrowserWindow.getAllWindows();
    const win = windows.find((w: any) => !w.isDestroyed());

    if (!win) {
      this.emit('error', new Error('No renderer window available for microphone capture'));
      return;
    }

    // Remove any stale listeners before registering (prevents accumulation on repeated start/stop)
    ipcMain.removeListener('voice:audio-chunk', this.handleAudioChunk);
    ipcMain.removeListener('voice:capture-error', this.handleCaptureError);

    // Register IPC listeners for audio data and errors from renderer
    ipcMain.on('voice:audio-chunk', this.handleAudioChunk);
    ipcMain.on('voice:capture-error', this.handleCaptureError);

    // Tell renderer to start capturing
    (win as any).webContents.send('voice:start-capture');

    this.capturing = true;
    this.inSpeech = false;
    this.silenceStart = 0;
    this.speechBuffer = [];
    this.speechBufferDuration = 0;
    this.lookbackBuffer = [];
    this.currentLevel = 0;
  }

  stopCapture(): void {
    if (!this.capturing) return;

    // Tell renderer to stop capturing
    const windows = BrowserWindow.getAllWindows();
    const win = windows.find((w: any) => !w.isDestroyed());
    if (win) {
      (win as any).webContents.send('voice:stop-capture');
    }

    // Clean up IPC listeners
    ipcMain.removeListener('voice:audio-chunk', this.handleAudioChunk);
    ipcMain.removeListener('voice:capture-error', this.handleCaptureError);

    this.capturing = false;
    this.inSpeech = false;
    this.speechBuffer = [];
    this.speechBufferDuration = 0;
    this.lookbackBuffer = [];
    this.currentLevel = 0;
  }

  isCapturing(): boolean {
    return this.capturing;
  }

  getAudioLevel(): number {
    return this.currentLevel;
  }

  on(event: AudioCaptureEvent, callback: EventCallback): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);

    // Return unsubscribe function
    return () => {
      this.listeners.get(event)?.delete(callback);
    };
  }
  // -- Private ----------------------------------------------------------------

  private emit(event: AudioCaptureEvent, payload?: unknown): void {
    const callbacks = this.listeners.get(event);
    if (callbacks) {
      for (const cb of callbacks) {
        cb(payload);
      }
    }
  }

  private handleAudioChunk = (_event: unknown, chunk: Float32Array): void => {
    if (!this.capturing) return;

    // Fix W8: Validate audio buffer size to prevent memory exhaustion
    if (!chunk || !(chunk instanceof Float32Array) || chunk.byteLength > MAX_AUDIO_CHUNK_SIZE) {
      console.warn(
        `[AudioCapture] Oversized or invalid audio chunk rejected: ${chunk?.byteLength ?? 0} bytes`
      );
      return;
    }

    // Compute energy level for UI metering
    const rms = computeRms(chunk);
    this.currentLevel = Math.min(1, rms / 0.5); // Normalize: 0.5 RMS = full scale

    // Emit raw audio chunk event
    this.emit('audio-chunk', chunk);

    // VAD: energy-based voice activity detection
    const isSpeech = rms > this.config.vadThreshold;
    const now = Date.now();
    const chunkDurationMs = (chunk.length / this.config.sampleRate) * 1000;

    if (isSpeech) {
      if (!this.inSpeech) {
        // Speech just started — prepend lookback buffer to capture onset
        this.inSpeech = true;
        this.speechBuffer = [...this.lookbackBuffer];
        this.speechBufferDuration = this.speechBuffer.length * chunkDurationMs;
        this.lookbackBuffer = [];
        this.emit('voice-start');
      }
      this.silenceStart = 0;
    } else if (this.inSpeech) {
      // In speech but current chunk is silence
      if (this.silenceStart === 0) {
        this.silenceStart = now;
      }

      const silenceElapsed = now - this.silenceStart;
      if (silenceElapsed >= this.config.silenceDuration) {
        // Enough silence -- end the utterance
        this.finishUtterance();
        return;
      }
    } else if (!this.inSpeech) {
      // Not in speech — maintain rolling lookback buffer for onset capture
      this.lookbackBuffer.push(chunk);
      if (this.lookbackBuffer.length > this.LOOKBACK_CHUNKS) {
        this.lookbackBuffer.shift();
      }
    }

    // Buffer audio during speech
    if (this.inSpeech) {
      this.speechBuffer.push(chunk);
      this.speechBufferDuration += chunkDurationMs;

      // Enforce max buffer duration
      if (this.speechBufferDuration >= this.config.maxBufferDuration) {
        this.finishUtterance();
      }
    }
  };

  private handleCaptureError = (_event: unknown, errorMessage: string): void => {
    this.emit('error', new Error(errorMessage));
    this.capturing = false;
    this.inSpeech = false;
    this.speechBuffer = [];
    this.speechBufferDuration = 0;

    // Clean up IPC listeners
    ipcMain.removeListener('voice:audio-chunk', this.handleAudioChunk);
    ipcMain.removeListener('voice:capture-error', this.handleCaptureError);
  };

  private finishUtterance(): void {
    // Concatenate all buffered chunks into a single Float32Array
    const totalLength = this.speechBuffer.reduce((acc, buf) => acc + buf.length, 0);
    const result = new Float32Array(totalLength);
    let offset = 0;
    for (const buf of this.speechBuffer) {
      result.set(buf, offset);
      offset += buf.length;
    }

    this.inSpeech = false;
    this.silenceStart = 0;
    this.speechBuffer = [];
    this.speechBufferDuration = 0;

    this.emit('voice-end', result);
  }
}

export const audioCapture = AudioCapture.getInstance();
