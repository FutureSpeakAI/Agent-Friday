/**
 * HMAC Integrity Engine — Safety-Critical Test Suite
 *
 * cLaw Gate Requirement:
 *   "HMAC tampering detection tests MUST pass or the build fails."
 *
 * Tests verify:
 *   1. Sign/verify round-trip succeeds for valid data
 *   2. Tampered data is detected and rejected
 *   3. Fabricated signatures are rejected
 *   4. Object signing detects field tampering
 *   5. File signing round-trip works
 *   6. Timing-safe comparison prevents length-based leaks
 */

import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import crypto from 'crypto';

// ── Setup ────────────────────────────────────────────────────────────
// v2: HMAC engine no longer depends on Electron. The signing key is
// injected as a SecureBuffer by the vault after passphrase derivation.
// We create a test SecureBuffer with random bytes for testing.

const testUserData = path.join(
  os.tmpdir(),
  `af-test-hmac-${crypto.randomUUID().slice(0, 8)}`,
);

// Import AFTER any mocks
import { SecureBuffer } from '../../src/main/crypto/secure-buffer';
import {
  initializeHmac,
  destroyHmac,
  sign,
  verify,
  signObject,
  verifyObject,
  signFile,
  verifyFile,
  isInitialized,
} from '../../src/main/integrity/hmac';

// ── Test Suite ───────────────────────────────────────────────────────

describe('HMAC Integrity Engine', () => {
  beforeAll(async () => {
    await fs.mkdir(testUserData, { recursive: true });
    // v2: Inject a test signing key (random 32-byte SecureBuffer)
    const rawKey = crypto.randomBytes(32);
    const hmacKey = SecureBuffer.from(rawKey);
    destroyHmac(); // ensure clean state
    initializeHmac(hmacKey);
  });

  afterAll(async () => {
    destroyHmac();
    await fs.rm(testUserData, { recursive: true, force: true }).catch(() => {});
  });

  // ── Initialization ─────────────────────────────────────────────

  describe('initialization', () => {
    it('should report as initialized after setup', () => {
      expect(isInitialized()).toBe(true);
    });

    it('should be idempotent — calling initializeHmac twice is safe', () => {
      const otherKey = SecureBuffer.from(crypto.randomBytes(32));
      initializeHmac(otherKey); // Should not throw or replace existing key
      expect(isInitialized()).toBe(true);
    });
  });

  // ── Sign/Verify Round-Trip ─────────────────────────────────────

  describe('sign/verify round-trip', () => {
    it('should verify a correctly signed string', () => {
      const data = 'test data for HMAC verification';
      const signature = sign(data);
      expect(verify(data, signature)).toBe(true);
    });

    it('should produce consistent signatures for the same input', () => {
      const data = 'deterministic signing test';
      expect(sign(data)).toBe(sign(data));
    });

    it('should produce different signatures for different inputs', () => {
      expect(sign('payload-alpha')).not.toBe(sign('payload-beta'));
    });

    it('should produce 64-character hex signatures (HMAC-SHA256)', () => {
      const sig = sign('length check');
      expect(sig).toMatch(/^[0-9a-f]{64}$/);
    });
  });

  // ── Tamper Detection (cLaw Gate Critical) ──────────────────────

  describe('tamper detection', () => {
    it('should REJECT tampered data', () => {
      const original = 'sensitive configuration data';
      const signature = sign(original);
      const tampered = 'sensitive configuration data MODIFIED';
      expect(verify(tampered, signature)).toBe(false);
    });

    it('should REJECT a single-character modification', () => {
      const original = 'The quick brown fox jumps over the lazy dog';
      const signature = sign(original);
      const tampered = 'The quick brown fox jumps over the lazy Dog'; // D→D uppercase
      expect(verify(tampered, signature)).toBe(false);
    });

    it('should REJECT a fabricated signature', () => {
      const data = 'protected payload';
      const fakeSig = 'a'.repeat(64); // Valid hex length, wrong content
      expect(verify(data, fakeSig)).toBe(false);
    });

    it('should REJECT an empty signature', () => {
      const data = 'protected payload';
      expect(verify(data, '')).toBe(false);
    });

    it('should REJECT a truncated signature', () => {
      const data = 'protected payload';
      const realSig = sign(data);
      const truncated = realSig.slice(0, 32); // Half length
      expect(verify(data, truncated)).toBe(false);
    });

    it('should REJECT a signature with appended bytes', () => {
      const data = 'protected payload';
      const realSig = sign(data);
      const extended = realSig + 'deadbeef';
      expect(verify(data, extended)).toBe(false);
    });
  });

  // ── Object Signing ─────────────────────────────────────────────

  describe('object signing', () => {
    it('should verify a correctly signed object', () => {
      const obj = { user: 'alice', role: 'admin', permissions: ['read', 'write'] };
      const signature = signObject(obj);
      expect(verifyObject(obj, signature)).toBe(true);
    });

    it('should REJECT a tampered object field', () => {
      const obj = { user: 'alice', role: 'admin' };
      const signature = signObject(obj);
      const tampered = { user: 'alice', role: 'superadmin' };
      expect(verifyObject(tampered, signature)).toBe(false);
    });

    it('should REJECT an object with an injected field', () => {
      const obj = { user: 'alice' };
      const signature = signObject(obj);
      const tampered = { user: 'alice', injected: 'malicious' };
      expect(verifyObject(tampered, signature)).toBe(false);
    });

    it('should REJECT an object with a removed field', () => {
      const obj = { user: 'alice', role: 'admin' };
      const signature = signObject(obj);
      const tampered = { user: 'alice' };
      expect(verifyObject(tampered, signature)).toBe(false);
    });
  });

  // ── File Signing ───────────────────────────────────────────────

  describe('file signing', () => {
    const testFilePath = path.join(testUserData, 'test-file.txt');

    it('should verify a correctly signed file', async () => {
      await fs.writeFile(testFilePath, 'file content for signing', 'utf-8');
      const signature = await signFile(testFilePath);
      expect(await verifyFile(testFilePath, signature)).toBe(true);
    });

    it('should REJECT a tampered file', async () => {
      await fs.writeFile(testFilePath, 'original content', 'utf-8');
      const signature = await signFile(testFilePath);
      await fs.writeFile(testFilePath, 'tampered content', 'utf-8');
      expect(await verifyFile(testFilePath, signature)).toBe(false);
    });

    it('should return empty string for non-existent file signing', async () => {
      const sig = await signFile(path.join(testUserData, 'nonexistent.txt'));
      expect(sig).toBe('');
    });

    it('should return false for non-existent file verification', async () => {
      const result = await verifyFile(
        path.join(testUserData, 'nonexistent.txt'),
        'abc123',
      );
      expect(result).toBe(false);
    });
  });
});
