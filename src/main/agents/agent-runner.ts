/**
 * agent-runner.ts — Enhanced Background Agent Runner for Agent Friday.
 *
 * Manages parallel agents, sub-agents, and agent teams with:
 * - Max concurrency with queue management
 * - Chain-of-thought streaming (live reasoning visible to user)
 * - Inter-agent awareness (agents know what others are doing)
 * - Immediate hard-stop cancellation
 * - Team-based collaboration with shared task lists
 * - Voice synthesis per persona
 * - Office visualization events
 */

import { BrowserWindow } from 'electron';
import crypto from 'crypto';
import { AgentTask, AgentStatus, AgentDefinition, AgentContext, AgentRole, AgentThought } from './agent-types';
import { builtinAgents } from './builtin-agents';
import { findPersonaForAgentType, type AgentPersona } from './agent-personas';
import { agentVoice } from './agent-voice';
import { agentTeams } from './agent-teams';
import { settingsManager } from '../settings';
import { officeManager } from '../agent-office/office-manager';
import { llmClient } from '../llm-client';
import { awarenessMesh } from './awareness-mesh';
import { capabilityMap } from './capability-map';
import { symbiontProtocol } from './symbiont-protocol';

const MAX_CONCURRENT = 5;   // Bumped from 3 for better parallelism
const MAX_TASKS = 100;

class AgentRunner {
  private tasks: Map<string, AgentTask> = new Map();
  private definitions: Map<string, AgentDefinition> = new Map();
  private running = 0;
  private queue: string[] = [];
  private cancelled: Set<string> = new Set();
  private hardStopped: Set<string> = new Set(); // Immediate abort
  private abortControllers: Map<string, AbortController> = new Map();
  private mainWindow: BrowserWindow | null = null;

  constructor() {
    for (const agent of builtinAgents) {
      this.definitions.set(agent.name, agent);
    }
  }

  initialize(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;

    // Populate capability map with builtin agent metadata for intelligent routing
    capabilityMap.registerBuiltins(this.getAgentTypes());

    console.log(`[AgentRunner] Initialized with ${this.definitions.size} agent types, ${capabilityMap.getAll().length} capabilities registered`);
  }

  /* ── Spawn ────────────────────────────────────────────────────────── */

  spawn(
    agentType: string,
    description: string,
    input: Record<string, unknown> = {},
    options: {
      parentId?: string;
      windowTitle?: string;
      role?: AgentRole;
      teamId?: string;
      appTarget?: string;
    } = {}
  ): AgentTask {
    const definition = this.definitions.get(agentType);
    if (!definition) {
      throw new Error(`Unknown agent type: ${agentType}. Available: ${[...this.definitions.keys()].join(', ')}`);
    }

    const persona = findPersonaForAgentType(agentType);
    const role = options.role || (options.parentId ? 'sub-agent' : options.teamId ? 'team-member' : 'solo');

    const task: AgentTask = {
      id: crypto.randomUUID(),
      agentType,
      description,
      status: 'queued',
      progress: 0,
      input,
      logs: [],
      thoughts: [],
      createdAt: Date.now(),
      parentId: options.parentId,
      windowTitle: options.windowTitle,
      personaId: persona?.id,
      personaName: persona?.name,
      role,
      teamId: options.teamId,
      appTarget: options.appTarget,
    };

    this.tasks.set(task.id, task);

    // Enforce max tasks (clean old completed ones)
    if (this.tasks.size > MAX_TASKS) {
      const oldest = [...this.tasks.values()]
        .filter((t) => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled')
        .sort((a, b) => (a.completedAt || a.createdAt) - (b.completedAt || b.createdAt));
      for (const old of oldest) {
        if (this.tasks.size <= MAX_TASKS) break;
        this.tasks.delete(old.id);
      }
    }

    // Join team if specified
    if (options.teamId) {
      agentTeams.addMember(options.teamId, task.id);
    }

    this.queue.push(task.id);
    this.emitUpdate(task);
    this.emitOfficeEvent('agent:queued', task);
    this.processQueue();

    // Register in awareness mesh for cross-tree coordination
    awarenessMesh.registerAgent(task.id, agentType, description, {
      role,
      teamId: options.teamId,
      parentId: options.parentId,
    });

    console.log(`[AgentRunner] Spawned ${agentType} [${role}]: "${description}" (${task.id.slice(0, 8)})`);
    return task;
  }

  /* ── Spawn Team ───────────────────────────────────────────────────── */

  /**
   * Create an agent team with multiple agents working toward a shared goal.
   */
  spawnTeam(
    teamName: string,
    goal: string,
    members: Array<{ agentType: string; description: string; input: Record<string, unknown> }>
  ): { teamId: string; taskIds: string[] } {
    const team = agentTeams.create(teamName, goal);
    const taskIds: string[] = [];

    for (const member of members) {
      const task = this.spawn(member.agentType, member.description, member.input, {
        role: 'team-member',
        teamId: team.id,
      });
      taskIds.push(task.id);
    }

    console.log(`[AgentRunner] Team "${teamName}" created with ${members.length} agents`);
    return { teamId: team.id, taskIds };
  }

  /* ── Cancel + Hard Stop ───────────────────────────────────────────── */

  /**
   * Graceful cancel — agent finishes current step then stops.
   */
  cancel(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;

    if (task.status === 'queued') {
      task.status = 'cancelled';
      task.completedAt = Date.now();
      this.queue = this.queue.filter((id) => id !== taskId);
      awarenessMesh.deregisterAgent(task.id);
      this.emitUpdate(task);
      this.emitOfficeEvent('agent:cancelled', task);
      return true;
    }

    if (task.status === 'running') {
      this.cancelled.add(taskId);
      task.logs.push('[System] Cancellation requested...');
      task.thoughts.push({
        timestamp: Date.now(),
        phase: 'cancelled',
        text: 'Cancellation requested — wrapping up current step',
      });
      this.emitUpdate(task);
      return true;
    }

    return false;
  }

  /**
   * HARD STOP — Immediately abort the agent. No graceful shutdown.
   * Aborts any pending HTTP requests and marks task as cancelled.
   * NOTE: We do NOT decrement this.running here — the .finally() in processQueue
   * handles that. We only mark the task so executeTask knows to bail out.
   */
  hardStop(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;

    if (task.status === 'queued') {
      return this.cancel(taskId);
    }

    if (task.status === 'running') {
      this.hardStopped.add(taskId);
      this.cancelled.add(taskId);

      // Abort any in-flight HTTP requests
      const controller = this.abortControllers.get(taskId);
      if (controller) {
        controller.abort();
        this.abortControllers.delete(taskId);
      }

      task.status = 'cancelled';
      task.completedAt = Date.now();
      task.logs.push('[System] ⛔ HARD STOPPED by user');
      task.thoughts.push({
        timestamp: Date.now(),
        phase: 'hard-stopped',
        text: 'Agent terminated immediately by user',
      });

      // Deregister from awareness mesh
      awarenessMesh.deregisterAgent(task.id);

      // Do NOT decrement this.running here — the .finally() callback in
      // processQueue handles the decrement when executeTask resolves/rejects.
      // Decrementing in both places caused this.running to go negative.
      this.emitUpdate(task);
      this.emitOfficeEvent('agent:stopped', task);
      // processQueue will be called by the .finally() when executeTask exits

      console.log(`[AgentRunner] HARD STOP: ${task.agentType} (${taskId.slice(0, 8)})`);
      return true;
    }

    return false;
  }

  /**
   * Stop all running and queued agents immediately.
   */
  hardStopAll(): number {
    let count = 0;
    for (const task of this.tasks.values()) {
      if (task.status === 'running' || task.status === 'queued') {
        this.hardStop(task.id);
        count++;
      }
    }
    return count;
  }

  /* ── Queries ──────────────────────────────────────────────────────── */

  get(taskId: string): AgentTask | undefined {
    return this.tasks.get(taskId);
  }

  list(status?: AgentStatus): AgentTask[] {
    const all = [...this.tasks.values()];
    if (status) return all.filter((t) => t.status === status);
    return all.sort((a, b) => b.createdAt - a.createdAt);
  }

  getAgentTypes(): Array<{ name: string; description: string }> {
    return [...this.definitions.values()].map((d) => ({
      name: d.name,
      description: d.description,
    }));
  }

  /**
   * Get a live awareness summary of all currently running agents.
   */
  getAwarenessSummary(excludeTaskId?: string): string {
    const running = [...this.tasks.values()].filter(
      (t) => t.status === 'running' && t.id !== excludeTaskId
    );

    if (running.length === 0) return 'No other agents are currently active.';

    return running
      .map((t) => {
        const persona = t.personaName || t.agentType;
        const phase = t.currentPhase || 'working';
        const progress = t.progress > 0 ? ` (${t.progress}%)` : '';
        const team = t.teamId ? ` [Team]` : '';
        return `• ${persona} — ${phase}${progress}${team}: ${t.description.slice(0, 80)}`;
      })
      .join('\n');
  }

  /**
   * Get thoughts for a specific task (for live chain-of-thought display).
   */
  getThoughts(taskId: string, sinceIndex = 0): AgentThought[] {
    const task = this.tasks.get(taskId);
    if (!task) return [];
    return task.thoughts.slice(sinceIndex);
  }

  /* ── Window Title ─────────────────────────────────────────────────── */

  setWindowTitle(taskId: string, windowTitle: string): void {
    const task = this.tasks.get(taskId);
    if (task) {
      task.windowTitle = windowTitle;
      this.emitUpdate(task);
    }
  }

  /* ── Private: Queue Processing ────────────────────────────────────── */

  private processingQueue = false;

  private async processQueue(): Promise<void> {
    // Guard against re-entrant calls causing race conditions
    if (this.processingQueue) return;
    this.processingQueue = true;

    try {
      while (this.running < MAX_CONCURRENT && this.queue.length > 0) {
        const taskId = this.queue.shift()!;
        const task = this.tasks.get(taskId);
        if (!task || task.status !== 'queued') continue;

        this.running++;
        task.status = 'running';
        task.startedAt = Date.now();
        this.emitUpdate(task);
        this.emitOfficeEvent('agent:started', task);

        this.executeTask(task).finally(() => {
          this.running = Math.max(0, this.running - 1); // Clamp to 0 as safety net
          this.abortControllers.delete(task.id);
          this.processQueue();
        });
      }
    } finally {
      this.processingQueue = false;
    }
  }

  /* ── Private: Task Execution ──────────────────────────────────────── */

  private async executeTask(task: AgentTask): Promise<void> {
    // If already hard-stopped before execution began
    if (this.hardStopped.has(task.id)) {
      this.hardStopped.delete(task.id);
      this.cancelled.delete(task.id);
      return;
    }

    const definition = this.definitions.get(task.agentType);
    if (!definition) {
      task.status = 'failed';
      task.error = `Agent type "${task.agentType}" not found`;
      task.completedAt = Date.now();
      this.emitUpdate(task);
      return;
    }

    // Create abort controller for this task
    const abortController = new AbortController();
    this.abortControllers.set(task.id, abortController);

    const context: AgentContext = {
      taskId: task.id,

      log: (message: string) => {
        if (this.hardStopped.has(task.id)) return;
        task.logs.push(`[${new Date().toLocaleTimeString('en-GB')}] ${message}`);
        this.emitUpdate(task);
      },

      setProgress: (percent: number) => {
        if (this.hardStopped.has(task.id)) return;
        task.progress = Math.max(0, Math.min(100, percent));
        awarenessMesh.updateAgent(task.id, { progress: task.progress });
        this.emitUpdate(task);
      },

      isCancelled: () => this.cancelled.has(task.id) || this.hardStopped.has(task.id),

      callClaude: (prompt: string, maxTokens?: number) =>
        this.callClaude(prompt, maxTokens, abortController.signal),

      think: (phase: string, thought: string, toolCall?: string) => {
        if (this.hardStopped.has(task.id)) return;
        const entry: AgentThought = {
          timestamp: Date.now(),
          phase,
          text: thought,
          toolCall,
        };
        task.thoughts.push(entry);

        // Keep last 50 thoughts to avoid memory bloat
        if (task.thoughts.length > 50) {
          task.thoughts = task.thoughts.slice(-50);
        }

        // Emit thought event for real-time streaming
        this.emitThought(task, entry);
        this.emitOfficeEvent('agent:thought', task, entry);
      },

      setPhase: (phase: string) => {
        if (this.hardStopped.has(task.id)) return;
        task.currentPhase = phase;
        awarenessMesh.updateAgent(task.id, { phase });
        this.emitUpdate(task);
        this.emitOfficeEvent('agent:phase', task);
      },

      getAwareness: () => {
        // Use awareness mesh if agent is registered, else fallback
        const meshContext = awarenessMesh.getAwarenessContext(task.id);
        task.awareness = meshContext !== 'Not registered in awareness mesh.'
          ? meshContext
          : this.getAwarenessSummary(task.id);
        return task.awareness;
      },

      postToTeam: (message: string) => {
        if (task.teamId) {
          const name = task.personaName || task.agentType;
          agentTeams.postMessage(task.teamId, name, message);
        }
      },

      getTeamContext: () => {
        if (task.teamId) {
          return agentTeams.getContext(task.teamId);
        }
        return '';
      },
    };

    try {
      const persona = task.personaId ? findPersonaForAgentType(task.agentType) : undefined;
      if (persona) {
        context.log(`${persona.name} (${persona.role}) taking over...`);
        context.think('initializing', `${persona.name} activating — ${persona.role}`);
      } else {
        context.log(`Starting ${task.agentType} agent...`);
        context.think('initializing', `${task.agentType} agent starting`);
      }

      context.setPhase('initializing');

      // Inject awareness if other agents are running
      const awareness = context.getAwareness();
      if (awareness !== 'No other agents are currently active.') {
        context.think('awareness', `Other active agents:\n${awareness}`);
      }

      const result = await definition.execute(task.input, context);

      if (this.hardStopped.has(task.id)) {
        // Already handled by hardStop()
        this.hardStopped.delete(task.id);
        this.cancelled.delete(task.id);
        return;
      }

      if (this.cancelled.has(task.id)) {
        task.status = 'cancelled';
        task.logs.push('[System] Task cancelled');
        context.think('cancelled', 'Task cancelled by user');
      } else {
        task.status = 'completed';
        task.result = result;
        task.progress = 100;
        context.think('complete', 'Task completed successfully');

        // Synthesize voice if enabled
        if (persona && result && settingsManager.isAgentVoicesEnabled()) {
          await this.synthesizeAndSpeak(task, persona, result);
        }
      }
    } catch (err) {
      if (this.hardStopped.has(task.id)) {
        this.hardStopped.delete(task.id);
        this.cancelled.delete(task.id);
        return;
      }

      task.status = 'failed';
      task.error = err instanceof Error ? err.message : String(err);
      task.logs.push(`[Error] ${task.error}`);
      context.think('error', `Failed: ${task.error}`);
    }

    task.completedAt = Date.now();
    this.cancelled.delete(task.id);
    this.hardStopped.delete(task.id);

    // Deregister from awareness mesh so peers see updated state
    awarenessMesh.deregisterAgent(task.id, task.result || undefined);

    // Record execution metrics in Symbiont Protocol for performance tracking
    const duration = task.completedAt - (task.startedAt || task.createdAt);
    symbiontProtocol.recordExecution({
      id: task.id,
      agentType: task.agentType,
      taskId: task.id,
      outcome: task.status as 'completed' | 'failed' | 'cancelled',
      durationMs: duration,
      hadError: !!task.error,
      errorMessage: task.error,
      role: task.role || 'solo',
      teamId: task.teamId,
      trustTier: 'local', // Default; delegation engine manages actual tier
      completedAt: task.completedAt,
    });

    // Apply self-healing corrections if any agents are underperforming
    const corrections = symbiontProtocol.getPendingCorrections();
    for (const correction of corrections) {
      if (correction.action === 'disable-agent') {
        capabilityMap.setEnabled(correction.agentType, false);
        console.warn(`[Symbiont] Disabled underperforming agent: ${correction.agentType} — ${correction.reason}`);
      }
    }

    this.emitUpdate(task);
    this.emitOfficeEvent('agent:completed', task);

    console.log(
      `[AgentRunner] ${task.agentType} [${task.role}] ${task.status} in ${Math.round(duration / 1000)}s: ${task.id.slice(0, 8)}`
    );
  }

  /* ── Private: AI Model API ────────────────────────────────────────── */

  /**
   * Call the preferred AI provider via the unified LLM abstraction layer.
   * The LLMClient automatically routes to the user's preferred provider
   * (Anthropic direct or OpenRouter) based on their settings.
   */
  private async callClaude(prompt: string, maxTokens = 2048, signal?: AbortSignal): Promise<string> {
    return llmClient.text(prompt, { maxTokens, signal });
  }

  /* ── Private: Voice Synthesis ─────────────────────────────────────── */

  private async synthesizeAndSpeak(
    task: AgentTask,
    persona: AgentPersona,
    resultText: string
  ): Promise<void> {
    try {
      const spokenText = await this.callClaude(
        `You are ${persona.name}, a ${persona.role}. ${persona.personality}\n\n` +
        `Summarize these findings in 2-4 spoken sentences as if briefing a colleague verbally. ` +
        `Be ${persona.speakingStyle}. Do NOT use bullet points or markdown — just natural speech.\n\n` +
        `FINDINGS:\n${resultText.slice(0, 3000)}`,
        512
      );

      if (!spokenText || spokenText.trim().length === 0) return;

      task.logs.push(`[Voice] ${persona.name} synthesizing speech...`);
      this.emitUpdate(task);

      const voiceResult = await agentVoice.speak(spokenText, persona.voiceId);

      if (!voiceResult) {
        // ElevenLabs not configured — degrade to text-only delivery
        this.mainWindow?.webContents.send('agents:speak', {
          taskId: task.id,
          personaId: persona.id,
          personaName: persona.name,
          personaRole: persona.role,
          audioBase64: null,
          contentType: null,
          durationEstimate: 0,
          spokenText,
        });
        task.logs.push(`[Voice] ${persona.name} — no voice service, delivered text-only`);
        return;
      }

      const { audioBuffer, durationEstimate } = voiceResult;

      this.mainWindow?.webContents.send('agents:speak', {
        taskId: task.id,
        personaId: persona.id,
        personaName: persona.name,
        personaRole: persona.role,
        audioBase64: audioBuffer.toString('base64'),
        contentType: 'audio/mpeg',
        durationEstimate,
        spokenText,
      });

      task.logs.push(`[Voice] ${persona.name} delivered (~${Math.round(durationEstimate)}s)`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      task.logs.push(`[Voice] Speech synthesis failed: ${msg}`);
      console.warn(`[AgentRunner] Voice synthesis failed for ${persona.name}:`, msg);
    }
  }

  /* ── Private: IPC Emission ────────────────────────────────────────── */

  private emitUpdate(task: AgentTask): void {
    const update = {
      id: task.id,
      agentType: task.agentType,
      description: task.description,
      status: task.status,
      progress: task.progress,
      result: task.result,
      error: task.error,
      logs: task.logs.slice(-30),
      thoughts: task.thoughts.slice(-10),
      createdAt: task.createdAt,
      startedAt: task.startedAt,
      completedAt: task.completedAt,
      parentId: task.parentId,
      windowTitle: task.windowTitle,
      personaId: task.personaId,
      personaName: task.personaName,
      role: task.role,
      teamId: task.teamId,
      currentPhase: task.currentPhase,
      appTarget: task.appTarget,
    };

    this.mainWindow?.webContents.send('agents:update', update);
  }

  /**
   * Emit a chain-of-thought event for real-time streaming.
   */
  private emitThought(task: AgentTask, thought: AgentThought): void {
    this.mainWindow?.webContents.send('agents:thought', {
      taskId: task.id,
      agentType: task.agentType,
      personaName: task.personaName,
      thought,
    });
  }

  /**
   * Emit events to the office visualization via officeManager.
   */
  private emitOfficeEvent(event: string, task: AgentTask, data?: unknown): void {
    const name = task.personaName || task.agentType;
    const role = task.role || 'solo';

    switch (event) {
      case 'agent:queued':
      case 'agent:started':
        officeManager.agentSpawned(task.id, name, role, task.teamId);
        break;
      case 'agent:thought':
        if (data && typeof data === 'object' && 'text' in data) {
          officeManager.agentThought(task.id, (data as AgentThought).text, (data as AgentThought).phase);
        }
        break;
      case 'agent:phase':
        officeManager.agentPhase(task.id, task.currentPhase || 'working');
        break;
      case 'agent:completed':
        officeManager.agentCompleted(task.id, task.result?.slice(0, 80));
        break;
      case 'agent:stopped':
      case 'agent:cancelled':
        officeManager.agentStopped(task.id);
        break;
    }
  }
}

export const agentRunner = new AgentRunner();
