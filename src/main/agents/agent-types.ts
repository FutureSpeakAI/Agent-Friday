/**
 * agent-types.ts — Type definitions for EVE's background agent system.
 */

export type AgentStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface AgentTask {
  id: string;
  agentType: string;
  description: string;
  status: AgentStatus;
  progress: number; // 0-100
  input: Record<string, unknown>;
  result?: string;
  error?: string;
  logs: string[];
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
  parentId?: string; // For orchestrated sub-tasks
  windowTitle?: string; // Window title this agent is working in (for focus_window click-to-watch)
  personaId?: string; // Persona assigned to this task (for voice synthesis)
  personaName?: string; // Display name of the persona
}

export interface AgentDefinition {
  name: string;
  description: string;
  /** Execute the agent task. Should update logs via the callback. */
  execute: (
    input: Record<string, unknown>,
    context: AgentContext
  ) => Promise<string>;
}

export interface AgentContext {
  /** Append a log line visible in the agent dashboard */
  log: (message: string) => void;
  /** Update progress (0-100) */
  setProgress: (percent: number) => void;
  /** Check if cancellation was requested */
  isCancelled: () => boolean;
  /** Access the Anthropic API for Claude-powered agents */
  callClaude: (prompt: string, maxTokens?: number) => Promise<string>;
}
