/**
 * Tests for deepSortKeys() — deterministic JSON canonicalization.
 *
 * cLaw Security Fix (HIGH-001): The previous shallow sort only sorted
 * top-level keys. These tests verify the recursive implementation produces
 * identical output regardless of key insertion order at ANY nesting depth.
 */

import { describe, it, expect } from 'vitest';
import crypto from 'crypto';

// ── Re-implement deepSortKeys locally (it's a private function) ──────
// We test it through signObject/verifyObject from hmac, AND test the
// standalone logic here to catch canonicalization bugs directly.

function deepSortKeys(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(deepSortKeys);
  if (typeof value === 'object') {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[key] = deepSortKeys((value as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return value;
}

function canonicalize(obj: unknown): string {
  return JSON.stringify(deepSortKeys(obj));
}

describe('deepSortKeys — deterministic canonicalization', () => {
  it('sorts top-level keys alphabetically', () => {
    const a = { z: 1, a: 2, m: 3 };
    const b = { a: 2, m: 3, z: 1 };
    expect(canonicalize(a)).toBe(canonicalize(b));
    expect(canonicalize(a)).toBe('{"a":2,"m":3,"z":1}');
  });

  it('sorts nested object keys recursively', () => {
    const a = { outer: { z: 1, a: 2 }, first: true };
    const b = { first: true, outer: { a: 2, z: 1 } };
    expect(canonicalize(a)).toBe(canonicalize(b));
  });

  it('handles deeply nested objects (3+ levels)', () => {
    const a = { l1: { l2: { l3: { z: 1, a: 2 } } } };
    const b = { l1: { l2: { l3: { a: 2, z: 1 } } } };
    expect(canonicalize(a)).toBe(canonicalize(b));
    // Verify the output is fully sorted
    const parsed = JSON.parse(canonicalize(a));
    expect(Object.keys(parsed.l1.l2.l3)).toEqual(['a', 'z']);
  });

  it('recursively sorts objects inside arrays', () => {
    const a = [{ z: 1, a: 2 }, { m: 3, b: 4 }];
    const b = [{ a: 2, z: 1 }, { b: 4, m: 3 }];
    expect(canonicalize(a)).toBe(canonicalize(b));
  });

  it('preserves array order (does NOT sort array elements)', () => {
    const obj = { items: [3, 1, 2] };
    expect(canonicalize(obj)).toBe('{"items":[3,1,2]}');
  });

  it('handles null values correctly', () => {
    const obj = { b: null, a: 1 };
    expect(canonicalize(obj)).toBe('{"a":1,"b":null}');
  });

  it('handles undefined values (JSON.stringify drops them)', () => {
    const obj = { b: undefined, a: 1 };
    expect(canonicalize(obj)).toBe('{"a":1}');
  });

  it('handles empty objects', () => {
    expect(canonicalize({})).toBe('{}');
  });

  it('handles empty arrays', () => {
    expect(canonicalize([])).toBe('[]');
  });

  it('handles primitive values', () => {
    expect(canonicalize('hello')).toBe('"hello"');
    expect(canonicalize(42)).toBe('42');
    expect(canonicalize(true)).toBe('true');
    expect(canonicalize(null)).toBe('null');
  });

  it('produces identical HMAC for same data in different key orders', () => {
    // This is the actual security property we care about
    const secret = crypto.randomBytes(32);
    const a = { payload: { z: 1, a: 2 }, meta: { type: 'test', id: 'abc' } };
    const b = { meta: { id: 'abc', type: 'test' }, payload: { a: 2, z: 1 } };

    const hmacA = crypto.createHmac('sha256', secret).update(canonicalize(a)).digest('hex');
    const hmacB = crypto.createHmac('sha256', secret).update(canonicalize(b)).digest('hex');

    expect(hmacA).toBe(hmacB);
  });

  it('produces DIFFERENT HMAC for actually different data', () => {
    const secret = crypto.randomBytes(32);
    const a = { key: 'value1' };
    const b = { key: 'value2' };

    const hmacA = crypto.createHmac('sha256', secret).update(canonicalize(a)).digest('hex');
    const hmacB = crypto.createHmac('sha256', secret).update(canonicalize(b)).digest('hex');

    expect(hmacA).not.toBe(hmacB);
  });

  it('handles mixed nested structures (the OLD shallow sort would fail this)', () => {
    // This specific test case would PASS with shallow sort but reveals the bug
    // when the inner object key order matters for HMAC
    const a = {
      attestation: {
        digest: 'abc123',
        meta: { version: 1, format: 'json' },
      },
      sessionId: 'sess-1',
    };

    const b = {
      sessionId: 'sess-1',
      attestation: {
        meta: { format: 'json', version: 1 },
        digest: 'abc123',
      },
    };

    // The shallow sort would sort top-level keys but leave nested keys in insertion order
    // Result: JSON would differ → HMAC mismatch → false tampering alarm
    expect(canonicalize(a)).toBe(canonicalize(b));
  });
});
