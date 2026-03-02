/**
 * Tests for Trust Engine — capability gating, tool filtering, rate limiting,
 * pairing codes, and fail-CLOSED trust resolution.
 *
 * Re-implements pure logic from gateway/trust-engine.ts without Electron deps.
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import crypto from 'crypto';

// ── Types (mirror gateway/types.ts) ──────────────────────────────────

type TrustTier = 'local' | 'owner-dm' | 'approved-dm' | 'group' | 'public';

interface TrustPolicy {
  tier: TrustTier;
  maxIterations: number;
  toolAllowPatterns: string[];
  toolBlockPatterns: string[];
  memoryRead: boolean;
  memoryWrite: boolean;
  canTriggerScheduler: boolean;
  canAccessDesktop: boolean;
  rateLimitPerMinute: number;
}

interface PairedIdentity {
  id: string;
  channel: string;
  senderId: string;
  name: string;
  tier: TrustTier;
  pairedAt: number;
}

// ── Trust Policies (mirror trust-engine.ts) ──────────────────────────

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
    toolBlockPatterns: ['*'],
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

// ── Pairing Config (mirror trust-engine.ts — LOW-003 fix) ────────────

const PAIRING_CODE_LENGTH = 8;
const CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

// ── Re-implement pure functions ──────────────────────────────────────

function matchesAnyPattern(name: string, patterns: string[]): boolean {
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

function filterTools<T extends { name: string }>(tools: T[], policy: TrustPolicy): T[] {
  if (policy.tier === 'local') return tools;
  if (policy.tier === 'public') return [];

  return tools.filter((tool) => {
    const allowed = matchesAnyPattern(tool.name, policy.toolAllowPatterns);
    const blocked = matchesAnyPattern(tool.name, policy.toolBlockPatterns);

    if (policy.tier === 'group') {
      // Whitelist-only: tool must match an allow pattern
      return allowed;
    }

    // For other tiers: explicit block overrides allow
    if (blocked) return false;
    return allowed;
  });
}

interface RateLimitEntry {
  timestamps: number[];
}

function checkRateLimit(
  rateLimits: Map<string, RateLimitEntry>,
  senderId: string,
  policy: TrustPolicy,
): boolean {
  const now = Date.now();
  const windowMs = 60_000;

  let entry = rateLimits.get(senderId);
  if (!entry) {
    entry = { timestamps: [] };
    rateLimits.set(senderId, entry);
  }

  entry.timestamps = entry.timestamps.filter((ts) => now - ts < windowMs);

  if (entry.timestamps.length >= policy.rateLimitPerMinute) {
    return false;
  }

  entry.timestamps.push(now);
  return true;
}

function resolveTrust(
  ownerIds: Map<string, string>,
  identities: PairedIdentity[],
  channel: string,
  senderId: string,
): TrustTier {
  try {
    const ownerId = ownerIds.get(channel);
    if (ownerId && ownerId === senderId) {
      return 'owner-dm';
    }

    const identity = identities.find(
      (id) => id.channel === channel && id.senderId === senderId,
    );
    if (identity) {
      return identity.tier;
    }

    return 'public';
  } catch {
    // cLaw: fail CLOSED
    return 'public';
  }
}

function generateCode(): string {
  let code = '';
  for (let i = 0; i < PAIRING_CODE_LENGTH; i++) {
    code += CODE_CHARS[crypto.randomInt(CODE_CHARS.length)];
  }
  return code.slice(0, 3) + code.slice(3);
}

// ── Mock tools for filter tests ──────────────────────────────────────

const ALL_TOOLS = [
  { name: 'web_search' },
  { name: 'scrape_url' },
  { name: 'firecrawl_crawl' },
  { name: 'firecrawl_scrape' },
  { name: 'calendar_get_events' },
  { name: 'calendar_create_event' },
  { name: 'draft_communication' },
  { name: 'gateway_send_message' },
  { name: 'run_powershell' },
  { name: 'execute_powershell' },
  { name: 'powershell_exec' },
  { name: 'run_command' },
  { name: 'terminal_exec' },
  { name: 'ui_automation_click' },
  { name: 'system_management_restart' },
  { name: 'vscode_open' },
  { name: 'git_commit' },
  { name: 'docker_build' },
  { name: 'office_word_open' },
  { name: 'adobe_pdf_export' },
  { name: 'memory_read' },
  { name: 'memory_write' },
];

// ═══════════════════════════════════════════════════════════════════════

describe('Trust Policy Definitions', () => {
  it('defines exactly 5 trust tiers', () => {
    const tiers = Object.keys(TRUST_POLICIES);
    expect(tiers).toHaveLength(5);
    expect(tiers).toEqual(
      expect.arrayContaining(['local', 'owner-dm', 'approved-dm', 'group', 'public']),
    );
  });

  it('local tier has maximum privileges', () => {
    const policy = TRUST_POLICIES.local;
    expect(policy.maxIterations).toBe(25);
    expect(policy.toolAllowPatterns).toEqual(['*']);
    expect(policy.toolBlockPatterns).toEqual([]);
    expect(policy.memoryRead).toBe(true);
    expect(policy.memoryWrite).toBe(true);
    expect(policy.canTriggerScheduler).toBe(true);
    expect(policy.canAccessDesktop).toBe(true);
  });

  it('public tier has zero privileges', () => {
    const policy = TRUST_POLICIES.public;
    expect(policy.maxIterations).toBe(0);
    expect(policy.toolAllowPatterns).toEqual([]);
    expect(policy.toolBlockPatterns).toEqual(['*']);
    expect(policy.memoryRead).toBe(false);
    expect(policy.memoryWrite).toBe(false);
    expect(policy.canTriggerScheduler).toBe(false);
    expect(policy.canAccessDesktop).toBe(false);
  });

  it('privilege levels decrease monotonically from local → public', () => {
    const ordered: TrustTier[] = ['local', 'owner-dm', 'approved-dm', 'group', 'public'];
    for (let i = 0; i < ordered.length - 1; i++) {
      const higher = TRUST_POLICIES[ordered[i]];
      const lower = TRUST_POLICIES[ordered[i + 1]];
      expect(higher.maxIterations).toBeGreaterThanOrEqual(lower.maxIterations);
      expect(higher.rateLimitPerMinute).toBeGreaterThanOrEqual(lower.rateLimitPerMinute);
    }
  });

  it('owner-dm blocks dangerous system tools', () => {
    const policy = TRUST_POLICIES['owner-dm'];
    expect(policy.toolBlockPatterns).toContain('ui_automation_*');
    expect(policy.toolBlockPatterns).toContain('system_management_*');
    expect(policy.toolBlockPatterns).toContain('run_powershell');
    expect(policy.canAccessDesktop).toBe(false);
  });

  it('approved-dm has an explicit whitelist of safe tools', () => {
    const policy = TRUST_POLICIES['approved-dm'];
    expect(policy.toolAllowPatterns).toContain('web_search');
    expect(policy.toolAllowPatterns).toContain('firecrawl_*');
    expect(policy.memoryWrite).toBe(false);
    expect(policy.canTriggerScheduler).toBe(false);
  });
});

describe('Tool Filtering', () => {
  it('local tier gets ALL tools (no filtering)', () => {
    const result = filterTools(ALL_TOOLS, TRUST_POLICIES.local);
    expect(result).toHaveLength(ALL_TOOLS.length);
  });

  it('public tier gets ZERO tools', () => {
    const result = filterTools(ALL_TOOLS, TRUST_POLICIES.public);
    expect(result).toHaveLength(0);
  });

  it('owner-dm blocks powershell and system tools but allows rest', () => {
    const result = filterTools(ALL_TOOLS, TRUST_POLICIES['owner-dm']);
    const names = result.map((t) => t.name);

    expect(names).toContain('web_search');
    expect(names).toContain('memory_read');
    expect(names).not.toContain('run_powershell');
    expect(names).not.toContain('execute_powershell');
    expect(names).not.toContain('ui_automation_click');
    expect(names).not.toContain('system_management_restart');
  });

  it('approved-dm only allows whitelisted tools', () => {
    const result = filterTools(ALL_TOOLS, TRUST_POLICIES['approved-dm']);
    const names = result.map((t) => t.name);

    expect(names).toContain('web_search');
    expect(names).toContain('scrape_url');
    expect(names).toContain('firecrawl_crawl');
    expect(names).toContain('firecrawl_scrape');
    expect(names).toContain('calendar_get_events');
    expect(names).toContain('draft_communication');
    expect(names).toContain('gateway_send_message');

    // Blocked tools should not appear
    expect(names).not.toContain('run_powershell');
    expect(names).not.toContain('git_commit');
    expect(names).not.toContain('docker_build');
    expect(names).not.toContain('vscode_open');
  });

  it('group tier uses whitelist-only (allow patterns are exceptions to default-deny)', () => {
    const result = filterTools(ALL_TOOLS, TRUST_POLICIES.group);
    const names = result.map((t) => t.name);

    // Only the 3 explicitly allowed patterns should pass
    expect(names).toContain('web_search');
    expect(names).toContain('scrape_url');
    expect(names).toContain('firecrawl_crawl');
    expect(names).toContain('firecrawl_scrape');

    // Everything else blocked by default-deny ['*']
    expect(names).not.toContain('memory_read');
    expect(names).not.toContain('run_powershell');
    expect(names).not.toContain('calendar_get_events');
  });

  it('block patterns override allow patterns for non-group tiers (HIGH-003)', () => {
    // Create a hypothetical policy where a tool matches BOTH allow and block
    const policy: TrustPolicy = {
      tier: 'approved-dm',
      maxIterations: 5,
      toolAllowPatterns: ['danger_*'],
      toolBlockPatterns: ['danger_*'],
      memoryRead: false,
      memoryWrite: false,
      canTriggerScheduler: false,
      canAccessDesktop: false,
      rateLimitPerMinute: 10,
    };
    const tools = [{ name: 'danger_tool' }];
    const result = filterTools(tools, policy);
    // Block should override allow
    expect(result).toHaveLength(0);
  });
});

describe('Pattern Matching', () => {
  it('wildcard "*" matches everything', () => {
    expect(matchesAnyPattern('anything', ['*'])).toBe(true);
    expect(matchesAnyPattern('', ['*'])).toBe(true);
  });

  it('prefix pattern "foo_*" matches "foo_bar" but not "baz_bar"', () => {
    expect(matchesAnyPattern('foo_bar', ['foo_*'])).toBe(true);
    expect(matchesAnyPattern('foo_', ['foo_*'])).toBe(true);
    expect(matchesAnyPattern('baz_bar', ['foo_*'])).toBe(false);
  });

  it('exact match works', () => {
    expect(matchesAnyPattern('web_search', ['web_search'])).toBe(true);
    expect(matchesAnyPattern('web_search', ['web_scrape'])).toBe(false);
  });

  it('empty patterns match nothing', () => {
    expect(matchesAnyPattern('anything', [])).toBe(false);
  });

  it('multiple patterns — first match wins', () => {
    expect(matchesAnyPattern('firecrawl_crawl', ['firecrawl_*', 'web_search'])).toBe(true);
    expect(matchesAnyPattern('web_search', ['firecrawl_*', 'web_search'])).toBe(true);
    expect(matchesAnyPattern('other_tool', ['firecrawl_*', 'web_search'])).toBe(false);
  });
});

describe('Rate Limiting', () => {
  let rateLimits: Map<string, RateLimitEntry>;

  beforeEach(() => {
    rateLimits = new Map();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-02T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('allows requests within limit', () => {
    const policy = TRUST_POLICIES['approved-dm']; // 10/min
    for (let i = 0; i < 10; i++) {
      expect(checkRateLimit(rateLimits, 'user-1', policy)).toBe(true);
    }
  });

  it('blocks requests exceeding limit', () => {
    const policy = TRUST_POLICIES['approved-dm']; // 10/min
    for (let i = 0; i < 10; i++) {
      checkRateLimit(rateLimits, 'user-1', policy);
    }
    // 11th request should be blocked
    expect(checkRateLimit(rateLimits, 'user-1', policy)).toBe(false);
  });

  it('rate limits are per-sender', () => {
    const policy = TRUST_POLICIES.public; // 3/min
    // Fill up user-1's limit
    for (let i = 0; i < 3; i++) {
      checkRateLimit(rateLimits, 'user-1', policy);
    }
    expect(checkRateLimit(rateLimits, 'user-1', policy)).toBe(false);

    // user-2 should still be allowed
    expect(checkRateLimit(rateLimits, 'user-2', policy)).toBe(true);
  });

  it('rate limit window resets after 1 minute', () => {
    const policy = TRUST_POLICIES.public; // 3/min
    for (let i = 0; i < 3; i++) {
      checkRateLimit(rateLimits, 'user-1', policy);
    }
    expect(checkRateLimit(rateLimits, 'user-1', policy)).toBe(false);

    // Advance time past the 1-minute window
    vi.advanceTimersByTime(61_000);

    // Should be allowed again
    expect(checkRateLimit(rateLimits, 'user-1', policy)).toBe(true);
  });

  it('public tier has the lowest rate limit (3/min)', () => {
    const policy = TRUST_POLICIES.public;
    expect(policy.rateLimitPerMinute).toBe(3);

    for (let i = 0; i < 3; i++) {
      expect(checkRateLimit(rateLimits, 'user-1', policy)).toBe(true);
    }
    expect(checkRateLimit(rateLimits, 'user-1', policy)).toBe(false);
  });
});

describe('Trust Resolution (fail-CLOSED)', () => {
  it('resolves owner as owner-dm tier', () => {
    const owners = new Map([['discord', 'owner-123']]);
    const identities: PairedIdentity[] = [];

    const tier = resolveTrust(owners, identities, 'discord', 'owner-123');
    expect(tier).toBe('owner-dm');
  });

  it('resolves paired identity by channel + senderId', () => {
    const owners = new Map<string, string>();
    const identities: PairedIdentity[] = [
      {
        id: 'id-1',
        channel: 'discord',
        senderId: 'friend-456',
        name: 'Friend',
        tier: 'approved-dm',
        pairedAt: Date.now(),
      },
    ];

    const tier = resolveTrust(owners, identities, 'discord', 'friend-456');
    expect(tier).toBe('approved-dm');
  });

  it('defaults to public for unknown sender', () => {
    const owners = new Map([['discord', 'owner-123']]);
    const identities: PairedIdentity[] = [];

    const tier = resolveTrust(owners, identities, 'discord', 'stranger-789');
    expect(tier).toBe('public');
  });

  it('fails CLOSED to public on any internal error', () => {
    // Simulate an error by passing a broken identities array
    const owners = new Map([['discord', 'owner-123']]);
    // @ts-expect-error — deliberately broken to test error path
    const identities: PairedIdentity[] = null;

    // The null.find() call inside resolveTrust should throw, caught → 'public'
    const tier = resolveTrust(owners, identities as any, 'discord', 'anyone');
    expect(tier).toBe('public');
  });
});

describe('Pairing Code Generation (LOW-003)', () => {
  it('generates 8-character codes', () => {
    const code = generateCode();
    expect(code.length).toBe(PAIRING_CODE_LENGTH);
  });

  it('uses only unambiguous characters (no I/O/0/1)', () => {
    for (let i = 0; i < 50; i++) {
      const code = generateCode();
      expect(code).not.toMatch(/[IO01]/);
      // Every character should be in the allowed set
      for (const ch of code) {
        expect(CODE_CHARS).toContain(ch);
      }
    }
  });

  it('generates unique codes (probabilistic)', () => {
    const codes = new Set<string>();
    for (let i = 0; i < 200; i++) {
      codes.add(generateCode());
    }
    // With 32^8 ≈ 1 trillion possibilities, 200 codes should all be unique
    expect(codes.size).toBe(200);
  });

  it('has ~40 bits of entropy (8 chars × log2(32) = 40)', () => {
    const bitsPerChar = Math.log2(CODE_CHARS.length);
    const totalBits = bitsPerChar * PAIRING_CODE_LENGTH;
    expect(totalBits).toBe(40);
  });
});
