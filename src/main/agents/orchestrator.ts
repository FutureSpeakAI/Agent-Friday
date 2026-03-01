/**
 * 2C — Multi-Agent Orchestrator (Phase 4: Orchestration Bridge)
 *
 * Decomposes complex goals into sub-tasks via Claude planning step,
 * spawns child agents through the Delegation Engine, tracks dependencies
 * via wave-based execution, and aggregates results into a cohesive output.
 *
 * Phase 4 upgrade: replaces inline ctx.callClaude() with real agent spawning
 * through delegationEngine.spawnSubAgent(), enabling:
 *   - Real sub-agent execution (research uses Gemini search, etc.)
 *   - Trust-tier inheritance across the delegation tree
 *   - Depth-limited recursive delegation (cLaw Second Law)
 *   - Agent Office visualization (sub-agents appear as sprites)
 *   - Halt propagation (cancelling orchestrator stops all children)
 *   - Partial result collection from interrupted sub-agents
 */

import { AgentDefinition, AgentContext } from './agent-types';
import { delegationEngine, TrustTier } from './delegation-engine';

// Late-bound import to avoid circular dependency: orchestrator -> agent-runner -> builtin-agents -> orchestrator
// Wrapped in _deps for test stubbing (vi.mock cannot intercept runtime require())
export const _deps = {
  getAgentRunner: (): any => require('./agent-runner').agentRunner,
};

interface SubTask {
  agentType: string;
  description: string;
  input: Record<string, unknown>;
  dependsOn?: number[]; // Indices of tasks this depends on
}

interface PlanStep {
  id: number;
  agentType: string;
  description: string;
  input: Record<string, unknown>;
  dependsOn: number[];
}

/**
 * Uses Claude to decompose a complex goal into a plan of sub-tasks,
 * then executes them respecting dependencies and aggregates results.
 */
async function decomposeGoal(
  goal: string,
  context: string,
  callClaude: (prompt: string, maxTokens?: number) => Promise<string>
): Promise<PlanStep[]> {
  const availableAgents: Array<{ name: string; description: string }> = _deps.getAgentRunner().getAgentTypes();
  const agentList = availableAgents
    .filter((a) => a.name !== 'orchestrate') // Prevent recursive orchestration in plan
    .map((a) => `- "${a.name}": ${a.description}`)
    .join('\n');

  const prompt = `You are a task decomposition engine. Break down a complex goal into concrete sub-tasks that can be executed by specialised agents.

AVAILABLE AGENTS:
${agentList}

GOAL: ${goal}
${context ? `\nCONTEXT: ${context}` : ''}

Decompose this into 2-6 sub-tasks. Each sub-task must use one of the available agents.
Return ONLY a JSON array with no other text. Each item must have:
- "agentType": one of the agent names above
- "description": what this sub-task should accomplish
- "input": object with the required input for that agent type
- "dependsOn": array of task indices (0-based) that must complete first (empty array if independent)

Rules:
- Research tasks are typically independent and can run in parallel
- Summarize tasks depend on research tasks that provide their input
- Code review tasks need code as input
- Draft-email tasks may depend on research for context
- Keep the plan focused and practical — don't over-decompose
- Each task should produce a meaningful, standalone output

Example for "Research AI governance and draft a briefing email to the board":
[
  {"agentType": "research", "description": "Research latest AI governance frameworks and regulations", "input": {"topic": "AI governance frameworks 2025"}, "dependsOn": []},
  {"agentType": "research", "description": "Research industry best practices for AI governance in enterprise", "input": {"topic": "enterprise AI governance best practices"}, "dependsOn": []},
  {"agentType": "summarize", "description": "Synthesise research findings into key points", "input": {"text": "{{results_0}} {{results_1}}", "style": "executive summary"}, "dependsOn": [0, 1]},
  {"agentType": "draft-email", "description": "Draft board briefing email with governance recommendations", "input": {"to": "Board of Directors", "subject": "AI Governance Update", "key_points": "{{results_2}}", "tone": "authoritative but accessible"}, "dependsOn": [2]}
]`;

  const response = await callClaude(prompt, 2048);

  // Parse the plan
  const match = response.match(/\[[\s\S]*\]/);
  if (!match) {
    throw new Error('Failed to decompose goal — Claude did not return valid JSON');
  }

  const rawPlan = JSON.parse(match[0]) as SubTask[];

  // Validate and clean
  const agentNames = new Set(availableAgents.map((a) => a.name));
  agentNames.delete('orchestrate'); // Prevent recursive orchestration
  const plan: PlanStep[] = rawPlan.map((task, i) => {
    if (!agentNames.has(task.agentType)) {
      throw new Error(`Invalid agent type "${task.agentType}" in plan step ${i}`);
    }
    return {
      id: i,
      agentType: task.agentType,
      description: task.description,
      input: task.input || {},
      dependsOn: Array.isArray(task.dependsOn) ? task.dependsOn.filter((d) => d >= 0 && d < i) : [],
    };
  });

  return plan;
}

/**
 * Resolve template references like {{results_0}} in input values,
 * replacing them with actual results from completed tasks.
 */
function resolveInputTemplates(
  input: Record<string, unknown>,
  results: Map<number, string>
): Record<string, unknown> {
  const resolved: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === 'string') {
      resolved[key] = value.replace(/\{\{results_(\d+)\}\}/g, (_, idx) => {
        const result = results.get(Number(idx));
        return result || `[Task ${idx} result unavailable]`;
      });
    } else {
      resolved[key] = value;
    }
  }
  return resolved;
}

/**
 * Poll agent runner until all given task IDs reach a terminal state.
 * Returns when all tasks are completed, failed, or cancelled.
 */
async function waitForAgentTasks(
  taskIds: string[],
  ctx: AgentContext,
  maxWaitMs: number = 5 * 60 * 1000
): Promise<void> {
  const runner = _deps.getAgentRunner();
  const start = Date.now();

  while (Date.now() - start < maxWaitMs) {
    if (ctx.isCancelled()) return;

    const allDone = taskIds.every((id) => {
      const task = runner.get(id);
      return task && (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled');
    });

    if (allDone) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

/**
 * Group plan steps into dependency waves for parallel execution.
 * Each wave contains steps whose dependencies are all in earlier waves.
 */
function computeWaves(plan: PlanStep[]): number[][] {
  const waves: number[][] = [];
  const assigned = new Set<number>();

  while (assigned.size < plan.length) {
    const wave: number[] = [];
    for (const step of plan) {
      if (assigned.has(step.id)) continue;
      const depsReady = step.dependsOn.every((d) => assigned.has(d));
      if (depsReady) {
        wave.push(step.id);
      }
    }
    if (wave.length === 0) {
      // Circular dependency — break and run remaining sequentially
      for (const step of plan) {
        if (!assigned.has(step.id)) wave.push(step.id);
      }
      waves.push(wave);
      break;
    }
    waves.push(wave);
    for (const id of wave) assigned.add(id);
  }

  return waves;
}

/**
 * The orchestrate agent definition — registered as a built-in agent.
 *
 * Phase 4: Now spawns real sub-agents via the Delegation Engine instead
 * of using inline Claude calls. This means sub-agents:
 *   - Execute their full logic (e.g., research agent uses Gemini search)
 *   - Appear in the Agent Office visualization
 *   - Inherit trust tiers (cLaw First Law)
 *   - Can be halted via delegation tree (cLaw Third Law / interruptibility)
 */
export const orchestrateAgent: AgentDefinition = {
  name: 'orchestrate',
  description:
    'Decompose a complex, multi-step goal into sub-tasks, execute them with specialised agents (respecting dependencies), and aggregate the results into a cohesive output. Use this for goals that require multiple agent types working together — e.g. "research X, summarise findings, and draft an email about it".',
  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    const goal = String(input.goal || input.description || input.task || '');
    const context = String(input.context || '');
    if (!goal) throw new Error('No goal provided for orchestration');

    ctx.log(`Orchestrating: "${goal}"`);
    ctx.setProgress(5);

    // ── Delegation Engine Registration ──────────────────────────────
    // Determine trust tier (from delegation metadata if this orchestrator
    // was itself spawned as a sub-agent, otherwise default to 'local')
    const delegationMeta = input.__delegation as
      | { trustTier?: TrustTier; depth?: number }
      | undefined;
    const trustTier: TrustTier = delegationMeta?.trustTier || 'local';

    // Register as delegation root only if not already in a delegation tree
    const isAlreadyDelegated = delegationEngine.isInTree(ctx.taskId);
    if (!isAlreadyDelegated) {
      delegationEngine.registerRoot(ctx.taskId, 'orchestrate', goal, trustTier);
      ctx.log(`Delegation root registered (trust=${trustTier})`);
    } else {
      ctx.log(`Already in delegation tree (trust=${delegationEngine.getTrustTier(ctx.taskId)})`);
    }

    // ── Step 1: Decompose goal into plan ─────────────────────────────
    ctx.log('Planning sub-tasks...');
    ctx.setPhase('planning');
    ctx.think('planning', `Decomposing goal into sub-tasks: "${goal}"`);

    let plan: PlanStep[];
    try {
      plan = await decomposeGoal(goal, context, ctx.callClaude);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      delegationEngine.reportCompletion(ctx.taskId, null, msg);
      throw new Error(`Planning failed: ${msg}`);
    }

    ctx.log(`Plan: ${plan.length} sub-tasks`);
    for (const step of plan) {
      const deps =
        step.dependsOn.length > 0
          ? ` (after ${step.dependsOn.map((d) => `#${d}`).join(', ')})`
          : ' (independent)';
      ctx.log(`  #${step.id} [${step.agentType}] ${step.description}${deps}`);
    }
    ctx.think('planning', `Plan ready: ${plan.length} sub-tasks across ${computeWaves(plan).length} waves`);
    ctx.setProgress(15);

    if (ctx.isCancelled()) {
      await delegationEngine.haltTree(ctx.taskId);
      return 'Cancelled during planning';
    }

    // ── Step 2: Execute plan via Delegation Engine ────────────────────
    ctx.setPhase('executing');
    const results = new Map<number, string>();
    const taskIdMap = new Map<number, string>(); // plan step index → AgentTask ID
    const completed = new Set<number>();
    const failed = new Set<number>();

    const waves = computeWaves(plan);
    ctx.log(`Execution plan: ${waves.length} waves`);

    const progressPerStep = 70 / plan.length;
    let completedCount = 0;

    for (let waveIdx = 0; waveIdx < waves.length; waveIdx++) {
      const wave = waves[waveIdx];
      ctx.log(`\nWave ${waveIdx + 1}/${waves.length}: ${wave.length} parallel task(s)`);
      ctx.think('executing', `Starting wave ${waveIdx + 1}/${waves.length} with ${wave.length} task(s)`);

      if (ctx.isCancelled()) {
        await delegationEngine.haltTree(ctx.taskId);
        return 'Cancelled during execution';
      }

      // Spawn all tasks in this wave via delegation engine
      const waveTaskIds: string[] = [];

      for (const stepIdx of wave) {
        const step = plan[stepIdx];

        // Check if any dependency failed
        const failedDep = step.dependsOn.find((d) => failed.has(d));
        if (failedDep !== undefined) {
          ctx.log(`  #${stepIdx} skipped — dependency #${failedDep} failed`);
          failed.add(stepIdx);
          completedCount++;
          ctx.setProgress(15 + Math.round(completedCount * progressPerStep));
          continue;
        }

        // Resolve template references in input (e.g., {{results_0}})
        const resolvedInput = resolveInputTemplates(step.input, results);

        // Spawn via delegation engine (which calls agentRunner.spawn internally)
        const spawnResult = await delegationEngine.spawnSubAgent({
          agentType: step.agentType,
          description: step.description,
          input: resolvedInput,
          parentTaskId: ctx.taskId,
          parentContext: `Orchestrating: "${goal}"\nThis is step #${step.id + 1} of ${plan.length}: ${step.description}`,
        });

        if (spawnResult.success && spawnResult.taskId) {
          taskIdMap.set(stepIdx, spawnResult.taskId);
          waveTaskIds.push(spawnResult.taskId);
          ctx.log(`  #${stepIdx} [${step.agentType}] spawned → ${spawnResult.taskId.slice(0, 8)}`);
          ctx.think(
            'delegating',
            `Spawned ${step.agentType} sub-agent for: ${step.description.slice(0, 80)}`
          );
        } else {
          ctx.log(`  #${stepIdx} [${step.agentType}] spawn failed: ${spawnResult.error}`);
          failed.add(stepIdx);
          results.set(stepIdx, `[SPAWN FAILED: ${spawnResult.error}]`);
          completedCount++;
          ctx.setProgress(15 + Math.round(completedCount * progressPerStep));
        }
      }

      // Wait for all spawned tasks in this wave to complete
      if (waveTaskIds.length > 0) {
        ctx.think('waiting', `Waiting for ${waveTaskIds.length} sub-agent(s) in wave ${waveIdx + 1}`);
        await waitForAgentTasks(waveTaskIds, ctx);
      }

      // Collect results from completed tasks and bridge to delegation engine
      const runner = _deps.getAgentRunner();
      for (const stepIdx of wave) {
        if (failed.has(stepIdx)) continue; // Already failed (dep or spawn)

        const agentTaskId = taskIdMap.get(stepIdx);
        if (!agentTaskId) continue;

        const task = runner.get(agentTaskId);
        if (!task) {
          failed.add(stepIdx);
          results.set(stepIdx, '[Agent task not found]');
          completedCount++;
          ctx.setProgress(15 + Math.round(completedCount * progressPerStep));
          continue;
        }

        // Bridge: report completion to delegation engine
        delegationEngine.reportCompletion(
          agentTaskId,
          task.result || null,
          task.error || null
        );

        if (task.status === 'completed' && task.result) {
          results.set(stepIdx, task.result);
          completed.add(stepIdx);
          ctx.log(`  #${stepIdx} [${plan[stepIdx].agentType}] completed ✓`);
        } else if (task.status === 'cancelled') {
          failed.add(stepIdx);
          results.set(stepIdx, '[CANCELLED]');
          ctx.log(`  #${stepIdx} [${plan[stepIdx].agentType}] cancelled`);
        } else {
          failed.add(stepIdx);
          results.set(stepIdx, `[FAILED: ${task.error || 'Unknown error'}]`);
          ctx.log(`  #${stepIdx} [${plan[stepIdx].agentType}] failed: ${task.error || 'Unknown error'}`);
        }

        completedCount++;
        ctx.setProgress(15 + Math.round(completedCount * progressPerStep));
      }
    }

    ctx.setProgress(90);

    if (ctx.isCancelled()) {
      await delegationEngine.haltTree(ctx.taskId);
      return 'Cancelled during aggregation';
    }

    // ── Step 3: Aggregate results ────────────────────────────────────
    ctx.setPhase('aggregating');
    ctx.log('\nAggregating results...');
    ctx.think('synthesising', 'Aggregating all sub-task results into cohesive output');

    const resultParts: string[] = [];
    for (const step of plan) {
      const result = results.get(step.id);
      const status = completed.has(step.id) ? '✓' : failed.has(step.id) ? '✗' : '?';
      resultParts.push(
        `## Step ${step.id + 1}: ${step.description} [${status}]\n**Agent**: ${step.agentType}\n\n${result || '[No result]'}`
      );
    }

    const aggregationPrompt = `You are aggregating the results of a multi-step task.

ORIGINAL GOAL: ${goal}

INDIVIDUAL RESULTS:
${resultParts.join('\n\n---\n\n')}

Write a cohesive final output that:
1. Synthesises all sub-task results into a unified response
2. Highlights the most important findings/outputs
3. Notes any failed steps and their impact
4. Provides a clear conclusion or next steps

Keep it well-structured and actionable. Don't just concatenate — synthesise.`;

    const finalResult = await ctx.callClaude(aggregationPrompt, 3000);
    ctx.setProgress(100);

    const summary = `${plan.length} sub-tasks executed (${completed.size} succeeded, ${failed.size} failed)`;
    ctx.log(`\nOrchestration complete: ${summary}`);
    ctx.think('complete', `Orchestration finished: ${summary}`);

    // Report orchestrator completion to delegation engine
    const fullOutput = `# Orchestrated Result\n_${summary}_\n\n${finalResult}\n\n---\n\n## Raw Sub-Task Results\n\n${resultParts.join('\n\n---\n\n')}`;
    delegationEngine.reportCompletion(ctx.taskId, fullOutput, null);

    return fullOutput;
  },
};
