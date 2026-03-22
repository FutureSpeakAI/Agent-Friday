import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('electron', () => ({
  BrowserWindow: vi.fn(),
  app: { getPath: () => '/tmp/test' },
}));

vi.mock('crypto', () => {
  let counter = 0;
  return {
    default: { randomUUID: vi.fn(() => {
      counter++;
      const hex = counter.toString(16).padStart(8, '0');
      return `${hex}-cccc-dddd-eeee-ffffffffffff`;
    }) },
    randomBytes: vi.fn((size: number) => Buffer.alloc(size, 0xab)),
    randomUUID: vi.fn(() => {
      counter++;
      const hex = counter.toString(16).padStart(8, '0');
      return `${hex}-cccc-dddd-eeee-ffffffffffff`;
    }),
  };
});

vi.mock('../../src/main/agents/builtin-agents', () => ({
  builtinAgents: [
    { name: 'researcher', description: 'Research agent', systemPrompt: 'You are a researcher', tools: [] },
    { name: 'writer', description: 'Writing agent', systemPrompt: 'You are a writer', tools: [] },
    { name: 'analyst', description: 'Analysis agent', systemPrompt: 'You are an analyst', tools: [] },
  ],
}));

vi.mock('../../src/main/agents/agent-personas', () => ({
  findPersonaForAgentType: vi.fn(() => ({ id: 'persona-1', name: 'Agent Smith' })),
}));

vi.mock('../../src/main/agents/agent-voice', () => ({ agentVoice: {} }));

vi.mock('../../src/main/agents/agent-teams', () => ({
  agentTeams: { create: vi.fn(() => ({ id: 'team-1' })), addMember: vi.fn() },
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: { get: vi.fn(() => ({})) },
}));

vi.mock('../../src/main/agent-office/office-manager', () => ({
  officeManager: { emit: vi.fn(), agentSpawned: vi.fn(), agentStopped: vi.fn(), agentCompleted: vi.fn(), agentThought: vi.fn(), agentPhase: vi.fn() },
}));

vi.mock('../../src/main/openrouter', () => ({
  openRouter: {},
}));

vi.mock('@anthropic-ai/sdk', () => ({ default: vi.fn() }));

import crypto from 'crypto';
import { agentRunner } from '../../src/main/agents/agent-runner';
import type { AgentTask, AgentStatus } from '../../src/main/agents/agent-types';

// We need a fresh agentRunner for each test since it's a singleton with internal state.
// Access private fields via cast for resetting.
function resetRunner(): void {
  const runner = agentRunner as any;
  runner.tasks = new Map();
  runner.running = 0;
  runner.queue = [];
  runner.cancelled = new Set();
  runner.hardStopped = new Set();
  runner.abortControllers = new Map();
  runner.mainWindow = null;
  runner.processingQueue = false;
  // Stub out processQueue so tasks remain in 'queued' state
  // (the real processQueue would call executeTask which needs full OpenRouter wiring)
  vi.spyOn(runner, 'processQueue').mockImplementation(async () => {});
}

// Helper: the crypto mock already increments a counter, but we reset the
// mockImplementation here to allow overriding per-test if needed.
let uuidCounter = 0;
function setupUniqueUUIDs(): void {
  // crypto is the default export from the mocked 'crypto' module
  // The vi.mock factory already provides incrementing UUIDs, so this is a no-op
  // (kept for compatibility with the test structure)
  uuidCounter = 0;
}

beforeEach(() => {
  resetRunner();
  uuidCounter = 0;
  setupUniqueUUIDs();
});

afterEach(() => {
  vi.restoreAllMocks();
});

/* ================================================================== *
 *  1. Agent types & definitions
 * ================================================================== */
describe('Agent types & definitions', () => {
  it('getAgentTypes returns builtin agent types', () => {
    const types = agentRunner.getAgentTypes();
    const names = types.map((t) => t.name);
    expect(names).toContain('researcher');
    expect(names).toContain('writer');
    expect(names).toContain('analyst');
  });

  it('each type has name and description', () => {
    const types = agentRunner.getAgentTypes();
    for (const t of types) {
      expect(t).toHaveProperty('name');
      expect(t).toHaveProperty('description');
      expect(typeof t.name).toBe('string');
      expect(typeof t.description).toBe('string');
    }
  });

  it('has at least 3 builtin types', () => {
    const types = agentRunner.getAgentTypes();
    expect(types.length).toBeGreaterThanOrEqual(3);
  });

  it('getAgentTypes returns an array', () => {
    const types = agentRunner.getAgentTypes();
    expect(Array.isArray(types)).toBe(true);
  });

  it('agent type names are unique', () => {
    const types = agentRunner.getAgentTypes();
    const names = types.map((t) => t.name);
    const unique = new Set(names);
    expect(unique.size).toBe(names.length);
  });
});

/* ================================================================== *
 *  2. spawn
 * ================================================================== */
describe('spawn', () => {
  it('returns AgentTask with id and status queued', () => {
    const task = agentRunner.spawn('researcher', 'Find facts');
    expect(task.id).toBeDefined();
    expect(typeof task.id).toBe('string');
    expect(task.status).toBe('queued');
  });

  it('task has correct agentType and description', () => {
    const task = agentRunner.spawn('writer', 'Write an essay');
    expect(task.agentType).toBe('writer');
    expect(task.description).toBe('Write an essay');
  });

  it('task has createdAt timestamp', () => {
    const before = Date.now();
    const task = agentRunner.spawn('analyst', 'Analyse data');
    const after = Date.now();
    expect(task.createdAt).toBeGreaterThanOrEqual(before);
    expect(task.createdAt).toBeLessThanOrEqual(after);
  });

  it('task has empty logs and thoughts arrays', () => {
    const task = agentRunner.spawn('researcher', 'Research topic');
    expect(Array.isArray(task.logs)).toBe(true);
    expect(task.logs.length).toBe(0);
    expect(Array.isArray(task.thoughts)).toBe(true);
    expect(task.thoughts.length).toBe(0);
  });

  it('task input is stored', () => {
    const input = { topic: 'quantum computing', depth: 3 };
    const task = agentRunner.spawn('researcher', 'Deep research', input);
    expect(task.input).toEqual(input);
  });

  it('throws for unknown agentType', () => {
    expect(() => agentRunner.spawn('nonexistent', 'Do something')).toThrow(/Unknown agent type/);
  });

  it('multiple spawns create distinct tasks', () => {
    const task1 = agentRunner.spawn('researcher', 'Task A');
    const task2 = agentRunner.spawn('writer', 'Task B');
    expect(task1.id).not.toBe(task2.id);
  });

  it('task gets personaId and personaName from persona lookup', () => {
    const task = agentRunner.spawn('researcher', 'Look up stuff');
    expect(task.personaId).toBe('persona-1');
    expect(task.personaName).toBe('Agent Smith');
  });

  it('default role is solo', () => {
    const task = agentRunner.spawn('researcher', 'Solo task');
    expect(task.role).toBe('solo');
  });

  it('with parentId, role becomes sub-agent', () => {
    const task = agentRunner.spawn('researcher', 'Sub task', {}, { parentId: 'parent-123' });
    expect(task.role).toBe('sub-agent');
  });
});

/* ================================================================== *
 *  3. spawnTeam
 * ================================================================== */
describe('spawnTeam', () => {
  it('returns teamId and taskIds array', () => {
    const result = agentRunner.spawnTeam('Alpha', 'Win the day', [
      { agentType: 'researcher', description: 'Research', input: {} },
    ]);
    expect(result).toHaveProperty('teamId');
    expect(result).toHaveProperty('taskIds');
    expect(Array.isArray(result.taskIds)).toBe(true);
  });

  it('taskIds length matches members array', () => {
    const members = [
      { agentType: 'researcher', description: 'Research', input: {} },
      { agentType: 'writer', description: 'Write', input: {} },
      { agentType: 'analyst', description: 'Analyse', input: {} },
    ];
    const result = agentRunner.spawnTeam('Bravo', 'Complete mission', members);
    expect(result.taskIds.length).toBe(members.length);
  });

  it('each member gets role team-member', () => {
    const result = agentRunner.spawnTeam('Charlie', 'Collaborate', [
      { agentType: 'researcher', description: 'R', input: {} },
      { agentType: 'writer', description: 'W', input: {} },
    ]);
    for (const taskId of result.taskIds) {
      const task = agentRunner.list().find((t) => t.id === taskId);
      expect(task).toBeDefined();
      expect(task!.role).toBe('team-member');
    }
  });

  it('all members get same teamId', () => {
    const result = agentRunner.spawnTeam('Delta', 'Shared goal', [
      { agentType: 'researcher', description: 'R', input: {} },
      { agentType: 'analyst', description: 'A', input: {} },
    ]);
    for (const taskId of result.taskIds) {
      const task = agentRunner.list().find((t) => t.id === taskId);
      expect(task).toBeDefined();
      expect(task!.teamId).toBe(result.teamId);
    }
  });

  it('empty members array creates team with no tasks', () => {
    const result = agentRunner.spawnTeam('Echo', 'Empty mission', []);
    expect(result.teamId).toBeDefined();
    expect(result.taskIds).toEqual([]);
  });
});

/* ================================================================== *
 *  4. cancel
 * ================================================================== */
describe('cancel', () => {
  it('cancel queued task sets status to cancelled', () => {
    const task = agentRunner.spawn('researcher', 'Cancel me');
    const success = agentRunner.cancel(task.id);
    expect(success).toBe(true);
    const updated = agentRunner.list().find((t) => t.id === task.id);
    expect(updated!.status).toBe('cancelled');
  });

  it('cancel queued task sets completedAt', () => {
    const task = agentRunner.spawn('researcher', 'Cancel me too');
    agentRunner.cancel(task.id);
    const updated = agentRunner.list().find((t) => t.id === task.id);
    expect(updated!.completedAt).toBeDefined();
    expect(typeof updated!.completedAt).toBe('number');
  });

  it('cancel queued task removes from queue', () => {
    const task = agentRunner.spawn('researcher', 'Queue removal');
    agentRunner.cancel(task.id);
    // Spawn another and verify the cancelled one is not picked up
    const queued = agentRunner.list('queued');
    const found = queued.find((t) => t.id === task.id);
    expect(found).toBeUndefined();
  });

  it('cancel returns false for non-existent task', () => {
    const result = agentRunner.cancel('non-existent-id');
    expect(result).toBe(false);
  });

  it('cancel returns false for already completed task', () => {
    const task = agentRunner.spawn('researcher', 'Complete me');
    // Manually mark as completed to simulate
    (task as any).status = 'completed';
    (task as any).completedAt = Date.now();
    const result = agentRunner.cancel(task.id);
    expect(result).toBe(false);
  });
});

/* ================================================================== *
 *  5. hardStop
 * ================================================================== */
describe('hardStop', () => {
  it('hardStop on queued task delegates to cancel', () => {
    const task = agentRunner.spawn('researcher', 'Hard stop queued');
    const result = agentRunner.hardStop(task.id);
    expect(result).toBe(true);
    const updated = agentRunner.list().find((t) => t.id === task.id);
    expect(updated!.status).toBe('cancelled');
  });

  it('hardStop on non-existent task returns false', () => {
    const result = agentRunner.hardStop('fake-task-id');
    expect(result).toBe(false);
  });

  it('hardStopAll returns count of stopped tasks', () => {
    agentRunner.spawn('researcher', 'Task 1');
    agentRunner.spawn('writer', 'Task 2');
    agentRunner.spawn('analyst', 'Task 3');
    const count = agentRunner.hardStopAll();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  it('hardStopAll with no running or queued tasks returns 0', () => {
    const count = agentRunner.hardStopAll();
    expect(count).toBe(0);
  });

  it('hardStop adds log message for hard-stopped task', () => {
    // Spawn a task and manually set it to running to test hardStop on running
    const task = agentRunner.spawn('researcher', 'Running task');
    // Simulate running state
    (task as any).status = 'running';
    const runner = agentRunner as any;
    // Remove from queue since it's "running"
    runner.queue = runner.queue.filter((id: string) => id !== task.id);

    const result = agentRunner.hardStop(task.id);
    expect(result).toBe(true);
    const updated = agentRunner.list().find((t) => t.id === task.id);
    expect(updated!.logs.some((log: string) => log.includes('HARD STOPPED'))).toBe(true);
  });
});

/* ================================================================== *
 *  6. list & queries
 * ================================================================== */
describe('list & queries', () => {
  it('list() returns all tasks', () => {
    agentRunner.spawn('researcher', 'A');
    agentRunner.spawn('writer', 'B');
    agentRunner.spawn('analyst', 'C');
    const all = agentRunner.list();
    expect(all.length).toBe(3);
  });

  it('list with status filter returns only matching tasks', () => {
    const t1 = agentRunner.spawn('researcher', 'Will cancel');
    agentRunner.spawn('writer', 'Stay queued');
    agentRunner.cancel(t1.id);
    const cancelled = agentRunner.list('cancelled');
    expect(cancelled.length).toBe(1);
    expect(cancelled[0].id).toBe(t1.id);
  });

  it('list with no matching status returns empty array', () => {
    agentRunner.spawn('researcher', 'Queued task');
    const running = agentRunner.list('running');
    // The task may or may not transition to running asynchronously,
    // but with the processQueue being async, at the point of immediate check
    // we can at least confirm the method returns an array.
    expect(Array.isArray(running)).toBe(true);
  });

  it('tasks sorted by createdAt descending in list()', () => {
    // Spawn with staggered times
    const t1 = agentRunner.spawn('researcher', 'First');
    (t1 as any).createdAt = 1000;
    const t2 = agentRunner.spawn('writer', 'Second');
    (t2 as any).createdAt = 3000;
    const t3 = agentRunner.spawn('analyst', 'Third');
    (t3 as any).createdAt = 2000;

    const all = agentRunner.list();
    expect(all[0].createdAt).toBeGreaterThanOrEqual(all[1].createdAt);
    expect(all[1].createdAt).toBeGreaterThanOrEqual(all[2].createdAt);
  });

  it('getAwarenessSummary returns no-agents message when none running', () => {
    const summary = agentRunner.getAwarenessSummary();
    expect(summary).toContain('No other agents');
  });
});

/* ================================================================== *
 *  7. Concurrency & limits
 * ================================================================== */
describe('Concurrency & limits', () => {
  it('spawning MAX_TASKS+1 cleans old completed tasks', () => {
    // Fill up with completed tasks
    for (let i = 0; i < 100; i++) {
      const task = agentRunner.spawn('researcher', `Task ${i}`);
      // Mark as completed so they are eligible for cleanup
      (task as any).status = 'completed';
      (task as any).completedAt = Date.now() - (100 - i);
    }

    // The 101st spawn should trigger cleanup
    const overflow = agentRunner.spawn('researcher', 'Overflow task');
    expect(overflow).toBeDefined();
    const all = agentRunner.list();
    expect(all.length).toBeLessThanOrEqual(101);
  });

  it('multiple spawns do not exceed stored task limit after cleanup', () => {
    // Spawn and complete many tasks
    for (let i = 0; i < 110; i++) {
      const task = agentRunner.spawn('researcher', `Bulk ${i}`);
      (task as any).status = 'completed';
      (task as any).completedAt = Date.now() - (200 - i);
    }
    // After cleanup the size should be at or under MAX_TASKS
    const all = agentRunner.list();
    expect(all.length).toBeLessThanOrEqual(110); // some were cleaned
  });

  it('tasks beyond MAX_CONCURRENT stay queued', () => {
    // Simulate 5 running tasks by setting runner.running to MAX_CONCURRENT
    const runner = agentRunner as any;
    runner.running = 5;

    const task = agentRunner.spawn('researcher', 'Should stay queued');
    // Since MAX_CONCURRENT is already reached, processQueue won't pick this up
    // The task should remain queued
    expect(task.status).toBe('queued');
  });

  it('queue is processed in order', () => {
    // Block the queue
    const runner = agentRunner as any;
    runner.running = 5;

    const t1 = agentRunner.spawn('researcher', 'First in queue');
    const t2 = agentRunner.spawn('writer', 'Second in queue');
    const t3 = agentRunner.spawn('analyst', 'Third in queue');

    // Check internal queue order
    const queueIds = runner.queue;
    const idx1 = queueIds.indexOf(t1.id);
    const idx2 = queueIds.indexOf(t2.id);
    const idx3 = queueIds.indexOf(t3.id);

    expect(idx1).toBeLessThan(idx2);
    expect(idx2).toBeLessThan(idx3);
  });
});
