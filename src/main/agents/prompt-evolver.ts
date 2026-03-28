/**
 * prompt-evolver.ts — Autonomous prompt engineering via iterative evolution.
 *
 * Inspired by autoresearch's approach to ML: instead of manually writing prompts,
 * let an agent iterate on them with measurable quality feedback. Each cycle:
 *   1. Mutate the prompt (add/remove/rephrase instructions)
 *   2. Run test queries through the mutated prompt
 *   3. Score outputs with a judge LLM
 *   4. Keep mutations that improve the score, discard regressions
 *
 * This is the core mechanism for Agent Friday's self-improving intelligence.
 * Prompts are the "editable surface" — the LLM's behavior is the "metric."
 */

import { AgentDefinition, AgentContext } from './agent-types';
import { resultsLedger } from './results-ledger';

// ── Types ───────────────────────────────────────────────────────────

export interface PromptCandidate {
  /** The prompt text being evaluated */
  prompt: string;
  /** Average quality score across test queries (0-10) */
  score: number;
  /** Generation number */
  generation: number;
  /** What was changed from the parent */
  mutation: string;
}

export interface EvalQuery {
  /** The test input to send through the prompt */
  input: string;
  /** What a good response should contain or achieve */
  criteria: string;
  /** Optional: an ideal reference response */
  reference?: string;
}

// ── Scoring Rubric ──────────────────────────────────────────────────

const JUDGE_RUBRIC = `You are a strict but fair judge evaluating AI assistant output quality.

Score the response on a scale of 0-10 across these dimensions:
1. ACCURACY (0-10): Is the information correct and complete?
2. RELEVANCE (0-10): Does it address the query directly?
3. CLARITY (0-10): Is it well-structured and easy to understand?
4. HELPFULNESS (0-10): Would a user find this genuinely useful?
5. PERSONALITY (0-10): Does it match the intended persona/tone?

Return ONLY a JSON object: {"accuracy": N, "relevance": N, "clarity": N, "helpfulness": N, "personality": N, "overall": N, "feedback": "brief note"}
where "overall" is your holistic score (not just an average).`;

// ── The Prompt Evolver Agent ────────────────────────────────────────

export const promptEvolverAgent: AgentDefinition = {
  name: 'evolve-prompt',
  description:
    'Iteratively improve a system prompt using judge-scored evaluation. ' +
    'Mutates the prompt, tests it, scores output quality, and keeps improvements.',

  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    // ── 1. Parse inputs ─────────────────────────────────────────────
    ctx.setPhase('parsing inputs');
    ctx.setProgress(2);

    const basePrompt = String(input.prompt || input.systemPrompt || '');
    if (!basePrompt) throw new Error('evolve-prompt requires a `prompt` (the system prompt to improve)');

    const targetPersona = String(input.persona || 'helpful AI assistant');
    const maxGenerations = Number(input.maxGenerations || 10);

    // Parse test queries
    let testQueries: EvalQuery[];
    if (Array.isArray(input.queries)) {
      testQueries = (input.queries as Array<Record<string, unknown>>).map((q) => ({
        input: String(q.input || q.query || ''),
        criteria: String(q.criteria || 'accurate, helpful, and clear'),
        reference: q.reference ? String(q.reference) : undefined,
      }));
    } else {
      // Generate test queries from the prompt context
      ctx.think('planning', 'No test queries provided — generating evaluation suite');
      const queryGenPrompt = `Given this system prompt for a "${targetPersona}":

${basePrompt.slice(0, 2000)}

Generate 5 diverse test queries that exercise the key behaviors this prompt defines.
Return ONLY a JSON array: [{"input": "query text", "criteria": "what a good response should do"}]`;

      const queryRaw = await ctx.callClaude(queryGenPrompt, 1000);
      try {
        const match = queryRaw.match(/\[[\s\S]*\]/);
        testQueries = match ? JSON.parse(match[0]) : [];
      } catch {
        testQueries = [
          { input: 'Hello, who are you?', criteria: 'Should match the persona and be welcoming' },
          { input: 'Help me with a complex task', criteria: 'Should be structured, clear, and actionable' },
          { input: 'What do you think about this?', criteria: 'Should show depth without being sycophantic' },
        ];
      }
    }

    ctx.log(`Evolving prompt for "${targetPersona}" with ${testQueries.length} test queries over ${maxGenerations} generations`);

    // ── 2. Evaluate baseline ────────────────────────────────────────
    ctx.setPhase('baseline evaluation');
    ctx.setProgress(5);

    const baseline = await evaluatePrompt(basePrompt, testQueries, targetPersona, ctx);
    ctx.log(`Baseline score: ${baseline.toFixed(2)}/10`);
    ctx.think('baseline', `Baseline prompt scores ${baseline.toFixed(2)}/10`);

    let bestPrompt = basePrompt;
    let bestScore = baseline;
    const history: PromptCandidate[] = [{
      prompt: basePrompt,
      score: baseline,
      generation: 0,
      mutation: 'original',
    }];

    // ── 3. Evolution loop ───────────────────────────────────────────
    for (let gen = 1; gen <= maxGenerations; gen++) {
      if (ctx.isCancelled()) break;

      ctx.setPhase(`generation ${gen}/${maxGenerations}`);
      ctx.setProgress(10 + (gen / maxGenerations) * 80);

      // Generate a mutation
      ctx.think('mutating', `Generation ${gen}: creating prompt variant`);
      const recentHistory = history.slice(-5).map((h) =>
        `Gen ${h.generation}: score=${h.score.toFixed(1)}, mutation="${h.mutation}"`
      ).join('\n');

      const mutationPrompt = `You are evolving a system prompt to maximize output quality. The target persona is "${targetPersona}".

CURRENT BEST PROMPT (score: ${bestScore.toFixed(2)}/10):
${bestPrompt.slice(0, 3000)}

RECENT EVOLUTION HISTORY:
${recentHistory}

TEST QUERIES USED FOR EVALUATION:
${testQueries.map((q, i) => `${i + 1}. "${q.input}" — criteria: ${q.criteria}`).join('\n')}

Generate ONE specific mutation to improve the prompt. Consider:
- Adding a missing instruction that would improve weak areas
- Rephrasing an instruction for clarity
- Adding an example or constraint
- Removing redundant or counterproductive instructions
- Adjusting tone or structure

Return your response as:
MUTATION: <brief description of what you changed>
PROMPT: <the complete mutated prompt>`;

      const mutationRaw = await ctx.callClaude(mutationPrompt, 3000);

      // Parse the mutation
      const mutDesc = mutationRaw.match(/MUTATION:\s*(.+?)(?=\nPROMPT:)/s)?.[1]?.trim() || 'unknown mutation';
      const mutPrompt = mutationRaw.match(/PROMPT:\s*([\s\S]+)/)?.[1]?.trim() || bestPrompt;

      if (mutPrompt === bestPrompt) {
        ctx.log(`Gen ${gen}: mutation produced no change — skipping`);
        continue;
      }

      // Evaluate the mutation
      ctx.think('evaluating', `Gen ${gen}: testing "${mutDesc}"`);
      const score = await evaluatePrompt(mutPrompt, testQueries, targetPersona, ctx);

      const delta = score - bestScore;
      const improved = delta > 0;

      // Record to ledger
      await resultsLedger.record('prompt-evolution', {
        timestamp: new Date().toISOString(),
        runTag: `evolve/${targetPersona}`,
        cycle: gen,
        metricValue: score,
        previousMetric: bestScore,
        delta,
        outcome: improved ? 'kept' : 'discarded',
        changes: mutDesc,
        durationMs: 0,
      });

      history.push({
        prompt: mutPrompt,
        score,
        generation: gen,
        mutation: mutDesc,
      });

      if (improved) {
        bestPrompt = mutPrompt;
        bestScore = score;
        ctx.log(`Gen ${gen}: KEPT "${mutDesc}" — score ${bestScore.toFixed(2)} (+${delta.toFixed(2)})`);
        ctx.think('keeping', `Gen ${gen}: improved! ${(score - delta).toFixed(2)} → ${score.toFixed(2)}`);
      } else {
        ctx.log(`Gen ${gen}: discarded "${mutDesc}" — score ${score.toFixed(2)} (${delta.toFixed(2)})`);
        ctx.think('discarding', `Gen ${gen}: no improvement (${delta.toFixed(2)})`);
      }
    }

    // ── 4. Summary ──────────────────────────────────────────────────
    ctx.setPhase('summary');
    ctx.setProgress(95);

    const improvements = history.filter((h, i) => i > 0 && h.score > history[i - 1].score).length;
    const report = [
      `\n═══ PROMPT EVOLUTION COMPLETE ═══`,
      `Persona: ${targetPersona}`,
      `Generations: ${history.length - 1}`,
      `Improvements: ${improvements}`,
      `Baseline score: ${baseline.toFixed(2)}/10`,
      `Final score: ${bestScore.toFixed(2)}/10`,
      `Total improvement: +${(bestScore - baseline).toFixed(2)}`,
      `\n── Evolved Prompt ──`,
      bestPrompt,
      `\n── Evolution History ──`,
      ...history.map((h) => `  Gen ${h.generation}: ${h.score.toFixed(2)} — ${h.mutation}`),
    ].join('\n');

    ctx.log(report);
    ctx.setProgress(100);
    return report;
  },
};

// ── Evaluation Helper ───────────────────────────────────────────────

/**
 * Evaluate a prompt by running test queries and scoring with a judge.
 * Returns average score (0-10).
 */
async function evaluatePrompt(
  systemPrompt: string,
  queries: EvalQuery[],
  persona: string,
  ctx: AgentContext
): Promise<number> {
  const scores: number[] = [];

  for (const query of queries) {
    if (ctx.isCancelled()) break;

    // Generate response using the candidate prompt
    const responsePrompt = `${systemPrompt}\n\nUser: ${query.input}`;
    const response = await ctx.callClaude(responsePrompt, 1000);

    // Judge the response
    const judgePrompt = `${JUDGE_RUBRIC}

PERSONA TARGET: ${persona}
USER QUERY: ${query.input}
EVALUATION CRITERIA: ${query.criteria}
${query.reference ? `REFERENCE RESPONSE: ${query.reference}` : ''}

RESPONSE TO EVALUATE:
${response.slice(0, 2000)}

Score this response. Return ONLY the JSON object.`;

    const judgeRaw = await ctx.callClaude(judgePrompt, 300);
    try {
      const match = judgeRaw.match(/\{[\s\S]*\}/);
      if (match) {
        const parsed = JSON.parse(match[0]);
        scores.push(Number(parsed.overall) || 5);
      } else {
        scores.push(5); // neutral default
      }
    } catch {
      scores.push(5);
    }
  }

  if (scores.length === 0) return 5;
  return scores.reduce((a, b) => a + b, 0) / scores.length;
}
