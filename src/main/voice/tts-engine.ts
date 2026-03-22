/**
 * tts-engine.ts -- Local text-to-speech engine wrapping Kokoro/Piper.
 *
 * The Mouth. Takes text strings and returns PCM Float32Array audio buffers
 * at 24kHz mono. Manages engine loading, backend fallback (Kokoro -> Piper),
 * readiness checks, voice listing, and an internal queue for sequential
 * processing.
 *
 * Sprint 4 K.1: "The Mouth" -- TTSEngine
 */

import { access } from 'node:fs/promises';
import { readdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { ttsBinding } from './tts-binding';

// -- Constants ----------------------------------------------------------------

/** Output sample rate for TTS audio */
const SAMPLE_RATE = 24_000;

/** Default model directories */
const DEFAULT_TTS_DIR = join(homedir(), '.nexus-os', 'models', 'tts');

/** Model file extension for ONNX models */
const MODEL_FILE_SUFFIX = '.onnx';

/** Backend priority order for auto-detection */
const BACKEND_PRIORITY: TTSBackend[] = ['kokoro', 'piper'];

// -- Types --------------------------------------------------------------------

export type TTSBackend = 'kokoro' | 'piper';

export interface SynthesisOptions {
  voiceId?: string;
  speed?: number;   // 0.5 - 2.0, default 1.0
  pitch?: number;   // -0.5 to 0.5, default 0.0
}

export interface VoiceInfo {
  id: string;
  name: string;
  language: string;
  backend: TTSBackend;
  sampleRate: number;
}

export interface TTSEngineInfo {
  backend: TTSBackend;
  version: string;
  voiceCount: number;
}

interface QueueItem {
  text: string;
  options?: SynthesisOptions;
  resolve: (result: Float32Array) => void;
  reject: (error: Error) => void;
}

// -- TTSEngine ----------------------------------------------------------------

export class TTSEngine {
  private static instance: TTSEngine | null = null;

  private activeBackend: TTSBackend | null = null;
  private engineLoaded = false;
  private voices: VoiceInfo[] = [];
  private ttsDir: string;
  private queue: QueueItem[] = [];
  private processing = false;

  private constructor(ttsDir?: string) {
    this.ttsDir = ttsDir ?? DEFAULT_TTS_DIR;
  }

  static getInstance(ttsDir?: string): TTSEngine {
    if (!TTSEngine.instance) {
      TTSEngine.instance = new TTSEngine(ttsDir);
    }
    return TTSEngine.instance;
  }

  static resetInstance(): void {
    if (TTSEngine.instance) {
      TTSEngine.instance.unloadEngine();
    }
    TTSEngine.instance = null;
  }

  // -- Engine lifecycle -------------------------------------------------------

  async loadEngine(backend?: TTSBackend): Promise<void> {
    const backendsToTry = backend ? [backend] : BACKEND_PRIORITY;

    for (const b of backendsToTry) {
      const backendDir = join(this.ttsDir, b);
      try {
        await access(backendDir);
        const files = await readdir(backendDir);
        const modelFiles = files.filter((f: string) => f.endsWith(MODEL_FILE_SUFFIX));

        if (modelFiles.length === 0) continue;

        // Load the first available model
        const modelPath = join(backendDir, modelFiles[0]);
        await ttsBinding.loadModel(modelPath, { backend: b });

        this.activeBackend = b;
        this.engineLoaded = true;

        // Build voice list from model files
        this.voices = modelFiles.map((f: string) => {
          const id = f.slice(0, -MODEL_FILE_SUFFIX.length);
          return {
            id,
            name: this.formatVoiceName(id),
            language: this.detectLanguage(id),
            backend: b,
            sampleRate: SAMPLE_RATE,
          };
        });

        return;
      } catch {
        // This backend not available, try next
        continue;
      }
    }

    throw new Error(
      'No TTS model found. Install Kokoro or Piper models in ' + this.ttsDir,
    );
  }

  unloadEngine(): void {
    if (this.engineLoaded) {
      ttsBinding.freeModel();
    }
    this.activeBackend = null;
    this.engineLoaded = false;
    this.voices = [];
  }

  isReady(): boolean {
    return this.engineLoaded && this.activeBackend !== null;
  }

  // -- Synthesis --------------------------------------------------------------

  async synthesize(text: string, opts?: SynthesisOptions): Promise<Float32Array> {
    if (text.length === 0) {
      return new Float32Array(0);
    }

    if (!this.isReady()) {
      throw new Error('TTS engine not loaded. Call loadEngine() first.');
    }

    return new Promise<Float32Array>((resolve, reject) => {
      this.queue.push({ text, options: opts, resolve, reject });
      void this.processQueue();
    });
  }

  async *synthesizeStream(
    text: string,
    opts?: SynthesisOptions,
  ): AsyncGenerator<Float32Array> {
    if (!this.isReady()) {
      throw new Error('TTS engine not loaded. Call loadEngine() first.');
    }

    const bindingOpts = opts
      ? { speed: opts.speed, pitch: opts.pitch, voiceId: opts.voiceId }
      : undefined;

    yield* ttsBinding.synthesizeStream(text, bindingOpts);
  }

  // -- Info -------------------------------------------------------------------

  getAvailableVoices(): VoiceInfo[] {
    return [...this.voices];
  }

  getInfo(): TTSEngineInfo {
    if (!this.activeBackend) {
      throw new Error('TTS engine not loaded. Call loadEngine() first.');
    }
    return {
      backend: this.activeBackend,
      version: ttsBinding.getVersion(),
      voiceCount: this.voices.length,
    };
  }

  // -- Private helpers --------------------------------------------------------

  private async processQueue(): Promise<void> {
    if (this.processing) return;
    this.processing = true;

    while (this.queue.length > 0) {
      const item = this.queue.shift()!;
      try {
        const result = await this.synthesizeInternal(item.text, item.options);
        item.resolve(result);
      } catch (err) {
        item.reject(err instanceof Error ? err : new Error(String(err)));
      }
    }

    this.processing = false;
  }

  private async synthesizeInternal(
    text: string,
    opts?: SynthesisOptions,
  ): Promise<Float32Array> {
    const bindingOpts = opts
      ? { speed: opts.speed, pitch: opts.pitch, voiceId: opts.voiceId }
      : undefined;

    return ttsBinding.synthesize(text, bindingOpts);
  }

  private formatVoiceName(id: string): string {
    return id
      .replace(/[-_]/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  private detectLanguage(id: string): string {
    if (id.startsWith('en')) return 'en';
    if (id.startsWith('de')) return 'de';
    if (id.startsWith('fr')) return 'fr';
    if (id.startsWith('es')) return 'es';
    if (id.startsWith('ja')) return 'ja';
    if (id.startsWith('zh')) return 'zh';
    return 'en';
  }
}

export const ttsEngine = TTSEngine.getInstance();
