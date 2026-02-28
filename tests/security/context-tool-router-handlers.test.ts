/**
 * Tests for context-tool-router-handlers.ts — IPC layer for Track III Phase 3.
 * Validates handler registration, input validation, delegation to the
 * ContextToolRouter singleton, and cLaw gate compliance.
 *
 * cLaw Gate assertion: all channels are read-only queries + registration.
 * No tool execution, no persistence, no data mutation.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock electron ────────────────────────────────────────────────────
const handlers = new Map<string, (...args: unknown[]) => unknown>();
vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    }),
  },
  BrowserWindow: vi.fn(),
}));

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
vi.mock('../../src/main/context-graph', () => ({
  contextGraph: {
    getActiveStream: vi.fn().mockReturnValue(null),
    getActiveEntities: vi.fn().mockReturnValue([]),
    getTopEntities: vi.fn().mockReturnValue([]),
    getRecentStreams: vi.fn().mockReturnValue([]),
    getSnapshot: vi.fn().mockReturnValue({
      activeStream: null,
      recentStreams: [],
      topEntities: [],
      activeEntities: [],
      streamCount: 0,
      entityCount: 0,
    }),
  },
}));

// ── Mock context tool router ────────────────────────────────────────
vi.mock('../../src/main/context-tool-router', () => ({
  contextToolRouter: {
    route: vi.fn().mockReturnValue([
      { toolName: 'ask_claude', category: 'code', score: 0.85, reason: 'code workflow' },
      { toolName: 'read_file', category: 'code', score: 0.72, reason: 'entities: file' },
    ]),
    getActiveCategory: vi.fn().mockReturnValue('code'),
    getCategoryScores: vi.fn().mockReturnValue({
      code: 0.8, communication: 0.1, research: 0.2, system: 0.1,
      meeting: 0.0, memory: 0.1, project: 0.5, automation: 0.0,
      creative: 0.0, trust: 0.0, general: 0.1,
    }),
    getSnapshot: vi.fn().mockReturnValue({
      suggestedTools: [
        { toolName: 'ask_claude', category: 'code', score: 0.85, reason: 'code workflow' },
      ],
      activeCategory: 'code',
      contextSummary: 'coding in VS Code',
      totalToolsScored: 45,
    }),
    getContextString: vi.fn().mockReturnValue('## Tool Suggestions\n- **ask_claude** (85%)'),
    getPromptContext: vi.fn().mockReturnValue('[TOOLS] code mode | prefer: ask_claude'),
    getStatus: vi.fn().mockReturnValue({
      totalProfiles: 45,
      activeCategoryScores: { code: 0.8 },
      lastRoutingTimestamp: Date.now(),
      suggestedToolCount: 2,
    }),
    registerToolsFromDeclarations: vi.fn(),
    unregisterTool: vi.fn(),
    getConfig: vi.fn().mockReturnValue({
      maxSuggestions: 8,
      minScore: 0.2,
      boostActiveEntities: 0.3,
      boostActiveTask: 0.25,
      boostRecentTool: 0.15,
    }),
  },
}));

import { contextToolRouter } from '../../src/main/context-tool-router';
const mockRouter = vi.mocked(contextToolRouter);

import { registerContextToolRouterHandlers } from '../../src/main/ipc/context-tool-router-handlers';

// ── Helper ──────────────────────────────────────────────────────────
function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for channel: ${channel}`);
  return handler({} as any, ...args);
}

// ── Tests ──────────────────────────────────────────────────────────

describe('Context Tool Router IPC Handlers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    handlers.clear();
    registerContextToolRouterHandlers();
  });

  // ── Registration ──────────────────────────────────────────────────

  describe('handler registration', () => {
    it('registers exactly 10 IPC channels', () => {
      expect(handlers.size).toBe(10);
    });

    it('registers all expected channels', () => {
      const expectedChannels = [
        'tool-router:suggestions',
        'tool-router:active-category',
        'tool-router:category-scores',
        'tool-router:snapshot',
        'tool-router:context-string',
        'tool-router:prompt-context',
        'tool-router:status',
        'tool-router:register-tools',
        'tool-router:unregister-tool',
        'tool-router:config',
      ];
      for (const channel of expectedChannels) {
        expect(handlers.has(channel)).toBe(true);
      }
    });
  });

  // ── Read-only Query Channels ──────────────────────────────────────

  describe('tool-router:suggestions', () => {
    it('delegates to contextToolRouter.route()', () => {
      const result = invoke('tool-router:suggestions');
      expect(mockRouter.route).toHaveBeenCalledOnce();
      expect(result).toEqual([
        { toolName: 'ask_claude', category: 'code', score: 0.85, reason: 'code workflow' },
        { toolName: 'read_file', category: 'code', score: 0.72, reason: 'entities: file' },
      ]);
    });
  });

  describe('tool-router:active-category', () => {
    it('delegates to contextToolRouter.getActiveCategory()', () => {
      const result = invoke('tool-router:active-category');
      expect(mockRouter.getActiveCategory).toHaveBeenCalledOnce();
      expect(result).toBe('code');
    });
  });

  describe('tool-router:category-scores', () => {
    it('delegates to contextToolRouter.getCategoryScores()', () => {
      const result = invoke('tool-router:category-scores');
      expect(mockRouter.getCategoryScores).toHaveBeenCalledOnce();
      expect(result).toHaveProperty('code');
      expect(result).toHaveProperty('communication');
    });
  });

  describe('tool-router:snapshot', () => {
    it('delegates to contextToolRouter.getSnapshot()', () => {
      const result = invoke('tool-router:snapshot') as any;
      expect(mockRouter.getSnapshot).toHaveBeenCalledOnce();
      expect(result.suggestedTools).toBeDefined();
      expect(result.activeCategory).toBe('code');
      expect(result.contextSummary).toBe('coding in VS Code');
      expect(result.totalToolsScored).toBe(45);
    });
  });

  describe('tool-router:context-string', () => {
    it('delegates to contextToolRouter.getContextString()', () => {
      const result = invoke('tool-router:context-string');
      expect(mockRouter.getContextString).toHaveBeenCalledOnce();
      expect(result).toContain('## Tool Suggestions');
    });
  });

  describe('tool-router:prompt-context', () => {
    it('delegates to contextToolRouter.getPromptContext()', () => {
      const result = invoke('tool-router:prompt-context');
      expect(mockRouter.getPromptContext).toHaveBeenCalledOnce();
      expect(result).toContain('[TOOLS]');
    });
  });

  describe('tool-router:status', () => {
    it('delegates to contextToolRouter.getStatus()', () => {
      const result = invoke('tool-router:status') as any;
      expect(mockRouter.getStatus).toHaveBeenCalledOnce();
      expect(result.totalProfiles).toBe(45);
      expect(result.suggestedToolCount).toBe(2);
    });
  });

  describe('tool-router:config', () => {
    it('delegates to contextToolRouter.getConfig()', () => {
      const result = invoke('tool-router:config') as any;
      expect(mockRouter.getConfig).toHaveBeenCalledOnce();
      expect(result.maxSuggestions).toBe(8);
      expect(result.minScore).toBe(0.2);
    });
  });

  // ── Registration Channels ─────────────────────────────────────────

  describe('tool-router:register-tools', () => {
    it('accepts valid tool declarations array', () => {
      const tools = [
        { name: 'my_tool', description: 'A custom tool' },
        { name: 'another_tool' },
      ];
      const result = invoke('tool-router:register-tools', tools) as any;
      expect(mockRouter.registerToolsFromDeclarations).toHaveBeenCalledOnce();
      expect(result.registered).toBe(2);
    });

    it('throws on non-array input', () => {
      expect(() => invoke('tool-router:register-tools', 'not an array'))
        .toThrow('requires an array');
    });

    it('throws on null input', () => {
      expect(() => invoke('tool-router:register-tools', null))
        .toThrow('requires an array');
    });

    it('throws on undefined input', () => {
      expect(() => invoke('tool-router:register-tools', undefined))
        .toThrow('requires an array');
    });

    it('caps tools array at 200 items', () => {
      const tools = Array.from({ length: 300 }, (_, i) => ({
        name: `tool_${i}`,
        description: `Tool number ${i}`,
      }));
      const result = invoke('tool-router:register-tools', tools) as any;
      expect(result.registered).toBe(200);
    });

    it('filters out tools with empty name', () => {
      const tools = [
        { name: '', description: 'Empty name' },
        { name: 'valid_tool', description: 'Valid' },
      ];
      const result = invoke('tool-router:register-tools', tools) as any;
      expect(result.registered).toBe(1);
    });

    it('filters out tools with non-string name', () => {
      const tools = [
        { name: 123 as any, description: 'Numeric name' },
        { name: null as any },
        { name: 'valid_tool' },
      ];
      const result = invoke('tool-router:register-tools', tools) as any;
      expect(result.registered).toBe(1);
    });

    it('filters out tools with name >= 100 chars', () => {
      const tools = [
        { name: 'a'.repeat(100), description: 'Too long' },
        { name: 'short_name', description: 'OK' },
      ];
      const result = invoke('tool-router:register-tools', tools) as any;
      expect(result.registered).toBe(1);
    });

    it('passes sanitized tools to registerToolsFromDeclarations', () => {
      const tools = [
        { name: 'tool_a', description: 'First' },
        { name: 'tool_b', description: 'Second' },
      ];
      invoke('tool-router:register-tools', tools);
      const passedTools = mockRouter.registerToolsFromDeclarations.mock.calls[0]?.[0];
      expect(passedTools).toHaveLength(2);
      expect(passedTools[0].name).toBe('tool_a');
      expect(passedTools[1].name).toBe('tool_b');
    });
  });

  describe('tool-router:unregister-tool', () => {
    it('accepts valid string name', () => {
      const result = invoke('tool-router:unregister-tool', 'my_tool') as any;
      expect(mockRouter.unregisterTool).toHaveBeenCalledWith('my_tool');
      expect(result.unregistered).toBe('my_tool');
    });

    it('throws on empty string', () => {
      expect(() => invoke('tool-router:unregister-tool', ''))
        .toThrow('requires a string name');
    });

    it('throws on non-string input', () => {
      expect(() => invoke('tool-router:unregister-tool', 123))
        .toThrow('requires a string name');
    });

    it('throws on null input', () => {
      expect(() => invoke('tool-router:unregister-tool', null))
        .toThrow('requires a string name');
    });

    it('throws on undefined input', () => {
      expect(() => invoke('tool-router:unregister-tool', undefined))
        .toThrow('requires a string name');
    });
  });

  // ── cLaw Gate Compliance ──────────────────────────────────────────

  describe('cLaw gate compliance', () => {
    it('does not register any persist/write/delete/execute channels', () => {
      const dangerousPatterns = /persist|save|write|delete|destroy|execute|run|invoke/i;
      for (const channel of handlers.keys()) {
        expect(channel).not.toMatch(dangerousPatterns);
      }
    });

    it('all query channels are pure delegation (no side effects)', () => {
      // Call all read-only channels and verify they delegate correctly
      invoke('tool-router:suggestions');
      invoke('tool-router:active-category');
      invoke('tool-router:category-scores');
      invoke('tool-router:snapshot');
      invoke('tool-router:context-string');
      invoke('tool-router:prompt-context');
      invoke('tool-router:status');
      invoke('tool-router:config');

      expect(mockRouter.route).toHaveBeenCalledOnce();
      expect(mockRouter.getActiveCategory).toHaveBeenCalledOnce();
      expect(mockRouter.getCategoryScores).toHaveBeenCalledOnce();
      expect(mockRouter.getSnapshot).toHaveBeenCalledOnce();
      expect(mockRouter.getContextString).toHaveBeenCalledOnce();
      expect(mockRouter.getPromptContext).toHaveBeenCalledOnce();
      expect(mockRouter.getStatus).toHaveBeenCalledOnce();
      expect(mockRouter.getConfig).toHaveBeenCalledOnce();
    });

    it('register-tools only calls registerToolsFromDeclarations', () => {
      invoke('tool-router:register-tools', [{ name: 'test_tool' }]);
      expect(mockRouter.registerToolsFromDeclarations).toHaveBeenCalledOnce();
      // Verify no other mutation methods were called
      expect(mockRouter.unregisterTool).not.toHaveBeenCalled();
    });

    it('unregister-tool only calls unregisterTool', () => {
      invoke('tool-router:unregister-tool', 'test_tool');
      expect(mockRouter.unregisterTool).toHaveBeenCalledOnce();
      expect(mockRouter.registerToolsFromDeclarations).not.toHaveBeenCalled();
    });
  });

  // ── Channel naming convention ──────────────────────────────────────

  describe('channel naming', () => {
    it('all channels use tool-router: prefix', () => {
      for (const channel of handlers.keys()) {
        expect(channel.startsWith('tool-router:')).toBe(true);
      }
    });

    it('channel names use kebab-case', () => {
      for (const channel of handlers.keys()) {
        const suffix = channel.replace('tool-router:', '');
        expect(suffix).toMatch(/^[a-z-]+$/);
      }
    });
  });
});
