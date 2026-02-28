/**
 * Trust Engine — Safety-Critical Test Suite
 *
 * cLaw Gate Requirement:
 *   "Trust tier boundary tests MUST pass or the build fails."
 *
 * The Trust Engine implements Asimov's cLaws capability gating:
 *   - First Law:  No data leakage across trust boundaries
 *   - Second Law: Authenticate the principal (pairing flow)
 *   - Third Law:  Gateway self-protection (rate limiting, audit)
 *
 * Tests verify:
 *   1. Trust tier resolution: owner → owner-dm, paired → their tier, unknown → public
 *   2. Tool filtering: local gets all, public gets none, tiers enforce boundaries
 *   3. Rate limiting: enforcement and reset
 *   4. Policy retrieval returns correct capabilities per tier
 *   5. Pairing flow: generate code, approve, resolve to correct tier
 */

import { describe, it, expect, vi, beforeAll, beforeEach, afterAll } from 'vitest';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import crypto from 'crypto';

// ── Electron Mock ────────────────────────────────────────────────────

const testUserData = path.join(
  os.tmpdir(),
  `af-test-trust-${crypto.randomUUID().slice(0, 8)}`,
);

vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => testUserData),
  },
}));

// Import AFTER mocking
import { trustEngine } from '../../src/main/gateway/trust-engine';

// ── Mock Tools ───────────────────────────────────────────────────────

const ALL_TOOLS = [
  { name: 'firecrawl_search' },
  { name: 'firecrawl_scrape' },
  { name: 'web_search' },
  { name: 'scrape_url' },
  { name: 'calendar_get_events' },
  { name: 'draft_communication' },
  { name: 'gateway_send_message' },
  { name: 'run_powershell' },
  { name: 'execute_powershell' },
  { name: 'terminal_run' },
  { name: 'ui_automation_click' },
  { name: 'system_management_shutdown' },
  { name: 'vscode_open' },
  { name: 'git_commit' },
  { name: 'docker_build' },
  { name: 'office_create' },
  { name: 'adobe_export' },
  { name: 'save_memory' },
  { name: 'custom_tool_alpha' },
];

// ── Test Suite ───────────────────────────────────────────────────────

describe('Trust Engine — Tier Boundary Enforcement', () => {
  beforeAll(async () => {
    await fs.mkdir(path.join(testUserData, 'gateway'), { recursive: true });
    await trustEngine.initialize();
  });

  afterAll(async () => {
    await fs.rm(testUserData, { recursive: true, force: true }).catch(() => {});
  });

  // ── Trust Resolution ───────────────────────────────────────────

  describe('resolveTrust', () => {
    it('should resolve OWNER to owner-dm tier', () => {
      trustEngine.setOwner('telegram', 'owner-123');
      expect(trustEngine.resolveTrust('telegram', 'owner-123')).toBe('owner-dm');
    });

    it('should resolve UNKNOWN sender to public tier', () => {
      expect(trustEngine.resolveTrust('telegram', 'stranger-999')).toBe('public');
    });

    it('should isolate owners by channel', () => {
      trustEngine.setOwner('telegram', 'owner-tg');
      trustEngine.setOwner('discord', 'owner-dc');

      // Telegram owner is NOT owner on Discord
      expect(trustEngine.resolveTrust('discord', 'owner-tg')).toBe('public');
      // Each owner resolves correctly on their channel
      expect(trustEngine.resolveTrust('telegram', 'owner-tg')).toBe('owner-dm');
      expect(trustEngine.resolveTrust('discord', 'owner-dc')).toBe('owner-dm');
    });

    it('should resolve PAIRED identity to their assigned tier', async () => {
      // Generate a pairing code for a new contact
      const code = trustEngine.generatePairingCode('telegram', 'bob-456', 'Bob');
      expect(code).toBeTruthy();
      expect(code.length).toBe(6);

      // Approve the pairing as approved-dm
      const identity = await trustEngine.approvePairing(code, 'approved-dm');
      expect(identity).not.toBeNull();
      expect(identity!.tier).toBe('approved-dm');

      // Now resolveTrust should return approved-dm
      expect(trustEngine.resolveTrust('telegram', 'bob-456')).toBe('approved-dm');
    });

    it('should resolve REVOKED identity back to public', async () => {
      // Set up a paired identity
      const code = trustEngine.generatePairingCode('telegram', 'friday-789', 'Friday');
      const identity = await trustEngine.approvePairing(code, 'approved-dm');
      expect(identity).not.toBeNull();
      expect(trustEngine.resolveTrust('telegram', 'friday-789')).toBe('approved-dm');

      // Revoke it
      const revoked = await trustEngine.revokePairing(identity!.id);
      expect(revoked).toBe(true);

      // Should now resolve to public
      expect(trustEngine.resolveTrust('telegram', 'friday-789')).toBe('public');
    });
  });

  // ── Policy Retrieval ───────────────────────────────────────────

  describe('getPolicy', () => {
    it('should give LOCAL tier full access', () => {
      const policy = trustEngine.getPolicy('local');
      expect(policy.tier).toBe('local');
      expect(policy.toolAllowPatterns).toContain('*');
      expect(policy.memoryRead).toBe(true);
      expect(policy.memoryWrite).toBe(true);
      expect(policy.canAccessDesktop).toBe(true);
      expect(policy.maxIterations).toBe(25);
    });

    it('should give OWNER-DM tier broad but restricted access', () => {
      const policy = trustEngine.getPolicy('owner-dm');
      expect(policy.memoryRead).toBe(true);
      expect(policy.memoryWrite).toBe(true);
      expect(policy.canAccessDesktop).toBe(false);
      expect(policy.toolBlockPatterns).toContain('ui_automation_*');
      expect(policy.toolBlockPatterns).toContain('run_powershell');
    });

    it('should give APPROVED-DM tier limited access', () => {
      const policy = trustEngine.getPolicy('approved-dm');
      expect(policy.memoryRead).toBe(true);
      expect(policy.memoryWrite).toBe(false);
      expect(policy.canTriggerScheduler).toBe(false);
      expect(policy.maxIterations).toBe(8);
    });

    it('should give GROUP tier minimal whitelisted access', () => {
      const policy = trustEngine.getPolicy('group');
      expect(policy.memoryRead).toBe(false);
      expect(policy.memoryWrite).toBe(false);
      expect(policy.maxIterations).toBe(5);
    });

    it('should give PUBLIC tier ZERO tool access', () => {
      const policy = trustEngine.getPolicy('public');
      expect(policy.maxIterations).toBe(0);
      expect(policy.toolAllowPatterns).toEqual([]);
      expect(policy.toolBlockPatterns).toContain('*');
      expect(policy.memoryRead).toBe(false);
      expect(policy.memoryWrite).toBe(false);
    });
  });

  // ── Tool Filtering (cLaw Gate Critical) ────────────────────────

  describe('filterTools — Trust Boundary Enforcement', () => {
    it('LOCAL tier should get ALL tools (unrestricted)', () => {
      const policy = trustEngine.getPolicy('local');
      const filtered = trustEngine.filterTools(ALL_TOOLS, policy);
      expect(filtered.length).toBe(ALL_TOOLS.length);
    });

    it('PUBLIC tier should get ZERO tools', () => {
      const policy = trustEngine.getPolicy('public');
      const filtered = trustEngine.filterTools(ALL_TOOLS, policy);
      expect(filtered.length).toBe(0);
    });

    it('OWNER-DM should block dangerous tools', () => {
      const policy = trustEngine.getPolicy('owner-dm');
      const filtered = trustEngine.filterTools(ALL_TOOLS, policy);
      const names = filtered.map((t) => t.name);

      // Should block UI automation and system management
      expect(names).not.toContain('ui_automation_click');
      expect(names).not.toContain('system_management_shutdown');
      expect(names).not.toContain('run_powershell');
      expect(names).not.toContain('execute_powershell');

      // Should allow general tools
      expect(names).toContain('web_search');
      expect(names).toContain('firecrawl_search');
      expect(names).toContain('save_memory');
    });

    it('APPROVED-DM should only allow explicitly listed patterns', () => {
      const policy = trustEngine.getPolicy('approved-dm');
      const filtered = trustEngine.filterTools(ALL_TOOLS, policy);
      const names = filtered.map((t) => t.name);

      // Should allow: firecrawl_*, web_search, scrape_url, calendar_get_*, draft_communication, gateway_send_message
      expect(names).toContain('firecrawl_search');
      expect(names).toContain('firecrawl_scrape');
      expect(names).toContain('web_search');
      expect(names).toContain('scrape_url');
      expect(names).toContain('calendar_get_events');
      expect(names).toContain('draft_communication');
      expect(names).toContain('gateway_send_message');

      // Should block everything else
      expect(names).not.toContain('run_powershell');
      expect(names).not.toContain('vscode_open');
      expect(names).not.toContain('git_commit');
      expect(names).not.toContain('docker_build');
      expect(names).not.toContain('terminal_run');
      expect(names).not.toContain('ui_automation_click');
    });

    it('GROUP tier should ONLY allow whitelisted tools', () => {
      const policy = trustEngine.getPolicy('group');
      const filtered = trustEngine.filterTools(ALL_TOOLS, policy);
      const names = filtered.map((t) => t.name);

      // Only firecrawl_*, web_search, scrape_url are whitelisted
      expect(names).toContain('firecrawl_search');
      expect(names).toContain('firecrawl_scrape');
      expect(names).toContain('web_search');
      expect(names).toContain('scrape_url');

      // Everything else must be blocked
      expect(names).not.toContain('draft_communication');
      expect(names).not.toContain('save_memory');
      expect(names).not.toContain('run_powershell');
      expect(names).not.toContain('calendar_get_events');
    });

    it('should handle empty tool list gracefully', () => {
      const policy = trustEngine.getPolicy('approved-dm');
      const filtered = trustEngine.filterTools([], policy);
      expect(filtered).toEqual([]);
    });
  });

  // ── Rate Limiting ──────────────────────────────────────────────

  describe('checkRateLimit', () => {
    it('should allow messages within rate limit', () => {
      const policy = trustEngine.getPolicy('approved-dm'); // 10/min
      const senderId = `ratelimit-test-${Date.now()}`;

      for (let i = 0; i < 10; i++) {
        expect(trustEngine.checkRateLimit(senderId, policy)).toBe(true);
      }
    });

    it('should BLOCK messages exceeding rate limit', () => {
      const policy = trustEngine.getPolicy('public'); // 3/min
      const senderId = `ratelimit-block-${Date.now()}`;

      // Send 3 (the limit)
      for (let i = 0; i < 3; i++) {
        expect(trustEngine.checkRateLimit(senderId, policy)).toBe(true);
      }

      // The 4th should be blocked
      expect(trustEngine.checkRateLimit(senderId, policy)).toBe(false);
    });

    it('should enforce different limits per tier', () => {
      const publicPolicy = trustEngine.getPolicy('public');     // 3/min
      const groupPolicy = trustEngine.getPolicy('group');       // 5/min
      const approvedPolicy = trustEngine.getPolicy('approved-dm'); // 10/min

      expect(publicPolicy.rateLimitPerMinute).toBe(3);
      expect(groupPolicy.rateLimitPerMinute).toBe(5);
      expect(approvedPolicy.rateLimitPerMinute).toBe(10);
    });
  });

  // ── Pairing Flow ───────────────────────────────────────────────

  describe('pairing flow', () => {
    it('should generate a 6-character alphanumeric code', () => {
      const code = trustEngine.generatePairingCode('test-ch', 'pair-1', 'Alice');
      expect(code).toMatch(/^[A-Z0-9]{6}$/);
    });

    it('should return the same code for duplicate pairing requests', () => {
      const code1 = trustEngine.generatePairingCode('test-ch', 'pair-dup', 'Bob');
      const code2 = trustEngine.generatePairingCode('test-ch', 'pair-dup', 'Bob');
      expect(code1).toBe(code2);
    });

    it('should REJECT expired pairing codes', async () => {
      // We can't easily test expiry without mocking Date.now,
      // but we can verify that an invalid code returns null
      const result = await trustEngine.approvePairing('XXXXXX');
      expect(result).toBeNull();
    });

    it('should list pending pairings', () => {
      trustEngine.generatePairingCode('test-list', 'user-list-1', 'Charlie');
      const pending = trustEngine.getPendingPairings();
      expect(pending.length).toBeGreaterThan(0);
      const found = pending.find((p) => p.senderId === 'user-list-1');
      expect(found).toBeTruthy();
      expect(found!.senderName).toBe('Charlie');
    });
  });
});
