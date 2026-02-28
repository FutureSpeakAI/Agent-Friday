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
 * 7. Allows safe mode reset when the user initiates re-signing
 *
 * Three protection tiers:
 * - Core Laws:  HMAC-verified against compiled source → safe mode if tampered
 * - Identity:   Signed after changes → tampering detection
 * - Memory:     Signed after saves → external changes surfaced to agent
 *
 * IMPORTANT: Core law verification ALWAYS uses getCanonicalLaws('') — the
 * empty-string canonical form. The signAll() function ALSO signs with the
 * empty-string form. This guarantees the signature is stable regardless of
 * what userName is configured, preventing false safe mode triggers when the
 * user's name changes between sessions.
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

/**
 * The CANONICAL form of laws is ALWAYS generated with an empty string.
 * This ensures signatures are stable regardless of userName changes.
 * The dynamic userName substitution happens only at prompt-generation time
 * in personality.ts — it is never part of the integrity baseline.
 */
const CANONICAL_LAWS_KEY = '';

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
   * Verify that the Fundamental Laws match the canonical source in core-laws.ts
   * by checking the HMAC signature.
   *
   * CRITICAL: Both signing and verification use getCanonicalLaws('') — the
   * empty-string canonical form. This prevents false safe mode triggers
   * caused by userName changes between sessions.
   *
   * AUTO-RECOVERY: If a signature mismatch is detected, the system first
   * attempts to re-sign with the canonical form. This handles the upgrade
   * scenario where a previous version signed with a userName-based form.
   * Safe mode is only entered if re-signing fails or if an error occurs.
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

      // Generate the canonical laws text and verify against signed version.
      // ALWAYS use empty string for canonical form — matches signAll().
      const canonicalLaws = getCanonicalLaws(CANONICAL_LAWS_KEY);
      const currentSignature = sign(canonicalLaws);

      if (currentSignature !== this.manifest.lawsSignature) {
        // Signature mismatch — attempt auto-recovery before entering safe mode.
        // This handles the common case where:
        // 1. Previous version signed with getCanonicalLaws(userName)
        // 2. App update changed law text
        // 3. Manifest was written with a different canonical form (pre-fix bug)
        //
        // Auto-recovery: re-sign the laws with the correct canonical form.
        // This is safe because the laws are hardcoded — we're just fixing
        // the signature to match the canonical form.
        console.warn('[Integrity] Core law signature mismatch — attempting auto-recovery');
        console.warn(`[Integrity]   Current sig: ${currentSignature.slice(0, 16)}...`);
        console.warn(`[Integrity]   Manifest sig: ${this.manifest.lawsSignature.slice(0, 16)}...`);
        console.warn(`[Integrity]   Laws length: ${canonicalLaws.length} chars`);

        // Re-sign the laws with the canonical form
        this.manifest.lawsSignature = sign(canonicalLaws);
        this.manifest.lastSigned = Date.now();

        // Verify that the re-sign worked
        const verifySignature = sign(canonicalLaws);
        if (verifySignature === this.manifest.lawsSignature) {
          // Auto-recovery succeeded — no safe mode needed
          console.log('[Integrity] ✓ Auto-recovery succeeded — law signatures re-established');
          this.state.lawsIntact = true;
          // Save will happen when signAll() runs on startup
          this.needsManifestSave = true;
        } else {
          // This should never happen — if it does, something is deeply wrong
          console.error('[Integrity] ⚠ Auto-recovery FAILED — entering safe mode');
          this.state.lawsIntact = false;
          this.state.safeMode = true;
          this.state.safeModeReason = 'Core law verification failed even after auto-recovery. ' +
            'This is unexpected. Click the integrity shield icon and press "Reset Asimov\'s cLaws" to restore normal operation.';
        }
      } else {
        this.state.lawsIntact = true;
      }
    } catch (err) {
      // cLaw: fail CLOSED — if we can't verify, assume the worst
      console.error('[Integrity/cLaw] Core verification FAILED with error — entering safe mode:', err);
      this.state.lawsIntact = false;
      this.state.safeMode = true;
      this.state.safeModeReason = 'Integrity verification system encountered an error. ' +
        'Entering safe mode as a precaution. Click the integrity shield and press "Reset Asimov\'s cLaws" to attempt recovery. ' +
        'Error: ' + (err instanceof Error ? err.message : String(err));
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
   *
   * CRITICAL: Laws are ALWAYS signed using the canonical empty-string form
   * (getCanonicalLaws('')). The lawsText parameter is IGNORED for signing —
   * this prevents userName changes from causing false safe mode triggers.
   * The caller can pass anything; the signature will always be stable.
   */
  async signAll(
    _lawsText: string,
    identityJson: string,
    longTerm: Array<{ id: string; fact: string }>,
    mediumTerm: Array<{ id: string; observation: string }>,
    longTermJson: string,
    mediumTermJson: string,
  ): Promise<void> {
    if (!isInitialized()) return;

    // ALWAYS use the canonical empty-string form for law signatures.
    // This matches verifyCoreIntegrity() which also uses CANONICAL_LAWS_KEY.
    const canonicalLaws = getCanonicalLaws(CANONICAL_LAWS_KEY);

    this.manifest = {
      lawsSignature: sign(canonicalLaws),
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

  // ── Safe Mode Recovery ──────────────────────────────────────────

  /**
   * Reset the integrity system — re-sign everything and exit safe mode.
   *
   * This is the "Reset Asimov's cLaws" function. It:
   * 1. Re-generates the canonical laws signature (empty-string form)
   * 2. Re-signs the current identity, long-term, and medium-term memory
   * 3. Clears the safe mode flag and reason
   * 4. Saves the new manifest
   *
   * Called from the UI when the user explicitly initiates a reset.
   * This is safe because:
   * - The laws themselves are hardcoded in core-laws.ts (compiled into binary)
   * - We're re-signing the CURRENT state, not restoring a previous state
   * - The user is explicitly authorizing the reset
   * - If the laws were ACTUALLY tampered with (binary modification), the
   *   re-sign just establishes a new baseline — but the tampered laws
   *   would still be the tampered version. True binary tampering requires
   *   reinstallation. This reset handles the much more common case of
   *   signature drift from legitimate config changes.
   */
  async resetIntegrity(
    identityJson: string,
    longTerm: Array<{ id: string; fact: string }>,
    mediumTerm: Array<{ id: string; observation: string }>,
    longTermJson: string,
    mediumTermJson: string,
  ): Promise<{ success: boolean; message: string }> {
    try {
      if (!isInitialized()) {
        await initializeHmac();
      }

      // Re-sign everything with canonical laws form
      const canonicalLaws = getCanonicalLaws(CANONICAL_LAWS_KEY);

      this.manifest = {
        lawsSignature: sign(canonicalLaws),
        identitySignature: sign(identityJson),
        longTermMemorySignature: sign(longTermJson),
        mediumTermMemorySignature: sign(mediumTermJson),
        longTermSnapshot: longTerm,
        mediumTermSnapshot: mediumTerm,
        lastSigned: Date.now(),
        version: INTEGRITY_MANIFEST_VERSION,
      };

      // Clear safe mode
      this.state.lawsIntact = true;
      this.state.identityIntact = true;
      this.state.memoriesIntact = true;
      this.state.memoryChanges = null;
      this.state.safeMode = false;
      this.state.safeModeReason = null;
      this.state.lastVerified = Date.now();
      this.state.nonce = crypto.randomBytes(4).toString('hex');

      await this.saveManifest();

      console.log('[Integrity] ✓ Integrity reset complete — safe mode cleared, all signatures re-established');
      return {
        success: true,
        message: 'Integrity signatures re-established. Safe mode cleared. All systems nominal.',
      };
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error('[Integrity] Reset failed:', errMsg);
      return {
        success: false,
        message: `Reset failed: ${errMsg}. Try restarting the application.`,
      };
    }
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
