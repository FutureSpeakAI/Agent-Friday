/**
 * Track B, Phase 1: "The Workbench" — Tool Registry
 *
 * Catalogs OS-level capabilities as tools that an LLM can discover,
 * reason about, and invoke via function calling. Each tool has:
 *   - A typed definition (name, description, input_schema, safetyLevel)
 *   - An execution handler (async function taking input → string result)
 *
 * This module is the catalog — it registers and resolves tools but
 * does NOT execute them. Phase B.3 (ExecutionDelegate) handles execution
 * through the safety pipeline (Phase B.2).
 *
 * Hermeneutic note: This module understands the *parts* — individual
 * tool capabilities. Phase B.2 (SafetyPipeline) will understand the
 * *whole* — which tool invocations are safe in context.
 */

import type { ToolDefinition } from './llm-client';
import { fileSearch } from './file-search';
import { filesManager } from './files-manager';
import { systemMonitor } from './system-monitor';
import { weather } from './weather';

// ── Types ─────────────────────────────────────────────────────────────

export type SafetyLevel = 'read-only' | 'write' | 'destructive';

/** Extended tool definition with safety classification */
export interface ToolRegistryDefinition extends ToolDefinition {
  safetyLevel: SafetyLevel;
}

/** Tool execution handler: takes parsed input, returns string result */
export type ToolHandler = (input: unknown) => Promise<string>;

export interface DefinitionFilter {
  safetyLevel?: SafetyLevel;
}

// ── ToolRegistry Class ────────────────────────────────────────────────

export class ToolRegistry {
  private tools = new Map<string, { definition: ToolRegistryDefinition; handler: ToolHandler }>();

  /**
   * Register a tool with its definition and execution handler.
   * Throws if a tool with the same name is already registered.
   */
  register(definition: ToolRegistryDefinition, handler: ToolHandler): void {
    if (this.tools.has(definition.name)) {
      throw new Error(
        `Tool "${definition.name}" is already registered. Use a unique name.`,
      );
    }
    this.tools.set(definition.name, { definition, handler });
  }

  /**
   * Get all registered tool definitions, optionally filtered.
   * Returns ToolRegistryDefinition[] suitable for LLM context injection.
   */
  getDefinitions(filter?: DefinitionFilter): ToolRegistryDefinition[] {
    const all = Array.from(this.tools.values()).map(t => t.definition);
    if (filter?.safetyLevel) {
      return all.filter(d => d.safetyLevel === filter.safetyLevel);
    }
    return all;
  }

  /**
   * Resolve a tool name to its execution handler.
   * Throws if the tool is not registered.
   */
  resolve(toolName: string): ToolHandler {
    const entry = this.tools.get(toolName);
    if (!entry) {
      throw new Error(
        `Unknown tool "${toolName}". Registered tools: ${Array.from(this.tools.keys()).join(', ') || '(none)'}`,
      );
    }
    return entry.handler;
  }
}

// ── Singleton with Startup Tools ──────────────────────────────────────

export const toolRegistry = new ToolRegistry();

// ── Register built-in tools ───────────────────────────────────────────

toolRegistry.register(
  {
    name: 'file_search',
    description: 'Search for files on the local filesystem by name, content, or type',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search query' },
        extensions: { type: 'array', items: { type: 'string' }, description: 'File extensions to filter' },
        maxResults: { type: 'number', description: 'Maximum results to return' },
      },
      required: ['query'],
    },
    safetyLevel: 'read-only',
  },
  async (input) => {
    const { query, extensions, maxResults } = input as { query: string; extensions?: string[]; maxResults?: number };
    const results = await fileSearch.search({ query, extensions, limit: maxResults });
    return JSON.stringify(results);
  },
);

toolRegistry.register(
  {
    name: 'list_directory',
    description: 'List files and folders in a directory, sorted directories-first',
    input_schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Directory path to list (supports ~ for home)' },
      },
      required: ['path'],
    },
    safetyLevel: 'read-only',
  },
  async (input) => {
    const { path } = input as { path: string };
    const entries = await filesManager.listDirectory(path);
    return JSON.stringify(entries);
  },
);

toolRegistry.register(
  {
    name: 'system_stats',
    description: 'Get current system statistics: CPU usage, memory, disk space, and uptime',
    input_schema: { type: 'object', properties: {} },
    safetyLevel: 'read-only',
  },
  async () => {
    const stats = await systemMonitor.getStats();
    return JSON.stringify(stats);
  },
);

toolRegistry.register(
  {
    name: 'system_processes',
    description: 'List running processes sorted by CPU usage',
    input_schema: {
      type: 'object',
      properties: {
        limit: { type: 'number', description: 'Maximum processes to return' },
      },
    },
    safetyLevel: 'read-only',
  },
  async (input) => {
    const { limit } = (input as { limit?: number }) ?? {};
    const procs = await systemMonitor.getProcesses(limit);
    return JSON.stringify(procs);
  },
);

toolRegistry.register(
  {
    name: 'weather_current',
    description: 'Get current weather conditions for the configured location',
    input_schema: { type: 'object', properties: {} },
    safetyLevel: 'read-only',
  },
  async () => {
    const current = await weather.getCurrent();
    return JSON.stringify(current);
  },
);
