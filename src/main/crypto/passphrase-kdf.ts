/**
 * Passphrase KDF — Derives all vault keys from a user-chosen passphrase.
 *
 * Key hierarchy:
 *   User Passphrase (≥8 words, never stored, never on disk)
 *     + salt (16 bytes, random, stored in .vault-salt)
 *     │
 *     ▼ Argon2id (opslimit=4, memlimit=256MB)
 *     │
 *     masterKey (32 bytes — zeroed after sub-key derivation)
 *     │
 *     ├─ crypto_kdf(id=1, ctx="AF_VAULT") → vaultKey    (encrypt all vault files)
 *     ├─ crypto_kdf(id=2, ctx="AF_HMAC_") → hmacKey     (HMAC-SHA256 integrity)
 *     └─ crypto_kdf(id=3, ctx="AF_IDENT") → identityKey (wrap Ed25519/X25519 privkeys)
 *
 * Implementation: Uses libsodium-wrappers-sumo (WASM) for Electron compatibility.
 * The previous sodium-native (N-API) implementation crashed in Electron because
 * sodium_malloc's guard-paged memory can't be wrapped into N-API buffers.
 *
 * Security properties:
 *   - masterKey exists in memory for ~ms (derive sub-keys, then destroy)
 *   - All sub-keys wrapped in SecureBuffer (secure zeroing on destroy)
 *   - Argon2id with 256MB memory makes GPU/ASIC brute-force impractical
 *   - No machine binding, no OS credential store, no recovery backdoor
 */

import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import sodium from 'libsodium-wrappers-sumo';
import { SecureBuffer } from './secure-buffer';

// ── Constants ─────────────────────────────────────────────────────────

/** Argon2id parameters — opslimit=4 iterations, memlimit=256MB */
const ARGON2_OPSLIMIT = 4;
const ARGON2_MEMLIMIT = 256 * 1024 * 1024; // 256 MB

/** KDF sub-key derivation contexts (must be exactly 8 bytes per libsodium spec) */
const KDF_CTX_VAULT = 'AF_VAULT'; // 8 bytes: vault encryption key
const KDF_CTX_HMAC  = 'AF_HMAC_'; // 8 bytes: HMAC signing key (padded with underscore)
const KDF_CTX_IDENT = 'AF_IDENT'; // 8 bytes: identity key wrapping

/** Sub-key IDs (arbitrary uint64, must be unique per context) */
const SUBKEY_ID_VAULT = 1;
const SUBKEY_ID_HMAC  = 2;
const SUBKEY_ID_IDENT = 3;

/** Master key size = crypto_kdf_KEYBYTES (32 bytes for BLAKE2b-based KDF) */
const MASTER_KEY_BYTES = 32;

/** Sub-key size (32 bytes — suitable for AES-256 or HMAC-SHA256) */
const SUBKEY_BYTES = 32;

/** Canary plaintext — a fixed string we encrypt to verify passphrase correctness */
const CANARY_PLAINTEXT = 'sovereign-vault-canary-v2';

/** File names */
const SALT_FILE = '.vault-salt';
const CANARY_FILE = '.vault-canary';
const META_FILE = '.vault-meta.json';

/** Minimum passphrase length (word count) */
export const MIN_PASSPHRASE_WORDS = 8;

// ── Sodium initialization ─────────────────────────────────────────────

let sodiumReady = false;

/**
 * Ensure libsodium WASM is initialized. Must be called before any
 * crypto operations. Safe to call multiple times (idempotent).
 */
export async function ensureSodiumReady(): Promise<void> {
  if (sodiumReady) return;
  await sodium.ready;
  sodiumReady = true;
  console.log('[PassphraseKDF] libsodium WASM initialized');
}

// ── Types ─────────────────────────────────────────────────────────────

export interface DerivedKeys {
  vaultKey: SecureBuffer;
  hmacKey: SecureBuffer;
  identityKey: SecureBuffer;
}

export interface VaultMeta {
  version: 2;
  initialized: boolean;
  createdAt: number;
}

// ── Salt Management ───────────────────────────────────────────────────

/**
 * Generate a cryptographically random salt for Argon2id.
 */
export function generateSalt(): Buffer {
  const salt = Buffer.alloc(sodium.crypto_pwhash_SALTBYTES);
  crypto.randomFillSync(salt);
  return salt;
}

/**
 * Read the stored salt from disk.
 * Returns null if not found (first run).
 */
export async function readSalt(userDataDir: string): Promise<Buffer | null> {
  try {
    return await fs.readFile(path.join(userDataDir, SALT_FILE));
  } catch {
    return null;
  }
}

/**
 * Write salt to disk.
 */
export async function writeSalt(userDataDir: string, salt: Buffer): Promise<void> {
  await fs.writeFile(path.join(userDataDir, SALT_FILE), salt);
}

// ── Master Key Derivation ─────────────────────────────────────────────

/**
 * Derive a 32-byte master key from a passphrase + salt using Argon2id.
 *
 * This is the most expensive operation (~1-4 seconds) and the root of the
 * entire key hierarchy. Returns a Buffer (caller must zero it after use).
 *
 * IMPORTANT: The caller MUST zero the returned buffer after deriving sub-keys.
 * The helper `deriveAllKeys()` handles this automatically.
 */
export function deriveMasterKey(passphrase: string, salt: Buffer): Buffer {
  if (salt.length !== sodium.crypto_pwhash_SALTBYTES) {
    throw new Error(`[PassphraseKDF] Salt must be ${sodium.crypto_pwhash_SALTBYTES} bytes, got ${salt.length}`);
  }

  // crypto_pwhash(keyLength, password, salt, opsLimit, memLimit, alg) → Uint8Array
  const masterKeyArr = sodium.crypto_pwhash(
    MASTER_KEY_BYTES,
    passphrase,
    salt,
    ARGON2_OPSLIMIT,
    ARGON2_MEMLIMIT,
    sodium.crypto_pwhash_ALG_ARGON2ID13,
  );

  return Buffer.from(masterKeyArr);
}

/**
 * Derive a sub-key from the master key using BLAKE2b-based KDF.
 *
 * @param masterKey - The 32-byte master key
 * @param subkeyId - Unique sub-key identifier (uint64)
 * @param context - 8-byte context string (e.g., "AF_VAULT")
 * @returns SecureBuffer containing the sub-key
 */
export function deriveSubkey(masterKey: Buffer, subkeyId: number, context: string): SecureBuffer {
  if (context.length !== sodium.crypto_kdf_CONTEXTBYTES) {
    throw new Error(`[PassphraseKDF] KDF context must be exactly ${sodium.crypto_kdf_CONTEXTBYTES} bytes`);
  }

  // crypto_kdf_derive_from_key(subkeyLen, subkeyId, ctx, key) → Uint8Array
  const subkeyArr = sodium.crypto_kdf_derive_from_key(
    SUBKEY_BYTES,
    subkeyId,
    context,
    masterKey,
  );

  // Wrap in SecureBuffer (copies data, then wipes the Uint8Array source)
  const subkeyBuf = Buffer.from(subkeyArr);
  const sb = SecureBuffer.from(subkeyBuf);
  // SecureBuffer.from already wipes subkeyBuf
  return sb;
}

/**
 * Derive all three sub-keys from a passphrase, then destroy the master key.
 *
 * This is the primary entry point. The master key exists in memory for
 * only the duration of three BLAKE2b KDF calls (~microseconds).
 *
 * IMPORTANT: Caller must await ensureSodiumReady() before calling this.
 *
 * @returns All three derived keys in SecureBuffers
 */
export function deriveAllKeys(passphrase: string, salt: Buffer): DerivedKeys {
  const t0 = Date.now();
  console.log('[PassphraseKDF] Starting Argon2id key derivation...');

  const masterKey = deriveMasterKey(passphrase, salt);

  console.log(`[PassphraseKDF] Argon2id completed in ${Date.now() - t0}ms — deriving sub-keys...`);

  // Derive all sub-keys from master key
  const vaultKey = deriveSubkey(masterKey, SUBKEY_ID_VAULT, KDF_CTX_VAULT);
  const hmacKey = deriveSubkey(masterKey, SUBKEY_ID_HMAC, KDF_CTX_HMAC);
  const identityKey = deriveSubkey(masterKey, SUBKEY_ID_IDENT, KDF_CTX_IDENT);

  // Destroy master key — it is never needed again
  crypto.randomFillSync(masterKey);
  masterKey.fill(0);

  console.log(`[PassphraseKDF] All keys derived in ${Date.now() - t0}ms (master key destroyed)`);

  return { vaultKey, hmacKey, identityKey };
}

// ── Canary Verification ───────────────────────────────────────────────

/**
 * Create a canary file: encrypt known plaintext with the vault key.
 * Used to verify passphrase on subsequent unlocks.
 *
 * Format: [24-byte nonce][MAC + ciphertext]
 * Uses XSalsa20-Poly1305 (crypto_secretbox) — the libsodium gold standard.
 */
export async function createCanary(vaultKey: SecureBuffer, userDataDir: string): Promise<void> {
  const plaintext = Buffer.from(CANARY_PLAINTEXT, 'utf-8');
  const nonce = Buffer.from(sodium.randombytes_buf(sodium.crypto_secretbox_NONCEBYTES));

  // Borrow vaultKey for encryption
  const ciphertext = vaultKey.withAccess('readonly', (key) => {
    return Buffer.from(sodium.crypto_secretbox_easy(plaintext, nonce, key));
  });

  // Write [nonce][ciphertext] to disk
  const canaryData = Buffer.concat([nonce, ciphertext]);
  await fs.writeFile(path.join(userDataDir, CANARY_FILE), canaryData);
}

/**
 * Verify the canary file to check if a passphrase is correct.
 *
 * Returns true if the vaultKey successfully decrypts the canary.
 * Returns false if:
 *   - The canary file doesn't exist (first run)
 *   - Decryption fails (wrong passphrase)
 *   - Decrypted plaintext doesn't match expected value
 */
export async function verifyCanary(vaultKey: SecureBuffer, userDataDir: string): Promise<boolean> {
  let canaryData: Buffer;
  try {
    canaryData = await fs.readFile(path.join(userDataDir, CANARY_FILE));
  } catch {
    return false; // No canary = not initialized
  }

  const nonceLen = sodium.crypto_secretbox_NONCEBYTES;
  if (canaryData.length < nonceLen + sodium.crypto_secretbox_MACBYTES) {
    return false; // Corrupted
  }

  const nonce = canaryData.subarray(0, nonceLen);
  const ciphertext = canaryData.subarray(nonceLen);

  // Borrow vaultKey for decryption
  try {
    const plaintext = vaultKey.withAccess('readonly', (key) => {
      return Buffer.from(sodium.crypto_secretbox_open_easy(ciphertext, nonce, key));
    });
    const a = Buffer.from(plaintext.toString('utf-8'), 'utf-8');
    const b = Buffer.from(CANARY_PLAINTEXT, 'utf-8');
    return a.length === b.length && crypto.timingSafeEqual(a, b);
  } catch {
    return false; // Auth tag failed → wrong passphrase
  }
}

// ── Vault Metadata ────────────────────────────────────────────────────

/**
 * Check if the vault has been initialized (meta file + salt file exist).
 */
export async function isVaultInitialized(userDataDir: string): Promise<boolean> {
  try {
    const metaPath = path.join(userDataDir, META_FILE);
    const raw = await fs.readFile(metaPath, 'utf-8');
    const meta: VaultMeta = JSON.parse(raw);
    return meta.initialized && meta.version === 2;
  } catch {
    return false;
  }
}

/**
 * Write vault metadata on first initialization.
 */
export async function writeVaultMeta(userDataDir: string): Promise<void> {
  const meta: VaultMeta = {
    version: 2,
    initialized: true,
    createdAt: Date.now(),
  };
  await fs.writeFile(
    path.join(userDataDir, META_FILE),
    JSON.stringify(meta, null, 2),
  );
}

// ── Passphrase Validation ─────────────────────────────────────────────

/**
 * Validate passphrase meets minimum requirements.
 * Returns null if valid, or an error message string.
 *
 * Checks:
 *   1. Non-empty
 *   2. At least MIN_PASSPHRASE_WORDS words (8)
 *   3. Average word length ≥ 3 characters (blocks "a a a a a a a a")
 *   4. At least 4 unique words (blocks "go go go go go go go go")
 *   5. Minimum total length ≥ 24 characters (rough entropy floor)
 */
export function validatePassphrase(passphrase: string): string | null {
  const trimmed = passphrase.trim();
  if (!trimmed) {
    return 'Passphrase cannot be empty';
  }

  const words = trimmed.split(/\s+/).filter(w => w.length > 0);
  if (words.length < MIN_PASSPHRASE_WORDS) {
    return `Passphrase must be at least ${MIN_PASSPHRASE_WORDS} words (got ${words.length})`;
  }

  // Average word length check — blocks trivial single-letter words
  const avgLen = words.reduce((sum, w) => sum + w.length, 0) / words.length;
  if (avgLen < 3) {
    return 'Words are too short — use real words, not single characters';
  }

  // Unique word check — blocks all-identical-word passphrases
  const uniqueWords = new Set(words.map(w => w.toLowerCase()));
  if (uniqueWords.size < 4) {
    return 'Passphrase needs more variety — use at least 4 different words';
  }

  // Minimum total length (characters) — rough entropy floor
  if (trimmed.length < 24) {
    return 'Passphrase is too short — use longer or more words';
  }

  return null; // Valid
}

// ── Encryption helpers (used by vault for identity key wrapping) ──────

/**
 * Encrypt arbitrary data with a SecureBuffer key using XSalsa20-Poly1305.
 * Returns: [24-byte nonce][MAC + ciphertext]
 */
export function secretboxEncrypt(plaintext: Buffer, key: SecureBuffer): Buffer {
  const nonce = Buffer.from(sodium.randombytes_buf(sodium.crypto_secretbox_NONCEBYTES));

  const ciphertext = key.withAccess('readonly', (k) => {
    return Buffer.from(sodium.crypto_secretbox_easy(plaintext, nonce, k));
  });

  return Buffer.concat([nonce, ciphertext]);
}

/**
 * Decrypt data encrypted with secretboxEncrypt().
 * Returns null if decryption fails (wrong key or tampered data).
 */
export function secretboxDecrypt(data: Buffer, key: SecureBuffer): Buffer | null {
  const nonceLen = sodium.crypto_secretbox_NONCEBYTES;
  if (data.length < nonceLen + sodium.crypto_secretbox_MACBYTES) {
    return null;
  }

  const nonce = data.subarray(0, nonceLen);
  const ciphertext = data.subarray(nonceLen);

  try {
    const plaintext = key.withAccess('readonly', (k) => {
      return Buffer.from(sodium.crypto_secretbox_open_easy(ciphertext, nonce, k));
    });
    return plaintext;
  } catch {
    return null; // Auth tag failed
  }
}
