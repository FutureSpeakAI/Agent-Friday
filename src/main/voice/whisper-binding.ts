/**
 * whisper-binding.ts -- Subprocess-based Whisper STT binding.
 *
 * Replaces the placeholder with real speech-to-text via whisper.cpp binary.
 * Writes captured audio to a temp WAV file, spawns the whisper binary,
 * parses JSON output for text + timed segments.
 *
 * Binary discovery order:
 *   1. ~/.nexus-os/bin/       (user-installed)
 *   2. App resources/bin/     (bundled with installer)
 *   3. System PATH            (global install)
 *
 * Input:  Float32Array PCM at 16 kHz mono (from AudioCapture / VAD).
 * Output: WhisperRawResult with text, language, timed segments.
 *
 * Sprint 4 J.1 + ForgeMap Track A Phase 2: "The Ear" — real binding.
 */

import { spawn } from 'node:child_process';
import { readFile, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  buildWavBuffer,
  findBinary,
  fileExists,
} from './tts-binding';

// -- Constants ---------------------------------------------------------------

const WHISPER_BINARY = 'whisper-cpp';
const WHISPER_BINARY_ALT = 'main'; // whisper.cpp default binary name
const TRANSCRIPTION_TIMEOUT_MS = 60_000; // 60s for long audio
const EXPECTED_SAMPLE_RATE = 16_000;

// -- Types -------------------------------------------------------------------

export interface WhisperModelHandle {
  handle: string;
  /** Absolute path to the .bin model file */
  modelPath: string;
  /** Path to the resolved whisper binary */
  binaryPath: string;
}

export interface WhisperTranscribeOptions {
  sampleRate: number;
  language?: string;
}

export interface WhisperRawResult {
  text: string;
  language: string;
  segments: Array<{
    text: string;
    start: number;
    end: number;
  }>;
}

export interface WhisperBinding {
  loadModel(path: string): Promise<WhisperModelHandle>;
  transcribe(
    audio: Float32Array,
    options: WhisperTranscribeOptions,
  ): Promise<WhisperRawResult>;
  freeModel(handle: WhisperModelHandle): void;
}

// -- Binary Discovery --------------------------------------------------------

/**
 * Find the whisper.cpp binary. Tries multiple names since builds
 * vary: `whisper-cpp`, `main`, `whisper`.
 */
async function findWhisperBinary(): Promise<string | null> {
  for (const name of [WHISPER_BINARY, WHISPER_BINARY_ALT, 'whisper']) {
    const found = await findBinary(name);
    if (found) return found;
  }
  return null;
}

// -- Subprocess Runner -------------------------------------------------------

function spawnWhisper(
  binaryPath: string,
  args: string[],
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(binaryPath, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let stdout = '';
    let stderr = '';

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Whisper transcription timed out after ${TRANSCRIPTION_TIMEOUT_MS}ms`));
    }, TRANSCRIPTION_TIMEOUT_MS);

    child.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on('data', (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    child.on('error', (err) => {
      clearTimeout(timer);
      reject(new Error(`Whisper binary spawn failed: ${err.message}`));
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`Whisper exited with code ${code}: ${stderr.slice(0, 500)}`));
      } else {
        resolve({ stdout, stderr });
      }
    });
  });
}

// -- Output Parsers ----------------------------------------------------------

/**
 * Parse whisper.cpp JSON output format (-oj flag).
 * Returns structured result with text and timed segments.
 */
function parseWhisperJson(jsonStr: string): WhisperRawResult {
  try {
    const data = JSON.parse(jsonStr);
    const transcription = data.transcription ?? data.result ?? [];

    const segments = (Array.isArray(transcription) ? transcription : []).map(
      (seg: { text?: string; offsets?: { from: number; to: number }; timestamps?: { from: string; to: string } }) => ({
        text: (seg.text ?? '').trim(),
        start: seg.offsets?.from != null
          ? seg.offsets.from / 1000 // ms → sec
          : parseTimestamp(seg.timestamps?.from ?? '0'),
        end: seg.offsets?.to != null
          ? seg.offsets.to / 1000
          : parseTimestamp(seg.timestamps?.to ?? '0'),
      }),
    );

    const fullText = segments.map((s: { text: string }) => s.text).join(' ').trim();
    const language = data.result?.language ?? data.language ?? 'en';

    return { text: fullText, language, segments };
  } catch {
    // Fallback: treat entire string as plain text output
    return parseWhisperPlainText(jsonStr);
  }
}

/**
 * Parse whisper.cpp plain text output (fallback when JSON parse fails).
 * whisper.cpp default output has lines like:
 *   [00:00:00.000 --> 00:00:02.500]   Hello world
 */
function parseWhisperPlainText(text: string): WhisperRawResult {
  const segmentRegex = /\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s*(.*)/g;
  const segments: WhisperRawResult['segments'] = [];
  let match;

  while ((match = segmentRegex.exec(text)) !== null) {
    segments.push({
      text: match[3].trim(),
      start: parseTimestamp(match[1]),
      end: parseTimestamp(match[2]),
    });
  }

  if (segments.length === 0) {
    // No timestamp lines found — treat entire output as one segment
    const cleaned = text.replace(/\[.*?\]/g, '').trim();
    if (cleaned.length > 0) {
      segments.push({ text: cleaned, start: 0, end: 0 });
    }
  }

  const fullText = segments.map((s) => s.text).join(' ').trim();
  return { text: fullText, language: 'en', segments };
}

/**
 * Parse a timestamp string like "00:01:23.456" into seconds.
 */
function parseTimestamp(ts: string): number {
  if (!ts) return 0;
  const parts = ts.split(':');
  if (parts.length === 3) {
    return (
      parseFloat(parts[0]) * 3600 +
      parseFloat(parts[1]) * 60 +
      parseFloat(parts[2])
    );
  }
  return parseFloat(ts) || 0;
}

// -- Binding Implementation --------------------------------------------------

export const whisperBinding: WhisperBinding = {
  async loadModel(path: string): Promise<WhisperModelHandle> {
    // Validate model file exists
    if (!(await fileExists(path))) {
      throw new Error(`Whisper model file not found: ${path}`);
    }

    // Find whisper binary
    const binaryPath = await findWhisperBinary();
    if (!binaryPath) {
      throw new Error(
        `Whisper binary not found. Install whisper-cpp in ~/.nexus-os/bin/, ` +
        `bundle it in app resources, or add it to PATH.`,
      );
    }

    const handle: WhisperModelHandle = {
      handle: `whisper-${Date.now()}`,
      modelPath: path,
      binaryPath,
    };

    console.log(
      `[Whisper Binding] Loaded model="${path}", binary="${binaryPath}"`,
    );

    return handle;
  },

  async transcribe(
    audio: Float32Array,
    options: WhisperTranscribeOptions,
  ): Promise<WhisperRawResult> {
    // We need an active model handle — the WhisperProvider stores it,
    // but the binding receives it implicitly via the handle stored in
    // the provider. For the subprocess approach, we need a way to get
    // the model path. We use a module-level reference set by loadModel.
    if (!currentHandle) {
      throw new Error('No Whisper model loaded. Call loadModel() first.');
    }

    if (audio.length === 0) {
      return { text: '', language: options.language ?? 'en', segments: [] };
    }

    // Write audio to a temporary WAV file (whisper.cpp reads from file)
    const tmpFile = join(
      tmpdir(),
      `nexus-stt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.wav`,
    );

    try {
      const sampleRate = options.sampleRate || EXPECTED_SAMPLE_RATE;
      const wavBuf = buildWavBuffer(audio, sampleRate);
      await writeFile(tmpFile, wavBuf);

      // Build whisper.cpp arguments
      const args = [
        '-m', currentHandle.modelPath,
        '-f', tmpFile,
        '-oj',               // Output JSON format
        '--no-timestamps',    // We get timestamps from JSON
        '-t', '4',            // 4 threads (reasonable default)
      ];

      if (options.language) {
        args.push('-l', options.language);
      }

      const { stdout, stderr } = await spawnWhisper(currentHandle.binaryPath, args);

      // whisper.cpp with -oj writes JSON to a file: {tmpFile}.json
      // But it also prints to stdout. Try the file first, then stdout.
      const jsonFile = tmpFile + '.json';
      let jsonContent: string;

      if (await fileExists(jsonFile)) {
        jsonContent = await readFile(jsonFile, 'utf-8');
        await unlink(jsonFile).catch(() => {});
      } else {
        // Fall back to stdout (some builds output JSON to stdout)
        jsonContent = stdout;
      }

      if (!jsonContent.trim()) {
        // Empty output — check stderr for hints
        if (stderr.includes('no speech')) {
          return { text: '', language: options.language ?? 'en', segments: [] };
        }
        // Try plain text fallback
        return parseWhisperPlainText(stderr + '\n' + stdout);
      }

      const result = parseWhisperJson(jsonContent);

      // Override language if user specified one
      if (options.language) {
        result.language = options.language;
      }

      return result;
    } finally {
      await unlink(tmpFile).catch(() => {});
    }
  },

  freeModel(handle: WhisperModelHandle): void {
    if (currentHandle && currentHandle.handle === handle.handle) {
      console.log(`[Whisper Binding] Unloaded model: ${handle.modelPath}`);
      currentHandle = null;
    }
  },
};

/**
 * Module-level reference to the current model handle.
 * Set by loadModel(), used by transcribe(), cleared by freeModel().
 *
 * This is necessary because the WhisperBinding.transcribe() interface
 * doesn't receive the handle (the WhisperProvider manages it separately),
 * but our subprocess approach needs the model path for every invocation.
 */
let currentHandle: WhisperModelHandle | null = null;

// Wrap loadModel to also store the handle
const originalLoadModel = whisperBinding.loadModel;
whisperBinding.loadModel = async function (path: string): Promise<WhisperModelHandle> {
  const handle = await originalLoadModel(path);
  currentHandle = handle;
  return handle;
};

// Wrap freeModel to clear the handle
const originalFreeModel = whisperBinding.freeModel;
whisperBinding.freeModel = function (handle: WhisperModelHandle): void {
  originalFreeModel(handle);
  currentHandle = null;
};

export default whisperBinding;
