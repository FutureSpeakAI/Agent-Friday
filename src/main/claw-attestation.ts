/**
 * cLaw Attestation Protocol — Cross-Agent Governance Verification
 *
 * Every Agent Friday instance must prove it operates under valid Asimov cLaws
 * before other agents will trust it. This module:
 *
 *   1. Computes a canonical SHA-256 hash of the Fundamental Laws text
 *   2. Signs the hash + timestamp with the agent's Ed25519 private key
 *   3. Attaches the attestation to every outbound P2P message
 *   4. Verifies inbound attestations: correct hash + valid signature + fresh timestamp
 *
 * An agent that fails attestation is flagged but NOT silently dropped —
 * the user is informed and can manually override if they trust the peer.
 *
 * Freshness window: 5 minutes (300,000ms). Clock skew > 5 min → stale attestation.
 */

import crypto from 'crypto';
import { getCanonicalLaws } from './integrity/core-laws';

// ── Constants ─────────────────────────────────────────────────────────

/** Maximum age of an attestation before it's considered stale (5 minutes) */
const ATTESTATION_FRESHNESS_MS = 5 * 60 * 1000;

/** Maximum allowed clock skew (attestation from the future) */
const MAX_CLOCK_SKEW_MS = 60 * 1000; // 1 minute

// ── Types ─────────────────────────────────────────────────────────────

export interface ClawAttestation {
  /** SHA-256 hash of the canonical Fundamental Laws text */
  lawsHash: string;
  /** Timestamp when this attestation was generated */
  timestamp: number;
  /** Ed25519 signature over `${lawsHash}|${timestamp}` */
  signature: string;
  /** The signing agent's public key (base64, for verification) */
  signerPublicKey: string;
}

export interface AttestationResult {
  /** Whether the attestation passed all checks */
  valid: boolean;
  /** Why it failed (null if valid) */
  reason: string | null;
  /** Specific failure code for programmatic handling */
  code: 'valid' | 'hash_mismatch' | 'signature_invalid' | 'stale' | 'future' | 'missing' | 'malformed';
}

// ── Canonical Laws Hash ───────────────────────────────────────────────

/** Cached hash of the canonical laws (computed once at module load) */
let canonicalLawsHash: string | null = null;

/**
 * Compute the SHA-256 hash of the canonical Fundamental Laws text.
 *
 * Uses getCanonicalLaws('') — the empty-string variant used for
 * integrity verification (user name doesn't affect the hash since
 * all agents use the same canonical text for attestation).
 */
export function computeCanonicalLawsHash(): string {
  if (canonicalLawsHash) return canonicalLawsHash;

  const canonicalText = getCanonicalLaws('');
  canonicalLawsHash = crypto.createHash('sha256')
    .update(canonicalText, 'utf-8')
    .digest('hex');

  return canonicalLawsHash;
}

// ── Attestation Generation ────────────────────────────────────────────

/**
 * Generate a cLaw attestation for this agent.
 *
 * Called before every outbound P2P message to prove this agent
 * operates under valid Fundamental Laws.
 *
 * @param signingPrivateKeyBase64 - Ed25519 private key (DER, base64)
 * @param signingPublicKeyBase64 - Ed25519 public key (DER, base64)
 * @returns The attestation object to attach to the message
 */
export function generateAttestation(
  signingPrivateKeyBase64: string,
  signingPublicKeyBase64: string,
): ClawAttestation {
  const lawsHash = computeCanonicalLawsHash();
  const timestamp = Date.now();

  // Sign: hash|timestamp
  const signable = `${lawsHash}|${timestamp}`;
  const key = crypto.createPrivateKey({
    key: Buffer.from(signingPrivateKeyBase64, 'base64'),
    format: 'der',
    type: 'pkcs8',
  });
  const signature = crypto.sign(null, Buffer.from(signable, 'utf-8'), key).toString('hex');

  return {
    lawsHash,
    timestamp,
    signature,
    signerPublicKey: signingPublicKeyBase64,
  };
}

// ── Attestation Verification ──────────────────────────────────────────

/**
 * Verify an inbound cLaw attestation from a peer agent.
 *
 * Checks:
 *   1. Attestation is present and well-formed
 *   2. Laws hash matches our canonical hash (same Fundamental Laws)
 *   3. Ed25519 signature is valid
 *   4. Timestamp is within freshness window (not stale, not future)
 *
 * @param attestation - The attestation from the inbound message
 * @param expectedPublicKey - The peer's known public key (from pairing)
 * @returns Verification result with reason if failed
 */
export function verifyAttestation(
  attestation: ClawAttestation | undefined | null,
  expectedPublicKey?: string,
): AttestationResult {
  // Check presence
  if (!attestation) {
    return { valid: false, reason: 'No cLaw attestation attached', code: 'missing' };
  }

  // Check well-formedness
  if (!attestation.lawsHash || !attestation.signature || !attestation.timestamp || !attestation.signerPublicKey) {
    return { valid: false, reason: 'Malformed attestation (missing fields)', code: 'malformed' };
  }

  // Check laws hash matches
  const ourHash = computeCanonicalLawsHash();
  if (attestation.lawsHash !== ourHash) {
    return {
      valid: false,
      reason: `Laws hash mismatch: peer has ${attestation.lawsHash.slice(0, 12)}... but we expect ${ourHash.slice(0, 12)}...`,
      code: 'hash_mismatch',
    };
  }

  // Check freshness
  const now = Date.now();
  const age = now - attestation.timestamp;

  if (age > ATTESTATION_FRESHNESS_MS) {
    return {
      valid: false,
      reason: `Attestation is stale (${Math.round(age / 1000)}s old, max ${ATTESTATION_FRESHNESS_MS / 1000}s)`,
      code: 'stale',
    };
  }

  if (age < -MAX_CLOCK_SKEW_MS) {
    return {
      valid: false,
      reason: `Attestation is from the future (${Math.round(-age / 1000)}s ahead, max skew ${MAX_CLOCK_SKEW_MS / 1000}s)`,
      code: 'future',
    };
  }

  // Verify Ed25519 signature
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
    return { valid: false, reason: 'Signature verification error (invalid key or signature format)', code: 'signature_invalid' };
  }

  return { valid: true, reason: null, code: 'valid' };
}

// ── User Override Tracking ────────────────────────────────────────────

/**
 * Set of agent IDs the user has manually approved despite attestation failure.
 * Persisted in memory only (resets on restart for safety).
 */
const userOverrides = new Set<string>();

/** Add a manual trust override for a specific agent. */
export function addUserOverride(agentId: string): void {
  userOverrides.add(agentId);
  console.log(`[cLaw] User override added for agent ${agentId}`);
}

/** Remove a manual trust override. */
export function removeUserOverride(agentId: string): void {
  userOverrides.delete(agentId);
}

/** Check if a user override exists for an agent. */
export function hasUserOverride(agentId: string): boolean {
  return userOverrides.has(agentId);
}

/** Get all agents with user overrides. */
export function getUserOverrides(): string[] {
  return Array.from(userOverrides);
}
