/**
 * Passphrase KDF — Unit Tests
 *
 * Tests the Argon2id-based key derivation, BLAKE2b sub-key derivation,
 * canary verification, passphrase validation, and secretbox helpers.
 *
 * NOTE: Argon2id with 256MB memlimit is too slow for unit tests.
 * We test with reduced parameters where possible, and test the
 * full-strength path in a focused integration test.
 *
 * These tests use libsodium-wrappers-sumo (WASM). No mocking.
 */

import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import crypto from 'crypto';
import sodium from 'libsodium-wrappers-sumo';

import {
  generateSalt,
  readSalt,
  writeSalt,
  deriveMasterKey,
  deriveSubkey,
  deriveAllKeys,
  createCanary,
  verifyCanary,
  isVaultInitialized,
  writeVaultMeta,
  validatePassphrase,
  secretboxEncrypt,
  secretboxDecrypt,
  MIN_PASSPHRASE_WORDS,
} from '../../src/main/crypto/passphrase-kdf';

import { SecureBuffer } from '../../src/main/crypto/secure-buffer';

// ── Sodium WASM init ─────────────────────────────────────────────────

beforeAll(async () => {
  await sodium.ready;
});

// ── Test Helpers ──────────────────────────────────────────────────────

let testDir: string;

beforeEach(async () => {
  testDir = path.join(os.tmpdir(), `af-kdf-test-${crypto.randomUUID().slice(0, 8)}`);
  await fs.mkdir(testDir, { recursive: true });
});

afterEach(async () => {
  try {
    await fs.rm(testDir, { recursive: true, force: true });
  } catch {
    // Ignore cleanup errors
  }
});

/** Test passphrase that meets the 8-word minimum */
const TEST_PASSPHRASE = 'correct horse battery staple xylophone quantum neutron cascade';

/** A second, different passphrase */
const WRONG_PASSPHRASE = 'wrong phrase that does not match the original at all extra';

// ── Salt Tests ────────────────────────────────────────────────────────

describe('Salt Management', () => {
  it('generates a salt of the correct length', () => {
    const salt = generateSalt();
    expect(salt).toBeInstanceOf(Buffer);
    expect(salt.length).toBe(sodium.crypto_pwhash_SALTBYTES);
  });

  it('generates unique salts each time', () => {
    const salt1 = generateSalt();
    const salt2 = generateSalt();
    expect(salt1.equals(salt2)).toBe(false);
  });

  it('writes and reads salt from disk', async () => {
    const salt = generateSalt();
    await writeSalt(testDir, salt);
    const loaded = await readSalt(testDir);
    expect(loaded).not.toBeNull();
    expect(loaded!.equals(salt)).toBe(true);
  });

  it('readSalt returns null for missing file', async () => {
    const result = await readSalt(testDir);
    expect(result).toBeNull();
  });
});

// ── Master Key Derivation ─────────────────────────────────────────────

describe('deriveMasterKey', () => {
  it('derives a 32-byte Buffer', () => {
    const salt = generateSalt();
    const mk = deriveMasterKey(TEST_PASSPHRASE, salt);

    expect(mk).toBeInstanceOf(Buffer);
    expect(mk.length).toBe(32);
  });

  it('is deterministic (same passphrase + salt → same key)', () => {
    const salt = generateSalt();
    const mk1 = deriveMasterKey(TEST_PASSPHRASE, salt);
    const mk2 = deriveMasterKey(TEST_PASSPHRASE, salt);

    expect(mk1.equals(mk2)).toBe(true);
  });

  it('produces different keys for different passphrases', () => {
    const salt = generateSalt();
    const mk1 = deriveMasterKey(TEST_PASSPHRASE, salt);
    const mk2 = deriveMasterKey(WRONG_PASSPHRASE, salt);

    expect(mk1.equals(mk2)).toBe(false);
  });

  it('produces different keys for different salts', () => {
    const salt1 = generateSalt();
    const salt2 = generateSalt();
    const mk1 = deriveMasterKey(TEST_PASSPHRASE, salt1);
    const mk2 = deriveMasterKey(TEST_PASSPHRASE, salt2);

    expect(mk1.equals(mk2)).toBe(false);
  });

  it('rejects invalid salt length', () => {
    expect(() => deriveMasterKey(TEST_PASSPHRASE, Buffer.alloc(8))).toThrow('Salt must be');
  });
});

// ── Sub-key Derivation ────────────────────────────────────────────────

describe('deriveSubkey', () => {
  it('derives a 32-byte sub-key from master key', () => {
    const salt = generateSalt();
    const mk = deriveMasterKey(TEST_PASSPHRASE, salt);

    const subkey = deriveSubkey(mk, 1, 'AF_VAULT');
    expect(subkey.length).toBe(32);

    subkey.destroy();
  });

  it('different sub-key IDs produce different keys', () => {
    const salt = generateSalt();
    const mk = deriveMasterKey(TEST_PASSPHRASE, salt);

    const sk1 = deriveSubkey(mk, 1, 'AF_VAULT');
    const sk2 = deriveSubkey(mk, 2, 'AF_VAULT');

    const b1 = sk1.withAccess('readonly', (buf) => Buffer.from(buf));
    const b2 = sk2.withAccess('readonly', (buf) => Buffer.from(buf));

    expect(b1.equals(b2)).toBe(false);

    sk1.destroy();
    sk2.destroy();
  });

  it('different contexts produce different keys', () => {
    const salt = generateSalt();
    const mk = deriveMasterKey(TEST_PASSPHRASE, salt);

    const sk1 = deriveSubkey(mk, 1, 'AF_VAULT');
    const sk2 = deriveSubkey(mk, 1, 'AF_HMAC_');

    const b1 = sk1.withAccess('readonly', (buf) => Buffer.from(buf));
    const b2 = sk2.withAccess('readonly', (buf) => Buffer.from(buf));

    expect(b1.equals(b2)).toBe(false);

    sk1.destroy();
    sk2.destroy();
  });

  it('rejects context with wrong length', () => {
    const salt = generateSalt();
    const mk = deriveMasterKey(TEST_PASSPHRASE, salt);

    expect(() => deriveSubkey(mk, 1, 'TOOLONG!')).not.toThrow(); // 8 bytes, OK
    expect(() => deriveSubkey(mk, 1, 'SHORT')).toThrow('exactly');
  });
});

// ── deriveAllKeys ─────────────────────────────────────────────────────

describe('deriveAllKeys', () => {
  it('returns three distinct SecureBuffers', () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    expect(keys.vaultKey.length).toBe(32);
    expect(keys.hmacKey.length).toBe(32);
    expect(keys.identityKey.length).toBe(32);

    // All three must be different
    const v = keys.vaultKey.withAccess('readonly', (buf) => Buffer.from(buf));
    const h = keys.hmacKey.withAccess('readonly', (buf) => Buffer.from(buf));
    const i = keys.identityKey.withAccess('readonly', (buf) => Buffer.from(buf));

    expect(v.equals(h)).toBe(false);
    expect(v.equals(i)).toBe(false);
    expect(h.equals(i)).toBe(false);

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });

  it('is deterministic (same passphrase + salt → same keys)', () => {
    const salt = generateSalt();
    const keys1 = deriveAllKeys(TEST_PASSPHRASE, salt);
    const keys2 = deriveAllKeys(TEST_PASSPHRASE, salt);

    const v1 = keys1.vaultKey.withAccess('readonly', (buf) => Buffer.from(buf));
    const v2 = keys2.vaultKey.withAccess('readonly', (buf) => Buffer.from(buf));

    expect(v1.equals(v2)).toBe(true);

    keys1.vaultKey.destroy();
    keys1.hmacKey.destroy();
    keys1.identityKey.destroy();
    keys2.vaultKey.destroy();
    keys2.hmacKey.destroy();
    keys2.identityKey.destroy();
  });
});

// ── Canary Verification ───────────────────────────────────────────────

describe('Canary', () => {
  it('createCanary + verifyCanary succeeds with correct key', async () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    await createCanary(keys.vaultKey, testDir);
    const result = await verifyCanary(keys.vaultKey, testDir);

    expect(result).toBe(true);

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });

  it('verifyCanary fails with wrong key (wrong passphrase)', async () => {
    const salt = generateSalt();
    const keys1 = deriveAllKeys(TEST_PASSPHRASE, salt);
    const keys2 = deriveAllKeys(WRONG_PASSPHRASE, salt);

    await createCanary(keys1.vaultKey, testDir);
    const result = await verifyCanary(keys2.vaultKey, testDir);

    expect(result).toBe(false);

    keys1.vaultKey.destroy();
    keys1.hmacKey.destroy();
    keys1.identityKey.destroy();
    keys2.vaultKey.destroy();
    keys2.hmacKey.destroy();
    keys2.identityKey.destroy();
  });

  it('verifyCanary returns false when no canary file exists', async () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    const result = await verifyCanary(keys.vaultKey, testDir);
    expect(result).toBe(false);

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });

  it('verifyCanary returns false for corrupted canary file', async () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    // Write garbage as canary
    await fs.writeFile(path.join(testDir, '.vault-canary'), Buffer.from('corrupt'));
    const result = await verifyCanary(keys.vaultKey, testDir);

    expect(result).toBe(false);

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });
});

// ── Vault Metadata ────────────────────────────────────────────────────

describe('Vault Metadata', () => {
  it('isVaultInitialized returns false when no meta file', async () => {
    expect(await isVaultInitialized(testDir)).toBe(false);
  });

  it('writeVaultMeta + isVaultInitialized round-trip', async () => {
    await writeVaultMeta(testDir);
    expect(await isVaultInitialized(testDir)).toBe(true);
  });

  it('isVaultInitialized returns false for v1 metadata', async () => {
    // Simulate old v1 metadata (no version field)
    const oldMeta = { initialized: true, createdAt: Date.now(), machineHash: 'abc', recoveryPhraseShown: true };
    await fs.writeFile(path.join(testDir, '.vault-meta.json'), JSON.stringify(oldMeta));

    // v2 check should fail — version !== 2
    expect(await isVaultInitialized(testDir)).toBe(false);
  });
});

// ── Passphrase Validation ─────────────────────────────────────────────

describe('validatePassphrase', () => {
  it('accepts passphrase with minimum words', () => {
    const phrase = Array.from({ length: MIN_PASSPHRASE_WORDS }, (_, i) => `word${i}`).join(' ');
    expect(validatePassphrase(phrase)).toBeNull();
  });

  it('accepts passphrase with more than minimum words', () => {
    const phrase = 'this is a very long passphrase with many more words than required';
    expect(validatePassphrase(phrase)).toBeNull();
  });

  it('rejects empty passphrase', () => {
    expect(validatePassphrase('')).toContain('empty');
  });

  it('rejects whitespace-only passphrase', () => {
    expect(validatePassphrase('   ')).toContain('empty');
  });

  it('rejects passphrase with too few words', () => {
    const phrase = 'only three words';
    const result = validatePassphrase(phrase);
    expect(result).toContain(`at least ${MIN_PASSPHRASE_WORDS}`);
  });

  it('handles extra whitespace between words', () => {
    // Multiple spaces between words should count correctly
    const phrase = 'word1  word2   word3    word4     word5      word6       word7        word8';
    expect(validatePassphrase(phrase)).toBeNull();
  });

  it('trims leading and trailing whitespace', () => {
    const phrase = `  ${TEST_PASSPHRASE}  `;
    expect(validatePassphrase(phrase)).toBeNull();
  });
});

// ── Secretbox Encrypt/Decrypt ─────────────────────────────────────────

describe('secretboxEncrypt / secretboxDecrypt', () => {
  it('round-trips plaintext correctly', () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    const plaintext = Buffer.from('Hello, Sovereign Vault!');
    const encrypted = secretboxEncrypt(plaintext, keys.vaultKey);
    const decrypted = secretboxDecrypt(encrypted, keys.vaultKey);

    expect(decrypted).not.toBeNull();
    expect(decrypted!.toString('utf-8')).toBe('Hello, Sovereign Vault!');

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });

  it('fails decryption with wrong key', () => {
    const salt = generateSalt();
    const keys1 = deriveAllKeys(TEST_PASSPHRASE, salt);
    const keys2 = deriveAllKeys(WRONG_PASSPHRASE, salt);

    const plaintext = Buffer.from('Top Secret');
    const encrypted = secretboxEncrypt(plaintext, keys1.vaultKey);
    const decrypted = secretboxDecrypt(encrypted, keys2.vaultKey);

    expect(decrypted).toBeNull();

    keys1.vaultKey.destroy();
    keys1.hmacKey.destroy();
    keys1.identityKey.destroy();
    keys2.vaultKey.destroy();
    keys2.hmacKey.destroy();
    keys2.identityKey.destroy();
  });

  it('detects tampered ciphertext', () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    const plaintext = Buffer.from('Tamper test');
    const encrypted = secretboxEncrypt(plaintext, keys.vaultKey);

    // Flip a bit in the ciphertext portion
    const nonceLen: number = sodium.crypto_secretbox_NONCEBYTES;
    encrypted[nonceLen + 5] ^= 0xFF;

    const decrypted = secretboxDecrypt(encrypted, keys.vaultKey);
    expect(decrypted).toBeNull();

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });

  it('returns null for truncated data', () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    const result = secretboxDecrypt(Buffer.alloc(5), keys.vaultKey);
    expect(result).toBeNull();

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });

  it('handles empty plaintext', () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    const plaintext = Buffer.alloc(0);
    const encrypted = secretboxEncrypt(plaintext, keys.vaultKey);
    const decrypted = secretboxDecrypt(encrypted, keys.vaultKey);

    expect(decrypted).not.toBeNull();
    expect(decrypted!.length).toBe(0);

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });

  it('each encryption produces different ciphertext (random nonce)', () => {
    const salt = generateSalt();
    const keys = deriveAllKeys(TEST_PASSPHRASE, salt);

    const plaintext = Buffer.from('same input');
    const enc1 = secretboxEncrypt(plaintext, keys.vaultKey);
    const enc2 = secretboxEncrypt(plaintext, keys.vaultKey);

    expect(enc1.equals(enc2)).toBe(false); // Different nonces

    keys.vaultKey.destroy();
    keys.hmacKey.destroy();
    keys.identityKey.destroy();
  });
});
