/**
 * SecureBuffer — Guard-paged, mlocked memory for cryptographic key material.
 *
 * Wraps sodium-native's sodium_malloc() which provides:
 *   - Guard pages before and after the allocation (SIGSEGV on overflow/underflow)
 *   - mlock() to prevent the OS from swapping to disk
 *   - Canary bytes to detect corruption
 *   - sodium_memzero() on destroy (guaranteed zeroing, not optimized away)
 *
 * Memory protection states (via mprotect):
 *   - NOACCESS: Default after alloc. Any read/write → SIGSEGV.
 *   - READONLY: Can read, writes → SIGSEGV.
 *   - READWRITE: Full access (use sparingly, for shortest possible duration).
 *
 * Design principle: Key material spends most of its lifetime in NOACCESS state.
 * The withAccess() helper unlocks → runs callback → re-locks automatically,
 * ensuring the minimum exposure window even if the callback throws.
 */

// eslint-disable-next-line @typescript-eslint/no-require-imports
const sodium = require('sodium-native');

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
    // Default state: NOACCESS
    sodium.sodium_mprotect_noaccess(this._inner);
  }

  /**
   * Allocate a new secure buffer of `size` bytes, filled with zeros.
   * Returns in NOACCESS state.
   */
  static alloc(size: number): SecureBuffer {
    const buf: Buffer = sodium.sodium_malloc(size);
    // sodium_malloc returns zeroed memory
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
    // Unlock to write
    sodium.sodium_mprotect_readwrite(sb._inner);
    source.copy(sb._inner);
    // Wipe the source — it's in non-protected memory
    sodium.sodium_memzero(source);
    // Leave in READONLY state
    sodium.sodium_mprotect_readonly(sb._inner);
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
    sodium.sodium_mprotect_readwrite(this._inner);
    this._protection = 'readwrite';
  }

  /**
   * Set to READONLY. Can read but not modify.
   */
  readonly(): void {
    this.assertAlive();
    sodium.sodium_mprotect_readonly(this._inner);
    this._protection = 'readonly';
  }

  /**
   * Set to NOACCESS. Any access → SIGSEGV.
   */
  lock(): void {
    this.assertAlive();
    sodium.sodium_mprotect_noaccess(this._inner);
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
    if (mode === 'readwrite') {
      sodium.sodium_mprotect_readwrite(this._inner);
    } else {
      sodium.sodium_mprotect_readonly(this._inner);
    }
    this._protection = mode;
    try {
      return fn(this._inner);
    } finally {
      sodium.sodium_mprotect_noaccess(this._inner);
      this._protection = 'noaccess';
    }
  }

  /**
   * Async borrow pattern: temporarily unlock → run async callback → re-lock.
   * Guarantees re-lock even if the promise rejects.
   */
  async withAccessAsync<T>(mode: 'readonly' | 'readwrite', fn: (buf: Buffer) => Promise<T>): Promise<T> {
    this.assertAlive();
    if (mode === 'readwrite') {
      sodium.sodium_mprotect_readwrite(this._inner);
    } else {
      sodium.sodium_mprotect_readonly(this._inner);
    }
    this._protection = mode;
    try {
      return await fn(this._inner);
    } finally {
      sodium.sodium_mprotect_noaccess(this._inner);
      this._protection = 'noaccess';
    }
  }

  /**
   * Permanently destroy this buffer. Zeros all bytes, then frees.
   * After this call, any access throws.
   */
  destroy(): void {
    if (this._destroyed) return; // Idempotent
    // Must be readwrite to zero
    sodium.sodium_mprotect_readwrite(this._inner);
    sodium.sodium_memzero(this._inner);
    this._destroyed = true;
    this._protection = 'noaccess';
    // Note: sodium_malloc'd memory is freed when GC collects the Buffer.
    // We've zeroed it, which is the important part.
  }

  private assertAlive(): void {
    if (this._destroyed) {
      throw new Error('SecureBuffer: use after destroy');
    }
  }
}
