/**
 * binary-downloader.ts -- Downloads pre-built voice binaries (whisper-cpp, sherpa-onnx)
 * from GitHub releases to ~/.nexus-os/bin/ when not locally available.
 *
 * Binaries downloaded:
 *   - whisper-cpp (STT): from ggerganov/whisper.cpp releases
 *   - sherpa-onnx-offline-tts (TTS): from k2-fsa/sherpa-onnx releases
 *
 * Also downloads a default TTS voice model if none exists.
 *
 * Binary discovery reuses `findBinary` / `fileExists` from tts-binding.ts,
 * so the search order remains:
 *   1. ~/.nexus-os/bin/       (user-installed / auto-downloaded)
 *   2. App resources/bin/     (bundled with installer)
 *   3. System PATH            (global install)
 */

import { createWriteStream } from 'node:fs';
import { mkdir, rename, unlink, readdir, chmod } from 'node:fs/promises';
import { homedir, platform, arch } from 'node:os';
import { join } from 'node:path';
import { execFile } from 'node:child_process';
import { pipeline as streamPipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';

import { findBinary, fileExists } from './tts-binding';

// -- Version constants (update when new releases are available) ---------------

const WHISPER_CPP_VERSION = '1.7.3';
const SHERPA_ONNX_VERSION = '1.10.35';
const PIPER_VOICES_VERSION = 'v1.0.0';

// -- Paths --------------------------------------------------------------------

const BIN_DIR = join(homedir(), '.nexus-os', 'bin');
const MODELS_DIR = join(homedir(), '.nexus-os', 'models', 'tts');
const IS_WIN = platform() === 'win32';
const ARCH = arch(); // 'x64', 'arm64', etc.

// -- Progress callback type ---------------------------------------------------

export type ProgressCallback = (downloaded: number, total: number) => void;

// -- Download URL builders ----------------------------------------------------

function getWhisperDownloadUrl(): string {
  if (IS_WIN) {
    // Windows x64 zip release
    return `https://github.com/ggerganov/whisper.cpp/releases/download/v${WHISPER_CPP_VERSION}/whisper-v${WHISPER_CPP_VERSION}-bin-x64.zip`;
  }
  if (platform() === 'darwin') {
    return `https://github.com/ggerganov/whisper.cpp/releases/download/v${WHISPER_CPP_VERSION}/whisper-v${WHISPER_CPP_VERSION}-bin-${ARCH === 'arm64' ? 'arm64' : 'x64'}.zip`;
  }
  // Linux
  return `https://github.com/ggerganov/whisper.cpp/releases/download/v${WHISPER_CPP_VERSION}/whisper-v${WHISPER_CPP_VERSION}-bin-x64.zip`;
}

function getSherpaOnnxDownloadUrl(): string {
  if (IS_WIN) {
    return `https://github.com/k2-fsa/sherpa-onnx/releases/download/v${SHERPA_ONNX_VERSION}/sherpa-onnx-v${SHERPA_ONNX_VERSION}-win-x64.tar.bz2`;
  }
  if (platform() === 'darwin') {
    const macArch = ARCH === 'arm64' ? 'arm64' : 'x86_64';
    return `https://github.com/k2-fsa/sherpa-onnx/releases/download/v${SHERPA_ONNX_VERSION}/sherpa-onnx-v${SHERPA_ONNX_VERSION}-osx-${macArch}.tar.bz2`;
  }
  // Linux
  return `https://github.com/k2-fsa/sherpa-onnx/releases/download/v${SHERPA_ONNX_VERSION}/sherpa-onnx-v${SHERPA_ONNX_VERSION}-linux-x64.tar.bz2`;
}

function getPiperVoiceModelUrl(): string {
  return `https://huggingface.co/rhasspy/piper-voices/resolve/${PIPER_VOICES_VERSION}/en/en_US/amy/medium/en_US-amy-medium.onnx`;
}

function getPiperVoiceConfigUrl(): string {
  return `https://huggingface.co/rhasspy/piper-voices/resolve/${PIPER_VOICES_VERSION}/en/en_US/amy/medium/en_US-amy-medium.onnx.json`;
}

// -- Core download utility ----------------------------------------------------

/**
 * Download a file from `url` to `destPath`, reporting progress via `onProgress`.
 * Uses Node.js built-in `fetch`. Creates parent directories as needed.
 * Returns the final file path.
 */
async function downloadFile(
  url: string,
  destPath: string,
  onProgress?: ProgressCallback,
): Promise<string> {
  const dir = join(destPath, '..');
  await mkdir(dir, { recursive: true });

  // Use a temp file to avoid partial downloads being mistaken for valid files
  const tempPath = destPath + '.download';

  try {
    const response = await fetch(url, { redirect: 'follow' });

    if (!response.ok) {
      throw new Error(`Download failed: HTTP ${response.status} ${response.statusText} for ${url}`);
    }

    const contentLength = parseInt(response.headers.get('content-length') ?? '0', 10);
    const total = contentLength || 0;

    if (!response.body) {
      throw new Error(`Download failed: no response body for ${url}`);
    }

    const fileStream = createWriteStream(tempPath);
    let downloaded = 0;

    // Convert the web ReadableStream to a Node.js Readable
    const reader = response.body.getReader();
    const nodeStream = new Readable({
      async read() {
        try {
          const { done, value } = await reader.read();
          if (done) {
            this.push(null);
            return;
          }
          downloaded += value.byteLength;
          if (onProgress) {
            onProgress(downloaded, total);
          }
          this.push(Buffer.from(value));
        } catch (err) {
          this.destroy(err as Error);
        }
      },
    });

    await streamPipeline(nodeStream, fileStream);

    // Move temp file to final destination
    await rename(tempPath, destPath);

    return destPath;
  } catch (err) {
    // Clean up temp file on failure
    await unlink(tempPath).catch(() => {});
    throw err;
  }
}

// -- Archive extraction -------------------------------------------------------

/**
 * Extract a .zip archive. On Windows uses PowerShell Expand-Archive;
 * on Unix uses the `unzip` command.
 */
function extractZip(archivePath: string, destDir: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (IS_WIN) {
      // PowerShell Expand-Archive
      const psCmd = `Expand-Archive -Path '${archivePath}' -DestinationPath '${destDir}' -Force`;
      execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', psCmd], {
        windowsHide: true,
        timeout: 120_000,
      }, (err, _stdout, stderr) => {
        if (err) {
          reject(new Error(`zip extraction failed: ${err.message}\n${stderr}`));
        } else {
          resolve();
        }
      });
    } else {
      execFile('unzip', ['-o', archivePath, '-d', destDir], {
        timeout: 120_000,
      }, (err, _stdout, stderr) => {
        if (err) {
          reject(new Error(`zip extraction failed: ${err.message}\n${stderr}`));
        } else {
          resolve();
        }
      });
    }
  });
}

/**
 * Extract a .tar.bz2 archive. On Windows uses `tar` (available since Win10 1803);
 * on Unix uses `tar` directly.
 */
function extractTarBz2(archivePath: string, destDir: string): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile('tar', ['-xjf', archivePath, '-C', destDir], {
      windowsHide: true,
      timeout: 120_000,
    }, (err, _stdout, stderr) => {
      if (err) {
        reject(new Error(`tar extraction failed: ${err.message}\n${stderr}`));
      } else {
        resolve();
      }
    });
  });
}

// -- File search in extracted directories -------------------------------------

/**
 * Recursively search for a file by name in a directory tree.
 * Returns the first match or null.
 */
async function findFileRecursive(dir: string, targetName: string): Promise<string | null> {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return null;
  }

  for (const entry of entries) {
    const fullPath = join(dir, entry.name);
    if (entry.isFile() && entry.name === targetName) {
      return fullPath;
    }
    if (entry.isDirectory()) {
      const found = await findFileRecursive(fullPath, targetName);
      if (found) return found;
    }
  }
  return null;
}

// -- Temporary directory management -------------------------------------------

/**
 * Create a unique temporary directory under ~/.nexus-os/tmp/ for extraction.
 */
async function makeTempDir(): Promise<string> {
  const tmpBase = join(homedir(), '.nexus-os', 'tmp');
  await mkdir(tmpBase, { recursive: true });
  const dirName = `extract-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const tmpDir = join(tmpBase, dirName);
  await mkdir(tmpDir, { recursive: true });
  return tmpDir;
}

/**
 * Remove a directory recursively. Best-effort, won't throw.
 */
async function rmDir(dir: string): Promise<void> {
  try {
    const { rm } = await import('node:fs/promises');
    await rm(dir, { recursive: true, force: true });
  } catch {
    // best-effort cleanup
  }
}

// -- Make binary executable (non-Windows) -------------------------------------

async function makeExecutable(filePath: string): Promise<void> {
  if (!IS_WIN) {
    await chmod(filePath, 0o755);
  }
}

// -- Public API: ensureWhisperBinary ------------------------------------------

/**
 * Ensure the whisper-cpp binary is available locally. If not found via the
 * standard search order, downloads it from GitHub releases to ~/.nexus-os/bin/.
 *
 * @param onProgress  Optional progress callback: (downloaded, total) => void
 * @returns           Absolute path to the whisper-cpp binary
 */
export async function ensureWhisperBinary(onProgress?: ProgressCallback): Promise<string> {
  // Check all standard locations first
  for (const name of ['whisper-cpp', 'main', 'whisper']) {
    const existing = await findBinary(name);
    if (existing) {
      console.log(`[BinaryDownloader] Whisper binary found at: ${existing}`);
      return existing;
    }
  }

  console.log('[BinaryDownloader] Whisper binary not found — downloading...');

  await mkdir(BIN_DIR, { recursive: true });

  const url = getWhisperDownloadUrl();
  const archiveExt = url.endsWith('.tar.bz2') ? '.tar.bz2' : '.zip';
  const archiveName = `whisper-cpp${archiveExt}`;
  const archivePath = join(BIN_DIR, archiveName);
  const tmpDir = await makeTempDir();

  try {
    // Download the archive
    await downloadFile(url, archivePath, onProgress);

    // Extract to temp directory
    if (archiveExt === '.zip') {
      await extractZip(archivePath, tmpDir);
    } else {
      await extractTarBz2(archivePath, tmpDir);
    }

    // Find the main binary in the extracted contents
    // whisper.cpp releases typically name the binary `main` or `main.exe`
    const targetExeName = IS_WIN ? 'main.exe' : 'main';
    const destExeName = IS_WIN ? 'whisper-cpp.exe' : 'whisper-cpp';

    let foundBinary = await findFileRecursive(tmpDir, targetExeName);

    // Some releases may use 'whisper' or 'whisper-cpp' as the binary name
    if (!foundBinary) {
      const altName = IS_WIN ? 'whisper.exe' : 'whisper';
      foundBinary = await findFileRecursive(tmpDir, altName);
    }
    if (!foundBinary) {
      const altName = IS_WIN ? 'whisper-cpp.exe' : 'whisper-cpp';
      foundBinary = await findFileRecursive(tmpDir, altName);
    }

    if (!foundBinary) {
      throw new Error(
        `Could not find whisper binary in downloaded archive. ` +
        `Searched for: ${targetExeName}, whisper, whisper-cpp in ${tmpDir}`,
      );
    }

    // Move to bin directory with canonical name
    const destPath = join(BIN_DIR, destExeName);
    await rename(foundBinary, destPath);
    await makeExecutable(destPath);

    console.log(`[BinaryDownloader] Whisper binary installed: ${destPath}`);
    return destPath;
  } finally {
    // Clean up archive and temp dir
    await unlink(archivePath).catch(() => {});
    await rmDir(tmpDir);
  }
}

// -- Public API: ensureTTSBinary ----------------------------------------------

/**
 * Ensure the sherpa-onnx-offline-tts binary is available locally. If not found,
 * downloads from GitHub releases to ~/.nexus-os/bin/.
 *
 * @param onProgress  Optional progress callback: (downloaded, total) => void
 * @returns           Absolute path to the sherpa-onnx-offline-tts binary
 */
export async function ensureTTSBinary(onProgress?: ProgressCallback): Promise<string> {
  // Check standard locations
  const existing = await findBinary('sherpa-onnx-offline-tts');
  if (existing) {
    console.log(`[BinaryDownloader] TTS binary found at: ${existing}`);
    return existing;
  }

  console.log('[BinaryDownloader] TTS binary not found — downloading...');

  await mkdir(BIN_DIR, { recursive: true });

  const url = getSherpaOnnxDownloadUrl();
  const archiveExt = url.endsWith('.tar.bz2') ? '.tar.bz2' : '.zip';
  const archiveName = `sherpa-onnx${archiveExt}`;
  const archivePath = join(BIN_DIR, archiveName);
  const tmpDir = await makeTempDir();

  try {
    // Download the archive
    await downloadFile(url, archivePath, onProgress);

    // Extract to temp directory
    if (archiveExt === '.zip') {
      await extractZip(archivePath, tmpDir);
    } else {
      await extractTarBz2(archivePath, tmpDir);
    }

    // Find the sherpa-onnx-offline-tts binary
    const targetExeName = IS_WIN ? 'sherpa-onnx-offline-tts.exe' : 'sherpa-onnx-offline-tts';
    const foundBinary = await findFileRecursive(tmpDir, targetExeName);

    if (!foundBinary) {
      throw new Error(
        `Could not find ${targetExeName} in downloaded archive. ` +
        `Searched recursively in ${tmpDir}`,
      );
    }

    // Move to bin directory
    const destPath = join(BIN_DIR, targetExeName);
    await rename(foundBinary, destPath);
    await makeExecutable(destPath);

    // Also copy any required shared libraries (.dll / .so / .dylib) from the
    // same directory as the binary
    const binaryDir = join(foundBinary, '..');
    try {
      const siblingFiles = await readdir(binaryDir, { withFileTypes: true });
      for (const file of siblingFiles) {
        if (file.isFile() && isSharedLib(file.name)) {
          const srcLib = join(binaryDir, file.name);
          const destLib = join(BIN_DIR, file.name);
          if (!(await fileExists(destLib))) {
            await rename(srcLib, destLib).catch(async () => {
              // rename may fail across devices — fall back to copy
              const { copyFile } = await import('node:fs/promises');
              await copyFile(srcLib, destLib);
            });
          }
        }
      }
    } catch {
      // Non-critical: binary might work without shared libs if statically linked
    }

    console.log(`[BinaryDownloader] TTS binary installed: ${destPath}`);
    return destPath;
  } finally {
    // Clean up archive and temp dir
    await unlink(archivePath).catch(() => {});
    await rmDir(tmpDir);
  }
}

/**
 * Check if a filename looks like a shared library.
 */
function isSharedLib(name: string): boolean {
  return name.endsWith('.dll') || name.endsWith('.so') || name.endsWith('.dylib') ||
    /\.so\.\d+/.test(name);
}

// -- Public API: ensureTTSModel -----------------------------------------------

/**
 * Ensure a default Piper TTS voice model exists at ~/.nexus-os/models/tts/.
 * Downloads en_US-amy-medium if no .onnx voice model is found.
 *
 * @param onProgress  Optional progress callback: (downloaded, total) => void
 * @returns           Absolute path to the .onnx model file
 */
export async function ensureTTSModel(onProgress?: ProgressCallback): Promise<string> {
  await mkdir(MODELS_DIR, { recursive: true });

  // Check if any .onnx model already exists
  const existingModel = await findExistingModel();
  if (existingModel) {
    console.log(`[BinaryDownloader] TTS model found at: ${existingModel}`);
    return existingModel;
  }

  console.log('[BinaryDownloader] No TTS model found — downloading en_US-amy-medium...');

  const modelFileName = 'en_US-amy-medium.onnx';
  const configFileName = 'en_US-amy-medium.onnx.json';
  const modelPath = join(MODELS_DIR, modelFileName);
  const configPath = join(MODELS_DIR, configFileName);

  // Download both the model and its config file.
  // Split progress: model gets 95% of progress, config gets 5%.
  const modelUrl = getPiperVoiceModelUrl();
  const configUrl = getPiperVoiceConfigUrl();

  // Download model (the large file)
  await downloadFile(modelUrl, modelPath, onProgress);

  // Download config (small JSON file, no progress needed)
  await downloadFile(configUrl, configPath);

  console.log(`[BinaryDownloader] TTS model installed: ${modelPath}`);
  return modelPath;
}

/**
 * Look for any existing .onnx voice model in the models/tts directory.
 */
async function findExistingModel(): Promise<string | null> {
  try {
    const entries = await readdir(MODELS_DIR);
    for (const entry of entries) {
      if (entry.endsWith('.onnx') && !entry.endsWith('.onnx.json')) {
        return join(MODELS_DIR, entry);
      }
    }
  } catch {
    // Directory might not exist yet
  }
  return null;
}
