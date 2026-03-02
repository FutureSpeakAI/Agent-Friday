/**
 * HMAC v2 — Unit Tests
 *
 * Tests the injected-key HMAC engine (no Electron dependency, no safeStorage).
 * Verifies signing, verification, timing-safe comparison, object signing,
 * and lifecycle (init/destroy).
 *
 * Uses real sodium-native SecureBuffer for the signing key.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import crypto from 'crypto';
import { SecureBuffer } from '../../src/main/crypto/secure-buffer';
import {
  initializeHmac,
  destroyHmac,
  sign,
  verify,
  signBytes,
  verifyBytes,
  signObject,
  verifyObject,
  isInitialized,
} from '../../src/main/integrity/hmac';

// ── Helpers ─────────────────────────────────────────────────────────────

function createTestKey(): SecureBuffer {
  const rawKey = crypto.randomBytes(32);
  const sb = SecureBuffer.from(rawKey);
  return sb;
}

// ── Test Suite ──────────────────────────────────────────────────────────

describe('HMAC v2 (injected key)', () => {
  let testKey: SecureBuffer;

  beforeEach(() => {
    destroyHmac(); // ensure clean state
    testKey = createTestKey();
    initializeHmac(testKey);
  });

  afterEach(() => {
    destroyHmac();
    // Don't destroy testKey here — HMAC stores a reference, destroyHmac clears it
    // The SecureBuffer itself may already be in use by the test
  });

  // ── Initialization ─────────────────────────────────────────────────

  describe('initialization', () => {
    it('reports initialized after initializeHmac()', () => {
      expect(isInitialized()).toBe(true);
    });

    it('reports not initialized after destroyHmac()', () => {
      destroyHmac();
      expect(isInitialized()).toBe(false);
    });

    it('is idempotent — second init does not change key', () => {
      const sig1 = sign('test');
      const otherKey = createTestKey();
      initializeHmac(otherKey); // should be ignored
      const sig2 = sign('test');
      expect(sig1).toBe(sig2);
    });

    it('throws when signing without initialization', () => {
      destroyHmac();
      expect(() => sign('test')).toThrow('Not initialized');
    });

    it('throws when verifying without initialization', () => {
      destroyHmac();
      expect(() => verify('test', 'abc')).toThrow('Not initialized');
    });
  });

  // ── String Signing ─────────────────────────────────────────────────

  describe('sign() / verify()', () => {
    it('produces a 64-character hex string (SHA-256)', () => {
      const sig = sign('hello world');
      expect(sig).toMatch(/^[0-9a-f]{64}$/);
    });

    it('is deterministic for the same input', () => {
      const sig1 = sign('deterministic test');
      const sig2 = sign('deterministic test');
      expect(sig1).toBe(sig2);
    });

    it('produces different signatures for different inputs', () => {
      const sig1 = sign('message A');
      const sig2 = sign('message B');
      expect(sig1).not.toBe(sig2);
    });

    it('verifies a correct signature', () => {
      const data = 'verify this';
      const sig = sign(data);
      expect(verify(data, sig)).toBe(true);
    });

    it('rejects a wrong signature', () => {
      const sig = sign('original');
      expect(verify('tampered', sig)).toBe(false);
    });

    it('rejects a corrupted signature', () => {
      const data = 'test data';
      const sig = sign(data);
      // Flip one character
      const corrupted = sig.slice(0, -1) + (sig.endsWith('0') ? '1' : '0');
      expect(verify(data, corrupted)).toBe(false);
    });

    it('rejects a signature of wrong length', () => {
      const data = 'test';
      expect(verify(data, 'short')).toBe(false);
      expect(verify(data, 'a'.repeat(128))).toBe(false);
    });
  });

  // ── Binary Signing ─────────────────────────────────────────────────

  describe('signBytes() / verifyBytes()', () => {
    it('signs and verifies binary data', () => {
      const data = Buffer.from([0x00, 0xff, 0x42, 0xde, 0xad, 0xbe, 0xef]);
      const sig = signBytes(data);
      expect(sig).toBeInstanceOf(Buffer);
      expect(sig.length).toBe(32); // SHA-256 = 32 bytes

      expect(verifyBytes(data, sig)).toBe(true);
    });

    it('rejects tampered binary data', () => {
      const data = Buffer.from('binary payload');
      const sig = signBytes(data);

      const tampered = Buffer.from('binary payloak'); // one byte different
      expect(verifyBytes(tampered, sig)).toBe(false);
    });

    it('rejects wrong-length signature', () => {
      const data = Buffer.from('test');
      expect(verifyBytes(data, Buffer.alloc(16))).toBe(false);
    });
  });

  // ── Object Signing ─────────────────────────────────────────────────

  describe('signObject() / verifyObject()', () => {
    it('signs and verifies a plain object', () => {
      const obj = { name: 'Agent Alpha', version: 3 };
      const sig = signObject(obj);
      expect(verifyObject(obj, sig)).toBe(true);
    });

    it('is key-order independent (canonical serialization)', () => {
      const obj1 = { b: 2, a: 1 };
      const obj2 = { a: 1, b: 2 };
      const sig1 = signObject(obj1);
      const sig2 = signObject(obj2);
      expect(sig1).toBe(sig2);
    });

    it('handles nested objects with deep key sorting', () => {
      const obj1 = { outer: { z: 3, a: 1 }, name: 'test' };
      const obj2 = { name: 'test', outer: { a: 1, z: 3 } };
      expect(signObject(obj1)).toBe(signObject(obj2));
    });

    it('handles arrays (preserves order)', () => {
      const obj1 = { items: [1, 2, 3] };
      const obj2 = { items: [3, 2, 1] };
      expect(signObject(obj1)).not.toBe(signObject(obj2));
    });

    it('detects tampered object fields', () => {
      const original = { balance: 100, owner: 'alice' };
      const sig = signObject(original);
      const tampered = { balance: 999, owner: 'alice' };
      expect(verifyObject(tampered, sig)).toBe(false);
    });

    it('handles null and undefined values', () => {
      const obj = { a: null, b: undefined };
      const sig = signObject(obj);
      expect(sig).toMatch(/^[0-9a-f]{64}$/);
      expect(verifyObject(obj, sig)).toBe(true);
    });
  });

  // ── Different keys produce different signatures ────────────────────

  describe('key isolation', () => {
    it('different keys produce different signatures for the same data', () => {
      const sig1 = sign('same data');

      // Re-init with a different key
      destroyHmac();
      const key2 = createTestKey();
      initializeHmac(key2);
      const sig2 = sign('same data');

      expect(sig1).not.toBe(sig2);
    });
  });
});
