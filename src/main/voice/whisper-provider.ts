/**
 * whisper-provider.ts -- Local speech-to-text engine wrapping whisper.cpp.
 *
 * The Ear. Takes raw 16kHz mono PCM Float32Array audio buffers and returns
 * transcribed text with timed segments. Manages model loading, readiness
 * checks, and an internal queue for sequential processing.
 *
 * Does NOT handle microphone access (J.2), streaming (J.3), or model
 * downloads (S6). CPU-only -- no VRAM concerns.
 *
 * Sprint 4 J.1: "The Ear" -- WhisperProvider
 */

import { access, mkdir, writeFile } from 'node:fs/promises';
import { readdir, stat } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { whisperBinding } from './whisper-binding';
import type { WhisperModelHandle } from './whisper-binding';

// -- Constants ----------------------------------------------------------------

/** Expected sample rate for whisper.cpp input */
const SAMPLE_RATE = 16_000;

/** Default model directory */
const DEFAULT_MODEL_DIR = join(homedir(), '.nexus-os', 'models', 'whisper');

/** Model filename pattern: ggml-{size}.bin */
const MODEL_FILE_PREFIX = 'ggml-';
const MODEL_FILE_SUFFIX = '.bin';

/** Valid model sizes */
const VALID_SIZES = ['tiny', 'base', 'small', 'medium', 'large-v3'] as const;

// -- Types --------------------------------------------------------------------

export type WhisperModelSize = 'tiny' | 'base' | 'small' | 'medium' | 'large-v3';

export interface TranscriptionResult {
  text: string;
  language: string;
  segments: TranscriptionSegment[];
  duration: number;
  processingTime: number;
}

export interface TranscriptionSegment {
  text: string;
  start: number;
  end: number;
}

export interface WhisperModelInfo {
  size: WhisperModelSize;
  path: string;
  fileSizeMB: number;
  downloaded: boolean;
}

interface QueueItem {
  audio: Float32Array;
  resolve: (result: TranscriptionResult) => void;
  reject: (error: Error) => void;
}

// -- WhisperProvider ---------------------------------------------------------

export class WhisperProvider {
  private static instance: WhisperProvider | null = null;

  private modelHandle: WhisperModelHandle | null = null;
  private modelLoaded = false;
  private modelDir: string;
  private queue: QueueItem[] = [];
  private processing = false;

  private constructor(modelDir?: string) {
    this.modelDir = modelDir ?? DEFAULT_MODEL_DIR;
  }

  static getInstance(modelDir?: string): WhisperProvider {
    if (!WhisperProvider.instance) {
      WhisperProvider.instance = new WhisperProvider(modelDir);
    }
    return WhisperProvider.instance;
  }

  static resetInstance(): void {
    if (WhisperProvider.instance) {
      WhisperProvider.instance.unloadModel();
    }
    WhisperProvider.instance = null;
  }

  async loadModel(size: WhisperModelSize = 'tiny'): Promise<void> {
    const modelPath = this.getModelPath(size);

    try {
      await access(modelPath);
    } catch {
      throw new Error(
        `Model file not found: ${modelPath}. Download the ggml-${size}.bin model first.`,
      );
    }

    if (this.modelHandle) {
      this.unloadModel();
    }

    this.modelHandle = await whisperBinding.loadModel(modelPath);
    this.modelLoaded = true;
  }

  unloadModel(): void {
    if (this.modelHandle) {
      whisperBinding.freeModel(this.modelHandle);
      this.modelHandle = null;
    }
    this.modelLoaded = false;
  }

  isReady(): boolean {
    return this.modelLoaded && this.modelHandle !== null;
  }

  async transcribe(audio: Float32Array): Promise<TranscriptionResult> {
    if (!this.isReady()) {
      throw new Error('Whisper model not loaded. Call loadModel() first.');
    }

    return new Promise<TranscriptionResult>((resolve, reject) => {
      this.queue.push({ audio, resolve, reject });
      void this.processQueue();
    });
  }

  private async processQueue(): Promise<void> {
    if (this.processing) return;
    this.processing = true;

    while (this.queue.length > 0) {
      const item = this.queue.shift()!;
      try {
        const result = await this.transcribeInternal(item.audio);
        item.resolve(result);
      } catch (err) {
        item.reject(err instanceof Error ? err : new Error(String(err)));
      }
    }

    this.processing = false;
  }

  private async transcribeInternal(audio: Float32Array): Promise<TranscriptionResult> {
    const duration = audio.length / SAMPLE_RATE;
    const startTime = Date.now();

    const raw = await whisperBinding.transcribe(audio, {
      sampleRate: SAMPLE_RATE,
    });

    const processingTime = Date.now() - startTime;

    return {
      text: raw.text,
      language: raw.language,
      segments: raw.segments.map((s) => ({
        text: s.text,
        start: s.start,
        end: s.end,
      })),
      duration,
      processingTime,
    };
  }

  async getAvailableModels(): Promise<WhisperModelInfo[]> {
    try {
      const files = await readdir(this.modelDir);
      const models: WhisperModelInfo[] = [];

      for (const file of files) {
        if (!file.startsWith(MODEL_FILE_PREFIX) || !file.endsWith(MODEL_FILE_SUFFIX)) {
          continue;
        }

        const sizeName = file
          .slice(MODEL_FILE_PREFIX.length)
          .slice(0, -MODEL_FILE_SUFFIX.length);

        if (!(VALID_SIZES as readonly string[]).includes(sizeName)) {
          continue;
        }

        const filePath = join(this.modelDir, file);
        const fileStat = await stat(filePath);

        models.push({
          size: sizeName as WhisperModelSize,
          path: filePath,
          fileSizeMB: Math.round(fileStat.size / (1024 * 1024)),
          downloaded: true,
        });
      }

      return models;
    } catch {
      return [];
    }
  }

  /**
   * Download a Whisper model from Hugging Face.
   * Creates the model directory if needed and streams the file to disk.
   * Returns the local file path on success.
   */
  async downloadModel(
    size: WhisperModelSize = 'tiny',
    onProgress?: (downloaded: number, total: number) => void,
  ): Promise<string> {
    const modelPath = this.getModelPath(size);
    const url = `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${MODEL_FILE_PREFIX}${size}${MODEL_FILE_SUFFIX}`;

    // Ensure model directory exists
    await mkdir(this.modelDir, { recursive: true });

    // Check if already downloaded
    try {
      await access(modelPath);
      console.log(`[WhisperProvider] Model already exists: ${modelPath}`);
      return modelPath;
    } catch {
      // Not downloaded yet — proceed
    }

    console.log(`[WhisperProvider] Downloading ${size} model from ${url}...`);

    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Download failed: HTTP ${response.status} ${response.statusText}`);
    }
    if (!response.body) {
      throw new Error('Download failed: no response body');
    }

    const total = Number(response.headers.get('content-length') || 0);
    let downloaded = 0;

    const fileStream = createWriteStream(modelPath);
    const reader = response.body.getReader();

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        fileStream.write(Buffer.from(value));
        downloaded += value.byteLength;
        onProgress?.(downloaded, total);
      }
      fileStream.end();
      // Wait for file stream to finish writing
      await new Promise<void>((resolve, reject) => {
        fileStream.on('finish', resolve);
        fileStream.on('error', reject);
      });
    } catch (err) {
      fileStream.end();
      // Clean up partial download
      try { const { unlink } = await import('node:fs/promises'); await unlink(modelPath); } catch {}
      throw err;
    }

    console.log(`[WhisperProvider] Download complete: ${modelPath} (${Math.round(downloaded / 1024 / 1024)}MB)`);
    return modelPath;
  }

  /**
   * Check if a model file exists on disk (without loading it).
   */
  async isModelDownloaded(size: WhisperModelSize = 'tiny'): Promise<boolean> {
    try {
      await access(this.getModelPath(size));
      return true;
    } catch {
      return false;
    }
  }

  private getModelPath(size: WhisperModelSize): string {
    return join(this.modelDir, `${MODEL_FILE_PREFIX}${size}${MODEL_FILE_SUFFIX}`);
  }
}

export const whisperProvider = WhisperProvider.getInstance();
