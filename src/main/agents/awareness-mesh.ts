/**
 * Track XI, Phase 5 — Awareness Mesh
 *
 * Unified inter-agent coordination layer that provides:
 *   - Cross-tree awareness (agents in different delegation trees know about each other)
 *   - Dependency declarations (agent A depends on agent B's output)
 *   - Mesh-wide broadcasting (share results beyond team/tree boundaries)
 *   - Deadlock detection (circular dependencies flagged and broken)
 *   - Rich awareness context generation (combines delegation + team + mesh data)
 *
 * The mesh sits above agent-runner and delegation-engine, unifying their
 * separate awareness systems into a single queryable layer.
 *
 * cLaw compliance:
 *   First Law: Broadcasts respect trust tiers — high-trust output can't flow
 *              to lower-trust agents without explicit downgrade
 *   Third Law: Mesh operations are non-blocking; agents can always be halted
 */

import { AgentRole } from './agent-types';
import { contextStream } from '../context-stream';

/* ── Types ──────────────────────────────────────────────────────────── */

export type TrustTier = 'local' | 'owner-dm' | 'approved-dm' | 'group' | 'public';

const TRUST_TIER_ORDER: Record<TrustTier, number> = {
  local: 0,
  'owner-dm': 1,
  'approved-dm': 2,
  group: 3,
  public: 4,
};

export interface MeshAgent {
  taskId: string;
  agentType: string;
  description: string;
  phase: string;
  progress: number;
  role: AgentRole;
  trustTier: TrustTier;
  teamId?: string;
  treeRoot?: string;          // Delegation tree root this agent belongs to
  parentId?: string;          // Direct parent (delegation)
  registeredAt: number;
  deregisteredAt?: number;
  result?: string;            // Set on completion (for dependency resolution)
}

export interface MeshDependency {
  id: string;
  waitingTaskId: string;
  dependsOnTaskId: string;
  reason: string;
  resolved: boolean;
  declaredAt: number;
  resolvedAt?: number;
}

export interface MeshBroadcast {
  id: string;
  fromTaskId: string;
  agentType: string;
  summary: string;
  trustTier: TrustTier;       // Trust tier of the broadcasting agent
  timestamp: number;
}

export type MeshEventType =
  | 'agent-registered'
  | 'agent-deregistered'
  | 'agent-updated'
  | 'dependency-declared'
  | 'dependency-resolved'
  | 'broadcast'
  | 'deadlock-detected';

export interface MeshEvent {
  type: MeshEventType;
  taskId: string;
  timestamp: number;
  data?: Record<string, unknown>;
}

export interface MeshSnapshot {
  agents: MeshAgent[];
  dependencies: MeshDependency[];
  broadcasts: MeshBroadcast[];
  activeTrees: string[];
  activeTeams: string[];
  deadlocks: string[][];
  timestamp: number;
}

export interface MeshConfig {
  maxBroadcasts: number;         // Max stored broadcasts (default 100)
  maxDependencies: number;       // Max tracked dependencies (default 200)
  broadcastRetentionMs: number;  // How long to keep broadcasts (default 10 min)
  dependencyRetentionMs: number; // How long to keep resolved deps (default 5 min)
}

const DEFAULT_CONFIG: MeshConfig = {
  maxBroadcasts: 100,
  maxDependencies: 200,
  broadcastRetentionMs: 10 * 60 * 1000,
  dependencyRetentionMs: 5 * 60 * 1000,
};

/* ── Awareness Mesh ─────────────────────────────────────────────────── */

class AwarenessMesh {
  private agents: Map<string, MeshAgent> = new Map();
  private dependencies: MeshDependency[] = [];
  private broadcasts: MeshBroadcast[] = [];
  private updateCallbacks: Array<(event: MeshEvent) => void> = [];
  private config: MeshConfig = { ...DEFAULT_CONFIG };
  private idCounter = 0;

  /* ── Configuration ────────────────────────────────────────────────── */

  configure(config: Partial<MeshConfig>): void {
    this.config = { ...this.config, ...config };
  }

  /* ── Agent Registration ───────────────────────────────────────────── */

  /**
   * Register an agent in the mesh. Called when an agent starts running.
   */
  registerAgent(
    taskId: string,
    agentType: string,
    description: string,
    opts?: {
      role?: AgentRole;
      trustTier?: TrustTier;
      teamId?: string;
      treeRoot?: string;
      parentId?: string;
    }
  ): void {
    if (this.agents.has(taskId)) return; // Idempotent

    const agent: MeshAgent = {
      taskId,
      agentType,
      description,
      phase: 'starting',
      progress: 0,
      role: opts?.role || 'solo',
      trustTier: opts?.trustTier || 'local',
      teamId: opts?.teamId,
      treeRoot: opts?.treeRoot,
      parentId: opts?.parentId,
      registeredAt: Date.now(),
    };

    this.agents.set(taskId, agent);
    this.emitEvent({ type: 'agent-registered', taskId, timestamp: Date.now() });
  }

  /**
   * Deregister an agent from the mesh. Called when an agent completes/fails.
   */
  deregisterAgent(taskId: string, result?: string): void {
    const agent = this.agents.get(taskId);
    if (!agent) return;

    agent.deregisteredAt = Date.now();
    if (result) agent.result = result;

    // Auto-resolve dependencies that depend on this agent
    this.resolveDependenciesFor(taskId);

    this.emitEvent({ type: 'agent-deregistered', taskId, timestamp: Date.now() });

    // Clean up after a delay (keep briefly for result retrieval)
    setTimeout(() => {
      this.agents.delete(taskId);
    }, 30_000);
  }

  /**
   * Update an agent's state in the mesh (phase, progress, etc.)
   */
  updateAgent(
    taskId: string,
    updates: Partial<Pick<MeshAgent, 'phase' | 'progress' | 'trustTier' | 'teamId' | 'treeRoot'>>
  ): void {
    const agent = this.agents.get(taskId);
    if (!agent) return;

    Object.assign(agent, updates);
    this.emitEvent({
      type: 'agent-updated',
      taskId,
      timestamp: Date.now(),
      data: updates as Record<string, unknown>,
    });
  }

  /**
   * Get a specific mesh agent's info.
   */
  getAgent(taskId: string): MeshAgent | null {
    return this.agents.get(taskId) || null;
  }

  /**
   * Get all active (non-deregistered) agents in the mesh.
   */
  getActiveAgents(): MeshAgent[] {
    return [...this.agents.values()].filter((a) => !a.deregisteredAt);
  }

  /* ── Dependencies ─────────────────────────────────────────────────── */

  /**
   * Declare that one agent depends on another's output.
   * Returns the dependency ID.
   */
  declareDependency(waitingTaskId: string, dependsOnTaskId: string, reason: string): string {
    // Check if this dependency already exists
    const existing = this.dependencies.find(
      (d) => d.waitingTaskId === waitingTaskId && d.dependsOnTaskId === dependsOnTaskId && !d.resolved
    );
    if (existing) return existing.id;

    const id = `dep-${String(++this.idCounter).padStart(4, '0')}`;
    const dep: MeshDependency = {
      id,
      waitingTaskId,
      dependsOnTaskId,
      reason,
      resolved: false,
      declaredAt: Date.now(),
    };

    this.dependencies.push(dep);
    this.pruneResolved();

    this.emitEvent({
      type: 'dependency-declared',
      taskId: waitingTaskId,
      timestamp: Date.now(),
      data: { dependsOn: dependsOnTaskId, reason },
    });

    // Check for deadlocks
    const deadlocks = this.detectDeadlocks();
    if (deadlocks.length > 0) {
      this.emitEvent({
        type: 'deadlock-detected',
        taskId: waitingTaskId,
        timestamp: Date.now(),
        data: { cycles: deadlocks },
      });

      try {
        contextStream.push({
          type: 'system',
          source: 'awareness-mesh',
          summary: `Deadlock detected: ${deadlocks.map((c) => c.join(' → ')).join('; ')}`,
          data: { deadlocks },
        });
      } catch {
        /* context stream may not be ready */
      }
    }

    return id;
  }

  /**
   * Resolve all dependencies that were waiting on a specific agent.
   */
  private resolveDependenciesFor(dependsOnTaskId: string): void {
    for (const dep of this.dependencies) {
      if (dep.dependsOnTaskId === dependsOnTaskId && !dep.resolved) {
        dep.resolved = true;
        dep.resolvedAt = Date.now();

        this.emitEvent({
          type: 'dependency-resolved',
          taskId: dep.waitingTaskId,
          timestamp: Date.now(),
          data: { dependsOn: dependsOnTaskId },
        });
      }
    }
  }

  /**
   * Get unresolved dependencies for a specific agent.
   */
  getUnresolvedDependencies(taskId: string): MeshDependency[] {
    return this.dependencies.filter((d) => d.waitingTaskId === taskId && !d.resolved);
  }

  /**
   * Get all agents that are waiting on a specific agent's output.
   */
  getDependents(taskId: string): MeshDependency[] {
    return this.dependencies.filter((d) => d.dependsOnTaskId === taskId && !d.resolved);
  }

  /**
   * Detect circular dependencies (deadlocks) using DFS cycle detection.
   * Returns arrays of task IDs forming cycles.
   */
  detectDeadlocks(): string[][] {
    const graph = new Map<string, string[]>();

    // Build adjacency list from unresolved dependencies
    for (const dep of this.dependencies) {
      if (dep.resolved) continue;
      const edges = graph.get(dep.waitingTaskId) || [];
      edges.push(dep.dependsOnTaskId);
      graph.set(dep.waitingTaskId, edges);
    }

    const cycles: string[][] = [];
    const visited = new Set<string>();
    const inStack = new Set<string>();
    const path: string[] = [];

    const dfs = (node: string): void => {
      if (inStack.has(node)) {
        // Found a cycle — extract it
        const cycleStart = path.indexOf(node);
        if (cycleStart >= 0) {
          cycles.push([...path.slice(cycleStart), node]);
        }
        return;
      }
      if (visited.has(node)) return;

      visited.add(node);
      inStack.add(node);
      path.push(node);

      for (const neighbor of graph.get(node) || []) {
        dfs(neighbor);
      }

      path.pop();
      inStack.delete(node);
    };

    for (const node of graph.keys()) {
      if (!visited.has(node)) {
        dfs(node);
      }
    }

    return cycles;
  }

  /* ── Broadcasting ─────────────────────────────────────────────────── */

  /**
   * Broadcast a result or status update to the entire mesh.
   * Respects trust tiers — broadcasts carry the sender's trust tier.
   */
  broadcast(fromTaskId: string, summary: string): void {
    const agent = this.agents.get(fromTaskId);
    if (!agent) return;

    const bc: MeshBroadcast = {
      id: `bc-${String(++this.idCounter).padStart(4, '0')}`,
      fromTaskId,
      agentType: agent.agentType,
      summary: summary.slice(0, 500), // Cap broadcast length
      trustTier: agent.trustTier,
      timestamp: Date.now(),
    };

    this.broadcasts.push(bc);
    this.pruneBroadcasts();

    this.emitEvent({
      type: 'broadcast',
      taskId: fromTaskId,
      timestamp: Date.now(),
      data: { summary: bc.summary },
    });
  }

  /**
   * Get broadcasts visible to a specific agent (respects trust tiers).
   * An agent can only see broadcasts from agents at same or lower trust tier
   * (lower numeric value = higher privilege).
   */
  getBroadcasts(forTaskId?: string, limit = 20): MeshBroadcast[] {
    let visible = this.broadcasts;

    if (forTaskId) {
      const agent = this.agents.get(forTaskId);
      if (agent) {
        const myTierOrder = TRUST_TIER_ORDER[agent.trustTier];
        // Can see broadcasts from same or more restrictive tier
        visible = visible.filter((bc) => TRUST_TIER_ORDER[bc.trustTier] <= myTierOrder);
      }
    }

    return visible.slice(-limit);
  }

  /* ── Awareness Context Generation ─────────────────────────────────── */

  /**
   * Generate a rich awareness context string for a specific agent.
   * Combines: active peers, team context, delegation siblings, dependencies, broadcasts.
   */
  getAwarenessContext(taskId: string): string {
    const agent = this.agents.get(taskId);
    if (!agent) return 'Not registered in awareness mesh.';

    const parts: string[] = [];
    const active = this.getActiveAgents().filter((a) => a.taskId !== taskId);

    if (active.length === 0) {
      return 'No other agents are currently active.';
    }

    // ── Active peers summary ───────────────────────────────────────
    const peerSummary = active
      .map((a) => {
        const phase = a.phase || 'working';
        const progress = a.progress > 0 ? ` (${a.progress}%)` : '';
        const team = a.teamId ? ` [Team:${a.teamId.slice(0, 6)}]` : '';
        const tree = a.treeRoot === agent.treeRoot && agent.treeRoot ? ' [same-tree]' : '';
        return `• ${a.agentType} — ${phase}${progress}${team}${tree}: ${a.description.slice(0, 60)}`;
      })
      .slice(0, 8); // Cap at 8 peer entries

    parts.push(`ACTIVE AGENTS (${active.length}):\n${peerSummary.join('\n')}`);

    // ── Delegation siblings (same parent) ──────────────────────────
    if (agent.parentId) {
      const siblings = active.filter(
        (a) => a.parentId === agent.parentId && a.taskId !== taskId
      );
      if (siblings.length > 0) {
        parts.push(
          `SIBLINGS (same parent):\n${siblings
            .map((s) => `• ${s.agentType}: ${s.description.slice(0, 60)}`)
            .join('\n')}`
        );
      }
    }

    // ── Unresolved dependencies ────────────────────────────────────
    const deps = this.getUnresolvedDependencies(taskId);
    if (deps.length > 0) {
      parts.push(
        `WAITING FOR:\n${deps
          .map((d) => {
            const depAgent = this.agents.get(d.dependsOnTaskId);
            const name = depAgent?.agentType || d.dependsOnTaskId.slice(0, 8);
            return `• ${name}: ${d.reason}`;
          })
          .join('\n')}`
      );
    }

    // ── Agents waiting on me ──────────────────────────────────────
    const dependents = this.getDependents(taskId);
    if (dependents.length > 0) {
      parts.push(
        `DEPENDING ON ME:\n${dependents
          .map((d) => {
            const waitAgent = this.agents.get(d.waitingTaskId);
            const name = waitAgent?.agentType || d.waitingTaskId.slice(0, 8);
            return `• ${name}: ${d.reason}`;
          })
          .join('\n')}`
      );
    }

    // ── Recent broadcasts (trust-filtered) ────────────────────────
    const recentBroadcasts = this.getBroadcasts(taskId, 5);
    if (recentBroadcasts.length > 0) {
      parts.push(
        `RECENT BROADCASTS:\n${recentBroadcasts
          .map((bc) => `• [${bc.agentType}] ${bc.summary.slice(0, 80)}`)
          .join('\n')}`
      );
    }

    return parts.join('\n\n');
  }

  /* ── Mesh Snapshot ────────────────────────────────────────────────── */

  /**
   * Get a full snapshot of the mesh state (for UI/debugging).
   */
  getSnapshot(): MeshSnapshot {
    const active = this.getActiveAgents();
    const activeTrees = new Set<string>();
    const activeTeams = new Set<string>();

    for (const agent of active) {
      if (agent.treeRoot) activeTrees.add(agent.treeRoot);
      if (agent.teamId) activeTeams.add(agent.teamId);
    }

    return {
      agents: active,
      dependencies: this.dependencies.filter((d) => !d.resolved),
      broadcasts: this.broadcasts.slice(-20),
      activeTrees: [...activeTrees],
      activeTeams: [...activeTeams],
      deadlocks: this.detectDeadlocks(),
      timestamp: Date.now(),
    };
  }

  /**
   * Get mesh statistics.
   */
  getStats(): {
    activeAgents: number;
    totalRegistered: number;
    unresolvedDeps: number;
    broadcasts: number;
    deadlocks: number;
  } {
    return {
      activeAgents: this.getActiveAgents().length,
      totalRegistered: this.agents.size,
      unresolvedDeps: this.dependencies.filter((d) => !d.resolved).length,
      broadcasts: this.broadcasts.length,
      deadlocks: this.detectDeadlocks().length,
    };
  }

  /* ── Event System ─────────────────────────────────────────────────── */

  /**
   * Subscribe to mesh events. Returns unsubscribe function.
   */
  onUpdate(callback: (event: MeshEvent) => void): () => void {
    this.updateCallbacks.push(callback);
    return () => {
      const idx = this.updateCallbacks.indexOf(callback);
      if (idx >= 0) this.updateCallbacks.splice(idx, 1);
    };
  }

  private emitEvent(event: MeshEvent): void {
    for (const cb of this.updateCallbacks) {
      try {
        cb(event);
      } catch {
        /* swallow callback errors */
      }
    }
  }

  /* ── Maintenance ──────────────────────────────────────────────────── */

  private pruneBroadcasts(): void {
    const now = Date.now();
    // Remove old broadcasts
    this.broadcasts = this.broadcasts.filter(
      (bc) => now - bc.timestamp < this.config.broadcastRetentionMs
    );
    // Cap at max
    if (this.broadcasts.length > this.config.maxBroadcasts) {
      this.broadcasts = this.broadcasts.slice(-this.config.maxBroadcasts);
    }
  }

  private pruneResolved(): void {
    const now = Date.now();
    // Remove old resolved dependencies
    this.dependencies = this.dependencies.filter(
      (d) => !d.resolved || (d.resolvedAt && now - d.resolvedAt < this.config.dependencyRetentionMs)
    );
    // Cap at max
    if (this.dependencies.length > this.config.maxDependencies) {
      this.dependencies = this.dependencies.slice(-this.config.maxDependencies);
    }
  }

  /**
   * Full cleanup — removes all state. Used for testing.
   */
  cleanup(): void {
    this.agents.clear();
    this.dependencies = [];
    this.broadcasts = [];
    this.updateCallbacks = [];
    this.idCounter = 0;
  }
}

/* ── Singleton Export ───────────────────────────────────────────────── */

export const awarenessMesh = new AwarenessMesh();
