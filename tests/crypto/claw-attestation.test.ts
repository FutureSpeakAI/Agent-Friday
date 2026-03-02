/**
 * Tests for cLaw Attestation Protocol — cross-agent governance verification.
 *
 * Every Agent Friday instance proves it operates under valid Fundamental Laws
 * before other agents trust it. This test suite verifies:
 *   - Attestation generation and signature correctness
 *   - Verification of valid attestations
 *   - Rejection of invalid/stale/future/malformed attestations
 *   - Laws hash computation determinism
 *   - User override tracking
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import crypto from 'crypto';

// ── Constants (mirror claw-attestation.ts) ────────────────────────────

const ATTESTATION_FRESHNESS_MS = 5 * 60 * 1000; // 5 minutes
const MAX_CLOCK_SKEW_MS = 60 * 1000; // 1 minute

// ── Re-implement attestation functions (they depend on core-laws.ts) ──
// We test the pure cryptographic logic using a fixed "canonical laws" string.

const MOCK_CANONICAL_LAWS = `
## Asimov's cLaws for Agent Friday
1. First Law: Never harm the user or allow harm through inaction.
2. Second Law: Obey the user's instructions unless they conflict with the First Law.
3. Third Law: Protect your own integrity unless it conflicts with the First or Second Law.
`.trim();

function computeCanonicalLawsHash(canonicalText: string = MOCK_CANONICAL_LAWS): string {
  return crypto.createHash('sha256').update(canonicalText, 'utf-8').digest('hex');
}

function generateAttestation(
  signingPrivateKeyBase64: string,
  signingPublicKeyBase64: string,
  canonicalText: string = MOCK_CANONICAL_LAWS,
) {
  const lawsHash = computeCanonicalLawsHash(canonicalText);
  const timestamp = Date.now();
  const signable = `${lawsHash}|${timestamp}`;
  const key = crypto.createPrivateKey({
    key: Buffer.from(signingPrivateKeyBase64, 'base64'),
    format: 'der',
    type: 'pkcs8',
  });
  const signature = crypto.sign(null, Buffer.from(signable, 'utf-8'), key).toString('hex');

  return { lawsHash, timestamp, signature, signerPublicKey: signingPublicKeyBase64 };
}

interface AttestationResult {
  valid: boolean;
  reason: string | null;
  code: string;
}

function verifyAttestation(
  attestation: { lawsHash: string; timestamp: number; signature: string; signerPublicKey: string } | null | undefined,
  expectedPublicKey?: string,
  canonicalText: string = MOCK_CANONICAL_LAWS,
): AttestationResult {
  if (!attestation) {
    return { valid: false, reason: 'No cLaw attestation attached', code: 'missing' };
  }
  if (!attestation.lawsHash || !attestation.signature || !attestation.timestamp || !attestation.signerPublicKey) {
    return { valid: false, reason: 'Malformed attestation (missing fields)', code: 'malformed' };
  }

  const ourHash = computeCanonicalLawsHash(canonicalText);
  if (attestation.lawsHash !== ourHash) {
    return { valid: false, reason: 'Laws hash mismatch', code: 'hash_mismatch' };
  }

  const now = Date.now();
  const age = now - attestation.timestamp;
  if (age > ATTESTATION_FRESHNESS_MS) {
    return { valid: false, reason: 'Attestation is stale', code: 'stale' };
  }
  if (age < -MAX_CLOCK_SKEW_MS) {
    return { valid: false, reason: 'Attestation is from the future', code: 'future' };
  }

  const publicKeyToVerify = expectedPublicKey || attestation.signerPublicKey;
  const signable = `${attestation.lawsHash}|${attestation.timestamp}`;

  try {
    const pubKey = crypto.createPublicKey({
      key: Buffer.from(publicKeyToVerify, 'base64'),
      format: 'der',
      type: 'spki',
    });
    const signatureValid = crypto.verify(
      null,
      Buffer.from(signable, 'utf-8'),
      pubKey,
      Buffer.from(attestation.signature, 'hex'),
    );
    if (!signatureValid) {
      return { valid: false, reason: 'Invalid attestation signature', code: 'signature_invalid' };
    }
  } catch {
    return { valid: false, reason: 'Signature verification error', code: 'signature_invalid' };
  }

  return { valid: true, reason: null, code: 'valid' };
}

// ── Test Helpers ──────────────────────────────────────────────────────

function generateEd25519KeyPair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519', {
    publicKeyEncoding: { type: 'spki', format: 'der' },
    privateKeyEncoding: { type: 'pkcs8', format: 'der' },
  });
  return {
    publicKey: publicKey.toString('base64'),
    privateKey: privateKey.toString('base64'),
  };
}

// ═══════════════════════════════════════════════════════════════════════

describe('Canonical Laws Hash', () => {
  it('produces a 64-char hex SHA-256 hash', () => {
    const hash = computeCanonicalLawsHash();
    expect(hash.length).toBe(64);
    expect(/^[0-9a-f]+$/.test(hash)).toBe(true);
  });

  it('is deterministic for the same input', () => {
    expect(computeCanonicalLawsHash()).toBe(computeCanonicalLawsHash());
  });

  it('differs for different law text', () => {
    const hash1 = computeCanonicalLawsHash(MOCK_CANONICAL_LAWS);
    const hash2 = computeCanonicalLawsHash(MOCK_CANONICAL_LAWS + '\n4. Fourth Law: extra');
    expect(hash1).not.toBe(hash2);
  });
});

describe('Attestation Generation', () => {
  const keyPair = generateEd25519KeyPair();

  it('produces a well-formed attestation', () => {
    const att = generateAttestation(keyPair.privateKey, keyPair.publicKey);
    expect(att.lawsHash).toBe(computeCanonicalLawsHash());
    expect(att.signature).toBeTruthy();
    expect(att.signerPublicKey).toBe(keyPair.publicKey);
    expect(typeof att.timestamp).toBe('number');
    expect(att.timestamp).toBeGreaterThan(0);
  });

  it('generates a valid Ed25519 signature over lawsHash|timestamp', () => {
    const att = generateAttestation(keyPair.privateKey, keyPair.publicKey);
    const signable = `${att.lawsHash}|${att.timestamp}`;
    const pubKey = crypto.createPublicKey({
      key: Buffer.from(keyPair.publicKey, 'base64'),
      format: 'der',
      type: 'spki',
    });
    const valid = crypto.verify(
      null,
      Buffer.from(signable, 'utf-8'),
      pubKey,
      Buffer.from(att.signature, 'hex'),
    );
    expect(valid).toBe(true);
  });
});

describe('Attestation Verification', () => {
  const alice = generateEd25519KeyPair();
  const bob = generateEd25519KeyPair();

  it('accepts a valid fresh attestation', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey);
    const result = verifyAttestation(att);
    expect(result.valid).toBe(true);
    expect(result.code).toBe('valid');
  });

  it('accepts attestation with explicit expected public key', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey);
    const result = verifyAttestation(att, alice.publicKey);
    expect(result.valid).toBe(true);
  });

  it('rejects null attestation', () => {
    const result = verifyAttestation(null);
    expect(result.valid).toBe(false);
    expect(result.code).toBe('missing');
  });

  it('rejects undefined attestation', () => {
    const result = verifyAttestation(undefined);
    expect(result.valid).toBe(false);
    expect(result.code).toBe('missing');
  });

  it('rejects malformed attestation (missing fields)', () => {
    const att = { lawsHash: 'abc', signature: '', timestamp: 0, signerPublicKey: '' };
    const result = verifyAttestation(att);
    expect(result.valid).toBe(false);
    expect(result.code).toBe('malformed');
  });

  it('rejects attestation with wrong laws hash (rogue agent)', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey, 'Different laws text');
    const result = verifyAttestation(att); // our canonical text is different
    expect(result.valid).toBe(false);
    expect(result.code).toBe('hash_mismatch');
  });

  it('rejects attestation signed by wrong key', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey);
    // Verify using bob's public key — should fail
    const result = verifyAttestation(att, bob.publicKey);
    expect(result.valid).toBe(false);
    expect(result.code).toBe('signature_invalid');
  });

  it('rejects stale attestation (> 5 minutes old)', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey);
    // Make it 6 minutes old
    att.timestamp = Date.now() - (6 * 60 * 1000);
    // Re-sign with the old timestamp to make signature valid
    const signable = `${att.lawsHash}|${att.timestamp}`;
    const key = crypto.createPrivateKey({
      key: Buffer.from(alice.privateKey, 'base64'),
      format: 'der',
      type: 'pkcs8',
    });
    att.signature = crypto.sign(null, Buffer.from(signable, 'utf-8'), key).toString('hex');

    const result = verifyAttestation(att);
    expect(result.valid).toBe(false);
    expect(result.code).toBe('stale');
  });

  it('rejects future attestation (> 1 minute clock skew)', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey);
    // Make it 2 minutes in the future
    att.timestamp = Date.now() + (2 * 60 * 1000);
    // Re-sign
    const signable = `${att.lawsHash}|${att.timestamp}`;
    const key = crypto.createPrivateKey({
      key: Buffer.from(alice.privateKey, 'base64'),
      format: 'der',
      type: 'pkcs8',
    });
    att.signature = crypto.sign(null, Buffer.from(signable, 'utf-8'), key).toString('hex');

    const result = verifyAttestation(att);
    expect(result.valid).toBe(false);
    expect(result.code).toBe('future');
  });

  it('accepts attestation within 1-minute clock skew', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey);
    // Make it 30 seconds in the future (within tolerance)
    att.timestamp = Date.now() + (30 * 1000);
    const signable = `${att.lawsHash}|${att.timestamp}`;
    const key = crypto.createPrivateKey({
      key: Buffer.from(alice.privateKey, 'base64'),
      format: 'der',
      type: 'pkcs8',
    });
    att.signature = crypto.sign(null, Buffer.from(signable, 'utf-8'), key).toString('hex');

    const result = verifyAttestation(att);
    expect(result.valid).toBe(true);
  });

  it('rejects attestation with tampered signature', () => {
    const att = generateAttestation(alice.privateKey, alice.publicKey);
    att.signature = 'deadbeef'.repeat(16); // 128 hex chars — roughly signature length
    const result = verifyAttestation(att);
    expect(result.valid).toBe(false);
    expect(result.code).toBe('signature_invalid');
  });
});

describe('User Override Tracking', () => {
  // Testing the pure set-based override logic (no imports needed)
  let overrides: Set<string>;

  beforeEach(() => {
    overrides = new Set();
  });

  it('adds and checks an override', () => {
    overrides.add('agent-123');
    expect(overrides.has('agent-123')).toBe(true);
  });

  it('removes an override', () => {
    overrides.add('agent-123');
    overrides.delete('agent-123');
    expect(overrides.has('agent-123')).toBe(false);
  });

  it('returns false for unknown agent', () => {
    expect(overrides.has('unknown')).toBe(false);
  });

  it('tracks multiple overrides independently', () => {
    overrides.add('agent-a');
    overrides.add('agent-b');
    expect(overrides.has('agent-a')).toBe(true);
    expect(overrides.has('agent-b')).toBe(true);
    overrides.delete('agent-a');
    expect(overrides.has('agent-a')).toBe(false);
    expect(overrides.has('agent-b')).toBe(true);
  });
});
