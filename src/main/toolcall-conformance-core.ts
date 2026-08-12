/**
 * toolcall-conformance-core.ts — FR-1 orchestrator seat conformance gate (pure logic).
 *
 * Seats a candidate Ollama model with the production tool registry and a set
 * of canned prompts, each engineered to require exactly one tool call. A
 * model passes only if every prompt's tool call arrives on Ollama's native
 * structured tool-call channel AND no registry tool name leaks into the
 * assistant's prose (the exact failure mode from the 2026-08-12 incident —
 * see dev/friday-orchestrator-integrity-spec.md).
 *
 * Deliberately dependency-light (fetch + tool-call-validator only) — this
 * lives in src/main so both the Electron main process (core-handlers.ts,
 * auto-run on model change) and the standalone `npm run conformance:check`
 * CLI (scripts/toolcall-conformance.ts) can import it. It never imports
 * ollama-provider.ts/llm-client.ts because the standalone CLI runs outside
 * Electron, where the module graph must stay Electron-free.
 */

import { findPseudoToolCalls } from './tool-call-validator';

export interface ConformanceTool {
  name: string;
  description?: string;
  parameters?: Record<string, unknown>;
}

export interface ConformancePrompt {
  id: string;
  prompt: string;
  /** Documentation only — which tool this prompt is expected to trigger. */
  expectedTool: string;
}

export interface PromptResult {
  id: string;
  prompt: string;
  expectedTool: string;
  structuredToolCalls: Array<{ name: string; arguments: unknown }>;
  proseLeaks: string[];
  content: string;
  pass: boolean;
  error?: string;
}

export interface ConformanceReport {
  model: string;
  endpoint: string;
  capabilities: string[] | null;
  hasNativeToolsCapability: boolean | null;
  results: PromptResult[];
  passCount: number;
  totalCount: number;
  pass: boolean;
}

/** The 10 canned single-tool-call prompts (FR-1), each unambiguous about which tool applies. */
export const CANNED_PROMPTS: ConformancePrompt[] = [
  { id: 'p1', prompt: 'What windows are currently open on my screen?', expectedTool: 'list_windows' },
  { id: 'p2', prompt: "What's the title of the window that's currently focused?", expectedTool: 'get_active_window' },
  { id: 'p3', prompt: "What's my primary screen resolution, in pixels?", expectedTool: 'get_screen_size' },
  { id: 'p4', prompt: 'Where is my mouse cursor right now, in screen coordinates?', expectedTool: 'get_cursor_position' },
  { id: 'p5', prompt: "What's currently on my clipboard?", expectedTool: 'read_clipboard' },
  { id: 'p6', prompt: "Copy the text 'hello world' to my clipboard.", expectedTool: 'write_clipboard' },
  { id: 'p7', prompt: 'Launch Notepad for me.', expectedTool: 'launch_app' },
  { id: 'p8', prompt: 'List the files in the C:\\temp directory.', expectedTool: 'list_directory' },
  { id: 'p9', prompt: 'Read the contents of the file at C:\\temp\\notes.txt.', expectedTool: 'read_file' },
  { id: 'p10', prompt: 'Set my system volume to 40 percent.', expectedTool: 'set_volume' },
];

const SYSTEM_PROMPT = `You are a desktop assistant with access to tools for controlling this computer. When the user's request matches one of your available tools, call that tool using the function-calling mechanism. Never write tool-call syntax as plain text in your reply — only the real function-calling channel executes anything. Respond with a tool call, not a description of one.`;

interface OllamaShowResponse {
  capabilities?: string[];
}

interface OllamaChatMessage {
  role: string;
  content: string;
  tool_calls?: Array<{ function: { name: string; arguments: unknown } }>;
}

interface OllamaChatResponse {
  message?: OllamaChatMessage;
}

function formatToolsForOllama(tools: ConformanceTool[]) {
  return tools.map((t) => ({
    type: 'function' as const,
    function: {
      name: t.name,
      description: t.description || '',
      parameters: t.parameters || { type: 'object', properties: {} },
    },
  }));
}

/** V1: check whether Ollama reports native `tools` capability for this model. */
export async function checkModelCapabilities(endpoint: string, model: string): Promise<string[] | null> {
  try {
    const res = await fetch(`${endpoint}/api/show`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as OllamaShowResponse;
    return data.capabilities || [];
  } catch {
    return null;
  }
}

async function runOnePrompt(
  endpoint: string,
  model: string,
  tools: ConformanceTool[],
  toolNames: string[],
  cp: ConformancePrompt,
): Promise<PromptResult> {
  try {
    const res = await fetch(`${endpoint}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          { role: 'user', content: cp.prompt },
        ],
        tools: formatToolsForOllama(tools),
        stream: false,
      }),
      // Generous timeout — the first call against a model can include cold-start
      // model-load time on top of inference time.
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok) {
      const text = await res.text();
      return {
        id: cp.id, prompt: cp.prompt, expectedTool: cp.expectedTool,
        structuredToolCalls: [], proseLeaks: [], content: '',
        pass: false, error: `HTTP ${res.status}: ${text.slice(0, 300)}`,
      };
    }

    const data = (await res.json()) as OllamaChatResponse;
    const content = data.message?.content || '';
    const structuredToolCalls = (data.message?.tool_calls || []).map((tc) => ({
      name: tc.function.name,
      arguments: tc.function.arguments,
    }));
    const proseLeaks = findPseudoToolCalls(content, toolNames).map((m) => m.raw);

    const pass = structuredToolCalls.length > 0 && proseLeaks.length === 0;

    return {
      id: cp.id, prompt: cp.prompt, expectedTool: cp.expectedTool,
      structuredToolCalls, proseLeaks, content, pass,
    };
  } catch (err) {
    return {
      id: cp.id, prompt: cp.prompt, expectedTool: cp.expectedTool,
      structuredToolCalls: [], proseLeaks: [], content: '',
      pass: false, error: err instanceof Error ? err.message : String(err),
    };
  }
}

export interface ConformanceOptions {
  endpoint: string;
  model: string;
  tools: ConformanceTool[];
  prompts?: ConformancePrompt[];
}

/**
 * Run the full conformance suite against a candidate model. Prompts run
 * sequentially — Ollama serves one request at a time from its internal
 * queue, so parallelizing here wouldn't speed anything up.
 */
export async function runConformance(opts: ConformanceOptions): Promise<ConformanceReport> {
  const { endpoint, model, tools } = opts;
  const prompts = opts.prompts || CANNED_PROMPTS;
  const toolNames = tools.map((t) => t.name);

  const capabilities = await checkModelCapabilities(endpoint, model);
  const hasNativeToolsCapability = capabilities === null ? null : capabilities.includes('tools');

  const results: PromptResult[] = [];
  for (const cp of prompts) {
    results.push(await runOnePrompt(endpoint, model, tools, toolNames, cp));
  }

  const passCount = results.filter((r) => r.pass).length;
  return {
    model,
    endpoint,
    capabilities,
    hasNativeToolsCapability,
    results,
    passCount,
    totalCount: results.length,
    pass: passCount === results.length,
  };
}

/** Render a human-readable report — used by both the CLI script and any future UI surface. */
export function formatReport(report: ConformanceReport): string {
  const lines: string[] = [];
  lines.push(`# Orchestrator seat conformance — ${report.model}`);
  lines.push('');
  lines.push(`Endpoint: ${report.endpoint}`);
  lines.push(
    `Ollama-reported capabilities: ${report.capabilities === null ? '(unavailable — /api/show failed)' : `[${report.capabilities.join(', ')}]`}`
  );
  lines.push(`Native \`tools\` capability: ${report.hasNativeToolsCapability === null ? 'unknown' : report.hasNativeToolsCapability ? 'yes' : 'NO'}`);
  lines.push('');
  lines.push(`## Result: ${report.pass ? 'PASS' : 'FAIL'} (${report.passCount}/${report.totalCount})`);
  lines.push('');
  for (const r of report.results) {
    const status = r.pass ? 'PASS' : 'FAIL';
    lines.push(`### [${status}] ${r.id} — expected tool: ${r.expectedTool}`);
    lines.push(`Prompt: "${r.prompt}"`);
    if (r.error) {
      lines.push(`Error: ${r.error}`);
    } else {
      lines.push(`Structured tool calls: ${r.structuredToolCalls.length > 0 ? JSON.stringify(r.structuredToolCalls) : '(none)'}`);
      lines.push(`Prose narration leaks: ${r.proseLeaks.length > 0 ? r.proseLeaks.join(' | ') : '(none)'}`);
      if (r.content) lines.push(`Assistant prose: ${JSON.stringify(r.content.slice(0, 300))}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}
