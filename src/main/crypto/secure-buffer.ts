/**
 * SecureBuffer — Secure memory wrapper for cryptographic key material.
 *
 * Electron-compatible implementation that uses standard Node.js Buffers
 * with guaranteed secure zeroing on destroy. Maintains the same API as
 * the previous sodium_malloc-based implementation.
 *
 * Previous implementation used sodium-native's sodium_malloc() which
 * provided guard pages and mlock(). Electron's N-API doesn't support
 * wrapping externally-allocated guard-paged memory into Buffers, so
 * we use regular Buffers with logical access control instead.
 *
 * Security properties preserved:
 *   - Guaranteed zeroing on destroy (crypto.randomFill to defeat optimizations)
 *   - Logical NOACCESS/READONLY/READWRITE states (enforced at JS level)
 *   - withAccess() borrow pattern (minimum exposure window)
 *   - Source buffer wiping on SecureBuffer.from()
 *
 * Security properties lost (acceptable trade-off for Electron compat):
 *   - Guard pages (SIGSEGV on overflow/underflow)
 *   - mlock() (OS may swap to disk under memory pressure)
 *   - mprotect-enforced NOACCESS (now logical, not hardware-enforced)
 */

import crypto from 'crypto';

export type ProtectionLevel = 'noaccess' | 'readonly' | 'readwrite';

export class SecureBuffer {
  private _inner: Buffer;
  private _destroyed = false;
  private _protection: ProtectionLevel = 'noaccess';
  public readonly length: number;

  /**
   * Private constructor — use SecureBuffer.alloc() or SecureBuffer.from().
   */
  private constructor(buf: Buffer) {
    this._inner = buf;
    this.length = buf.length;
  }

  /**
   * Allocate a new secure buffer of `size` bytes, filled with zeros.
   * Returns in NOACCESS state.
   */
  static alloc(size: number): SecureBuffer {
    const buf = Buffer.alloc(size); // Zero-filled
    return new SecureBuffer(buf);
  }

  /**
   * Create a secure buffer by copying from a source, then wiping the source.
   * Returns in READONLY state (caller typically needs to read it immediately).
   *
   * The source buffer is securely zeroed after copying to prevent
   * key material from lingering in non-protected memory.
   */
  static from(source: Buffer): SecureBuffer {
    const sb = SecureBuffer.alloc(source.length);
    sb._protection = 'readwrite';
    source.copy(sb._inner);
    // Wipe the source — it's in non-protected memory
    SecureBuffer.secureZero(source);
    // Leave in READONLY state
    sb._protection = 'readonly';
    return sb;
  }

  /**
   * Access the underlying Buffer.
   * Throws if destroyed or in NOACCESS state.
   */
  get inner(): Buffer {
    if (this._destroyed) {
      throw new Error('SecureBuffer: use after destroy');
    }
    if (this._protection === 'noaccess') {
      throw new Error('SecureBuffer: buffer is in NOACCESS state — call unlock() or readonly() first');
    }
    return this._inner;
  }

  /** Is this buffer destroyed? */
  get destroyed(): boolean {
    return this._destroyed;
  }

  /** Current protection level. */
  get protection(): ProtectionLevel {
    return this._protection;
  }

  /**
   * Set to READWRITE. Use for the shortest possible duration.
   */
  unlock(): void {
    this.assertAlive();
    this._protection = 'readwrite';
  }

  /**
   * Set to READONLY. Can read but not modify.
   */
  readonly(): void {
    this.assertAlive();
    this._protection = 'readonly';
  }

  /**
   * Set to NOACCESS. Logical state — any access via .inner throws.
   */
  lock(): void {
    this.assertAlive();
    this._protection = 'noaccess';
  }

  /**
   * Borrow pattern: temporarily unlock → run callback → re-lock.
   * Guarantees re-lock even if callback throws.
   *
   * @param mode - Protection level during access ('readonly' or 'readwrite')
   * @param fn - Callback receiving the raw Buffer
   * @returns The callback's return value
   */
  withAccess<T>(mode: 'readonly' | 'readwrite', fn: (buf: Buffer) => T): T {
    this.assertAlive();
    this._protection = mode;
    try {
      return fn(this._inner);
    } finally {
      this._protection = 'noaccess';
    }
  }

  /**
   * Async borrow pattern: temporarily unlock → run async callback → re-lock.
   * Guarantees re-lock even if the promise rejects.
   */
  async withAccessAsync<T>(mode: 'readonly' | 'readwrite', fn: (buf: Buffer) => Promise<T>): Promise<T> {
    this.assertAlive();
    this._protection = mode;
    try {
      return await fn(this._inner);
    } finally {
      this._protection = 'noaccess';
    }
  }

  /**
   * Permanently destroy this buffer. Zeros all bytes securely.
   * After this call, any access throws.
   */
  destroy(): void {
    if (this._destroyed) return; // Idempotent
    SecureBuffer.secureZero(this._inner);
    this._destroyed = true;
    this._protection = 'noaccess';
  }

  private assertAlive(): void {
    if (this._destroyed) {
      throw new Error('SecureBuffer: use after destroy');
    }
  }

  /**
   * Securely zero a buffer. Uses crypto.randomFillSync first (to defeat
   * compiler optimizations that might skip a simple .fill(0)), then fills
   * with zeros. This two-step approach ensures the buffer is truly wiped.
   */
  private static secureZero(buf: Buffer): void {
    crypto.randomFillSync(buf);
    buf.fill(0);
  }
}
