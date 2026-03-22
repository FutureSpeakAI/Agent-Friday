/**
 * Track XI, Phase 6 — Capability Map Tests
 *
 * Tests for the dynamic agent type registry that provides:
 *   - Structured capability metadata
 *   - Capability-based routing via findCapable()
 *   - Runtime registration and deregistration
 *   - Trust-tier-filtered capability views
 *   - Capability gap tracking
 *   - Rich orchestrator prompt context generation
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  capabilityMap,
  AgentCapability,
  CapabilityQuery,
} from '../../src/main/agents/capability-map';

/* ── Helpers ──────────────────────────────────────────────────────────── */

function makeCapability(overrides: Partial<AgentCapability> = {}): AgentCapability {
  return {
    name: overrides.name || 'test-agent',
    description: overrides.description || 'A test agent',
    tags: overrides.tags || ['test'],
    domains: overrides.domains || ['general'],
    inputSchema: overrides.inputSchema || [
      { name: 'input', type: 'string', required: true, description: 'Test input' },
    ],
    outputFormat: overrides.outputFormat || 'Text',
    trustTier: overrides.trustTier || 'local',
    canDelegate: overrides.canDelegate ?? false,
    latency: overrides.latency || 'fast',
    source: overrides.source || 'builtin',
    registeredAt: overrides.registeredAt || Date.now(),
    enabled: overrides.enabled ?? true,
  };
}

/* ── Test Suite ────────────────────────────────────────────────────────── */

describe('CapabilityMap', () => {
  beforeEach(() => {
    capabilityMap.cleanup();
  });

  /* ── Registration ──────────────────────────────────────────────────── */

  describe('Registration', () => {
    it('registers a new capability', () => {
      const cap = makeCapability({ name: 'alpha' });
      capabilityMap.register(cap);

      const retrieved = capabilityMap.get('alpha');
      expect(retrieved).not.toBeNull();
      expect(retrieved!.name).toBe('alpha');
      expect(retrieved!.tags).toEqual(['test']);
    });

    it('updates an existing capability with same name (idempotent)', () => {
      capabilityMap.register(makeCapability({ name: 'beta', tags: ['v1'] }));
      capabilityMap.register(makeCapability({ name: 'beta', tags: ['v2', 'updated'] }));

      const cap = capabilityMap.get('beta');
      expect(cap!.tags).toEqual(['v2', 'updated']);
    });

    it('sets registeredAt to Date.now() if not provided', () => {
      const before = Date.now();
      capabilityMap.register(makeCapability({ name: 'gamma', registeredAt: 0 }));
      const after = Date.now();

      const cap = capabilityMap.get('gamma');
      expect(cap!.registeredAt).toBeGreaterThanOrEqual(before);
      expect(cap!.registeredAt).toBeLessThanOrEqual(after);
    });

    it('registerBuiltins populates from agent type list', () => {
      capabilityMap.registerBuiltins([
        { name: 'research', description: 'Research agent' },
        { name: 'summarize', description: 'Summarize agent' },
        { name: 'code-review', description: 'Code review agent' },
        { name: 'draft-email', description: 'Draft email agent' },
        { name: 'orchestrate', description: 'Orchestration agent' },
      ]);

      expect(capabilityMap.getAll().length).toBe(5);

      // Research should have known tags
      const research = capabilityMap.get('research');
      expect(research).not.toBeNull();
      expect(research!.tags).toContain('research');
      expect(research!.tags).toContain('web-search');
      expect(research!.source).toBe('builtin');
      expect(research!.latency).toBe('slow');
    });

    it('registerBuiltins handles unknown agent types with defaults', () => {
      capabilityMap.registerBuiltins([
        { name: 'custom-agent', description: 'A custom agent' },
      ]);

      const custom = capabilityMap.get('custom-agent');
      expect(custom).not.toBeNull();
      expect(custom!.tags).toEqual([]);
      expect(custom!.domains).toEqual(['general']);
      expect(custom!.latency).toBe('medium');
      expect(custom!.source).toBe('builtin');
    });
  });

  /* ── Deregistration ────────────────────────────────────────────────── */

  describe('Deregistration', () => {
    it('unregisters a capability by name', () => {
      capabilityMap.register(makeCapability({ name: 'doomed' }));
      expect(capabilityMap.get('doomed')).not.toBeNull();

      const removed = capabilityMap.unregister('doomed');
      expect(removed).toBe(true);
      expect(capabilityMap.get('doomed')).toBeNull();
    });

    it('returns false for non-existent unregister', () => {
      expect(capabilityMap.unregister('nonexistent')).toBe(false);
    });
  });

  /* ── Enable/Disable ────────────────────────────────────────────────── */

  describe('Enable/Disable', () => {
    it('disables a capability without removing it', () => {
      capabilityMap.register(makeCapability({ name: 'toggleable' }));
      capabilityMap.setEnabled('toggleable', false);

      const cap = capabilityMap.get('toggleable');
      expect(cap!.enabled).toBe(false);

      // Disabled agents excluded from getAll(enabledOnly=true)
      expect(capabilityMap.getAll(true)).toHaveLength(0);
      // But included in getAll(enabledOnly=false)
      expect(capabilityMap.getAll(false)).toHaveLength(1);
    });

    it('re-enables a disabled capability', () => {
      capabilityMap.register(makeCapability({ name: 'toggled' }));
      capabilityMap.setEnabled('toggled', false);
      capabilityMap.setEnabled('toggled', true);

      expect(capabilityMap.getAll(true)).toHaveLength(1);
    });

    it('setEnabled is a no-op for non-existent capability', () => {
      // Should not throw
      capabilityMap.setEnabled('ghost', false);
    });
  });

  /* ── Queries ────────────────────────────────────────────────────────── */

  describe('Queries', () => {
    it('get returns null for non-existent capability', () => {
      expect(capabilityMap.get('missing')).toBeNull();
    });

    it('getAll returns only enabled capabilities by default', () => {
      capabilityMap.register(makeCapability({ name: 'a', enabled: true }));
      capabilityMap.register(makeCapability({ name: 'b', enabled: false }));
      capabilityMap.register(makeCapability({ name: 'c', enabled: true }));

      expect(capabilityMap.getAll()).toHaveLength(2);
      expect(capabilityMap.getAll(false)).toHaveLength(3);
    });

    it('getAgentTypes returns simplified name/description pairs', () => {
      capabilityMap.register(makeCapability({ name: 'x', description: 'Agent X' }));
      capabilityMap.register(makeCapability({ name: 'y', description: 'Agent Y' }));

      const types = capabilityMap.getAgentTypes();
      expect(types).toHaveLength(2);
      expect(types[0]).toEqual({ name: 'x', description: 'Agent X' });
    });
  });

  /* ── findCapable — Capability-Based Routing ─────────────────────────── */

  describe('findCapable', () => {
    beforeEach(() => {
      capabilityMap.register(makeCapability({
        name: 'researcher',
        description: 'Deep web research and information gathering',
        tags: ['research', 'web-search', 'information-gathering'],
        domains: ['general', 'technology'],
        trustTier: 'local',
        latency: 'slow',
      }));
      capabilityMap.register(makeCapability({
        name: 'writer',
        description: 'Draft emails and written communication',
        tags: ['writing', 'email', 'communication'],
        domains: ['communication', 'business'],
        trustTier: 'local',
        latency: 'fast',
      }));
      capabilityMap.register(makeCapability({
        name: 'coder',
        description: 'Code review and analysis',
        tags: ['code-review', 'code-analysis', 'bugs'],
        domains: ['code', 'software-engineering'],
        trustTier: 'owner-dm',
        latency: 'medium',
      }));
    });

    it('matches by tags (OR matching)', () => {
      const matches = capabilityMap.findCapable({ tags: ['research', 'web-search'] });
      expect(matches.length).toBeGreaterThanOrEqual(1);
      expect(matches[0].capability.name).toBe('researcher');
      expect(matches[0].score).toBeGreaterThan(0);
      expect(matches[0].reason).toContain('tags');
    });

    it('matches by domain', () => {
      const matches = capabilityMap.findCapable({ domain: 'code' });
      expect(matches.length).toBeGreaterThanOrEqual(1);
      expect(matches.some(m => m.capability.name === 'coder')).toBe(true);
    });

    it('matches by free-text need', () => {
      const matches = capabilityMap.findCapable({ need: 'web research about technology' });
      expect(matches.length).toBeGreaterThanOrEqual(1);
      // Researcher should score highest for web research
      expect(matches[0].capability.name).toBe('researcher');
    });

    it('combines tag + domain + need scoring', () => {
      const matches = capabilityMap.findCapable({
        tags: ['research'],
        domain: 'technology',
        need: 'gather information',
      });
      expect(matches.length).toBeGreaterThanOrEqual(1);
      expect(matches[0].capability.name).toBe('researcher');
      // Should have higher score than tag-only match
      expect(matches[0].score).toBeGreaterThan(0.3);
    });

    it('excludes specified agents', () => {
      const matches = capabilityMap.findCapable({
        tags: ['research'],
        exclude: ['researcher'],
      });
      expect(matches.every(m => m.capability.name !== 'researcher')).toBe(true);
    });

    it('filters by source type', () => {
      capabilityMap.register(makeCapability({
        name: 'plugin-agent',
        tags: ['research'],
        source: 'plugin',
      }));

      const matches = capabilityMap.findCapable({
        tags: ['research'],
        sources: ['plugin'],
      });
      expect(matches).toHaveLength(1);
      expect(matches[0].capability.name).toBe('plugin-agent');
    });

    it('returns matches sorted by score descending', () => {
      const matches = capabilityMap.findCapable({ need: 'code review and bugs' });
      for (let i = 1; i < matches.length; i++) {
        expect(matches[i - 1].score).toBeGreaterThanOrEqual(matches[i].score);
      }
    });

    it('scores are capped at 1.0', () => {
      // Create an agent that would match everything
      capabilityMap.register(makeCapability({
        name: 'super-agent',
        description: 'research web-search information-gathering technology code',
        tags: ['research', 'web-search', 'code-review', 'writing'],
        domains: ['general', 'technology', 'code', 'communication'],
      }));

      const matches = capabilityMap.findCapable({
        tags: ['research', 'web-search', 'code-review', 'writing'],
        domain: 'technology',
        need: 'research web technology information code',
      });
      const superMatch = matches.find(m => m.capability.name === 'super-agent');
      expect(superMatch).toBeDefined();
      expect(superMatch!.score).toBeLessThanOrEqual(1);
    });

    it('gives baseline score when query is broad (no tags, no need)', () => {
      const matches = capabilityMap.findCapable({ domain: 'communication' });
      // Writer should match by domain
      const writer = matches.find(m => m.capability.name === 'writer');
      expect(writer).toBeDefined();
    });

    it('skips disabled agents', () => {
      capabilityMap.setEnabled('researcher', false);
      const matches = capabilityMap.findCapable({ tags: ['research'] });
      expect(matches.every(m => m.capability.name !== 'researcher')).toBe(true);
    });
  });

  /* ── Trust-Tier Filtering (cLaw First Law) ──────────────────────────── */

  describe('Trust-Tier Filtering', () => {
    beforeEach(() => {
      capabilityMap.register(makeCapability({
        name: 'local-agent',
        tags: ['sensitive'],
        trustTier: 'local',
      }));
      capabilityMap.register(makeCapability({
        name: 'dm-agent',
        tags: ['sensitive'],
        trustTier: 'owner-dm',
      }));
      capabilityMap.register(makeCapability({
        name: 'public-agent',
        tags: ['sensitive'],
        trustTier: 'public',
      }));
    });

    it('maxTrustTier=local only returns local agents', () => {
      const matches = capabilityMap.findCapable({
        tags: ['sensitive'],
        maxTrustTier: 'local',
      });
      expect(matches).toHaveLength(1);
      expect(matches[0].capability.name).toBe('local-agent');
    });

    it('maxTrustTier=owner-dm returns local and owner-dm agents', () => {
      const matches = capabilityMap.findCapable({
        tags: ['sensitive'],
        maxTrustTier: 'owner-dm',
      });
      expect(matches).toHaveLength(2);
      const names = matches.map(m => m.capability.name).sort();
      expect(names).toEqual(['dm-agent', 'local-agent']);
    });

    it('maxTrustTier=public returns all agents', () => {
      const matches = capabilityMap.findCapable({
        tags: ['sensitive'],
        maxTrustTier: 'public',
      });
      expect(matches).toHaveLength(3);
    });

    it('no maxTrustTier returns all agents (no filtering)', () => {
      const matches = capabilityMap.findCapable({ tags: ['sensitive'] });
      expect(matches).toHaveLength(3);
    });
  });

  /* ── Capability Gap Tracking ────────────────────────────────────────── */

  describe('Capability Gap Tracking', () => {
    it('records a gap when findCapable returns no matches', () => {
      capabilityMap.findCapable({ need: 'underwater basket weaving' });

      const gaps = capabilityMap.getGaps();
      expect(gaps).toHaveLength(1);
      expect(gaps[0].need).toBe('underwater basket weaving');
      expect(gaps[0].hitCount).toBe(1);
    });

    it('increments hit count for repeated gap needs', () => {
      capabilityMap.findCapable({ need: 'quantum computing simulation' });
      capabilityMap.findCapable({ need: 'quantum computing simulation' });
      capabilityMap.findCapable({ need: 'quantum computing simulation' });

      const gaps = capabilityMap.getGaps();
      expect(gaps).toHaveLength(1);
      expect(gaps[0].hitCount).toBe(3);
    });

    it('case-insensitive gap deduplication', () => {
      capabilityMap.findCapable({ need: 'Machine Learning' });
      capabilityMap.findCapable({ need: 'machine learning' });

      const gaps = capabilityMap.getGaps();
      expect(gaps).toHaveLength(1);
      expect(gaps[0].hitCount).toBe(2);
    });

    it('gaps sorted by hit count (most requested first)', () => {
      capabilityMap.findCapable({ need: 'rare need' });
      capabilityMap.findCapable({ need: 'common need' });
      capabilityMap.findCapable({ need: 'common need' });
      capabilityMap.findCapable({ need: 'common need' });
      capabilityMap.findCapable({ need: 'medium need' });
      capabilityMap.findCapable({ need: 'medium need' });

      const gaps = capabilityMap.getGaps();
      expect(gaps[0].need).toBe('common need');
      expect(gaps[1].need).toBe('medium need');
      expect(gaps[2].need).toBe('rare need');
    });

    it('clears a specific gap by ID', () => {
      capabilityMap.findCapable({ need: 'gap to clear' });
      const gaps = capabilityMap.getGaps();
      expect(gaps).toHaveLength(1);

      const cleared = capabilityMap.clearGap(gaps[0].id);
      expect(cleared).toBe(true);
      expect(capabilityMap.getGaps()).toHaveLength(0);
    });

    it('does not record a gap when matches exist', () => {
      capabilityMap.register(makeCapability({ name: 'test-agent', tags: ['test'] }));
      capabilityMap.findCapable({ tags: ['test'] });

      expect(capabilityMap.getGaps()).toHaveLength(0);
    });

    it('does not record a gap when need is not specified', () => {
      capabilityMap.findCapable({ tags: ['nonexistent-tag'] });
      // No need specified → no gap recorded (even though no matches)
      expect(capabilityMap.getGaps()).toHaveLength(0);
    });

    it('caps at 50 gaps (evicts lowest hit count)', () => {
      for (let i = 0; i < 55; i++) {
        capabilityMap.findCapable({ need: `unique gap ${i}` });
      }
      expect(capabilityMap.getGaps().length).toBeLessThanOrEqual(50);
    });
  });

  /* ── Orchestrator Prompt Context ────────────────────────────────────── */

  describe('Orchestrator Prompt Context', () => {
    beforeEach(() => {
      capabilityMap.registerBuiltins([
        { name: 'research', description: 'Research agent' },
        { name: 'summarize', description: 'Summarize agent' },
        { name: 'orchestrate', description: 'Orchestration agent' },
      ]);
    });

    it('generates rich prompt context with tags, domains, inputs, speed', () => {
      const ctx = capabilityMap.getOrchestratorPromptContext();
      expect(ctx).toContain('"research"');
      expect(ctx).toContain('research, web-search');
      expect(ctx).toContain('Speed: slow');
      expect(ctx).toContain('Required inputs: topic');
    });

    it('excludeOrchestrate filters out orchestrate agent', () => {
      const ctx = capabilityMap.getOrchestratorPromptContext({ excludeOrchestrate: true });
      expect(ctx).not.toContain('"orchestrate"');
      expect(ctx).toContain('"research"');
      expect(ctx).toContain('"summarize"');
    });

    it('filters by trust tier', () => {
      capabilityMap.register(makeCapability({
        name: 'public-agent',
        description: 'Public agent',
        tags: ['public'],
        trustTier: 'public',
      }));

      const ctx = capabilityMap.getOrchestratorPromptContext({ maxTrustTier: 'local' });
      expect(ctx).toContain('"research"'); // local trust tier
      expect(ctx).not.toContain('"public-agent"'); // public trust tier excluded
    });

    it('returns "No agents available." when empty', () => {
      capabilityMap.cleanup();
      expect(capabilityMap.getOrchestratorPromptContext()).toBe('No agents available.');
    });
  });

  /* ── Snapshot ────────────────────────────────────────────────────────── */

  describe('Snapshot', () => {
    it('returns comprehensive capability map snapshot', () => {
      capabilityMap.register(makeCapability({ name: 'a', source: 'builtin', enabled: true }));
      capabilityMap.register(makeCapability({ name: 'b', source: 'plugin', enabled: true }));
      capabilityMap.register(makeCapability({ name: 'c', source: 'builtin', enabled: false }));

      // Trigger a gap
      capabilityMap.findCapable({ need: 'something impossible' });

      const snap = capabilityMap.getSnapshot();
      expect(snap.totalRegistered).toBe(3);
      expect(snap.enabledCount).toBe(2);
      expect(snap.bySources['builtin']).toBe(2);
      expect(snap.bySources['plugin']).toBe(1);
      expect(snap.gaps).toHaveLength(1);
      expect(snap.capabilities).toHaveLength(3);
      expect(snap.timestamp).toBeGreaterThan(0);
    });
  });

  /* ── Cleanup ─────────────────────────────────────────────────────────── */

  describe('Cleanup', () => {
    it('removes all state', () => {
      capabilityMap.register(makeCapability({ name: 'a' }));
      capabilityMap.register(makeCapability({ name: 'b' }));
      capabilityMap.findCapable({ need: 'some gap' });

      capabilityMap.cleanup();

      expect(capabilityMap.getAll(false)).toHaveLength(0);
      expect(capabilityMap.getGaps()).toHaveLength(0);
    });
  });

  /* ── Edge Cases ──────────────────────────────────────────────────────── */

  describe('Edge Cases', () => {
    it('findCapable with empty query returns baseline scores', () => {
      capabilityMap.register(makeCapability({ name: 'a' }));
      capabilityMap.register(makeCapability({ name: 'b' }));

      const matches = capabilityMap.findCapable({});
      expect(matches.length).toBe(2);
      // Should have baseline score
      expect(matches[0].score).toBeGreaterThan(0);
      expect(matches[0].reason).toContain('available');
    });

    it('findCapable handles short need words (< 3 chars filtered)', () => {
      capabilityMap.register(makeCapability({
        name: 'test',
        description: 'An agent for testing',
      }));

      // "do it" → both words < 3 chars, should not crash
      const matches = capabilityMap.findCapable({ need: 'do it' });
      // Should still return results (baseline or no match — should not error)
      expect(() => capabilityMap.findCapable({ need: 'do it' })).not.toThrow();
    });

    it('registerBuiltins profiles include correct data for all 5 builtin agents', () => {
      capabilityMap.registerBuiltins([
        { name: 'research', description: 'R' },
        { name: 'summarize', description: 'S' },
        { name: 'code-review', description: 'CR' },
        { name: 'draft-email', description: 'DE' },
        { name: 'orchestrate', description: 'O' },
      ]);

      // Research
      const research = capabilityMap.get('research')!;
      expect(research.tags).toContain('web-search');
      expect(research.latency).toBe('slow');
      expect(research.canDelegate).toBe(false);

      // Summarize
      const summarize = capabilityMap.get('summarize')!;
      expect(summarize.tags).toContain('summarization');
      expect(summarize.latency).toBe('fast');

      // Code Review
      const codeReview = capabilityMap.get('code-review')!;
      expect(codeReview.tags).toContain('code-review');
      expect(codeReview.domains).toContain('software-engineering');

      // Draft Email
      const draftEmail = capabilityMap.get('draft-email')!;
      expect(draftEmail.tags).toContain('email');
      expect(draftEmail.domains).toContain('communication');

      // Orchestrate
      const orchestrate = capabilityMap.get('orchestrate')!;
      expect(orchestrate.tags).toContain('orchestration');
      expect(orchestrate.canDelegate).toBe(true);
      expect(orchestrate.latency).toBe('slow');
    });
  });

  /* ── cLaw Compliance ─────────────────────────────────────────────────── */

  describe('cLaw Compliance', () => {
    it('First Law: trust-tier filtering prevents high-trust tasks from routing to lower-trust agents', () => {
      capabilityMap.register(makeCapability({
        name: 'secure-agent',
        tags: ['sensitive'],
        trustTier: 'local',
      }));
      capabilityMap.register(makeCapability({
        name: 'public-agent',
        tags: ['sensitive'],
        trustTier: 'public',
      }));

      // When restricted to local trust, public agent must not appear
      const matches = capabilityMap.findCapable({
        tags: ['sensitive'],
        maxTrustTier: 'local',
      });
      expect(matches).toHaveLength(1);
      expect(matches[0].capability.name).toBe('secure-agent');
    });

    it('Third Law: all query operations are synchronous and non-blocking', () => {
      // Register many agents
      for (let i = 0; i < 100; i++) {
        capabilityMap.register(makeCapability({
          name: `agent-${i}`,
          tags: [`tag-${i % 10}`],
          domains: [`domain-${i % 5}`],
          description: `Agent number ${i} for testing performance`,
        }));
      }

      const start = Date.now();

      // Run multiple queries
      capabilityMap.findCapable({ tags: ['tag-5'], domain: 'domain-3', need: 'test performance' });
      capabilityMap.getOrchestratorPromptContext();
      capabilityMap.getSnapshot();
      capabilityMap.getGaps();

      const elapsed = Date.now() - start;
      // All operations must complete in under 500ms (Third Law)
      expect(elapsed).toBeLessThan(500);
    });
  });

  /* ── Integration Readiness ───────────────────────────────────────────── */

  describe('Integration Readiness', () => {
    it('getOrchestratorPromptContext matches format expected by orchestrator.ts', () => {
      capabilityMap.registerBuiltins([
        { name: 'research', description: 'Research topics' },
        { name: 'summarize', description: 'Summarize text' },
      ]);

      const ctx = capabilityMap.getOrchestratorPromptContext({ excludeOrchestrate: true });

      // Format: - "name": description [tags] Domains: x. Required inputs: y. Speed: z.
      expect(ctx).toMatch(/^- "/m);
      expect(ctx).toContain('Speed:');
      // Should be multi-line (one per agent)
      const lines = ctx.split('\n').filter(l => l.startsWith('- '));
      expect(lines.length).toBe(2);
    });

    it('getAgentTypes returns backward-compatible format for orchestrator validation', () => {
      capabilityMap.registerBuiltins([
        { name: 'research', description: 'Research' },
      ]);

      const types = capabilityMap.getAgentTypes();
      expect(types).toEqual([{ name: 'research', description: 'Research' }]);
    });

    it('findCapable integrates with trust tiers from delegation-engine', () => {
      // Register agents at different trust tiers
      capabilityMap.register(makeCapability({ name: 'local', trustTier: 'local' }));
      capabilityMap.register(makeCapability({ name: 'group', trustTier: 'group' }));

      // Query with trust tier from delegation context
      const localOnly = capabilityMap.findCapable({
        maxTrustTier: 'owner-dm',
      });
      // Local (0) <= owner-dm (1) → included
      // Group (3) <= owner-dm (1) → excluded
      const names = localOnly.map(m => m.capability.name);
      expect(names).toContain('local');
      expect(names).not.toContain('group');
    });
  });
});
