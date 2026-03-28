/**
 * directive-loader.ts — Loads and parses program.md-style agent directives.
 *
 * Inspired by Karpathy's autoresearch: agent behavior is defined in human-readable
 * markdown files, not compiled code. Each directive defines an objective, editable
 * surface, iteration loop, constraints, and success metric.
 *
 * Directive format:
 *   ## Objective        — what the agent is trying to achieve
 *   ## Editable Surface — file globs the agent may modify
 *   ## Metric           — how to measure success (shell command → number)
 *   ## Loop             — numbered iteration steps
 *   ## Constraints      — hard rules the agent must never violate
 *   ## Budget           — time/cycle limits
 *   ## Circuit Breaker  — conditions that trigger immediate halt
 */

import fs from 'fs/promises';
import path from 'path';

// ── Types ───────────────────────────────────────────────────────────

export interface Directive {
  /** Raw source file path */
  source: string;
  /** Title from first H1 or filename */
  title: string;
  /** What the agent is trying to achieve */
  objective: string;
  /** File glob patterns the agent may modify */
  editableSurface: string[];
  /** Shell command that outputs the primary metric (lower is better by default) */
  metricCommand: string;
  /** Human-readable description of the metric */
  metricDescription: string;
  /** Whether lower metric values are better (default true, like val_bpb) */
  lowerIsBetter: boolean;
  /** Ordered iteration loop steps */
  loopSteps: string[];
  /** Hard constraints the agent must obey */
  constraints: string[];
  /** Max wall-clock seconds per iteration cycle */
  timeBudgetSeconds: number;
  /** Max number of iteration cycles (0 = unlimited until interrupted) */
  maxCycles: number;
  /** Conditions that trigger immediate halt */
  circuitBreakers: string[];
  /** Full raw markdown for Claude context injection */
  raw: string;
}

const DEFAULT_TIME_BUDGET = 300; // 5 minutes, matching autoresearch
const DEFAULT_MAX_CYCLES = 0;   // Unlimited, matching "NEVER STOP"

// ── Parser ──────────────────────────────────────────────────────────

/**
 * Parse a markdown directive file into a structured Directive object.
 */
export function parseDirective(markdown: string, sourcePath: string): Directive {
  const lines = markdown.split('\n');

  // Extract title from first H1
  const h1 = lines.find((l) => /^#\s+/.test(l));
  const title = h1 ? h1.replace(/^#\s+/, '').trim() : path.basename(sourcePath, '.md');

  // Extract sections by ## headers
  const sections = new Map<string, string>();
  let currentSection = '';
  let currentContent: string[] = [];

  for (const line of lines) {
    const headerMatch = line.match(/^##\s+(.+)/);
    if (headerMatch) {
      if (currentSection) {
        sections.set(currentSection.toLowerCase(), currentContent.join('\n').trim());
      }
      currentSection = headerMatch[1].trim();
      currentContent = [];
    } else if (currentSection) {
      currentContent.push(line);
    }
  }
  // Flush last section
  if (currentSection) {
    sections.set(currentSection.toLowerCase(), currentContent.join('\n').trim());
  }

  // Parse each section
  const objective = sections.get('objective') || sections.get('goal') || '';
  const editableSurface = parseList(sections.get('editable surface') || sections.get('modifiable surface') || '');
  const constraints = parseList(sections.get('constraints') || sections.get('rules') || '');
  const loopSteps = parseList(sections.get('loop') || sections.get('iteration') || sections.get('steps') || '');
  const circuitBreakers = parseList(sections.get('circuit breaker') || sections.get('circuit breakers') || sections.get('halt conditions') || '');

  // Parse metric section
  const metricRaw = sections.get('metric') || sections.get('success metric') || '';
  const { metricCommand, metricDescription, lowerIsBetter } = parseMetric(metricRaw);

  // Parse budget section
  const budgetRaw = sections.get('budget') || sections.get('time budget') || '';
  const { timeBudgetSeconds, maxCycles } = parseBudget(budgetRaw);

  return {
    source: sourcePath,
    title,
    objective,
    editableSurface,
    metricCommand,
    metricDescription,
    lowerIsBetter,
    loopSteps,
    constraints,
    timeBudgetSeconds,
    maxCycles,
    circuitBreakers,
    raw: markdown,
  };
}

/**
 * Load a directive from a markdown file.
 */
export async function loadDirective(filePath: string): Promise<Directive> {
  const absPath = path.resolve(filePath);
  const content = await fs.readFile(absPath, 'utf-8');
  return parseDirective(content, absPath);
}

/**
 * Load all directives from a directory.
 */
export async function loadDirectivesFromDir(dirPath: string): Promise<Directive[]> {
  const absDir = path.resolve(dirPath);
  try {
    const entries = await fs.readdir(absDir);
    const mdFiles = entries.filter((e) => e.endsWith('.md'));
    const directives: Directive[] = [];
    for (const file of mdFiles) {
      try {
        directives.push(await loadDirective(path.join(absDir, file)));
      } catch (err) {
        console.warn(`[DirectiveLoader] Failed to load ${file}:`, err instanceof Error ? err.message : err);
      }
    }
    return directives;
  } catch {
    return [];
  }
}

// ── Helpers ─────────────────────────────────────────────────────────

/** Extract a list from markdown (numbered or bulleted lines). */
function parseList(text: string): string[] {
  if (!text) return [];
  return text
    .split('\n')
    .map((l) => l.replace(/^\s*[-*]\s+/, '').replace(/^\s*\d+[.)]\s+/, '').trim())
    .filter((l) => l.length > 0 && !l.startsWith('#'));
}

/** Parse the metric section for command, description, and direction. */
function parseMetric(text: string): { metricCommand: string; metricDescription: string; lowerIsBetter: boolean } {
  if (!text) {
    return { metricCommand: '', metricDescription: 'No metric defined', lowerIsBetter: true };
  }

  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
  let metricCommand = '';
  let metricDescription = '';
  let lowerIsBetter = true;

  for (const line of lines) {
    // Look for code blocks or backtick-wrapped commands
    const codeMatch = line.match(/`([^`]+)`/);
    if (codeMatch && !metricCommand) {
      metricCommand = codeMatch[1];
    } else if (line.toLowerCase().includes('higher is better') || line.toLowerCase().includes('maximize')) {
      lowerIsBetter = false;
    } else if (!metricDescription) {
      metricDescription = line.replace(/^[-*]\s+/, '');
    }
  }

  return { metricCommand, metricDescription: metricDescription || metricCommand, lowerIsBetter };
}

/** Parse the budget section for time and cycle limits. */
function parseBudget(text: string): { timeBudgetSeconds: number; maxCycles: number } {
  if (!text) {
    return { timeBudgetSeconds: DEFAULT_TIME_BUDGET, maxCycles: DEFAULT_MAX_CYCLES };
  }

  let timeBudgetSeconds = DEFAULT_TIME_BUDGET;
  let maxCycles = DEFAULT_MAX_CYCLES;

  // Look for time patterns: "5 minutes", "300 seconds", "5m"
  const timeMatch = text.match(/(\d+)\s*(minutes?|mins?|m(?:\b|(?=\s)))/i);
  if (timeMatch) {
    timeBudgetSeconds = parseInt(timeMatch[1], 10) * 60;
  }
  const secMatch = text.match(/(\d+)\s*(seconds?|secs?|s(?:\b|(?=\s)))/i);
  if (secMatch) {
    timeBudgetSeconds = parseInt(secMatch[1], 10);
  }

  // Look for cycle patterns: "10 cycles", "max 20", "unlimited"
  const cycleMatch = text.match(/(\d+)\s*(cycles?|iterations?|runs?)/i);
  if (cycleMatch) {
    maxCycles = parseInt(cycleMatch[1], 10);
  }
  if (/unlimited|forever|never\s*stop/i.test(text)) {
    maxCycles = 0;
  }

  return { timeBudgetSeconds, maxCycles };
}
