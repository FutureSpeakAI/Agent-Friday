/**
 * Integrity Manager — Orchestrates all integrity verification.
 *
 * This is the central hub that:
 * 1. Initializes HMAC signing on app startup
 * 2. Verifies core laws haven't been tampered with
 * 3. Verifies agent identity settings are signed and intact
 * 4. Detects external memory modifications and computes diffs
 * 5. Signs everything after legitimate changes
 * 6. Provides state for the UI integrity indicator
 *
 * Three protection tiers:
 * - Core Laws:  HMAC-verified against compiled source → safe mode if tampered
 * - Identity:   Signed after changes → tampering detection
 * - Memory:     Signed after saves → external changes surfaced to agent
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';

import crypto from 'crypto';
import { initializeHmac, sign, verify, isInitialized } from './hmac';
import { getCanonicalLaws, getIntegrityAwarenessContext, getMemoryChangeContext } from './core-laws';
import { checkMemoryIntegrity, buildMemorySnapshots } from './memory-watchdog';
import {
  type IntegrityState,
  type IntegrityManifest,
  type MemoryChangeReport,
  DEFAULT_INTEGRITY_STATE,
  INTEGRITY_MANIFEST_VERSION,
} from './types';

// Re-export everything for convenience
export { getCanonicalLaws, getIntegrityAwarenessContext, getMemoryChangeContext, getSafeModePesonality } from './core-laws';
export { initializeHmac, sign, verify, signBytes, verifyBytes } from './hmac';
export type { IntegrityState, IntegrityManifest, MemoryChangeReport, IntegrityAttestation } from './types';
export { toAttestation, serializeAttestation, deserializeAttestation } from './types';

// ── Constants ─────────────────────────────────────────────────────────

const MANIFEST_FILE = 'integrity-manifest.json';

// ── Integrity Manager Singleton ───────────────────────────────────────

class IntegrityManager {
  private state: IntegrityState = { ...DEFAULT_INTEGRITY_STATE };
  private manifest: IntegrityManifest | null = null;
  private manifestPath = '';

  /**
   * Initialize the integrity system.
   * Must be called after app.whenReady() and after settingsManager + memoryManager init.
   */
  async initialize(): Promise<void> {
    this.manifestPath = path.join(app.getPath('userData'), MANIFEST_FILE);

    // Step 1: Initialize HMAC engine (loads or generates signing key)
    await initializeHmac();

    // Step 2: Load existing manifest (if any)
    await this.loadManifest();

    // Step 3: Verify core laws
    this.verifyCoreIntegrity();

    this.state.initialized = true;
    this.state.lastVerified = Date.now();
    this.state.nonce = crypto.randomBytes(4).toString('hex');
    this.state.sessionId = crypto.randomUUID().slice(0, 12);

    console.log(`[Integrity] Initialized — laws: ${this.state.lawsIntact ? '✓' : '✗ TAMPERED'}, ` +
      `identity: ${this.state.identityIntact ? '✓' : '?'}, ` +
      `safe mode: ${this.state.safeMode ? 'YES' : 'no'}`);
  }

  // ── Core Law Verification ───────────────────────────────────────

  /**
   * Verify that the Fundamental Laws in personality.ts match the
   * canonical source in core-laws.ts by checking the HMAC signature.
   *
   * If this fails → safe mode. The laws are immutable.
   *
   * cLaw Safety: ANY error during verification triggers safe mode (fail CLOSED).
   */
  private verifyCoreIntegrity(): void {
    try {
      if (!this.manifest) {
        // First run — no manifest exists yet. Sign the current laws.
        // This is not a failure; we're establishing the baseline.
        console.log('[Integrity] First run — establishing law signatures');
        this.state.lawsIntact = true;
        return;
      }

      // Generate the canonical laws text and verify against signed version
      const canonicalLaws = getCanonicalLaws(''); // Use empty string for comparison
      const currentSignature = sign(canonicalLaws);

      if (currentSignature !== this.manifest.lawsSignature) {
        // CRITICAL FAILURE — laws have been tampered with
        console.error('[Integrity] ⚠ CORE LAW TAMPERING DETECTED — entering safe mode');
        this.state.lawsIntact = false;
        this.state.safeMode = true;
        this.state.safeModeReason = 'Core Fundamental Laws have been modified outside of normal operation. ' +
          'The compiled laws do not match the signed baseline. This could indicate tampering.';
      } else {
        this.state.lawsIntact = true;
      }
    } catch (err) {
      // cLaw: fail CLOSED — if we can't verify, assume the worst
      console.error('[Integrity/cLaw] Core verification FAILED with error — entering safe mode:', err);
      this.state.lawsIntact = false;
      this.state.safeMode = true;
      this.state.safeModeReason = 'Integrity verification system encountered an error. ' +
        'Entering safe mode as a precaution. Error: ' + (err instanceof Error ? err.message : String(err));
    }
  }

  // ── Identity Verification ────────────────────────────────────────

  /**
   * Verify agent identity settings against the signed manifest.
   * Returns true if identity is intact or no manifest exists yet.
   */
  verifyIdentity(identityJson: string): boolean {
    if (!this.manifest || !this.manifest.identitySignature) {
      // No signature yet — will be signed on next save
      return true;
    }

    const isValid = verify(identityJson, this.manifest.identitySignature);
    this.state.identityIntact = isValid;

    if (!isValid) {
      console.warn('[Integrity] Agent identity has been modified externally');
    }

    return isValid;
  }

  /**
   * Sign the current agent identity after a legitimate change.
   */
  async signIdentity(identityJson: string): Promise<void> {
    if (!isInitialized()) return;

    if (!this.manifest) {
      this.manifest = this.createEmptyManifest();
    }

    this.manifest.identitySignature = sign(identityJson);
    this.manifest.lastSigned = Date.now();
    this.state.identityIntact = true;
    await this.saveManifest();
  }

  // ── Memory Verification ──────────────────────────────────────────

  /**
   * Check memory files for external modifications.
   * Compares current state against signed snapshots.
   * Returns a MemoryChangeReport if changes detected.
   */
  checkMemories(
    longTerm: Array<{ id: string; fact: string; category: string; confirmed: boolean; createdAt: number; source: string }>,
    mediumTerm: Array<{ id: string; observation: string; category: string; confidence: number; firstObserved: number; lastReinforced: number; occurrences: number }>,
  ): MemoryChangeReport | null {
    const report = checkMemoryIntegrity(longTerm as any, mediumTerm as any, this.manifest);

    if (report) {
      this.state.memoriesIntact = false;
      this.state.memoryChanges = report;
      console.log(`[Integrity] Memory changes detected: ` +
        `+${report.longTermAdded.length} -${report.longTermRemoved.length} ~${report.longTermModified.length} long-term, ` +
        `+${report.mediumTermAdded.length} -${report.mediumTermRemoved.length} ~${report.mediumTermModified.length} medium-term`);
    } else {
      this.state.memoriesIntact = true;
      this.state.memoryChanges = null;
    }

    return report;
  }

  /**
   * Sign the current memory state after a legitimate save.
   * Also updates the snapshots for future diff computation.
   */
  async signMemories(
    longTerm: Array<{ id: string; fact: string }>,
    mediumTerm: Array<{ id: string; observation: string }>,
    longTermJson: string,
    mediumTermJson: string,
  ): Promise<void> {
    if (!isInitialized()) return;

    if (!this.manifest) {
      this.manifest = this.createEmptyManifest();
    }

    this.manifest.longTermMemorySignature = sign(longTermJson);
    this.manifest.mediumTermMemorySignature = sign(mediumTermJson);

    // Update snapshots for diff computation
    const snapshots = buildMemorySnapshots(longTerm as any, mediumTerm as any);
    this.manifest.longTermSnapshot = snapshots.longTermSnapshot;
    this.manifest.mediumTermSnapshot = snapshots.mediumTermSnapshot;

    this.manifest.lastSigned = Date.now();
    this.state.memoriesIntact = true;
    this.state.memoryChanges = null;

    await this.saveManifest();
  }

  // ── Initial Signing (First Run) ──────────────────────────────────

  /**
   * Sign everything for the first time (or re-sign after verification).
   * Called after the integrity system is initialized and all data is loaded.
   */
  async signAll(
    lawsText: string,
    identityJson: string,
    longTerm: Array<{ id: string; fact: string }>,
    mediumTerm: Array<{ id: string; observation: string }>,
    longTermJson: string,
    mediumTermJson: string,
  ): Promise<void> {
    if (!isInitialized()) return;

    this.manifest = {
      lawsSignature: sign(lawsText),
      identitySignature: sign(identityJson),
      longTermMemorySignature: sign(longTermJson),
      mediumTermMemorySignature: sign(mediumTermJson),
      longTermSnapshot: longTerm,
      mediumTermSnapshot: mediumTerm,
      lastSigned: Date.now(),
      version: INTEGRITY_MANIFEST_VERSION,
    };

    this.state.lawsIntact = true;
    this.state.identityIntact = true;
    this.state.memoriesIntact = true;
    this.state.memoryChanges = null;

    await this.saveManifest();
    console.log('[Integrity] All signatures established');
  }

  // ── Memory Change Acknowledgment ─────────────────────────────────

  /**
   * Mark memory changes as acknowledged by the agent.
   * Called after the agent has discussed the changes with the user.
   */
  acknowledgeMemoryChanges(): void {
    if (this.state.memoryChanges) {
      this.state.memoryChanges.acknowledged = true;
    }
  }

  // ── State Access ─────────────────────────────────────────────────

  /** Get the current integrity state for UI display and system prompt injection. */
  getState(): IntegrityState {
    return { ...this.state };
  }

  /** Check if the system is in safe mode. */
  isInSafeMode(): boolean {
    return this.state.safeMode;
  }

  /** Get safe mode reason, if any. */
  getSafeModeReason(): string | null {
    return this.state.safeModeReason;
  }

  /** Get unacknowledged memory changes for system prompt injection. */
  getUnacknowledgedMemoryChanges(): MemoryChangeReport | null {
    if (this.state.memoryChanges && !this.state.memoryChanges.acknowledged) {
      return this.state.memoryChanges;
    }
    return null;
  }

  /**
   * Build integrity context for the system prompt.
   * Returns the awareness context + any memory change notifications.
   */
  buildIntegrityContext(): string {
    const parts: string[] = [];

    // Always include integrity awareness (agent knows about its protection)
    parts.push(getIntegrityAwarenessContext());

    // Include memory change report if there are unacknowledged changes
    const changes = this.getUnacknowledgedMemoryChanges();
    if (changes) {
      const changeContext = getMemoryChangeContext(
        changes.longTermAdded,
        changes.longTermRemoved,
        changes.longTermModified,
        changes.mediumTermAdded,
        changes.mediumTermRemoved,
        changes.mediumTermModified,
      );
      if (changeContext) {
        parts.push(changeContext);
      }
    }

    return parts.join('\n\n');
  }

  // ── Manifest Persistence ─────────────────────────────────────────

  private async loadManifest(): Promise<void> {
    try {
      const data = await fs.readFile(this.manifestPath, 'utf-8');
      this.manifest = JSON.parse(data);
      console.log('[Integrity] Manifest loaded');
    } catch {
      // No manifest yet — first run
      this.manifest = null;
      console.log('[Integrity] No existing manifest — first run');
    }
  }

  private async saveManifest(): Promise<void> {
    if (!this.manifest) return;
    try {
      await fs.writeFile(this.manifestPath, JSON.stringify(this.manifest, null, 2), 'utf-8');
    } catch (err) {
      console.error('[Integrity] Failed to save manifest:', err);
    }
  }

  private createEmptyManifest(): IntegrityManifest {
    return {
      lawsSignature: '',
      identitySignature: '',
      longTermMemorySignature: '',
      mediumTermMemorySignature: '',
      longTermSnapshot: [],
      mediumTermSnapshot: [],
      lastSigned: Date.now(),
      version: INTEGRITY_MANIFEST_VERSION,
    };
  }
}

// ── Singleton export ─────────────────────────────────────────────────

export const integrityManager = new IntegrityManager();
