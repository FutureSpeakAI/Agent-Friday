/**
 * model-breeder.ts — Breeds specialized Ollama models for custom tasks.
 *
 * Uses autoresearch-style iteration to evolve Ollama Modelfiles:
 *   1. Generate a Modelfile (system prompt + parameters for a specific task)
 *   2. Create the model via Ollama API
 *   3. Evaluate it on a benchmark suite
 *   4. Mutate the Modelfile to improve performance
 *   5. Repeat — best-performing model survives
 *
 * This enables Agent Friday to spawn specialized subagent models that are
 * optimized for specific tasks (code review, research synthesis, etc.)
 * without any cloud dependency.
 */

import { AgentDefinition, AgentContext } from './agent-types';
import { resultsLedger } from './results-ledger';

// ── Types ───────────────────────────────────────────────────────────

export interface ModelfileSpec {
  /** Base model to build from (e.g., 'llama3.2', 'mistral') */
  baseModel: string;
  /** System prompt for the specialized model */
  systemPrompt: string;
  /** Temperature (0.0-2.0) */
  temperature: number;
  /** Top-K sampling */
  topK: number;
  /** Top-P (nucleus sampling) */
  topP: number;
  /** Context window size */
  contextLength: number;
  /** Repeat penalty */
  repeatPenalty: number;
  /** Stop sequences */
  stop: string[];
}

export interface BreedingResult {
  /** Model name in Ollama */
  modelName: string;
  /** The Modelfile that created it */
  modelfile: ModelfileSpec;
  /** Average benchmark score */
  score: number;
  /** Generation number */
  generation: number;
}

// ── Modelfile Generation ────────────────────────────────────────────

function generateModelfile(spec: ModelfileSpec): string {
  const lines: string[] = [
    `FROM ${spec.baseModel}`,
    '',
    `PARAMETER temperature ${spec.temperature}`,
    `PARAMETER top_k ${spec.topK}`,
    `PARAMETER top_p ${spec.topP}`,
    `PARAMETER num_ctx ${spec.contextLength}`,
    `PARAMETER repeat_penalty ${spec.repeatPenalty}`,
  ];

  for (const stop of spec.stop) {
    lines.push(`PARAMETER stop "${stop}"`);
  }

  lines.push('');
  lines.push(`SYSTEM """${spec.systemPrompt}"""`);

  return lines.join('\n');
}

function defaultSpec(baseModel: string, taskDescription: string): ModelfileSpec {
  return {
    baseModel,
    systemPrompt: `You are a specialized AI assistant optimized for: ${taskDescription}. Be precise, concise, and directly helpful.`,
    temperature: 0.7,
    topK: 40,
    topP: 0.9,
    contextLength: 4096,
    repeatPenalty: 1.1,
    stop: ['User:', 'Human:'],
  };
}

// ── The Model Breeder Agent ─────────────────────────────────────────

export const modelBreederAgent: AgentDefinition = {
  name: 'breed-model',
  description:
    'Breed a specialized Ollama model for a specific task. Iteratively evolves ' +
    'a Modelfile (system prompt + parameters), creates models, benchmarks them, ' +
    'and keeps the best performer.',

  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    // ── 1. Parse inputs ─────────────────────────────────────────────
    ctx.setPhase('parsing inputs');
    ctx.setProgress(2);

    const taskDescription = String(input.task || input.description || '');
    if (!taskDescription) throw new Error('breed-model requires a `task` description');

    const baseModel = String(input.baseModel || 'llama3.2');
    const modelPrefix = String(input.modelName || 'friday-bred');
    const maxGenerations = Number(input.maxGenerations || 8);

    // Parse benchmark queries
    let benchmarks: Array<{ input: string; criteria: string }>;
    if (Array.isArray(input.benchmarks)) {
      benchmarks = (input.benchmarks as Array<Record<string, unknown>>).map((b) => ({
        input: String(b.input || b.query || ''),
        criteria: String(b.criteria || 'accurate and helpful'),
      }));
    } else {
      // Generate benchmarks from task description
      ctx.think('planning', 'Generating benchmark suite for task evaluation');
      const benchGenPrompt = `Generate 5 benchmark test cases for a model specialized in: "${taskDescription}"

Each test should exercise a different aspect of the task.
Return ONLY a JSON array: [{"input": "test query", "criteria": "what good output looks like"}]`;

      const benchRaw = await ctx.callClaude(benchGenPrompt, 1000);
      try {
        const match = benchRaw.match(/\[[\s\S]*\]/);
        benchmarks = match ? JSON.parse(match[0]) : [];
      } catch {
        benchmarks = [
          { input: `Help me with: ${taskDescription}`, criteria: 'Task-specific, accurate, clear' },
          { input: 'What are you specialized in?', criteria: 'Should correctly identify specialization' },
          { input: 'Give me a detailed analysis', criteria: 'Should show depth of specialized knowledge' },
        ];
      }
    }

    ctx.log(`Breeding model for: "${taskDescription}"`);
    ctx.log(`Base model: ${baseModel}, prefix: ${modelPrefix}, max generations: ${maxGenerations}`);
    ctx.log(`Benchmark suite: ${benchmarks.length} test cases`);

    // ── 2. Check Ollama availability ────────────────────────────────
    ctx.setPhase('checking Ollama');
    ctx.setProgress(5);

    try {
      const healthResp = await fetch('http://localhost:11434/api/tags');
      if (!healthResp.ok) throw new Error('Ollama not responding');
      ctx.log('Ollama is running');
    } catch {
      return 'ERROR: Ollama is not running. Start Ollama first (`ollama serve`).';
    }

    // ── 3. Create and evaluate baseline ─────────────────────────────
    ctx.setPhase('baseline model');
    ctx.setProgress(10);

    let currentSpec = defaultSpec(baseModel, taskDescription);

    // Design initial prompt via Claude
    ctx.think('designing', 'Designing initial specialized system prompt');
    const designPrompt = `Design an expert system prompt for a local LLM (${baseModel}) that specializes in: "${taskDescription}"

The prompt should:
1. Define the model's identity and expertise clearly
2. Set behavioral guidelines specific to this task
3. Include 2-3 few-shot style instructions (not full examples, just patterns)
4. Be concise (under 500 words) — local models have limited context

Return ONLY the system prompt text, no explanation.`;

    const designedPrompt = await ctx.callClaude(designPrompt, 1500);
    currentSpec.systemPrompt = designedPrompt.trim();

    // Create the baseline model
    const baselineName = `${modelPrefix}-gen0`;
    await createOllamaModel(baselineName, currentSpec, ctx);
    const baselineScore = await benchmarkModel(baselineName, benchmarks, ctx);

    ctx.log(`Baseline model "${baselineName}" score: ${baselineScore.toFixed(2)}/10`);

    let bestSpec = { ...currentSpec };
    let bestScore = baselineScore;
    let bestModelName = baselineName;
    const history: BreedingResult[] = [{
      modelName: baselineName,
      modelfile: { ...currentSpec },
      score: baselineScore,
      generation: 0,
    }];

    // ── 4. Evolution loop ───────────────────────────────────────────
    for (let gen = 1; gen <= maxGenerations; gen++) {
      if (ctx.isCancelled()) break;

      ctx.setPhase(`generation ${gen}/${maxGenerations}`);
      ctx.setProgress(15 + (gen / maxGenerations) * 75);

      // Plan mutation
      const recentHistory = history.slice(-5).map((h) =>
        `Gen ${h.generation}: score=${h.score.toFixed(1)}, temp=${h.modelfile.temperature}, topK=${h.modelfile.topK}`
      ).join('\n');

      const mutatePrompt = `You are evolving an Ollama Modelfile for a model specialized in: "${taskDescription}"

CURRENT BEST CONFIG (score: ${bestScore.toFixed(2)}/10):
- System prompt: ${bestSpec.systemPrompt.slice(0, 500)}...
- Temperature: ${bestSpec.temperature}
- Top-K: ${bestSpec.topK}
- Top-P: ${bestSpec.topP}
- Context length: ${bestSpec.contextLength}
- Repeat penalty: ${bestSpec.repeatPenalty}

RECENT HISTORY:
${recentHistory}

BENCHMARK RESULTS: The model is being tested on ${benchmarks.length} queries about "${taskDescription}".

Suggest ONE mutation to improve performance. You can change:
- The system prompt (most impactful)
- Temperature (lower = more focused, higher = more creative)
- Top-K / Top-P (sampling parameters)
- Context length
- Repeat penalty

Return as JSON: {"mutation": "description", "systemPrompt": "full new prompt or null to keep", "temperature": N or null, "topK": N or null, "topP": N or null, "contextLength": N or null, "repeatPenalty": N or null}`;

      const mutRaw = await ctx.callClaude(mutatePrompt, 2000);
      let mutation: Record<string, unknown>;
      try {
        const match = mutRaw.match(/\{[\s\S]*\}/);
        mutation = match ? JSON.parse(match[0]) : {};
      } catch {
        ctx.log(`Gen ${gen}: failed to parse mutation — skipping`);
        continue;
      }

      // Apply mutation
      const newSpec: ModelfileSpec = {
        ...bestSpec,
        systemPrompt: (mutation.systemPrompt as string) || bestSpec.systemPrompt,
        temperature: (mutation.temperature as number) ?? bestSpec.temperature,
        topK: (mutation.topK as number) ?? bestSpec.topK,
        topP: (mutation.topP as number) ?? bestSpec.topP,
        contextLength: (mutation.contextLength as number) ?? bestSpec.contextLength,
        repeatPenalty: (mutation.repeatPenalty as number) ?? bestSpec.repeatPenalty,
      };

      const mutDesc = String(mutation.mutation || 'parameter adjustment');
      const genName = `${modelPrefix}-gen${gen}`;

      // Create and benchmark
      ctx.think('breeding', `Gen ${gen}: creating "${genName}" — ${mutDesc}`);
      await createOllamaModel(genName, newSpec, ctx);
      const score = await benchmarkModel(genName, benchmarks, ctx);

      const delta = score - bestScore;
      const improved = delta > 0;

      await resultsLedger.record('model-breeding', {
        timestamp: new Date().toISOString(),
        runTag: `breed/${modelPrefix}`,
        cycle: gen,
        metricValue: score,
        previousMetric: bestScore,
        delta,
        outcome: improved ? 'kept' : 'discarded',
        changes: mutDesc,
        durationMs: 0,
      });

      history.push({
        modelName: genName,
        modelfile: { ...newSpec },
        score,
        generation: gen,
      });

      if (improved) {
        bestSpec = { ...newSpec };
        bestScore = score;
        bestModelName = genName;
        ctx.log(`Gen ${gen}: KEPT "${mutDesc}" — score ${score.toFixed(2)} (+${delta.toFixed(2)})`);
      } else {
        // Clean up failed model
        await deleteOllamaModel(genName);
        ctx.log(`Gen ${gen}: discarded "${mutDesc}" — score ${score.toFixed(2)} (${delta.toFixed(2)})`);
      }
    }

    // ── 5. Finalize best model ──────────────────────────────────────
    ctx.setPhase('finalizing');
    ctx.setProgress(95);

    // Rename best to final name
    const finalName = `${modelPrefix}-final`;
    await createOllamaModel(finalName, bestSpec, ctx);

    const improvements = history.filter((h, i) => i > 0 && h.score > history[i - 1].score).length;
    const report = [
      `\n═══ MODEL BREEDING COMPLETE ═══`,
      `Task: ${taskDescription}`,
      `Base model: ${baseModel}`,
      `Final model: ${finalName} (also available as ${bestModelName})`,
      `Generations: ${history.length - 1}`,
      `Improvements: ${improvements}`,
      `Baseline score: ${history[0].score.toFixed(2)}/10`,
      `Final score: ${bestScore.toFixed(2)}/10`,
      `Total improvement: +${(bestScore - history[0].score).toFixed(2)}`,
      `\n── Best Modelfile ──`,
      generateModelfile(bestSpec),
      `\n── Use it ──`,
      `ollama run ${finalName}`,
      `Or set it as a provider in Friday's settings.`,
    ].join('\n');

    ctx.log(report);
    ctx.setProgress(100);
    return report;
  },
};

// ── Ollama API Helpers ──────────────────────────────────────────────

async function createOllamaModel(
  name: string,
  spec: ModelfileSpec,
  ctx: AgentContext
): Promise<void> {
  const modelfileContent = generateModelfile(spec);
  ctx.log(`Creating model "${name}"...`);

  try {
    const resp = await fetch('http://localhost:11434/api/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, modelfile: modelfileContent }),
    });

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Ollama create failed: ${text.slice(0, 200)}`);
    }

    // Stream response until done
    const reader = resp.body?.getReader();
    if (reader) {
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        // Parse NDJSON lines
        for (const line of chunk.split('\n').filter(Boolean)) {
          try {
            const parsed = JSON.parse(line);
            if (parsed.status) {
              ctx.log(`  [create] ${parsed.status}`);
            }
          } catch {
            // skip malformed lines
          }
        }
      }
    }

    ctx.log(`Model "${name}" created successfully`);
  } catch (err) {
    ctx.log(`Failed to create model "${name}": ${err instanceof Error ? err.message : err}`);
    throw err;
  }
}

async function deleteOllamaModel(name: string): Promise<void> {
  try {
    await fetch('http://localhost:11434/api/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
  } catch {
    // Non-critical — cleanup failure is fine
  }
}

async function queryOllamaModel(
  modelName: string,
  prompt: string,
  timeoutMs = 30_000
): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const resp = await fetch('http://localhost:11434/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: modelName,
        prompt,
        stream: false,
      }),
      signal: controller.signal,
    });

    clearTimeout(timer);
    if (!resp.ok) return '';

    const data = await resp.json();
    return data.response || '';
  } catch {
    clearTimeout(timer);
    return '';
  }
}

async function benchmarkModel(
  modelName: string,
  benchmarks: Array<{ input: string; criteria: string }>,
  ctx: AgentContext
): Promise<number> {
  const scores: number[] = [];

  for (const bench of benchmarks) {
    if (ctx.isCancelled()) break;

    const response = await queryOllamaModel(modelName, bench.input);
    if (!response) {
      scores.push(0);
      continue;
    }

    // Judge with Claude (cloud judge evaluating local model output)
    const judgePrompt = `Rate this AI response 0-10.
Query: ${bench.input}
Criteria: ${bench.criteria}
Response: ${response.slice(0, 1500)}

Return ONLY a JSON object: {"score": N, "reason": "brief"}`;

    const judgeRaw = await ctx.callClaude(judgePrompt, 200);
    try {
      const match = judgeRaw.match(/\{[\s\S]*\}/);
      if (match) {
        const parsed = JSON.parse(match[0]);
        scores.push(Number(parsed.score) || 5);
      } else {
        scores.push(5);
      }
    } catch {
      scores.push(5);
    }
  }

  if (scores.length === 0) return 0;
  return scores.reduce((a, b) => a + b, 0) / scores.length;
}
