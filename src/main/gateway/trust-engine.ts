/**
 * gateway/trust-engine.ts — Trust & Security Engine for the messaging gateway.
 *
 * Implements Asimov's cLaws capability gating:
 *   - First Law:  No data leakage across trust boundaries
 *   - Second Law: Authenticate the principal (pairing flow)
 *   - Third Law:  Gateway self-protection (rate limiting, audit)
 *
 * Each sender is resolved to a TrustTier, which maps to a TrustPolicy
 * that gates tool access, memory permissions, and iteration limits.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import {
  TrustTier,
  TrustPolicy,
  PairedIdentity,
  PendingPairing,
} from './types';

// ── Trust Policies ───────────────────────────────────────────────────
// Static policy definitions for each trust tier.

const TRUST_POLICIES: Record<TrustTier, TrustPolicy> = {
  local: {
    tier: 'local',
    maxIterations: 25,
    toolAllowPatterns: ['*'],
    toolBlockPatterns: [],
    memoryRead: true,
    memoryWrite: true,
    canTriggerScheduler: true,
    canAccessDesktop: true,
    rateLimitPerMinute: 999,
  },
  'owner-dm': {
    tier: 'owner-dm',
    maxIterations: 15,
    toolAllowPatterns: ['*'],
    toolBlockPatterns: [
      'ui_automation_*',
      'system_management_*',
      'run_powershell',
      'execute_powershell',
    ],
    memoryRead: true,
    memoryWrite: true,
    canTriggerScheduler: true,
    canAccessDesktop: false,
    rateLimitPerMinute: 30,
  },
  'approved-dm': {
    tier: 'approved-dm',
    maxIterations: 8,
    toolAllowPatterns: [
      'firecrawl_*',
      'web_search',
      'scrape_url',
      'calendar_get_*',
      'draft_communication',
      'gateway_send_message',
    ],
    toolBlockPatterns: [
      'powershell_*',
      'run_powershell',
      'execute_powershell',
      'run_command',
      'terminal_*',
      'ui_automation_*',
      'system_management_*',
      'vscode_*',
      'git_*',
      'docker_*',
      'office_*',
      'adobe_*',
    ],
    memoryRead: true,
    memoryWrite: false,
    canTriggerScheduler: false,
    canAccessDesktop: false,
    rateLimitPerMinute: 10,
  },
  group: {
    tier: 'group',
    maxIterations: 5,
    toolAllowPatterns: ['firecrawl_*', 'web_search', 'scrape_url'],
    toolBlockPatterns: ['*'],  // Block all, then allow specific ones
    memoryRead: false,
    memoryWrite: false,
    canTriggerScheduler: false,
    canAccessDesktop: false,
    rateLimitPerMinute: 5,
  },
  public: {
    tier: 'public',
    maxIterations: 0,
    toolAllowPatterns: [],
    toolBlockPatterns: ['*'],
    memoryRead: false,
    memoryWrite: false,
    canTriggerScheduler: false,
    canAccessDesktop: false,
    rateLimitPerMinute: 3,
  },
};

// ── Pairing Code Config ──────────────────────────────────────────────

/**
 * cLaw Security Fix (LOW-003): Increased from 6 to 8 characters.
 * 6 chars × log2(32) = ~25 bits — brute-forceable in minutes.
 * 8 chars × log2(32) = ~40 bits — requires billions of attempts.
 */
const PAIRING_CODE_LENGTH = 8;
const PAIRING_CODE_EXPIRY_MS = 15 * 60 * 1000; // 15 minutes
const CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // No I/O/0/1 to avoid confusion

// ── Rate Limiter ─────────────────────────────────────────────────────

interface RateLimitEntry {
  timestamps: number[];
}

// ── Trust Engine ─────────────────────────────────────────────────────

class TrustEngine {
  private identitiesPath = '';
  private identities: PairedIdentity[] = [];
  private pendingPairings: Map<string, PendingPairing> = new Map();
  private rateLimits: Map<string, RateLimitEntry> = new Map();
  private ownerIds: Map<string, string> = new Map(); // channel → ownerId
  private rateLimitSweepTimer: ReturnType<typeof setInterval> | null = null;

  async initialize(): Promise<void> {
    // Crypto Sprint 7 (CRITICAL): Periodic sweep of stale rateLimits entries
    this.rateLimitSweepTimer = setInterval(() => this.sweepRateLimits(), 60_000);
    const gatewayDir = path.join(app.getPath('userData'), 'gateway');
    await fs.mkdir(gatewayDir, { recursive: true });
    this.identitiesPath = path.join(gatewayDir, 'identities.json');

    try {
      // Vault-aware read: decrypts if vault is unlocked, falls back to plaintext
      const { vaultRead } = require('../vault');
      const data = await vaultRead(this.identitiesPath);
      this.identities = JSON.parse(data);
      console.log(`[TrustEngine] Loaded ${this.identities.length} paired identities`);
    } catch {
      this.identities = [];
    }
  }

  /**
   * Set the owner's sender ID for a channel (from settings).
   * Owner DMs get automatic `owner-dm` trust.
   */
  setOwner(channel: string, senderId: string): void {
    if (senderId) {
      this.ownerIds.set(channel, senderId);
    }
  }

  /**
   * Resolve a sender's trust tier.
   * Priority: owner check → paired identity lookup → public
   *
   * cLaw Safety: fails CLOSED to 'public' (most restrictive) on ANY error.
   */
  resolveTrust(channel: string, senderId: string): TrustTier {
    try {
      // Check if this is the owner
      const ownerId = this.ownerIds.get(channel);
      if (ownerId && ownerId === senderId) {
        return 'owner-dm';
      }

      // Check paired identities
      const identity = this.identities.find(
        (id) => id.channel === channel && id.senderId === senderId
      );
      if (identity) {
        return identity.tier;
      }

      return 'public';
    } catch (err) {
      // Crypto Sprint 17: Sanitize error output.
      // cLaw: fail CLOSED — most restrictive tier on any error
      console.error('[TrustEngine/cLaw] resolveTrust failed, defaulting to public (most restrictive):', err instanceof Error ? err.message : 'Unknown error');
      return 'public';
    }
  }

  /**
   * Get the capability policy for a trust tier.
   */
  getPolicy(tier: TrustTier): TrustPolicy {
    return TRUST_POLICIES[tier];
  }

  /**
   * Filter tools based on a trust policy's allow/block patterns.
   * Uses simple glob matching: '*' matches everything, 'prefix_*' matches prefix.
   */
  filterTools<T extends { name: string }>(tools: T[], policy: TrustPolicy): T[] {
    if (policy.tier === 'local') return tools;
    if (policy.tier === 'public') return [];

    return tools.filter((tool) => {
      // cLaw Security Fix (HIGH-003): For whitelist-only tiers (group), allow patterns
      // carve exceptions from the default-deny block list. For all other tiers,
      // explicit block patterns override allow patterns.
      const allowed = this.matchesAnyPattern(tool.name, policy.toolAllowPatterns);
      const blocked = this.matchesAnyPattern(tool.name, policy.toolBlockPatterns);

      if (policy.tier === 'group') {
        // Whitelist-only: tool must explicitly match an allow pattern to pass.
        // The block pattern ['*'] is the default-deny; allow patterns are exceptions.
        return allowed;
      }

      // For other tiers: explicit block patterns always take precedence over allow
      if (blocked) return false;
      return allowed;
    });
  }

  /**
   * Crypto Sprint 7 (CRITICAL): Sweep stale entries from the rateLimits map.
   * Entries with no recent timestamps are deleted to prevent unbounded growth
   * from unique sender IDs flooding the gateway.
   */
  private sweepRateLimits(): void {
    const now = Date.now();
    const staleThresholdMs = 300_000; // 5 minutes
    for (const [key, entry] of this.rateLimits) {
      const newest = entry.timestamps.length > 0
        ? Math.max(...entry.timestamps)
        : 0;
      if (now - newest > staleThresholdMs) {
        this.rateLimits.delete(key);
      }
    }
  }

  /**
   * Check rate limiting for a sender. Returns true if allowed, false if rate-limited.
   */
  checkRateLimit(senderId: string, policy: TrustPolicy): boolean {
    const now = Date.now();
    const windowMs = 60_000; // 1 minute window

    // Crypto Sprint 7 (CRITICAL): Hard cap — reject if map is too large (DoS protection)
    if (!this.rateLimits.has(senderId) && this.rateLimits.size >= 10_000) {
      console.warn('[TrustEngine] Rate limit map at capacity (10k entries) — rejecting new sender');
      return false;
    }

    let entry = this.rateLimits.get(senderId);
    if (!entry) {
      entry = { timestamps: [] };
      this.rateLimits.set(senderId, entry);
    }

    // Prune old timestamps
    entry.timestamps = entry.timestamps.filter((ts) => now - ts < windowMs);

    if (entry.timestamps.length >= policy.rateLimitPerMinute) {
      return false;
    }

    entry.timestamps.push(now);
    return true;
  }

  // ── Pairing Flow ─────────────────────────────────────────────────

  /**
   * Generate a pairing code for an unknown sender.
   * Returns the code to send back to them.
   */
  generatePairingCode(channel: string, senderId: string, senderName: string): string {
    // Check if there's already a pending pairing for this sender
    for (const [code, pending] of this.pendingPairings) {
      if (pending.channel === channel && pending.senderId === senderId) {
        if (pending.expiresAt > Date.now()) {
          return code; // Return existing valid code
        }
        this.pendingPairings.delete(code); // Expired, remove it
      }
    }

    // Generate new code
    const code = this.generateCode();
    this.pendingPairings.set(code, {
      code,
      channel,
      senderId,
      senderName,
      createdAt: Date.now(),
      expiresAt: Date.now() + PAIRING_CODE_EXPIRY_MS,
    });

    // Clean up expired pairings
    this.cleanExpiredPairings();

    return code;
  }

  /**
   * Get all pending pairings (for display in the Electron UI).
   */
  getPendingPairings(): PendingPairing[] {
    this.cleanExpiredPairings();
    return Array.from(this.pendingPairings.values());
  }

  /**
   * Approve a pairing code entered in the Electron UI.
   * Returns the paired identity, or null if the code is invalid/expired.
   */
  async approvePairing(
    code: string,
    tier: TrustTier = 'approved-dm'
  ): Promise<PairedIdentity | null> {
    const normalizedCode = code.toUpperCase().replace(/[^A-Z0-9]/g, '');
    const pending = this.pendingPairings.get(normalizedCode);

    if (!pending || pending.expiresAt < Date.now()) {
      return null;
    }

    // Create paired identity
    const identity: PairedIdentity = {
      id: crypto.randomUUID(),
      channel: pending.channel,
      senderId: pending.senderId,
      name: pending.senderName,
      tier,
      pairedAt: Date.now(),
    };

    // Remove any existing pairing for this channel+sender
    this.identities = this.identities.filter(
      (id) => !(id.channel === pending.channel && id.senderId === pending.senderId)
    );

    this.identities.push(identity);
    this.pendingPairings.delete(normalizedCode);
    await this.saveIdentities();

    console.log(
      `[TrustEngine] Paired ${pending.senderName} (${pending.channel}:${pending.senderId}) as ${tier}`
    );

    return identity;
  }

  /**
   * Revoke a pairing by identity ID.
   */
  async revokePairing(identityId: string): Promise<boolean> {
    const before = this.identities.length;
    this.identities = this.identities.filter((id) => id.id !== identityId);
    if (this.identities.length < before) {
      await this.saveIdentities();
      return true;
    }
    return false;
  }

  /**
   * Get all paired identities.
   */
  getPairedIdentities(): PairedIdentity[] {
    return [...this.identities];
  }

  // ── Private Helpers ──────────────────────────────────────────────

  private matchesAnyPattern(name: string, patterns: string[]): boolean {
    for (const pattern of patterns) {
      if (pattern === '*') return true;
      if (pattern.endsWith('*')) {
        const prefix = pattern.slice(0, -1);
        if (name.startsWith(prefix)) return true;
      } else if (pattern === name) {
        return true;
      }
    }
    return false;
  }

  private generateCode(): string {
    let code = '';
    for (let i = 0; i < PAIRING_CODE_LENGTH; i++) {
      code += CODE_CHARS[crypto.randomInt(CODE_CHARS.length)];
    }
    // Format as XXX-XXX for readability
    return code.slice(0, 3) + code.slice(3);
  }

  private cleanExpiredPairings(): void {
    const now = Date.now();
    for (const [code, pending] of this.pendingPairings) {
      if (pending.expiresAt < now) {
        this.pendingPairings.delete(code);
      }
    }
  }

  private async saveIdentities(): Promise<void> {
    try {
      // Vault-aware write: encrypts if vault is unlocked, falls back to plaintext
      const { vaultWrite } = require('../vault');
      await vaultWrite(
        this.identitiesPath,
        JSON.stringify(this.identities, null, 2),
      );
    } catch (err) {
      // Crypto Sprint 16: Sanitize — trust engine errors may contain crypto material.
      console.warn('[TrustEngine] Failed to save identities:', err instanceof Error ? err.message : 'Unknown error');
    }
  }
}

export const trustEngine = new TrustEngine();
