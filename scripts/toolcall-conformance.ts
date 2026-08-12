#!/usr/bin/env tsx
/**
 * toolcall-conformance.ts — FR-1: orchestrator seat conformance gate.
 *
 * Usage:
 *   npm run conformance:check -- <model> [<model2> ...]
 *   npm run conformance:check -- gemma3:4b qwen3:8b
 *
 * Seats each candidate model with the real production tool registry
 * (desktop-tools.ts's DESKTOP_TOOL_DECLARATIONS — what's actually wired
 * into the local voice conversation loop, see V1 findings in
 * dev/friday-orchestrator-integrity-spec.md) and 10 canned prompts, each
 * requiring exactly one tool call.
 *
 * Pass = 10/10 prompts produce a structured tool call AND zero registry
 * tool names leak into the assistant's prose. A red model cannot hold the
 * orchestrator seat — see FR-1. Exits 1 if any candidate model fails, so
 * this can gate CI or a pre-commit hook if the model is pinned in config.
 */

import { DESKTOP_TOOL_DECLARATIONS } from '../src/main/desktop-tools';
import { runConformance, formatReport, type ConformanceTool } from '../src/main/toolcall-conformance-core';

const DEFAULT_ENDPOINT = 'http://localhost:11434';

async function main(): Promise<void> {
  const endpoint = process.env.OLLAMA_ENDPOINT || DEFAULT_ENDPOINT;
  const models = process.argv.slice(2);

  if (models.length === 0) {
    console.error('Usage: npm run conformance:check -- <model> [<model2> ...]');
    process.exit(2);
  }

  const tools: ConformanceTool[] = DESKTOP_TOOL_DECLARATIONS.map((t) => ({
    name: t.name,
    description: t.description,
    parameters: t.parameters,
  }));

  console.log(`Conformance gate — ${tools.length} tools from the production registry, ${models.length} candidate model(s).\n`);

  let anyFailed = false;
  for (const model of models) {
    console.log(`── Running ${model} ──────────────────────────────`);
    const report = await runConformance({ endpoint, model, tools });
    console.log(formatReport(report));
    if (!report.pass) anyFailed = true;
  }

  process.exit(anyFailed ? 1 : 0);
}

main().catch((err) => {
  console.error('Conformance script crashed:', err instanceof Error ? err.stack : err);
  process.exit(1);
});
