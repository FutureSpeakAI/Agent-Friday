/**
 * agent-types.ts — Type definitions for Friday's enhanced multi-agent system.
 *
 * Supports parallel agents, sub-agents, agent teams with shared task lists,
 * chain-of-thought streaming, inter-agent awareness, and immediate cancellation.
 */

export type AgentStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export type AgentRole = 'parallel' | 'sub-agent' | 'team-member' | 'solo';

export interface AgentThought {
  timestamp: number;
  phase: string;        // "planning", "searching", "analysing", "synthesising", etc.
  text: string;         // The actual thought/reasoning step
  toolCall?: string;    // If this thought triggered a tool call
}

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
  thoughts: AgentThought[];      // Chain-of-thought stream
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
  parentId?: string;             // For sub-agent tasks
  windowTitle?: string;          // Window title this agent is working in
  personaId?: string;            // Persona for voice synthesis
  personaName?: string;          // Display name
  role: AgentRole;               // parallel, sub-agent, team-member, solo
  teamId?: string;               // Team this agent belongs to
  awareness?: string;            // Summary of what other agents are currently doing
  currentPhase?: string;         // Current work phase (for UI display)
  appTarget?: string;            // Application this agent is managing
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
  /** Stream a chain-of-thought step (visible in real-time to user) */
  think: (phase: string, thought: string, toolCall?: string) => void;
  /** Set current work phase label */
  setPhase: (phase: string) => void;
  /** Get awareness context (what other agents are doing) */
  getAwareness: () => string;
  /** Post a message to the team's shared context (if in a team) */
  postToTeam: (message: string) => void;
  /** Get the team's shared context (if in a team) */
  getTeamContext: () => string;
}

/* ── Team Types ──────────────────────────────────────────────────────── */

export interface TeamTask {
  id: string;
  description: string;
  assignedTo?: string;     // Agent task ID
  status: 'pending' | 'in-progress' | 'done' | 'blocked';
  priority: 'high' | 'medium' | 'low';
  result?: string;
  createdAt: number;
  completedAt?: number;
}

export interface AgentTeam {
  id: string;
  name: string;
  goal: string;
  members: string[];        // Agent task IDs
  taskList: TeamTask[];     // Shared task list
  sharedContext: string[];  // Messages posted by team members
  createdAt: number;
  status: 'active' | 'completed' | 'disbanded';
}

/* ── Office Visualization Types ──────────────────────────────────────── */

export interface OfficeAgent {
  id: string;              // Maps to AgentTask.id
  name: string;            // Persona name or agent type
  palette: number;         // Sprite palette index (0-5)
  hueShift: number;        // Hue rotation for uniqueness
  state: 'idle' | 'walk' | 'type' | 'spawning' | 'despawning';
  tileCol: number;
  tileRow: number;
  seatId?: string;
  currentTask?: string;    // Brief description for speech bubble
  currentThought?: string; // Latest thought for display
  isSubAgent: boolean;
  parentId?: string;
  teamId?: string;
  role: AgentRole;
}
