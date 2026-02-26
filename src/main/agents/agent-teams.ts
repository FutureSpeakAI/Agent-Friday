/**
 * agent-teams.ts — Agent Team management for collaborative multi-agent work.
 *
 * Teams are groups of agents that share:
 * - A common goal
 * - A shared task list (visible to all members)
 * - A shared context channel (members can post messages)
 * - Awareness of what each member is doing
 *
 * Teams can be created by the orchestrator or by Friday herself.
 */

import crypto from 'crypto';
import { AgentTeam, TeamTask } from './agent-types';

class AgentTeamManager {
  private teams: Map<string, AgentTeam> = new Map();

  /**
   * Create a new agent team.
   */
  create(name: string, goal: string): AgentTeam {
    const team: AgentTeam = {
      id: crypto.randomUUID(),
      name,
      goal,
      members: [],
      taskList: [],
      sharedContext: [],
      createdAt: Date.now(),
      status: 'active',
    };
    this.teams.set(team.id, team);
    console.log(`[Teams] Created team "${name}" (${team.id.slice(0, 8)}): ${goal}`);
    return team;
  }

  /**
   * Add an agent to a team.
   */
  addMember(teamId: string, agentTaskId: string): boolean {
    const team = this.teams.get(teamId);
    if (!team || team.status !== 'active') return false;
    if (!team.members.includes(agentTaskId)) {
      team.members.push(agentTaskId);
    }
    return true;
  }

  /**
   * Remove an agent from a team.
   */
  removeMember(teamId: string, agentTaskId: string): boolean {
    const team = this.teams.get(teamId);
    if (!team) return false;
    team.members = team.members.filter((m) => m !== agentTaskId);
    return true;
  }

  /**
   * Add a task to the team's shared task list.
   */
  addTask(teamId: string, description: string, priority: 'high' | 'medium' | 'low' = 'medium'): TeamTask | null {
    const team = this.teams.get(teamId);
    if (!team || team.status !== 'active') return null;

    const task: TeamTask = {
      id: crypto.randomUUID(),
      description,
      status: 'pending',
      priority,
      createdAt: Date.now(),
    };

    team.taskList.push(task);
    return task;
  }

  /**
   * Claim a task from the team list.
   */
  claimTask(teamId: string, taskId: string, agentTaskId: string): boolean {
    const team = this.teams.get(teamId);
    if (!team) return false;

    const task = team.taskList.find((t) => t.id === taskId);
    if (!task || task.status !== 'pending') return false;

    task.assignedTo = agentTaskId;
    task.status = 'in-progress';
    return true;
  }

  /**
   * Complete a team task.
   */
  completeTask(teamId: string, taskId: string, result?: string): boolean {
    const team = this.teams.get(teamId);
    if (!team) return false;

    const task = team.taskList.find((t) => t.id === taskId);
    if (!task) return false;

    task.status = 'done';
    task.result = result;
    task.completedAt = Date.now();

    // Check if all tasks are done
    const allDone = team.taskList.every((t) => t.status === 'done');
    if (allDone && team.taskList.length > 0) {
      team.status = 'completed';
      console.log(`[Teams] Team "${team.name}" completed all tasks`);
    }

    return true;
  }

  /**
   * Post a message to the team's shared context.
   */
  postMessage(teamId: string, agentName: string, message: string): void {
    const team = this.teams.get(teamId);
    if (!team) return;

    const entry = `[${new Date().toLocaleTimeString('en-GB')}] ${agentName}: ${message}`;
    team.sharedContext.push(entry);

    // Keep last 100 messages
    if (team.sharedContext.length > 100) {
      team.sharedContext = team.sharedContext.slice(-100);
    }
  }

  /**
   * Get the team's shared context as a formatted string.
   */
  getContext(teamId: string): string {
    const team = this.teams.get(teamId);
    if (!team) return '';

    const taskSummary = team.taskList
      .map((t) => `  [${t.status.toUpperCase()}] ${t.description}${t.assignedTo ? ` (assigned)` : ''}`)
      .join('\n');

    const recentMessages = team.sharedContext.slice(-20).join('\n');

    return `TEAM: ${team.name}\nGOAL: ${team.goal}\n\nTASK LIST:\n${taskSummary || '  (empty)'}\n\nRECENT MESSAGES:\n${recentMessages || '  (none)'}`;
  }

  /**
   * Get a team by ID.
   */
  get(teamId: string): AgentTeam | undefined {
    return this.teams.get(teamId);
  }

  /**
   * List all active teams.
   */
  listActive(): AgentTeam[] {
    return [...this.teams.values()].filter((t) => t.status === 'active');
  }

  /**
   * List all teams.
   */
  listAll(): AgentTeam[] {
    return [...this.teams.values()].sort((a, b) => b.createdAt - a.createdAt);
  }

  /**
   * Disband a team.
   */
  disband(teamId: string): void {
    const team = this.teams.get(teamId);
    if (team) {
      team.status = 'disbanded';
      console.log(`[Teams] Team "${team.name}" disbanded`);
    }
  }

  /**
   * Clean up old completed/disbanded teams (keep last 20).
   */
  cleanup(): void {
    const inactive = [...this.teams.values()]
      .filter((t) => t.status !== 'active')
      .sort((a, b) => b.createdAt - a.createdAt);

    for (const team of inactive.slice(20)) {
      this.teams.delete(team.id);
    }
  }
}

export const agentTeams = new AgentTeamManager();
