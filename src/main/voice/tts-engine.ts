/**
 * tts-engine.ts -- Local text-to-speech engine wrapping kokoro-js / Kokoro / Piper.
 *
 * The Mouth. Takes text strings and returns PCM Float32Array audio buffers
 * at 24kHz mono. Manages engine loading, backend fallback, readiness checks,
 * voice listing, and an internal queue for sequential processing.
 *
 * Backend priority:
 *   1. kokoro-js  (pure Node.js via ONNX — no binary deps, auto-downloads model)
 *   2. kokoro     (binary-based: sherpa-onnx-offline-tts)
 *   3. piper      (binary-based: piper TTS)
 *
 * Sprint 4 K.1: "The Mouth" -- TTSEngine
 */

import { access } from 'node:fs/promises';
import { readdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { ttsBinding } from './tts-binding';
import * as chatterbox from './chatterbox-server';

// -- Constants ----------------------------------------------------------------

/** Output sample rate for TTS audio */
const SAMPLE_RATE = 24_000;

/** Default model directories */
const DEFAULT_TTS_DIR = join(homedir(), '.nexus-os', 'models', 'tts');

/** Model file extension for ONNX models */
const MODEL_FILE_SUFFIX = '.onnx';

/** Backend priority order for auto-detection */
const BACKEND_PRIORITY: TTSBackend[] = ['chatterbox', 'kokoro-js', 'kokoro', 'piper'];

// -- Types --------------------------------------------------------------------

export type TTSBackend = 'chatterbox' | 'kokoro-js' | 'kokoro' | 'piper';

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

/** kokoro-js TTS instance (lazily imported) */
interface KokoroJSInstance {
  generate: (text: string, options?: { voice?: string; speed?: number }) => Promise<{
    audio: Float32Array;
    // kokoro-js may return other fields but we only need audio
  }>;
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

  /** kokoro-js TTS instance (only set when using kokoro-js backend) */
  private kokoroJS: KokoroJSInstance | null = null;

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
      // ── Chatterbox Turbo: highest quality, managed Python sidecar ──
      if (b === 'chatterbox') {
        try {
          if (!(await chatterbox.isSetupComplete())) {
            console.log('[TTSEngine] Chatterbox not set up — skipping');
            continue;
          }

          console.log('[TTSEngine] Starting Chatterbox Turbo server...');
          await chatterbox.start();

          this.activeBackend = 'chatterbox';
          this.engineLoaded = true;

          this.voices = [
            { id: 'chatterbox-default', name: 'Chatterbox Turbo', language: 'en', backend: 'chatterbox', sampleRate: SAMPLE_RATE },
          ];

          console.log('[TTSEngine] Chatterbox Turbo ready (highest quality)');
          return;
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn(`[TTSEngine] Chatterbox unavailable: ${msg}`);
          continue;
        }
      }

      // ── kokoro-js: pure Node.js ONNX TTS — no binary deps ─────────
      if (b === 'kokoro-js') {
        try {
          // eslint-disable-next-line @typescript-eslint/no-var-requires
          const { KokoroTTS } = require('kokoro-js') as { KokoroTTS: { from_pretrained: (model: string, opts?: Record<string, unknown>) => Promise<unknown> } };
          console.log('[TTSEngine] Loading kokoro-js (Node.js ONNX TTS)...');
          const tts = await KokoroTTS.from_pretrained(
            'onnx-community/Kokoro-82M-v1.0-ONNX',
            { dtype: 'q8' },
          );
          this.kokoroJS = tts as unknown as KokoroJSInstance;
          this.activeBackend = 'kokoro-js';
          this.engineLoaded = true;

          // kokoro-js v1.0 ships 54 voices — list a few common defaults
          this.voices = [
            { id: 'af_heart', name: 'Heart (Female)', language: 'en', backend: 'kokoro-js', sampleRate: SAMPLE_RATE },
            { id: 'af_sky', name: 'Sky (Female)', language: 'en', backend: 'kokoro-js', sampleRate: SAMPLE_RATE },
            { id: 'am_adam', name: 'Adam (Male)', language: 'en', backend: 'kokoro-js', sampleRate: SAMPLE_RATE },
            { id: 'am_michael', name: 'Michael (Male)', language: 'en', backend: 'kokoro-js', sampleRate: SAMPLE_RATE },
            { id: 'bf_emma', name: 'Emma (British F)', language: 'en', backend: 'kokoro-js', sampleRate: SAMPLE_RATE },
            { id: 'bm_george', name: 'George (British M)', language: 'en', backend: 'kokoro-js', sampleRate: SAMPLE_RATE },
          ];

          console.log('[TTSEngine] kokoro-js loaded successfully (Kokoro-82M ONNX, Q8)');
          return;
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn(`[TTSEngine] kokoro-js unavailable: ${msg}`);
          // Fall through to next backend
          continue;
        }
      }

      // ── Binary-based backends (kokoro/sherpa-onnx, piper) ─────────
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
      'No TTS backend available. Install kokoro-js (npm i kokoro-js) or place Kokoro/Piper models in ' + this.ttsDir,
    );
  }

  unloadEngine(): void {
    if (this.engineLoaded) {
      if (this.activeBackend === 'chatterbox') {
        chatterbox.stop();
      } else if (this.activeBackend === 'kokoro-js') {
        this.kokoroJS = null;
      } else {
        ttsBinding.freeModel();
      }
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

    // Chatterbox: batch-then-yield (HTTP sidecar doesn't support streaming yet)
    if (this.activeBackend === 'chatterbox') {
      const audio = await this.synthesizeInternal(text, opts);
      if (audio.length > 0) yield audio;
      return;
    }

    // kokoro-js: use native stream() API for sentence-level streaming
    if (this.activeBackend === 'kokoro-js' && this.kokoroJS) {
      const tts = this.kokoroJS as unknown as {
        stream: (text: string, opts?: { voice?: string; speed?: number }) =>
          AsyncGenerator<{ audio: { audio: Float32Array } }, void, void>;
      };
      try {
        for await (const chunk of tts.stream(text, {
          voice: opts?.voiceId || 'af_heart',
          speed: opts?.speed,
        })) {
          if (chunk.audio?.audio?.length > 0) yield chunk.audio.audio;
        }
        return;
      } catch {
        // stream() not available — fall back to batch
        const audio = await this.synthesizeInternal(text, opts);
        if (audio.length > 0) yield audio;
        return;
      }
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
      version: this.activeBackend === 'chatterbox' ? '0.1.6-chatterbox-turbo'
        : this.activeBackend === 'kokoro-js' ? '1.0.0-kokoro-js'
        : ttsBinding.getVersion(),
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
    // ── Chatterbox Turbo — highest quality, HTTP sidecar ────────────
    if (this.activeBackend === 'chatterbox') {
      return chatterbox.synthesize(text, {
        exaggeration: 0.5,
        cfgWeight: 0.5,
      });
    }

    // ── kokoro-js backend — pure Node.js synthesis ──────────────────
    if (this.activeBackend === 'kokoro-js' && this.kokoroJS) {
      const result = await this.kokoroJS.generate(text, {
        voice: opts?.voiceId || 'af_heart',
        speed: opts?.speed,
      });
      return result.audio;
    }

    // ── Binary-based backends ─────────────────────────────────────
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
