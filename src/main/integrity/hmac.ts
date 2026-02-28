/**
 * HMAC Engine — Cryptographic signing and verification for integrity protection.
 *
 * Uses HMAC-SHA256 with a key protected by Electron's safeStorage API,
 * which encrypts using the OS credential store:
 *   - Windows: DPAPI (Data Protection API)
 *   - macOS: Keychain
 *   - Linux: libsecret / kwallet
 *
 * The signing key is generated on first run, encrypted by the OS, and stored
 * in a separate file from the data it signs. An attacker would need both the
 * encrypted key file AND access to the OS credential store to forge signatures.
 */

import crypto from 'crypto';
import fs from 'fs/promises';
import path from 'path';
import { app, safeStorage, dialog } from 'electron';

// ── Constants ─────────────────────────────────────────────────────────

const KEY_FILE_NAME = '.integrity-key';
const KEY_LENGTH = 32; // 256-bit HMAC key
const ALGORITHM = 'sha256';

// ── State ─────────────────────────────────────────────────────────────

let signingKey: Buffer | null = null;
let initialized = false;
let safeStorageAvailable = false;

// ── Initialization ────────────────────────────────────────────────────

/**
 * Initialize the HMAC engine: load or generate the signing key.
 * Must be called after app.whenReady() since safeStorage requires it.
 */
export async function initializeHmac(): Promise<void> {
  if (initialized) return;

  const keyPath = path.join(app.getPath('userData'), KEY_FILE_NAME);

  // Check if safeStorage is available (DPAPI on Windows, Keychain on macOS)
  const canEncrypt = safeStorage.isEncryptionAvailable();
  safeStorageAvailable = canEncrypt;

  // cLaw Security Fix (HIGH-001): Loud warning when OS credential store is unavailable.
  // Without safeStorage, HMAC keys are stored in plaintext — tamper detection still works
  // (attacker must find the key file) but the security bar is much lower.
  if (!canEncrypt) {
    const msg = '[Integrity/HMAC] WARNING: OS credential store (safeStorage) is NOT available. '
      + 'HMAC signing keys will be stored unencrypted. Integrity tamper detection will still '
      + 'function, but an attacker with filesystem access could forge signatures. '
      + 'On Linux, install libsecret or kwallet. On Windows/macOS this should not happen.';
    console.error(msg);
    try {
      dialog.showMessageBoxSync({
        type: 'warning',
        title: 'Agent Friday — Security Warning',
        message: 'OS Credential Store Unavailable',
        detail: 'The integrity system cannot encrypt its signing keys because the OS credential store '
          + 'is not available. The agent will still function, but tamper-detection is weakened.\n\n'
          + 'On Linux, install libsecret (GNOME) or kwallet (KDE) to resolve this.',
        buttons: ['I Understand'],
      });
    } catch {
      // Dialog may fail in headless/CI environments — that's OK, console warning suffices
    }
  }

  try {
    // Try to load existing key
    const encryptedKey = await fs.readFile(keyPath);

    if (canEncrypt) {
      // Decrypt the stored key using OS credential store
      const decrypted = safeStorage.decryptString(encryptedKey);
      signingKey = Buffer.from(decrypted, 'hex');
    } else {
      // Fallback: key stored as-is (less secure, but still provides tamper detection)
      signingKey = encryptedKey;
    }

    console.log('[Integrity/HMAC] Signing key loaded');
  } catch {
    // First run — generate a new key
    signingKey = crypto.randomBytes(KEY_LENGTH);

    if (canEncrypt) {
      // Encrypt with OS credential store before writing
      const encrypted = safeStorage.encryptString(signingKey.toString('hex'));
      await fs.writeFile(keyPath, encrypted);
    } else {
      // Fallback: store raw key
      await fs.writeFile(keyPath, signingKey);
    }

    console.log('[Integrity/HMAC] New signing key generated and stored');
  }

  initialized = true;
}

// ── Signing ───────────────────────────────────────────────────────────

/**
 * Compute HMAC-SHA256 signature for a string payload.
 */
export function sign(data: string): string {
  if (!signingKey) {
    throw new Error('[Integrity/HMAC] Not initialized — call initializeHmac() first');
  }

  const hmac = crypto.createHmac(ALGORITHM, signingKey);
  hmac.update(data, 'utf8');
  return hmac.digest('hex');
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

  const hmac = crypto.createHmac(ALGORITHM, signingKey);
  hmac.update(data);
  return hmac.digest();
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
 * Sign a JSON-serializable object by converting to canonical JSON string.
 */
export function signObject(obj: unknown): string {
  const canonical = JSON.stringify(obj, Object.keys(obj as object).sort());
  return sign(canonical);
}

/**
 * Verify a JSON-serializable object's signature.
 */
export function verifyObject(obj: unknown, expectedSignature: string): boolean {
  const canonical = JSON.stringify(obj, Object.keys(obj as object).sort());
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

/**
 * Check if OS safeStorage is available for key encryption.
 * cLaw Security Fix (HIGH-001): Expose this so other modules can degrade gracefully.
 */
export function isSafeStorageAvailable(): boolean {
  return safeStorageAvailable;
}
