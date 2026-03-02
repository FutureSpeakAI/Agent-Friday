/**
 * Sovereign Vault v2 — Integration Tests
 *
 * Tests the full vault lifecycle: initialization, unlock, wrong passphrase,
 * encryption/decryption round-trips, identity key helpers, and destruction.
 *
 * These tests mock Electron's app.getPath() but use real sodium-native
 * for all cryptographic operations — no fake crypto.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';

// Mock Electron before importing vault
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(),
  },
}));

import { app } from 'electron';
import {
  initializeNewVault,
  unlockVault,
  isVaultUnlocked,
  isVaultInitialized,
  getHmacKey,
  destroyVault,
  vaultWrite,
  vaultRead,
  vaultReadJSON,
  encryptPrivateKey,
  decryptPrivateKey,
} from '../../src/main/vault';

// ── Helpers ─────────────────────────────────────────────────────────────

const TEST_PASSPHRASE = 'the old lighthouse keeper plays chess with seagulls every morning';
const WRONG_PASSPHRASE = 'a completely different sentence that is also eight words long';

let testDir: string;

async function createTempDir(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), 'vault-v2-test-'));
}

async function cleanDir(dir: string): Promise<void> {
  try {
    await fs.rm(dir, { recursive: true, force: true });
  } catch { /* ignore */ }
}

// ── Test Suite ──────────────────────────────────────────────────────────

describe('Sovereign Vault v2', () => {
  beforeEach(async () => {
    testDir = await createTempDir();
    (app.getPath as ReturnType<typeof vi.fn>).mockReturnValue(testDir);
    // Ensure vault is destroyed between tests (module-level state)
    destroyVault();
  });

  afterEach(async () => {
    destroyVault();
    await cleanDir(testDir);
  });

  // ── First-time Initialization ──────────────────────────────────────

  describe('initializeNewVault()', () => {
    it('initializes successfully with a valid passphrase', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      expect(isVaultUnlocked()).toBe(true);
    });

    it('creates salt and canary files', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const files = await fs.readdir(testDir);
      expect(files).toContain('.vault-salt');
      expect(files).toContain('.vault-canary');
      expect(files).toContain('.vault-meta.json');
    });

    it('rejects passphrase with fewer than 8 words', async () => {
      await expect(initializeNewVault('too few words here'))
        .rejects.toThrow();
    });

    it('rejects empty passphrase', async () => {
      await expect(initializeNewVault(''))
        .rejects.toThrow();
    });

    it('throws if vault is already initialized', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      destroyVault(); // unlock state cleared, but files remain
      await expect(initializeNewVault(TEST_PASSPHRASE))
        .rejects.toThrow('Already initialized');
    });

    it('provides an HMAC key after initialization', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const hmacKey = getHmacKey();
      expect(hmacKey).not.toBeNull();
      expect(hmacKey!.length).toBe(32);
    });
  });

  // ── Unlock ─────────────────────────────────────────────────────────

  describe('unlockVault()', () => {
    beforeEach(async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      destroyVault(); // simulate app restart
    });

    it('unlocks with correct passphrase', async () => {
      const ok = await unlockVault(TEST_PASSPHRASE);
      expect(ok).toBe(true);
      expect(isVaultUnlocked()).toBe(true);
    });

    it('rejects wrong passphrase', async () => {
      const ok = await unlockVault(WRONG_PASSPHRASE);
      expect(ok).toBe(false);
      expect(isVaultUnlocked()).toBe(false);
    });

    it('rejects empty passphrase gracefully', async () => {
      const ok = await unlockVault('');
      expect(ok).toBe(false);
    });

    it('provides HMAC key after unlock', async () => {
      await unlockVault(TEST_PASSPHRASE);
      const hmacKey = getHmacKey();
      expect(hmacKey).not.toBeNull();
      expect(hmacKey!.length).toBe(32);
    });

    it('derives the same HMAC key every time', async () => {
      await unlockVault(TEST_PASSPHRASE);
      const key1hex = getHmacKey()!.withAccess('readonly', (k) => k.toString('hex'));
      destroyVault();

      await unlockVault(TEST_PASSPHRASE);
      const key2hex = getHmacKey()!.withAccess('readonly', (k) => k.toString('hex'));

      expect(key1hex).toBe(key2hex);
    });
  });

  // ── Vault Encryption (AES-256-GCM) ────────────────────────────────

  describe('vaultWrite / vaultRead', () => {
    beforeEach(async () => {
      await initializeNewVault(TEST_PASSPHRASE);
    });

    it('round-trips string content through encryption', async () => {
      const filePath = path.join(testDir, 'test-data.json');
      const content = JSON.stringify({ hello: 'world', count: 42 });

      await vaultWrite(filePath, content);
      const result = await vaultRead(filePath);

      expect(result).toBe(content);
    });

    it('encrypted file is binary (not plaintext)', async () => {
      const filePath = path.join(testDir, 'test-encrypted.json');
      const content = 'this is sensitive data';

      await vaultWrite(filePath, content);
      const raw = await fs.readFile(filePath);

      // Should NOT contain plaintext
      expect(raw.toString('utf-8')).not.toBe(content);
      // Should have at least IV + tag + some ciphertext
      expect(raw.length).toBeGreaterThan(28);
    });

    it('reads plaintext files transparently (graceful migration)', async () => {
      const filePath = path.join(testDir, 'legacy-plain.txt');
      await fs.writeFile(filePath, 'plaintext data', 'utf-8');

      const result = await vaultRead(filePath);
      expect(result).toBe('plaintext data');
    });

    it('vaultReadJSON parses encrypted JSON', async () => {
      const filePath = path.join(testDir, 'test-json.json');
      const obj = { agents: ['alpha', 'beta'], version: 3 };

      await vaultWrite(filePath, JSON.stringify(obj));
      const result = await vaultReadJSON(filePath);

      expect(result).toEqual(obj);
    });

    it('each write produces different ciphertext (random IV)', async () => {
      const filePath1 = path.join(testDir, 'rng-test-1.bin');
      const filePath2 = path.join(testDir, 'rng-test-2.bin');

      await vaultWrite(filePath1, 'same content');
      await vaultWrite(filePath2, 'same content');

      const raw1 = await fs.readFile(filePath1);
      const raw2 = await fs.readFile(filePath2);

      // Same plaintext → different ciphertext (random IV)
      expect(raw1.equals(raw2)).toBe(false);
    });
  });

  // ── Identity Key (private key encryption) ──────────────────────────

  describe('encryptPrivateKey / decryptPrivateKey', () => {
    beforeEach(async () => {
      await initializeNewVault(TEST_PASSPHRASE);
    });

    it('round-trips a base64 private key', () => {
      const original = 'dGhpcyBpcyBhIGZha2UgcHJpdmF0ZSBrZXk=';
      const encrypted = encryptPrivateKey(original);
      expect(encrypted.startsWith('enc:')).toBe(true);

      const decrypted = decryptPrivateKey(encrypted);
      expect(decrypted).toBe(original);
    });

    it('encrypted key differs from plaintext', () => {
      const original = 'c29tZSByYW5kb20gYnl0ZXM=';
      const encrypted = encryptPrivateKey(original);
      expect(encrypted).not.toBe(original);
      expect(encrypted.startsWith('enc:')).toBe(true);
    });

    it('decrypts plaintext passthrough (legacy)', () => {
      const plaintext = 'just-a-plain-base64-key';
      const result = decryptPrivateKey(plaintext);
      expect(result).toBe(plaintext);
    });

    it('throws for v1 safe: prefix keys', () => {
      expect(() => decryptPrivateKey('safe:encrypted-blob'))
        .toThrow('Cannot decrypt v1 safeStorage-protected key');
    });

    it('produces different ciphertext each encryption (random nonce)', () => {
      const original = 'dGVzdCBrZXk=';
      const enc1 = encryptPrivateKey(original);
      const enc2 = encryptPrivateKey(original);
      expect(enc1).not.toBe(enc2); // Different nonces
      expect(decryptPrivateKey(enc1)).toBe(original);
      expect(decryptPrivateKey(enc2)).toBe(original);
    });
  });

  // ── Destroy ────────────────────────────────────────────────────────

  describe('destroyVault()', () => {
    it('clears all keys and locks the vault', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      expect(isVaultUnlocked()).toBe(true);

      destroyVault();
      expect(isVaultUnlocked()).toBe(false);
      expect(getHmacKey()).toBeNull();
    });

    it('is idempotent (safe to call multiple times)', () => {
      destroyVault();
      destroyVault();
      destroyVault();
      expect(isVaultUnlocked()).toBe(false);
    });
  });

  // ── isVaultInitialized ─────────────────────────────────────────────

  describe('isVaultInitialized()', () => {
    it('returns false before initialization', async () => {
      expect(await isVaultInitialized()).toBe(false);
    });

    it('returns true after initialization', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      expect(await isVaultInitialized()).toBe(true);
    });

    it('returns true even after destroyVault (files persist)', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      destroyVault();
      expect(await isVaultInitialized()).toBe(true);
    });
  });

  // ── Cross-session key derivation consistency ───────────────────────

  describe('deterministic key derivation', () => {
    it('derives the same vault encryption key across sessions', async () => {
      // Session 1: init and encrypt
      await initializeNewVault(TEST_PASSPHRASE);
      const filePath = path.join(testDir, 'cross-session.dat');
      await vaultWrite(filePath, 'persistent secret');
      destroyVault();

      // Session 2: unlock and decrypt
      const ok = await unlockVault(TEST_PASSPHRASE);
      expect(ok).toBe(true);
      const recovered = await vaultRead(filePath);
      expect(recovered).toBe('persistent secret');
    });

    it('derives the same identity key across sessions', async () => {
      // Session 1: init and encrypt a private key
      await initializeNewVault(TEST_PASSPHRASE);
      const encrypted = encryptPrivateKey('bXkgcHJpdmF0ZSBrZXk=');
      destroyVault();

      // Session 2: unlock and decrypt the same private key
      await unlockVault(TEST_PASSPHRASE);
      const decrypted = decryptPrivateKey(encrypted);
      expect(decrypted).toBe('bXkgcHJpdmF0ZSBrZXk=');
    });
  });
});
