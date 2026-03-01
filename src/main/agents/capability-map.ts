/**
 * Track XI, Phase 6 — Capability Map
 *
 * Dynamic agent type registry that provides:
 *   - Structured capability metadata beyond name/description
 *   - Capability-based routing ("who can handle web research?")
 *   - Runtime registration of new agent types (superpowers, plugins)
 *   - Rich capability descriptions for orchestrator planning prompts
 *   - Trust-tier-filtered capability views for delegation
 *   - Capability gap tracking (what tasks have no capable agent)
 *   - Tag-based and fuzzy query matching
 *
 * The map sits between the orchestrator's planning step and the agent
 * runner's dispatch, enabling intelligent routing rather than flat lookup.
 *
 * cLaw compliance:
 *   First Law: Capability queries filter by trust tier — high-trust tasks
 *              won't be routed to lower-trust agents
 *   Third Law: All queries are synchronous and non-blocking
 */

import type { TrustTier } from './delegation-engine';
import { symbiontProtocol } from './symbiont-protocol';

/* ── Types ──────────────────────────────────────────────────────────── */

export interface AgentCapability {
  /** Unique agent name (matches AgentDefinition.name) */
  name: string;
  /** Human-readable description of what the agent does */
  description: string;
  /** Capability tags for routing (e.g. 'research', 'web-search', 'synthesis') */
  tags: string[];
  /** Knowledge domains this agent handles (e.g. 'general', 'code', 'finance') */
  domains: string[];
  /** Input fields this agent accepts */
  inputSchema: InputField[];
  /** Output format description */
  outputFormat: string;
  /** Trust tier required to use this agent (default: 'local') */
  trustTier: TrustTier;
  /** Whether this agent can spawn sub-agents */
  canDelegate: boolean;
  /** Estimated latency category */
  latency: 'fast' | 'medium' | 'slow';
  /** Source of this capability registration */
  source: 'builtin' | 'superpower' | 'plugin' | 'remote';
  /** Timestamp of registration */
  registeredAt: number;
  /** Whether this capability is currently active/available */
  enabled: boolean;
}

export interface InputField {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'object' | 'array';
  required: boolean;
  description: string;
}

export interface CapabilityQuery {
  /** Free-text description of what's needed */
  need?: string;
  /** Tags to match against (OR matching) */
  tags?: string[];
  /** Domain filter */
  domain?: string;
  /** Maximum trust tier allowed (agents at this tier or higher privilege) */
  maxTrustTier?: TrustTier;
  /** Exclude specific agent names */
  exclude?: string[];
  /** Only include agents from these sources */
  sources?: AgentCapability['source'][];
}

export interface CapabilityMatch {
  capability: AgentCapability;
  /** Relevance score 0-1 (higher = better match) */
  score: number;
  /** Why this agent matched */
  reason: string;
}

export interface CapabilityGap {
  id: string;
  need: string;
  tags: string[];
  domain?: string;
  recordedAt: number;
  /** How many times this gap has been hit */
  hitCount: number;
}

export interface CapabilitySnapshot {
  capabilities: AgentCapability[];
  gaps: CapabilityGap[];
  totalRegistered: number;
  enabledCount: number;
  bySources: Record<string, number>;
  timestamp: number;
}

/* ── Trust tier ordering (reused from delegation engine) ────────────── */

const TRUST_TIER_ORDER: Record<TrustTier, number> = {
  local: 0,
  'owner-dm': 1,
  'approved-dm': 2,
  group: 3,
  public: 4,
};

/* ── Capability Map ─────────────────────────────────────────────────── */

class CapabilityMap {
  private capabilities: Map<string, AgentCapability> = new Map();
  private gaps: Map<string, CapabilityGap> = new Map();
  private gapIdCounter = 0;

  /* ── Registration ──────────────────────────────────────────────────── */

  /**
   * Register or update an agent's capabilities.
   * Idempotent — re-registering with the same name updates the entry.
   */
  register(capability: AgentCapability): void {
    this.capabilities.set(capability.name, {
      ...capability,
      registeredAt: capability.registeredAt || Date.now(),
    });
  }

  /**
   * Register builtin agents with default capability metadata.
   * Called once at startup from the agent runner's definitions.
   */
  registerBuiltins(agents: Array<{ name: string; description: string }>): void {
    // Default capability profiles for known builtin agents
    const profiles: Record<string, Partial<AgentCapability>> = {
      research: {
        tags: ['research', 'web-search', 'information-gathering', 'synthesis'],
        domains: ['general', 'technology', 'science', 'business'],
        inputSchema: [
          { name: 'topic', type: 'string', required: true, description: 'Research topic or question' },
          { name: 'query', type: 'string', required: false, description: 'Alternative query field' },
        ],
        outputFormat: 'Markdown briefing with sources and key findings',
        latency: 'slow',
        canDelegate: false,
      },
      summarize: {
        tags: ['summarization', 'synthesis', 'condensation', 'analysis'],
        domains: ['general', 'text-processing'],
        inputSchema: [
          { name: 'text', type: 'string', required: true, description: 'Text to summarize' },
          { name: 'style', type: 'string', required: false, description: 'Summary style (executive, technical, casual)' },
        ],
        outputFormat: 'Condensed summary in requested style',
        latency: 'fast',
        canDelegate: false,
      },
      'code-review': {
        tags: ['code-review', 'code-analysis', 'quality', 'bugs', 'security'],
        domains: ['code', 'software-engineering'],
        inputSchema: [
          { name: 'code', type: 'string', required: true, description: 'Code to review' },
          { name: 'language', type: 'string', required: false, description: 'Programming language' },
          { name: 'focus', type: 'string', required: false, description: 'Review focus (security, performance, style)' },
        ],
        outputFormat: 'Structured code review with issues, suggestions, and severity ratings',
        latency: 'medium',
        canDelegate: false,
      },
      'draft-email': {
        tags: ['writing', 'email', 'communication', 'drafting'],
        domains: ['communication', 'business'],
        inputSchema: [
          { name: 'to', type: 'string', required: true, description: 'Recipient name or role' },
          { name: 'subject', type: 'string', required: false, description: 'Email subject' },
          { name: 'key_points', type: 'string', required: false, description: 'Key points to cover' },
          { name: 'tone', type: 'string', required: false, description: 'Tone (formal, casual, friendly)' },
        ],
        outputFormat: 'Ready-to-send email draft with subject line',
        latency: 'fast',
        canDelegate: false,
      },
      orchestrate: {
        tags: ['orchestration', 'planning', 'decomposition', 'coordination'],
        domains: ['general', 'task-management'],
        inputSchema: [
          { name: 'goal', type: 'string', required: true, description: 'Complex goal to decompose and execute' },
          { name: 'context', type: 'string', required: false, description: 'Additional context for planning' },
        ],
        outputFormat: 'Aggregated results from coordinated sub-tasks',
        latency: 'slow',
        canDelegate: true,
      },
    };

    for (const agent of agents) {
      const profile = profiles[agent.name] || {};
      this.register({
        name: agent.name,
        description: agent.description,
        tags: profile.tags || [],
        domains: profile.domains || ['general'],
        inputSchema: profile.inputSchema || [],
        outputFormat: profile.outputFormat || 'Text result',
        trustTier: 'local',
        canDelegate: profile.canDelegate || false,
        latency: profile.latency || 'medium',
        source: 'builtin',
        registeredAt: Date.now(),
        enabled: true,
      });
    }
  }

  /**
   * Unregister an agent capability.
   */
  unregister(name: string): boolean {
    return this.capabilities.delete(name);
  }

  /**
   * Enable or disable a capability without removing it.
   */
  setEnabled(name: string, enabled: boolean): void {
    const cap = this.capabilities.get(name);
    if (cap) cap.enabled = enabled;
  }

  /* ── Queries ───────────────────────────────────────────────────────── */

  /**
   * Get a specific capability by name.
   */
  get(name: string): AgentCapability | null {
    return this.capabilities.get(name) || null;
  }

  /**
   * Get all registered capabilities.
   */
  getAll(enabledOnly = true): AgentCapability[] {
    const all = [...this.capabilities.values()];
    return enabledOnly ? all.filter((c) => c.enabled) : all;
  }

  /**
   * Find agents capable of handling a specific need.
   * Returns matches sorted by relevance score (highest first).
   */
  findCapable(query: CapabilityQuery): CapabilityMatch[] {
    const candidates = this.getAll(true);
    const matches: CapabilityMatch[] = [];

    for (const cap of candidates) {
      // ── Exclusion filters ──────────────────────────────────────────
      if (query.exclude?.includes(cap.name)) continue;
      if (query.sources && !query.sources.includes(cap.source)) continue;

      // ── Trust tier filter (cLaw First Law) ─────────────────────────
      if (query.maxTrustTier) {
        const maxOrder = TRUST_TIER_ORDER[query.maxTrustTier];
        const capOrder = TRUST_TIER_ORDER[cap.trustTier];
        // Agent must be at same or higher privilege (lower number)
        if (capOrder > maxOrder) continue;
      }

      // ── Score calculation ──────────────────────────────────────────
      let score = 0;
      const reasons: string[] = [];

      // Tag matching (OR — any matching tag adds score)
      if (query.tags && query.tags.length > 0) {
        const matchedTags = query.tags.filter((t) =>
          cap.tags.some((ct) => ct.toLowerCase() === t.toLowerCase())
        );
        if (matchedTags.length > 0) {
          score += (matchedTags.length / query.tags.length) * 0.4;
          reasons.push(`tags: ${matchedTags.join(', ')}`);
        }
      }

      // Domain matching
      if (query.domain) {
        if (cap.domains.some((d) => d.toLowerCase() === query.domain!.toLowerCase())) {
          score += 0.2;
          reasons.push(`domain: ${query.domain}`);
        }
      }

      // Free-text need matching (fuzzy against name, description, tags)
      if (query.need) {
        const needLower = query.need.toLowerCase();
        const needWords = needLower.split(/\s+/).filter((w) => w.length > 2);

        // Check name
        if (cap.name.toLowerCase().includes(needLower)) {
          score += 0.3;
          reasons.push('name match');
        }

        // Check description word overlap
        const descLower = cap.description.toLowerCase();
        const descMatches = needWords.filter((w) => descLower.includes(w));
        if (descMatches.length > 0) {
          score += (descMatches.length / needWords.length) * 0.3;
          reasons.push(`description: ${descMatches.length}/${needWords.length} words`);
        }

        // Check tags word overlap
        const tagString = cap.tags.join(' ').toLowerCase();
        const tagMatches = needWords.filter((w) => tagString.includes(w));
        if (tagMatches.length > 0) {
          score += (tagMatches.length / needWords.length) * 0.2;
          reasons.push(`tag words: ${tagMatches.length}/${needWords.length}`);
        }
      }

      // Baseline score for any remaining candidate (minimum visibility)
      if (score === 0 && !query.need && !query.tags) {
        score = 0.1; // Low baseline when query is broad
        reasons.push('available');
      }

      // Symbiont Protocol: apply performance-based score adjustment
      if (score > 0) {
        const perfBoost = symbiontProtocol.getPerformanceBoost(cap.name);
        if (perfBoost !== 0) {
          score += perfBoost;
          const direction = perfBoost > 0 ? '+' : '';
          reasons.push(`perf: ${direction}${(perfBoost * 100).toFixed(0)}%`);
        }
      }

      if (score > 0) {
        matches.push({
          capability: cap,
          score: Math.min(1, Math.max(0.01, score)), // Floor at 0.01 (never fully eliminate)
          reason: reasons.join('; '),
        });
      }
    }

    // Sort by score descending
    matches.sort((a, b) => b.score - a.score);

    // If no matches and a need was specified, record a capability gap
    if (matches.length === 0 && query.need) {
      this.recordGap(query.need, query.tags || [], query.domain);
    }

    return matches;
  }

  /**
   * Generate a rich capability description string for the orchestrator's
   * planning prompt. Includes tags, domains, input schemas, and latency.
   */
  getOrchestratorPromptContext(options?: {
    maxTrustTier?: TrustTier;
    excludeOrchestrate?: boolean;
  }): string {
    let caps = this.getAll(true);

    // Trust-tier filter
    if (options?.maxTrustTier) {
      const maxOrder = TRUST_TIER_ORDER[options.maxTrustTier];
      caps = caps.filter((c) => TRUST_TIER_ORDER[c.trustTier] <= maxOrder);
    }

    // Exclude orchestrate to prevent recursive planning
    if (options?.excludeOrchestrate) {
      caps = caps.filter((c) => c.name !== 'orchestrate');
    }

    if (caps.length === 0) return 'No agents available.';

    return caps
      .map((c) => {
        const tags = c.tags.length > 0 ? ` [${c.tags.join(', ')}]` : '';
        const domains = c.domains.length > 0 ? ` Domains: ${c.domains.join(', ')}.` : '';
        const inputs = c.inputSchema
          .filter((f) => f.required)
          .map((f) => f.name)
          .join(', ');
        const inputStr = inputs ? ` Required inputs: ${inputs}.` : '';
        const speed = ` Speed: ${c.latency}.`;
        return `- "${c.name}": ${c.description}${tags}${domains}${inputStr}${speed}`;
      })
      .join('\n');
  }

  /**
   * Get a simplified agent list (backward compatible with old orchestrator).
   * Returns same format as agentRunner.getAgentTypes() but from capability map.
   */
  getAgentTypes(enabledOnly = true): Array<{ name: string; description: string }> {
    return this.getAll(enabledOnly).map((c) => ({
      name: c.name,
      description: c.description,
    }));
  }

  /* ── Capability Gap Tracking ───────────────────────────────────────── */

  /**
   * Record a capability gap — a need that no agent can currently fulfill.
   */
  private recordGap(need: string, tags: string[], domain?: string): void {
    // Check if similar gap already recorded (fuzzy match on need)
    const needLower = need.toLowerCase();
    for (const gap of this.gaps.values()) {
      if (gap.need.toLowerCase() === needLower) {
        gap.hitCount++;
        gap.recordedAt = Date.now();
        return;
      }
    }

    const id = `gap-${String(++this.gapIdCounter).padStart(4, '0')}`;
    this.gaps.set(id, {
      id,
      need,
      tags,
      domain,
      recordedAt: Date.now(),
      hitCount: 1,
    });

    // Cap at 50 gaps
    if (this.gaps.size > 50) {
      // Remove oldest, lowest-hit gaps
      const sorted = [...this.gaps.values()].sort(
        (a, b) => a.hitCount - b.hitCount || a.recordedAt - b.recordedAt
      );
      this.gaps.delete(sorted[0].id);
    }
  }

  /**
   * Get all recorded capability gaps, sorted by hit count (most requested first).
   */
  getGaps(): CapabilityGap[] {
    return [...this.gaps.values()].sort((a, b) => b.hitCount - a.hitCount);
  }

  /**
   * Clear a specific gap (e.g., after a new agent is registered that fills it).
   */
  clearGap(gapId: string): boolean {
    return this.gaps.delete(gapId);
  }

  /* ── Snapshot ──────────────────────────────────────────────────────── */

  /**
   * Get a full snapshot of the capability map (for UI/debugging).
   */
  getSnapshot(): CapabilitySnapshot {
    const all = this.getAll(false);
    const bySources: Record<string, number> = {};
    for (const c of all) {
      bySources[c.source] = (bySources[c.source] || 0) + 1;
    }

    return {
      capabilities: all,
      gaps: this.getGaps(),
      totalRegistered: all.length,
      enabledCount: all.filter((c) => c.enabled).length,
      bySources,
      timestamp: Date.now(),
    };
  }

  /* ── Cleanup ──────────────────────────────────────────────────────── */

  /**
   * Full cleanup — removes all state. Used for testing.
   */
  cleanup(): void {
    this.capabilities.clear();
    this.gaps.clear();
    this.gapIdCounter = 0;
  }
}

/* ── Singleton Export ───────────────────────────────────────────────── */

export const capabilityMap = new CapabilityMap();
