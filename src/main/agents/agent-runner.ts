/**
 * agent-runner.ts — Background Agent Runner for EVE OS.
 *
 * Manages a queue of background agent tasks with max concurrency.
 * Agents run in the main process, powered by Claude Sonnet for reasoning.
 * The renderer is notified of status updates via IPC events.
 */

import { BrowserWindow } from 'electron';
import crypto from 'crypto';
import { AgentTask, AgentStatus, AgentDefinition, AgentContext } from './agent-types';
import { builtinAgents } from './builtin-agents';
import { findPersonaForAgentType, type AgentPersona } from './agent-personas';
import { agentVoice } from './agent-voice';
import { settingsManager } from '../settings';

const MAX_CONCURRENT = 3;
const MAX_TASKS = 50;

class AgentRunner {
  private tasks: Map<string, AgentTask> = new Map();
  private definitions: Map<string, AgentDefinition> = new Map();
  private running = 0;
  private queue: string[] = [];
  private cancelled: Set<string> = new Set();
  private mainWindow: BrowserWindow | null = null;

  constructor() {
    // Register built-in agents
    for (const agent of builtinAgents) {
      this.definitions.set(agent.name, agent);
    }
  }

  initialize(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;
    console.log(`[AgentRunner] Initialized with ${this.definitions.size} agent types`);
  }

  /**
   * Spawn a new background agent task.
   */
  spawn(
    agentType: string,
    description: string,
    input: Record<string, unknown> = {},
    parentId?: string,
    windowTitle?: string
  ): AgentTask {
    const definition = this.definitions.get(agentType);
    if (!definition) {
      throw new Error(`Unknown agent type: ${agentType}. Available: ${[...this.definitions.keys()].join(', ')}`);
    }

    // Match persona to agent type for voice synthesis
    const persona = findPersonaForAgentType(agentType);

    const task: AgentTask = {
      id: crypto.randomUUID(),
      agentType,
      description,
      status: 'queued',
      progress: 0,
      input,
      logs: [],
      createdAt: Date.now(),
      parentId,
      windowTitle,
      personaId: persona?.id,
      personaName: persona?.name,
    };

    this.tasks.set(task.id, task);

    // Enforce max tasks
    if (this.tasks.size > MAX_TASKS) {
      const oldest = [...this.tasks.values()]
        .filter((t) => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled')
        .sort((a, b) => (a.completedAt || a.createdAt) - (b.completedAt || b.createdAt));
      for (const old of oldest) {
        if (this.tasks.size <= MAX_TASKS) break;
        this.tasks.delete(old.id);
      }
    }

    this.queue.push(task.id);
    this.emitUpdate(task);
    this.processQueue();

    console.log(`[AgentRunner] Spawned ${agentType}: "${description}" (${task.id.slice(0, 8)})`);
    return task;
  }

  /**
   * Cancel a running or queued task.
   */
  cancel(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;

    if (task.status === 'queued') {
      task.status = 'cancelled';
      task.completedAt = Date.now();
      this.queue = this.queue.filter((id) => id !== taskId);
      this.emitUpdate(task);
      return true;
    }

    if (task.status === 'running') {
      this.cancelled.add(taskId);
      task.logs.push('[System] Cancellation requested...');
      this.emitUpdate(task);
      return true;
    }

    return false;
  }

  /**
   * Get a task by ID.
   */
  get(taskId: string): AgentTask | undefined {
    return this.tasks.get(taskId);
  }

  /**
   * List all tasks, optionally filtered by status.
   */
  list(status?: AgentStatus): AgentTask[] {
    const all = [...this.tasks.values()];
    if (status) return all.filter((t) => t.status === status);
    return all.sort((a, b) => b.createdAt - a.createdAt);
  }

  /**
   * Get available agent type names and descriptions.
   */
  getAgentTypes(): Array<{ name: string; description: string }> {
    return [...this.definitions.values()].map((d) => ({
      name: d.name,
      description: d.description,
    }));
  }

  // --- Private ---

  private async processQueue(): Promise<void> {
    while (this.running < MAX_CONCURRENT && this.queue.length > 0) {
      const taskId = this.queue.shift()!;
      const task = this.tasks.get(taskId);
      if (!task || task.status !== 'queued') continue;

      this.running++;
      task.status = 'running';
      task.startedAt = Date.now();
      this.emitUpdate(task);

      this.executeTask(task).finally(() => {
        this.running--;
        this.processQueue();
      });
    }
  }

  private async executeTask(task: AgentTask): Promise<void> {
    const definition = this.definitions.get(task.agentType);
    if (!definition) {
      task.status = 'failed';
      task.error = `Agent type "${task.agentType}" not found`;
      task.completedAt = Date.now();
      this.emitUpdate(task);
      return;
    }

    const context: AgentContext = {
      log: (message: string) => {
        task.logs.push(`[${new Date().toLocaleTimeString('en-GB')}] ${message}`);
        this.emitUpdate(task);
      },
      setProgress: (percent: number) => {
        task.progress = Math.max(0, Math.min(100, percent));
        this.emitUpdate(task);
      },
      isCancelled: () => this.cancelled.has(task.id),
      callClaude: (prompt: string, maxTokens?: number) => this.callClaude(prompt, maxTokens),
    };

    try {
      const persona = task.personaId ? findPersonaForAgentType(task.agentType) : undefined;
      if (persona) {
        context.log(`${persona.name} (${persona.role}) taking over...`);
      } else {
        context.log(`Starting ${task.agentType} agent...`);
      }

      const result = await definition.execute(task.input, context);

      if (this.cancelled.has(task.id)) {
        task.status = 'cancelled';
        task.logs.push('[System] Task cancelled');
      } else {
        task.status = 'completed';
        task.result = result;
        task.progress = 100;

        // Synthesize voice for persona agents if enabled
        if (persona && result && settingsManager.isAgentVoicesEnabled()) {
          await this.synthesizeAndSpeak(task, persona, result);
        }
      }
    } catch (err) {
      task.status = 'failed';
      task.error = err instanceof Error ? err.message : String(err);
      task.logs.push(`[Error] ${task.error}`);
    }

    task.completedAt = Date.now();
    this.cancelled.delete(task.id);
    this.emitUpdate(task);

    const duration = task.completedAt - (task.startedAt || task.createdAt);
    console.log(
      `[AgentRunner] ${task.agentType} ${task.status} in ${Math.round(duration / 1000)}s: ${task.id.slice(0, 8)}`
    );
  }

  /**
   * Synthesize agent result to speech via ElevenLabs and send to renderer for playback.
   */
  private async synthesizeAndSpeak(
    task: AgentTask,
    persona: AgentPersona,
    resultText: string
  ): Promise<void> {
    try {
      // Create a concise spoken summary (agents produce detailed text — voice needs a tighter version)
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

      const { audioBuffer, durationEstimate } = await agentVoice.speak(
        spokenText,
        persona.voiceId
      );

      // Send audio to renderer for playback
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

  private async callClaude(prompt: string, maxTokens = 2048): Promise<string> {
    const Anthropic = require('@anthropic-ai/sdk');
    const anthropic = new Anthropic.default({
      apiKey: process.env.ANTHROPIC_API_KEY,
    });

    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-20250514',
      max_tokens: maxTokens,
      messages: [{ role: 'user', content: prompt }],
    });

    return response.content.find((b: any) => b.type === 'text')?.text || '';
  }

  private emitUpdate(task: AgentTask): void {
    // Strip large fields for IPC efficiency
    const update = {
      id: task.id,
      agentType: task.agentType,
      description: task.description,
      status: task.status,
      progress: task.progress,
      result: task.result,
      error: task.error,
      logs: task.logs.slice(-20), // Last 20 log lines
      createdAt: task.createdAt,
      startedAt: task.startedAt,
      completedAt: task.completedAt,
      parentId: task.parentId,
      windowTitle: task.windowTitle,
      personaId: task.personaId,
      personaName: task.personaName,
    };

    this.mainWindow?.webContents.send('agents:update', update);
  }

  /**
   * Update the associated window title for a running agent task.
   * Agents may discover their target window mid-execution.
   */
  setWindowTitle(taskId: string, windowTitle: string): void {
    const task = this.tasks.get(taskId);
    if (task) {
      task.windowTitle = windowTitle;
      this.emitUpdate(task);
    }
  }
}

export const agentRunner = new AgentRunner();
