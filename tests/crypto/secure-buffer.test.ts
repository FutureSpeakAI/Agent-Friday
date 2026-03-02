/**
 * SecureBuffer — Unit Tests
 *
 * Tests the guard-paged, mlocked memory wrapper for cryptographic keys.
 * Verifies allocation, protection state transitions, borrow patterns,
 * source wiping, and use-after-destroy safety.
 *
 * NOTE: These tests use real sodium-native calls (native addon).
 * No mocking — this IS the security foundation.
 */

import { describe, it, expect } from 'vitest';
import { SecureBuffer } from '../../src/main/crypto/secure-buffer';

describe('SecureBuffer', () => {
  // ── Allocation ───────────────────────────────────────────────────

  describe('alloc()', () => {
    it('allocates a buffer of the requested size', () => {
      const sb = SecureBuffer.alloc(32);
      expect(sb.length).toBe(32);
      expect(sb.destroyed).toBe(false);
      sb.destroy();
    });

    it('starts in NOACCESS state', () => {
      const sb = SecureBuffer.alloc(32);
      expect(sb.protection).toBe('noaccess');
      sb.destroy();
    });

    it('throws when accessing inner in NOACCESS state', () => {
      const sb = SecureBuffer.alloc(32);
      expect(() => sb.inner).toThrow('NOACCESS');
      sb.destroy();
    });
  });

  // ── from() — copy + wipe ──────────────────────────────────────

  describe('from()', () => {
    it('creates a secure buffer with copied content', () => {
      const source = Buffer.from('secret-key-material-here');
      const sb = SecureBuffer.from(source);

      expect(sb.length).toBe(24); // 'secret-key-material-here'.length
      expect(sb.protection).toBe('readonly');

      // Source should be wiped
      const allZeros = source.every(b => b === 0);
      expect(allZeros).toBe(true);

      // Content should be accessible in readonly mode
      expect(sb.inner.toString('utf-8')).toBe('secret-key-material-here');

      sb.destroy();
    });

    it('wipes the source buffer after copying', () => {
      const secret = Buffer.alloc(32, 0xAA);
      SecureBuffer.from(secret);

      // Every byte in source should now be zero
      for (let i = 0; i < secret.length; i++) {
        expect(secret[i]).toBe(0);
      }
    });
  });

  // ── Protection State Transitions ─────────────────────────────

  describe('protection states', () => {
    it('transitions: noaccess → readwrite → readonly → noaccess', () => {
      const sb = SecureBuffer.alloc(16);

      expect(sb.protection).toBe('noaccess');

      sb.unlock(); // → readwrite
      expect(sb.protection).toBe('readwrite');
      // Can read and write
      sb.inner.fill(0x42);
      expect(sb.inner[0]).toBe(0x42);

      sb.readonly(); // → readonly
      expect(sb.protection).toBe('readonly');
      // Can still read
      expect(sb.inner[0]).toBe(0x42);

      sb.lock(); // → noaccess
      expect(sb.protection).toBe('noaccess');
      expect(() => sb.inner).toThrow('NOACCESS');

      sb.destroy();
    });

    it('readonly() allows reading the inner buffer', () => {
      const source = Buffer.from('test-data');
      const sb = SecureBuffer.from(source);

      expect(sb.protection).toBe('readonly');
      expect(sb.inner.toString('utf-8')).toBe('test-data');

      sb.destroy();
    });
  });

  // ── withAccess() borrow pattern ──────────────────────────────

  describe('withAccess()', () => {
    it('temporarily unlocks for readonly access', () => {
      const source = Buffer.from('borrow-test');
      const sb = SecureBuffer.from(source);
      sb.lock();

      const result = sb.withAccess('readonly', (buf) => {
        return buf.toString('utf-8');
      });

      expect(result).toBe('borrow-test');
      expect(sb.protection).toBe('noaccess'); // Re-locked after callback
      sb.destroy();
    });

    it('temporarily unlocks for readwrite access', () => {
      const sb = SecureBuffer.alloc(4);

      sb.withAccess('readwrite', (buf) => {
        buf.writeUInt32BE(0xDEADBEEF, 0);
      });

      expect(sb.protection).toBe('noaccess');

      // Verify the write stuck
      const val = sb.withAccess('readonly', (buf) => buf.readUInt32BE(0));
      expect(val).toBe(0xDEADBEEF);

      sb.destroy();
    });

    it('re-locks even if callback throws', () => {
      const sb = SecureBuffer.alloc(16);

      expect(() => {
        sb.withAccess('readonly', () => {
          throw new Error('intentional test error');
        });
      }).toThrow('intentional test error');

      // Must still be locked
      expect(sb.protection).toBe('noaccess');
      sb.destroy();
    });
  });

  // ── withAccessAsync() async borrow pattern ────────────────────

  describe('withAccessAsync()', () => {
    it('temporarily unlocks for async readonly access', async () => {
      const source = Buffer.from('async-test');
      const sb = SecureBuffer.from(source);
      sb.lock();

      const result = await sb.withAccessAsync('readonly', async (buf) => {
        // Simulate async work
        await new Promise(r => setTimeout(r, 10));
        return buf.toString('utf-8');
      });

      expect(result).toBe('async-test');
      expect(sb.protection).toBe('noaccess');
      sb.destroy();
    });

    it('re-locks even if async callback rejects', async () => {
      const sb = SecureBuffer.alloc(16);

      await expect(
        sb.withAccessAsync('readwrite', async () => {
          throw new Error('async failure');
        }),
      ).rejects.toThrow('async failure');

      expect(sb.protection).toBe('noaccess');
      sb.destroy();
    });
  });

  // ── destroy() ────────────────────────────────────────────────

  describe('destroy()', () => {
    it('marks buffer as destroyed', () => {
      const sb = SecureBuffer.alloc(32);
      expect(sb.destroyed).toBe(false);
      sb.destroy();
      expect(sb.destroyed).toBe(true);
    });

    it('is idempotent', () => {
      const sb = SecureBuffer.alloc(32);
      sb.destroy();
      sb.destroy(); // Should not throw
      expect(sb.destroyed).toBe(true);
    });

    it('throws on inner access after destroy', () => {
      const sb = SecureBuffer.alloc(32);
      sb.destroy();
      expect(() => sb.inner).toThrow('use after destroy');
    });

    it('throws on unlock after destroy', () => {
      const sb = SecureBuffer.alloc(32);
      sb.destroy();
      expect(() => sb.unlock()).toThrow('use after destroy');
    });

    it('throws on lock after destroy', () => {
      const sb = SecureBuffer.alloc(32);
      sb.destroy();
      expect(() => sb.lock()).toThrow('use after destroy');
    });

    it('throws on withAccess after destroy', () => {
      const sb = SecureBuffer.alloc(32);
      sb.destroy();
      expect(() => sb.withAccess('readonly', () => {})).toThrow('use after destroy');
    });
  });

  // ── Edge Cases ───────────────────────────────────────────────

  describe('edge cases', () => {
    it('handles 1-byte allocation', () => {
      const sb = SecureBuffer.alloc(1);
      sb.withAccess('readwrite', (buf) => {
        buf[0] = 0xFF;
      });
      const val = sb.withAccess('readonly', (buf) => buf[0]);
      expect(val).toBe(0xFF);
      sb.destroy();
    });

    it('handles large allocation (1KB)', () => {
      const sb = SecureBuffer.alloc(1024);
      expect(sb.length).toBe(1024);
      sb.destroy();
    });

    it('multiple secure buffers are independent', () => {
      const sb1 = SecureBuffer.alloc(16);
      const sb2 = SecureBuffer.alloc(16);

      sb1.withAccess('readwrite', (buf) => buf.fill(0xAA));
      sb2.withAccess('readwrite', (buf) => buf.fill(0xBB));

      const v1 = sb1.withAccess('readonly', (buf) => buf[0]);
      const v2 = sb2.withAccess('readonly', (buf) => buf[0]);

      expect(v1).toBe(0xAA);
      expect(v2).toBe(0xBB);

      sb1.destroy();
      sb2.destroy();
    });
  });
});
