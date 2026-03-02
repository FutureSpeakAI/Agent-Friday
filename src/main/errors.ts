/**
 * errors.ts — Standardized Error Taxonomy for Agent Friday.
 *
 * Every error in the system falls into one of four categories:
 *   1. Transient  — network blip, temporary service unavailability (auto-retry)
 *   2. Persistent — invalid API key, misconfiguration (user action required)
 *   3. Recoverable — retry with backoff, fallback to cached data
 *   4. Fatal      — corrupted integrity data, unrecoverable state (safe mode)
 *
 * cLaw Safety Rule: Safety-critical errors MUST fail CLOSED, not open.
 *   - HMAC failure → Safe Mode (never skip)
 *   - Trust engine error → most restrictive tier (never most permissive)
 */

// ── Error Categories ─────────────────────────────────────────────────

export type ErrorCategory = 'transient' | 'persistent' | 'recoverable' | 'fatal';

export type ErrorSource =
  | 'gemini'
  | 'claude'
  | 'openrouter'
  | 'mcp'
  | 'trust-engine'
  | 'integrity'
  | 'memory'
  | 'oauth'
  | 'network'
  | 'filesystem'
  | 'soc'
  | 'scheduler'
  | 'git-scanner'
  | 'git-review'
  | 'workflow-executor'
  | 'inbox'
  | 'outbound'
  | 'intelligence-router'
  | 'agent-network'
  | 'ecosystem'
  | 'persistence'
  | 'container'
  | 'delegation'
  | 'unknown';

// ── Base Error Class ─────────────────────────────────────────────────

export class AgentFridayError extends Error {
  readonly category: ErrorCategory;
  readonly source: ErrorSource;
  readonly retryable: boolean;
  readonly userFacing: boolean;
  readonly userMessage: string;
  readonly timestamp: number;

  constructor(opts: {
    message: string;
    category: ErrorCategory;
    source: ErrorSource;
    retryable?: boolean;
    userFacing?: boolean;
    userMessage?: string;
    cause?: unknown;
  }) {
    super(opts.message);
    this.name = 'AgentFridayError';
    this.category = opts.category;
    this.source = opts.source;
    this.retryable = opts.retryable ?? opts.category === 'transient';
    this.userFacing = opts.userFacing ?? opts.category === 'persistent';
    this.userMessage = opts.userMessage ?? this.defaultUserMessage(opts.category, opts.source);
    this.timestamp = Date.now();
    if (opts.cause) {
      this.cause = opts.cause;
    }
  }

  private defaultUserMessage(category: ErrorCategory, source: ErrorSource): string {
    switch (category) {
      case 'transient':
        return `Temporary issue connecting to ${source}. Retrying automatically...`;
      case 'persistent':
        return `Configuration issue with ${source}. Please check your settings.`;
      case 'recoverable':
        return `${source} encountered an issue. Attempting recovery...`;
      case 'fatal':
        return `Critical system error in ${source}. Entering safe mode for protection.`;
    }
  }

  /** Serialize for IPC transport or logging. */
  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      message: this.message,
      category: this.category,
      source: this.source,
      retryable: this.retryable,
      userFacing: this.userFacing,
      userMessage: this.userMessage,
      timestamp: this.timestamp,
      stack: this.stack,
    };
  }
}

// ── Specialized Error Types ──────────────────────────────────────────

/** Network/service temporarily unavailable — auto-retry with backoff. */
export class TransientError extends AgentFridayError {
  constructor(source: ErrorSource, message: string, cause?: unknown) {
    super({ message, category: 'transient', source, retryable: true, cause });
    this.name = 'TransientError';
  }
}

/** Invalid config, bad API key, expired token — user must fix. */
export class PersistentError extends AgentFridayError {
  constructor(source: ErrorSource, message: string, opts?: { userMessage?: string; cause?: unknown }) {
    super({
      message,
      category: 'persistent',
      source,
      retryable: false,
      userFacing: true,
      userMessage: opts?.userMessage,
      cause: opts?.cause,
    });
    this.name = 'PersistentError';
  }
}

/** Rate limited, partial failure — retry or use fallback. */
export class RecoverableError extends AgentFridayError {
  readonly retryAfterMs: number;

  constructor(source: ErrorSource, message: string, opts?: { retryAfterMs?: number; cause?: unknown }) {
    super({ message, category: 'recoverable', source, retryable: true, cause: opts?.cause });
    this.name = 'RecoverableError';
    this.retryAfterMs = opts?.retryAfterMs ?? 5000;
  }
}

/** Integrity violation, corrupted data — fail CLOSED, enter safe mode. */
export class FatalIntegrityError extends AgentFridayError {
  constructor(source: ErrorSource, message: string, cause?: unknown) {
    super({
      message,
      category: 'fatal',
      source,
      retryable: false,
      userFacing: true,
      userMessage: 'A critical integrity violation was detected. The system is entering safe mode to protect you.',
      cause,
    });
    this.name = 'FatalIntegrityError';
  }
}

// ── Error Classification Helpers ─────────────────────────────────────

/** Classify a raw error into an AgentFridayError. */
export function classifyError(source: ErrorSource, err: unknown): AgentFridayError {
  if (err instanceof AgentFridayError) return err;

  const message = err instanceof Error ? err.message : String(err);
  const lowerMsg = message.toLowerCase();

  // API key / auth errors → persistent
  if (lowerMsg.includes('api key') || lowerMsg.includes('invalid key') ||
      lowerMsg.includes('unauthorized') || lowerMsg.includes('403') ||
      lowerMsg.includes('authentication')) {
    return new PersistentError(source, message, {
      userMessage: `Invalid or expired API key for ${source}. Please update it in Settings.`,
      cause: err,
    });
  }

  // Rate limiting → recoverable
  if (lowerMsg.includes('rate limit') || lowerMsg.includes('429') ||
      lowerMsg.includes('too many requests') || lowerMsg.includes('quota')) {
    const retryMatch = message.match(/retry.after[:\s]*(\d+)/i);
    const retryAfterMs = retryMatch ? parseInt(retryMatch[1]) * 1000 : 30000;
    return new RecoverableError(source, message, { retryAfterMs, cause: err });
  }

  // Network errors → transient
  if (lowerMsg.includes('econnrefused') || lowerMsg.includes('enotfound') ||
      lowerMsg.includes('network') || lowerMsg.includes('timeout') ||
      lowerMsg.includes('econnreset') || lowerMsg.includes('fetch failed') ||
      lowerMsg.includes('socket hang up')) {
    return new TransientError(source, message, err);
  }

  // Default: transient (optimistic — most errors are temporary)
  return new TransientError(source, message, err);
}

// ── Retry Utility ────────────────────────────────────────────────────

export interface RetryOptions {
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
  /** Called before each retry with attempt number and delay. */
  onRetry?: (attempt: number, delayMs: number, error: AgentFridayError) => void;
}

const DEFAULT_RETRY: RetryOptions = {
  maxAttempts: 3,
  baseDelayMs: 1000,
  maxDelayMs: 30000,
};

/**
 * Execute an async function with exponential backoff retry.
 * Only retries transient and recoverable errors.
 * Persistent and fatal errors are thrown immediately.
 */
export async function withRetry<T>(
  source: ErrorSource,
  fn: () => Promise<T>,
  opts?: Partial<RetryOptions>,
): Promise<T> {
  const config = { ...DEFAULT_RETRY, ...opts };
  let lastError: AgentFridayError | null = null;

  for (let attempt = 1; attempt <= config.maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (raw) {
      lastError = classifyError(source, raw);

      // Non-retryable → throw immediately
      if (!lastError.retryable) throw lastError;

      // Last attempt → throw
      if (attempt >= config.maxAttempts) throw lastError;

      // Calculate delay with exponential backoff + jitter
      const exponentialDelay = config.baseDelayMs * Math.pow(2, attempt - 1);
      const jitter = Math.random() * config.baseDelayMs * 0.5;
      const delayMs = Math.min(exponentialDelay + jitter, config.maxDelayMs);

      // Use retryAfterMs hint if available
      const effectiveDelay = lastError instanceof RecoverableError
        ? Math.max(delayMs, lastError.retryAfterMs)
        : delayMs;

      config.onRetry?.(attempt, effectiveDelay, lastError);

      await new Promise((resolve) => setTimeout(resolve, effectiveDelay));
    }
  }

  // Should never reach here, but TypeScript needs it
  throw lastError ?? new TransientError(source, 'Retry exhausted');
}

// ── Fail-Closed Helpers (cLaw Safety Gate) ───────────────────────────

/**
 * Wrap a trust resolution call so that ANY error defaults to 'public'
 * (the most restrictive tier). cLaw requirement: fail CLOSED, not open.
 */
export function failClosedTrust<T>(
  fn: () => T,
  fallback: T,
  context: string,
): T {
  try {
    return fn();
  } catch (err) {
    // Crypto Sprint 17: Sanitize error output.
    console.error(`[cLaw/FailClosed] Trust resolution failed (${context}), using most restrictive tier:`, err instanceof Error ? err.message : 'Unknown error');
    return fallback;
  }
}

/**
 * Wrap an integrity check so that ANY error triggers safe mode.
 * cLaw requirement: HMAC failure → safe mode (never skip).
 */
export function failClosedIntegrity(
  fn: () => boolean,
  context: string,
): boolean {
  try {
    return fn();
  } catch (err) {
    console.error(`[cLaw/FailClosed] Integrity check failed (${context}), assuming violation:`, err instanceof Error ? err.message : 'Unknown error');
    return false; // false = integrity NOT intact → triggers safe mode
  }
}
