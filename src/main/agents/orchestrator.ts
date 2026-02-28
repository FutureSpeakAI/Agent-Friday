/**
 * 2C — Multi-Agent Orchestrator
 *
 * Decomposes complex goals into sub-tasks via Claude planning step,
 * spawns child agents, tracks dependencies, and aggregates results
 * into a cohesive final output.
 */

import { AgentDefinition, AgentContext } from './agent-types';
import { settingsManager } from '../settings';

// Late-bound import to avoid circular dependency: orchestrator -> agent-runner -> builtin-agents -> orchestrator
function getAgentRunner() {
  return require('./agent-runner').agentRunner;
}

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
  const availableAgents: Array<{ name: string; description: string }> = getAgentRunner().getAgentTypes();
  const agentList = availableAgents
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
 * The orchestrate agent definition — registered as a built-in agent.
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

    // Step 1: Decompose goal into plan
    ctx.log('Planning sub-tasks...');
    let plan: PlanStep[];
    try {
      plan = await decomposeGoal(goal, context, ctx.callClaude);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Planning failed: ${msg}`);
    }

    ctx.log(`Plan: ${plan.length} sub-tasks`);
    for (const step of plan) {
      const deps = step.dependsOn.length > 0 ? ` (after ${step.dependsOn.map((d) => `#${d}`).join(', ')})` : ' (independent)';
      ctx.log(`  #${step.id} [${step.agentType}] ${step.description}${deps}`);
    }
    ctx.setProgress(15);

    if (ctx.isCancelled()) return 'Cancelled during planning';

    // Step 2: Execute plan respecting dependencies
    const results = new Map<number, string>();
    const taskIds = new Map<number, string>(); // plan step index → AgentTask ID
    const completed = new Set<number>();
    const failed = new Set<number>();

    // Group tasks by dependency wave
    const waves: number[][] = [];
    const assigned = new Set<number>();

    while (assigned.size < plan.length) {
      const wave: number[] = [];
      for (const step of plan) {
        if (assigned.has(step.id)) continue;
        // All dependencies must be assigned already (completed or in earlier waves)
        const depsReady = step.dependsOn.every((d) => assigned.has(d));
        if (depsReady) {
          wave.push(step.id);
        }
      }
      if (wave.length === 0) {
        // Circular dependency or other issue — break and run remaining sequentially
        for (const step of plan) {
          if (!assigned.has(step.id)) wave.push(step.id);
        }
        waves.push(wave);
        break;
      }
      waves.push(wave);
      for (const id of wave) assigned.add(id);
    }

    ctx.log(`Execution plan: ${waves.length} waves`);

    const progressPerStep = 70 / plan.length;
    let completedCount = 0;

    for (let waveIdx = 0; waveIdx < waves.length; waveIdx++) {
      const wave = waves[waveIdx];
      ctx.log(`\nWave ${waveIdx + 1}/${waves.length}: ${wave.length} parallel task(s)`);

      if (ctx.isCancelled()) return 'Cancelled during execution';

      // Spawn all tasks in this wave
      const wavePromises: Promise<void>[] = [];

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

        // Resolve template references in input
        const resolvedInput = resolveInputTemplates(step.input, results);

        const promise = (async () => {
          try {
            ctx.log(`  #${stepIdx} [${step.agentType}] starting: ${step.description}`);

            // Use Claude directly rather than spawning sub-agents for simplicity
            // This avoids agent inception issues and keeps the orchestrator cohesive
            const agentPrompt = buildAgentPrompt(step.agentType, step.description, resolvedInput);
            const result = await ctx.callClaude(agentPrompt, 3000);

            results.set(stepIdx, result);
            completed.add(stepIdx);
            ctx.log(`  #${stepIdx} [${step.agentType}] completed ✓`);
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            failed.add(stepIdx);
            results.set(stepIdx, `[FAILED: ${msg}]`);
            ctx.log(`  #${stepIdx} [${step.agentType}] failed: ${msg}`);
          }

          completedCount++;
          ctx.setProgress(15 + Math.round(completedCount * progressPerStep));
        })();

        wavePromises.push(promise);
      }

      // Wait for all tasks in this wave to complete before moving to next
      await Promise.all(wavePromises);
    }

    ctx.setProgress(90);

    if (ctx.isCancelled()) return 'Cancelled during aggregation';

    // Step 3: Aggregate results
    ctx.log('\nAggregating results...');

    const resultParts: string[] = [];
    for (const step of plan) {
      const result = results.get(step.id);
      const status = completed.has(step.id) ? '✓' : failed.has(step.id) ? '✗' : '?';
      resultParts.push(`## Step ${step.id + 1}: ${step.description} [${status}]\n**Agent**: ${step.agentType}\n\n${result || '[No result]'}`);
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

    return `# Orchestrated Result\n_${summary}_\n\n${finalResult}\n\n---\n\n## Raw Sub-Task Results\n\n${resultParts.join('\n\n---\n\n')}`;
  },
};

/**
 * Build a Claude prompt that mimics what the individual agent would do,
 * but inline within the orchestrator's Claude calls.
 */
function buildAgentPrompt(
  agentType: string,
  description: string,
  input: Record<string, unknown>
): string {
  switch (agentType) {
    case 'research':
      return `You are a research analyst. Research the following topic thoroughly and provide a comprehensive briefing.

TOPIC: ${input.topic || input.query || description}

Provide a well-structured briefing (300-600 words) with key findings, analysis, and actionable takeaways.`;

    case 'summarize':
      return `Summarise the following text as a ${input.style || 'concise briefing'}. Highlight key points and use clear structure.

TEXT:
${String(input.text || input.content || '').slice(0, 10000)}

Provide a clear, well-structured summary.`;

    case 'code-review':
      return `You are a senior engineer. Review this code for bugs, security issues, performance, and best practices.

LANGUAGE: ${input.language || 'auto-detect'}
FOCUS: ${input.focus || 'all areas'}

CODE:
\`\`\`
${String(input.code || '').slice(0, 12000)}
\`\`\`

Provide a detailed, actionable code review.`;

    case 'draft-email':
      return `Draft a professional email:

TO: ${input.to || 'recipient'}
SUBJECT: ${input.subject || ''}
KEY POINTS: ${input.key_points || input.points || ''}
TONE: ${input.tone || 'professional but warm'}

Write a natural, well-structured email. The sender is ${settingsManager.getAgentConfig().userName || 'the user'}.`;

    default:
      return `Task: ${description}\n\nInput: ${JSON.stringify(input, null, 2)}\n\nExecute this task and provide a clear result.`;
  }
}
