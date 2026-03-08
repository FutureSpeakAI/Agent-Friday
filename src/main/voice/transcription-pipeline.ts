/**
 * transcription-pipeline.ts -- The nerve fiber connecting perception to cognition.
 *
 * Wires AudioCapture (VAD events + audio chunks) to WhisperProvider (transcription).
 * Manages the full lifecycle: start/stop, audio buffering during speech, periodic
 * partial transcription for long utterances, final transcription on voice-end,
 * error handling, and performance stats tracking.
 *
 * Sprint 4 J.3: "The Stream" -- TranscriptionPipeline
 */

import { audioCapture } from '../voice/audio-capture';
import { whisperProvider } from '../voice/whisper-provider';

// -- Types --------------------------------------------------------------------

export interface TranscriptEvent {
  text: string;
  language: string;
  duration: number;
  latencyMs: number;
  segments: Array<{ text: string; start: number; end: number }>;
}

export interface TranscriptionStats {
  totalTranscriptions: number;
  averageLatencyMs: number;
  totalAudioDurationSec: number;
}

type PipelineEvent = 'transcript' | 'partial' | 'error';
type EventCallback = (payload: unknown) => void;

const SAMPLE_RATE = 16_000;
const PARTIAL_INTERVAL_MS = 2000;

export class TranscriptionPipeline {
  private static instance: TranscriptionPipeline | null = null;

  private listening = false;
  private buffering = false;
  private audioBuffer: Float32Array[] = [];
  private bufferStartTime = 0;
  private lastPartialTime = 0;
  private unsubscribers: Array<() => void> = [];
  private listeners = new Map<PipelineEvent, Set<EventCallback>>();
  private totalTranscriptions = 0;
  private totalLatencyMs = 0;
  private totalAudioDurationSec = 0;
  private transcriptionQueue: Float32Array[] = [];
  private processing = false;

  private constructor() {}

  static getInstance(): TranscriptionPipeline {
    if (!TranscriptionPipeline.instance) {
      TranscriptionPipeline.instance = new TranscriptionPipeline();
    }
    return TranscriptionPipeline.instance;
  }

  static resetInstance(): void {
    if (TranscriptionPipeline.instance) {
      TranscriptionPipeline.instance.stop();
      TranscriptionPipeline.instance.listeners.clear();
    }
    TranscriptionPipeline.instance = null;
  }

  async start(): Promise<void> {
    if (this.listening) return;

    try {
      await whisperProvider.loadModel();
    } catch (err) {
      this.emit('error', err instanceof Error ? err : new Error(String(err)));
      return;
    }

    this.unsubscribers.push(
      audioCapture.on('voice-start', () => this.onVoiceStart()),
      audioCapture.on('voice-end', (payload) => this.onVoiceEnd(payload as Float32Array)),
      audioCapture.on('audio-chunk', (payload) => this.onAudioChunk(payload as Float32Array)),
      audioCapture.on('error', (payload) => this.onAudioError(payload as Error)),
    );

    await audioCapture.startCapture();
    this.listening = true;
  }

  stop(): void {
    if (!this.listening) return;
    for (const unsub of this.unsubscribers) { unsub(); }
    this.unsubscribers = [];
    audioCapture.stopCapture();
    this.listening = false;
    this.buffering = false;
    this.audioBuffer = [];
    this.transcriptionQueue = [];
    this.processing = false;
  }

  isListening(): boolean {
    return this.listening;
  }

  on(event: PipelineEvent, cb: EventCallback): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(cb);
    return () => { this.listeners.get(event)?.delete(cb); };
  }

  getStats(): TranscriptionStats {
    return {
      totalTranscriptions: this.totalTranscriptions,
      averageLatencyMs: this.totalTranscriptions > 0
        ? this.totalLatencyMs / this.totalTranscriptions : 0,
      totalAudioDurationSec: this.totalAudioDurationSec,
    };
  }

  private onVoiceStart(): void {
    this.buffering = true;
    this.audioBuffer = [];
    this.bufferStartTime = Date.now();
    this.lastPartialTime = Date.now();
  }

  private onAudioChunk(chunk: Float32Array): void {
    if (!this.buffering) return;
    this.audioBuffer.push(chunk);
    const elapsed = Date.now() - this.bufferStartTime;
    const sinceLast = Date.now() - this.lastPartialTime;
    if (elapsed >= PARTIAL_INTERVAL_MS && sinceLast >= PARTIAL_INTERVAL_MS) {
      this.lastPartialTime = Date.now();
      const merged = this.mergeBuffers(this.audioBuffer);
      void this.transcribePartial(merged);
    }
  }

  private onVoiceEnd(buffer: Float32Array): void {
    this.buffering = false;
    this.audioBuffer = [];
    this.transcriptionQueue.push(buffer);
    void this.processQueue();
  }

  private onAudioError(error: Error): void {
    this.emit('error', error);
    this.listening = false;
    this.buffering = false;
    this.audioBuffer = [];
  }

  private async processQueue(): Promise<void> {
    if (this.processing) return;
    this.processing = true;
    while (this.transcriptionQueue.length > 0) {
      const buffer = this.transcriptionQueue.shift()!;
      await this.transcribeFinal(buffer);
    }
    this.processing = false;
  }

  private async transcribeFinal(buffer: Float32Array): Promise<void> {
    try {
      const result = await whisperProvider.transcribe(buffer);
      this.totalTranscriptions++;
      this.totalLatencyMs += result.processingTime;
      this.totalAudioDurationSec += result.duration;
      const event: TranscriptEvent = {
        text: result.text,
        language: result.language,
        duration: result.duration,
        latencyMs: result.processingTime,
        segments: result.segments,
      };
      this.emit('transcript', event);
    } catch (err) {
      this.emit('error', err instanceof Error ? err : new Error(String(err)));
    }
  }

  private async transcribePartial(buffer: Float32Array): Promise<void> {
    try {
      const result = await whisperProvider.transcribe(buffer);
      const event: TranscriptEvent = {
        text: result.text,
        language: result.language,
        duration: result.duration,
        latencyMs: result.processingTime,
        segments: result.segments,
      };
      this.emit('partial', event);
    } catch {
      // Partial transcription failures are non-fatal
    }
  }

  private mergeBuffers(buffers: Float32Array[]): Float32Array {
    const totalLength = buffers.reduce((acc, buf) => acc + buf.length, 0);
    const result = new Float32Array(totalLength);
    let offset = 0;
    for (const buf of buffers) {
      result.set(buf, offset);
      offset += buf.length;
    }
    return result;
  }

  private emit(event: PipelineEvent, payload: unknown): void {
    const callbacks = this.listeners.get(event);
    if (callbacks) {
      for (const cb of callbacks) { cb(payload); }
    }
  }
}

export const transcriptionPipeline = TranscriptionPipeline.getInstance();
