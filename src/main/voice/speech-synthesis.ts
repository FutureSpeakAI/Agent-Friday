/**
 * speech-synthesis.ts -- SpeechSynthesis manager for Agent Friday.
 *
 * Manages utterance queuing, interrupts, pause/resume, and audio output
 * coordination. Sends synthesized audio chunks to renderer via IPC.
 *
 * Sprint 4 K.3: "The Utterance" -- SpeechSynthesis
 */

import { BrowserWindow } from 'electron';
import { ttsEngine } from './tts-engine';
import { voiceProfileManager } from './voice-profile-manager';

// -- Types --------------------------------------------------------------------

export interface UtteranceEvent {
  text: string;
  profileId: string;
  duration: number;
}

export interface SpeakOptions {
  profileId?: string;
}

type SynthEventName = 'utterance-start' | 'utterance-end' | 'queue-empty' | 'interrupted';
type SynthEventCallback = (event?: UtteranceEvent) => void;

interface QueuedUtterance {
  text: string;
  opts?: SpeakOptions;
  resolve: () => void;
  reject: (err: Error) => void;
}

// -- Constants ----------------------------------------------------------------

const MAX_QUEUE_DEPTH = 5;
const SAMPLE_RATE = 24_000;
const SENTENCE_SPLIT_RE = /(?<=[.!?])\s+/;

function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

// -- SpeechSynthesisManager ---------------------------------------------------

export class SpeechSynthesisManager {
  private static instance: SpeechSynthesisManager | null = null;
  private queue: QueuedUtterance[] = [];
  private speaking = false;
  private paused = false;
  private generation = 0;
  private listeners = new Map<SynthEventName, Set<SynthEventCallback>>();
  private processing = false;

  private constructor() {}

  static getInstance(): SpeechSynthesisManager {
    if (!SpeechSynthesisManager.instance) {
      SpeechSynthesisManager.instance = new SpeechSynthesisManager();
    }
    return SpeechSynthesisManager.instance;
  }

  static resetInstance(): void {
    if (SpeechSynthesisManager.instance) {
      SpeechSynthesisManager.instance.stop();
    }
    SpeechSynthesisManager.instance = null;
  }

  speak(text: string, opts?: SpeakOptions): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      this.queue.push({ text, opts, resolve, reject });
      this.enforceQueueDepth();
      if (!this.processing && !this.paused) {
        void this.processLoop();
      }
    });
  }

  speakImmediate(text: string): Promise<void> {
    this.generation++;
    this.clearQueue();
    this.speaking = false;
    this.processing = false;
    this.emit('interrupted');

    return new Promise<void>((resolve, reject) => {
      this.queue.push({ text, resolve, reject });
      void this.processLoop();
    });
  }

  stop(): void {
    this.generation++;
    this.speaking = false;
    this.paused = false;
    this.processing = false;
    this.clearQueue();
  }

  pause(): void {
    this.paused = true;
  }

  resume(): void {
    if (!this.paused) return;
    this.paused = false;
    if (this.queue.length > 0 && !this.processing) {
      void this.processLoop();
    }
  }

  isSpeaking(): boolean {
    return this.speaking;
  }

  getQueueLength(): number {
    return this.queue.length;
  }

  on(event: SynthEventName, cb: SynthEventCallback): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    const set = this.listeners.get(event);
    set!.add(cb);
    return () => {
      this.listeners.get(event)?.delete(cb);
    };
  }

  private async processLoop(): Promise<void> {
    if (this.processing) return;
    this.processing = true;
    const gen = this.generation;

    while (this.queue.length > 0 && !this.paused && gen === this.generation) {
      const item = this.queue.shift()!;
      this.speaking = true;

      try {
        await this.synthesizeUtterance(item.text, item.opts, gen);
        item.resolve();
      } catch (err) {
        item.reject(err instanceof Error ? err : new Error(String(err)));
      }

      // If generation changed, another loop took over
      if (gen !== this.generation) return;

      if (this.queue.length > 0 && !this.paused) {
        await yieldToEventLoop();
      }
    }

    // Only update state if this loop is still the active one
    if (gen === this.generation) {
      this.speaking = false;
      this.processing = false;
      if (this.queue.length === 0) {
        this.emit('queue-empty');
      }
    }
  }

  private async synthesizeUtterance(
    text: string,
    opts: SpeakOptions | undefined,
    gen: number,
  ): Promise<void> {
    const sentences = this.chunkText(text);
    const profileId = opts?.profileId ?? voiceProfileManager.getActiveProfile().id;
    const profile = voiceProfileManager.getActiveProfile();

    const startEvent: UtteranceEvent = { text, profileId, duration: 0 };
    this.emit('utterance-start', startEvent);

    let totalDuration = 0;

    for (const sentence of sentences) {
      if (gen !== this.generation) return;

      try {
        const audio = await ttsEngine.synthesize(sentence, {
          voiceId: profile.voiceId,
          speed: profile.speed,
          pitch: profile.pitch,
        });

        if (gen !== this.generation) return;

        const duration = audio.length / SAMPLE_RATE;
        totalDuration += duration;
        this.sendAudioToRenderer(audio);
      } catch (err) {
        console.warn(`[SpeechSynthesis] Failed to synthesize sentence, skipping: "${sentence.slice(0, 60)}"`, err);
        // Skip this sentence and continue with the rest — partial speech is
        // better than total silence.
      }
    }

    const endEvent: UtteranceEvent = { text, profileId, duration: totalDuration };
    this.emit('utterance-end', endEvent);
  }

  private chunkText(text: string): string[] {
    const chunks = text.split(SENTENCE_SPLIT_RE).filter((s) => s.trim().length > 0);
    return chunks.length > 0 ? chunks : [text];
  }

  private sendAudioToRenderer(audio: Float32Array): void {
    try {
      const windows = BrowserWindow.getAllWindows();
      for (const win of windows) {
        win.webContents.send('voice:play-chunk', audio);
      }
    } catch {
      // IPC not available
    }
  }

  private enforceQueueDepth(): void {
    while (this.queue.length > MAX_QUEUE_DEPTH) {
      const dropped = this.queue.shift();
      if (dropped) {
        dropped.reject(new Error('Utterance dropped during flush'));
      }
    }
  }

  private clearQueue(): void {
    const items = this.queue.splice(0);
    for (const item of items) {
      item.reject(new Error('Utterance dropped during flush'));
    }
  }

  private emit(event: SynthEventName, data?: UtteranceEvent): void {
    const cbs = this.listeners.get(event);
    if (cbs) {
      for (const cb of cbs) {
        cb(data);
      }
    }
  }
}

export const speechSynthesis = SpeechSynthesisManager.getInstance();
