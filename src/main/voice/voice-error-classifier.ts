/**
 * voice-error-classifier.ts — Error Classification for Agent Friday Voice Pipeline.
 *
 * Transforms raw errors from WebSocket close codes, getUserMedia rejections, Ollama
 * failures, and audio subsystem crashes into actionable, user-friendly messages with
 * concrete recovery paths.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HERMENEUTIC CIRCLE — Understanding Part ↔ Whole
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * THE WHOLE: When voice fails, the user needs to know exactly what's wrong and
 * exactly what to do about it. Generic "Connection failed" messages are hostile —
 * they blame the user for a system failure and give no path forward. The whole
 * purpose of this module is to translate machine errors into human actions.
 *
 * THE PARTS: Each VoiceErrorCategory maps to a specific failure mode with a
 * specific recovery path. The classification function examines error shapes
 * (WebSocket close codes, DOMException names, HTTP status codes, message
 * patterns) to route to the correct category.
 *
 * THE CIRCLE: Understanding what "actionable" means requires knowing what the
 * user can actually do (open settings, retry, grant mic, pull model).
 * Understanding what errors occur requires knowing all failure modes across
 * the voice pipeline. Each informs the other.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * SOCRATIC DISCOVERY — Questions Answered Before Writing
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * TENSION Q1: "How much detail should the user see?"
 *   → Two tiers: (1) Brief message + recovery button (default), (2) expandable
 *     technical details for power users. The `technicalDetail` field exists for
 *     tier 2 but is hidden by default.
 *
 * INVERSION Q1: "If you wanted to make every error look like the user's fault,
 *   how would you phrase the messages?"
 *   → "Invalid API key" instead of "Your API key may be invalid or expired."
 *     We avoid blame language. "Microphone access was denied" not "You denied
 *     the microphone." The system could be wrong (key rotated server-side,
 *     OS-level mic block the user didn't trigger).
 *
 * PRECEDENT Q1: "Does the codebase already classify errors?"
 *   → Yes. `src/main/errors.ts` has a four-category taxonomy (transient,
 *     persistent, recoverable, fatal) with AgentFridayError as the base class.
 *     We extend this vocabulary rather than replacing it. The VoiceErrorCategory
 *     is a finer-grained lens over the same error space, specific to voice.
 *
 * BOUNDARY Q1: "When is an error transient vs. persistent?"
 *   → Transient: will likely fix itself (network blip, rate limit cooldown,
 *     Gemini 5xx). Persistent: requires user action (bad API key, denied mic,
 *     missing model). The `isTransient` flag drives auto-retry behavior.
 *
 * Phase 5.1 Track 5: Error Categorization & Actionable Messages
 * Dependencies: P1.1 (VoiceStateMachine)
 */

import type { VoiceState } from './voice-state-machine';

// ── Types ─────────────────────────────────────────────────────────────────

/**
 * The 14 voice-specific error categories. Each represents a distinct failure
 * mode with a distinct recovery path.
 *
 * DESIGN NOTE: Why 14 and not fewer? Because collapsing categories collapses
 * recovery paths. "mic-denied" and "mic-unavailable" look similar but demand
 * different user actions (change permission vs. plug in hardware). Collapsing
 * them into "mic-error" would produce a message that's wrong half the time.
 */
export type VoiceErrorCategory =
  | 'api-key-invalid'      // Gemini key wrong or expired
  | 'api-key-missing'      // No Gemini key configured
  | 'network-unreachable'  // No internet / WebSocket blocked
  | 'network-timeout'      // Internet slow, connection timed out
  | 'mic-denied'           // User denied microphone permission
  | 'mic-unavailable'      // No mic hardware detected
  | 'model-not-downloaded' // Ollama running but model missing
  | 'ollama-unreachable'   // Ollama not running
  | 'whisper-load-failed'  // Whisper model corrupted or missing
  | 'tts-load-failed'      // TTS model corrupted or missing
  | 'audio-context-dead'   // AudioContext permanently suspended
  | 'gemini-rate-limit'    // 429 from Gemini API
  | 'gemini-server-error'  // 5xx from Gemini
  | 'unknown';             // Catch-all — should be rare if classification is thorough

/**
 * Actions that recovery buttons can trigger. Each maps to a concrete UI
 * action in the renderer process via IPC.
 */
export type RecoveryButtonAction =
  | 'open-settings'      // Navigate to API key / config page
  | 'retry'              // Re-attempt the failed operation
  | 'open-system-prefs'  // Open OS microphone permission settings
  | 'pull-model'         // Trigger `ollama pull` for the missing model
  | 'switch-to-text';    // Abandon voice, fall back to text input

/**
 * The fully classified error — everything the UI needs to show an actionable
 * error message with a recovery path.
 *
 * DESIGN NOTE: `userMessage` is written for a non-technical user. It never
 * contains stack traces, error codes, or internal component names.
 * `technicalDetail` is the power-user expansion — it includes the raw error
 * message, relevant codes, and debugging hints.
 */
export interface ClassifiedError {
  /** Which of the 14 categories this error falls into. */
  category: VoiceErrorCategory;

  /** Brief, friendly message for the user. No jargon, no blame. */
  userMessage: string;

  /** What the user can do to fix it — one clear sentence. */
  recoveryAction: string;

  /** Optional button the UI can render for one-click recovery. */
  recoveryButton?: {
    label: string;
    action: RecoveryButtonAction;
  };

  /** Expandable technical details for power users / debugging. */
  technicalDetail: string;

  /**
   * Will this error likely fix itself? Drives auto-retry behavior.
   * true = auto-retry is reasonable (network blip, rate limit cooldown).
   * false = requires user action (bad key, denied mic, missing model).
   */
  isTransient: boolean;
}

/**
 * Context provided by the caller to help classify ambiguous errors.
 * Not all fields are always available — the classifier degrades gracefully
 * when context is missing.
 */
export interface ClassificationContext {
  /** Current voice state when the error occurred. */
  voiceState?: VoiceState;

  /**
   * Which voice path was active. We define this locally since the codebase
   * doesn't have a VoicePath type yet — the state machine uses states like
   * CLOUD_ACTIVE and LOCAL_ACTIVE to represent the active path.
   */
  voicePath?: 'cloud' | 'local';

  /** Whether a Gemini API key is configured (non-empty). */
  hasGeminiKey?: boolean;

  /** Whether Ollama was reachable at last health check. */
  ollamaHealthy?: boolean;

  /** WebSocket close code, if the error came from a WebSocket close event. */
  wsCloseCode?: number;

  /** HTTP status code, if the error came from an HTTP response. */
  httpStatus?: number;
}

// ── Classification Logic ──────────────────────────────────────────────────

/**
 * Classify a raw error into an actionable, user-facing ClassifiedError.
 *
 * Classification strategy (ordered by specificity):
 * 1. WebSocket close codes (most specific — directly map to categories)
 * 2. DOMException names (getUserMedia errors have distinct .name values)
 * 3. HTTP status codes (rate limit, server error)
 * 4. Error message pattern matching (Ollama, Whisper, TTS patterns)
 * 5. Context-based inference (hasGeminiKey, ollamaHealthy, voiceState)
 * 6. Fallback to 'unknown'
 *
 * SOCRATIC NOTE: Why this order? More specific signals (close codes, exception
 * names) are less likely to produce false positives than message-string matching.
 * We check them first so that a clear signal isn't overridden by an ambiguous one.
 */
export function classifyVoiceError(
  error: unknown,
  context: ClassificationContext = {},
): ClassifiedError {
  const err = normalizeError(error);
  const msg = err.message.toLowerCase();

  // ── 1. WebSocket close codes ──────────────────────────────────────────
  // WebSocket close codes are the most reliable signal for Gemini connection
  // failures because they come directly from the protocol layer.
  if (context.wsCloseCode !== undefined) {
    const wsResult = classifyWebSocketClose(context.wsCloseCode, err.message, context);
    if (wsResult) return wsResult;
  }

  // ── 2. DOMException names (getUserMedia) ──────────────────────────────
  // getUserMedia errors have standardized .name values per the W3C spec.
  // These are the most reliable way to distinguish "denied" from "no device."
  if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
    return makeMicDenied(err.message);
  }
  if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
    return makeMicUnavailable(err.message);
  }
  if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
    // Mic exists but OS won't let us read it (another app has exclusive lock, driver crash)
    return makeMicUnavailable(err.message);
  }
  if (err.name === 'OverconstrainedError') {
    return makeMicUnavailable(err.message);
  }

  // ── 3. HTTP status codes ──────────────────────────────────────────────
  if (context.httpStatus !== undefined) {
    const httpResult = classifyHttpStatus(context.httpStatus, err.message, context);
    if (httpResult) return httpResult;
  }

  // ── 4. Error message pattern matching ─────────────────────────────────
  // SOCRATIC NOTE on ordering: We check specific component failures first
  // (Ollama, Whisper, TTS) before generic network patterns, because a message
  // like "ECONNREFUSED" could mean Ollama is down OR the internet is out.
  // Checking context.voicePath helps disambiguate.

  // Ollama-specific patterns
  if (msg.includes('ollama') || (context.voicePath === 'local' && isConnectionRefused(msg))) {
    return classifyOllamaError(msg, err.message, context);
  }

  // Whisper-specific patterns
  if (msg.includes('whisper') || msg.includes('transcription') || msg.includes('stt')) {
    return makeWhisperLoadFailed(err.message);
  }

  // TTS-specific patterns
  if (msg.includes('kokoro') || msg.includes('piper') || msg.includes('tts')) {
    return makeTtsLoadFailed(err.message);
  }

  // AudioContext patterns
  if (msg.includes('audiocontext') || msg.includes('audio context') ||
      msg.includes('the audiocontext was not allowed') ||
      msg.includes('failed to construct') && msg.includes('audio')) {
    return makeAudioContextDead(err.message);
  }

  // API key patterns (generic — catches errors not caught by WebSocket/HTTP paths)
  if (msg.includes('api key') || msg.includes('api_key') || msg.includes('apikey') ||
      msg.includes('invalid key') || msg.includes('authentication failed')) {
    if (context.hasGeminiKey === false) {
      return makeApiKeyMissing(err.message);
    }
    return makeApiKeyInvalid(err.message);
  }

  // Mic patterns (generic — catches errors not caught by DOMException name check)
  if (msg.includes('microphone') || msg.includes('getusermedia') ||
      msg.includes('permission denied') && msg.includes('audio')) {
    if (msg.includes('denied') || msg.includes('permission')) {
      return makeMicDenied(err.message);
    }
    return makeMicUnavailable(err.message);
  }

  // Network patterns (last among patterns — most generic)
  if (isNetworkError(msg)) {
    if (msg.includes('timeout') || msg.includes('timed out') || msg.includes('etimedout')) {
      return makeNetworkTimeout(err.message);
    }
    return makeNetworkUnreachable(err.message);
  }

  // ── 5. Context-based inference ────────────────────────────────────────
  // When the error message is ambiguous, use context clues.
  if (context.hasGeminiKey === false && context.voicePath === 'cloud') {
    return makeApiKeyMissing(err.message);
  }
  if (context.ollamaHealthy === false && context.voicePath === 'local') {
    return makeOllamaUnreachable(err.message);
  }

  // ── 6. Fallback ───────────────────────────────────────────────────────
  // SOCRATIC NOTE: The 'unknown' category should be rare. If it's common in
  // production, it means our classification is missing patterns. Track these
  // in telemetry to improve classification over time.
  return makeUnknown(err.message, context);
}

// ── WebSocket Close Code Classification ───────────────────────────────────

/**
 * Map WebSocket close codes to error categories.
 *
 * SOCRATIC NOTE: Why map close codes explicitly instead of treating all non-1000
 * codes as "network error"? Because the recovery action differs dramatically:
 * - 1008 (Policy Violation) with an API key = bad key → open settings
 * - 1006 (Abnormal Closure) = network drop → auto-retry
 * - 1013 (Try Again Later) = server overloaded → wait and retry
 * Collapsing these into "connection failed" would give wrong guidance.
 */
function classifyWebSocketClose(
  code: number,
  rawMessage: string,
  context: ClassificationContext,
): ClassifiedError | null {
  switch (code) {
    // 1000: Normal closure — not an error
    case 1000:
      return null;

    // 1001: Going away — server shutting down or page navigating
    case 1001:
      return makeGeminiServerError(
        rawMessage,
        `WebSocket closed with code 1001 (Going Away). The Gemini server closed the connection, possibly due to maintenance.`,
      );

    // 1006: Abnormal closure — no close frame received (network drop)
    case 1006:
      return makeNetworkUnreachable(
        rawMessage,
        `WebSocket closed abnormally (code 1006). This usually means the network connection was lost.`,
      );

    // 1008: Policy violation — Gemini uses this for invalid/expired API keys
    case 1008:
      if (context.hasGeminiKey === false) {
        return makeApiKeyMissing(rawMessage);
      }
      return makeApiKeyInvalid(
        rawMessage,
        `WebSocket closed with code 1008 (Policy Violation). Gemini rejected the API key.`,
      );

    // 1011: Unexpected condition — server-side error
    case 1011:
      return makeGeminiServerError(
        rawMessage,
        `WebSocket closed with code 1011 (Unexpected Condition). Gemini encountered an internal error.`,
      );

    // 1013: Try again later — server overloaded
    case 1013:
      return makeGeminiRateLimit(
        rawMessage,
        `WebSocket closed with code 1013 (Try Again Later). Gemini is temporarily overloaded.`,
      );

    // 1014/1015: Other protocol-level issues
    case 1014:
    case 1015:
      return makeNetworkUnreachable(
        rawMessage,
        `WebSocket closed with code ${code}. This may indicate a TLS or proxy issue.`,
      );

    default:
      // Codes 4000-4999 are application-specific (Gemini custom codes)
      if (code >= 4000 && code < 5000) {
        // Gemini uses 4xxx codes for various rejection reasons
        if (rawMessage.toLowerCase().includes('key') ||
            rawMessage.toLowerCase().includes('auth')) {
          return makeApiKeyInvalid(rawMessage, `Gemini custom close code ${code}: ${rawMessage}`);
        }
        return makeGeminiServerError(rawMessage, `Gemini custom close code ${code}: ${rawMessage}`);
      }
      return null; // Let other classification stages handle it
  }
}

// ── HTTP Status Code Classification ───────────────────────────────────────

function classifyHttpStatus(
  status: number,
  rawMessage: string,
  context: ClassificationContext,
): ClassifiedError | null {
  if (status === 401 || status === 403) {
    if (context.hasGeminiKey === false) {
      return makeApiKeyMissing(rawMessage);
    }
    return makeApiKeyInvalid(
      rawMessage,
      `HTTP ${status}: The API key was rejected by the server.`,
    );
  }

  if (status === 429) {
    return makeGeminiRateLimit(
      rawMessage,
      `HTTP 429: Rate limit exceeded. The server is asking us to slow down.`,
    );
  }

  if (status >= 500 && status < 600) {
    return makeGeminiServerError(
      rawMessage,
      `HTTP ${status}: The server encountered an internal error.`,
    );
  }

  return null;
}

// ── Ollama Error Classification ───────────────────────────────────────────

/**
 * Classify Ollama-specific errors. Distinguishes between "Ollama isn't running"
 * and "Ollama is running but the model is missing."
 *
 * SOCRATIC NOTE: This distinction matters because the recovery actions are
 * completely different: "start Ollama" vs. "download the model." A user who
 * sees "Ollama error" without this distinction will try the wrong fix.
 */
function classifyOllamaError(
  lowerMessage: string,
  rawMessage: string,
  context: ClassificationContext,
): ClassifiedError {
  // Model not found — Ollama is running but the requested model isn't pulled
  if (lowerMessage.includes('model') && (
    lowerMessage.includes('not found') ||
    lowerMessage.includes('does not exist') ||
    lowerMessage.includes('pull')
  )) {
    return makeModelNotDownloaded(rawMessage);
  }

  // Connection refused — Ollama process not running
  if (isConnectionRefused(lowerMessage) || lowerMessage.includes('not running')) {
    return makeOllamaUnreachable(rawMessage);
  }

  // Context says Ollama isn't healthy
  if (context.ollamaHealthy === false) {
    return makeOllamaUnreachable(rawMessage);
  }

  // Default for Ollama errors — assume unreachable (safer: user will check if running)
  return makeOllamaUnreachable(rawMessage);
}

// ── Helper: Error Message Pattern Checks ──────────────────────────────────

function isConnectionRefused(msg: string): boolean {
  return msg.includes('econnrefused') || msg.includes('connection refused');
}

function isNetworkError(msg: string): boolean {
  return msg.includes('econnrefused') ||
    msg.includes('enotfound') ||
    msg.includes('econnreset') ||
    msg.includes('epipe') ||
    msg.includes('etimedout') ||
    msg.includes('timeout') ||
    msg.includes('timed out') ||
    msg.includes('network') ||
    msg.includes('fetch failed') ||
    msg.includes('socket hang up') ||
    msg.includes('dns') ||
    msg.includes('unreachable');
}

// ── Helper: Normalize unknown → Error ─────────────────────────────────────

function normalizeError(error: unknown): Error {
  if (error instanceof Error) return error;
  if (typeof error === 'string') return new Error(error);
  if (typeof error === 'object' && error !== null) {
    const obj = error as Record<string, unknown>;
    const msg = (obj['message'] as string) ?? (obj['error'] as string) ?? JSON.stringify(error);
    const err = new Error(String(msg));
    if (typeof obj['name'] === 'string') err.name = obj['name'];
    return err;
  }
  return new Error(String(error));
}

// ── Factory Functions: One per Category ───────────────────────────────────
//
// Each factory produces a ClassifiedError with a carefully worded user message,
// a specific recovery action, and optional recovery button.
//
// DESIGN NOTE: These are factored out so that the classification logic above
// only decides *which* category — the message content is centralized here.
// This makes it easy to update wording without touching classification logic.

function makeApiKeyInvalid(rawMessage: string, detail?: string): ClassifiedError {
  return {
    category: 'api-key-invalid',
    userMessage: 'Your Gemini API key appears to be invalid or expired.',
    recoveryAction: 'Check your API key in Settings and update it if needed.',
    recoveryButton: { label: 'Open Settings', action: 'open-settings' },
    technicalDetail: detail ?? `API key validation failed: ${rawMessage}`,
    isTransient: false,
  };
}

function makeApiKeyMissing(rawMessage: string): ClassifiedError {
  return {
    category: 'api-key-missing',
    userMessage: 'No Gemini API key is configured.',
    recoveryAction: 'Add your Gemini API key in Settings to enable cloud voice.',
    recoveryButton: { label: 'Open Settings', action: 'open-settings' },
    technicalDetail: `Attempted cloud voice without a configured API key. Raw: ${rawMessage}`,
    isTransient: false,
  };
}

function makeNetworkUnreachable(rawMessage: string, detail?: string): ClassifiedError {
  return {
    category: 'network-unreachable',
    userMessage: 'Unable to reach the voice server. Check your internet connection.',
    recoveryAction: 'Make sure you are connected to the internet, then try again.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: detail ?? `Network error: ${rawMessage}`,
    isTransient: true,
  };
}

function makeNetworkTimeout(rawMessage: string): ClassifiedError {
  return {
    category: 'network-timeout',
    userMessage: 'The connection timed out. Your internet may be slow or unstable.',
    recoveryAction: 'Wait a moment and try again. If this keeps happening, check your connection.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: `Connection timed out: ${rawMessage}`,
    isTransient: true,
  };
}

function makeMicDenied(rawMessage: string): ClassifiedError {
  return {
    category: 'mic-denied',
    userMessage: 'Microphone access was denied.',
    recoveryAction: 'Allow microphone access in your system settings, then try again.',
    recoveryButton: { label: 'Open System Settings', action: 'open-system-prefs' },
    technicalDetail: `getUserMedia rejected (NotAllowedError): ${rawMessage}`,
    isTransient: false,
  };
}

function makeMicUnavailable(rawMessage: string): ClassifiedError {
  return {
    category: 'mic-unavailable',
    userMessage: 'No microphone was detected.',
    recoveryAction: 'Connect a microphone and try again.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: `No audio input device found or device not readable: ${rawMessage}`,
    isTransient: false,
  };
}

function makeModelNotDownloaded(rawMessage: string): ClassifiedError {
  return {
    category: 'model-not-downloaded',
    userMessage: "The language model hasn't been downloaded yet.",
    recoveryAction: 'Download the required model to enable local voice.',
    recoveryButton: { label: 'Download Model', action: 'pull-model' },
    technicalDetail: `Ollama model not found: ${rawMessage}`,
    isTransient: false,
  };
}

function makeOllamaUnreachable(rawMessage: string): ClassifiedError {
  return {
    category: 'ollama-unreachable',
    userMessage: 'The local AI engine (Ollama) is not running.',
    recoveryAction: 'Start Ollama, then try again. If the problem persists, check that Ollama is installed.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: `Ollama connection failed: ${rawMessage}`,
    isTransient: false,
  };
}

function makeWhisperLoadFailed(rawMessage: string): ClassifiedError {
  return {
    category: 'whisper-load-failed',
    userMessage: 'The speech recognition engine failed to start.',
    recoveryAction: 'Try restarting the app. If the problem persists, the Whisper model may need to be reinstalled.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: `Whisper initialization failed: ${rawMessage}`,
    isTransient: false,
  };
}

function makeTtsLoadFailed(rawMessage: string): ClassifiedError {
  return {
    category: 'tts-load-failed',
    userMessage: 'The text-to-speech engine failed to start.',
    recoveryAction: 'Try restarting the app. Voice will work without speech output using text display.',
    recoveryButton: { label: 'Switch to Text', action: 'switch-to-text' },
    technicalDetail: `TTS engine initialization failed: ${rawMessage}`,
    isTransient: false,
  };
}

function makeAudioContextDead(rawMessage: string): ClassifiedError {
  return {
    category: 'audio-context-dead',
    userMessage: 'Audio playback has stopped working.',
    recoveryAction: 'Try clicking anywhere in the app to resume audio, or restart the app.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: `AudioContext suspended or closed: ${rawMessage}. Browsers require user interaction to resume AudioContext after suspension.`,
    // SOCRATIC NOTE: AudioContext suspension is a grey area between transient and
    // persistent. It *can* be fixed by user interaction (click to resume), so it's
    // "transient" in the sense that auto-recovery (calling .resume()) may succeed.
    // But if .resume() fails, it becomes persistent. We mark it transient to allow
    // one auto-recovery attempt before surfacing to user.
    isTransient: true,
  };
}

function makeGeminiRateLimit(rawMessage: string, detail?: string): ClassifiedError {
  return {
    category: 'gemini-rate-limit',
    userMessage: 'The voice service is temporarily busy. Please wait a moment.',
    recoveryAction: 'Wait 30 seconds and try again. Rate limits reset automatically.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: detail ?? `Rate limited by Gemini: ${rawMessage}`,
    // Rate limits are the canonical example of transient errors.
    isTransient: true,
  };
}

function makeGeminiServerError(rawMessage: string, detail?: string): ClassifiedError {
  return {
    category: 'gemini-server-error',
    userMessage: 'The voice service encountered a temporary problem.',
    recoveryAction: 'This usually resolves on its own. Try again in a moment.',
    recoveryButton: { label: 'Retry', action: 'retry' },
    technicalDetail: detail ?? `Gemini server error: ${rawMessage}`,
    isTransient: true,
  };
}

function makeUnknown(rawMessage: string, context: ClassificationContext): ClassifiedError {
  // SOCRATIC NOTE: The unknown category is a design smell detector. If many
  // errors land here, our classification is missing patterns. The technical
  // detail includes full context to help developers add new patterns.
  const contextSummary = [
    context.voiceState ? `state=${context.voiceState}` : null,
    context.voicePath ? `path=${context.voicePath}` : null,
    context.hasGeminiKey !== undefined ? `hasKey=${context.hasGeminiKey}` : null,
    context.ollamaHealthy !== undefined ? `ollamaOk=${context.ollamaHealthy}` : null,
    context.wsCloseCode !== undefined ? `wsClose=${context.wsCloseCode}` : null,
    context.httpStatus !== undefined ? `http=${context.httpStatus}` : null,
  ].filter(Boolean).join(', ');

  return {
    category: 'unknown',
    userMessage: 'Voice encountered an unexpected problem.',
    recoveryAction: 'Try again. If the problem persists, switch to text input.',
    recoveryButton: { label: 'Switch to Text', action: 'switch-to-text' },
    technicalDetail: `Unclassified voice error: ${rawMessage}. Context: [${contextSummary}]. ` +
      `If you see this often, please report it — it means our error classification needs updating.`,
    // SOCRATIC NOTE: Unknown errors are marked transient (optimistic). Most
    // unclassified errors turn out to be transient. If they're actually persistent,
    // the escalation ladder (VoiceHealthMonitor) will surface them after repeated
    // failures — which is the correct behavior for an unknown error.
    isTransient: true,
  };
}
