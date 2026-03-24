/**
 * Sovereign Vault v2 — Passphrase-Only Root of Trust
 *
 * At-rest encryption for all agent state files using AES-256-GCM.
 * The entire key hierarchy derives from a user-chosen passphrase —
 * no OS credential store, no machine binding, no recovery backdoor.
 *
 * Key hierarchy:
 *   Passphrase (≥8 words, never stored)
 *     + salt (16 bytes, .vault-salt)
 *     ▼ Argon2id (opslimit=4, memlimit=256MB)
 *     masterKey (32 bytes, destroyed after ~100ms)
 *     ├─ vaultKey    (AES-256-GCM for all vault files)
 *     ├─ hmacKey     (HMAC-SHA256 integrity signing)
 *     └─ identityKey (wraps Ed25519/X25519 private keys)
 *
 * Files encrypted:
 *   - Agent identity & private keys (agent-network.json)
 *   - Memory stores (shortTerm/mediumTerm/longTerm.json)
 *   - Settings & API keys (friday-settings.json)
 *   - Trust graph (trust-graph.json)
 *   - Gateway identities (gateway/identities.json)
 *
 * Cipher format (per-file):
 *   [12-byte IV][16-byte authTag][...ciphertext...]
 *
 * HMAC stacking: The integrity/hmac.ts layer operates on PLAINTEXT
 * before encryption. On read, vault decrypts first, then HMAC verifies.
 */

import crypto from 'crypto';
import fs from 'fs/promises';
import path from 'path';
import { app } from 'electron';

import { SecureBuffer } from './crypto/secure-buffer';
import {
  generateSalt,
  readSalt,
  writeSalt,
  deriveAllKeys,
  createCanary,
  verifyCanary,
  isVaultInitialized as checkVaultInitialized,
  writeVaultMeta,
  validatePassphrase,
  secretboxEncrypt,
  secretboxDecrypt,
  ensureSodiumReady,
  type DerivedKeys,
} from './crypto/passphrase-kdf';

// ── Constants ─────────────────────────────────────────────────────────

const IV_LENGTH = 12;        // GCM standard
const TAG_LENGTH = 16;       // GCM auth tag
const ALGORITHM = 'aes-256-gcm';

// ── State ─────────────────────────────────────────────────────────────

let vaultKey: SecureBuffer | null = null;
let hmacKey: SecureBuffer | null = null;
let identityKey: SecureBuffer | null = null;
let vaultUnlocked = false;

// ── Initialization ────────────────────────────────────────────────────

/**
 * Initialize a new vault on first run.
 *
 * 1. Validates the passphrase (≥8 words)
 * 2. Generates a random salt
 * 3. Derives all keys via Argon2id + BLAKE2b KDF
 * 4. Creates canary file (for future passphrase verification)
 * 5. Writes vault metadata
 *
 * After this call, the vault is unlocked and all encryption functions work.
 *
 * @param passphrase - User-chosen sentence (≥8 words)
 * @throws if passphrase validation fails or vault already initialized
 */
export async function initializeNewVault(passphrase: string): Promise<void> {
  const t0 = Date.now();
  console.log('[Vault] First-time initialization starting...');

  // Ensure libsodium WASM is ready
  await ensureSodiumReady();

  const userDataDir = app.getPath('userData');

  // Guard: don't re-initialize
  if (await checkVaultInitialized(userDataDir)) {
    throw new Error('[Vault] Already initialized — use unlockVault() instead');
  }

  // Validate passphrase
  const validationError = validatePassphrase(passphrase);
  if (validationError) {
    throw new Error(`[Vault] ${validationError}`);
  }

  // Generate and store salt
  const salt = generateSalt();
  await writeSalt(userDataDir, salt);

  // Derive all keys (Argon2id + BLAKE2b KDF)
  const keys = deriveAllKeys(passphrase, salt);
  setKeys(keys);

  // Create canary for future passphrase verification
  await createCanary(keys.vaultKey, userDataDir);

  // Write vault metadata
  await writeVaultMeta(userDataDir);

  console.log(`[Vault] First-time initialization complete in ${Date.now() - t0}ms`);
}

/**
 * Unlock an existing vault with the user's passphrase.
 *
 * 1. Reads stored salt
 * 2. Derives keys via Argon2id + BLAKE2b KDF
 * 3. Verifies canary (wrong passphrase → false, no data exposed)
 *
 * @param passphrase - The user's passphrase sentence
 * @returns true if unlock succeeded, false if wrong passphrase
 */
export async function unlockVault(passphrase: string): Promise<boolean> {
  const t0 = Date.now();
  console.log('[Vault] Unlock starting...');

  // Ensure libsodium WASM is ready
  await ensureSodiumReady();

  const userDataDir = app.getPath('userData');

  // Read stored salt
  const salt = await readSalt(userDataDir);
  if (!salt) {
    console.error('[Vault] No salt file found — vault not initialized');
    return false;
  }

  // Derive keys
  const keys = deriveAllKeys(passphrase, salt);

  // Verify canary
  const canaryOk = await verifyCanary(keys.vaultKey, userDataDir);
  if (!canaryOk) {
    console.warn(`[Vault] Canary verification failed — wrong passphrase (${Date.now() - t0}ms)`);
    // Destroy the incorrectly-derived keys
    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
    return false;
  }

  // Success — store keys in module state
  setKeys(keys);

  console.log(`[Vault] Unlocked successfully in ${Date.now() - t0}ms`);
  return true;
}

/**
 * Borrow the identity key for a short-lived operation.
 *
 * The identity key wraps/unwraps Ed25519 and X25519 private keys.
 * It spends most of its life in NOACCESS state; this function
 * temporarily unlocks it for the callback, then re-locks.
 *
 * @param fn - Callback receiving the identityKey SecureBuffer
 * @returns The callback's return value
 */
export async function withIdentityKey<T>(fn: (key: SecureBuffer) => T | Promise<T>): Promise<T> {
  if (!identityKey || !vaultUnlocked) {
    throw new Error('[Vault] Not unlocked — cannot access identity key');
  }
  // The SecureBuffer.withAccessAsync handles unlock→callback→re-lock
  return identityKey.withAccessAsync('readonly', async (buf) => {
    // Create a temporary readonly view for the callback
    // The callback receives the whole SecureBuffer so it can use secretboxEncrypt/Decrypt
    return fn(identityKey!);
  });
}

/**
 * Get the HMAC signing key (for injection into hmac.ts).
 * Returns null if vault is not unlocked.
 */
export function getHmacKey(): SecureBuffer | null {
  return hmacKey;
}

/**
 * Destroy all key material on shutdown.
 * Must be called in the app's shutdown handler.
 */
export function destroyVault(): void {
  if (vaultKey) { vaultKey.destroy(); vaultKey = null; }
  if (hmacKey) { hmacKey.destroy(); hmacKey = null; }
  if (identityKey) { identityKey.destroy(); identityKey = null; }
  vaultUnlocked = false;
  console.log('[Vault] All key material destroyed');
}

/**
 * Erase all vault files on disk so the next launch treats it as a fresh install.
 * Destroys in-memory key material first, then removes .vault-salt, .vault-canary,
 * and .vault-meta.json from userData.  This is the nuclear "start fresh" path —
 * all agent data encrypted under the old passphrase becomes unrecoverable.
 */
export async function resetVaultFiles(): Promise<void> {
  destroyVault();

  const userDataDir = app.getPath('userData');
  const vaultFiles = ['.vault-salt', '.vault-canary', '.vault-meta.json'];

  for (const file of vaultFiles) {
    try {
      await fs.unlink(path.join(userDataDir, file));
    } catch {
      // File may not exist — that's fine
    }
  }

  console.log('[Vault] All vault files removed — next launch will be fresh');
}

// ── Status Queries ────────────────────────────────────────────────────

/** Is the vault currently unlocked and ready for encryption? */
export function isVaultUnlocked(): boolean {
  return vaultUnlocked;
}

/** Was the vault initialized (first-run setup completed)? */
export async function isVaultInitialized(): Promise<boolean> {
  const userDataDir = app.getPath('userData');
  return checkVaultInitialized(userDataDir);
}

// ── AES-256-GCM Encryption (for vault files) ─────────────────────────

/**
 * Encrypt plaintext bytes with AES-256-GCM.
 * Returns: [12-byte IV][16-byte authTag][ciphertext]
 *
 * Uses the vaultKey (derived from passphrase via Argon2id + KDF).
 */
function vaultEncrypt(plaintext: Buffer): Buffer {
  if (!vaultKey || !vaultUnlocked) {
    throw new Error('[Vault] Not unlocked');
  }

  const iv = crypto.randomBytes(IV_LENGTH);

  return vaultKey.withAccess('readonly', (key) => {
    const cipher = crypto.createCipheriv(ALGORITHM, key, iv);
    const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
    const tag = cipher.getAuthTag();
    return Buffer.concat([iv, tag, encrypted]);
  });
}

/**
 * Decrypt vault-encrypted bytes. Returns null if decryption fails
 * (wrong key, tampered data, etc.)
 */
function vaultDecrypt(data: Buffer): Buffer | null {
  if (!vaultKey || !vaultUnlocked) {
    throw new Error('[Vault] Not unlocked');
  }

  if (data.length < IV_LENGTH + TAG_LENGTH) return null;

  const iv = data.subarray(0, IV_LENGTH);
  const tag = data.subarray(IV_LENGTH, IV_LENGTH + TAG_LENGTH);
  const ciphertext = data.subarray(IV_LENGTH + TAG_LENGTH);

  try {
    return vaultKey.withAccess('readonly', (key) => {
      const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);
      decipher.setAuthTag(tag);
      return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
    });
  } catch {
    return null;
  }
}

// ── Public File I/O API ───────────────────────────────────────────────

/**
 * Write data to disk with vault encryption.
 *
 * Requires the vault to be unlocked. Throws if locked (Fix M6 — no plaintext
 * fallback for writes). Callers must unlock the vault first or handle the error.
 *
 * @param filePath - Absolute path to write to
 * @param content - String content to encrypt and write
 * @throws if vault is locked
 */
export async function vaultWrite(filePath: string, content: string): Promise<void> {
  if (!vaultUnlocked || !vaultKey) {
    // Fix M6: Block plaintext writes when vault is locked.
    // Writing plaintext defeats the purpose of encryption. Callers must
    // unlock the vault first or handle the error (e.g. queue the write).
    throw new Error('[Vault] Cannot write — vault is locked. Unlock first.');
  }

  const plaintext = Buffer.from(content, 'utf-8');
  const encrypted = vaultEncrypt(plaintext);
  await fs.writeFile(filePath, encrypted);
}

/**
 * Read and decrypt a vault-encrypted file.
 *
 * Handles both encrypted and plaintext files transparently:
 *   1. Read raw bytes
 *   2. If vault is unlocked, try to decrypt
 *   3. If decryption fails (file was plaintext), return as-is
 *   4. If vault is locked, return raw content as string
 *
 * This means the transition from plaintext → encrypted is seamless.
 * Existing plaintext files will be read normally, and will be encrypted
 * on next write.
 *
 * @param filePath - Absolute path to read from
 * @returns Decrypted string content
 */
export async function vaultRead(filePath: string): Promise<string> {
  const raw = await fs.readFile(filePath);

  if (!vaultUnlocked || !vaultKey) {
    // Vault not ready — return raw content (unencrypted read)
    console.warn(`[Vault] ⚠ PLAINTEXT READ — vault locked, reading unencrypted: ${path.basename(filePath)}`);
    return raw.toString('utf-8');
  }

  // Try to decrypt
  const decrypted = vaultDecrypt(raw);
  if (decrypted) {
    return decrypted.toString('utf-8');
  }

  // Decryption failed — distinguish legacy plaintext from corruption.
  // If the file looks like it was encrypted (binary header, sufficient length for IV+tag),
  // but decryption failed, the data is likely corrupted — throw instead of silently
  // returning raw bytes that callers would misinterpret as plaintext (Fix L7).
  const looksEncrypted = raw.length >= IV_LENGTH + TAG_LENGTH && !isLikelyPlaintext(raw);
  if (looksEncrypted) {
    throw new Error(`[Vault] File appears corrupted — decryption failed. Recovery may be needed: ${path.basename(filePath)}`);
  }

  // File is legacy plaintext (pre-vault era) — return as-is; it'll be encrypted on next save
  console.warn(`[Vault] ⚠ LEGACY PLAINTEXT — file not encrypted, will encrypt on next write: ${path.basename(filePath)}`);
  return raw.toString('utf-8');
}

/**
 * Convenience: read and parse a JSON file from the vault.
 */
export async function vaultReadJSON<T = unknown>(filePath: string): Promise<T> {
  const content = await vaultRead(filePath);
  return JSON.parse(content) as T;
}

// ── Binary Vault I/O (Crypto Sprint 3 — MEDIUM-003) ───────────────────

/**
 * Write binary data to disk with vault encryption.
 *
 * Unlike vaultWrite (which takes string content), this operates directly
 * on Buffers for binary data like transferred files, images, etc.
 *
 * Requires the vault to be unlocked. Throws if locked (Fix M6).
 *
 * @param filePath - Absolute path to write to
 * @param data - Binary data to encrypt and write
 * @throws if vault is locked
 */
export async function vaultWriteBinary(filePath: string, data: Buffer): Promise<void> {
  if (!vaultUnlocked || !vaultKey) {
    // Fix M6: Block plaintext writes when vault is locked.
    throw new Error('[Vault] Cannot write — vault is locked. Unlock first.');
  }

  const encrypted = vaultEncrypt(data);
  await fs.writeFile(filePath, encrypted);
}

/**
 * Read and decrypt a vault-encrypted binary file.
 *
 * Handles both encrypted and plaintext files transparently
 * (same approach as vaultRead but returns Buffer instead of string).
 *
 * @param filePath - Absolute path to read from
 * @returns Decrypted binary content
 */
export async function vaultReadBinary(filePath: string): Promise<Buffer> {
  const raw = await fs.readFile(filePath);

  if (!vaultUnlocked || !vaultKey) {
    console.warn(`[Vault] ⚠ PLAINTEXT READ — vault locked, reading unencrypted: ${path.basename(filePath)}`);
    return raw;
  }

  // Try to decrypt
  const decrypted = vaultDecrypt(raw);
  if (decrypted) {
    return decrypted;
  }

  // Fix L7: distinguish legacy plaintext from corruption (same logic as vaultRead).
  const looksEncrypted = raw.length >= IV_LENGTH + TAG_LENGTH && !isLikelyPlaintext(raw);
  if (looksEncrypted) {
    throw new Error(`[Vault] File appears corrupted — decryption failed. Recovery may be needed: ${path.basename(filePath)}`);
  }

  // Decryption failed — file is plaintext (pre-encryption era)
  console.warn(`[Vault] ⚠ LEGACY PLAINTEXT — file not encrypted, will encrypt on next write: ${path.basename(filePath)}`);
  return raw;
}

// ── Identity Key Helpers (for agent-network.ts) ───────────────────────

/**
 * Encrypt a private key (base64 string) with the identity key.
 * Returns a prefixed string: "enc:<base64 of nonce+ciphertext>"
 *
 * Used by agent-network.ts to protect Ed25519/X25519 private keys at rest.
 */
export function encryptPrivateKey(keyBase64: string): string {
  if (!identityKey || !vaultUnlocked) {
    // Graceful degradation: return plaintext if vault not ready.
    // LOG A WARNING so unprotected keys are always visible in logs.
    if (keyBase64) {
      console.warn('[Vault] ⚠ PLAINTEXT KEY — vault locked, private key NOT encrypted');
    }
    return keyBase64;
  }
  const plaintext = Buffer.from(keyBase64, 'utf-8');
  const encrypted = secretboxEncrypt(plaintext, identityKey);
  return `enc:${encrypted.toString('base64')}`;
}

/**
 * Decrypt a private key protected with encryptPrivateKey().
 * Handles both "enc:" prefix (v2) and plaintext (legacy).
 *
 * @returns The base64-encoded private key
 * @throws If key has "enc:" prefix but vault is locked
 */
export function decryptPrivateKey(stored: string): string {
  if (stored.startsWith('enc:')) {
    if (!identityKey || !vaultUnlocked) {
      throw new Error('[Vault] Cannot decrypt private key — vault locked');
    }
    const data = Buffer.from(stored.slice(4), 'base64');
    const decrypted = secretboxDecrypt(data, identityKey);
    if (!decrypted) {
      throw new Error('[Vault] Failed to decrypt private key — corrupted or wrong key');
    }
    return decrypted.toString('utf-8');
  }

  // Legacy: "safe:" prefix from v1 — cannot decrypt without safeStorage
  if (stored.startsWith('safe:')) {
    throw new Error(
      '[Vault] Cannot decrypt v1 safeStorage-protected key. ' +
      'This key was encrypted with the old DPAPI/Keychain system which has been removed. ' +
      'The agent identity must be regenerated.',
    );
  }

  // Plaintext fallback (pre-vault files)
  return stored;
}

// ── Internal Helpers ──────────────────────────────────────────────────

/**
 * Heuristic: does this buffer look like it was plaintext (UTF-8 text)?
 * Checks the first few bytes for printable ASCII / common UTF-8 patterns.
 * Used to distinguish legacy unencrypted files from corrupted encrypted files.
 */
function isLikelyPlaintext(buf: Buffer): boolean {
  if (buf.length === 0) return true;
  // Check first byte — JSON starts with '{' or '[', XML with '<', etc.
  const first = buf[0];
  // Common plaintext first bytes: printable ASCII (0x20-0x7E), BOM (0xEF), newline/tab
  if (first === 0x7B || first === 0x5B || first === 0x3C || first === 0x22) {
    return true; // { [ < " — very likely JSON/XML/text
  }
  // Check if first 32 bytes (or whole buffer if shorter) are mostly printable ASCII
  const checkLen = Math.min(buf.length, 32);
  let printable = 0;
  for (let i = 0; i < checkLen; i++) {
    const b = buf[i];
    if ((b >= 0x20 && b <= 0x7E) || b === 0x0A || b === 0x0D || b === 0x09) {
      printable++;
    }
  }
  return printable / checkLen > 0.8;
}

function setKeys(keys: DerivedKeys): void {
  // Destroy old keys if any
  if (vaultKey) vaultKey.destroy();
  if (hmacKey) hmacKey.destroy();
  if (identityKey) identityKey.destroy();

  vaultKey = keys.vaultKey;
  hmacKey = keys.hmacKey;
  identityKey = keys.identityKey;
  vaultUnlocked = true;
}
