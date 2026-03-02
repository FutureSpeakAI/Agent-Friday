/**
 * Identity Key Encryption — Integration Tests
 *
 * Tests the vault's encryptPrivateKey/decryptPrivateKey round-trip,
 * which protects Ed25519 and X25519 private keys at rest using
 * XSalsa20-Poly1305 via the identity sub-key.
 *
 * These tests verify the exact workflow used by agent-network.ts:
 *   save() → encryptPrivateKey() → "enc:..." on disk
 *   load() → decryptPrivateKey() → original base64 key
 *
 * Uses real sodium-native for all crypto. Mocks only Electron's app.getPath().
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import crypto from 'crypto';

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
  destroyVault,
  encryptPrivateKey,
  decryptPrivateKey,
} from '../../src/main/vault';

// ── Helpers ─────────────────────────────────────────────────────────────

const TEST_PASSPHRASE = 'sovereign vault integration test with eight or more words here';

let testDir: string;

function randomBase64Key(): string {
  return crypto.randomBytes(32).toString('base64');
}

// ── Test Suite ──────────────────────────────────────────────────────────

describe('Identity Key Encryption (protectPrivateKey / unprotectPrivateKey)', () => {
  beforeEach(async () => {
    testDir = await fs.mkdtemp(path.join(os.tmpdir(), 'identity-enc-test-'));
    (app.getPath as ReturnType<typeof vi.fn>).mockReturnValue(testDir);
    destroyVault();
  });

  afterEach(async () => {
    destroyVault();
    try { await fs.rm(testDir, { recursive: true, force: true }); } catch { /* ignore */ }
  });

  // ── Basic Round-Trip ───────────────────────────────────────────────

  describe('round-trip encryption', () => {
    it('encrypts and decrypts an Ed25519 signing private key', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const original = randomBase64Key();

      const encrypted = encryptPrivateKey(original);
      expect(encrypted).not.toBe(original);
      expect(encrypted.startsWith('enc:')).toBe(true);

      const decrypted = decryptPrivateKey(encrypted);
      expect(decrypted).toBe(original);
    });

    it('encrypts and decrypts an X25519 exchange private key', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const original = randomBase64Key();

      const encrypted = encryptPrivateKey(original);
      const decrypted = decryptPrivateKey(encrypted);
      expect(decrypted).toBe(original);
    });

    it('encrypts and decrypts a shared secret', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const sharedSecret = crypto.randomBytes(32).toString('base64');

      const encrypted = encryptPrivateKey(sharedSecret);
      const decrypted = decryptPrivateKey(encrypted);
      expect(decrypted).toBe(sharedSecret);
    });

    it('handles empty string gracefully (returns as-is if vault not ready)', async () => {
      // Vault not initialized — graceful degradation
      const result = encryptPrivateKey('');
      expect(result).toBe('');
    });
  });

  // ── Nonce Uniqueness ───────────────────────────────────────────────

  describe('nonce uniqueness', () => {
    it('produces different ciphertext for the same key (random nonce)', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const key = randomBase64Key();

      const enc1 = encryptPrivateKey(key);
      const enc2 = encryptPrivateKey(key);
      const enc3 = encryptPrivateKey(key);

      // All different (random nonces)
      expect(enc1).not.toBe(enc2);
      expect(enc2).not.toBe(enc3);
      expect(enc1).not.toBe(enc3);

      // But all decrypt to the same value
      expect(decryptPrivateKey(enc1)).toBe(key);
      expect(decryptPrivateKey(enc2)).toBe(key);
      expect(decryptPrivateKey(enc3)).toBe(key);
    });
  });

  // ── Cross-Session Persistence ──────────────────────────────────────

  describe('cross-session persistence', () => {
    it('decrypts a key from a previous session', async () => {
      // Session 1: initialize and encrypt
      await initializeNewVault(TEST_PASSPHRASE);
      const original = randomBase64Key();
      const encrypted = encryptPrivateKey(original);
      destroyVault();

      // Session 2: unlock and decrypt
      const ok = await unlockVault(TEST_PASSPHRASE);
      expect(ok).toBe(true);
      const decrypted = decryptPrivateKey(encrypted);
      expect(decrypted).toBe(original);
    });

    it('simulates a full agent-network.json save/load cycle', async () => {
      // Session 1: agent generates keys and saves
      await initializeNewVault(TEST_PASSPHRASE);
      const signingPrivKey = randomBase64Key();
      const exchangePrivKey = randomBase64Key();
      const peerSharedSecret = randomBase64Key();

      const saved = {
        keyPair: {
          signingPrivateKey: encryptPrivateKey(signingPrivKey),
          exchangePrivateKey: encryptPrivateKey(exchangePrivKey),
        },
        peers: [
          { peerId: 'agent-beta', sharedSecret: encryptPrivateKey(peerSharedSecret) },
        ],
      };

      // Write to disk as JSON
      const filePath = path.join(testDir, 'agent-network.json');
      await fs.writeFile(filePath, JSON.stringify(saved), 'utf-8');
      destroyVault();

      // Session 2: agent loads keys
      await unlockVault(TEST_PASSPHRASE);
      const loaded = JSON.parse(await fs.readFile(filePath, 'utf-8'));

      expect(decryptPrivateKey(loaded.keyPair.signingPrivateKey)).toBe(signingPrivKey);
      expect(decryptPrivateKey(loaded.keyPair.exchangePrivateKey)).toBe(exchangePrivKey);
      expect(decryptPrivateKey(loaded.peers[0].sharedSecret)).toBe(peerSharedSecret);
    });
  });

  // ── Legacy Handling ────────────────────────────────────────────────

  describe('legacy format handling', () => {
    it('passes through plaintext keys (pre-vault era)', () => {
      const plaintext = randomBase64Key();
      expect(decryptPrivateKey(plaintext)).toBe(plaintext);
    });

    it('throws for v1 safe: prefix (DPAPI/Keychain)', () => {
      expect(() => decryptPrivateKey('safe:SomeEncryptedBlob==')).toThrow(
        'Cannot decrypt v1 safeStorage-protected key',
      );
    });

    it('throws descriptive error for v1 safe: prefix', () => {
      try {
        decryptPrivateKey('safe:blob');
        expect.fail('should have thrown');
      } catch (err: any) {
        expect(err.message).toContain('safeStorage');
        expect(err.message).toContain('removed');
        expect(err.message).toContain('regenerated');
      }
    });
  });

  // ── Graceful Degradation ───────────────────────────────────────────

  describe('graceful degradation (vault locked)', () => {
    it('encryptPrivateKey returns plaintext when vault is locked', () => {
      // Vault never initialized
      const key = randomBase64Key();
      const result = encryptPrivateKey(key);
      expect(result).toBe(key); // No encryption, passthrough
    });

    it('decryptPrivateKey handles enc: prefix when vault is locked', async () => {
      // Encrypt while vault is open
      await initializeNewVault(TEST_PASSPHRASE);
      const key = randomBase64Key();
      const encrypted = encryptPrivateKey(key);
      destroyVault();

      // Try to decrypt while vault is locked → should throw
      expect(() => decryptPrivateKey(encrypted)).toThrow('vault locked');
    });
  });

  // ── Tamper Detection ───────────────────────────────────────────────

  describe('tamper detection', () => {
    it('rejects tampered ciphertext', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const encrypted = encryptPrivateKey(randomBase64Key());

      // Tamper with the base64 blob
      const parts = encrypted.split(':');
      const blob = Buffer.from(parts[1], 'base64');
      blob[blob.length - 1] ^= 0xff; // flip last byte
      const tampered = `enc:${blob.toString('base64')}`;

      expect(() => decryptPrivateKey(tampered)).toThrow();
    });

    it('rejects truncated ciphertext', async () => {
      await initializeNewVault(TEST_PASSPHRASE);
      const encrypted = encryptPrivateKey(randomBase64Key());

      // Truncate the blob
      const blob = Buffer.from(encrypted.slice(4), 'base64');
      const truncated = `enc:${blob.subarray(0, 10).toString('base64')}`;

      expect(() => decryptPrivateKey(truncated)).toThrow();
    });
  });

  // ── Multiple Keys in Same Session ──────────────────────────────────

  describe('multiple keys in same session', () => {
    it('handles encrypting many keys without leaks or collisions', async () => {
      await initializeNewVault(TEST_PASSPHRASE);

      const keys = Array.from({ length: 50 }, () => randomBase64Key());
      const encrypted = keys.map(encryptPrivateKey);

      // All encrypted values should be unique (random nonces)
      const uniqueSet = new Set(encrypted);
      expect(uniqueSet.size).toBe(50);

      // All should round-trip correctly
      encrypted.forEach((enc, i) => {
        expect(decryptPrivateKey(enc)).toBe(keys[i]);
      });
    });
  });
});
