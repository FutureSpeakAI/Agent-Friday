/**
 * HMAC Engine — Cryptographic signing and verification for integrity protection.
 *
 * v2: The signing key is injected by the vault after passphrase-based derivation.
 * No Electron dependency. No safeStorage. No file I/O for key management.
 *
 * The key is derived via:
 *   Passphrase → Argon2id → masterKey → crypto_kdf(id=2, ctx="AF_HMAC_") → hmacKey
 *
 * The key lives in a SecureBuffer (guard-paged, mlocked, zeroed on destroy).
 * An attacker with OS admin access cannot extract it from DPAPI/Keychain
 * because those mechanisms are no longer used.
 */

import crypto from 'crypto';
import fs from 'fs/promises';

import { SecureBuffer } from '../crypto/secure-buffer';

// ── Constants ─────────────────────────────────────────────────────────

const ALGORITHM = 'sha256';

// ── State ─────────────────────────────────────────────────────────────

let signingKey: SecureBuffer | null = null;
let initialized = false;

// ── Initialization ────────────────────────────────────────────────────

/**
 * Initialize the HMAC engine with a pre-derived signing key.
 *
 * Called by the boot sequence AFTER the vault is unlocked.
 * The key is derived from the user's passphrase — no I/O, no Electron APIs.
 *
 * @param key - The HMAC signing key (SecureBuffer from vault KDF)
 */
export function initializeHmac(key: SecureBuffer): void {
  if (initialized) return;

  signingKey = key;
  initialized = true;

  console.log('[Integrity/HMAC] Signing key injected (vault-derived, SecureBuffer)');
}

/**
 * Clean up HMAC state on shutdown.
 * The SecureBuffer is owned by the vault — we just clear our reference.
 */
export function destroyHmac(): void {
  signingKey = null;
  initialized = false;
  console.log('[Integrity/HMAC] State cleared');
}

// ── Signing ───────────────────────────────────────────────────────────

/**
 * Compute HMAC-SHA256 signature for a string payload.
 */
export function sign(data: string): string {
  if (!signingKey) {
    throw new Error('[Integrity/HMAC] Not initialized — call initializeHmac() first');
  }

  return signingKey.withAccess('readonly', (key) => {
    const hmac = crypto.createHmac(ALGORITHM, key);
    hmac.update(data, 'utf8');
    return hmac.digest('hex');
  });
}

/**
 * Compute HMAC-SHA256 signature for an arbitrary binary payload.
 * Returns raw bytes — callers can .toString('hex') if they need a string.
 *
 * Track X foundation: ledger transactions, DAG node signing, and cross-agent
 * attestations operate on binary payloads, not UTF-8 strings.
 */
export function signBytes(data: Buffer): Buffer {
  if (!signingKey) {
    throw new Error('[Integrity/HMAC] Not initialized — call initializeHmac() first');
  }

  return signingKey.withAccess('readonly', (key) => {
    const hmac = crypto.createHmac(ALGORITHM, key);
    hmac.update(data);
    return hmac.digest();
  });
}

/**
 * Verify an HMAC-SHA256 signature against a binary payload.
 * Uses timing-safe comparison to prevent timing attacks.
 */
export function verifyBytes(data: Buffer, expectedSignature: Buffer): boolean {
  if (!signingKey) {
    throw new Error('[Integrity/HMAC] Not initialized — call initializeHmac() first');
  }

  const actual = signBytes(data);
  if (actual.length !== expectedSignature.length) return false;
  return crypto.timingSafeEqual(actual, expectedSignature);
}

/**
 * Verify an HMAC-SHA256 signature against a payload.
 * Uses timing-safe comparison to prevent timing attacks.
 */
export function verify(data: string, expectedSignature: string): boolean {
  if (!signingKey) {
    throw new Error('[Integrity/HMAC] Not initialized — call initializeHmac() first');
  }

  const actual = sign(data);

  // Timing-safe comparison
  if (actual.length !== expectedSignature.length) return false;
  return crypto.timingSafeEqual(
    Buffer.from(actual, 'hex'),
    Buffer.from(expectedSignature, 'hex'),
  );
}

/**
 * Recursively sort all object keys for deterministic serialization.
 *
 * Deep-sorts ALL object keys at every nesting level to ensure
 * HMAC signatures are stable regardless of key insertion order.
 */
function deepSortKeys(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(deepSortKeys);
  if (typeof value === 'object') {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[key] = deepSortKeys((value as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return value;
}

/**
 * Sign a JSON-serializable object by converting to canonical (deep-sorted) JSON string.
 */
export function signObject(obj: unknown): string {
  const canonical = JSON.stringify(deepSortKeys(obj));
  return sign(canonical);
}

/**
 * Verify a JSON-serializable object's signature.
 */
export function verifyObject(obj: unknown, expectedSignature: string): boolean {
  const canonical = JSON.stringify(deepSortKeys(obj));
  return verify(canonical, expectedSignature);
}

/**
 * Sign a file's contents.
 */
export async function signFile(filePath: string): Promise<string> {
  try {
    const content = await fs.readFile(filePath, 'utf-8');
    return sign(content);
  } catch {
    return ''; // File doesn't exist
  }
}

/**
 * Verify a file's contents against an expected signature.
 */
export async function verifyFile(filePath: string, expectedSignature: string): Promise<boolean> {
  try {
    const content = await fs.readFile(filePath, 'utf-8');
    return verify(content, expectedSignature);
  } catch {
    return false; // File doesn't exist or can't be read
  }
}

/**
 * Check if the HMAC engine is initialized.
 */
export function isInitialized(): boolean {
  return initialized;
}
