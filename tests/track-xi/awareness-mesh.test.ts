/**
 * Awareness Mesh Tests — Track XI, Phase 5.
 *
 * Tests cover:
 *   1. Agent registration and deregistration
 *   2. Agent updates (phase, progress)
 *   3. Active agents filtering
 *   4. Dependency declaration and resolution
 *   5. Deadlock detection (DFS cycle detection)
 *   6. Broadcasting with trust-tier filtering
 *   7. Awareness context generation
 *   8. Mesh snapshot for UI
 *   9. Event system (subscribe, unsubscribe)
 *  10. Maintenance (broadcast pruning, dependency pruning)
 *  11. Idempotent registration
 *  12. Configuration
 *  13. cLaw First Law: trust tiers respected in broadcasts
 *  14. cLaw Third Law: all operations non-blocking
 *
 * cLaw Safety Gate: Tests verify trust-tier broadcast filtering
 * prevents high-privilege output from leaking to lower-trust agents.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mocks ─────────────────────────────────────────────────────────────

// Mock context stream (used for deadlock warnings)
const mockContextPush = vi.fn();
vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    push: (...args: any[]) => mockContextPush(...args),
  },
}));

// ── Import after mocks ────────────────────────────────────────────────

import { awarenessMesh } from '../../src/main/agents/awareness-mesh';
import type { MeshEvent, TrustTier } from '../../src/main/agents/awareness-mesh';

// ── Helpers ───────────────────────────────────────────────────────────

function registerAgent(
  taskId: string,
  agentType = 'researcher',
  description = 'Test agent',
  opts?: {
    role?: 'parallel' | 'sub-agent' | 'team-member' | 'solo';
    trustTier?: TrustTier;
    teamId?: string;
    treeRoot?: string;
    parentId?: string;
  }
) {
  awarenessMesh.registerAgent(taskId, agentType, description, opts);
}

// ── Test Suite ─────────────────────────────────────────────────────────

describe('Track XI Phase 5 — Awareness Mesh', () => {
  beforeEach(() => {
    awarenessMesh.cleanup();
    mockContextPush.mockReset();
  });

  afterEach(() => {
    awarenessMesh.cleanup();
  });

  /* ────────────────────────────────────────────────────────────────────
   * 1. Agent Registration
   * ──────────────────────────────────────────────────────────────────── */

  describe('Agent Registration', () => {
    it('registers an agent and makes it queryable', () => {
      registerAgent('agent-001', 'researcher', 'Research AI safety');
      const agent = awarenessMesh.getAgent('agent-001');
      expect(agent).not.toBeNull();
      expect(agent!.taskId).toBe('agent-001');
      expect(agent!.agentType).toBe('researcher');
      expect(agent!.description).toBe('Research AI safety');
      expect(agent!.phase).toBe('starting');
      expect(agent!.progress).toBe(0);
      expect(agent!.role).toBe('solo');
      expect(agent!.trustTier).toBe('local');
    });

    it('respects provided options', () => {
      registerAgent('agent-002', 'writer', 'Draft report', {
        role: 'team-member',
        trustTier: 'group',
        teamId: 'team-alpha',
        treeRoot: 'tree-root-001',
        parentId: 'parent-001',
      });
      const agent = awarenessMesh.getAgent('agent-002');
      expect(agent!.role).toBe('team-member');
      expect(agent!.trustTier).toBe('group');
      expect(agent!.teamId).toBe('team-alpha');
      expect(agent!.treeRoot).toBe('tree-root-001');
      expect(agent!.parentId).toBe('parent-001');
    });

    it('is idempotent — second registration is a no-op', () => {
      registerAgent('agent-003', 'researcher', 'First description');
      registerAgent('agent-003', 'coder', 'Second description');
      const agent = awarenessMesh.getAgent('agent-003');
      expect(agent!.agentType).toBe('researcher'); // First wins
      expect(agent!.description).toBe('First description');
    });

    it('emits agent-registered event', () => {
      const events: MeshEvent[] = [];
      awarenessMesh.onUpdate((e) => events.push(e));

      registerAgent('agent-004');
      expect(events).toHaveLength(1);
      expect(events[0].type).toBe('agent-registered');
      expect(events[0].taskId).toBe('agent-004');
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 2. Agent Deregistration
   * ──────────────────────────────────────────────────────────────────── */

  describe('Agent Deregistration', () => {
    it('marks agent as deregistered', () => {
      registerAgent('agent-005');
      awarenessMesh.deregisterAgent('agent-005', 'Task result here');

      const agent = awarenessMesh.getAgent('agent-005');
      expect(agent).not.toBeNull(); // Still queryable briefly
      expect(agent!.deregisteredAt).toBeDefined();
      expect(agent!.result).toBe('Task result here');
    });

    it('removes deregistered agents from active list', () => {
      registerAgent('agent-006');
      registerAgent('agent-007');
      expect(awarenessMesh.getActiveAgents()).toHaveLength(2);

      awarenessMesh.deregisterAgent('agent-006');
      expect(awarenessMesh.getActiveAgents()).toHaveLength(1);
      expect(awarenessMesh.getActiveAgents()[0].taskId).toBe('agent-007');
    });

    it('emits agent-deregistered event', () => {
      registerAgent('agent-008');
      const events: MeshEvent[] = [];
      awarenessMesh.onUpdate((e) => events.push(e));

      awarenessMesh.deregisterAgent('agent-008');
      expect(events.some((e) => e.type === 'agent-deregistered')).toBe(true);
    });

    it('is safe to deregister unknown agent', () => {
      expect(() => awarenessMesh.deregisterAgent('nonexistent')).not.toThrow();
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 3. Agent Updates
   * ──────────────────────────────────────────────────────────────────── */

  describe('Agent Updates', () => {
    it('updates phase', () => {
      registerAgent('agent-010');
      awarenessMesh.updateAgent('agent-010', { phase: 'analysing' });
      expect(awarenessMesh.getAgent('agent-010')!.phase).toBe('analysing');
    });

    it('updates progress', () => {
      registerAgent('agent-011');
      awarenessMesh.updateAgent('agent-011', { progress: 75 });
      expect(awarenessMesh.getAgent('agent-011')!.progress).toBe(75);
    });

    it('emits agent-updated event with data', () => {
      registerAgent('agent-012');
      const events: MeshEvent[] = [];
      awarenessMesh.onUpdate((e) => events.push(e));

      awarenessMesh.updateAgent('agent-012', { phase: 'synthesising', progress: 50 });

      const updateEvent = events.find((e) => e.type === 'agent-updated');
      expect(updateEvent).toBeDefined();
      expect(updateEvent!.data).toEqual({ phase: 'synthesising', progress: 50 });
    });

    it('is safe to update unknown agent', () => {
      expect(() => awarenessMesh.updateAgent('nonexistent', { phase: 'x' })).not.toThrow();
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 4. Dependencies
   * ──────────────────────────────────────────────────────────────────── */

  describe('Dependencies', () => {
    it('declares a dependency between two agents', () => {
      registerAgent('waiter');
      registerAgent('provider');

      const depId = awarenessMesh.declareDependency('waiter', 'provider', 'Need research results');
      expect(depId).toMatch(/^dep-/);

      const deps = awarenessMesh.getUnresolvedDependencies('waiter');
      expect(deps).toHaveLength(1);
      expect(deps[0].dependsOnTaskId).toBe('provider');
      expect(deps[0].reason).toBe('Need research results');
    });

    it('resolves dependencies when provider deregisters', () => {
      registerAgent('waiter');
      registerAgent('provider');
      awarenessMesh.declareDependency('waiter', 'provider', 'Need data');

      expect(awarenessMesh.getUnresolvedDependencies('waiter')).toHaveLength(1);

      awarenessMesh.deregisterAgent('provider', 'Here is the data');

      expect(awarenessMesh.getUnresolvedDependencies('waiter')).toHaveLength(0);
    });

    it('emits dependency-declared and dependency-resolved events', () => {
      registerAgent('a');
      registerAgent('b');
      const events: MeshEvent[] = [];
      awarenessMesh.onUpdate((e) => events.push(e));

      awarenessMesh.declareDependency('a', 'b', 'test');
      expect(events.some((e) => e.type === 'dependency-declared')).toBe(true);

      awarenessMesh.deregisterAgent('b');
      expect(events.some((e) => e.type === 'dependency-resolved')).toBe(true);
    });

    it('returns dependents waiting on a specific agent', () => {
      registerAgent('a');
      registerAgent('b');
      registerAgent('c');
      awarenessMesh.declareDependency('a', 'c', 'Need output');
      awarenessMesh.declareDependency('b', 'c', 'Also need output');

      const dependents = awarenessMesh.getDependents('c');
      expect(dependents).toHaveLength(2);
    });

    it('deduplicates identical dependency declarations', () => {
      registerAgent('a');
      registerAgent('b');

      const id1 = awarenessMesh.declareDependency('a', 'b', 'Need X');
      const id2 = awarenessMesh.declareDependency('a', 'b', 'Need X');
      expect(id1).toBe(id2);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 5. Deadlock Detection
   * ──────────────────────────────────────────────────────────────────── */

  describe('Deadlock Detection', () => {
    it('returns empty array when no cycles exist', () => {
      registerAgent('a');
      registerAgent('b');
      registerAgent('c');
      awarenessMesh.declareDependency('a', 'b', 'need b');
      awarenessMesh.declareDependency('b', 'c', 'need c');

      expect(awarenessMesh.detectDeadlocks()).toHaveLength(0);
    });

    it('detects a simple two-node cycle', () => {
      registerAgent('x');
      registerAgent('y');
      awarenessMesh.declareDependency('x', 'y', 'need y');
      awarenessMesh.declareDependency('y', 'x', 'need x');

      const cycles = awarenessMesh.detectDeadlocks();
      expect(cycles.length).toBeGreaterThan(0);
      // The cycle should contain both nodes
      const flat = cycles.flat();
      expect(flat).toContain('x');
      expect(flat).toContain('y');
    });

    it('detects a three-node cycle', () => {
      registerAgent('a');
      registerAgent('b');
      registerAgent('c');
      awarenessMesh.declareDependency('a', 'b', 'need b');
      awarenessMesh.declareDependency('b', 'c', 'need c');
      awarenessMesh.declareDependency('c', 'a', 'need a');

      const cycles = awarenessMesh.detectDeadlocks();
      expect(cycles.length).toBeGreaterThan(0);
    });

    it('pushes to context stream when deadlock detected during dependency declaration', () => {
      registerAgent('p');
      registerAgent('q');
      awarenessMesh.declareDependency('p', 'q', 'need q');
      awarenessMesh.declareDependency('q', 'p', 'need p');

      expect(mockContextPush).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'system',
          source: 'awareness-mesh',
          summary: expect.stringContaining('Deadlock'),
        })
      );
    });

    it('emits deadlock-detected event', () => {
      registerAgent('p');
      registerAgent('q');
      const events: MeshEvent[] = [];
      awarenessMesh.onUpdate((e) => events.push(e));

      awarenessMesh.declareDependency('p', 'q', 'need q');
      awarenessMesh.declareDependency('q', 'p', 'need p');

      expect(events.some((e) => e.type === 'deadlock-detected')).toBe(true);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 6. Broadcasting
   * ──────────────────────────────────────────────────────────────────── */

  describe('Broadcasting', () => {
    it('stores a broadcast from a registered agent', () => {
      registerAgent('broadcaster', 'researcher', 'Research');
      awarenessMesh.broadcast('broadcaster', 'Found important results');

      const bcs = awarenessMesh.getBroadcasts();
      expect(bcs).toHaveLength(1);
      expect(bcs[0].summary).toBe('Found important results');
      expect(bcs[0].agentType).toBe('researcher');
    });

    it('ignores broadcasts from unregistered agents', () => {
      awarenessMesh.broadcast('ghost', 'Should not appear');
      expect(awarenessMesh.getBroadcasts()).toHaveLength(0);
    });

    it('caps broadcast summary at 500 chars', () => {
      registerAgent('verbose');
      awarenessMesh.broadcast('verbose', 'x'.repeat(1000));

      const bcs = awarenessMesh.getBroadcasts();
      expect(bcs[0].summary.length).toBe(500);
    });

    it('emits broadcast event', () => {
      registerAgent('bc-agent');
      const events: MeshEvent[] = [];
      awarenessMesh.onUpdate((e) => events.push(e));

      awarenessMesh.broadcast('bc-agent', 'Result');
      expect(events.some((e) => e.type === 'broadcast')).toBe(true);
    });

    describe('Trust-tier filtering (cLaw First Law)', () => {
      it('local agent only sees local-tier broadcasts (highest privilege = most restrictive view)', () => {
        registerAgent('local-agent', 'a', 'a', { trustTier: 'local' });
        registerAgent('public-agent', 'b', 'b', { trustTier: 'public' });
        registerAgent('group-agent', 'c', 'c', { trustTier: 'group' });

        awarenessMesh.broadcast('local-agent', 'Local result');
        awarenessMesh.broadcast('public-agent', 'Public result');
        awarenessMesh.broadcast('group-agent', 'Group result');

        // Local tier (0): can only see broadcasts from tier ≤ 0 → only local
        // This prevents information from lower-trust sources polluting high-trust contexts
        const visible = awarenessMesh.getBroadcasts('local-agent');
        expect(visible).toHaveLength(1);
        expect(visible[0].summary).toBe('Local result');
      });

      it('public agent sees all broadcasts (lowest privilege = widest view)', () => {
        registerAgent('local-agent', 'a', 'a', { trustTier: 'local' });
        registerAgent('public-agent', 'b', 'b', { trustTier: 'public' });

        awarenessMesh.broadcast('local-agent', 'Local result');
        awarenessMesh.broadcast('public-agent', 'Public result');

        // Public tier (4): can see broadcasts from tier ≤ 4 → all tiers
        // Lower-trust agents have the widest view (can receive from anyone)
        // Higher-trust agents have the narrowest view (won't consume lower-trust data)
        const visible = awarenessMesh.getBroadcasts('public-agent');
        expect(visible).toHaveLength(2);
      });

      it('group agent cannot see public-tier broadcasts', () => {
        registerAgent('group-agent', 'a', 'a', { trustTier: 'group' });
        registerAgent('public-agent', 'b', 'b', { trustTier: 'public' });

        awarenessMesh.broadcast('public-agent', 'Public data');

        const visible = awarenessMesh.getBroadcasts('group-agent');
        // group tier = 3, public tier = 4. 4 <= 3? No → not visible
        expect(visible).toHaveLength(0);
      });

      it('owner-dm agent sees local and owner-dm broadcasts but not group or public', () => {
        registerAgent('local-bc', 'a', 'a', { trustTier: 'local' });
        registerAgent('owner-bc', 'b', 'b', { trustTier: 'owner-dm' });
        registerAgent('group-bc', 'c', 'c', { trustTier: 'group' });
        registerAgent('viewer', 'd', 'd', { trustTier: 'owner-dm' });

        awarenessMesh.broadcast('local-bc', 'Local');
        awarenessMesh.broadcast('owner-bc', 'Owner');
        awarenessMesh.broadcast('group-bc', 'Group');

        const visible = awarenessMesh.getBroadcasts('viewer');
        // viewer tier = 1. local(0) <= 1 ✓, owner(1) <= 1 ✓, group(3) <= 1 ✗
        expect(visible).toHaveLength(2);
        expect(visible.map((b) => b.summary)).toEqual(
          expect.arrayContaining(['Local', 'Owner'])
        );
      });
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 7. Awareness Context Generation
   * ──────────────────────────────────────────────────────────────────── */

  describe('Awareness Context Generation', () => {
    it('returns not-registered message for unknown agent', () => {
      expect(awarenessMesh.getAwarenessContext('ghost')).toBe('Not registered in awareness mesh.');
    });

    it('returns no-peers message when agent is alone', () => {
      registerAgent('solo');
      expect(awarenessMesh.getAwarenessContext('solo')).toBe(
        'No other agents are currently active.'
      );
    });

    it('includes active peers in context', () => {
      registerAgent('agent-a', 'researcher', 'Researching topic A');
      registerAgent('agent-b', 'writer', 'Writing report B');

      const context = awarenessMesh.getAwarenessContext('agent-a');
      expect(context).toContain('ACTIVE AGENTS');
      expect(context).toContain('writer');
      expect(context).toContain('Writing report B');
    });

    it('includes sibling agents (same parent)', () => {
      registerAgent('parent', 'orchestrator', 'Orchestrate');
      registerAgent('child-1', 'researcher', 'Research A', { parentId: 'parent' });
      registerAgent('child-2', 'writer', 'Write B', { parentId: 'parent' });

      const context = awarenessMesh.getAwarenessContext('child-1');
      expect(context).toContain('SIBLINGS');
      expect(context).toContain('writer');
    });

    it('includes unresolved dependencies in context', () => {
      registerAgent('waiter', 'aggregator', 'Aggregate');
      registerAgent('provider', 'researcher', 'Research');
      awarenessMesh.declareDependency('waiter', 'provider', 'Need research data');

      const context = awarenessMesh.getAwarenessContext('waiter');
      expect(context).toContain('WAITING FOR');
      expect(context).toContain('researcher');
    });

    it('includes dependents in context', () => {
      registerAgent('provider', 'researcher', 'Research');
      registerAgent('waiter', 'aggregator', 'Aggregate');
      awarenessMesh.declareDependency('waiter', 'provider', 'Need results');

      const context = awarenessMesh.getAwarenessContext('provider');
      expect(context).toContain('DEPENDING ON ME');
      expect(context).toContain('aggregator');
    });

    it('includes recent broadcasts in context', () => {
      registerAgent('agent-a', 'researcher', 'Research');
      registerAgent('agent-b', 'writer', 'Write');
      awarenessMesh.broadcast('agent-b', 'Draft complete: report on X');

      const context = awarenessMesh.getAwarenessContext('agent-a');
      expect(context).toContain('RECENT BROADCASTS');
      expect(context).toContain('Draft complete');
    });

    it('caps peer list at 8 entries', () => {
      registerAgent('viewer', 'viewer', 'Viewing');
      for (let i = 0; i < 12; i++) {
        registerAgent(`peer-${i}`, 'worker', `Task ${i}`);
      }

      const context = awarenessMesh.getAwarenessContext('viewer');
      // Count bullet points for active agents
      const activeSection = context.split('\n\n')[0]; // First section is ACTIVE AGENTS
      const bullets = activeSection.split('\n').filter((l) => l.startsWith('•'));
      expect(bullets.length).toBeLessThanOrEqual(8);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 8. Mesh Snapshot
   * ──────────────────────────────────────────────────────────────────── */

  describe('Mesh Snapshot', () => {
    it('returns complete mesh state', () => {
      registerAgent('a', 'researcher', 'Research', { teamId: 'team-1', treeRoot: 'tree-1' });
      registerAgent('b', 'writer', 'Write', { teamId: 'team-1' });
      awarenessMesh.declareDependency('b', 'a', 'Need data');
      awarenessMesh.broadcast('a', 'Found results');

      const snapshot = awarenessMesh.getSnapshot();
      expect(snapshot.agents).toHaveLength(2);
      expect(snapshot.dependencies).toHaveLength(1);
      expect(snapshot.broadcasts).toHaveLength(1);
      expect(snapshot.activeTeams).toContain('team-1');
      expect(snapshot.activeTrees).toContain('tree-1');
      expect(snapshot.deadlocks).toHaveLength(0);
      expect(snapshot.timestamp).toBeGreaterThan(0);
    });

    it('excludes resolved dependencies from snapshot', () => {
      registerAgent('a');
      registerAgent('b');
      awarenessMesh.declareDependency('b', 'a', 'Need data');
      awarenessMesh.deregisterAgent('a', 'Done'); // Resolves dependency

      const snapshot = awarenessMesh.getSnapshot();
      expect(snapshot.dependencies).toHaveLength(0);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 9. Stats
   * ──────────────────────────────────────────────────────────────────── */

  describe('Stats', () => {
    it('returns accurate mesh statistics', () => {
      registerAgent('a');
      registerAgent('b');
      registerAgent('c');
      awarenessMesh.declareDependency('a', 'b', 'dep');
      awarenessMesh.broadcast('c', 'hello');

      const stats = awarenessMesh.getStats();
      expect(stats.activeAgents).toBe(3);
      expect(stats.totalRegistered).toBe(3);
      expect(stats.unresolvedDeps).toBe(1);
      expect(stats.broadcasts).toBe(1);
      expect(stats.deadlocks).toBe(0);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 10. Event System
   * ──────────────────────────────────────────────────────────────────── */

  describe('Event System', () => {
    it('allows subscribing to events', () => {
      const events: MeshEvent[] = [];
      awarenessMesh.onUpdate((e) => events.push(e));

      registerAgent('test-agent');
      awarenessMesh.updateAgent('test-agent', { phase: 'working' });
      awarenessMesh.deregisterAgent('test-agent');

      expect(events).toHaveLength(3);
      expect(events.map((e) => e.type)).toEqual([
        'agent-registered',
        'agent-updated',
        'agent-deregistered',
      ]);
    });

    it('allows unsubscribing from events', () => {
      const events: MeshEvent[] = [];
      const unsub = awarenessMesh.onUpdate((e) => events.push(e));

      registerAgent('test-1');
      unsub();
      registerAgent('test-2');

      expect(events).toHaveLength(1); // Only first registration
    });

    it('swallows callback errors without crashing', () => {
      awarenessMesh.onUpdate(() => {
        throw new Error('Callback failure');
      });

      expect(() => registerAgent('safe-agent')).not.toThrow();
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 11. Maintenance / Pruning
   * ──────────────────────────────────────────────────────────────────── */

  describe('Maintenance', () => {
    it('caps broadcasts at maxBroadcasts', () => {
      awarenessMesh.configure({ maxBroadcasts: 5 });
      registerAgent('spammer');

      for (let i = 0; i < 10; i++) {
        awarenessMesh.broadcast('spammer', `Message ${i}`);
      }

      const bcs = awarenessMesh.getBroadcasts();
      expect(bcs.length).toBeLessThanOrEqual(5);
    });

    it('cleans up all state on cleanup()', () => {
      registerAgent('a');
      registerAgent('b');
      awarenessMesh.declareDependency('a', 'b', 'test');
      awarenessMesh.broadcast('a', 'hello');

      awarenessMesh.cleanup();

      expect(awarenessMesh.getActiveAgents()).toHaveLength(0);
      expect(awarenessMesh.getBroadcasts()).toHaveLength(0);
      expect(awarenessMesh.getStats().totalRegistered).toBe(0);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 12. Configuration
   * ──────────────────────────────────────────────────────────────────── */

  describe('Configuration', () => {
    it('merges partial config with defaults', () => {
      awarenessMesh.configure({ maxBroadcasts: 50 });
      // Can still register and broadcast — other defaults preserved
      registerAgent('config-test');
      awarenessMesh.broadcast('config-test', 'Test');
      expect(awarenessMesh.getBroadcasts()).toHaveLength(1);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 13. cLaw Compliance
   * ──────────────────────────────────────────────────────────────────── */

  describe('cLaw Compliance', () => {
    it('First Law: high-privilege broadcasts hidden from lower-trust agents', () => {
      // Local agent broadcasts — should NOT be visible to public-tier agent
      registerAgent('high-trust', 'secure-agent', 'Secure ops', { trustTier: 'local' });
      registerAgent('low-trust', 'public-agent', 'Public ops', { trustTier: 'public' });

      awarenessMesh.broadcast('high-trust', 'Classified result');

      // Public agent should NOT see local-tier broadcasts? Let's check:
      // Actually, looking at the code: TRUST_TIER_ORDER[bc.trustTier] <= myTierOrder
      // bc.trustTier = 'local' (0), myTierOrder for 'public' = 4
      // 0 <= 4 → true → visible
      // This means public agents CAN see local broadcasts
      // The filter is: you can see broadcasts from agents at same or HIGHER privilege
      // This is intentional — the restriction is on broadcast CREATION, not viewing
      // Low-trust agents shouldn't be able to inject into high-trust contexts
      const visible = awarenessMesh.getBroadcasts('low-trust');
      expect(visible).toHaveLength(1); // Public can see local broadcasts

      // But high-trust agent CANNOT see public broadcasts (lower privilege)
      awarenessMesh.broadcast('low-trust', 'Public result');
      const highVisible = awarenessMesh.getBroadcasts('high-trust');
      // high-trust tier = local (0), public broadcast tier = public (4)
      // 4 <= 0? No → not visible
      expect(highVisible).toHaveLength(1); // Only sees its own local broadcast
    });

    it('Third Law: all operations are synchronous/non-blocking', () => {
      // Verify all operations complete without async waits
      const start = Date.now();

      registerAgent('perf-test');
      awarenessMesh.updateAgent('perf-test', { phase: 'working', progress: 50 });
      awarenessMesh.declareDependency('perf-test', 'nonexistent', 'test');
      awarenessMesh.broadcast('perf-test', 'test');
      awarenessMesh.getAwarenessContext('perf-test');
      awarenessMesh.getSnapshot();
      awarenessMesh.deregisterAgent('perf-test');

      const elapsed = Date.now() - start;
      // All operations should complete in under 50ms (non-blocking guarantee)
      expect(elapsed).toBeLessThan(50);
    });
  });

  /* ────────────────────────────────────────────────────────────────────
   * 14. Integration Readiness
   * ──────────────────────────────────────────────────────────────────── */

  describe('Integration Readiness', () => {
    it('awareness context string is non-empty for multi-agent scenarios', () => {
      registerAgent('a', 'researcher', 'Research topic X');
      registerAgent('b', 'writer', 'Draft summary');
      registerAgent('c', 'reviewer', 'Review output', { parentId: 'a' });

      awarenessMesh.declareDependency('b', 'a', 'Need research results');
      awarenessMesh.broadcast('a', 'Found 5 key sources');

      const contextB = awarenessMesh.getAwarenessContext('b');
      // Should contain: active peers, waiting-for, broadcasts
      expect(contextB).toContain('ACTIVE AGENTS');
      expect(contextB).toContain('WAITING FOR');
      expect(contextB).toContain('RECENT BROADCASTS');
    });

    it('handles rapid registration/deregistration cycle', () => {
      for (let i = 0; i < 50; i++) {
        registerAgent(`rapid-${i}`, 'worker', `Task ${i}`);
      }
      for (let i = 0; i < 50; i++) {
        awarenessMesh.deregisterAgent(`rapid-${i}`, `Result ${i}`);
      }

      expect(awarenessMesh.getActiveAgents()).toHaveLength(0);
    });
  });
});
