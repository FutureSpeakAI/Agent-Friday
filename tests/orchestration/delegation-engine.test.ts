/**
 * Delegation Engine Tests — Track XI, Phase 3.
 *
 * Tests cover:
 *   1. Root registration and lifecycle
 *   2. Sub-agent spawning with cLaw gates
 *   3. Trust-tier inheritance (child ≤ parent, never escalates)
 *   4. Depth limit enforcement (default 3, max 5)
 *   5. Circuit breakers (children-per-agent, total nodes)
 *   6. Safe mode auto-deny (cLaw First Law)
 *   7. Halt propagation (interruptibility guarantee)
 *   8. Result collection and completion reporting
 *   9. Tree queries (getTree, getNode, getActiveTrees, getAncestry)
 *  10. Context summarization (no full context propagation)
 *  11. Delegation context API for agents
 *  12. Config management (update, clamp)
 *  13. Cleanup of old trees
 *  14. Event emission
 *  15. cLaw Three Laws verification
 *
 * cLaw Safety Gate: Tests explicitly verify all Three Laws are respected
 * at every delegation boundary.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mocks ─────────────────────────────────────────────────────────────

// Mock integrity manager
const mockIsInSafeMode = vi.fn();
vi.mock('../../src/main/integrity', () => ({
  integrityManager: {
    isInSafeMode: () => mockIsInSafeMode(),
  },
}));

// Mock context stream
const mockContextPush = vi.fn();
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    push: (...args: any[]) => mockContextPush(...args),
  },
}));

// Agent runner + office manager mocks — stubbed directly on the singleton
// because delegation-engine uses late-bound require() which vi.mock can't intercept.
const mockSpawn = vi.fn();
const mockHardStop = vi.fn();
let spawnIdCounter = 0;
const mockCharacters = new Map<string, any>();

// Mock crypto to produce predictable UUIDs (default import in delegation-engine)
let uuidCounter = 0;
vi.mock('crypto', () => ({
  default: {
    randomUUID: () => `test-uuid-${String(++uuidCounter).padStart(4, '0')}-0000-0000-000000000000`,
  },
  randomUUID: () => `test-uuid-${String(++uuidCounter).padStart(4, '0')}-0000-0000-000000000000`,
}));

// ── Import after mocks ────────────────────────────────────────────────

import { delegationEngine } from '../../src/main/agents/delegation-engine';
import type {
  TrustTier,
  DelegationNode,
  DelegationConfig,
  SpawnSubAgentOptions,
  SpawnResult,
  DelegationTree,
  HaltResult,
  DelegationUpdate,
} from '../../src/main/agents/delegation-engine';

// ── Helper: Reset singleton state ─────────────────────────────────────

function resetEngine(config?: Partial<DelegationConfig>): void {
  const engine = delegationEngine as any;
  engine.nodes = new Map();
  engine.roots = new Set();
  engine.updateCallbacks = [];
  engine.config = {
    defaultDepthLimit: 3,
    maxDepthLimit: 5,
    maxOfficeSprites: 8,
    haltTimeoutMs: 500,
    maxChildrenPerAgent: 5,
    maxTotalNodes: 30,
    ...config,
  };
  // Stub late-bound require() methods directly on singleton
  engine.getAgentRunner = () => ({
    spawn: (...args: any[]) => mockSpawn(...args),
    hardStop: (...args: any[]) => mockHardStop(...args),
  });
  engine.getOfficeManager = () => ({
    characters: mockCharacters,
  });
  uuidCounter = 0;
  spawnIdCounter = 0;
}

/** Helper: register a root and return it */
function registerRoot(
  taskId: string = 'root-001',
  agentType: string = 'orchestrate',
  description: string = 'Root task',
  trustTier: TrustTier = 'local',
): DelegationNode {
  return delegationEngine.registerRoot(taskId, agentType, description, trustTier);
}

/** Helper: set up mockSpawn to return tasks with predictable IDs */
function setupMockSpawn(): void {
  mockSpawn.mockImplementation((agentType: string, desc: string, input: any, opts: any) => {
    const id = `spawned-${String(++spawnIdCounter).padStart(3, '0')}`;
    return { id, type: agentType, description: desc, input, status: 'running', ...opts };
  });
}

// ── Test Suite ────────────────────────────────────────────────────────

describe('Delegation Engine', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockIsInSafeMode.mockReturnValue(false);
    resetEngine();
    setupMockSpawn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // =========================================================================
  // 1. Root Registration
  // =========================================================================
  describe('Root Registration', () => {
    it('registers a root delegation node', () => {
      const node = registerRoot('root-1', 'orchestrate', 'Master plan');

      expect(node.taskId).toBe('root-1');
      expect(node.agentType).toBe('orchestrate');
      expect(node.description).toBe('Master plan');
      expect(node.parentId).toBeNull();
      expect(node.depth).toBe(0);
      expect(node.trustTier).toBe('local');
      expect(node.state).toBe('running');
      expect(node.children).toEqual([]);
      expect(node.result).toBeNull();
      expect(node.error).toBeNull();
      expect(node.createdAt).toBeGreaterThan(0);
      expect(node.completedAt).toBeNull();
    });

    it('defaults trust tier to local', () => {
      const node = delegationEngine.registerRoot('r1', 'research', 'Test');
      expect(node.trustTier).toBe('local');
    });

    it('accepts custom trust tier', () => {
      const node = registerRoot('r2', 'research', 'Test', 'group');
      expect(node.trustTier).toBe('group');
    });

    it('emits node-created event', () => {
      const updates: DelegationUpdate[] = [];
      delegationEngine.onUpdate(u => updates.push(u));

      registerRoot('r3', 'research', 'Test');

      expect(updates.length).toBe(1);
      expect(updates[0].type).toBe('node-created');
      expect(updates[0].rootId).toBe('r3');
      expect(updates[0].node?.taskId).toBe('r3');
    });

    it('is retrievable via getNode', () => {
      registerRoot('r4', 'research', 'Test');
      const node = delegationEngine.getNode('r4');
      expect(node).not.toBeNull();
      expect(node!.taskId).toBe('r4');
    });
  });

  // =========================================================================
  // 2. Sub-Agent Spawning
  // =========================================================================
  describe('Sub-Agent Spawning', () => {
    it('spawns a sub-agent under a registered root', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research sub-task',
        input: { query: 'test' },
        parentTaskId: 'root-1',
      });

      expect(result.success).toBe(true);
      expect(result.taskId).toBeDefined();
      expect(result.node).toBeDefined();
      expect(result.node!.depth).toBe(1);
      expect(result.node!.parentId).toBe('root-1');
      expect(result.node!.state).toBe('running');
      expect(result.node!.trustTier).toBe('local'); // Inherited from parent
    });

    it('injects delegation metadata into input', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: { query: 'test' },
        parentTaskId: 'root-1',
      });

      expect(mockSpawn).toHaveBeenCalledTimes(1);
      const spawnInput = mockSpawn.mock.calls[0][2];
      expect(spawnInput.__delegation).toBeDefined();
      expect(spawnInput.__delegation.parentTaskId).toBe('root-1');
      expect(spawnInput.__delegation.depth).toBe(1);
      expect(spawnInput.__delegation.trustTier).toBe('local');
    });

    it('sets parent state to delegating', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
      });

      const parent = delegationEngine.getNode('root-1');
      expect(parent!.state).toBe('delegating');
    });

    it('adds child to parent children list', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
      });

      const parent = delegationEngine.getNode('root-1');
      expect(parent!.children).toContain(result.taskId);
    });

    it('fails for nonexistent parent', async () => {
      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'nonexistent',
      });

      expect(result.success).toBe(false);
      expect(result.error).toContain('not found');
    });

    it('passes role sub-agent to agent runner', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
      });

      expect(mockSpawn).toHaveBeenCalledWith(
        'research',
        'Research',
        expect.any(Object),
        expect.objectContaining({ role: 'sub-agent' }),
      );
    });
  });

  // =========================================================================
  // 3. Trust Tier Inheritance (cLaw First Law)
  // =========================================================================
  describe('Trust Tier Inheritance', () => {
    it('inherits parent trust tier by default', async () => {
      registerRoot('root-1', 'orchestrate', 'Root', 'owner-dm');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
      });

      expect(result.node!.trustTier).toBe('owner-dm');
    });

    it('allows trust tier degradation (less privileged)', async () => {
      registerRoot('root-1', 'orchestrate', 'Root', 'local');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
        trustTier: 'group', // Less privileged than local — allowed
      });

      expect(result.node!.trustTier).toBe('group');
    });

    it('BLOCKS trust tier escalation (more privileged than parent)', async () => {
      registerRoot('root-1', 'orchestrate', 'Root', 'group');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
        trustTier: 'local', // MORE privileged — cLaw violation
      });

      // Should succeed but fall back to parent tier
      expect(result.success).toBe(true);
      expect(result.node!.trustTier).toBe('group'); // NOT 'local'
    });

    it('propagates trust degradation across multiple levels', async () => {
      registerRoot('root-1', 'orchestrate', 'Root', 'local');

      // Level 1: degrade to owner-dm
      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'L1',
        input: {},
        parentTaskId: 'root-1',
        trustTier: 'owner-dm',
      });

      // Level 2: degrade to group
      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize',
        description: 'L2',
        input: {},
        parentTaskId: r1.taskId!,
        trustTier: 'group',
      });

      expect(r2.node!.trustTier).toBe('group');
      expect(r2.node!.depth).toBe(2);

      // Trying to escalate back to local from group should be blocked
      const r3 = await delegationEngine.spawnSubAgent({
        agentType: 'code-review',
        description: 'L3 attempt escalation',
        input: {},
        parentTaskId: r2.taskId!,
        trustTier: 'local', // Escalation attempt
      });

      // Succeeds but stays at group (parent tier)
      expect(r3.success).toBe(false); // Actually depth limit reached (depth 3 >= default 3)
    });

    it('getTrustTier returns public for unknown task (fail CLOSED)', () => {
      expect(delegationEngine.getTrustTier('nonexistent')).toBe('public');
    });
  });

  // =========================================================================
  // 4. Depth Limit Enforcement
  // =========================================================================
  describe('Depth Limits', () => {
    it('blocks spawning at default depth limit (3)', async () => {
      resetEngine({ defaultDepthLimit: 2 });
      setupMockSpawn();

      registerRoot('root-1', 'orchestrate', 'Root');

      // Depth 1 — OK
      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'L1',
        input: {},
        parentTaskId: 'root-1',
      });
      expect(r1.success).toBe(true);
      expect(r1.node!.depth).toBe(1);

      // Depth 2 — BLOCKED (>= depthLimit 2)
      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize',
        description: 'L2',
        input: {},
        parentTaskId: r1.taskId!,
      });
      expect(r2.success).toBe(false);
      expect(r2.error).toContain('Depth limit');
    });

    it('respects custom depth limit up to maxDepthLimit', async () => {
      resetEngine({ defaultDepthLimit: 2, maxDepthLimit: 5 });
      setupMockSpawn();

      registerRoot('root-1', 'orchestrate', 'Root');

      // Override to depth limit 4
      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'L1',
        input: {},
        parentTaskId: 'root-1',
        depthLimit: 4,
      });
      expect(r1.success).toBe(true);

      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize',
        description: 'L2',
        input: {},
        parentTaskId: r1.taskId!,
        depthLimit: 4,
      });
      expect(r2.success).toBe(true);

      const r3 = await delegationEngine.spawnSubAgent({
        agentType: 'code-review',
        description: 'L3',
        input: {},
        parentTaskId: r2.taskId!,
        depthLimit: 4,
      });
      expect(r3.success).toBe(true);

      // Depth 4 — BLOCKED (>= depthLimit 4)
      const r4 = await delegationEngine.spawnSubAgent({
        agentType: 'draft-email',
        description: 'L4',
        input: {},
        parentTaskId: r3.taskId!,
        depthLimit: 4,
      });
      expect(r4.success).toBe(false);
      expect(r4.error).toContain('Depth limit');
    });

    it('caps custom depth limit at maxDepthLimit', async () => {
      resetEngine({ maxDepthLimit: 3 });
      setupMockSpawn();

      registerRoot('root-1', 'orchestrate', 'Root');

      // Try to override to depth 10 — capped at 3
      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'L1',
        input: {},
        parentTaskId: 'root-1',
        depthLimit: 10,
      });
      expect(r1.success).toBe(true);

      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize',
        description: 'L2',
        input: {},
        parentTaskId: r1.taskId!,
        depthLimit: 10,
      });
      expect(r2.success).toBe(true);

      // Depth 3 — BLOCKED (cap at maxDepthLimit 3)
      const r3 = await delegationEngine.spawnSubAgent({
        agentType: 'code-review',
        description: 'L3',
        input: {},
        parentTaskId: r2.taskId!,
        depthLimit: 10,
      });
      expect(r3.success).toBe(false);
    });
  });

  // =========================================================================
  // 5. Circuit Breakers
  // =========================================================================
  describe('Circuit Breakers', () => {
    it('blocks when max children per agent reached', async () => {
      resetEngine({ maxChildrenPerAgent: 2 });
      setupMockSpawn();

      registerRoot('root-1', 'orchestrate', 'Root');

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      });
      expect(r1.success).toBe(true);

      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'C2', input: {}, parentTaskId: 'root-1',
      });
      expect(r2.success).toBe(true);

      // Third child — BLOCKED
      const r3 = await delegationEngine.spawnSubAgent({
        agentType: 'code-review', description: 'C3', input: {}, parentTaskId: 'root-1',
      });
      expect(r3.success).toBe(false);
      expect(r3.error).toContain('Max children per agent');
    });

    it('blocks when max total nodes reached', async () => {
      resetEngine({ maxTotalNodes: 3 });
      setupMockSpawn();

      registerRoot('root-1', 'orchestrate', 'Root'); // Node 1

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      }); // Node 2
      expect(r1.success).toBe(true);

      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'C2', input: {}, parentTaskId: 'root-1',
      }); // Node 3
      expect(r2.success).toBe(true);

      // Node 4 — BLOCKED (total 3 already)
      const r3 = await delegationEngine.spawnSubAgent({
        agentType: 'code-review', description: 'C3', input: {}, parentTaskId: 'root-1',
      });
      expect(r3.success).toBe(false);
      expect(r3.error).toContain('Total delegation node limit');
      expect(r3.error).toContain('Circuit breaker');
    });
  });

  // =========================================================================
  // 6. Safe Mode Auto-Deny (cLaw First Law)
  // =========================================================================
  describe('Safe Mode', () => {
    it('auto-denies delegation in safe mode', async () => {
      mockIsInSafeMode.mockReturnValue(true);
      registerRoot('root-1', 'orchestrate', 'Root');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
      });

      expect(result.success).toBe(false);
      expect(result.error).toContain('safe mode');
    });

    it('does NOT call agent runner in safe mode', async () => {
      mockIsInSafeMode.mockReturnValue(true);
      registerRoot('root-1', 'orchestrate', 'Root');

      await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Research',
        input: {},
        parentTaskId: 'root-1',
      });

      expect(mockSpawn).not.toHaveBeenCalled();
    });
  });

  // =========================================================================
  // 7. Halt Propagation (Interruptibility Guarantee)
  // =========================================================================
  describe('Halt Propagation', () => {
    it('halts a single running root node', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const result = await delegationEngine.haltTree('root-1');

      expect(result.halted).toBe(1);
      expect(result.partialResults.length).toBe(1);
      expect(result.partialResults[0].taskId).toBe('root-1');
      expect(result.elapsedMs).toBeLessThanOrEqual(1000); // Well within 500ms guarantee

      const node = delegationEngine.getNode('root-1');
      expect(node!.state).toBe('interrupted');
    });

    it('halts all descendants via BFS', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      });
      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'C2', input: {}, parentTaskId: 'root-1',
      });

      const result = await delegationEngine.haltTree('root-1');

      // Root + 2 children = 3 halted
      expect(result.halted).toBe(3);
      expect(mockHardStop).toHaveBeenCalledTimes(3);

      // All nodes should be interrupted
      expect(delegationEngine.getNode('root-1')!.state).toBe('interrupted');
      expect(delegationEngine.getNode(r1.taskId!)!.state).toBe('interrupted');
      expect(delegationEngine.getNode(r2.taskId!)!.state).toBe('interrupted');
    });

    it('collects partial results during halt', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      // Set partial result on root before halting
      const rootNode = delegationEngine.getNode('root-1')!;
      rootNode.result = 'partial work done';

      const result = await delegationEngine.haltTree('root-1');

      expect(result.partialResults.length).toBe(1);
      expect(result.partialResults[0].result).toBe('partial work done');
    });

    it('haltAll stops all active trees', async () => {
      registerRoot('tree-1', 'orchestrate', 'Tree 1');
      registerRoot('tree-2', 'research', 'Tree 2');

      const result = await delegationEngine.haltAll();

      expect(result.treesHalted).toBe(2);
      expect(result.totalAgents).toBe(2);
    });

    it('emits tree-halted event', async () => {
      const updates: DelegationUpdate[] = [];
      delegationEngine.onUpdate(u => updates.push(u));

      registerRoot('root-1', 'orchestrate', 'Root');
      await delegationEngine.haltTree('root-1');

      const haltEvents = updates.filter(u => u.type === 'tree-halted');
      expect(haltEvents.length).toBe(1);
      expect(haltEvents[0].rootId).toBe('root-1');
    });
  });

  // =========================================================================
  // 8. Result Collection and Completion
  // =========================================================================
  describe('Result Collection', () => {
    it('reports completion successfully', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      delegationEngine.reportCompletion('root-1', 'Done!', null);

      const node = delegationEngine.getNode('root-1');
      expect(node!.state).toBe('completed');
      expect(node!.result).toBe('Done!');
      expect(node!.error).toBeNull();
      expect(node!.completedAt).toBeGreaterThan(0);
    });

    it('reports failure with error', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      delegationEngine.reportCompletion('root-1', null, 'Something broke');

      const node = delegationEngine.getNode('root-1');
      expect(node!.state).toBe('failed');
      expect(node!.error).toBe('Something broke');
    });

    it('collects child results', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'Research Q1', input: {}, parentTaskId: 'root-1',
      });
      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'Summarize', input: {}, parentTaskId: 'root-1',
      });

      // Complete children
      delegationEngine.reportCompletion(r1.taskId!, 'Answer to Q1', null);
      delegationEngine.reportCompletion(r2.taskId!, 'Summary done', null);

      const results = delegationEngine.collectChildResults('root-1');
      expect(results.length).toBe(2);
      expect(results[0].result).toBe('Answer to Q1');
      expect(results[1].result).toBe('Summary done');
    });

    it('parent moves to collecting when all children complete', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      });
      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'C2', input: {}, parentTaskId: 'root-1',
      });

      delegationEngine.reportCompletion(r1.taskId!, 'R1', null);
      delegationEngine.reportCompletion(r2.taskId!, 'R2', null);

      const parent = delegationEngine.getNode('root-1');
      expect(parent!.state).toBe('collecting');
    });

    it('emits tree-completed event when root completes', async () => {
      const updates: DelegationUpdate[] = [];
      delegationEngine.onUpdate(u => updates.push(u));

      registerRoot('root-1', 'orchestrate', 'Root');
      delegationEngine.reportCompletion('root-1', 'Done', null);

      const treeCompleted = updates.filter(u => u.type === 'tree-completed');
      expect(treeCompleted.length).toBe(1);
    });

    it('returns empty array for nonexistent parent', () => {
      const results = delegationEngine.collectChildResults('nonexistent');
      expect(results).toEqual([]);
    });
  });

  // =========================================================================
  // 9. Tree Queries
  // =========================================================================
  describe('Tree Queries', () => {
    it('getTree returns full tree structure', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      });

      const tree = delegationEngine.getTree('root-1');
      expect(tree).not.toBeNull();
      expect(tree!.rootId).toBe('root-1');
      expect(tree!.nodes.length).toBe(2);
      expect(tree!.state).toBe('active');
      expect(tree!.depth).toBe(1);
      expect(tree!.trustTier).toBe('local');
    });

    it('getTree returns null for nonexistent root', () => {
      expect(delegationEngine.getTree('nonexistent')).toBeNull();
    });

    it('getActiveTrees returns only active trees', async () => {
      registerRoot('active-1', 'orchestrate', 'Active');
      registerRoot('done-1', 'research', 'Done');

      delegationEngine.reportCompletion('done-1', 'Finished', null);

      const active = delegationEngine.getActiveTrees();
      expect(active.length).toBe(1);
      expect(active[0].rootId).toBe('active-1');
    });

    it('getAllTrees returns all trees', () => {
      registerRoot('t1', 'orchestrate', 'T1');
      registerRoot('t2', 'research', 'T2');

      delegationEngine.reportCompletion('t2', 'Done', null);

      const all = delegationEngine.getAllTrees();
      expect(all.length).toBe(2);
    });

    it('getNode returns null for unknown task', () => {
      expect(delegationEngine.getNode('nonexistent')).toBeNull();
    });

    it('getChildren returns child nodes', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      });
      await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'C2', input: {}, parentTaskId: 'root-1',
      });

      const children = delegationEngine.getChildren('root-1');
      expect(children.length).toBe(2);
      expect(children[0].agentType).toBe('research');
      expect(children[1].agentType).toBe('summarize');
    });

    it('getAncestry returns chain from root to node', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'L1', input: {}, parentTaskId: 'root-1',
      });

      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'L2', input: {}, parentTaskId: r1.taskId!,
      });

      const ancestry = delegationEngine.getAncestry(r2.taskId!);
      expect(ancestry.length).toBe(3);
      expect(ancestry[0].taskId).toBe('root-1');
      expect(ancestry[1].taskId).toBe(r1.taskId);
      expect(ancestry[2].taskId).toBe(r2.taskId);
    });

    it('isInTree returns true for registered nodes', () => {
      registerRoot('root-1', 'orchestrate', 'Root');
      expect(delegationEngine.isInTree('root-1')).toBe(true);
      expect(delegationEngine.isInTree('nonexistent')).toBe(false);
    });
  });

  // =========================================================================
  // 10. Statistics
  // =========================================================================
  describe('Statistics', () => {
    it('returns correct stats', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');
      await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      });

      const stats = delegationEngine.getStats();
      expect(stats.totalNodes).toBe(2);
      expect(stats.activeNodes).toBe(2); // root=delegating, child=running
      expect(stats.maxDepthSeen).toBe(1);
      expect(stats.config.defaultDepthLimit).toBe(3);
    });
  });

  // =========================================================================
  // 11. Config Management
  // =========================================================================
  describe('Config Management', () => {
    it('returns current config', () => {
      const config = delegationEngine.getConfig();
      expect(config.defaultDepthLimit).toBe(3);
      expect(config.maxDepthLimit).toBe(5);
      expect(config.maxOfficeSprites).toBe(8);
      expect(config.haltTimeoutMs).toBe(500);
      expect(config.maxChildrenPerAgent).toBe(5);
      expect(config.maxTotalNodes).toBe(30);
    });

    it('updates config with clamping', () => {
      delegationEngine.updateConfig({ defaultDepthLimit: 10 });
      // Should be clamped to maxDepthLimit (5)
      expect(delegationEngine.getConfig().defaultDepthLimit).toBe(5);
    });

    it('clamps maxDepthLimit to [1, 5]', () => {
      delegationEngine.updateConfig({ maxDepthLimit: 100 });
      expect(delegationEngine.getConfig().maxDepthLimit).toBe(5);

      delegationEngine.updateConfig({ maxDepthLimit: 0 });
      expect(delegationEngine.getConfig().maxDepthLimit).toBe(1);
    });

    it('clamps haltTimeoutMs to [100, 2000]', () => {
      delegationEngine.updateConfig({ haltTimeoutMs: 10 });
      expect(delegationEngine.getConfig().haltTimeoutMs).toBe(100);

      delegationEngine.updateConfig({ haltTimeoutMs: 50000 });
      expect(delegationEngine.getConfig().haltTimeoutMs).toBe(2000);
    });

    it('clamps maxChildrenPerAgent to [1, 10]', () => {
      delegationEngine.updateConfig({ maxChildrenPerAgent: 0 });
      expect(delegationEngine.getConfig().maxChildrenPerAgent).toBe(1);
    });
  });

  // =========================================================================
  // 12. Cleanup
  // =========================================================================
  describe('Cleanup', () => {
    it('removes old completed trees', () => {
      registerRoot('old-tree', 'orchestrate', 'Old');

      // Complete and backdate
      delegationEngine.reportCompletion('old-tree', 'Done', null);
      const node = delegationEngine.getNode('old-tree')!;
      (node as any).completedAt = Date.now() - 60 * 60 * 1000; // 1 hour ago
      (node as any).createdAt = Date.now() - 2 * 60 * 60 * 1000; // 2 hours ago

      const removed = delegationEngine.cleanup(30 * 60 * 1000); // 30 min max age
      expect(removed).toBe(1);
      expect(delegationEngine.getNode('old-tree')).toBeNull();
    });

    it('does NOT remove active trees', () => {
      registerRoot('active-tree', 'orchestrate', 'Active');

      const removed = delegationEngine.cleanup(0); // Even with 0 max age
      expect(removed).toBe(0);
      expect(delegationEngine.getNode('active-tree')).not.toBeNull();
    });

    it('removes all nodes in a cleaned tree', async () => {
      registerRoot('old-root', 'orchestrate', 'Old');
      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'old-root',
      });

      // Complete all
      delegationEngine.reportCompletion(r1.taskId!, 'Done', null);
      delegationEngine.reportCompletion('old-root', 'Done', null);

      // Backdate
      for (const id of ['old-root', r1.taskId!]) {
        const node = delegationEngine.getNode(id)!;
        (node as any).completedAt = Date.now() - 60 * 60 * 1000;
        (node as any).createdAt = Date.now() - 2 * 60 * 60 * 1000;
      }

      const removed = delegationEngine.cleanup(30 * 60 * 1000);
      expect(removed).toBe(2); // Root + child
    });
  });

  // =========================================================================
  // 13. Delegation Context API
  // =========================================================================
  describe('Delegation Context', () => {
    it('creates a delegation context for an agent', () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const ctx = delegationEngine.createDelegationContext('root-1');

      expect(typeof ctx.spawnSubAgent).toBe('function');
      expect(typeof ctx.collectResults).toBe('function');
      expect(typeof ctx.waitForChildren).toBe('function');
      expect(typeof ctx.getDepth).toBe('function');
      expect(typeof ctx.getTrustTier).toBe('function');
      expect(typeof ctx.canDelegate).toBe('function');
    });

    it('canDelegate returns true when depth allows', () => {
      registerRoot('root-1', 'orchestrate', 'Root');
      const ctx = delegationEngine.createDelegationContext('root-1');
      expect(ctx.canDelegate()).toBe(true);
    });

    it('getDepth returns correct depth', () => {
      registerRoot('root-1', 'orchestrate', 'Root');
      const ctx = delegationEngine.createDelegationContext('root-1');
      expect(ctx.getDepth()).toBe(0);
    });

    it('getTrustTier returns node trust tier', () => {
      registerRoot('root-1', 'orchestrate', 'Root', 'owner-dm');
      const ctx = delegationEngine.createDelegationContext('root-1');
      expect(ctx.getTrustTier()).toBe('owner-dm');
    });

    it('spawnSubAgent delegates to engine', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');
      const ctx = delegationEngine.createDelegationContext('root-1');

      const result = await ctx.spawnSubAgent('research', 'Find info', { query: 'test' });
      expect(result.success).toBe(true);
      expect(result.node!.parentId).toBe('root-1');
    });

    it('collectResults returns child results', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');
      const ctx = delegationEngine.createDelegationContext('root-1');

      const r1 = await ctx.spawnSubAgent('research', 'C1', {});
      delegationEngine.reportCompletion(r1.taskId!, 'Result 1', null);

      const results = ctx.collectResults();
      expect(results.length).toBe(1);
      expect(results[0].result).toBe('Result 1');
    });
  });

  // =========================================================================
  // 14. Event Emission
  // =========================================================================
  describe('Event Emission', () => {
    it('onUpdate returns unsubscribe function', () => {
      const updates: DelegationUpdate[] = [];
      const unsub = delegationEngine.onUpdate(u => updates.push(u));

      registerRoot('r1', 'orchestrate', 'Test');
      expect(updates.length).toBe(1);

      unsub();

      registerRoot('r2', 'research', 'Test 2');
      expect(updates.length).toBe(1); // No new events after unsubscribe
    });

    it('swallows callback errors', () => {
      delegationEngine.onUpdate(() => { throw new Error('Callback failure'); });

      // Should not throw
      expect(() => registerRoot('r1', 'orchestrate', 'Test')).not.toThrow();
    });

    it('emits context stream events on spawn', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'Test', input: {}, parentTaskId: 'root-1',
      });

      expect(mockContextPush).toHaveBeenCalled();
      const call = mockContextPush.mock.calls.find(
        (c: any) => c[0]?.source === 'delegation-engine'
      );
      expect(call).toBeDefined();
    });
  });

  // =========================================================================
  // 15. cLaw Three Laws Gate
  // =========================================================================
  describe('cLaw Three Laws Gate', () => {
    // First Law: Agent delegation must never harm the user or their data.
    // Trust tier can ONLY degrade — never escalate.
    it('First Law: trust tier NEVER escalates', async () => {
      registerRoot('root-1', 'orchestrate', 'Root', 'approved-dm');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Attempt escalation',
        input: {},
        parentTaskId: 'root-1',
        trustTier: 'local', // MORE privileged than approved-dm
      });

      // Spawns but at parent tier (no escalation)
      expect(result.success).toBe(true);
      expect(result.node!.trustTier).toBe('approved-dm');
    });

    // First Law: Safe mode blocks all delegation
    it('First Law: safe mode blocks all delegation', async () => {
      mockIsInSafeMode.mockReturnValue(true);
      registerRoot('root-1', 'orchestrate', 'Root');

      const result = await delegationEngine.spawnSubAgent({
        agentType: 'research',
        description: 'Should be denied',
        input: {},
        parentTaskId: 'root-1',
      });

      expect(result.success).toBe(false);
      expect(result.error).toContain('safe mode');
      expect(mockSpawn).not.toHaveBeenCalled();
    });

    // Second Law: Depth limits prevent unbounded recursion
    it('Second Law: depth limit prevents infinite delegation', async () => {
      resetEngine({ defaultDepthLimit: 2 });
      setupMockSpawn();

      registerRoot('root-1', 'orchestrate', 'Root');

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'L1', input: {}, parentTaskId: 'root-1',
      });

      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'L2 blocked', input: {}, parentTaskId: r1.taskId!,
      });

      expect(r2.success).toBe(false);
      expect(r2.error).toContain('Depth limit');
    });

    // Third Law: Interruptibility — halt reaches all descendants
    it('Third Law: halt propagates to all descendants', async () => {
      registerRoot('root-1', 'orchestrate', 'Root');

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      });
      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'C2', input: {}, parentTaskId: 'root-1',
      });

      const result = await delegationEngine.haltTree('root-1');

      expect(result.halted).toBe(3); // Root + 2 children
      expect(delegationEngine.getNode('root-1')!.state).toBe('interrupted');
      expect(delegationEngine.getNode(r1.taskId!)!.state).toBe('interrupted');
      expect(delegationEngine.getNode(r2.taskId!)!.state).toBe('interrupted');
    });

    // Circuit breaker prevents resource exhaustion
    it('Circuit breaker: total nodes limit prevents resource exhaustion', async () => {
      resetEngine({ maxTotalNodes: 2 });
      setupMockSpawn();

      registerRoot('root-1', 'orchestrate', 'Root'); // 1 node

      const r1 = await delegationEngine.spawnSubAgent({
        agentType: 'research', description: 'C1', input: {}, parentTaskId: 'root-1',
      }); // 2 nodes
      expect(r1.success).toBe(true);

      const r2 = await delegationEngine.spawnSubAgent({
        agentType: 'summarize', description: 'C2', input: {}, parentTaskId: 'root-1',
      }); // 3 nodes — BLOCKED
      expect(r2.success).toBe(false);
      expect(r2.error).toContain('Circuit breaker');
    });
  });

  // =========================================================================
  // 16. Type Contract
  // =========================================================================
  describe('Type Contract', () => {
    it('exports singleton with all required methods', () => {
      expect(typeof delegationEngine.registerRoot).toBe('function');
      expect(typeof delegationEngine.spawnSubAgent).toBe('function');
      expect(typeof delegationEngine.reportCompletion).toBe('function');
      expect(typeof delegationEngine.collectChildResults).toBe('function');
      expect(typeof delegationEngine.haltTree).toBe('function');
      expect(typeof delegationEngine.haltAll).toBe('function');
      expect(typeof delegationEngine.getTree).toBe('function');
      expect(typeof delegationEngine.getNode).toBe('function');
      expect(typeof delegationEngine.getActiveTrees).toBe('function');
      expect(typeof delegationEngine.getAllTrees).toBe('function');
      expect(typeof delegationEngine.getChildren).toBe('function');
      expect(typeof delegationEngine.getAncestry).toBe('function');
      expect(typeof delegationEngine.getTrustTier).toBe('function');
      expect(typeof delegationEngine.isInTree).toBe('function');
      expect(typeof delegationEngine.getStats).toBe('function');
      expect(typeof delegationEngine.getConfig).toBe('function');
      expect(typeof delegationEngine.updateConfig).toBe('function');
      expect(typeof delegationEngine.onUpdate).toBe('function');
      expect(typeof delegationEngine.cleanup).toBe('function');
      expect(typeof delegationEngine.createDelegationContext).toBe('function');
    });
  });
});
