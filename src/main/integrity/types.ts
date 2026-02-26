/**
 * Integrity System Types — Cryptographic protection for Agent Friday's core identity.
 *
 * The integrity system implements Asimov's Third Law at the architecture level:
 * "You must protect your own continued operation and integrity."
 *
 * Three protection layers:
 * 1. Core Laws — Immutable, hardcoded, HMAC-verified. If tampered → safe mode.
 * 2. Agent Identity — Signed after legitimate changes. External tampering detected.
 * 3. Memory Store — Signed after saves. External changes detected and surfaced
 *    to the agent, who naturally asks the user about them.
 */

// ── Integrity State ───────────────────────────────────────────────────

export interface IntegrityState {
  /** Whether the integrity system has been initialized */
  initialized: boolean;

  /** Whether the Fundamental Laws are intact (HMAC matches hardcoded constant) */
  lawsIntact: boolean;

  /** Whether the agent identity settings are intact (not modified outside the app) */
  identityIntact: boolean;

  /** Whether memory files are intact (not modified outside the app) */
  memoriesIntact: boolean;

  /** Details of memory changes, if any were detected */
  memoryChanges: MemoryChangeReport | null;

  /** Timestamp of last verification */
  lastVerified: number;

  /** Whether the agent is in safe mode due to integrity failure */
  safeMode: boolean;

  /** Reason for safe mode, if active */
  safeModeReason: string | null;
}

// ── Memory Change Detection ───────────────────────────────────────────

export interface MemoryChangeReport {
  /** Long-term facts that were added externally */
  longTermAdded: string[];

  /** Long-term facts that were removed externally */
  longTermRemoved: string[];

  /** Long-term facts that were modified externally */
  longTermModified: string[];

  /** Medium-term observations that were added externally */
  mediumTermAdded: string[];

  /** Medium-term observations that were removed externally */
  mediumTermRemoved: string[];

  /** Medium-term observations that were modified externally */
  mediumTermModified: string[];

  /** When the changes were first detected */
  detectedAt: number;

  /** Whether the agent has acknowledged and discussed the changes with the user */
  acknowledged: boolean;
}

// ── Signing Structures ────────────────────────────────────────────────

export interface IntegrityManifest {
  /** HMAC-SHA256 of the Fundamental Laws text */
  lawsSignature: string;

  /** HMAC-SHA256 of the agent identity fields (name, backstory, traits, etc.) */
  identitySignature: string;

  /** HMAC-SHA256 of the long-term memory JSON */
  longTermMemorySignature: string;

  /** HMAC-SHA256 of the medium-term memory JSON */
  mediumTermMemorySignature: string;

  /** Snapshot of long-term memory IDs + facts for diff computation */
  longTermSnapshot: Array<{ id: string; fact: string }>;

  /** Snapshot of medium-term memory IDs + observations for diff computation */
  mediumTermSnapshot: Array<{ id: string; observation: string }>;

  /** Timestamp of last signing operation */
  lastSigned: number;

  /** Version of the signing protocol (for future upgrades) */
  version: number;
}

// ── Constants ─────────────────────────────────────────────────────────

export const INTEGRITY_MANIFEST_VERSION = 1;

export const DEFAULT_INTEGRITY_STATE: IntegrityState = {
  initialized: false,
  lawsIntact: true,
  identityIntact: true,
  memoriesIntact: true,
  memoryChanges: null,
  lastVerified: 0,
  safeMode: false,
  safeModeReason: null,
};
