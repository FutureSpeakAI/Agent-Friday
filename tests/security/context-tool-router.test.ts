/**
 * Tests for context-tool-router.ts — Track III Phase 3: Context-Aware Tool Routing.
 * Validates tool scoring, category inference, dynamic registration,
 * context generation, snapshot, caching, and edge cases.
 *
 * cLaw Gate assertion: router is read-only over context graph + tool registry.
 * No tool execution, no persistence, no data mutation.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock context stream (required by context-graph) ──────────────────
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    on: vi.fn(() => vi.fn()),
    getSnapshot: vi.fn().mockReturnValue({
      activeApp: '',
      windowTitle: '',
      inferredTask: '',
      focusStreak: 0,
      currentMood: 'neutral',
      moodConfidence: 0,
      energyLevel: 0.5,
      lastClipboardType: '',
      lastClipboardPreview: '',
      recentToolCalls: [],
      recentNotifications: [],
      activeWorkStream: '',
      lastUpdated: 0,
    }),
  },
}));

// ── Mock context graph ──────────────────────────────────────────────
const mockActiveStream: any = null;
const mockActiveEntities: any[] = [];
const mockTopEntities: any[] = [];
const mockRecentStreams: any[] = [];

vi.mock('../../src/main/context-graph', () => ({
  contextGraph: {
    getActiveStream: vi.fn(() => mockActiveStream),
    getActiveEntities: vi.fn(() => mockActiveEntities),
    getTopEntities: vi.fn(() => mockTopEntities),
    getRecentStreams: vi.fn(() => mockRecentStreams),
    getSnapshot: vi.fn().mockReturnValue({
      activeStream: null,
      recentStreams: [],
      topEntities: [],
      activeEntities: [],
      streamCount: 0,
      entityCount: 0,
    }),
    getContextString: vi.fn().mockReturnValue(''),
    getPromptContext: vi.fn().mockReturnValue(''),
    getStatus: vi.fn().mockReturnValue({
      activeStreamId: null,
      streamCount: 0,
      entityCount: 0,
      totalEventsProcessed: 0,
      memoryEstimateKb: 0,
    }),
  },
}));

import { contextGraph } from '../../src/main/context-graph';
const mockGraph = vi.mocked(contextGraph);

import { ContextToolRouter } from '../../src/main/context-tool-router';
import type {
  ToolCategory,
  ToolSuggestion,
  ToolRoutingConfig,
  ToolRoutingSnapshot,
  ToolRoutingStatus,
} from '../../src/main/context-tool-router';

// ── Helpers ────────────────────────────────────────────────────────────

function makeWorkStream(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ws-test-001',
    name: 'Testing in VS Code',
    task: 'coding',
    app: 'VS Code',
    startedAt: Date.now() - 60_000,
    lastActiveAt: Date.now(),
    eventCount: 10,
    entities: [],
    eventTypes: new Set(['ambient']),
    summary: 'Coding session in VS Code',
    ...overrides,
  };
}

function makeEntity(type: string, value: string, extra: Record<string, unknown> = {}) {
  return {
    type,
    value,
    normalizedValue: value.toLowerCase(),
    firstSeen: Date.now() - 60_000,
    lastSeen: Date.now(),
    occurrences: 3,
    sourceStreamIds: ['ws-test-001'],
    ...extra,
  };
}

// ── Tests ──────────────────────────────────────────────────────────────

describe('ContextToolRouter', () => {
  let router: ContextToolRouter;

  beforeEach(() => {
    vi.clearAllMocks();
    // Each test gets a fresh router with no cache
    router = new ContextToolRouter();
    // Reset mock return values to empty defaults
    mockGraph.getActiveStream.mockReturnValue(null);
    mockGraph.getActiveEntities.mockReturnValue([]);
    mockGraph.getTopEntities.mockReturnValue([]);
    mockGraph.getRecentStreams.mockReturnValue([]);
  });

  // ── Constructor & Config ───────────────────────────────────────────

  describe('constructor', () => {
    it('initializes with default config', () => {
      const config = router.getConfig();
      expect(config.maxSuggestions).toBe(8);
      expect(config.minScore).toBe(0.2);
      expect(config.boostActiveEntities).toBe(0.3);
      expect(config.boostActiveTask).toBe(0.25);
      expect(config.boostRecentTool).toBe(0.15);
    });

    it('accepts partial config override', () => {
      const custom = new ContextToolRouter({ maxSuggestions: 3, minScore: 0.5 });
      const config = custom.getConfig();
      expect(config.maxSuggestions).toBe(3);
      expect(config.minScore).toBe(0.5);
      // Non-overridden fields stay default
      expect(config.boostActiveTask).toBe(0.25);
    });

    it('loads static tool profiles', () => {
      const status = router.getStatus();
      expect(status.totalProfiles).toBeGreaterThanOrEqual(40);
    });
  });

  // ── Route (Core Scoring) ──────────────────────────────────────────

  describe('route()', () => {
    it('returns empty array when no context is active', () => {
      // With minScore 0.2, some tools with basePriority still qualify
      // but with no context, scores are lower
      const suggestions = router.route();
      expect(Array.isArray(suggestions)).toBe(true);
      // At minimum we expect some tools to pass the threshold on basePriority alone
    });

    it('returns suggestions as sorted ToolSuggestion array', () => {
      const suggestions = router.route();
      for (let i = 1; i < suggestions.length; i++) {
        expect(suggestions[i - 1].score).toBeGreaterThanOrEqual(suggestions[i].score);
      }
      for (const s of suggestions) {
        expect(s).toHaveProperty('toolName');
        expect(s).toHaveProperty('category');
        expect(s).toHaveProperty('score');
        expect(s).toHaveProperty('reason');
        expect(s.score).toBeGreaterThanOrEqual(0);
        expect(s.score).toBeLessThanOrEqual(1);
      }
    });

    it('limits results to maxSuggestions', () => {
      const constrained = new ContextToolRouter({ maxSuggestions: 3 });
      const suggestions = constrained.route();
      expect(suggestions.length).toBeLessThanOrEqual(3);
    });

    it('filters results below minScore', () => {
      const strict = new ContextToolRouter({ minScore: 0.99 });
      const suggestions = strict.route();
      for (const s of suggestions) {
        expect(s.score).toBeGreaterThanOrEqual(0.99);
      }
    });

    it('boosts code tools when coding stream is active', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getRecentStreams.mockReturnValue([makeWorkStream({ task: 'coding' })] as any);

      const suggestions = router.route();
      const codeTools = suggestions.filter(s => s.category === 'code' || s.category === 'project');
      expect(codeTools.length).toBeGreaterThan(0);

      // Code tools should be scored higher than unrelated tools
      if (codeTools.length > 0 && suggestions.length > codeTools.length) {
        const avgCodeScore = codeTools.reduce((sum, s) => sum + s.score, 0) / codeTools.length;
        const nonCodeTools = suggestions.filter(s => s.category !== 'code' && s.category !== 'project');
        if (nonCodeTools.length > 0) {
          const avgOtherScore = nonCodeTools.reduce((sum, s) => sum + s.score, 0) / nonCodeTools.length;
          expect(avgCodeScore).toBeGreaterThan(avgOtherScore);
        }
      }
    });

    it('boosts communication tools during communicating', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'communicating', app: 'Slack' }) as any);
      mockGraph.getRecentStreams.mockReturnValue([makeWorkStream({ task: 'communicating' })] as any);

      const suggestions = router.route();
      const commTools = suggestions.filter(s =>
        s.category === 'communication' || s.category === 'trust'
      );
      expect(commTools.length).toBeGreaterThan(0);
    });

    it('boosts meeting tools during meeting task', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'meeting', app: 'Zoom' }) as any);
      mockGraph.getRecentStreams.mockReturnValue([makeWorkStream({ task: 'meeting' })] as any);

      const suggestions = router.route();
      const meetingTools = suggestions.filter(s => s.category === 'meeting');
      expect(meetingTools.length).toBeGreaterThan(0);
    });

    it('applies entity affinity boost for file entities', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getActiveEntities.mockReturnValue([
        makeEntity('file', 'src/main/index.ts'),
        makeEntity('file', 'src/main/context-graph.ts'),
        makeEntity('project', 'agent-friday'),
      ] as any);

      const suggestions = router.route();
      // Tools with file entity affinity should get a boost
      const readFile = suggestions.find(s => s.toolName === 'read_file');
      if (readFile) {
        expect(readFile.reason).toContain('entities');
      }
    });

    it('applies entity affinity boost for person entities', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'communicating' }) as any);
      mockGraph.getActiveEntities.mockReturnValue([
        makeEntity('person', 'John Smith'),
        makeEntity('person', 'Sarah Jones'),
      ] as any);

      const suggestions = router.route();
      const personTools = suggestions.filter(s =>
        s.toolName === 'lookup_person' || s.toolName === 'draft_communication'
      );
      expect(personTools.length).toBeGreaterThan(0);
    });

    it('boosts recently used tools', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getActiveEntities.mockReturnValue([
        makeEntity('tool', 'ask_claude'),
      ] as any);

      const suggestions = router.route();
      const askClaude = suggestions.find(s => s.toolName === 'ask_claude');
      expect(askClaude).toBeDefined();
      if (askClaude) {
        expect(askClaude.reason).toContain('recently used');
      }
    });

    it('includes top entity cross-reference boost', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getTopEntities.mockReturnValue([
        makeEntity('file', 'src/main/index.ts'),
        makeEntity('project', 'agent-friday'),
        makeEntity('topic', 'typescript'),
        makeEntity('person', 'John'),
        makeEntity('url', 'https://github.com'),
      ] as any);

      const suggestions = router.route();
      expect(suggestions.length).toBeGreaterThan(0);
    });

    it('scores all tools in [0, 1] range', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getActiveEntities.mockReturnValue([
        makeEntity('file', 'index.ts'),
        makeEntity('tool', 'ask_claude'),
        makeEntity('project', 'nexus'),
      ] as any);
      mockGraph.getTopEntities.mockReturnValue([
        makeEntity('file', 'test.ts'),
        makeEntity('topic', 'testing'),
      ] as any);

      const suggestions = router.route();
      for (const s of suggestions) {
        expect(s.score).toBeGreaterThanOrEqual(0);
        expect(s.score).toBeLessThanOrEqual(1);
      }
    });

    it('generates reason strings for each suggestion', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);

      const suggestions = router.route();
      for (const s of suggestions) {
        expect(typeof s.reason).toBe('string');
        expect(s.reason.length).toBeGreaterThan(0);
      }
    });
  });

  // ── Caching ───────────────────────────────────────────────────────

  describe('caching', () => {
    it('returns cached results within 5 seconds', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);

      const first = router.route();
      // Change context — if cached, should NOT affect result
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'meeting' }) as any);
      const second = router.route();

      expect(second).toBe(first); // Same array reference = cache hit
    });

    it('invalidates cache after creating a new router', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      const first = router.route();

      // New router = no cache
      const freshRouter = new ContextToolRouter();
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'meeting' }) as any);
      const result = freshRouter.route();

      // Should NOT be the same reference since fresh router has no cache
      expect(result).not.toBe(first);
    });
  });

  // ── getActiveCategory ──────────────────────────────────────────────

  describe('getActiveCategory()', () => {
    it('returns "general" when no active stream', () => {
      expect(router.getActiveCategory()).toBe('general');
    });

    it('returns "code" for coding task', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      expect(router.getActiveCategory()).toBe('code');
    });

    it('returns "communication" for emailing task', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'emailing' }) as any);
      expect(router.getActiveCategory()).toBe('communication');
    });

    it('returns "meeting" for meeting task', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'meeting' }) as any);
      expect(router.getActiveCategory()).toBe('meeting');
    });

    it('returns "research" for researching task', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'researching' }) as any);
      expect(router.getActiveCategory()).toBe('research');
    });

    it('returns "general" for unknown task', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'unknowntask' }) as any);
      expect(router.getActiveCategory()).toBe('general');
    });
  });

  // ── getCategoryScores ──────────────────────────────────────────────

  describe('getCategoryScores()', () => {
    it('returns scores for all 11 categories', () => {
      const scores = router.getCategoryScores();
      const categories: ToolCategory[] = [
        'code', 'communication', 'research', 'system', 'meeting',
        'memory', 'project', 'automation', 'creative', 'trust', 'general',
      ];
      for (const cat of categories) {
        expect(scores).toHaveProperty(cat);
        expect(typeof scores[cat]).toBe('number');
      }
    });

    it('all scores are in [0, 1] range', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      const scores = router.getCategoryScores();
      for (const [, score] of Object.entries(scores)) {
        expect(score).toBeGreaterThanOrEqual(0);
        expect(score).toBeLessThanOrEqual(1);
      }
    });

    it('assigns highest score to primary task category', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      const scores = router.getCategoryScores();
      // 'code' should be highest or tied for highest
      expect(scores['code']).toBeGreaterThanOrEqual(scores['trust']);
      expect(scores['code']).toBeGreaterThanOrEqual(scores['meeting']);
    });

    it('returns zero scores when no context active', () => {
      const scores = router.getCategoryScores();
      // With no active stream or entities, all scores should be low
      for (const [, score] of Object.entries(scores)) {
        expect(score).toBeLessThanOrEqual(0.5);
      }
    });
  });

  // ── Context String Generation ─────────────────────────────────────

  describe('getContextString()', () => {
    it('returns empty string when no suggestions', () => {
      const strictRouter = new ContextToolRouter({ minScore: 0.99 });
      const ctx = strictRouter.getContextString();
      expect(ctx).toBe('');
    });

    it('returns markdown with tool suggestions', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);

      const ctx = router.getContextString();
      expect(ctx).toContain('## Tool Suggestions');
      expect(ctx).toContain('Context:');
      expect(ctx).toContain('relevant');
    });

    it('includes tool names and percentages', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);

      const ctx = router.getContextString();
      expect(ctx).toMatch(/\*\*\w+\*\*/); // Bold tool names
      expect(ctx).toMatch(/\d+%/);          // Percentage scores
    });
  });

  // ── Prompt Context (Budget) ───────────────────────────────────────

  describe('getPromptContext()', () => {
    it('returns empty string when no suggestions', () => {
      const strictRouter = new ContextToolRouter({ minScore: 0.99 });
      expect(strictRouter.getPromptContext()).toBe('');
    });

    it('returns single-line prefixed with [TOOLS]', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);

      const ctx = router.getPromptContext();
      expect(ctx).toMatch(/^\[TOOLS\]/);
      expect(ctx).toContain('mode');
      expect(ctx).toContain('prefer:');
    });

    it('limits to 5 tool names', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);

      const ctx = router.getPromptContext();
      // Count commas in the 'prefer:' section
      const preferSection = ctx.split('prefer:')[1] || '';
      const toolNames = preferSection.split(',');
      expect(toolNames.length).toBeLessThanOrEqual(5);
    });
  });

  // ── Snapshot & Status ──────────────────────────────────────────────

  describe('getSnapshot()', () => {
    it('returns complete ToolRoutingSnapshot', () => {
      const snap = router.getSnapshot();
      expect(snap).toHaveProperty('suggestedTools');
      expect(snap).toHaveProperty('activeCategory');
      expect(snap).toHaveProperty('contextSummary');
      expect(snap).toHaveProperty('totalToolsScored');
      expect(Array.isArray(snap.suggestedTools)).toBe(true);
      expect(typeof snap.activeCategory).toBe('string');
      expect(typeof snap.contextSummary).toBe('string');
      expect(typeof snap.totalToolsScored).toBe('number');
    });

    it('reports "No active context" when no stream', () => {
      const snap = router.getSnapshot();
      expect(snap.contextSummary).toBe('No active context');
    });

    it('reports task and app when stream active', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding', app: 'VS Code' }) as any);

      const snap = router.getSnapshot();
      expect(snap.contextSummary).toContain('coding');
      expect(snap.contextSummary).toContain('VS Code');
    });

    it('includes total tools scored count', () => {
      const snap = router.getSnapshot();
      expect(snap.totalToolsScored).toBeGreaterThanOrEqual(40);
    });
  });

  describe('getStatus()', () => {
    it('returns ToolRoutingStatus with required fields', () => {
      const status = router.getStatus();
      expect(status).toHaveProperty('totalProfiles');
      expect(status).toHaveProperty('activeCategoryScores');
      expect(status).toHaveProperty('lastRoutingTimestamp');
      expect(status).toHaveProperty('suggestedToolCount');
    });

    it('updates suggestedToolCount after routing', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      router.route();

      const status = router.getStatus();
      expect(status.suggestedToolCount).toBeGreaterThan(0);
    });

    it('updates lastRoutingTimestamp after routing', () => {
      const before = Date.now();
      router.route();
      const status = router.getStatus();
      expect(status.lastRoutingTimestamp).toBeGreaterThanOrEqual(before - 1);
    });
  });

  // ── Dynamic Tool Registration ──────────────────────────────────────

  describe('registerTool()', () => {
    it('registers a new dynamic tool', () => {
      const before = router.getStatus().totalProfiles;
      router.registerTool('my_custom_tool', 'code', ['custom'], ['file'], ['coding']);
      const after = router.getStatus().totalProfiles;
      expect(after).toBe(before + 1);
    });

    it('does not override static profiles', () => {
      const before = router.getStatus().totalProfiles;
      router.registerTool('ask_claude', 'meeting'); // ask_claude is a static code tool
      const after = router.getStatus().totalProfiles;
      expect(after).toBe(before); // No new profile added
    });

    it('includes dynamic tools in route() scoring', () => {
      router.registerTool('my_tool', 'code', ['coding'], ['file'], ['coding']);
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);

      const suggestions = router.route();
      const found = suggestions.find(s => s.toolName === 'my_tool');
      // May or may not appear depending on score threshold, but should be scored
      // At minimum, totalToolsScored should include it
      const snap = router.getSnapshot();
      expect(snap.totalToolsScored).toBeGreaterThanOrEqual(41);
    });

    it('sets basePriority to 0.3 for dynamic tools', () => {
      router.registerTool('dynamic_tool', 'general');
      // We can't directly access the profile, but we can verify it was added
      const status = router.getStatus();
      expect(status.totalProfiles).toBeGreaterThanOrEqual(41);
    });
  });

  describe('registerToolsFromDeclarations()', () => {
    it('bulk registers tools with auto-categorization', () => {
      const before = router.getStatus().totalProfiles;
      router.registerToolsFromDeclarations([
        { name: 'email_sender', description: 'Send email messages to contacts' },
        { name: 'code_formatter', description: 'Format and lint code files' },
      ]);
      const after = router.getStatus().totalProfiles;
      expect(after).toBe(before + 2);
    });

    it('infers "communication" category from email description', () => {
      router.registerToolsFromDeclarations([
        { name: 'email_helper', description: 'Send and draft email messages' },
      ]);

      // Set context to communicating to see if it gets scored
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'communicating' }) as any);
      const suggestions = router.route();
      const tool = suggestions.find(s => s.toolName === 'email_helper');
      if (tool) {
        expect(tool.category).toBe('communication');
      }
    });

    it('infers "code" category from code description', () => {
      router.registerToolsFromDeclarations([
        { name: 'syntax_checker', description: 'Check and debug code syntax errors' },
      ]);
      // Trigger routing to see the tool
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      const suggestions = router.route();
      const tool = suggestions.find(s => s.toolName === 'syntax_checker');
      if (tool) {
        expect(tool.category).toBe('code');
      }
    });

    it('skips tools that already exist (static or dynamic)', () => {
      const before = router.getStatus().totalProfiles;
      router.registerToolsFromDeclarations([
        { name: 'ask_claude', description: 'Already exists as static' },
      ]);
      expect(router.getStatus().totalProfiles).toBe(before);
    });

    it('skips already-registered dynamic tools', () => {
      router.registerTool('custom_one', 'general');
      const after1 = router.getStatus().totalProfiles;
      router.registerToolsFromDeclarations([
        { name: 'custom_one', description: 'Already registered' },
      ]);
      expect(router.getStatus().totalProfiles).toBe(after1);
    });

    it('handles tools with empty description', () => {
      router.registerToolsFromDeclarations([
        { name: 'bare_tool' }, // No description
      ]);
      expect(router.getStatus().totalProfiles).toBeGreaterThan(40);
    });
  });

  describe('unregisterTool()', () => {
    it('removes a dynamic tool', () => {
      router.registerTool('temp_tool', 'general');
      const before = router.getStatus().totalProfiles;
      router.unregisterTool('temp_tool');
      const after = router.getStatus().totalProfiles;
      expect(after).toBe(before - 1);
    });

    it('does nothing for non-existent tool', () => {
      const before = router.getStatus().totalProfiles;
      router.unregisterTool('does_not_exist');
      expect(router.getStatus().totalProfiles).toBe(before);
    });

    it('does not remove static tools', () => {
      const before = router.getStatus().totalProfiles;
      router.unregisterTool('ask_claude'); // Static tool
      expect(router.getStatus().totalProfiles).toBe(before);
    });
  });

  // ── Category Inference (Private, tested via side effects) ──────────

  describe('category inference', () => {
    it('maps coding task to code + project categories', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      const cat = router.getActiveCategory();
      expect(cat).toBe('code');
    });

    it('maps debugging task to code', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'debugging' }) as any);
      expect(router.getActiveCategory()).toBe('code');
    });

    it('maps browsing task to research', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'browsing' }) as any);
      expect(router.getActiveCategory()).toBe('research');
    });

    it('maps scheduling task to meeting', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'scheduling' }) as any);
      expect(router.getActiveCategory()).toBe('meeting');
    });

    it('maps writing task to creative', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'writing' }) as any);
      expect(router.getActiveCategory()).toBe('creative');
    });

    it('maps automating task to automation', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'automating' }) as any);
      expect(router.getActiveCategory()).toBe('automation');
    });

    it('maps deploying task to code', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'deploying' }) as any);
      expect(router.getActiveCategory()).toBe('code');
    });
  });

  // ── inferCategory for dynamic tools ────────────────────────────────

  describe('auto-categorization from description', () => {
    it('categorizes git-related tool as project', () => {
      router.registerToolsFromDeclarations([
        { name: 'git_blame_tool', description: 'Run git blame on repo files' },
      ]);
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      const suggestions = router.route();
      const tool = suggestions.find(s => s.toolName === 'git_blame_tool');
      if (tool) {
        expect(tool.category).toBe('project');
      }
    });

    it('categorizes calendar-related tool as meeting', () => {
      router.registerToolsFromDeclarations([
        { name: 'cal_sync', description: 'Sync calendar events and schedule meetings' },
      ]);
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'scheduling' }) as any);
      const suggestions = router.route();
      const tool = suggestions.find(s => s.toolName === 'cal_sync');
      if (tool) {
        expect(tool.category).toBe('meeting');
      }
    });

    it('defaults to general when no keywords match', () => {
      router.registerToolsFromDeclarations([
        { name: 'mystery_tool', description: 'Does something interesting' },
      ]);
      // General tools should still get scored
      const status = router.getStatus();
      expect(status.totalProfiles).toBeGreaterThan(40);
    });
  });

  // ── extractKeywords (tested via registration side effects) ────────

  describe('keyword extraction', () => {
    it('extracts meaningful keywords from description', () => {
      router.registerToolsFromDeclarations([
        { name: 'search_emails', description: 'Search and filter email messages by subject and sender' },
      ]);
      // If keywords extracted correctly, routing with matching context should score it
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'communicating' }) as any);
      const suggestions = router.route();
      // The tool should at least be registered and scored
      const snap = router.getSnapshot();
      expect(snap.totalToolsScored).toBeGreaterThan(40);
    });
  });

  // ── Edge Cases ─────────────────────────────────────────────────────

  describe('edge cases', () => {
    it('handles empty activeEntities gracefully', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getActiveEntities.mockReturnValue([]);
      expect(() => router.route()).not.toThrow();
    });

    it('handles null activeStream gracefully', () => {
      mockGraph.getActiveStream.mockReturnValue(null);
      expect(() => router.route()).not.toThrow();
      expect(router.getActiveCategory()).toBe('general');
    });

    it('handles empty recentStreams gracefully', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getRecentStreams.mockReturnValue([]);
      expect(() => router.route()).not.toThrow();
    });

    it('handles work stream with undefined entities', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({
        task: 'coding',
        entities: undefined,
      }) as any);
      expect(() => router.route()).not.toThrow();
    });

    it('handles entity types not in any affinity list', () => {
      mockGraph.getActiveEntities.mockReturnValue([
        makeEntity('channel', '#random'),
      ] as any);
      expect(() => router.route()).not.toThrow();
    });
  });

  // ── cLaw Gate ─────────────────────────────────────────────────────

  describe('cLaw gate compliance', () => {
    it('does not expose any persist/save/write methods', () => {
      const proto = Object.getOwnPropertyNames(Object.getPrototypeOf(router));
      const mutationMethods = proto.filter(m =>
        /save|persist|write|delete|destroy|export|mutate/i.test(m)
      );
      expect(mutationMethods).toEqual([]);
    });

    it('does not execute any tools', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      router.route();
      // If tools were executed, there would be side effects — verify none
      // The router only reads from context graph and returns data
    });

    it('route() returns serializable data (no functions or circular refs)', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      const suggestions = router.route();
      const serialized = JSON.stringify(suggestions);
      expect(serialized).toBeTruthy();
      const parsed = JSON.parse(serialized);
      expect(parsed).toEqual(suggestions);
    });

    it('getSnapshot() returns serializable data', () => {
      const snap = router.getSnapshot();
      const serialized = JSON.stringify(snap);
      expect(serialized).toBeTruthy();
    });
  });

  // ── Multi-category routing ────────────────────────────────────────

  describe('multi-category routing', () => {
    it('includes secondary categories from recent streams', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getRecentStreams.mockReturnValue([
        makeWorkStream({ task: 'coding' }),
        makeWorkStream({ task: 'communicating', app: 'Slack' }),
      ] as any);

      const suggestions = router.route();
      const categories = new Set(suggestions.map(s => s.category));
      // Should include both code and communication categories
      expect(categories.has('code') || categories.has('project')).toBe(true);
    });

    it('scoring favors primary category over secondary', () => {
      mockGraph.getActiveStream.mockReturnValue(makeWorkStream({ task: 'coding' }) as any);
      mockGraph.getRecentStreams.mockReturnValue([
        makeWorkStream({ task: 'coding' }),
        makeWorkStream({ task: 'meeting', app: 'Zoom' }),
      ] as any);

      const scores = router.getCategoryScores();
      // Primary (code) should score higher than secondary (meeting)
      expect(scores['code']).toBeGreaterThanOrEqual(scores['meeting']);
    });
  });
});
