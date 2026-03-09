/**
 * tts-binding.ts -- Subprocess-based TTS binding for Kokoro & Piper.
 *
 * Replaces the placeholder with real speech synthesis via external binaries:
 *   - Kokoro: `sherpa-onnx-offline-tts` (ONNX-based, multilingual)
 *   - Piper:  `piper` (ONNX-based, lightweight, many voices)
 *
 * Binary discovery order:
 *   1. ~/.nexus-os/bin/       (user-installed)
 *   2. App resources/bin/     (bundled with installer)
 *   3. System PATH            (global install)
 *
 * Output: Float32Array PCM at 24 kHz mono — matches TTSEngine contract.
 *
 * Sprint 4 K.1 + ForgeMap Track A Phase 1: "The Mouth" — real binding.
 */

import { spawn, execFile } from 'node:child_process';
import { readFile, unlink, access, writeFile } from 'node:fs/promises';
import { tmpdir, homedir, platform } from 'node:os';
import { join, dirname } from 'node:path';

// -- Constants ---------------------------------------------------------------

const BIN_DIR = join(homedir(), '.nexus-os', 'bin');
const TARGET_SAMPLE_RATE = 24_000;
const SYNTHESIS_TIMEOUT_MS = 30_000;
const IS_WIN = platform() === 'win32';

/**
 * Binary names per backend, per platform.
 * Kokoro uses sherpa-onnx-offline-tts; Piper uses its own binary.
 */
const BACKEND_BINARIES: Record<string, string> = {
  kokoro: 'sherpa-onnx-offline-tts',
  piper: 'piper',
};

// -- Types -------------------------------------------------------------------

export interface TTSBinding {
  loadModel(path: string, config?: Record<string, unknown>): Promise<void>;
  synthesize(
    text: string,
    options?: { speed?: number; pitch?: number; voiceId?: string },
  ): Promise<Float32Array>;
  synthesizeStream(
    text: string,
    options?: { speed?: number; pitch?: number; voiceId?: string },
  ): AsyncGenerator<Float32Array>;
  freeModel(): void;
  getVersion(): string;
}

interface ActiveModel {
  modelPath: string;
  backend: string;
  binaryPath: string;
  /** Piper .onnx.json companion config (contains sample_rate, etc.) */
  configPath?: string;
  /** Native sample rate from model config — used for resampling */
  nativeSampleRate: number;
}

// -- Module State ------------------------------------------------------------

let activeModel: ActiveModel | null = null;

// -- Binary Discovery --------------------------------------------------------

/**
 * Search for a TTS binary in user dir → app resources → PATH.
 */
async function findBinary(baseName: string): Promise<string | null> {
  const exeName = IS_WIN ? `${baseName}.exe` : baseName;

  // 1. User bin directory
  const userPath = join(BIN_DIR, exeName);
  if (await fileExists(userPath)) return userPath;

  // 2. Bundled resources (Electron packaged app)
  try {
    const resourceBase = typeof process !== 'undefined' && process.resourcesPath
      ? process.resourcesPath
      : join(__dirname, '..', '..', '..');
    const resourcePath = join(resourceBase, 'bin', exeName);
    if (await fileExists(resourcePath)) return resourcePath;
  } catch {
    // Not in Electron context — skip
  }

  // 3. System PATH via `where` (Windows) or `which` (Unix)
  return new Promise((resolve) => {
    const whichCmd = IS_WIN ? 'where' : 'which';
    execFile(whichCmd, [exeName], { windowsHide: true }, (err, stdout) => {
      if (err || !stdout?.trim()) return resolve(null);
      resolve(stdout.trim().split(/\r?\n/)[0].trim());
    });
  });
}

async function fileExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

// -- PCM Conversion ----------------------------------------------------------

/** Convert a buffer of signed 16-bit LE PCM samples to Float32Array [-1, 1]. */
function int16ToFloat32(buf: Buffer): Float32Array {
  const sampleCount = Math.floor(buf.length / 2);
  const out = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) {
    out[i] = buf.readInt16LE(i * 2) / 32768;
  }
  return out;
}

/**
 * Simple linear-interpolation resampler.
 * Good enough for TTS output (not music-critical).
 */
function resample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const outputLen = Math.ceil(input.length / ratio);
  const output = new Float32Array(outputLen);
  for (let i = 0; i < outputLen; i++) {
    const srcIdx = i * ratio;
    const lo = Math.floor(srcIdx);
    const frac = srcIdx - lo;
    const hi = Math.min(lo + 1, input.length - 1);
    output[i] = input[lo] * (1 - frac) + input[hi] * frac;
  }
  return output;
}

// -- WAV Parser --------------------------------------------------------------

interface WavData {
  pcm: Float32Array;
  sampleRate: number;
}

/**
 * Parse a WAV file buffer, extracting PCM data and sample rate.
 * Supports 16-bit int and 32-bit float formats.
 */
function parseWav(buf: Buffer): WavData {
  if (buf.length < 44) throw new Error('WAV file too small');
  if (buf.toString('ascii', 0, 4) !== 'RIFF') throw new Error('Not a RIFF file');
  if (buf.toString('ascii', 8, 12) !== 'WAVE') throw new Error('Not a WAVE file');

  let offset = 12;
  let sampleRate = TARGET_SAMPLE_RATE;
  let bitsPerSample = 16;
  let audioFormat = 1; // 1 = PCM, 3 = IEEE Float
  let dataStart = 0;
  let dataSize = 0;

  while (offset + 8 <= buf.length) {
    const chunkId = buf.toString('ascii', offset, offset + 4);
    const chunkSize = buf.readUInt32LE(offset + 4);

    if (chunkId === 'fmt ') {
      audioFormat = buf.readUInt16LE(offset + 8);
      sampleRate = buf.readUInt32LE(offset + 12);
      bitsPerSample = buf.readUInt16LE(offset + 22);
    } else if (chunkId === 'data') {
      dataStart = offset + 8;
      dataSize = chunkSize;
      break;
    }

    offset += 8 + chunkSize;
    // WAV chunks are word-aligned
    if (chunkSize % 2 !== 0) offset++;
  }

  if (dataStart === 0) throw new Error('No data chunk found in WAV');

  const dataEnd = Math.min(dataStart + dataSize, buf.length);
  const dataBuf = buf.subarray(dataStart, dataEnd);

  if (bitsPerSample === 16 && audioFormat === 1) {
    return { pcm: int16ToFloat32(dataBuf), sampleRate };
  }
  if (bitsPerSample === 32 && audioFormat === 3) {
    // IEEE 754 float PCM
    const floats = new Float32Array(
      dataBuf.buffer,
      dataBuf.byteOffset,
      Math.floor(dataBuf.length / 4),
    );
    return { pcm: new Float32Array(floats), sampleRate };
  }

  throw new Error(`Unsupported WAV format: ${bitsPerSample}-bit, audioFormat=${audioFormat}`);
}

// -- WAV Writer (for temp files) ---------------------------------------------

/**
 * Build a minimal 16-bit mono WAV header + data buffer.
 * Used internally when we need to pass audio TO a subprocess.
 */
function buildWavBuffer(pcm: Float32Array, sampleRate: number): Buffer {
  const numSamples = pcm.length;
  const bytesPerSample = 2; // 16-bit
  const dataSize = numSamples * bytesPerSample;
  const headerSize = 44;
  const buf = Buffer.alloc(headerSize + dataSize);

  // RIFF header
  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write('WAVE', 8);

  // fmt chunk
  buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16);         // chunk size
  buf.writeUInt16LE(1, 20);          // PCM format
  buf.writeUInt16LE(1, 22);          // mono
  buf.writeUInt32LE(sampleRate, 24); // sample rate
  buf.writeUInt32LE(sampleRate * bytesPerSample, 28); // byte rate
  buf.writeUInt16LE(bytesPerSample, 32);  // block align
  buf.writeUInt16LE(16, 34);         // bits per sample

  // data chunk
  buf.write('data', 36);
  buf.writeUInt32LE(dataSize, 40);

  // Write PCM samples
  for (let i = 0; i < numSamples; i++) {
    const clamped = Math.max(-1, Math.min(1, pcm[i]));
    buf.writeInt16LE(Math.round(clamped * 32767), headerSize + i * 2);
  }

  return buf;
}

// -- Piper Backend -----------------------------------------------------------

/**
 * Parse Piper's companion .onnx.json to extract sample_rate.
 * Returns 22050 (Piper's most common rate) as default.
 */
async function getPiperSampleRate(configPath?: string): Promise<number> {
  if (!configPath) return 22050;
  try {
    const raw = await readFile(configPath, 'utf-8');
    const cfg = JSON.parse(raw);
    return cfg?.audio?.sample_rate ?? 22050;
  } catch {
    return 22050;
  }
}

async function synthesizePiper(
  text: string,
  model: ActiveModel,
  opts?: { speed?: number },
): Promise<Float32Array> {
  const tmpFile = join(tmpdir(), `nexus-tts-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.wav`);

  try {
    const args = ['--model', model.modelPath, '--output_file', tmpFile];

    if (model.configPath) {
      args.push('--config', model.configPath);
    }

    if (opts?.speed && opts.speed !== 1.0) {
      // Piper uses length_scale: <1 = faster, >1 = slower (inverse of speed)
      args.push('--length_scale', String(1.0 / opts.speed));
    }

    await spawnAndWait(model.binaryPath, args, text);

    const wavBuf = await readFile(tmpFile);
    const { pcm, sampleRate } = parseWav(wavBuf);
    return resample(pcm, sampleRate, TARGET_SAMPLE_RATE);
  } finally {
    await unlink(tmpFile).catch(() => {});
  }
}

// -- Kokoro Backend (via sherpa-onnx-offline-tts) ----------------------------

async function synthesizeKokoro(
  text: string,
  model: ActiveModel,
  opts?: { speed?: number; voiceId?: string },
): Promise<Float32Array> {
  const tmpFile = join(tmpdir(), `nexus-tts-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.wav`);

  try {
    const modelDir = dirname(model.modelPath);

    const args = [
      `--vits-model=${model.modelPath}`,
      `--vits-tokens=${join(modelDir, 'tokens.txt')}`,
      `--output-filename=${tmpFile}`,
    ];

    // Optional: lexicon file
    const lexiconPath = join(modelDir, 'lexicon.txt');
    if (await fileExists(lexiconPath)) {
      args.push(`--vits-lexicon=${lexiconPath}`);
    }

    // Optional: espeak-ng data directory
    const dataDir = join(modelDir, 'espeak-ng-data');
    if (await fileExists(dataDir)) {
      args.push(`--vits-data-dir=${dataDir}`);
    }

    if (opts?.speed && opts.speed !== 1.0) {
      args.push(`--vits-length-scale=${(1.0 / opts.speed).toFixed(3)}`);
    }

    if (opts?.voiceId) {
      args.push(`--sid=${opts.voiceId}`);
    }

    // sherpa-onnx takes text as the final positional argument
    args.push(text);

    await spawnAndWait(model.binaryPath, args);

    const wavBuf = await readFile(tmpFile);
    const { pcm, sampleRate } = parseWav(wavBuf);
    return resample(pcm, sampleRate, TARGET_SAMPLE_RATE);
  } finally {
    await unlink(tmpFile).catch(() => {});
  }
}

// -- Shared Subprocess Runner ------------------------------------------------

/**
 * Spawn a TTS binary, optionally pipe text on stdin, wait for exit.
 * Rejects on non-zero exit, timeout, or spawn error.
 */
function spawnAndWait(
  bin: string,
  args: string[],
  stdinText?: string,
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const child = spawn(bin, args, {
      stdio: [stdinText ? 'pipe' : 'ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let stdout = '';
    let stderr = '';

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`TTS synthesis timed out after ${SYNTHESIS_TIMEOUT_MS}ms`));
    }, SYNTHESIS_TIMEOUT_MS);

    child.stdout?.on('data', (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr?.on('data', (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    child.on('error', (err) => {
      clearTimeout(timer);
      reject(new Error(`TTS binary spawn failed: ${err.message}`));
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`TTS process exited with code ${code}: ${stderr.slice(0, 500)}`));
      } else {
        resolve(stdout);
      }
    });

    if (stdinText && child.stdin) {
      child.stdin.write(stdinText);
      child.stdin.end();
    }
  });
}

// -- Streaming Piper Output --------------------------------------------------

async function* streamPiper(
  text: string,
  model: ActiveModel,
  opts?: { speed?: number },
): AsyncGenerator<Float32Array> {
  const args = ['--model', model.modelPath, '--output_raw'];

  if (model.configPath) {
    args.push('--config', model.configPath);
  }

  if (opts?.speed && opts.speed !== 1.0) {
    args.push('--length_scale', String(1.0 / opts.speed));
  }

  const child = spawn(model.binaryPath, args, {
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  });

  let spawnError: Error | null = null;
  child.on('error', (err) => {
    spawnError = err;
  });
  child.stderr?.on('data', () => {
    /* drain stderr to prevent backpressure */
  });

  // Pipe text into stdin
  child.stdin!.write(text);
  child.stdin!.end();

  // Read raw Int16 PCM from stdout in ~200ms chunks
  const CHUNK_SAMPLES = Math.floor(model.nativeSampleRate * 0.2); // 200ms
  const CHUNK_BYTES = CHUNK_SAMPLES * 2; // Int16 = 2 bytes
  let remainder = Buffer.alloc(0);

  for await (const chunk of child.stdout!) {
    if (spawnError) throw spawnError;

    remainder = Buffer.concat([remainder, chunk as Buffer]);

    while (remainder.length >= CHUNK_BYTES) {
      const slice = remainder.subarray(0, CHUNK_BYTES);
      remainder = remainder.subarray(CHUNK_BYTES);
      const pcm = int16ToFloat32(slice);
      yield resample(pcm, model.nativeSampleRate, TARGET_SAMPLE_RATE);
    }
  }

  // Flush remaining samples
  if (remainder.length >= 2) {
    const pcm = int16ToFloat32(remainder);
    yield resample(pcm, model.nativeSampleRate, TARGET_SAMPLE_RATE);
  }

  if (spawnError) throw spawnError;
}

// -- Binding Implementation --------------------------------------------------

export const ttsBinding: TTSBinding = {
  async loadModel(path: string, config?: Record<string, unknown>): Promise<void> {
    const backend = (config?.backend as string) ?? 'piper';

    const baseName = BACKEND_BINARIES[backend];
    if (!baseName) {
      throw new Error(
        `Unknown TTS backend: "${backend}". Supported: ${Object.keys(BACKEND_BINARIES).join(', ')}`,
      );
    }

    const binaryPath = await findBinary(baseName);
    if (!binaryPath) {
      throw new Error(
        `TTS binary "${baseName}" not found. ` +
        `Install it in ${BIN_DIR}, bundle it in app resources, or add it to PATH.`,
      );
    }

    // Validate model file
    if (!(await fileExists(path))) {
      throw new Error(`TTS model file not found: ${path}`);
    }

    // Companion config for Piper (e.g., en_US-lessac-medium.onnx.json)
    let configPath: string | undefined;
    const jsonCfg = path + '.json';
    if (await fileExists(jsonCfg)) {
      configPath = jsonCfg;
    }

    // Determine native sample rate
    let nativeSampleRate = TARGET_SAMPLE_RATE;
    if (backend === 'piper') {
      nativeSampleRate = await getPiperSampleRate(configPath);
    }

    activeModel = { modelPath: path, backend, binaryPath, configPath, nativeSampleRate };

    console.log(
      `[TTS Binding] Loaded backend="${backend}", binary="${binaryPath}", ` +
      `model="${path}", sampleRate=${nativeSampleRate}`,
    );
  },

  async synthesize(
    text: string,
    options?: { speed?: number; pitch?: number; voiceId?: string },
  ): Promise<Float32Array> {
    if (!activeModel) {
      throw new Error('TTS model not loaded. Call loadModel() first.');
    }

    if (!text.trim()) return new Float32Array(0);

    if (activeModel.backend === 'piper') {
      return synthesizePiper(text, activeModel, options);
    }

    if (activeModel.backend === 'kokoro') {
      return synthesizeKokoro(text, activeModel, options);
    }

    throw new Error(`Unsupported TTS backend: ${activeModel.backend}`);
  },

  async *synthesizeStream(
    text: string,
    options?: { speed?: number; pitch?: number; voiceId?: string },
  ): AsyncGenerator<Float32Array> {
    if (!activeModel) {
      throw new Error('TTS model not loaded. Call loadModel() first.');
    }

    if (!text.trim()) return;

    // Piper supports raw PCM streaming via stdout
    if (activeModel.backend === 'piper') {
      yield* streamPiper(text, activeModel, options);
      return;
    }

    // Kokoro / other backends: fall back to batch-then-yield
    const fullAudio = await ttsBinding.synthesize(text, options);
    if (fullAudio.length > 0) {
      yield fullAudio;
    }
  },

  freeModel(): void {
    if (activeModel) {
      console.log(`[TTS Binding] Unloaded ${activeModel.backend} model`);
    }
    activeModel = null;
  },

  getVersion(): string {
    if (!activeModel) return '0.0.0-unloaded';
    return `1.0.0-${activeModel.backend}`;
  },
};

// -- Utility Exports (used by whisper-binding and future bindings) -----------

export { int16ToFloat32, resample, parseWav, buildWavBuffer, findBinary, fileExists };

export default ttsBinding;
