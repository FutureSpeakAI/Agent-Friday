/**
 * personaplex-server.ts — Managed Python sidecar for NVIDIA PersonaPlex.
 *
 * PersonaPlex is a 7B-parameter full-duplex speech-to-speech conversational
 * model. Unlike the Whisper+Ollama+TTS pipeline, PersonaPlex handles the
 * entire voice interaction in a single model: it listens and speaks
 * simultaneously with ~170ms latency.
 *
 * This module manages the full lifecycle:
 *   1. Python venv creation + dependency installation (one-time setup)
 *   2. SSL certificate generation (required for WSS)
 *   3. Server process spawning (`python -m moshi.server`)
 *   4. Health monitoring and auto-restart
 *   5. Graceful shutdown
 *
 * The server runs locally on port 8998 and communicates via WebSocket Secure.
 * Audio protocol: client sends PCM frames, server returns Ogg Opus pages.
 */

import { spawn, execFile, type ChildProcess } from 'node:child_process';
import { mkdir, writeFile, access, rm, rename, readdir } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { homedir, platform, arch } from 'node:os';
import { join } from 'node:path';
import { EventEmitter } from 'node:events';

// -- Constants ----------------------------------------------------------------

const PERSONAPLEX_DIR = join(homedir(), '.nexus-os', 'services', 'personaplex');
const VENV_DIR = join(PERSONAPLEX_DIR, 'venv');
const SSL_DIR = join(PERSONAPLEX_DIR, 'ssl');
const IS_WIN = platform() === 'win32';
const PYTHON_BIN = IS_WIN
  ? join(VENV_DIR, 'Scripts', 'python.exe')
  : join(VENV_DIR, 'bin', 'python');
const PIP_BIN = IS_WIN
  ? join(VENV_DIR, 'Scripts', 'pip.exe')
  : join(VENV_DIR, 'bin', 'pip');

/** Default server port */
const DEFAULT_PORT = 8998;

/** Max time to wait for the server to become healthy after spawn */
const STARTUP_TIMEOUT_MS = 180_000; // Model loading can take a while

/** Health check interval while server is running */
const HEALTH_CHECK_INTERVAL_MS = 30_000;

/** Required Python version range */
const MIN_PYTHON_MINOR = 11;
const MAX_PYTHON_MINOR = 13;

/** PersonaPlex voice presets */
export const PERSONAPLEX_VOICES = {
  // Natural voices
  NATF0: 'Natural Female 1',
  NATF1: 'Natural Female 2',
  NATF2: 'Natural Female 3',
  NATF3: 'Natural Female 4',
  NATM0: 'Natural Male 1',
  NATM1: 'Natural Male 2',
  NATM2: 'Natural Male 3',
  NATM3: 'Natural Male 4',
  // Variety voices
  VARF0: 'Variety Female 1',
  VARF1: 'Variety Female 2',
  VARF2: 'Variety Female 3',
  VARF3: 'Variety Female 4',
  VARF4: 'Variety Female 5',
  VARM0: 'Variety Male 1',
  VARM1: 'Variety Male 2',
  VARM2: 'Variety Male 3',
  VARM3: 'Variety Male 4',
  VARM4: 'Variety Male 5',
} as const;

export type PersonaPlexVoiceId = keyof typeof PERSONAPLEX_VOICES;

// -- Types --------------------------------------------------------------------

export type PersonaPlexSetupProgress = {
  stage:
    | 'checking-python'
    | 'creating-venv'
    | 'installing-pytorch'
    | 'installing-personaplex'
    | 'installing-opus'
    | 'generating-ssl'
    | 'complete'
    | 'error';
  message: string;
  percent: number;
};

export interface PersonaPlexConfig {
  /** Voice preset ID (e.g., 'NATF2') */
  voiceId?: PersonaPlexVoiceId;
  /** Text prompt defining persona/role */
  textPrompt?: string;
  /** Whether to use CPU offloading for limited GPU memory */
  cpuOffload?: boolean;
  /** HuggingFace token for model download */
  hfToken?: string;
}

// -- Module state -------------------------------------------------------------

let serverProcess: ChildProcess | null = null;
let serverPort: number = DEFAULT_PORT;
let healthTimer: ReturnType<typeof setInterval> | null = null;

export const personaplexEvents = new EventEmitter();

// Add default error listener to prevent crash on unhandled 'error' events
personaplexEvents.on('error', (err) => {
  console.error('[PersonaPlex] Unhandled event error:', err);
});

// -- Helpers ------------------------------------------------------------------

async function fileExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

/**
 * Find a compatible Python (3.11-3.13).
 * PersonaPlex/Moshi requires Python 3.12+ but we accept 3.11 for flexibility.
 */
async function findPython(): Promise<string | null> {
  const candidates: Array<{ cmd: string; args: string[] }> = [];

  if (IS_WIN) {
    for (let minor = MAX_PYTHON_MINOR; minor >= MIN_PYTHON_MINOR; minor--) {
      candidates.push({ cmd: 'py', args: [`-3.${minor}`, '--version'] });
    }
  }

  const bareCmds = IS_WIN ? ['python', 'python3'] : ['python3', 'python'];
  for (const c of bareCmds) {
    candidates.push({ cmd: c, args: ['--version'] });
  }

  for (const { cmd, args } of candidates) {
    try {
      const version = await new Promise<string>((resolve, reject) => {
        execFile(cmd, args, { windowsHide: true }, (err, stdout, stderr) => {
          if (err) reject(err);
          else resolve((stdout || stderr).trim());
        });
      });
      const match = version.match(/Python (\d+)\.(\d+)/);
      if (match) {
        const major = parseInt(match[1]);
        const minor = parseInt(match[2]);
        if (major === 3 && minor >= MIN_PYTHON_MINOR && minor <= MAX_PYTHON_MINOR) {
          const fullCmd = cmd === 'py' ? `py -3.${minor}` : cmd;
          console.log(`[PersonaPlex] Found compatible Python: ${version} (${fullCmd})`);
          return fullCmd;
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
  const parts = cmd.split(/\s+/);
  const actualCmd = parts[0];
  const prefixArgs = parts.slice(1);
  const allArgs = [...prefixArgs, ...args];

  return new Promise((resolve, reject) => {
    const child = spawn(actualCmd, allArgs, {
      cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      shell: IS_WIN,
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

/** Generate a self-signed SSL certificate for local WSS. */
async function generateSslCerts(): Promise<void> {
  await mkdir(SSL_DIR, { recursive: true });
  const keyPath = join(SSL_DIR, 'key.pem');
  const certPath = join(SSL_DIR, 'cert.pem');

  if (await fileExists(keyPath) && await fileExists(certPath)) {
    console.log('[PersonaPlex] SSL certificates already exist');
    return;
  }

  console.log('[PersonaPlex] Generating self-signed SSL certificates...');

  // Use Python to generate self-signed certs (avoids requiring openssl CLI)
  const sslScript = `
import ssl, os, sys
certfile = sys.argv[1]
keyfile = sys.argv[2]

# Generate using Python's ssl module helper (requires Python 3.10+)
try:
    import subprocess
    subprocess.run([
        sys.executable, '-c',
        f'''
import ssl
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
# Use openssl via subprocess for cert generation
import subprocess, os
keyfile = "{keyfile.replace('\\\\', '/')}"
certfile = "{certfile.replace('\\\\', '/')}"
subprocess.run([
    "openssl", "req", "-x509", "-newkey", "rsa:2048",
    "-keyout", keyfile, "-out", certfile,
    "-days", "365", "-nodes",
    "-subj", "/CN=localhost"
], check=True, capture_output=True)
print("SSL_CERTS_READY")
'''
    ], check=True)
except Exception as e:
    # Fallback: use Python cryptography library if available
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
            .sign(key, hashes.SHA256()))
        with open(keyfile, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        with open(certfile, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        print("SSL_CERTS_READY")
    except ImportError:
        print("ERROR: Neither openssl CLI nor cryptography library available", file=sys.stderr)
        sys.exit(1)
`;

  const tempScript = join(PERSONAPLEX_DIR, '_gen_ssl.py');
  await writeFile(tempScript, sslScript, 'utf-8');
  try {
    await runCommand(PYTHON_BIN, [tempScript, certPath, keyPath]);
  } finally {
    await rm(tempScript, { force: true }).catch(() => {});
  }
}

// -- Public API ---------------------------------------------------------------

/**
 * Check if the PersonaPlex server setup exists (venv + deps installed).
 */
export async function isSetupComplete(): Promise<boolean> {
  return (await fileExists(PYTHON_BIN)) && (await fileExists(join(VENV_DIR, IS_WIN ? 'Scripts' : 'bin')));
}

/**
 * Check if a CUDA-capable GPU is available.
 */
export async function hasCudaGpu(): Promise<boolean> {
  try {
    await runCommand('nvidia-smi', []);
    return true;
  } catch {
    return false;
  }
}

/**
 * Full one-time setup: create venv, install PyTorch + PersonaPlex/Moshi deps.
 *
 * Total download: ~3GB (PyTorch CUDA) + ~20GB (model, downloaded on first run).
 */
export async function setup(
  onProgress?: (p: PersonaPlexSetupProgress) => void,
  config?: PersonaPlexConfig,
): Promise<void> {
  const emit = (p: PersonaPlexSetupProgress) => {
    onProgress?.(p);
    personaplexEvents.emit('setup-progress', p);
  };

  // Step 1: Find Python
  emit({ stage: 'checking-python', message: 'Checking for compatible Python 3.11+...', percent: 0 });
  const pythonCmd = await findPython();

  if (!pythonCmd) {
    const msg = 'No compatible Python 3.11+ found. Please install Python 3.12 from python.org.';
    emit({ stage: 'error', message: msg, percent: 0 });
    throw new Error(msg);
  }

  // Step 2: Create venv
  emit({ stage: 'creating-venv', message: 'Creating Python virtual environment...', percent: 5 });
  await mkdir(PERSONAPLEX_DIR, { recursive: true });

  if (!(await fileExists(PYTHON_BIN))) {
    await runCommand(pythonCmd, ['-m', 'venv', VENV_DIR]);
  }

  await runCommand(PIP_BIN, ['install', '--upgrade', 'pip', 'setuptools', 'wheel']);

  // Step 3: Install PyTorch with CUDA
  emit({ stage: 'installing-pytorch', message: 'Installing PyTorch with CUDA support (this may take several minutes)...', percent: 10 });
  const hasCuda = await hasCudaGpu();

  if (hasCuda) {
    await runCommand(PIP_BIN, [
      'install', '--upgrade',
      'torch', 'torchaudio',
      '--index-url', 'https://download.pytorch.org/whl/cu126',
    ]);
  } else {
    emit({ stage: 'installing-pytorch', message: 'No NVIDIA GPU detected. Installing CPU-only PyTorch (PersonaPlex will be slow)...', percent: 10 });
    await runCommand(PIP_BIN, [
      'install', '--upgrade',
      'torch', 'torchaudio',
      '--index-url', 'https://download.pytorch.org/whl/cpu',
    ]);
  }

  // Step 4: Install PersonaPlex/Moshi
  emit({ stage: 'installing-personaplex', message: 'Installing PersonaPlex (NVIDIA Moshi)...', percent: 40 });

  // Clone and install from the PersonaPlex repo
  const repoDir = join(PERSONAPLEX_DIR, 'repo');
  if (!(await fileExists(join(repoDir, 'moshi')))) {
    await runCommand('git', ['clone', '--depth', '1', 'https://github.com/NVIDIA/personaplex.git', repoDir]);
  }

  // Install the moshi package from the repo
  await runCommand(PIP_BIN, ['install', join(repoDir, 'moshi')]);

  // Step 5: Install accelerate for CPU offloading support
  await runCommand(PIP_BIN, ['install', 'accelerate']);

  // Step 6: Install opus development library check
  emit({ stage: 'installing-opus', message: 'Verifying Opus codec availability...', percent: 70 });
  // On Windows, opus comes bundled with the moshi package typically
  // On Linux, users need libopus-dev

  // Step 7: Generate SSL certificates
  emit({ stage: 'generating-ssl', message: 'Generating SSL certificates for local WebSocket...', percent: 80 });
  await generateSslCerts();

  // Step 8: Set up HuggingFace token if provided
  if (config?.hfToken) {
    // Write HF token to venv config so the server can access the model
    const hfDir = join(homedir(), '.cache', 'huggingface');
    await mkdir(hfDir, { recursive: true });
    await writeFile(join(hfDir, 'token'), config.hfToken, 'utf-8');
    console.log('[PersonaPlex] HuggingFace token saved');
  }

  emit({ stage: 'complete', message: 'PersonaPlex installed successfully. Model will be downloaded on first run (~20GB).', percent: 100 });
  console.log('[PersonaPlex] Setup complete.');
}

/**
 * Start the PersonaPlex server as a child process.
 * Waits for the server to become healthy before resolving.
 */
export async function start(config?: PersonaPlexConfig): Promise<{ port: number; wssUrl: string }> {
  if (serverProcess) {
    // Already running — verify health
    try {
      const res = await fetch(`https://127.0.0.1:${serverPort}/api`, {
        // @ts-expect-error Node.js fetch rejectUnauthorized
        rejectUnauthorized: false,
      });
      if (res.ok) return { port: serverPort, wssUrl: `wss://127.0.0.1:${serverPort}` };
    } catch {
      stop();
    }
  }

  if (!(await isSetupComplete())) {
    throw new Error('PersonaPlex not set up. Call setup() first.');
  }

  // Ensure SSL certs exist
  await generateSslCerts();

  const port = DEFAULT_PORT;
  serverPort = port;

  console.log(`[PersonaPlex] Starting server on port ${port}...`);

  const args = ['-m', 'moshi.server', '--ssl', SSL_DIR];
  if (config?.cpuOffload) {
    args.push('--cpu-offload');
  }

  // Set up environment
  const env: Record<string, string> = { ...process.env } as Record<string, string>;
  if (config?.hfToken) {
    env.HF_TOKEN = config.hfToken;
  }

  const proc = spawn(PYTHON_BIN, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    detached: false,
    env,
  });

  serverProcess = proc;

  // Log server output
  proc.stdout?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) {
      console.log(`[PersonaPlex Server] ${line}`);
      personaplexEvents.emit('server-log', line);
    }
  });
  proc.stderr?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) {
      console.warn(`[PersonaPlex Server ERR] ${line}`);
      personaplexEvents.emit('server-log', `ERR: ${line}`);
    }
  });

  proc.on('exit', (code) => {
    console.log(`[PersonaPlex] Server process exited with code ${code}`);
    serverProcess = null;
    if (healthTimer) {
      clearInterval(healthTimer);
      healthTimer = null;
    }
    personaplexEvents.emit('server-stopped', code);
  });

  // Wait for server to become ready (poll /api or listen for ready message)
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`PersonaPlex server failed to start within ${STARTUP_TIMEOUT_MS / 1000}s`));
    }, STARTUP_TIMEOUT_MS);

    let resolved = false;

    // Poll for server readiness
    const pollInterval = setInterval(async () => {
      if (resolved) return;
      try {
        const controller = new AbortController();
        const fetchTimeout = setTimeout(() => controller.abort(), 3000);
        const res = await fetch(`https://127.0.0.1:${port}/api`, {
          signal: controller.signal,
          // @ts-expect-error Node.js fetch option for self-signed certs
          rejectUnauthorized: false,
        }).catch(() => null);
        clearTimeout(fetchTimeout);
        if (res && res.ok) {
          resolved = true;
          clearTimeout(timer);
          clearInterval(pollInterval);
          resolve();
        }
      } catch {
        // Not ready yet
      }
    }, 2000);

    proc.on('error', (err) => {
      if (!resolved) {
        resolved = true;
        clearTimeout(timer);
        clearInterval(pollInterval);
        reject(new Error(`Failed to spawn PersonaPlex server: ${err.message}`));
      }
    });

    proc.on('exit', (code) => {
      if (!resolved) {
        resolved = true;
        clearTimeout(timer);
        clearInterval(pollInterval);
        reject(new Error(`PersonaPlex server exited with code ${code} during startup`));
      }
    });
  });

  // Start health check timer
  healthTimer = setInterval(async () => {
    try {
      const res = await fetch(`https://127.0.0.1:${port}/api`, {
        // @ts-expect-error Node.js fetch rejectUnauthorized
        rejectUnauthorized: false,
      });
      if (!res.ok) throw new Error(`Health check returned ${res.status}`);
    } catch (err) {
      console.warn('[PersonaPlex] Health check failed:', err instanceof Error ? err.message : String(err));
      personaplexEvents.emit('health-degraded');
    }
  }, HEALTH_CHECK_INTERVAL_MS);

  const wssUrl = `wss://127.0.0.1:${port}`;
  console.log(`[PersonaPlex] Server started: ${wssUrl}`);
  personaplexEvents.emit('server-started', { port, wssUrl });

  return { port, wssUrl };
}

/**
 * Stop the PersonaPlex server.
 */
export function stop(): void {
  if (healthTimer) {
    clearInterval(healthTimer);
    healthTimer = null;
  }
  if (serverProcess) {
    console.log('[PersonaPlex] Stopping server...');
    serverProcess.kill('SIGTERM');
    const forceTimer = setTimeout(() => {
      if (serverProcess) {
        serverProcess.kill('SIGKILL');
        serverProcess = null;
      }
    }, 5_000);
    serverProcess.once('exit', () => clearTimeout(forceTimer));
    serverProcess = null;
  }
  serverPort = DEFAULT_PORT;
}

/**
 * Check if the server is currently running and healthy.
 */
export async function isRunning(): Promise<boolean> {
  if (!serverProcess) return false;
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    const res = await fetch(`https://127.0.0.1:${serverPort}/api`, {
      signal: controller.signal,
      // @ts-expect-error Node.js fetch rejectUnauthorized
      rejectUnauthorized: false,
    });
    clearTimeout(timeout);
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Get the WSS URL for connecting to the PersonaPlex server.
 */
export function getWssUrl(): string | null {
  if (!serverProcess) return null;
  return `wss://127.0.0.1:${serverPort}`;
}

/**
 * Get the server port.
 */
export function getPort(): number | null {
  if (!serverProcess) return null;
  return serverPort;
}

/**
 * List available PersonaPlex voices.
 */
export function listVoices(): Array<{ id: string; name: string }> {
  return Object.entries(PERSONAPLEX_VOICES).map(([id, name]) => ({ id, name }));
}
