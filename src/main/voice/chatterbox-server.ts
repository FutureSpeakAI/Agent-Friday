/**
 * chatterbox-server.ts — Managed Python sidecar for Chatterbox Turbo TTS.
 *
 * Chatterbox Turbo (Resemble AI, MIT license) is the highest-quality open-source
 * TTS model available — it beats ElevenLabs in blind tests, supports emotion tags
 * ([laugh], [sigh], [cough]), voice cloning, and runs at sub-200ms latency.
 *
 * This module manages the full lifecycle:
 *   1. Python venv creation + dependency installation (one-time setup)
 *   2. Server process spawning on a random localhost port
 *   3. Health monitoring and auto-restart
 *   4. HTTP-based synthesis (text → WAV → Float32Array)
 *   5. Graceful shutdown
 *
 * The Python server auto-downloads the model weights from HuggingFace on first use
 * (~4 GB, cached in ~/.cache/huggingface/).
 */

import { spawn, execFile, type ChildProcess } from 'node:child_process';
import { mkdir, writeFile, access } from 'node:fs/promises';
import { homedir, platform } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';
import http from 'node:http';
import { EventEmitter } from 'node:events';

// -- Constants ----------------------------------------------------------------

const CHATTERBOX_DIR = join(homedir(), '.nexus-os', 'services', 'chatterbox');
const VENV_DIR = join(CHATTERBOX_DIR, 'venv');
const SERVER_SCRIPT = join(CHATTERBOX_DIR, 'server.py');
const IS_WIN = platform() === 'win32';
const PYTHON_BIN = IS_WIN
  ? join(VENV_DIR, 'Scripts', 'python.exe')
  : join(VENV_DIR, 'bin', 'python');
const PIP_BIN = IS_WIN
  ? join(VENV_DIR, 'Scripts', 'pip.exe')
  : join(VENV_DIR, 'bin', 'pip');

/** Max time to wait for the server to become healthy after spawn */
const STARTUP_TIMEOUT_MS = 120_000; // Model download can take a while on first run
/** Max time for a single synthesis request */
const SYNTHESIS_TIMEOUT_MS = 30_000;
/** Health check interval while server is running */
const HEALTH_CHECK_INTERVAL_MS = 30_000;

// -- Python Server Script -----------------------------------------------------

const SERVER_PY = `#!/usr/bin/env python3
"""Chatterbox Turbo TTS HTTP server for Agent Friday.

Minimal HTTP server that loads ChatterboxTurboTTS and serves synthesis requests.
Runs on localhost only — not exposed to the network.
"""

import sys
import json
import io
import struct
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9877

model = None
model_device = None

def load_model():
    global model, model_device
    if model is not None:
        return

    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_device = device
    print(f"[Chatterbox] Loading Turbo model on {device}...", flush=True)
    model = ChatterboxTurboTTS.from_pretrained(device=device)
    print(f"[Chatterbox] Model loaded. Sample rate: {model.sr}Hz", flush=True)

    # Warm up: generate a tiny silent clip to initialize CUDA kernels
    if device == "cuda":
        print("[Chatterbox] Warming up CUDA kernels...", flush=True)
        _ = model.generate("Hello.")
        print("[Chatterbox] Warm-up complete.", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default access logs

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "model_loaded": model is not None,
                "device": model_device,
                "sample_rate": model.sr if model else None,
            }).encode())
        elif self.path == "/info":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "engine": "chatterbox-turbo",
                "version": "0.1.6",
                "model": "ResembleAI/chatterbox-turbo",
                "sample_rate": 24000,
                "supports_voice_cloning": True,
                "supports_emotion_tags": True,
                "emotion_tags": ["[laugh]", "[chuckle]", "[sigh]", "[cough]", "[gasp]",
                                 "[clears throat]", "[sniff]", "[groan]"],
            }).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/synthesize":
            try:
                import numpy as np

                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                text = body.get("text", "")

                if not text.strip():
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Empty text"}).encode())
                    return

                load_model()

                ref_audio = body.get("reference_audio_path")
                exaggeration = float(body.get("exaggeration", 0.5))
                cfg_weight = float(body.get("cfg_weight", 0.5))

                wav = model.generate(
                    text,
                    audio_prompt_path=ref_audio if ref_audio else None,
                    exaggeration=exaggeration,
                    cfg_weight=cfg_weight,
                )

                # Convert to float32 PCM numpy array
                pcm = wav.squeeze(0).cpu().numpy().astype(np.float32)
                sample_rate = model.sr  # 24000

                # Build WAV file (32-bit float format for lossless transfer)
                wav_buf = io.BytesIO()
                num_samples = len(pcm)
                data_size = num_samples * 4  # float32 = 4 bytes

                wav_buf.write(b"RIFF")
                wav_buf.write(struct.pack("<I", 36 + data_size))
                wav_buf.write(b"WAVE")
                wav_buf.write(b"fmt ")
                wav_buf.write(struct.pack("<I", 16))          # chunk size
                wav_buf.write(struct.pack("<H", 3))           # IEEE float format
                wav_buf.write(struct.pack("<H", 1))           # mono
                wav_buf.write(struct.pack("<I", sample_rate))
                wav_buf.write(struct.pack("<I", sample_rate * 4))  # byte rate
                wav_buf.write(struct.pack("<H", 4))           # block align
                wav_buf.write(struct.pack("<H", 32))          # bits per sample
                wav_buf.write(b"data")
                wav_buf.write(struct.pack("<I", data_size))
                wav_buf.write(pcm.tobytes())

                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("X-Sample-Rate", str(sample_rate))
                self.send_header("X-Duration-Ms", str(int(num_samples / sample_rate * 1000)))
                self.end_headers()
                self.wfile.write(wav_buf.getvalue())

            except Exception as e:
                traceback.print_exc()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_error(404)


if __name__ == "__main__":
    print(f"[Chatterbox] Starting TTS server on port {PORT}...", flush=True)
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[Chatterbox] Server ready at http://127.0.0.1:{PORT}", flush=True)
    # Signal readiness (parent process reads stdout for this line)
    print("CHATTERBOX_READY", flush=True)
    server.serve_forever()
`;

// -- Types --------------------------------------------------------------------

export interface ChatterboxSynthesisOptions {
  /** Path to reference audio for voice cloning */
  referenceAudioPath?: string;
  /** Emotion intensity (0.0 to 1.0+, default 0.5) */
  exaggeration?: number;
  /** Classifier-free guidance weight (0.0 to 1.0, default 0.5) */
  cfgWeight?: number;
}

export type ChatterboxSetupProgress = {
  stage: 'checking-python' | 'creating-venv' | 'installing-pytorch' | 'installing-chatterbox' | 'writing-server' | 'complete' | 'error';
  message: string;
  percent: number;
};

// -- Module state -------------------------------------------------------------

let serverProcess: ChildProcess | null = null;
let serverPort: number | null = null;
let healthTimer: ReturnType<typeof setInterval> | null = null;

export const chatterboxEvents = new EventEmitter();

// -- Helpers ------------------------------------------------------------------

async function fileExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      if (addr && typeof addr !== 'string') {
        const port = addr.port;
        server.close(() => resolve(port));
      } else {
        server.close(() => reject(new Error('Failed to find free port')));
      }
    });
    server.on('error', reject);
  });
}

/**
 * Find a system Python >=3.10.
 * Returns the command string (e.g. "python" or "python3").
 */
async function findPython(): Promise<string | null> {
  const candidates = IS_WIN
    ? ['python', 'python3']
    : ['python3', 'python'];

  for (const cmd of candidates) {
    try {
      const version = await new Promise<string>((resolve, reject) => {
        execFile(cmd, ['--version'], { windowsHide: true }, (err, stdout, stderr) => {
          if (err) reject(err);
          else resolve((stdout || stderr).trim());
        });
      });
      const match = version.match(/Python (\d+)\.(\d+)/);
      if (match) {
        const major = parseInt(match[1]);
        const minor = parseInt(match[2]);
        if (major >= 3 && minor >= 10) {
          console.log(`[Chatterbox] Found Python: ${version} (${cmd})`);
          return cmd;
        }
      }
    } catch {
      continue;
    }
  }
  return null;
}

/** Run a shell command and return stdout. */
function runCommand(cmd: string, args: string[], cwd?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      shell: IS_WIN, // Required for some Python commands on Windows
    });

    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    child.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });

    child.on('error', (err) => reject(new Error(`Spawn failed: ${err.message}`)));
    child.on('close', (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`Exit code ${code}: ${stderr.slice(0, 500)}`));
    });
  });
}

/** Make an HTTP request to the local server. */
function httpRequest(
  method: string,
  path: string,
  body?: Record<string, unknown>,
  timeoutMs = 10_000,
): Promise<{ status: number; body: Buffer }> {
  return new Promise((resolve, reject) => {
    const postData = body ? JSON.stringify(body) : undefined;

    const req = http.request(
      {
        hostname: '127.0.0.1',
        port: serverPort!,
        path,
        method,
        headers: postData
          ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postData) }
          : undefined,
        timeout: timeoutMs,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          resolve({ status: res.statusCode || 0, body: Buffer.concat(chunks) });
        });
      },
    );

    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Request timed out'));
    });

    if (postData) req.write(postData);
    req.end();
  });
}

// -- Public API ---------------------------------------------------------------

/**
 * Check if the Chatterbox server setup exists (venv + deps installed).
 */
export async function isSetupComplete(): Promise<boolean> {
  return (
    (await fileExists(PYTHON_BIN)) &&
    (await fileExists(SERVER_SCRIPT))
  );
}

/**
 * Check if a system Python >=3.10 is available.
 */
export async function isPythonAvailable(): Promise<boolean> {
  return (await findPython()) !== null;
}

/**
 * Check if the system has a CUDA-capable GPU.
 */
export async function hasCudaGpu(): Promise<boolean> {
  const python = await findPython();
  if (!python) return false;
  try {
    const result = await runCommand(python, [
      '-c',
      'import subprocess; r=subprocess.run(["nvidia-smi"],capture_output=True); print("yes" if r.returncode==0 else "no")',
    ]);
    return result.trim() === 'yes';
  } catch {
    return false;
  }
}

/**
 * Full one-time setup: create venv, install PyTorch + chatterbox-tts, write server script.
 * Emits progress events via chatterboxEvents.
 *
 * Total download: ~3GB (PyTorch) + ~4GB (model, downloaded on first synthesis).
 */
export async function setup(
  onProgress?: (p: ChatterboxSetupProgress) => void,
): Promise<void> {
  const emit = (p: ChatterboxSetupProgress) => {
    onProgress?.(p);
    chatterboxEvents.emit('setup-progress', p);
  };

  // Step 1: Find Python
  emit({ stage: 'checking-python', message: 'Checking for Python 3.10+...', percent: 0 });
  const pythonCmd = await findPython();
  if (!pythonCmd) {
    emit({ stage: 'error', message: 'Python 3.10+ not found. Install Python from python.org.', percent: 0 });
    throw new Error('Python 3.10+ not found. Install Python from https://python.org');
  }

  // Step 2: Create venv
  emit({ stage: 'creating-venv', message: 'Creating Python virtual environment...', percent: 10 });
  await mkdir(CHATTERBOX_DIR, { recursive: true });

  if (!(await fileExists(PYTHON_BIN))) {
    await runCommand(pythonCmd, ['-m', 'venv', VENV_DIR]);
  }

  // Step 3: Install PyTorch with CUDA
  emit({ stage: 'installing-pytorch', message: 'Installing PyTorch (this may take a few minutes)...', percent: 20 });
  const hasCuda = await hasCudaGpu();

  if (hasCuda) {
    await runCommand(PIP_BIN, [
      'install', '--upgrade',
      'torch==2.6.0', 'torchaudio==2.6.0',
      '--index-url', 'https://download.pytorch.org/whl/cu124',
    ]);
  } else {
    await runCommand(PIP_BIN, [
      'install', '--upgrade',
      'torch==2.6.0', 'torchaudio==2.6.0',
      '--index-url', 'https://download.pytorch.org/whl/cpu',
    ]);
  }

  // Step 4: Install chatterbox-tts
  emit({ stage: 'installing-chatterbox', message: 'Installing Chatterbox Turbo TTS...', percent: 60 });
  await runCommand(PIP_BIN, ['install', '--upgrade', 'chatterbox-tts']);

  // Step 5: Write server script
  emit({ stage: 'writing-server', message: 'Finalizing setup...', percent: 90 });
  await writeFile(SERVER_SCRIPT, SERVER_PY, 'utf-8');

  emit({ stage: 'complete', message: 'Chatterbox Turbo TTS installed successfully.', percent: 100 });
  console.log('[Chatterbox] Setup complete.');
}

/**
 * Start the Chatterbox TTS server as a child process.
 * Waits for the server to become healthy before resolving.
 * The model is loaded lazily on first synthesis request.
 */
export async function start(): Promise<void> {
  if (serverProcess && serverPort) {
    // Already running — verify health
    try {
      const res = await httpRequest('GET', '/health');
      if (res.status === 200) return;
    } catch {
      // Server died — clean up and restart
      stop();
    }
  }

  if (!(await isSetupComplete())) {
    throw new Error('Chatterbox not set up. Call setup() first.');
  }

  const port = await findFreePort();
  serverPort = port;

  console.log(`[Chatterbox] Starting server on port ${port}...`);

  const proc = spawn(PYTHON_BIN, [SERVER_SCRIPT, String(port)], {
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    detached: false,
  });

  serverProcess = proc;

  // Log server output
  proc.stdout?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.log(`[Chatterbox Server] ${line}`);
  });
  proc.stderr?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.warn(`[Chatterbox Server ERR] ${line}`);
  });

  proc.on('exit', (code) => {
    console.log(`[Chatterbox] Server process exited with code ${code}`);
    serverProcess = null;
    if (healthTimer) {
      clearInterval(healthTimer);
      healthTimer = null;
    }
    chatterboxEvents.emit('server-stopped', code);
  });

  // Wait for "CHATTERBOX_READY" line or health check success
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`Chatterbox server failed to start within ${STARTUP_TIMEOUT_MS}ms`));
    }, STARTUP_TIMEOUT_MS);

    const onData = (data: Buffer) => {
      if (data.toString().includes('CHATTERBOX_READY')) {
        clearTimeout(timer);
        proc.stdout?.removeListener('data', onData);
        resolve();
      }
    };

    proc.stdout?.on('data', onData);

    proc.on('error', (err) => {
      clearTimeout(timer);
      reject(new Error(`Failed to spawn Chatterbox server: ${err.message}`));
    });

    proc.on('exit', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`Chatterbox server exited with code ${code} during startup`));
      }
    });
  });

  // Start health check timer
  healthTimer = setInterval(async () => {
    try {
      const res = await httpRequest('GET', '/health', undefined, 5_000);
      if (res.status !== 200) throw new Error(`Health check returned ${res.status}`);
    } catch (err) {
      console.warn('[Chatterbox] Health check failed:', err instanceof Error ? err.message : String(err));
      chatterboxEvents.emit('health-degraded');
    }
  }, HEALTH_CHECK_INTERVAL_MS);

  console.log(`[Chatterbox] Server started on port ${port}`);
  chatterboxEvents.emit('server-started', port);
}

/**
 * Stop the Chatterbox server.
 */
export function stop(): void {
  if (healthTimer) {
    clearInterval(healthTimer);
    healthTimer = null;
  }
  if (serverProcess) {
    console.log('[Chatterbox] Stopping server...');
    serverProcess.kill('SIGTERM');
    // Force kill after 5s if graceful shutdown fails
    const forceTimer = setTimeout(() => {
      if (serverProcess) {
        serverProcess.kill('SIGKILL');
        serverProcess = null;
      }
    }, 5_000);
    serverProcess.once('exit', () => clearTimeout(forceTimer));
    serverProcess = null;
  }
  serverPort = null;
}

/**
 * Check if the server is currently running and healthy.
 */
export async function isRunning(): Promise<boolean> {
  if (!serverProcess || !serverPort) return false;
  try {
    const res = await httpRequest('GET', '/health', undefined, 3_000);
    return res.status === 200;
  } catch {
    return false;
  }
}

/**
 * Synthesize text to audio via the Chatterbox server.
 * Returns Float32Array PCM at 24kHz mono.
 *
 * The model is loaded lazily on the first call (~30-60s with model download).
 * Subsequent calls complete in <200ms.
 */
export async function synthesize(
  text: string,
  opts?: ChatterboxSynthesisOptions,
): Promise<Float32Array> {
  if (!serverPort) {
    throw new Error('Chatterbox server not started');
  }

  const res = await httpRequest(
    'POST',
    '/synthesize',
    {
      text,
      reference_audio_path: opts?.referenceAudioPath,
      exaggeration: opts?.exaggeration ?? 0.5,
      cfg_weight: opts?.cfgWeight ?? 0.5,
    },
    SYNTHESIS_TIMEOUT_MS,
  );

  if (res.status !== 200) {
    let errorMsg = `Synthesis failed (${res.status})`;
    try {
      const errBody = JSON.parse(res.body.toString());
      errorMsg = errBody.error || errorMsg;
    } catch { /* non-JSON response */ }
    throw new Error(errorMsg);
  }

  // Parse WAV response
  return parseWavToFloat32(res.body);
}

/**
 * Get the server port (for external callers).
 */
export function getPort(): number | null {
  return serverPort;
}

// -- WAV Parser ---------------------------------------------------------------

function parseWavToFloat32(buf: Buffer): Float32Array {
  if (buf.length < 44) throw new Error('WAV too small');
  if (buf.toString('ascii', 0, 4) !== 'RIFF') throw new Error('Not RIFF');
  if (buf.toString('ascii', 8, 12) !== 'WAVE') throw new Error('Not WAVE');

  let offset = 12;
  let bitsPerSample = 16;
  let audioFormat = 1;
  let dataStart = 0;
  let dataSize = 0;

  while (offset + 8 <= buf.length) {
    const chunkId = buf.toString('ascii', offset, offset + 4);
    const chunkSize = buf.readUInt32LE(offset + 4);

    if (chunkId === 'fmt ') {
      audioFormat = buf.readUInt16LE(offset + 8);
      bitsPerSample = buf.readUInt16LE(offset + 22);
    } else if (chunkId === 'data') {
      dataStart = offset + 8;
      dataSize = chunkSize;
      break;
    }

    offset += 8 + chunkSize;
    if (chunkSize % 2 !== 0) offset++;
  }

  if (dataStart === 0) throw new Error('No data chunk in WAV');

  const dataEnd = Math.min(dataStart + dataSize, buf.length);
  const dataBuf = buf.subarray(dataStart, dataEnd);

  // IEEE 32-bit float (audioFormat=3)
  if (bitsPerSample === 32 && audioFormat === 3) {
    const floats = new Float32Array(
      dataBuf.buffer,
      dataBuf.byteOffset,
      Math.floor(dataBuf.length / 4),
    );
    return new Float32Array(floats); // Copy to avoid shared buffer issues
  }

  // 16-bit PCM (audioFormat=1)
  if (bitsPerSample === 16 && audioFormat === 1) {
    const sampleCount = Math.floor(dataBuf.length / 2);
    const out = new Float32Array(sampleCount);
    for (let i = 0; i < sampleCount; i++) {
      out[i] = dataBuf.readInt16LE(i * 2) / 32768;
    }
    return out;
  }

  throw new Error(`Unsupported WAV: ${bitsPerSample}-bit, format=${audioFormat}`);
}
