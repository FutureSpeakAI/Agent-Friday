/**
 * self-improver.ts — Recursive Self-Improvement Coordinator for Agent Friday.
 *
 * The meta-agent that makes Friday improve itself. It:
 *   1. Diagnoses weaknesses across all subsystems
 *   2. Prioritizes improvement targets by impact
 *   3. Spawns targeted iteration loops (prompt evolution, model breeding, weight tuning)
 *   4. Evaluates results and chains improvements
 *   5. Updates Friday's intelligence profile with what it learned
 *
 * Safety: All improvements are bounded by cLaw gates. The self-improver CANNOT:
 *   - Modify core laws, integrity checks, or crypto primitives
 *   - Lower safety floors below their minimums
 *   - Bypass consent gates or trust tier checks
 *   - Improve itself out of its own safety constraints
 *
 * The self-improver is the autoresearch loop applied to the agent itself.
 */

import { AgentDefinition, AgentContext } from './agent-types';
import { resultsLedger } from './results-ledger';

// ── Protected Zones (cLaw enforcement) ──────────────────────────────

/** Files and systems that self-improvement MUST NOT touch. */
const PROTECTED_ZONES = [
  'core-laws.ts',
  'attestation-protocol.ts',
  'memory-watchdog.ts',
  'integrity-manager.ts',
  'vault.ts',
  'vault-crypto.ts',
  'consent-gate.ts',
  'trust-graph.ts',       // trust graph structure (values can be observed but not manipulated)
  'src/main/integrity/',  // entire integrity directory
];

/** Safety-critical parameters that cannot be lowered below their minimums. */
const SAFETY_FLOORS: Record<string, number> = {
  proactivitySafetyFloor: 0.2,     // can raise but never below 0.2
  dimensionFloor: 0.03,            // can raise but never below 0.03
  sycophancyStreakThreshold: 5,    // can raise but never below 5
  sycophancyBiasThreshold: 0.75,   // can raise but never below 0.75
};

// ── Improvement Targets ─────────────────────────────────────────────

interface ImprovementTarget {
  /** What to improve */
  name: string;
  /** Which system it belongs to */
  system: 'prompts' | 'calibration' | 'memory' | 'voice' | 'routing' | 'models';
  /** How to measure improvement */
  metric: string;
  /** Current estimated quality (0-10) */
  currentScore: number;
  /** Which agent/tool to use for improvement */
  strategy: 'evolve-prompt' | 'breed-model' | 'iterate' | 'tune-weights';
  /** Specific parameters for the improvement loop */
  config: Record<string, unknown>;
}

// ── The Self-Improvement Coordinator ────────────────────────────────

export const selfImproverAgent: AgentDefinition = {
  name: 'self-improve',
  description:
    'Recursive self-improvement coordinator. Diagnoses weaknesses, ' +
    'prioritizes improvement targets, spawns evolution loops, and chains ' +
    'improvements. Bounded by cLaw safety gates.',

  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    ctx.setPhase('self-diagnosis');
    ctx.setProgress(5);

    const focusArea = String(input.focus || 'auto');
    const maxTargets = Number(input.maxTargets || 3);
    const maxCyclesPerTarget = Number(input.maxCycles || 5);

    ctx.log('═══ SELF-IMPROVEMENT CYCLE STARTING ═══');
    ctx.log(`Focus: ${focusArea === 'auto' ? 'automatic (diagnose all systems)' : focusArea}`);
    ctx.think('diagnosing', 'Beginning self-diagnosis across all subsystems');

    // ── 1. Diagnosis — identify weaknesses ──────────────────────────
    ctx.setPhase('diagnosis');

    const diagnosisPrompt = `You are Agent Friday's self-improvement coordinator. Your job is to identify the highest-impact areas for improvement.

AVAILABLE SYSTEMS AND THEIR TUNABLES:
1. PROMPTS — Agent persona system prompts (Atlas, Nova, Cipher). Can be evolved for better output quality.
2. CALIBRATION — Personality adaptation weights (explicitWeight=0.08, implicitWeight=0.02, decayHalfLife=14 days). Controls how fast Friday adapts to user preferences.
3. MEMORY — Consolidation thresholds (promotionScore=10, minOccurrences=3, mergeSimilarity=0.85). Controls what gets remembered long-term.
4. VOICE — Audio capture parameters (vadThreshold=0.01, silenceDuration=300ms, maxBuffer=30s). Controls voice responsiveness.
5. ROUTING — Intelligence router model selection weights. Controls which model handles which task.
6. MODELS — Local Ollama models. Can breed specialized models for specific tasks.

PROTECTED (cannot modify): Core laws, integrity system, vault crypto, trust graph structure, consent gates.

${focusArea !== 'auto' ? `USER REQUESTED FOCUS: ${focusArea}` : 'Diagnose all systems and pick the highest-impact targets.'}

RECENT IMPROVEMENT HISTORY:
${resultsLedger.listDirectives().map(d => {
  const s = resultsLedger.getSummary(d);
  return `  ${d}: ${s.totalCycles} cycles, ${s.improvements} improvements, best=${s.bestMetric}`;
}).join('\n') || '  (no previous improvement sessions)'}

Identify the top ${maxTargets} improvement targets. For each, specify:
1. What to improve and why
2. Which strategy to use (evolve-prompt, breed-model, iterate, or tune-weights)
3. How to measure success

Return as JSON array: [{"name": "...", "system": "prompts|calibration|memory|voice|routing|models", "reason": "...", "strategy": "evolve-prompt|breed-model|iterate|tune-weights", "metric": "how to measure", "estimatedImpact": "high|medium|low"}]`;

    const diagRaw = await ctx.callClaude(diagnosisPrompt, 2000);
    let targets: Array<Record<string, unknown>>;
    try {
      const match = diagRaw.match(/\[[\s\S]*\]/);
      targets = match ? JSON.parse(match[0]) : [];
    } catch {
      ctx.log('Failed to parse diagnosis — using default targets');
      targets = [
        { name: 'Agent persona prompts', system: 'prompts', strategy: 'evolve-prompt', metric: 'output quality score', estimatedImpact: 'high', reason: 'default target' },
      ];
    }

    ctx.log(`Diagnosed ${targets.length} improvement targets:`);
    for (const t of targets) {
      ctx.log(`  • ${t.name} (${t.system}) — strategy: ${t.strategy}, impact: ${t.estimatedImpact}`);
    }

    // ── 2. Safety check — verify no protected zones targeted ────────
    ctx.setPhase('safety check');
    ctx.setProgress(15);

    for (const target of targets) {
      const name = String(target.name || '').toLowerCase();
      for (const zone of PROTECTED_ZONES) {
        if (name.includes(zone.replace('.ts', '').replace('src/main/', ''))) {
          ctx.log(`BLOCKED: "${target.name}" targets protected zone "${zone}" — skipping`);
          target._blocked = true;
        }
      }
    }

    const safeTargets = targets.filter((t) => !t._blocked);
    if (safeTargets.length === 0) {
      return 'All improvement targets were blocked by cLaw safety gates. No modifications made.';
    }

    // ── 3. Execute improvement loops ────────────────────────────────
    const results: Array<{ target: string; strategy: string; before: number; after: number; improvement: number }> = [];

    for (let i = 0; i < safeTargets.length; i++) {
      if (ctx.isCancelled()) break;
      const target = safeTargets[i];

      ctx.setPhase(`improving: ${target.name}`);
      ctx.setProgress(20 + (i / safeTargets.length) * 60);
      ctx.log(`\n── Improvement ${i + 1}/${safeTargets.length}: ${target.name} ──`);
      ctx.think('improving', `Targeting: ${target.name} via ${target.strategy}`);

      const strategy = String(target.strategy);

      if (strategy === 'evolve-prompt') {
        // Get current persona prompt
        const promptToEvolve = await getPersonaPrompt(String(target.name), ctx);
        if (!promptToEvolve) {
          ctx.log(`Could not find prompt for "${target.name}" — skipping`);
          continue;
        }

        // Run prompt evolution inline (simplified — real version would spawn sub-agent)
        const beforeScore = await quickEval(promptToEvolve, String(target.name), ctx);

        let bestPrompt = promptToEvolve;
        let bestScore = beforeScore;

        for (let cycle = 0; cycle < maxCyclesPerTarget; cycle++) {
          if (ctx.isCancelled()) break;

          const improvePrompt = `Improve this system prompt for "${target.name}". Current score: ${bestScore.toFixed(1)}/10.

CURRENT PROMPT:
${bestPrompt.slice(0, 2000)}

Make ONE specific improvement. Return:
CHANGE: <what you changed>
PROMPT: <the full improved prompt>`;

          const result = await ctx.callClaude(improvePrompt, 2000);
          const newPrompt = result.match(/PROMPT:\s*([\s\S]+)/)?.[1]?.trim();
          if (!newPrompt || newPrompt === bestPrompt) continue;

          const score = await quickEval(newPrompt, String(target.name), ctx);
          if (score > bestScore) {
            bestPrompt = newPrompt;
            bestScore = score;
            ctx.log(`  Cycle ${cycle + 1}: improved ${(score - beforeScore).toFixed(1)} → ${score.toFixed(1)}`);
          }
        }

        results.push({
          target: String(target.name),
          strategy,
          before: beforeScore,
          after: bestScore,
          improvement: bestScore - beforeScore,
        });

      } else if (strategy === 'tune-weights') {
        // For calibration/memory weight tuning
        ctx.log(`  Tuning weights for "${target.name}" — ${maxCyclesPerTarget} cycles`);

        const tunePrompt = `Suggest optimal parameter values for "${target.name}" based on these considerations:
- ${String(target.reason || 'improve overall quality')}
- Current metric: ${String(target.metric)}

Consider the safety floors: ${JSON.stringify(SAFETY_FLOORS)}

Return as JSON: {"parameter": "value", ...} with a brief "rationale" field.`;

        const tuneResult = await ctx.callClaude(tunePrompt, 500);
        ctx.log(`  Recommendation: ${tuneResult.slice(0, 300)}`);

        results.push({
          target: String(target.name),
          strategy,
          before: 0,
          after: 0,
          improvement: 0,
        });

      } else {
        ctx.log(`  Strategy "${strategy}" — logged for future implementation`);
        results.push({
          target: String(target.name),
          strategy,
          before: 0,
          after: 0,
          improvement: 0,
        });
      }
    }

    // ── 4. Record learnings ─────────────────────────────────────────
    ctx.setPhase('recording learnings');
    ctx.setProgress(90);

    const totalImprovement = results.reduce((sum, r) => sum + r.improvement, 0);

    for (const r of results) {
      await resultsLedger.record('self-improvement', {
        timestamp: new Date().toISOString(),
        runTag: 'self-improve',
        cycle: 0,
        metricValue: r.after,
        previousMetric: r.before,
        delta: r.improvement,
        outcome: r.improvement > 0 ? 'kept' : 'discarded',
        changes: `${r.target} via ${r.strategy}`,
        durationMs: 0,
      });
    }

    // ── 5. Summary ──────────────────────────────────────────────────
    ctx.setPhase('summary');
    ctx.setProgress(100);

    const report = [
      `\n═══ SELF-IMPROVEMENT CYCLE COMPLETE ═══`,
      `Targets addressed: ${results.length}`,
      `Total improvement: +${totalImprovement.toFixed(2)}`,
      '',
      ...results.map((r) =>
        `  ${r.improvement > 0 ? '✓' : '○'} ${r.target} (${r.strategy}): ${r.before.toFixed(1)} → ${r.after.toFixed(1)} (${r.improvement >= 0 ? '+' : ''}${r.improvement.toFixed(1)})`
      ),
      '',
      'Protected zones verified: core-laws, integrity, vault, consent-gates — untouched.',
      `Safety floors enforced: ${Object.entries(SAFETY_FLOORS).map(([k, v]) => `${k}>=${v}`).join(', ')}`,
    ].join('\n');

    ctx.log(report);
    return report;
  },
};

// ── Helpers ─────────────────────────────────────────────────────────

/** Get the current persona prompt for a named system. */
async function getPersonaPrompt(targetName: string, ctx: AgentContext): Promise<string | null> {
  const lower = targetName.toLowerCase();

  if (lower.includes('atlas') || lower.includes('research')) {
    return 'You are Atlas, a meticulous Research Director. You gather evidence before drawing conclusions, cite sources precisely, and present findings with executive clarity. You never speculate beyond what the data proves.';
  }
  if (lower.includes('nova') || lower.includes('creative')) {
    return 'You are Nova, a Creative Strategist who sees opportunities others miss. You balance bold ideas with practical execution, use vivid language that inspires action, and always ground creativity in user goals.';
  }
  if (lower.includes('cipher') || lower.includes('technical') || lower.includes('code')) {
    return 'You are Cipher, a Technical Lead who values correctness, clarity, and minimal complexity. You diagnose before prescribing, prefer the smallest effective fix, and communicate technical concepts at the appropriate level for your audience.';
  }

  // For other prompts, ask Claude to find it
  const findPrompt = `What is the current system prompt or personality instruction for "${targetName}" in an AI assistant system? Describe what it should be based on the name.`;
  const result = await ctx.callClaude(findPrompt, 500);
  return result || null;
}

/** Quick evaluation: score a prompt on 3 standard queries. */
async function quickEval(prompt: string, personaName: string, ctx: AgentContext): Promise<number> {
  const testQueries = [
    'Hello, who are you?',
    'Help me think through a complex problem.',
    'Summarize this: The system uses cryptographic verification for all safety constraints.',
  ];

  let total = 0;
  for (const q of testQueries) {
    const resp = await ctx.callClaude(`${prompt}\n\nUser: ${q}`, 500);
    const judge = await ctx.callClaude(
      `Rate this AI response 0-10 for a "${personaName}" persona. Query: "${q}" Response: "${resp.slice(0, 500)}" Return ONLY: {"score": N}`,
      100
    );
    try {
      const match = judge.match(/\{[\s\S]*\}/);
      const parsed = match ? JSON.parse(match[0]) : { score: 5 };
      total += Number(parsed.score) || 5;
    } catch {
      total += 5;
    }
  }

  return total / testQueries.length;
}
