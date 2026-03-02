/**
 * Passphrase KDF — Derives all vault keys from a user-chosen passphrase.
 *
 * Key hierarchy:
 *   User Passphrase (≥8 words, never stored, never on disk)
 *     + salt (16 bytes, random, stored in .vault-salt)
 *     │
 *     ▼ Argon2id (opslimit=4, memlimit=256MB)
 *     │
 *     masterKey (32 bytes, SecureBuffer — zeroed after sub-key derivation)
 *     │
 *     ├─ crypto_kdf(id=1, ctx="AF_VAULT") → vaultKey    (encrypt all vault files)
 *     ├─ crypto_kdf(id=2, ctx="AF_HMAC_") → hmacKey     (HMAC-SHA256 integrity)
 *     └─ crypto_kdf(id=3, ctx="AF_IDENT") → identityKey (wrap Ed25519/X25519 privkeys)
 *
 * Canary verification:
 *   A known plaintext is encrypted with the vaultKey on first init.
 *   On unlock, we try to decrypt it — if the GCM auth tag fails,
 *   the passphrase was wrong. No verifier hash is stored.
 *
 * Security properties:
 *   - masterKey exists in memory for ~100ms (derive sub-keys, then destroy)
 *   - All sub-keys live in SecureBuffer (guard-paged, mlocked, NOACCESS default)
 *   - Argon2id with 256MB memory makes GPU/ASIC brute-force impractical
 *   - No machine binding, no OS credential store, no recovery backdoor
 */

import fs from 'fs/promises';
import path from 'path';
import { SecureBuffer } from './secure-buffer';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const sodium = require('sodium-native');

// ── Constants ─────────────────────────────────────────────────────────

/** Argon2id parameters — opslimit=4 iterations, memlimit=256MB */
const ARGON2_OPSLIMIT = 4;
const ARGON2_MEMLIMIT = 256 * 1024 * 1024; // 256 MB
const ARGON2_ALG = sodium.crypto_pwhash_ALG_ARGON2ID13;

/** KDF sub-key derivation contexts (must be exactly 8 bytes per libsodium spec) */
const KDF_CTX_VAULT = 'AF_VAULT'; // 8 bytes: vault encryption key
const KDF_CTX_HMAC  = 'AF_HMAC_'; // 8 bytes: HMAC signing key (padded with underscore)
const KDF_CTX_IDENT = 'AF_IDENT'; // 8 bytes: identity key wrapping

/** Sub-key IDs (arbitrary uint64, must be unique per context) */
const SUBKEY_ID_VAULT = 1;
const SUBKEY_ID_HMAC  = 2;
const SUBKEY_ID_IDENT = 3;

/** Master key size = crypto_kdf_KEYBYTES (32 bytes for BLAKE2b-based KDF) */
const MASTER_KEY_BYTES: number = sodium.crypto_kdf_KEYBYTES;

/** Sub-key size (32 bytes — suitable for AES-256 or HMAC-SHA256) */
const SUBKEY_BYTES = 32;

/** Salt size for Argon2id (crypto_pwhash_SALTBYTES = 16) */
const SALT_BYTES: number = sodium.crypto_pwhash_SALTBYTES;

/** Canary plaintext — a fixed string we encrypt to verify passphrase correctness */
const CANARY_PLAINTEXT = 'sovereign-vault-canary-v2';

/** File names */
const SALT_FILE = '.vault-salt';
const CANARY_FILE = '.vault-canary';
const META_FILE = '.vault-meta.json';

/** Minimum passphrase length (word count) */
export const MIN_PASSPHRASE_WORDS = 8;

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
  const salt = Buffer.alloc(SALT_BYTES);
  sodium.randombytes_buf(salt);
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
 * entire key hierarchy. The returned SecureBuffer is in READONLY state.
 *
 * IMPORTANT: The caller MUST destroy the master key after deriving sub-keys.
 * The helper `deriveAllKeys()` handles this automatically.
 */
export function deriveMasterKey(passphrase: string, salt: Buffer): SecureBuffer {
  if (salt.length !== SALT_BYTES) {
    throw new Error(`[PassphraseKDF] Salt must be ${SALT_BYTES} bytes, got ${salt.length}`);
  }

  // Allocate secure output buffer
  const masterBuf = SecureBuffer.alloc(MASTER_KEY_BYTES);

  // Unlock for write
  masterBuf.unlock();

  // Argon2id: passphrase + salt → masterKey
  // crypto_pwhash(output, password, salt, opslimit, memlimit, alg)
  sodium.crypto_pwhash(
    masterBuf.inner,
    Buffer.from(passphrase, 'utf-8'),
    salt,
    ARGON2_OPSLIMIT,
    ARGON2_MEMLIMIT,
    ARGON2_ALG,
  );

  // Leave in READONLY state
  masterBuf.readonly();
  return masterBuf;
}

/**
 * Derive a sub-key from the master key using BLAKE2b-based KDF.
 *
 * @param masterKey - The 32-byte master key (must be in READONLY or READWRITE state)
 * @param subkeyId - Unique sub-key identifier (uint64)
 * @param context - 8-byte context string (e.g., "AF_VAULT")
 * @returns SecureBuffer in READONLY state
 */
export function deriveSubkey(masterKey: SecureBuffer, subkeyId: number, context: string): SecureBuffer {
  if (context.length !== sodium.crypto_kdf_CONTEXTBYTES) {
    throw new Error(`[PassphraseKDF] KDF context must be exactly ${sodium.crypto_kdf_CONTEXTBYTES} bytes`);
  }

  const subkey = SecureBuffer.alloc(SUBKEY_BYTES);
  subkey.unlock();

  // crypto_kdf_derive_from_key(subkey, subkeyId, ctx, masterKey)
  sodium.crypto_kdf_derive_from_key(
    subkey.inner,
    subkeyId,
    Buffer.from(context, 'ascii'),
    masterKey.inner,  // masterKey must be readable
  );

  subkey.readonly();
  return subkey;
}

/**
 * Derive all three sub-keys from a passphrase, then destroy the master key.
 *
 * This is the primary entry point. The master key exists in memory for
 * only the duration of three BLAKE2b KDF calls (~microseconds).
 *
 * @returns All three derived keys in READONLY state
 */
export function deriveAllKeys(passphrase: string, salt: Buffer): DerivedKeys {
  const t0 = Date.now();
  console.log('[PassphraseKDF] Starting Argon2id key derivation...');

  const masterKey = deriveMasterKey(passphrase, salt);

  console.log(`[PassphraseKDF] Argon2id completed in ${Date.now() - t0}ms — deriving sub-keys...`);

  // Master key is in READONLY state — derive all sub-keys
  const vaultKey = deriveSubkey(masterKey, SUBKEY_ID_VAULT, KDF_CTX_VAULT);
  const hmacKey = deriveSubkey(masterKey, SUBKEY_ID_HMAC, KDF_CTX_HMAC);
  const identityKey = deriveSubkey(masterKey, SUBKEY_ID_IDENT, KDF_CTX_IDENT);

  // Destroy master key — it is never needed again
  masterKey.destroy();

  console.log(`[PassphraseKDF] All keys derived in ${Date.now() - t0}ms (master key destroyed)`);

  return { vaultKey, hmacKey, identityKey };
}

// ── Canary Verification ───────────────────────────────────────────────

/**
 * Create a canary file: encrypt known plaintext with the vault key.
 * Used to verify passphrase on subsequent unlocks.
 *
 * Format: [24-byte nonce][16-byte MAC][ciphertext]
 * Uses XSalsa20-Poly1305 (crypto_secretbox) — the libsodium gold standard.
 */
export async function createCanary(vaultKey: SecureBuffer, userDataDir: string): Promise<void> {
  const plaintext = Buffer.from(CANARY_PLAINTEXT, 'utf-8');
  const nonce = Buffer.alloc(sodium.crypto_secretbox_NONCEBYTES);
  sodium.randombytes_buf(nonce);

  const ciphertext = Buffer.alloc(plaintext.length + sodium.crypto_secretbox_MACBYTES);

  // Borrow vaultKey for encryption
  vaultKey.withAccess('readonly', (key) => {
    sodium.crypto_secretbox_easy(ciphertext, plaintext, nonce, key);
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

  const nonceLen: number = sodium.crypto_secretbox_NONCEBYTES;
  if (canaryData.length < nonceLen + sodium.crypto_secretbox_MACBYTES) {
    return false; // Corrupted
  }

  const nonce = canaryData.subarray(0, nonceLen);
  const ciphertext = canaryData.subarray(nonceLen);
  const plaintext = Buffer.alloc(ciphertext.length - sodium.crypto_secretbox_MACBYTES);

  // Borrow vaultKey for decryption
  const ok = vaultKey.withAccess('readonly', (key) => {
    return sodium.crypto_secretbox_open_easy(plaintext, ciphertext, nonce, key);
  });

  if (!ok) return false; // Auth tag failed → wrong passphrase

  return plaintext.toString('utf-8') === CANARY_PLAINTEXT;
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
  const nonce = Buffer.alloc(sodium.crypto_secretbox_NONCEBYTES);
  sodium.randombytes_buf(nonce);

  const ciphertext = Buffer.alloc(plaintext.length + sodium.crypto_secretbox_MACBYTES);

  key.withAccess('readonly', (k) => {
    sodium.crypto_secretbox_easy(ciphertext, plaintext, nonce, k);
  });

  return Buffer.concat([nonce, ciphertext]);
}

/**
 * Decrypt data encrypted with secretboxEncrypt().
 * Returns null if decryption fails (wrong key or tampered data).
 */
export function secretboxDecrypt(data: Buffer, key: SecureBuffer): Buffer | null {
  const nonceLen: number = sodium.crypto_secretbox_NONCEBYTES;
  if (data.length < nonceLen + sodium.crypto_secretbox_MACBYTES) {
    return null;
  }

  const nonce = data.subarray(0, nonceLen);
  const ciphertext = data.subarray(nonceLen);
  const plaintext = Buffer.alloc(ciphertext.length - sodium.crypto_secretbox_MACBYTES);

  const ok = key.withAccess('readonly', (k) => {
    return sodium.crypto_secretbox_open_easy(plaintext, ciphertext, nonce, k);
  });

  return ok ? plaintext : null;
}
