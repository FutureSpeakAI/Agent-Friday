/**
 * delegation-engine.ts — Recursive Agent Delegation Engine for Agent Friday.
 *
 * Track XI, Phase 3: The Delegation Engine.
 *
 * Extends flat dispatch (depth 1) to recursive delegation, enabling agents to
 * spawn sub-agents with:
 *   - Trust-tier inheritance (child ≤ parent, never escalates)
 *   - Configurable depth limit (default 3, max 5)
 *   - Context summarization at each delegation level
 *   - Tool access filtering based on inherited trust tier
 *   - Interruptibility guarantee (halt reaches all descendants within 500ms)
 *   - Partial result collection from interrupted children
 *   - Agent Office visualization integration (sprites for the delegation tree)
 *   - cLaw governance at every delegation boundary
 *
 * Architecture: COORDINATING LAYER over agent-runner.ts.
 *   - DelegationEngine tracks parent→child relationships and trust inheritance
 *   - agentRunner.spawn() handles actual execution with role='sub-agent'
 *   - officeManager receives delegation metadata for visual connections
 *
 * cLaw Safety Boundary:
 *   - Trust tier can ONLY degrade across delegation (never escalate)
 *   - Depth limit prevents unbounded recursion (circuit breaker)
 *   - All consent requirements propagate (children inherit parent consent state)
 *   - Halt signal broadcasts to entire tree (500ms interruptibility guarantee)
 *   - Safe mode auto-denies ALL delegation attempts
 *   - Each delegation boundary is logged for auditability
 *
 * Socratic Inquiry Answers (embedded in architecture):
 *   - Boundary: Trust tier, cLaw constraints, personality mode propagate; vault
 *     credentials, full memory, consent authorizations do NOT propagate
 *   - Depth: Default 3, max 5 — beyond 3 levels context degrades too much and
 *     user comprehension drops (Agent Office caps at 8 sprites)
 *   - Constraint: Child trust tier = min(parent trust tier, originating tier);
 *     child can NEVER have more authority than parent (cLaw First Law)
 *   - Precedent: Delegation is VERTICAL (parent→child hierarchy); teams are
 *     HORIZONTAL (shared goal, parallel). New module coordinating agent-runner.
 *   - Tension: Office capped at 8 concurrent sprites; excess delegates queue
 *     visually as "pending" without new sprites
 *   - Interruptibility: haltTree() broadcasts to all descendants via BFS,
 *     uses agentRunner.hardStop() per-node, collects partial results
 *   - Inversion: Depth limit + trust degradation + consent propagation prevents
 *     delegation chains that violate cLaw at depth N
 */

import crypto from 'crypto';
import { integrityManager } from '../integrity';
import { contextStream } from '../context-stream';

// ── Types ─────────────────────────────────────────────────────────────

/** Trust tiers — ordered from most to least privileged */
export type TrustTier = 'local' | 'owner-dm' | 'approved-dm' | 'group' | 'public';

/** Numeric ordering for trust tier comparison (lower = more trusted) */
const TRUST_TIER_ORDER: Record<TrustTier, number> = {
  'local': 0,
  'owner-dm': 1,
  'approved-dm': 2,
  'group': 3,
  'public': 4,
};

/** Delegation node — tracks a single agent in the delegation tree */
export interface DelegationNode {
  /** Unique ID for this delegation node (same as AgentTask.id) */
  taskId: string;
  /** Agent type being executed */
  agentType: string;
  /** Human-readable description of the sub-task */
  description: string;
  /** Parent node's taskId (null for root) */
  parentId: string | null;
  /** Current depth in the delegation tree (root = 0) */
  depth: number;
  /** Inherited trust tier (child ≤ parent) */
  trustTier: TrustTier;
  /** Current state of this delegation node */
  state: DelegationState;
  /** Summarized context passed from parent */
  contextSummary: string;
  /** Result from this agent (partial or complete) */
  result: string | null;
  /** Error message if failed */
  error: string | null;
  /** Child node taskIds */
  children: string[];
  /** Timestamp of creation */
  createdAt: number;
  /** Timestamp of completion */
  completedAt: number | null;
}

export type DelegationState =
  | 'pending'       // Waiting to be spawned
  | 'running'       // Actively executing
  | 'delegating'    // Spawning sub-agents
  | 'collecting'    // Gathering results from children
  | 'completed'     // Finished successfully
  | 'failed'        // Failed with error
  | 'interrupted'   // Halted by user or parent
  | 'depth-blocked'; // Blocked because depth limit reached

/** Configuration for the delegation engine */
export interface DelegationConfig {
  /** Default depth limit for delegation trees */
  defaultDepthLimit: number;
  /** Absolute maximum depth (cannot be exceeded even with explicit override) */
  maxDepthLimit: number;
  /** Maximum concurrent sprites in the Agent Office (visual cap) */
  maxOfficeSprites: number;
  /** Timeout for halt propagation in milliseconds */
  haltTimeoutMs: number;
  /** Maximum number of children a single agent can spawn */
  maxChildrenPerAgent: number;
  /** Maximum total nodes across all active delegation trees */
  maxTotalNodes: number;
}

/** Options for spawning a sub-agent */
export interface SpawnSubAgentOptions {
  /** Agent type to spawn */
  agentType: string;
  /** Description of the sub-task */
  description: string;
  /** Input data for the sub-agent */
  input: Record<string, unknown>;
  /** Parent task ID (required) */
  parentTaskId: string;
  /** Override depth limit for this tree (capped at maxDepthLimit) */
  depthLimit?: number;
  /** Override trust tier (can only be LOWER than parent, never higher) */
  trustTier?: TrustTier;
  /** Additional context from parent to summarize for child */
  parentContext?: string;
}

/** Result from spawning a sub-agent */
export interface SpawnResult {
  success: boolean;
  taskId?: string;
  error?: string;
  node?: DelegationNode;
}

/** Delegation tree — a complete view of a delegation hierarchy */
export interface DelegationTree {
  rootId: string;
  nodes: DelegationNode[];
  depth: number;
  trustTier: TrustTier;
  state: 'active' | 'completed' | 'interrupted';
  createdAt: number;
}

/** Halt result — what happened when we stopped a tree */
export interface HaltResult {
  halted: number;
  partialResults: Array<{
    taskId: string;
    agentType: string;
    result: string | null;
    state: DelegationState;
  }>;
  elapsedMs: number;
}

/** IPC event payload for delegation updates */
export interface DelegationUpdate {
  type: 'node-created' | 'node-updated' | 'node-completed' | 'node-halted' | 'tree-completed' | 'tree-halted';
  node?: DelegationNode;
  tree?: DelegationTree;
  rootId: string;
}

// ── Default Config ────────────────────────────────────────────────────

const DEFAULT_CONFIG: DelegationConfig = {
  defaultDepthLimit: 3,
  maxDepthLimit: 5,
  maxOfficeSprites: 8,
  haltTimeoutMs: 500,
  maxChildrenPerAgent: 5,
  maxTotalNodes: 30,
};

// ── Delegation Engine ─────────────────────────────────────────────────

class DelegationEngine {
  private nodes: Map<string, DelegationNode> = new Map();
  private roots: Set<string> = new Set();
  private config: DelegationConfig;
  private updateCallbacks: Array<(update: DelegationUpdate) => void> = [];

  // Late-bound import to avoid circular dependency
  private getAgentRunner() {
    return require('./agent-runner').agentRunner;
  }

  private getOfficeManager() {
    return require('../agent-office/office-manager').officeManager;
  }

  constructor(config?: Partial<DelegationConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    console.log('[DelegationEngine] Initialized — depth limit:', this.config.defaultDepthLimit,
      ', max:', this.config.maxDepthLimit, ', halt timeout:', this.config.haltTimeoutMs, 'ms');
  }

  /* ── Configuration ──────────────────────────────────────────────── */

  getConfig(): DelegationConfig {
    return { ...this.config };
  }

  updateConfig(updates: Partial<DelegationConfig>): void {
    if (updates.defaultDepthLimit !== undefined) {
      this.config.defaultDepthLimit = Math.min(
        Math.max(1, updates.defaultDepthLimit),
        this.config.maxDepthLimit
      );
    }
    if (updates.maxDepthLimit !== undefined) {
      this.config.maxDepthLimit = Math.min(Math.max(1, updates.maxDepthLimit), 5);
    }
    if (updates.maxOfficeSprites !== undefined) {
      this.config.maxOfficeSprites = Math.min(Math.max(2, updates.maxOfficeSprites), 12);
    }
    if (updates.haltTimeoutMs !== undefined) {
      this.config.haltTimeoutMs = Math.min(Math.max(100, updates.haltTimeoutMs), 2000);
    }
    if (updates.maxChildrenPerAgent !== undefined) {
      this.config.maxChildrenPerAgent = Math.min(Math.max(1, updates.maxChildrenPerAgent), 10);
    }
    if (updates.maxTotalNodes !== undefined) {
      this.config.maxTotalNodes = Math.min(Math.max(5, updates.maxTotalNodes), 100);
    }
  }

  /* ── Event Subscription ─────────────────────────────────────────── */

  onUpdate(callback: (update: DelegationUpdate) => void): () => void {
    this.updateCallbacks.push(callback);
    return () => {
      this.updateCallbacks = this.updateCallbacks.filter(cb => cb !== callback);
    };
  }

  private emitUpdate(update: DelegationUpdate): void {
    for (const cb of this.updateCallbacks) {
      try { cb(update); } catch { /* swallow callback errors */ }
    }
  }

  /* ── Root Agent Registration ────────────────────────────────────── */

  /**
   * Register an existing agent task as a delegation root.
   * Call this when the orchestrator or user spawns a top-level agent
   * that should be allowed to delegate sub-tasks.
   */
  registerRoot(taskId: string, agentType: string, description: string, trustTier: TrustTier = 'local'): DelegationNode {
    const node: DelegationNode = {
      taskId,
      agentType,
      description,
      parentId: null,
      depth: 0,
      trustTier,
      state: 'running',
      contextSummary: '',
      result: null,
      error: null,
      children: [],
      createdAt: Date.now(),
      completedAt: null,
    };

    this.nodes.set(taskId, node);
    this.roots.add(taskId);

    console.log(`[DelegationEngine] Root registered: ${agentType} (${taskId.slice(0, 8)}) trust=${trustTier}`);

    this.emitUpdate({ type: 'node-created', node, rootId: taskId });
    return node;
  }

  /* ── Sub-Agent Spawning ─────────────────────────────────────────── */

  /**
   * Spawn a sub-agent under a parent agent.
   *
   * cLaw enforcement:
   *   - Auto-deny in safe mode
   *   - Trust tier can only degrade (child ≤ parent)
   *   - Depth limit enforced (default 3, max 5)
   *   - Context summarized (full context not propagated)
   *   - Circuit breaker: max children per agent, max total nodes
   */
  async spawnSubAgent(options: SpawnSubAgentOptions): Promise<SpawnResult> {
    const { agentType, description, input, parentTaskId, parentContext } = options;

    // ── cLaw Gate: Safe mode check ────────────────────────────────
    if (integrityManager.isInSafeMode()) {
      console.warn('[DelegationEngine/cLaw] DENIED delegation — system is in safe mode');
      return { success: false, error: 'Delegation denied — system is in safe mode' };
    }

    // ── Resolve parent node ───────────────────────────────────────
    const parentNode = this.nodes.get(parentTaskId);
    if (!parentNode) {
      return { success: false, error: `Parent task ${parentTaskId} not found in delegation tree` };
    }

    // ── Depth limit check ─────────────────────────────────────────
    const depthLimit = options.depthLimit
      ? Math.min(options.depthLimit, this.config.maxDepthLimit)
      : this.config.defaultDepthLimit;

    const childDepth = parentNode.depth + 1;
    if (childDepth >= depthLimit) {
      console.warn(`[DelegationEngine] Depth limit reached: ${childDepth} >= ${depthLimit} for ${agentType}`);
      return {
        success: false,
        error: `Depth limit reached (${childDepth}/${depthLimit}). Cannot delegate further.`,
      };
    }

    // ── Children-per-agent limit ──────────────────────────────────
    if (parentNode.children.length >= this.config.maxChildrenPerAgent) {
      return {
        success: false,
        error: `Max children per agent reached (${this.config.maxChildrenPerAgent}). Cannot spawn more sub-agents.`,
      };
    }

    // ── Total nodes circuit breaker ───────────────────────────────
    if (this.nodes.size >= this.config.maxTotalNodes) {
      return {
        success: false,
        error: `Total delegation node limit reached (${this.config.maxTotalNodes}). Circuit breaker activated.`,
      };
    }

    // ── cLaw First Law: Trust tier can ONLY degrade ───────────────
    const parentTrustOrder = TRUST_TIER_ORDER[parentNode.trustTier];
    let childTrustTier = parentNode.trustTier;

    if (options.trustTier) {
      const requestedOrder = TRUST_TIER_ORDER[options.trustTier];
      if (requestedOrder < parentTrustOrder) {
        // Requested tier is MORE privileged than parent — cLaw violation
        console.warn(
          `[DelegationEngine/cLaw] Trust escalation BLOCKED: requested=${options.trustTier} parent=${parentNode.trustTier}`
        );
        childTrustTier = parentNode.trustTier; // Fall back to parent tier
      } else {
        childTrustTier = options.trustTier; // Degradation allowed
      }
    }

    // ── Context summarization ─────────────────────────────────────
    const contextSummary = this.summarizeContext(parentNode, parentContext);

    // ── Create delegation node ────────────────────────────────────
    const taskId = crypto.randomUUID();

    const childNode: DelegationNode = {
      taskId,
      agentType,
      description,
      parentId: parentTaskId,
      depth: childDepth,
      trustTier: childTrustTier,
      state: 'pending',
      contextSummary,
      result: null,
      error: null,
      children: [],
      createdAt: Date.now(),
      completedAt: null,
    };

    this.nodes.set(taskId, childNode);
    parentNode.children.push(taskId);
    parentNode.state = 'delegating';

    // ── Spawn via agent runner ─────────────────────────────────────
    try {
      const runner = this.getAgentRunner();

      // Inject delegation metadata into input
      const delegatedInput = {
        ...input,
        __delegation: {
          parentTaskId,
          depth: childDepth,
          trustTier: childTrustTier,
          contextSummary,
          depthLimit,
        },
      };

      const task = runner.spawn(agentType, description, delegatedInput, {
        parentId: parentTaskId,
        role: 'sub-agent' as const,
      });

      // Update node with actual task ID from runner (use runner's UUID)
      this.nodes.delete(taskId);
      childNode.taskId = task.id;
      this.nodes.set(task.id, childNode);

      // Update parent's children list
      const childIdx = parentNode.children.indexOf(taskId);
      if (childIdx >= 0) parentNode.children[childIdx] = task.id;

      childNode.state = 'running';

      // ── Office integration: set parentId on the sprite ──────────
      try {
        const office = this.getOfficeManager();
        const ch = office.characters?.get?.(task.id);
        if (ch) {
          ch.parentId = parentTaskId;
          ch.isSubAgent = true;
        }
      } catch { /* office may not be available */ }

      // ── Emit context stream event ───────────────────────────────
      try {
        contextStream.push({
          type: 'system',
          source: 'delegation-engine',
          summary: `Delegated ${agentType} sub-agent (depth ${childDepth}, trust=${childTrustTier}): ${description.slice(0, 100)}`,
          data: {
            parentTaskId,
            childTaskId: task.id,
            agentType,
            depth: childDepth,
            trustTier: childTrustTier,
          },
        });
      } catch { /* context stream may not be initialized */ }

      console.log(
        `[DelegationEngine] Spawned sub-agent: ${agentType} (${task.id.slice(0, 8)}) ` +
        `parent=${parentTaskId.slice(0, 8)} depth=${childDepth} trust=${childTrustTier}`
      );

      this.emitUpdate({ type: 'node-created', node: childNode, rootId: this.findRoot(task.id) });

      return { success: true, taskId: task.id, node: childNode };

    } catch (err) {
      childNode.state = 'failed';
      childNode.error = err instanceof Error ? err.message : String(err);
      childNode.completedAt = Date.now();

      console.error(`[DelegationEngine] Failed to spawn sub-agent ${agentType}:`, childNode.error);

      this.emitUpdate({
        type: 'node-updated',
        node: childNode,
        rootId: this.findRoot(childNode.taskId),
      });

      return { success: false, error: childNode.error };
    }
  }

  /* ── Result Collection ──────────────────────────────────────────── */

  /**
   * Report that a delegation node has completed.
   * Called by agent-runner when a sub-agent finishes.
   */
  reportCompletion(taskId: string, result: string | null, error: string | null): void {
    const node = this.nodes.get(taskId);
    if (!node) return;

    node.result = result;
    node.error = error;
    node.state = error ? 'failed' : 'completed';
    node.completedAt = Date.now();

    const rootId = this.findRoot(taskId);

    this.emitUpdate({ type: 'node-completed', node, rootId });

    // Check if parent's children are all done → parent can collect
    if (node.parentId) {
      const parentNode = this.nodes.get(node.parentId);
      if (parentNode && parentNode.state === 'delegating') {
        const allChildrenDone = parentNode.children.every(cid => {
          const child = this.nodes.get(cid);
          return child && (
            child.state === 'completed' ||
            child.state === 'failed' ||
            child.state === 'interrupted'
          );
        });
        if (allChildrenDone) {
          parentNode.state = 'collecting';
          this.emitUpdate({ type: 'node-updated', node: parentNode, rootId });
        }
      }
    }

    // Check if entire tree is complete
    if (this.roots.has(taskId)) {
      this.checkTreeCompletion(taskId);
    } else if (node.parentId) {
      this.checkTreeCompletion(this.findRoot(taskId));
    }

    console.log(
      `[DelegationEngine] Node ${node.state}: ${node.agentType} (${taskId.slice(0, 8)}) ` +
      `${error ? 'error=' + error.slice(0, 60) : 'result=' + (result?.slice(0, 60) || 'null')}`
    );
  }

  /**
   * Collect results from all children of a parent.
   * Returns combined results for the parent to use.
   */
  collectChildResults(parentTaskId: string): Array<{
    taskId: string;
    agentType: string;
    description: string;
    result: string | null;
    error: string | null;
    state: DelegationState;
  }> {
    const parentNode = this.nodes.get(parentTaskId);
    if (!parentNode) return [];

    return parentNode.children.map(cid => {
      const child = this.nodes.get(cid);
      if (!child) return {
        taskId: cid,
        agentType: 'unknown',
        description: 'unknown',
        result: null,
        error: 'Node not found',
        state: 'failed' as DelegationState,
      };
      return {
        taskId: child.taskId,
        agentType: child.agentType,
        description: child.description,
        result: child.result,
        error: child.error,
        state: child.state,
      };
    });
  }

  /* ── Halt Propagation (Interruptibility Guarantee) ──────────────── */

  /**
   * Halt an entire delegation tree from any node.
   *
   * Propagates halt signal to ALL descendants via BFS.
   * Uses agentRunner.hardStop() per node for immediate abort.
   *
   * cLaw interruptibility: guaranteed ≤500ms for the entire tree.
   */
  async haltTree(taskId: string): Promise<HaltResult> {
    const startTime = Date.now();
    const partialResults: HaltResult['partialResults'] = [];
    let halted = 0;

    // Find all nodes in this tree (BFS from the given node downward)
    const toHalt: string[] = [];
    const queue = [taskId];
    const visited = new Set<string>();

    while (queue.length > 0) {
      const current = queue.shift()!;
      if (visited.has(current)) continue;
      visited.add(current);

      toHalt.push(current);

      const node = this.nodes.get(current);
      if (node) {
        for (const childId of node.children) {
          if (!visited.has(childId)) queue.push(childId);
        }
      }
    }

    // Also halt upward if we're not at the root
    const node = this.nodes.get(taskId);
    if (node?.parentId && !visited.has(node.parentId)) {
      // Only halt descendants, not the parent — parent should handle child interruption
    }

    // Halt all nodes concurrently (Promise.all for speed)
    const runner = this.getAgentRunner();
    const haltPromises = toHalt.map(async (nodeId) => {
      const n = this.nodes.get(nodeId);
      if (!n) return;

      // Capture partial result before halting
      if (n.state === 'running' || n.state === 'delegating' || n.state === 'collecting') {
        partialResults.push({
          taskId: n.taskId,
          agentType: n.agentType,
          result: n.result,
          state: n.state,
        });

        // Hard stop via agent runner
        try {
          runner.hardStop(nodeId);
        } catch { /* may already be stopped */ }

        n.state = 'interrupted';
        n.completedAt = Date.now();
        halted++;

        this.emitUpdate({
          type: 'node-halted',
          node: n,
          rootId: this.findRoot(nodeId),
        });
      }
    });

    // Wait for all halts with timeout
    await Promise.race([
      Promise.all(haltPromises),
      new Promise<void>(resolve => setTimeout(resolve, this.config.haltTimeoutMs)),
    ]);

    const elapsedMs = Date.now() - startTime;

    // Emit tree-level halt event
    const rootId = this.findRoot(taskId);
    this.emitUpdate({
      type: 'tree-halted',
      rootId,
      tree: this.getTree(rootId) ?? undefined,
    });

    // Emit context stream event
    try {
      contextStream.push({
        type: 'system',
        source: 'delegation-engine',
        summary: `Delegation tree halted: ${halted} agents stopped in ${elapsedMs}ms`,
        data: { rootId: taskId, halted, elapsedMs, partialResults: partialResults.length },
      });
    } catch { /* swallow */ }

    console.log(
      `[DelegationEngine] Tree halt: ${halted} agents stopped in ${elapsedMs}ms ` +
      `(${partialResults.length} partial results captured) ` +
      `${elapsedMs <= this.config.haltTimeoutMs ? '✓ within guarantee' : '✗ EXCEEDED guarantee'}`
    );

    return { halted, partialResults, elapsedMs };
  }

  /**
   * Halt ALL active delegation trees. Emergency stop.
   */
  async haltAll(): Promise<{ treesHalted: number; totalAgents: number; elapsedMs: number }> {
    const startTime = Date.now();
    let totalAgents = 0;

    const results = await Promise.all(
      [...this.roots].map(rootId => this.haltTree(rootId))
    );

    for (const r of results) totalAgents += r.halted;

    return {
      treesHalted: results.filter(r => r.halted > 0).length,
      totalAgents,
      elapsedMs: Date.now() - startTime,
    };
  }

  /* ── Tree Queries ───────────────────────────────────────────────── */

  /**
   * Get the full delegation tree from a root node.
   */
  getTree(rootId: string): DelegationTree | null {
    if (!this.roots.has(rootId)) {
      // Maybe it's a child — find the root
      const actualRoot = this.findRoot(rootId);
      if (!this.roots.has(actualRoot)) return null;
      return this.getTree(actualRoot);
    }

    const rootNode = this.nodes.get(rootId);
    if (!rootNode) return null;

    // BFS to collect all nodes
    const nodes: DelegationNode[] = [];
    const queue = [rootId];
    const visited = new Set<string>();
    let maxDepth = 0;

    while (queue.length > 0) {
      const current = queue.shift()!;
      if (visited.has(current)) continue;
      visited.add(current);

      const node = this.nodes.get(current);
      if (node) {
        nodes.push(node);
        maxDepth = Math.max(maxDepth, node.depth);
        for (const childId of node.children) {
          if (!visited.has(childId)) queue.push(childId);
        }
      }
    }

    // Determine tree state
    const allDone = nodes.every(n =>
      n.state === 'completed' || n.state === 'failed' || n.state === 'interrupted'
    );
    const anyInterrupted = nodes.some(n => n.state === 'interrupted');

    return {
      rootId,
      nodes,
      depth: maxDepth,
      trustTier: rootNode.trustTier,
      state: allDone ? (anyInterrupted ? 'interrupted' : 'completed') : 'active',
      createdAt: rootNode.createdAt,
    };
  }

  /**
   * Get a specific delegation node.
   */
  getNode(taskId: string): DelegationNode | null {
    return this.nodes.get(taskId) || null;
  }

  /**
   * Get all active delegation trees.
   */
  getActiveTrees(): DelegationTree[] {
    const trees: DelegationTree[] = [];
    for (const rootId of this.roots) {
      const tree = this.getTree(rootId);
      if (tree && tree.state === 'active') {
        trees.push(tree);
      }
    }
    return trees;
  }

  /**
   * Get all delegation trees (active + completed).
   */
  getAllTrees(): DelegationTree[] {
    const trees: DelegationTree[] = [];
    for (const rootId of this.roots) {
      const tree = this.getTree(rootId);
      if (tree) trees.push(tree);
    }
    return trees;
  }

  /**
   * Get children of a specific node.
   */
  getChildren(taskId: string): DelegationNode[] {
    const node = this.nodes.get(taskId);
    if (!node) return [];
    return node.children
      .map(cid => this.nodes.get(cid))
      .filter((n): n is DelegationNode => n !== null && n !== undefined);
  }

  /**
   * Get the ancestry chain from root to this node.
   */
  getAncestry(taskId: string): DelegationNode[] {
    const chain: DelegationNode[] = [];
    let current = this.nodes.get(taskId);
    while (current) {
      chain.unshift(current);
      current = current.parentId ? this.nodes.get(current.parentId) : undefined;
    }
    return chain;
  }

  /**
   * Get the trust tier for a given task ID.
   * Returns 'public' (most restrictive) if not found — fail CLOSED.
   */
  getTrustTier(taskId: string): TrustTier {
    const node = this.nodes.get(taskId);
    return node ? node.trustTier : 'public';
  }

  /**
   * Check if a task is in a delegation tree.
   */
  isInTree(taskId: string): boolean {
    return this.nodes.has(taskId);
  }

  /**
   * Get statistics about the delegation engine.
   */
  getStats(): {
    totalNodes: number;
    activeNodes: number;
    activeTrees: number;
    maxDepthSeen: number;
    config: DelegationConfig;
  } {
    let activeNodes = 0;
    let maxDepthSeen = 0;

    for (const node of this.nodes.values()) {
      if (node.state === 'running' || node.state === 'delegating' || node.state === 'collecting') {
        activeNodes++;
      }
      maxDepthSeen = Math.max(maxDepthSeen, node.depth);
    }

    return {
      totalNodes: this.nodes.size,
      activeNodes,
      activeTrees: this.getActiveTrees().length,
      maxDepthSeen,
      config: { ...this.config },
    };
  }

  /* ── Cleanup ────────────────────────────────────────────────────── */

  /**
   * Remove completed delegation trees older than the given age.
   */
  cleanup(maxAgeMs: number = 30 * 60 * 1000): number {
    const now = Date.now();
    let removed = 0;

    for (const rootId of [...this.roots]) {
      const tree = this.getTree(rootId);
      if (!tree) continue;

      if (tree.state !== 'active') {
        const oldestCompletion = Math.max(
          ...tree.nodes.map(n => n.completedAt || n.createdAt)
        );
        if (now - oldestCompletion > maxAgeMs) {
          // Remove all nodes in this tree
          for (const node of tree.nodes) {
            this.nodes.delete(node.taskId);
            removed++;
          }
          this.roots.delete(rootId);
        }
      }
    }

    if (removed > 0) {
      console.log(`[DelegationEngine] Cleanup: removed ${removed} nodes from completed trees`);
    }

    return removed;
  }

  /* ── Context Delegation (AgentContext Extension) ─────────────────── */

  /**
   * Create a delegation-aware context extension for an agent.
   *
   * Returns an object with spawnSubAgent and collectResults methods
   * that agents can use during execution to delegate sub-tasks.
   */
  createDelegationContext(taskId: string): {
    spawnSubAgent: (agentType: string, description: string, input: Record<string, unknown>, parentContext?: string) => Promise<SpawnResult>;
    collectResults: () => Array<{ taskId: string; agentType: string; description: string; result: string | null; error: string | null; state: DelegationState }>;
    waitForChildren: () => Promise<void>;
    getDepth: () => number;
    getTrustTier: () => TrustTier;
    canDelegate: () => boolean;
  } {
    const engine = this;
    const node = this.nodes.get(taskId);

    return {
      spawnSubAgent: async (agentType, description, input, parentContext) => {
        return engine.spawnSubAgent({
          agentType,
          description,
          input,
          parentTaskId: taskId,
          parentContext,
        });
      },

      collectResults: () => {
        return engine.collectChildResults(taskId);
      },

      waitForChildren: async () => {
        // Poll until all children are complete
        const maxWait = 5 * 60 * 1000; // 5 minute max wait
        const start = Date.now();
        while (Date.now() - start < maxWait) {
          const parent = engine.nodes.get(taskId);
          if (!parent || parent.children.length === 0) return;

          const allDone = parent.children.every(cid => {
            const child = engine.nodes.get(cid);
            return child && (
              child.state === 'completed' ||
              child.state === 'failed' ||
              child.state === 'interrupted'
            );
          });

          if (allDone) return;
          await new Promise(resolve => setTimeout(resolve, 500));
        }
      },

      getDepth: () => node?.depth ?? 0,
      getTrustTier: () => node?.trustTier ?? 'public',
      canDelegate: () => {
        if (!node) return false;
        return node.depth + 1 < engine.config.defaultDepthLimit;
      },
    };
  }

  /* ── Private Helpers ────────────────────────────────────────────── */

  /**
   * Find the root taskId for any node in a delegation tree.
   */
  private findRoot(taskId: string): string {
    let current = this.nodes.get(taskId);
    while (current?.parentId) {
      const parent = this.nodes.get(current.parentId);
      if (!parent) break;
      current = parent;
    }
    return current?.taskId || taskId;
  }

  /**
   * Summarize parent context for child consumption.
   * Prevents full context propagation — each level gets a condensed view.
   */
  private summarizeContext(parentNode: DelegationNode, additionalContext?: string): string {
    const parts: string[] = [];

    // Ancestry chain summary
    const ancestry = this.getAncestry(parentNode.taskId);
    if (ancestry.length > 1) {
      parts.push(
        'Task chain: ' +
        ancestry.map(n => `${n.agentType}("${n.description.slice(0, 40)}")`).join(' → ')
      );
    }

    // Parent's description
    parts.push(`Parent task: ${parentNode.description.slice(0, 200)}`);

    // Parent's trust tier
    parts.push(`Trust level: ${parentNode.trustTier}`);

    // Current depth
    parts.push(`Delegation depth: ${parentNode.depth + 1}`);

    // Sibling context (what other sub-agents are doing)
    const siblings = parentNode.children
      .map(cid => this.nodes.get(cid))
      .filter((n): n is DelegationNode => n !== null && n !== undefined && n.state === 'running');
    if (siblings.length > 0) {
      parts.push(
        'Sibling agents: ' +
        siblings.map(s => `${s.agentType}("${s.description.slice(0, 30)}")`).join(', ')
      );
    }

    // Additional context from parent (truncated)
    if (additionalContext) {
      parts.push(`Parent context: ${additionalContext.slice(0, 500)}`);
    }

    return parts.join('\n');
  }

  /**
   * Check if an entire tree is complete and emit tree-completed event.
   */
  private checkTreeCompletion(rootId: string): void {
    const tree = this.getTree(rootId);
    if (!tree) return;

    if (tree.state !== 'active') {
      this.emitUpdate({ type: 'tree-completed', tree, rootId });

      console.log(
        `[DelegationEngine] Tree ${tree.state}: root=${rootId.slice(0, 8)} ` +
        `nodes=${tree.nodes.length} depth=${tree.depth}`
      );
    }
  }
}

// ── Singleton Export ───────────────────────────────────────────────────

export const delegationEngine = new DelegationEngine();
