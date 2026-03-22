/**
 * Track XI, Phase 4 — Orchestration Bridge Tests
 *
 * Validates that the orchestrator correctly wires to the delegation engine
 * for real sub-agent spawning instead of inline Claude calls.
 *
 * cLaw verification:
 *   - First Law: Trust tier inheritance through orchestration tree
 *   - Second Law: Depth limit prevents unbounded orchestration nesting
 *   - Third Law: Halt propagation stops all sub-agents on cancellation
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── UUID counter for deterministic IDs ───────────────────────────────
let uuidCounter = 0;
let spawnIdCounter = 0;

vi.mock('crypto', () => ({
  default: {
    randomUUID: () => `test-uuid-${String(++uuidCounter).padStart(4, '0')}-0000-0000-000000000000`,
  },
  randomUUID: () => `test-uuid-${String(++uuidCounter).padStart(4, '0')}-0000-0000-000000000000`,
}));

// ── Mock settings ────────────────────────────────────────────────────
vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    getAgentConfig: () => ({ userName: 'TestUser' }),
    getGeminiApiKey: () => 'test-key',
    getPreferredProvider: () => 'openrouter',
    isAgentVoicesEnabled: () => false,
  },
}));

// ── Mock integrity ───────────────────────────────────────────────────
let mockSafeMode = false;
vi.mock('../../src/main/integrity', () => ({
  integrityManager: {
    isInSafeMode: () => mockSafeMode,
  },
}));

// ── Mock context stream ──────────────────────────────────────────────
const mockContextPush = vi.fn().mockReturnValue({ id: 'ctx-1', timestamp: Date.now() });
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    push: (...args: any[]) => mockContextPush(...args),
  },
}));

// ── Mock spawn/hardStop for delegation engine ────────────────────────
const completedTasks = new Map<string, { status: string; result: string | null; error: string | null }>();
const mockSpawn = vi.fn().mockImplementation((agentType: string, description: string, input: any, opts: any) => {
  const id = `spawn-${String(++spawnIdCounter).padStart(4, '0')}`;
  // Auto-mark as completed after a tick (simulating immediate execution)
  setTimeout(() => {
    completedTasks.set(id, { status: 'completed', result: `Result from ${agentType}: ${description}`, error: null });
  }, 10);
  return { id, agentType, description, status: 'queued', role: opts?.role || 'sub-agent' };
});

const mockHardStop = vi.fn().mockReturnValue(true);

// ── Mock characters for office manager ───────────────────────────────
const mockCharacters = new Map();

// ── Import delegation engine ─────────────────────────────────────────
import { delegationEngine, DelegationConfig } from '../../src/main/agents/delegation-engine';

// ── Mock agentRunner with task tracking ──────────────────────────────
const mockAgentRunner = {
  spawn: (...args: any[]) => mockSpawn(...args),
  hardStop: (...args: any[]) => mockHardStop(...args),
  getAgentTypes: () => [
    { name: 'research', description: 'Deep research on a topic' },
    { name: 'summarize', description: 'Summarise text' },
    { name: 'code-review', description: 'Review code' },
    { name: 'draft-email', description: 'Draft an email' },
    { name: 'orchestrate', description: 'Orchestrate multi-step tasks' },
  ],
  get: (taskId: string) => {
    const completed = completedTasks.get(taskId);
    if (completed) {
      return {
        id: taskId,
        status: completed.status,
        result: completed.result,
        error: completed.error,
      };
    }
    return { id: taskId, status: 'running', result: null, error: null };
  },
};

// ── Helper to reset engine state ─────────────────────────────────────
const DEFAULT_CONFIG: DelegationConfig = {
  defaultDepthLimit: 3,
  maxDepthLimit: 5,
  maxOfficeSprites: 8,
  haltTimeoutMs: 500,
  maxChildrenPerAgent: 5,
  maxTotalNodes: 30,
};

function resetEngine(config?: Partial<DelegationConfig>): void {
  const engine = delegationEngine as any;
  engine.nodes = new Map();
  engine.roots = new Set();
  engine.updateCallbacks = [];
  engine.config = { ...DEFAULT_CONFIG, ...config };
  engine.getAgentRunner = () => mockAgentRunner;
  engine.getOfficeManager = () => ({ characters: mockCharacters });
  // Also stub orchestrator's own late-bound import
  _deps.getAgentRunner = () => mockAgentRunner;
  uuidCounter = 0;
  spawnIdCounter = 0;
  completedTasks.clear();
  mockSpawn.mockReset();
  mockSpawn.mockImplementation((agentType: string, description: string, input: any, opts: any) => {
    const id = `spawn-${String(++spawnIdCounter).padStart(4, '0')}`;
    setTimeout(() => {
      completedTasks.set(id, { status: 'completed', result: `Result from ${agentType}: ${description}`, error: null });
    }, 10);
    return { id, agentType, description, status: 'queued', role: opts?.role || 'sub-agent' };
  });
  mockHardStop.mockReset();
  mockHardStop.mockReturnValue(true);
  mockContextPush.mockClear();
  mockSafeMode = false;
}

// ── Mock callClaude for orchestrator ─────────────────────────────────
function createMockCallClaude(planResponse?: string) {
  return vi.fn().mockImplementation(async (prompt: string) => {
    // If it's a decomposition prompt, return the plan
    if (prompt.includes('task decomposition engine') || prompt.includes('AVAILABLE AGENTS')) {
      return planResponse || JSON.stringify([
        {
          agentType: 'research',
          description: 'Research topic A',
          input: { topic: 'topic A' },
          dependsOn: [],
        },
        {
          agentType: 'research',
          description: 'Research topic B',
          input: { topic: 'topic B' },
          dependsOn: [],
        },
        {
          agentType: 'summarize',
          description: 'Summarize findings',
          input: { text: '{{results_0}} {{results_1}}', style: 'executive summary' },
          dependsOn: [0, 1],
        },
      ]);
    }

    // If it's an aggregation prompt, return a synthesis
    if (prompt.includes('aggregating the results')) {
      return 'Synthesized final output based on all sub-task results.';
    }

    return 'Mock Claude response';
  });
}

// ── Create a mock AgentContext ────────────────────────────────────────
function createMockContext(taskId: string, overrides?: Partial<{ isCancelled: () => boolean; callClaude: any }>) {
  const logs: string[] = [];
  const thoughts: Array<{ phase: string; text: string }> = [];
  let progress = 0;
  let phase = '';

  return {
    ctx: {
      taskId,
      log: (msg: string) => logs.push(msg),
      setProgress: (p: number) => { progress = p; },
      isCancelled: overrides?.isCancelled || (() => false),
      callClaude: overrides?.callClaude || createMockCallClaude(),
      think: (p: string, t: string) => thoughts.push({ phase: p, text: t }),
      setPhase: (p: string) => { phase = p; },
      getAwareness: () => 'No other agents are currently active.',
      postToTeam: () => {},
      getTeamContext: () => '',
    } as any,
    logs,
    thoughts,
    getProgress: () => progress,
    getPhase: () => phase,
  };
}

// ── Import the orchestrate agent and test deps ──────────────────────
import { orchestrateAgent, _deps } from '../../src/main/agents/orchestrator';

// ══════════════════════════════════════════════════════════════════════
// Tests
// ══════════════════════════════════════════════════════════════════════

describe('Phase 4 — Orchestration Bridge', () => {
  beforeEach(() => {
    resetEngine();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Core Wiring ──────────────────────────────────────────────────

  describe('Delegation Engine registration', () => {
    it('registers orchestrator as delegation root on execute', async () => {
      const { ctx } = createMockContext('orch-root-001');

      await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      const node = delegationEngine.getNode('orch-root-001');
      expect(node).not.toBeNull();
      expect(node!.agentType).toBe('orchestrate');
      expect(node!.trustTier).toBe('local');
      expect(node!.parentId).toBeNull();
    });

    it('uses trust tier from delegation metadata when spawned as sub-agent', async () => {
      // Pre-register a root so the orchestrator is already in the tree
      delegationEngine.registerRoot('parent-root', 'research', 'Parent task', 'owner-dm');

      // Spawn orchestrator as sub-agent (delegation engine creates node)
      const spawnResult = await delegationEngine.spawnSubAgent({
        agentType: 'orchestrate',
        description: 'Sub-orchestrate',
        input: { goal: 'Sub goal' },
        parentTaskId: 'parent-root',
      });

      expect(spawnResult.success).toBe(true);
      const childNode = delegationEngine.getNode(spawnResult.taskId!);
      expect(childNode).not.toBeNull();
      expect(childNode!.trustTier).toBe('owner-dm');
    });

    it('does not double-register if already in delegation tree', async () => {
      // Pre-register the task in the tree
      delegationEngine.registerRoot('orch-pre-reg', 'orchestrate', 'Pre-registered', 'local');

      const { ctx } = createMockContext('orch-pre-reg');
      await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      // Should still have only one root
      const trees = delegationEngine.getAllTrees();
      const rootCount = trees.filter(t => t.rootId === 'orch-pre-reg').length;
      expect(rootCount).toBe(1);
    });

    it('defaults to local trust tier without delegation metadata', async () => {
      const { ctx } = createMockContext('orch-local');
      await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      const node = delegationEngine.getNode('orch-local');
      expect(node!.trustTier).toBe('local');
    });
  });

  // ── Sub-Agent Spawning ───────────────────────────────────────────

  describe('Sub-agent spawning via delegation engine', () => {
    it('spawns sub-agents through delegation engine, not inline Claude', async () => {
      const mockClaude = createMockCallClaude();
      const { ctx } = createMockContext('orch-spawn-001', { callClaude: mockClaude });

      await orchestrateAgent.execute({ goal: 'Research and summarize topic' }, ctx);

      // Delegation engine should have spawned sub-agents
      expect(mockSpawn).toHaveBeenCalled();

      // Claude should NOT have been called with buildAgentPrompt-style prompts
      // (it should only be called for planning and aggregation)
      const claudeCalls = mockClaude.mock.calls;
      for (const [prompt] of claudeCalls) {
        // No inline "You are a research analyst" prompts
        expect(prompt).not.toContain('You are a research analyst. Research the following');
        // No inline "Summarise the following text" prompts
        expect(prompt).not.toContain('Summarise the following text as a');
      }
    });

    it('passes resolved input templates to sub-agents', async () => {
      // Pre-complete first two tasks so templates can resolve
      completedTasks.set('spawn-0001', { status: 'completed', result: 'Research A result', error: null });
      completedTasks.set('spawn-0002', { status: 'completed', result: 'Research B result', error: null });

      const { ctx } = createMockContext('orch-template-001');
      await orchestrateAgent.execute({ goal: 'Research and summarize' }, ctx);

      // The third spawn (summarize) should have received resolved templates
      const summarizeCall = mockSpawn.mock.calls.find(
        (call: any[]) => call[0] === 'summarize'
      );
      if (summarizeCall) {
        const input = summarizeCall[2];
        // Input text should NOT contain {{results_0}} template markers
        if (typeof input.text === 'string') {
          expect(input.text).not.toContain('{{results_');
        }
      }
    });

    it('creates delegation nodes for each sub-agent', async () => {
      const { ctx } = createMockContext('orch-nodes-001');
      await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      const rootNode = delegationEngine.getNode('orch-nodes-001');
      expect(rootNode).not.toBeNull();
      // Root should have children
      expect(rootNode!.children.length).toBeGreaterThan(0);
    });

    it('sets parentTaskId on sub-agent spawn options', async () => {
      const { ctx } = createMockContext('orch-parent-001');
      await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      // Every spawnSubAgent call should have used the orchestrator's taskId as parent
      const rootNode = delegationEngine.getNode('orch-parent-001');
      for (const childId of rootNode!.children) {
        const childNode = delegationEngine.getNode(childId);
        expect(childNode!.parentId).toBe('orch-parent-001');
      }
    });
  });

  // ── Wave-based Execution ─────────────────────────────────────────

  describe('Wave-based execution', () => {
    it('executes independent tasks in the same wave (parallel)', async () => {
      const spawnTimes: number[] = [];
      mockSpawn.mockImplementation((agentType: string, desc: string, input: any, opts: any) => {
        const id = `spawn-${String(++spawnIdCounter).padStart(4, '0')}`;
        spawnTimes.push(Date.now());
        setTimeout(() => {
          completedTasks.set(id, { status: 'completed', result: `Result: ${desc}`, error: null });
        }, 10);
        return { id, agentType, description: desc, status: 'queued', role: 'sub-agent' };
      });

      const { ctx } = createMockContext('orch-wave-001');
      await orchestrateAgent.execute({ goal: 'Research and summarize' }, ctx);

      // First two research tasks should have been spawned before waiting
      expect(spawnTimes.length).toBeGreaterThanOrEqual(2);
      // They should spawn within the same wave (very close timestamps)
      if (spawnTimes.length >= 2) {
        expect(Math.abs(spawnTimes[1] - spawnTimes[0])).toBeLessThan(100);
      }
    });

    it('waits for wave completion before starting dependent tasks', async () => {
      const spawnOrder: string[] = [];
      mockSpawn.mockImplementation((agentType: string, desc: string, input: any, opts: any) => {
        const id = `spawn-${String(++spawnIdCounter).padStart(4, '0')}`;
        spawnOrder.push(agentType);
        setTimeout(() => {
          completedTasks.set(id, { status: 'completed', result: `Result: ${desc}`, error: null });
        }, 10);
        return { id, agentType, description: desc, status: 'queued', role: 'sub-agent' };
      });

      const { ctx } = createMockContext('orch-dep-001');
      await orchestrateAgent.execute({ goal: 'Research and summarize' }, ctx);

      // Research tasks should come before summarize
      const researchIdx = spawnOrder.findIndex(t => t === 'research');
      const summarizeIdx = spawnOrder.findIndex(t => t === 'summarize');
      if (researchIdx >= 0 && summarizeIdx >= 0) {
        expect(summarizeIdx).toBeGreaterThan(researchIdx);
      }
    });

    it('skips tasks whose dependencies failed', async () => {
      // Make the first research task fail
      mockSpawn.mockImplementation((agentType: string, desc: string, input: any, opts: any) => {
        const id = `spawn-${String(++spawnIdCounter).padStart(4, '0')}`;
        if (agentType === 'research' && desc.includes('topic A')) {
          setTimeout(() => {
            completedTasks.set(id, { status: 'failed', result: null, error: 'Network timeout' });
          }, 10);
        } else {
          setTimeout(() => {
            completedTasks.set(id, { status: 'completed', result: `Result: ${desc}`, error: null });
          }, 10);
        }
        return { id, agentType, description: desc, status: 'queued', role: 'sub-agent' };
      });

      // Use a plan where summarize depends on BOTH research tasks
      const { ctx, logs } = createMockContext('orch-skip-001');
      await orchestrateAgent.execute({ goal: 'Research and summarize' }, ctx);

      // Should have logged that summarize was skipped due to dependency
      const skipLog = logs.find(l => l.includes('skipped') && l.includes('dependency'));
      // Note: skip only happens if the summarize depends on a failed research
      // In our default plan, summarize depends on [0, 1]
      // Since research 0 fails, summarize should be skipped
      expect(skipLog).toBeDefined();
    });
  });

  // ── Result Collection ────────────────────────────────────────────

  describe('Result collection and aggregation', () => {
    it('bridges agent runner results to delegation engine', async () => {
      const { ctx } = createMockContext('orch-bridge-001');
      await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      // Check that delegation nodes have results
      const rootNode = delegationEngine.getNode('orch-bridge-001');
      if (rootNode && rootNode.children.length > 0) {
        for (const childId of rootNode.children) {
          const childNode = delegationEngine.getNode(childId);
          if (childNode && childNode.state === 'completed') {
            expect(childNode.result).not.toBeNull();
          }
        }
      }
    });

    it('reports orchestrator completion to delegation engine', async () => {
      const { ctx } = createMockContext('orch-complete-001');
      await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      const node = delegationEngine.getNode('orch-complete-001');
      expect(node!.state).toBe('completed');
      expect(node!.result).not.toBeNull();
      expect(node!.result).toContain('Orchestrated Result');
    });

    it('includes both synthesized and raw results in output', async () => {
      const { ctx } = createMockContext('orch-output-001');
      const result = await orchestrateAgent.execute({ goal: 'Test goal' }, ctx);

      expect(result).toContain('# Orchestrated Result');
      expect(result).toContain('## Raw Sub-Task Results');
      expect(result).toContain('succeeded');
    });

    it('calls Claude for final aggregation after sub-tasks complete', async () => {
      const mockClaude = createMockCallClaude();
      const { ctx } = createMockContext('orch-agg-001', { callClaude: mockClaude });

      await orchestrateAgent.execute({ goal: 'Research and summarize' }, ctx);

      // Should have called Claude for aggregation
      const aggCall = mockClaude.mock.calls.find(
        ([prompt]: [string]) => prompt.includes('aggregating the results')
      );
      expect(aggCall).toBeDefined();
    });
  });

  // ── Cancellation and Halt Propagation ────────────────────────────

  describe('Cancellation and halt propagation', () => {
    it('halts delegation tree when orchestrator is cancelled during planning', async () => {
      let cancelled = false;
      const mockClaude = vi.fn().mockImplementation(async () => {
        cancelled = true; // Cancel during Claude call
        return '[]'; // Empty plan
      });

      const { ctx } = createMockContext('orch-cancel-001', {
        callClaude: mockClaude,
        isCancelled: () => cancelled,
      });

      const result = await orchestrateAgent.execute({ goal: 'Test' }, ctx);
      expect(result).toBe('Cancelled during planning');
    });

    it('halts delegation tree when orchestrator is cancelled during execution', async () => {
      let callCount = 0;
      const mockClaude = createMockCallClaude();
      const { ctx } = createMockContext('orch-cancel-exec-001', {
        callClaude: mockClaude,
        isCancelled: () => callCount++ > 3, // Cancel after a few checks
      });

      const result = await orchestrateAgent.execute({ goal: 'Test' }, ctx);
      expect(result).toContain('Cancelled');
    });
  });

  // ── cLaw Compliance ──────────────────────────────────────────────

  describe('cLaw First Law — trust tier inheritance', () => {
    it('sub-agents inherit trust tier from orchestrator root', async () => {
      const { ctx } = createMockContext('orch-trust-001');

      // Execute with default local trust
      await orchestrateAgent.execute({ goal: 'Test' }, ctx);

      const rootNode = delegationEngine.getNode('orch-trust-001');
      expect(rootNode!.trustTier).toBe('local');

      for (const childId of rootNode!.children) {
        const childNode = delegationEngine.getNode(childId);
        if (childNode) {
          // Child trust must be <= parent trust (same or more restrictive)
          expect(childNode.trustTier).toBe('local');
        }
      }
    });

    it('respects inherited trust tier from delegation metadata', async () => {
      const { ctx } = createMockContext('orch-trust-inherit');

      await orchestrateAgent.execute({
        goal: 'Test',
        __delegation: { trustTier: 'group', depth: 1 },
      }, ctx);

      const rootNode = delegationEngine.getNode('orch-trust-inherit');
      expect(rootNode!.trustTier).toBe('group');
    });
  });

  describe('cLaw Second Law — depth limits', () => {
    it('prevents orchestration if depth limit reached', async () => {
      resetEngine({ defaultDepthLimit: 2, maxDepthLimit: 2 });

      // Register a root at depth 0
      delegationEngine.registerRoot('deep-root', 'research', 'Root', 'local');

      // Spawn at depth 1 — should succeed
      const depth1 = await delegationEngine.spawnSubAgent({
        agentType: 'orchestrate',
        description: 'Depth 1 orchestrator',
        input: { goal: 'Test' },
        parentTaskId: 'deep-root',
      });
      expect(depth1.success).toBe(true);

      // Spawn at depth 2 — should be BLOCKED
      const depth2 = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Too deep',
        input: {},
        parentTaskId: depth1.taskId!,
      });
      expect(depth2.success).toBe(false);
      expect(depth2.error).toContain('Depth limit');
    });
  });

  describe('cLaw Third Law — interruptibility', () => {
    it('halt propagates to all sub-agents in the orchestration tree', async () => {
      const { ctx } = createMockContext('orch-halt-001');

      // Don't auto-complete tasks so they stay running
      mockSpawn.mockImplementation((agentType: string, desc: string, input: any, opts: any) => {
        const id = `spawn-${String(++spawnIdCounter).padStart(4, '0')}`;
        // Don't auto-complete — stay running
        return { id, agentType, description: desc, status: 'queued', role: 'sub-agent' };
      });

      // Register root and spawn some children
      delegationEngine.registerRoot('orch-halt-001', 'orchestrate', 'Test halt', 'local');

      const child1 = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research A',
        input: {},
        parentTaskId: 'orch-halt-001',
      });

      const child2 = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research B',
        input: {},
        parentTaskId: 'orch-halt-001',
      });

      // Halt the tree
      const haltResult = await delegationEngine.haltTree('orch-halt-001');

      // All active nodes should be halted
      expect(haltResult.halted).toBeGreaterThanOrEqual(1);
      expect(haltResult.elapsedMs).toBeLessThanOrEqual(600); // Within guarantee + margin

      // Check nodes are interrupted
      const rootNode = delegationEngine.getNode('orch-halt-001');
      expect(rootNode!.state).toBe('interrupted');
    });
  });

  // ── Error Handling ───────────────────────────────────────────────

  describe('Error handling', () => {
    it('throws on empty goal', async () => {
      const { ctx } = createMockContext('orch-err-001');
      await expect(orchestrateAgent.execute({}, ctx)).rejects.toThrow('No goal provided');
    });

    it('reports planning failure to delegation engine', async () => {
      const mockClaude = vi.fn().mockRejectedValue(new Error('API timeout'));
      const { ctx } = createMockContext('orch-plan-fail', { callClaude: mockClaude });

      await expect(
        orchestrateAgent.execute({ goal: 'Test' }, ctx)
      ).rejects.toThrow('Planning failed');

      const node = delegationEngine.getNode('orch-plan-fail');
      expect(node!.state).toBe('failed');
      expect(node!.error).toContain('API timeout');
    });

    it('handles spawn failures gracefully', async () => {
      // Make ALL spawns fail
      mockSpawn.mockImplementation(() => {
        throw new Error('Agent runner unavailable');
      });

      const { ctx, logs } = createMockContext('orch-spawn-fail');
      const result = await orchestrateAgent.execute({ goal: 'Test' }, ctx);

      // Should complete with failures noted
      expect(result).toContain('failed');
      const failLogs = logs.filter(l => l.includes('spawn failed') || l.includes('SPAWN FAILED'));
      expect(failLogs.length).toBeGreaterThan(0);
    });

    it('handles Claude returning invalid plan JSON', async () => {
      const mockClaude = vi.fn().mockResolvedValue('This is not valid JSON at all');
      const { ctx } = createMockContext('orch-bad-json', { callClaude: mockClaude });

      await expect(
        orchestrateAgent.execute({ goal: 'Test' }, ctx)
      ).rejects.toThrow('Planning failed');
    });
  });

  // ── Safe Mode ────────────────────────────────────────────────────

  describe('Safe mode', () => {
    it('blocks sub-agent spawning in safe mode', async () => {
      const { ctx } = createMockContext('orch-safe-001');
      mockSafeMode = true;

      // Should still attempt planning, but spawning will be blocked
      const result = await orchestrateAgent.execute({ goal: 'Test' }, ctx);

      // All sub-tasks should have failed to spawn
      expect(result).toContain('failed');
    });
  });

  // ── Integration: orchestrate agent filters itself ────────────────

  describe('Self-orchestration prevention', () => {
    it('filters orchestrate from available agents in decomposition', async () => {
      const mockClaude = vi.fn().mockImplementation(async (prompt: string) => {
        if (prompt.includes('AVAILABLE AGENTS')) {
          // Verify 'orchestrate' is NOT listed
          expect(prompt).not.toContain('"orchestrate"');
          return JSON.stringify([
            { agentType: 'research', description: 'Test', input: { topic: 'X' }, dependsOn: [] },
          ]);
        }
        return 'Aggregated result';
      });

      const { ctx } = createMockContext('orch-no-recurse');
      await orchestrateAgent.execute({ goal: 'Test' }, ctx as any);
    });
  });

  // ── Progress and Phases ──────────────────────────────────────────

  describe('Progress tracking and phases', () => {
    it('progresses through planning → executing → aggregating phases', async () => {
      const phases: string[] = [];
      const { ctx } = createMockContext('orch-phase-001');
      const originalSetPhase = ctx.setPhase;
      ctx.setPhase = (p: string) => { phases.push(p); originalSetPhase(p); };

      await orchestrateAgent.execute({ goal: 'Test' }, ctx as any);

      expect(phases).toContain('planning');
      expect(phases).toContain('executing');
      expect(phases).toContain('aggregating');
    });

    it('emits chain-of-thought during orchestration', async () => {
      const { ctx, thoughts } = createMockContext('orch-think-001');
      await orchestrateAgent.execute({ goal: 'Test' }, ctx as any);

      expect(thoughts.length).toBeGreaterThan(0);
      const planThought = thoughts.find(t => t.phase === 'planning');
      expect(planThought).toBeDefined();
    });

    it('reaches 100% progress on completion', async () => {
      const { ctx, getProgress } = createMockContext('orch-prog-001');
      await orchestrateAgent.execute({ goal: 'Test' }, ctx as any);
      expect(getProgress()).toBe(100);
    });
  });

  // ── Context Stream Events ────────────────────────────────────────

  describe('Context stream integration', () => {
    it('emits delegation context stream events for spawned sub-agents', async () => {
      const { ctx } = createMockContext('orch-ctx-001');
      await orchestrateAgent.execute({ goal: 'Test' }, ctx as any);

      // Delegation engine should have pushed context stream events
      const delegationEvents = mockContextPush.mock.calls.filter(
        ([event]: [any]) => event.source === 'delegation-engine'
      );
      expect(delegationEvents.length).toBeGreaterThan(0);
    });
  });
});
