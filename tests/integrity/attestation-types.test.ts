/**
 * Tests for Integrity Attestation types — compact serialization protocol.
 *
 * Verifies toAttestation(), serializeAttestation(), deserializeAttestation()
 * produce correct, compact, and round-trippable attestation payloads.
 */

import { describe, it, expect } from 'vitest';
import {
  type IntegrityState,
  type IntegrityAttestation,
  toAttestation,
  serializeAttestation,
  deserializeAttestation,
  DEFAULT_INTEGRITY_STATE,
  INTEGRITY_ATTESTATION_VERSION,
} from '../../src/main/integrity/types';

describe('toAttestation()', () => {
  it('produces a well-formed attestation from healthy state', () => {
    const state: IntegrityState = {
      ...DEFAULT_INTEGRITY_STATE,
      initialized: true,
      lawsIntact: true,
      identityIntact: true,
      memoriesIntact: true,
      safeMode: false,
      nonce: 'abcd1234',
    };

    const att = toAttestation(state, 'digest-hex-string', 'session-abc');

    expect(att.digest).toBe('digest-hex-string');
    expect(att.sessionId).toBe('session-abc');
    expect(att.nonce).toBe('abcd1234');
    expect(att.intact).toBe(true);
    expect(att.safeMode).toBe(false);
    expect(att.v).toBe(INTEGRITY_ATTESTATION_VERSION);
    expect(typeof att.ts).toBe('number');
    expect(att.ts).toBeGreaterThan(0);
  });

  it('reflects safe mode state', () => {
    const state: IntegrityState = {
      ...DEFAULT_INTEGRITY_STATE,
      safeMode: true,
      safeModeReason: 'Core law tampered',
      lawsIntact: false,
    };

    const att = toAttestation(state, 'abc', 'sess-1');
    expect(att.safeMode).toBe(true);
    expect(att.intact).toBe(false); // lawsIntact is false
  });

  it('intact is false if any integrity check fails', () => {
    // Memory failure
    const memFail: IntegrityState = { ...DEFAULT_INTEGRITY_STATE, memoriesIntact: false };
    expect(toAttestation(memFail, 'x', 's').intact).toBe(false);

    // Identity failure
    const idFail: IntegrityState = { ...DEFAULT_INTEGRITY_STATE, identityIntact: false };
    expect(toAttestation(idFail, 'x', 's').intact).toBe(false);

    // Laws failure
    const lawsFail: IntegrityState = { ...DEFAULT_INTEGRITY_STATE, lawsIntact: false };
    expect(toAttestation(lawsFail, 'x', 's').intact).toBe(false);
  });

  it('generates a random nonce if state has none', () => {
    const state: IntegrityState = { ...DEFAULT_INTEGRITY_STATE, nonce: undefined };
    const att = toAttestation(state, 'digest', 'sess');
    expect(att.nonce).toBeTruthy();
    expect(att.nonce.length).toBeGreaterThan(0);
  });
});

describe('serializeAttestation()', () => {
  it('produces a JSON string', () => {
    const att: IntegrityAttestation = {
      digest: 'abc123',
      ts: Date.now(),
      nonce: 'nonce1234',
      sessionId: 'sess-1',
      intact: true,
      safeMode: false,
      v: 1,
    };

    const serialized = serializeAttestation(att);
    expect(typeof serialized).toBe('string');
    expect(JSON.parse(serialized)).toEqual(att);
  });

  it('produces output under 512 bytes', () => {
    const att: IntegrityAttestation = {
      digest: 'a'.repeat(64), // 64-char hex digest (HMAC-SHA256)
      ts: 1709000000000, // 13-digit timestamp
      nonce: 'abcd1234', // 8-char nonce
      sessionId: 'sess-abcdef12', // 12-char session
      intact: true,
      safeMode: false,
      v: 1,
    };

    const serialized = serializeAttestation(att);
    expect(serialized.length).toBeLessThan(512);
  });
});

describe('deserializeAttestation()', () => {
  it('round-trips through serialize → deserialize', () => {
    const original: IntegrityAttestation = {
      digest: 'deadbeef',
      ts: Date.now(),
      nonce: 'abc123',
      sessionId: 'sess-1',
      intact: true,
      safeMode: false,
      v: 1,
    };

    const roundTripped = deserializeAttestation(serializeAttestation(original));
    expect(roundTripped).toEqual(original);
  });

  it('returns null for invalid JSON', () => {
    expect(deserializeAttestation('not json')).toBeNull();
    expect(deserializeAttestation('')).toBeNull();
    expect(deserializeAttestation('{}')).toBeNull(); // missing required fields
  });

  it('returns null for missing required fields', () => {
    // Missing nonce
    expect(deserializeAttestation(JSON.stringify({ digest: 'abc', ts: 123 }))).toBeNull();
    // Missing ts
    expect(deserializeAttestation(JSON.stringify({ digest: 'abc', nonce: '123' }))).toBeNull();
    // Missing digest
    expect(deserializeAttestation(JSON.stringify({ ts: 123, nonce: '123' }))).toBeNull();
  });

  it('returns null for wrong types', () => {
    // digest is number instead of string
    expect(deserializeAttestation(JSON.stringify({ digest: 123, ts: 456, nonce: 'abc' }))).toBeNull();
    // ts is string instead of number
    expect(deserializeAttestation(JSON.stringify({ digest: 'abc', ts: '456', nonce: 'abc' }))).toBeNull();
  });

  it('accepts attestation with extra fields (forwards compatibility)', () => {
    const data = JSON.stringify({
      digest: 'abc',
      ts: 123,
      nonce: '456',
      sessionId: 'sess',
      intact: true,
      safeMode: false,
      v: 1,
      futureField: 'extra',
    });
    const result = deserializeAttestation(data);
    expect(result).not.toBeNull();
    expect(result!.digest).toBe('abc');
  });
});
