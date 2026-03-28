/**
 * iteration-engine.ts — Autoresearch-style autonomous iteration loop for Agent Friday.
 *
 * Inspired by Karpathy's autoresearch: agents iterate in a tight loop —
 * modify code, execute, measure a metric, keep improvements, discard regressions.
 * Runs until interrupted or budget exhausted.
 *
 * The engine reads a program.md-style directive that defines:
 *   - What to optimize (objective + metric)
 *   - What files can be modified (editable surface)
 *   - How to iterate (loop steps)
 *   - When to stop (budget + circuit breakers)
 *
 * Plugs directly into AgentRunner as a builtin agent definition.
 */

import { AgentDefinition, AgentContext } from './agent-types';
import { loadDirective, parseDirective, type Directive } from './directive-loader';
import { resultsLedger, type LedgerEntry } from './results-ledger';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

// ── Types ───────────────────────────────────────────────────────────

export interface IterationState {
  directive: Directive;
  cycle: number;
  bestMetric: number;
  currentMetric: number;
  startTime: number;
  status: 'running' | 'completed' | 'halted' | 'cancelled';
  haltReason?: string;
}

// ── Metric Evaluation ───────────────────────────────────────────────

/**
 * Execute a metric command and extract a numeric value from stdout.
 * Returns NaN if the command fails or produces no parseable number.
 */
async function evaluateMetric(
  command: string,
  timeoutMs: number,
  ctx: AgentContext
): Promise<{ value: number; rawOutput: string }> {
  if (!command) {
    return { value: NaN, rawOutput: 'No metric command defined' };
  }

  try {
    ctx.think('measuring', `Running metric: ${command}`);
    const { stdout, stderr } = await execAsync(command, {
      timeout: timeoutMs,
      maxBuffer: 1024 * 1024,
      cwd: process.cwd(),
    });

    const output = stdout + (stderr || '');

    // Extract the last number in the output (most metrics print results at the end)
    const numbers = output.match(/[-+]?\d*\.?\d+/g);
    if (numbers && numbers.length > 0) {
      const value = parseFloat(numbers[numbers.length - 1]);
      return { value, rawOutput: output.slice(-500) };
    }

    return { value: NaN, rawOutput: output.slice(-500) };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { value: NaN, rawOutput: `Metric command failed: ${msg}` };
  }
}

/**
 * Execute a shell command and return stdout.
 */
async function runCommand(
  command: string,
  timeoutMs: number
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  try {
    const { stdout, stderr } = await execAsync(command, {
      timeout: timeoutMs,
      maxBuffer: 2 * 1024 * 1024,
      cwd: process.cwd(),
    });
    return { stdout, stderr, exitCode: 0 };
  } catch (err: any) {
    return {
      stdout: err.stdout || '',
      stderr: err.stderr || err.message || '',
      exitCode: err.code || 1,
    };
  }
}

// ── Circuit Breaker Checks ──────────────────────────────────────────

/**
 * Check if any circuit breaker conditions are met.
 * Like autoresearch's `if math.isnan(train_loss) or train_loss > 100: exit(1)`
 */
function checkCircuitBreakers(
  directive: Directive,
  metricValue: number,
  rawOutput: string,
  _ctx: AgentContext
): string | null {
  // Built-in breakers
  if (isNaN(metricValue) && directive.metricCommand) {
    return 'Metric returned NaN — possible catastrophic failure';
  }

  // Check custom breakers from directive
  for (const breaker of directive.circuitBreakers) {
    const lower = breaker.toLowerCase();

    // "metric > N" or "metric < N" patterns
    const gtMatch = lower.match(/metric\s*>\s*([\d.]+)/);
    if (gtMatch && metricValue > parseFloat(gtMatch[1])) {
      return `Circuit breaker: metric ${metricValue} > ${gtMatch[1]}`;
    }

    const ltMatch = lower.match(/metric\s*<\s*([\d.]+)/);
    if (ltMatch && metricValue < parseFloat(ltMatch[1])) {
      return `Circuit breaker: metric ${metricValue} < ${ltMatch[1]}`;
    }

    // "build fails" or "tests fail" — check raw output
    if ((lower.includes('build fail') || lower.includes('test fail')) &&
        /error|fail|FAIL/i.test(rawOutput)) {
      return `Circuit breaker: ${breaker}`;
    }
  }

  return null;
}

// ── The Iteration Engine Agent ──────────────────────────────────────

export const iterationAgent: AgentDefinition = {
  name: 'iterate',
  description:
    'Autoresearch-style autonomous iteration loop. Reads a program.md directive, ' +
    'then loops: modify → execute → measure → keep/discard. Runs until interrupted or budget exhausted.',

  execute: async (input: Record<string, unknown>, ctx: AgentContext): Promise<string> => {
    // ── 1. Load directive ───────────────────────────────────────────
    ctx.setPhase('loading directive');
    ctx.setProgress(2);

    let directive: Directive;

    if (typeof input.directivePath === 'string') {
      ctx.think('loading', `Loading directive from: ${input.directivePath}`);
      directive = await loadDirective(input.directivePath);
    } else if (typeof input.directive === 'string') {
      ctx.think('loading', 'Parsing inline directive');
      directive = parseDirective(input.directive, 'inline');
    } else {
      throw new Error(
        'Iteration engine requires either `directivePath` (path to .md file) or `directive` (raw markdown)'
      );
    }

    ctx.log(`Directive loaded: "${directive.title}"`);
    ctx.log(`Objective: ${directive.objective}`);
    ctx.log(`Editable surface: ${directive.editableSurface.join(', ') || 'unrestricted'}`);
    ctx.log(`Metric: ${directive.metricDescription}`);
    ctx.log(`Budget: ${directive.timeBudgetSeconds}s/cycle, ${directive.maxCycles || '∞'} cycles`);
    ctx.log(`Constraints: ${directive.constraints.length} rules`);
    ctx.think('planning', `Loaded directive "${directive.title}" — entering iteration loop`);

    // ── 2. Baseline measurement ─────────────────────────────────────
    ctx.setPhase('baseline measurement');
    ctx.setProgress(5);

    const baselineTimeout = directive.timeBudgetSeconds * 1000;
    const { value: baselineMetric, rawOutput: baselineOutput } = await evaluateMetric(
      directive.metricCommand,
      baselineTimeout,
      ctx
    );

    ctx.log(`Baseline metric: ${isNaN(baselineMetric) ? 'N/A' : baselineMetric.toFixed(4)}`);
    ctx.think('baseline', `Baseline: ${isNaN(baselineMetric) ? 'no metric' : baselineMetric.toFixed(4)}\n${baselineOutput.slice(0, 200)}`);

    // ── 3. Git setup ────────────────────────────────────────────────
    ctx.setPhase('git setup');
    const runTag = `autoresearch/${directive.title.replace(/\s+/g, '-').toLowerCase()}/${new Date().toISOString().slice(0, 10)}`;

    // Create a branch for this run (don't fail if branch exists)
    await runCommand(`git checkout -b "${runTag}" 2>/dev/null || git checkout "${runTag}" 2>/dev/null || true`, 10_000);
    ctx.log(`Git branch: ${runTag}`);

    // ── 4. Iteration loop ───────────────────────────────────────────
    const state: IterationState = {
      directive,
      cycle: 0,
      bestMetric: baselineMetric,
      currentMetric: baselineMetric,
      startTime: Date.now(),
      status: 'running',
    };

    const sessionStartTime = Date.now();

    while (state.status === 'running') {
      // Check cancellation
      if (ctx.isCancelled()) {
        state.status = 'cancelled';
        ctx.log('Iteration cancelled by user');
        break;
      }

      // Check cycle budget
      state.cycle++;
      if (directive.maxCycles > 0 && state.cycle > directive.maxCycles) {
        state.status = 'completed';
        ctx.log(`Budget exhausted: ${directive.maxCycles} cycles completed`);
        break;
      }

      const cycleStartTime = Date.now();
      ctx.setPhase(`cycle ${state.cycle}`);
      ctx.setProgress(Math.min(90, 10 + (state.cycle / Math.max(directive.maxCycles, 20)) * 80));
      ctx.log(`\n── Cycle ${state.cycle} ──────────────────────────`);

      // ── 4a. Plan modification ───────────────────────────────────
      ctx.think('planning', `Cycle ${state.cycle}: Planning next modification`);

      const previousResults = resultsLedger.getEntries(directive.title).slice(-5);
      const recentHistory = previousResults.length > 0
        ? `\nRecent results:\n${previousResults.map((e) => `  Cycle ${e.cycle}: ${e.outcome} (metric: ${e.metricValue.toFixed(4)}, delta: ${e.delta.toFixed(4)}) — ${e.changes}`).join('\n')}`
        : '';

      const planPrompt = `You are an autonomous research agent running iteration cycle ${state.cycle}.

DIRECTIVE:
${directive.raw}

CURRENT STATE:
- Current best metric: ${isNaN(state.bestMetric) ? 'not yet measured' : state.bestMetric.toFixed(6)}
- Baseline metric: ${isNaN(baselineMetric) ? 'not yet measured' : baselineMetric.toFixed(6)}
- Cycles completed: ${state.cycle - 1}
${recentHistory}

EDITABLE FILES: ${directive.editableSurface.join(', ') || 'any project files'}

CONSTRAINTS:
${directive.constraints.map((c) => `- ${c}`).join('\n') || '- None specified'}

Based on the directive objective and what you've learned from previous cycles, describe your NEXT modification in detail:
1. What specific change will you make?
2. What file(s) will you modify?
3. What improvement do you expect?
4. What shell commands should be run to implement this change?

Be specific. Output your plan as:
CHANGE: <brief description>
COMMANDS: <shell commands to implement the change, one per line>
EXPECTED: <what you expect to happen>`;

      const plan = await ctx.callClaude(planPrompt, 1500);
      ctx.think('planning', `Plan:\n${plan.slice(0, 300)}`);

      // Extract change description and commands from plan
      const changeMatch = plan.match(/CHANGE:\s*(.+?)(?=\n(?:COMMANDS|EXPECTED|$))/s);
      const commandsMatch = plan.match(/COMMANDS:\s*(.+?)(?=\n(?:EXPECTED|$))/s);
      const changeDescription = changeMatch?.[1]?.trim() || 'Agent-planned modification';
      const commands = commandsMatch?.[1]?.trim().split('\n').filter((l) => l.trim()) || [];

      ctx.log(`Plan: ${changeDescription}`);

      // ── 4b. Execute modification ────────────────────────────────
      ctx.setPhase(`cycle ${state.cycle} — executing`);
      ctx.think('executing', `Executing ${commands.length} commands`);

      let executionError: string | null = null;
      for (const cmd of commands) {
        if (ctx.isCancelled()) break;
        const trimmed = cmd.replace(/^[-*]\s+/, '').replace(/^`|`$/g, '').trim();
        if (!trimmed || trimmed.startsWith('#')) continue;

        ctx.log(`  $ ${trimmed}`);
        const result = await runCommand(trimmed, directive.timeBudgetSeconds * 1000);
        if (result.exitCode !== 0) {
          executionError = `Command failed (exit ${result.exitCode}): ${result.stderr.slice(0, 200)}`;
          ctx.log(`  ✗ ${executionError}`);
          break;
        }
      }

      // ── 4c. Measure metric ──────────────────────────────────────
      ctx.setPhase(`cycle ${state.cycle} — measuring`);

      let cycleMetric = NaN;
      let rawOutput = '';

      if (!executionError) {
        const measurement = await evaluateMetric(
          directive.metricCommand,
          directive.timeBudgetSeconds * 1000,
          ctx
        );
        cycleMetric = measurement.value;
        rawOutput = measurement.rawOutput;
        ctx.log(`Metric: ${isNaN(cycleMetric) ? 'NaN' : cycleMetric.toFixed(4)}`);
      }

      // ── 4d. Circuit breaker check ───────────────────────────────
      const breaker = checkCircuitBreakers(directive, cycleMetric, rawOutput, ctx);
      if (breaker) {
        state.status = 'halted';
        state.haltReason = breaker;
        ctx.log(`CIRCUIT BREAKER: ${breaker}`);
        ctx.think('halted', `Circuit breaker triggered: ${breaker}`);

        // Revert and record
        await runCommand('git checkout -- .', 10_000);

        await resultsLedger.record(directive.title, {
          timestamp: new Date().toISOString(),
          runTag,
          cycle: state.cycle,
          metricValue: cycleMetric,
          previousMetric: state.currentMetric,
          delta: isNaN(cycleMetric) ? NaN : cycleMetric - state.currentMetric,
          outcome: 'halted',
          changes: changeDescription,
          durationMs: Date.now() - cycleStartTime,
          error: breaker,
        });
        break;
      }

      // ── 4e. Keep or discard ─────────────────────────────────────
      ctx.setPhase(`cycle ${state.cycle} — evaluating`);

      const previousMetric = state.currentMetric;
      const delta = isNaN(cycleMetric) || isNaN(previousMetric) ? NaN : cycleMetric - previousMetric;
      const improved = !isNaN(delta) && (
        (directive.lowerIsBetter && delta < 0) ||
        (!directive.lowerIsBetter && delta > 0)
      );

      let outcome: LedgerEntry['outcome'];
      let commitHash: string | undefined;

      if (executionError) {
        // Execution failed — revert
        outcome = 'error';
        await runCommand('git checkout -- .', 10_000);
        ctx.log(`DISCARDED (error) — reverting`);
        ctx.think('discarding', `Cycle ${state.cycle}: error — ${executionError}`);
      } else if (improved) {
        // Improvement — commit
        outcome = 'kept';
        state.bestMetric = cycleMetric;
        state.currentMetric = cycleMetric;

        const commitResult = await runCommand(
          `git add -A && git commit -m "autoresearch cycle ${state.cycle}: ${changeDescription.slice(0, 60)}\n\nmetric: ${cycleMetric.toFixed(6)} (delta: ${delta.toFixed(6)})"`,
          10_000
        );
        const hashMatch = commitResult.stdout.match(/\[.+ ([a-f0-9]+)\]/);
        commitHash = hashMatch?.[1];

        ctx.log(`KEPT — metric improved by ${Math.abs(delta).toFixed(4)} (commit: ${commitHash || 'unknown'})`);
        ctx.think('keeping', `Cycle ${state.cycle}: improved! ${previousMetric.toFixed(4)} → ${cycleMetric.toFixed(4)} (Δ${delta.toFixed(4)})`);
      } else {
        // No improvement — revert
        outcome = 'discarded';
        await runCommand('git checkout -- .', 10_000);

        if (isNaN(delta)) {
          ctx.log(`DISCARDED — could not measure improvement`);
        } else {
          ctx.log(`DISCARDED — metric ${directive.lowerIsBetter ? 'increased' : 'decreased'} by ${Math.abs(delta).toFixed(4)}`);
        }
        ctx.think('discarding', `Cycle ${state.cycle}: no improvement (${isNaN(delta) ? 'NaN' : delta.toFixed(4)})`);
      }

      // ── 4f. Record to ledger ────────────────────────────────────
      await resultsLedger.record(directive.title, {
        timestamp: new Date().toISOString(),
        runTag,
        cycle: state.cycle,
        metricValue: cycleMetric,
        previousMetric,
        delta: isNaN(delta) ? NaN : delta,
        outcome,
        changes: changeDescription,
        durationMs: Date.now() - cycleStartTime,
        commitHash,
        error: executionError || undefined,
      });
    }

    // ── 5. Summary ──────────────────────────────────────────────────
    ctx.setPhase('summary');
    ctx.setProgress(95);

    const summary = resultsLedger.getSummary(directive.title);
    const totalTime = Date.now() - sessionStartTime;

    const report = [
      `\n═══ AUTORESEARCH SESSION COMPLETE ═══`,
      `Directive: ${directive.title}`,
      `Status: ${state.status}${state.haltReason ? ` (${state.haltReason})` : ''}`,
      `Cycles: ${summary.totalCycles} (${summary.improvements} kept, ${summary.discards} discarded, ${summary.errors} errors)`,
      `Baseline metric: ${isNaN(baselineMetric) ? 'N/A' : baselineMetric.toFixed(4)}`,
      `Best metric: ${isNaN(summary.bestMetric) ? 'N/A' : summary.bestMetric.toFixed(4)}`,
      `Total improvement: ${isNaN(summary.totalImprovement) ? 'N/A' : summary.totalImprovement.toFixed(4)}`,
      `Total time: ${Math.round(totalTime / 1000)}s`,
      `Branch: ${runTag}`,
      `\nLedger:\n${resultsLedger.toTsv(directive.title)}`,
    ].join('\n');

    ctx.log(report);
    ctx.setProgress(100);
    ctx.think('complete', `Session complete: ${summary.improvements}/${summary.totalCycles} improvements, best metric: ${summary.bestMetric}`);

    return report;
  },
};
