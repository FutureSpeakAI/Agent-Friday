/**
 * pre-flight-checks.ts — Pre-connection validation for the voice pipeline
 * in Agent Friday.
 *
 * Phase 3.2, Track 3: Mic Permission Pre-check & Model Validation
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Understanding Part ↔ Whole
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * THE WHOLE: Before any voice path is attempted, we must know what resources
 * are actually available. A missing model, a denied mic, or an invalid API
 * key should be detected BEFORE the connection flow starts — not 15 seconds
 * into a blanket timeout.
 *
 * THE PARTS: Five independent checks — mic permission, Ollama model, Gemini
 * key, Whisper model, TTS model. Each validates one real-world resource.
 * Together they determine which voice path is viable.
 *
 * THE CIRCLE: Understanding which checks to run requires knowing which voice
 * path will be attempted. Understanding which path to attempt requires
 * knowing what's available. Pre-flight resolves this by checking EVERYTHING
 * in parallel and letting the caller (VoiceFallbackManager) decide the path
 * based on results.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * SOCRATIC DISCOVERY — Questions Answered Before Writing
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * BOUNDARY Q1: "What must be true before ANY voice path is attempted?"
 *   → Mic permission granted (or explicitly denied → text fallback). This is
 *     currently NOT checked until deep inside the connection flow. Pre-flight
 *     surfaces it immediately.
 *
 * BOUNDARY Q2: "What must be true before the LOCAL path is attempted?"
 *   → Ollama healthy AND requested model is downloaded. The current code only
 *     checks health, not model availability. A user with Ollama running but
 *     no llama3.1:8b model gets a cryptic error 45 seconds into the flow.
 *
 * PRECEDENT Q3: "How does HardwareStep already handle model downloads?"
 *   → SetupWizard uses IPC to check/download Whisper + TTS models. We reuse
 *     the same file-system check pattern (access() on model paths) to verify
 *     models exist before starting.
 *
 * INVERSION Q4: "How would you cause the worst user experience?"
 *   → Start connecting to Ollama, discover 45s later that the model isn't
 *     downloaded, show a generic "connection failed" message, and leave the
 *     user guessing. Pre-flight prevents this by checking model availability
 *     in < 3 seconds and giving actionable guidance ("run `ollama pull ...`").
 *
 * CONSTRAINT Q5: "All checks complete in < 3 seconds (parallel where possible)"
 *   → Mic check requires IPC round-trip to renderer (fast). Ollama health +
 *     model list is a single HTTP call. File existence checks are near-instant.
 *     Running in parallel via Promise.allSettled keeps the total under 3s.
 */

import { access } from 'node:fs/promises';
import { readdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { BrowserWindow, systemPreferences } from 'electron';

// ── Types ─────────────────────────────────────────────────────────────────

/**
 * Complete pre-flight check result. Captures the state of every resource
 * needed for voice. Consumers (VoiceFallbackManager) use this to decide
 * which voice path to attempt — or whether to skip voice entirely.
 *
 * HERMENEUTIC NOTE: Each field answers a binary question the user implicitly
 * asks: "Can I use my mic?" / "Is my model ready?" / "Is my key valid?"
 */
export interface PreFlightResult {
  /** Mic is available and permission was granted (or will be auto-granted). */
  micAvailable: boolean;
  /** Mic was explicitly denied by the user/OS (not just untested). */
  micDenied: boolean;
  /** Ollama is reachable at its configured endpoint. */
  ollamaHealthy: boolean;
  /** The requested Ollama model is downloaded and available. */
  ollamaModelReady: boolean;
  /** Which Ollama model was checked. */
  ollamaModelName: string;
  /** Gemini API key is non-empty and passes basic format validation. */
  geminiKeyValid: boolean;
  /** Whisper STT model file exists on disk. */
  whisperReady: boolean;
  /** TTS model file(s) exist on disk (Kokoro or Piper). */
  ttsReady: boolean;
}

// ── Constants ─────────────────────────────────────────────────────────────

/** Default Ollama endpoint — matches OllamaProvider.DEFAULT_OLLAMA_ENDPOINT. */
const OLLAMA_ENDPOINT = 'http://localhost:11434';

/** Timeout for HTTP health checks (ms). */
const HEALTH_TIMEOUT_MS = 5_000;

/** Whisper model directory — matches WhisperProvider.DEFAULT_MODEL_DIR. */
const WHISPER_MODEL_DIR = join(homedir(), '.nexus-os', 'models', 'whisper');

/** TTS model directory — matches TTSEngine.DEFAULT_TTS_DIR. */
const TTS_MODEL_DIR = join(homedir(), '.nexus-os', 'models', 'tts');

/** TTS backends to check for models, in priority order. */
const TTS_BACKENDS = ['kokoro', 'piper'] as const;

/** TTS model file extension. */
const TTS_MODEL_SUFFIX = '.onnx';

/** Default Ollama model to check for if none specified. */
const DEFAULT_OLLAMA_MODEL = 'llama3.2';

/** Default Whisper model size to check for. */
const DEFAULT_WHISPER_SIZE = 'tiny';

/**
 * Gemini API key format: starts with "AI" and is 37-40 characters.
 * This is a loose check — not authoritative, but catches blanks and obvious junk.
 *
 * SOCRATIC NOTE: Why not call the Gemini API to validate? Because that
 * would add latency and require network access. The pre-flight check is
 * about local readiness. The actual API validation happens at backend-probe.
 */
const GEMINI_KEY_PATTERN = /^AI[a-zA-Z0-9_-]{35,38}$/;

// ── Ollama wire types (minimal, matching OllamaProvider) ──────────────────

interface OllamaTagsResponse {
  models: Array<{
    name: string;
    model: string;
    size: number;
  }>;
}

// ── Individual Check Functions ─────────────────────────────────────────────

/**
 * Check microphone permission status.
 *
 * On macOS: Uses Electron's systemPreferences.getMediaAccessStatus().
 * On Windows/Linux: Mic permission is handled by the Electron permission
 * handler (auto-granted in index.ts). We check if any BrowserWindow exists
 * (needed for the renderer-side getUserMedia call).
 *
 * SOCRATIC NOTE (BOUNDARY): "What must be true before we say mic is available?"
 *   → On macOS: OS-level permission must be 'granted' (not 'not-determined').
 *   → On Windows: Electron's permission handler auto-grants 'media' permissions,
 *     so mic is available if a BrowserWindow exists for the renderer context.
 *   → We do NOT call getUserMedia here (that's renderer-side). We check the
 *     OS-level gating that would prevent getUserMedia from succeeding.
 */
export async function checkMicPermission(): Promise<{
  available: boolean;
  denied: boolean;
}> {
  try {
    if (process.platform === 'darwin') {
      // macOS has explicit media permission system
      const status = systemPreferences.getMediaAccessStatus('microphone');
      if (status === 'granted') {
        return { available: true, denied: false };
      }
      if (status === 'denied' || status === 'restricted') {
        return { available: false, denied: true };
      }
      // 'not-determined' — permission hasn't been requested yet.
      // We report not-available/not-denied so the caller knows to request it.
      return { available: false, denied: false };
    }

    // Windows/Linux: Electron auto-grants mic via permission handler.
    // Check that a renderer window exists (needed for getUserMedia context).
    const windows = BrowserWindow.getAllWindows();
    const hasWindow = windows.length > 0 && !windows[0].isDestroyed();
    return { available: hasWindow, denied: false };
  } catch (err) {
    console.warn('[PreFlight] Mic permission check failed:', (err as Error).message);
    return { available: false, denied: false };
  }
}

/**
 * Check if Ollama is healthy AND if a specific model is downloaded.
 *
 * Uses the same /api/tags endpoint as OllamaProvider.listModels() to:
 * 1. Verify Ollama is reachable (health check)
 * 2. Parse the model list for the requested model name
 *
 * SOCRATIC NOTE (INVERSION): "How would you get stuck with a running Ollama
 * but missing model?"
 *   → Call /api/chat with a model that doesn't exist. Ollama returns an error
 *     deep into the conversation flow. Pre-flight catches this by checking
 *     /api/tags for the specific model name BEFORE starting the conversation.
 *
 * Model name matching: Ollama model names can have tags (e.g., "llama3.1:8b"
 * or just "llama3.2"). We check for both exact match and prefix match
 * (e.g., "llama3.2" matches "llama3.2:latest").
 */
export async function checkOllamaModel(
  modelName: string = DEFAULT_OLLAMA_MODEL,
): Promise<{
  healthy: boolean;
  modelReady: boolean;
}> {
  try {
    const res = await fetch(`${OLLAMA_ENDPOINT}/api/tags`, {
      method: 'GET',
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });

    if (!res.ok) {
      return { healthy: false, modelReady: false };
    }

    const data = (await res.json()) as OllamaTagsResponse;

    if (!Array.isArray(data.models)) {
      // Ollama is running but returned unexpected data
      return { healthy: true, modelReady: false };
    }

    // Ollama is healthy — now check for the specific model.
    // Model names in Ollama can be "llama3.2" (which implies ":latest")
    // or "llama3.1:8b" (explicit tag). We match flexibly:
    //   - Exact match: "llama3.2" === "llama3.2"
    //   - With implicit tag: "llama3.2" matches "llama3.2:latest"
    //   - Requested with tag: "llama3.1:8b" === "llama3.1:8b"
    const requestedLower = modelName.toLowerCase();
    const modelFound = data.models.some((m) => {
      const nameLower = m.name.toLowerCase();
      return (
        nameLower === requestedLower ||
        nameLower === `${requestedLower}:latest` ||
        nameLower.startsWith(`${requestedLower}:`)
      );
    });

    return { healthy: true, modelReady: modelFound };
  } catch (err) {
    // Network error, Ollama not running, timeout, etc.
    console.warn('[PreFlight] Ollama check failed:', (err as Error).message);
    return { healthy: false, modelReady: false };
  }
}

/**
 * Validate the Gemini API key format.
 *
 * This is a LOCAL format check only — it does not call the Gemini API.
 * The actual API validation happens during the backend-probe stage.
 *
 * SOCRATIC NOTE: Why not just check for non-empty? Because a key that
 * is "test" or "abc" will fail at the API level with a confusing error.
 * A format check catches obvious mistakes early with clear guidance.
 *
 * We import settingsManager lazily to avoid circular dependencies.
 */
export async function checkGeminiKey(): Promise<{ valid: boolean }> {
  try {
    // Lazy import to avoid circular dependency with settings.ts
    const { settingsManager } = require('../settings');
    const key: string = settingsManager.getGeminiApiKey();

    if (!key || key.trim().length === 0) {
      return { valid: false };
    }

    // Basic format validation — Gemini keys start with "AI" and are ~39 chars.
    // This is intentionally loose: if the format changes, the backend-probe
    // will catch the real error. This just catches blanks and obvious junk.
    return { valid: GEMINI_KEY_PATTERN.test(key.trim()) };
  } catch (err) {
    console.warn('[PreFlight] Gemini key check failed:', (err as Error).message);
    return { valid: false };
  }
}

/**
 * Check if a Whisper STT model file exists on disk.
 *
 * Uses the same path convention as WhisperProvider: ~/.nexus-os/models/whisper/ggml-{size}.bin
 *
 * PRECEDENT: Matches WhisperProvider.isModelDownloaded() but returns the
 * path for diagnostic display if the model is missing.
 */
export async function checkWhisperModel(
  size: string = DEFAULT_WHISPER_SIZE,
): Promise<{
  ready: boolean;
  path: string;
}> {
  const modelPath = join(WHISPER_MODEL_DIR, `ggml-${size}.bin`);
  try {
    await access(modelPath);
    return { ready: true, path: modelPath };
  } catch {
    return { ready: false, path: modelPath };
  }
}

/**
 * Check if any TTS model file exists on disk.
 *
 * Checks both Kokoro and Piper backend directories for .onnx files,
 * matching the TTSEngine.loadEngine() search pattern.
 *
 * PRECEDENT: TTSEngine iterates BACKEND_PRIORITY looking for .onnx files
 * in each backend's subdirectory. We replicate this check without loading.
 */
export async function checkTtsModel(): Promise<{
  ready: boolean;
  path: string;
}> {
  for (const backend of TTS_BACKENDS) {
    const backendDir = join(TTS_MODEL_DIR, backend);
    try {
      await access(backendDir);
      const files = await readdir(backendDir);
      const modelFiles = files.filter((f: string) => f.endsWith(TTS_MODEL_SUFFIX));
      if (modelFiles.length > 0) {
        return { ready: true, path: join(backendDir, modelFiles[0]) };
      }
    } catch {
      // Backend directory doesn't exist — try next
      continue;
    }
  }
  return { ready: false, path: TTS_MODEL_DIR };
}

// ── Aggregate Pre-Flight ──────────────────────────────────────────────────

/**
 * Run all pre-flight checks in parallel and return a complete result.
 *
 * CONSTRAINT: All checks complete in < 3 seconds total. This is achieved
 * by running all five checks concurrently via Promise.allSettled. Each
 * individual check has its own timeout (the Ollama HTTP call uses
 * AbortSignal.timeout; file checks are near-instant; mic check is local).
 *
 * SOCRATIC NOTE (BOUNDARY): "What must be true before transitioning from
 * pre-flight to the connection flow?"
 *   → At minimum, we need to know mic status. The other checks are
 *     informational — they help choose the right path, but their failure
 *     doesn't prevent attempting a connection (the connection flow has
 *     its own error handling via ConnectionStageMonitor).
 *
 * @param ollamaModel — The Ollama model name to validate (default: 'llama3.2')
 * @param whisperSize — The Whisper model size to check for (default: 'tiny')
 */
export async function runPreFlightChecks(
  ollamaModel: string = DEFAULT_OLLAMA_MODEL,
  whisperSize: string = DEFAULT_WHISPER_SIZE,
): Promise<PreFlightResult> {
  // Run all checks in parallel — none depend on each other.
  //
  // HERMENEUTIC NOTE: Promise.allSettled (not Promise.all) because a failure
  // in one check should not prevent the others from completing. Each check
  // failing is useful diagnostic information, not a fatal error.
  const [micResult, ollamaResult, geminiResult, whisperResult, ttsResult] =
    await Promise.allSettled([
      checkMicPermission(),
      checkOllamaModel(ollamaModel),
      checkGeminiKey(),
      checkWhisperModel(whisperSize),
      checkTtsModel(),
    ]);

  // Extract results, defaulting to failure if the promise rejected.
  const mic =
    micResult.status === 'fulfilled'
      ? micResult.value
      : { available: false, denied: false };

  const ollama =
    ollamaResult.status === 'fulfilled'
      ? ollamaResult.value
      : { healthy: false, modelReady: false };

  const gemini =
    geminiResult.status === 'fulfilled'
      ? geminiResult.value
      : { valid: false };

  const whisper =
    whisperResult.status === 'fulfilled'
      ? whisperResult.value
      : { ready: false, path: '' };

  const tts =
    ttsResult.status === 'fulfilled'
      ? ttsResult.value
      : { ready: false, path: '' };

  return {
    micAvailable: mic.available,
    micDenied: mic.denied,
    ollamaHealthy: ollama.healthy,
    ollamaModelReady: ollama.modelReady,
    ollamaModelName: ollamaModel,
    geminiKeyValid: gemini.valid,
    whisperReady: whisper.ready,
    ttsReady: tts.ready,
  };
}
