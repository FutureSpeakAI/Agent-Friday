/**
 * Crypto Sprint 8: Shared IPC input validation utilities.
 *
 * Every ipcMain.handle() receives arbitrary data from the renderer process.
 * TypeScript type annotations provide ZERO runtime safety — `as` casts are
 * especially dangerous because they never throw, even on type mismatches.
 *
 * This module provides reusable validation functions that throw descriptive
 * errors for invalid input, ensuring the main process never blindly trusts
 * renderer data.
 */

import path from 'path';
import { app } from 'electron';

/* ── Primitive validators ─────────────────────────────────────────────── */

/**
 * Assert value is a non-empty string within a length limit.
 */
export function assertString(
  value: unknown,
  name: string,
  maxLength = 10_000,
): asserts value is string {
  if (typeof value !== 'string') {
    throw new Error(`${name} must be a string, got ${typeof value}`);
  }
  if (value.length === 0) {
    throw new Error(`${name} must not be empty`);
  }
  if (value.length > maxLength) {
    throw new Error(`${name} exceeds max length (${maxLength} chars)`);
  }
}

/**
 * Assert value is a string, but allow empty strings.
 */
export function assertOptionalString(
  value: unknown,
  name: string,
  maxLength = 10_000,
): asserts value is string {
  if (typeof value !== 'string') {
    throw new Error(`${name} must be a string, got ${typeof value}`);
  }
  if (value.length > maxLength) {
    throw new Error(`${name} exceeds max length (${maxLength} chars)`);
  }
}

/**
 * Assert value is a finite number within bounds.
 */
export function assertNumber(
  value: unknown,
  name: string,
  min = -Infinity,
  max = Infinity,
): asserts value is number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${name} must be a finite number, got ${typeof value === 'number' ? value : typeof value}`);
  }
  if (value < min || value > max) {
    throw new Error(`${name} must be between ${min} and ${max}, got ${value}`);
  }
}

/**
 * Assert value is a boolean.
 */
export function assertBoolean(
  value: unknown,
  name: string,
): asserts value is boolean {
  if (typeof value !== 'boolean') {
    throw new Error(`${name} must be a boolean, got ${typeof value}`);
  }
}

/**
 * Assert value is a non-null plain object.
 */
export function assertObject(
  value: unknown,
  name: string,
): asserts value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${name} must be a plain object`);
  }
}

/**
 * Assert value is an array with a maximum length, optionally checking element types.
 */
export function assertArray(
  value: unknown,
  name: string,
  maxLength = 10_000,
): asserts value is unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${name} must be an array, got ${typeof value}`);
  }
  if (value.length > maxLength) {
    throw new Error(`${name} exceeds max length (${maxLength} elements)`);
  }
}

/**
 * Assert value is an array of strings.
 */
export function assertStringArray(
  value: unknown,
  name: string,
  maxLength = 1_000,
  maxElementLength = 10_000,
): asserts value is string[] {
  assertArray(value, name, maxLength);
  for (let i = 0; i < value.length; i++) {
    if (typeof value[i] !== 'string') {
      throw new Error(`${name}[${i}] must be a string, got ${typeof value[i]}`);
    }
    if ((value[i] as string).length > maxElementLength) {
      throw new Error(`${name}[${i}] exceeds max element length (${maxElementLength} chars)`);
    }
  }
}

/* ── URL validators ───────────────────────────────────────────────────── */

/**
 * Allowed URL schemes for different contexts.
 */
const URL_SCHEME_ALLOWLISTS = {
  git: ['https://', 'http://'],  // git:// intentionally excluded (unencrypted)
  meeting: ['https://'],
  web: ['https://', 'http://'],
} as const;

/**
 * Assert URL uses an allowed scheme. Blocks file://, ssh://, data://, javascript:, etc.
 */
export function assertSafeUrl(
  url: unknown,
  name: string,
  context: keyof typeof URL_SCHEME_ALLOWLISTS = 'web',
  maxLength = 2_048,
): asserts url is string {
  assertString(url, name, maxLength);
  const schemes = URL_SCHEME_ALLOWLISTS[context];
  const lower = (url as string).toLowerCase();
  const allowed = schemes.some(scheme => lower.startsWith(scheme));
  if (!allowed) {
    throw new Error(
      `${name} must use one of [${schemes.join(', ')}], got: ${lower.slice(0, 40)}...`,
    );
  }
}

/* ── Path validators ──────────────────────────────────────────────────── */

/**
 * Dangerous path patterns that should never appear in user-supplied paths.
 */
const DANGEROUS_PATH_PATTERNS = [
  /\.\.[/\\]/, // directory traversal
  /^\\\\/, // UNC paths (Windows SMB — leaks NTLMv2 hashes)
  /[\r\n\0]/, // null bytes and newlines
  /[;&|`$]/, // shell metacharacters
];

/**
 * Assert a file path is safe (no traversal, no UNC, no shell chars).
 * Does NOT check confinement to a specific directory — use assertConfinedPath for that.
 */
export function assertSafePath(
  value: unknown,
  name: string,
  maxLength = 1_000,
): asserts value is string {
  assertString(value, name, maxLength);
  for (const pattern of DANGEROUS_PATH_PATTERNS) {
    if (pattern.test(value as string)) {
      throw new Error(`${name} contains a dangerous path pattern`);
    }
  }
}

/**
 * Assert a file path is confined within an allowed base directory.
 * Resolves the path and checks it starts with the base.
 */
export function assertConfinedPath(
  filePath: unknown,
  name: string,
  baseDir: string,
  maxLength = 1_000,
): string {
  assertSafePath(filePath, name, maxLength);
  const resolved = path.resolve(baseDir, filePath as string);
  // Normalize both to ensure consistent comparison
  const normalizedBase = path.resolve(baseDir) + path.sep;
  const normalizedResolved = path.resolve(resolved);
  if (!normalizedResolved.startsWith(normalizedBase) && normalizedResolved !== path.resolve(baseDir)) {
    throw new Error(`${name} must be within ${baseDir}`);
  }
  return normalizedResolved;
}

/**
 * Get the app's user data directory (for confining file operations).
 */
export function getUserDataDir(): string {
  return app.getPath('userData');
}

/* ── Passphrase validators ────────────────────────────────────────────── */

/**
 * Maximum passphrase length to prevent Argon2id memory/CPU DoS.
 * 1KB is more than sufficient for any reasonable passphrase (8+ words ≈ 60 chars).
 * An attacker sending a 1GB string would cause Argon2id to hash a 1GB input
 * with 256MB memory, potentially crashing the process.
 */
const MAX_PASSPHRASE_LENGTH = 1_024;

/**
 * Assert passphrase is a string within safe bounds.
 */
export function assertPassphrase(value: unknown, name = 'passphrase'): asserts value is string {
  assertString(value, name, MAX_PASSPHRASE_LENGTH);
}

/* ── Tool call validators ─────────────────────────────────────────────── */

/**
 * Maximum serialized size of tool arguments (100KB).
 * Prevents memory exhaustion from huge arg objects.
 */
const MAX_TOOL_ARGS_SIZE = 100_000;

/**
 * Validate tool call inputs (toolName + args).
 * Used by mcp:call-tool, desktop:call-tool, browser:call-tool, connectors:call-tool, etc.
 */
export function assertToolCallArgs(
  toolName: unknown,
  args: unknown,
  context: string,
): { validatedName: string; validatedArgs: Record<string, unknown> } {
  assertString(toolName, `${context} toolName`, 256);

  if (args === undefined || args === null) {
    return { validatedName: toolName as string, validatedArgs: {} };
  }

  assertObject(args, `${context} args`);

  // Check serialized size to prevent memory bombs
  const serialized = JSON.stringify(args);
  if (serialized.length > MAX_TOOL_ARGS_SIZE) {
    throw new Error(
      `${context} args too large (${serialized.length} bytes, max ${MAX_TOOL_ARGS_SIZE})`,
    );
  }

  return { validatedName: toolName as string, validatedArgs: args as Record<string, unknown> };
}

/* ── Message array validators ─────────────────────────────────────────── */

/**
 * Maximum number of messages in a conversation history array.
 * Prevents memory exhaustion from renderer sending huge arrays.
 */
const MAX_MESSAGES = 500;

/**
 * Validate a messages array ({ role, content }) with size limits.
 */
export function assertMessageArray(
  value: unknown,
  name: string,
  maxMessages = MAX_MESSAGES,
  maxContentLength = 100_000,
): Array<{ role: string; content: string }> {
  assertArray(value, name, maxMessages);
  const arr = value as unknown[];
  for (let i = 0; i < arr.length; i++) {
    const msg = arr[i];
    if (!msg || typeof msg !== 'object') {
      throw new Error(`${name}[${i}] must be an object`);
    }
    const m = msg as Record<string, unknown>;
    if (typeof m.role !== 'string') {
      throw new Error(`${name}[${i}].role must be a string`);
    }
    if (typeof m.content !== 'string') {
      throw new Error(`${name}[${i}].content must be a string`);
    }
    if (m.content.length > maxContentLength) {
      throw new Error(`${name}[${i}].content exceeds max length (${maxContentLength} chars)`);
    }
  }
  return arr as Array<{ role: string; content: string }>;
}
