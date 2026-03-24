# Agent System Flow

## Quick Reference

| Property | Value |
|----------|-------|
| **Status** | Active, multi-agent orchestration system |
| **Type** | Async task queue with parallel execution |
| **Complexity** | High (team coordination, persona voice synthesis, awareness mesh) |
| **Last Analysed** | 2026-03-24 |

## Overview

The agent system provides background task execution through specialized AI agents (research, code review, writing, etc.). Agents are spawned via IPC from the renderer, queued by `AgentRunner` with a max concurrency of 5, and executed by calling the LLM through `llmClient`. Each agent has a defined persona with a distinct voice, role, and personality. Agents can operate solo, as sub-agents, or as team members with shared task lists and context channels. Real-time chain-of-thought streaming and an awareness mesh allow agents to coordinate work.

## Flow Boundaries

- **Start**: Renderer calls `window.eve.agents.spawn(agentType, description, input)` via IPC
- **End**: Agent completes/fails, result emitted via `agents:update` IPC event, optional voice synthesis delivered

## Component Quick Reference

| Component | File | Purpose |
|-----------|------|---------|
| AgentDashboard | `src/renderer/components/AgentDashboard.tsx` | UI for viewing/managing running agents |
| Agent IPC Handlers | `src/main/ipc/agent-handlers.ts` | `agents:spawn`, `agents:list`, `agents:get`, `agents:cancel`, `agents:types` |
| AgentRunner | `src/main/agents/agent-runner.ts` | Task queue, concurrency control, execution engine |
| AgentTypes | `src/main/agents/agent-types.ts` | Type definitions: `AgentTask`, `AgentDefinition`, `AgentContext`, `AgentTeam` |
| BuiltinAgents | `src/main/agents/builtin-agents.ts` | Pre-built agents: research, code-review, writing, etc. |
| AgentPersonas | `src/main/agents/agent-personas.ts` | Persona definitions (Atlas, Nova, Cipher) with voice mappings |
| AgentTeams | `src/main/agents/agent-teams.ts` | Team creation, shared task lists, context channels |
| AgentVoice | `src/main/agents/agent-voice.ts` | Voice synthesis per persona (Gemini, local TTS, ElevenLabs) |
| AwarenessMesh | `src/main/agents/awareness-mesh.ts` | Cross-agent coordination and state sharing |
| CapabilityMap | `src/main/agents/capability-map.ts` | Agent capability registration and routing |
| SymbiontProtocol | `src/main/agents/symbiont-protocol.ts` | Performance tracking and self-healing corrections |
| OfficeManager | `src/main/agent-office/office-manager.ts` | Pixel-art visualization events for the Agent Office |
| LLMClient | `src/main/llm-client.ts` | Unified LLM abstraction for agent reasoning |

## Detailed Steps

### 1. Spawn Request (Renderer -> Main)

1. Renderer calls `window.eve.agents.spawn(agentType, description, input)`.
2. IPC handler in `agent-handlers.ts:14-24` validates inputs (`assertString`, `assertObject`).
3. Handler delegates to `agentRunner.spawn()`.

### 2. Task Creation (AgentRunner)

4. `agentRunner.spawn()` (agent-runner.ts:58) looks up the `AgentDefinition` from the builtin registry.
5. A `findPersonaForAgentType()` call (agent-personas.ts:84) matches the agent type to a persona (Atlas for research, Nova for writing, Cipher for code-review).
6. An `AgentTask` object is created with:
   - Unique UUID
   - Status: `queued`
   - Role: `solo`, `sub-agent`, or `team-member` based on options
   - Persona ID and name (if matched)
7. Task is stored in `this.tasks` Map and pushed to `this.queue`.
8. Old completed tasks are pruned if total exceeds 100 (agent-runner.ts:100-108).
9. If a `teamId` is specified, the agent is registered with `agentTeams.addMember()`.
10. Agent is registered in the `awarenessMesh` for cross-tree coordination (agent-runner.ts:121-125).
11. An `agents:update` IPC event is emitted to the renderer.
12. An office visualization event is emitted via `officeManager.agentSpawned()`.
13. `processQueue()` is called to start execution.

### 3. Queue Processing (AgentRunner)

14. `processQueue()` (agent-runner.ts:318-344) uses a re-entrancy guard.
15. While `this.running < MAX_CONCURRENT (5)` and queue is non-empty:
    - Task is dequeued, status set to `running`, `startedAt` timestamp recorded.
    - `executeTask(task)` runs asynchronously.
    - `.finally()` callback decrements `running` and re-calls `processQueue()`.

### 4. Task Execution (AgentRunner)

16. `executeTask()` (agent-runner.ts:348) creates an `AbortController` for the task.
17. An `AgentContext` object is constructed with:
    - `log(message)` -- appends to task logs, emits update
    - `setProgress(percent)` -- updates progress 0-100, notifies awareness mesh
    - `isCancelled()` -- checks both cancelled and hardStopped sets
    - `callClaude(prompt, maxTokens)` -- calls LLM via `llmClient.text()` (agent-runner.ts:544)
    - `think(phase, thought)` -- streams chain-of-thought to renderer via `agents:thought` event
    - `setPhase(phase)` -- updates current work phase label
    - `getAwareness()` -- returns summary of all other running agents
    - `postToTeam(message)` -- posts to team shared context
    - `getTeamContext()` -- reads team task list + recent messages
18. If persona exists, an initialization thought is emitted.
19. Awareness context is injected if other agents are running (agent-runner.ts:455-458).
20. `definition.execute(task.input, context)` is called -- the actual agent logic runs.

### 5. Agent Logic (BuiltinAgents)

21. Each builtin agent follows a pattern (e.g., research agent in builtin-agents.ts:12):
    - **Plan**: Use `ctx.callClaude()` to generate search queries or a work plan.
    - **Execute**: Perform web searches, tool calls, or analysis.
    - **Check cancellation**: `ctx.isCancelled()` at each major step.
    - **Stream thinking**: `ctx.think()` at each reasoning step for real-time UI.
    - **Update progress**: `ctx.setProgress()` throughout execution.
    - **Synthesize**: Use `ctx.callClaude()` to produce a final summary/result.
22. The agent returns a result string on success.

### 6. Completion and Voice (AgentRunner)

23. On success (agent-runner.ts:474-483):
    - Status set to `completed`, progress to 100.
    - If persona exists and `settingsManager.isAgentVoicesEnabled()`:
      - `synthesizeAndSpeak()` generates a 2-4 sentence spoken summary via Claude.
      - Voice synthesis runs through `agentVoice.speak()` with the persona's voice mapping.
      - Audio is sent to renderer via `agents:speak` IPC event.
24. On failure (agent-runner.ts:484-495):
    - Status set to `failed`, error message recorded.
25. On cancellation (agent-runner.ts:469-473):
    - Status set to `cancelled`.
26. In all cases:
    - Agent is deregistered from awareness mesh (agent-runner.ts:502).
    - Execution metrics recorded in `symbiontProtocol` (agent-runner.ts:506-518).
    - Self-healing corrections checked (agent-runner.ts:521-526): underperforming agents may be auto-disabled.
    - Final `agents:update` and office event emitted.

### 7. Team Collaboration (AgentTeams)

27. Teams are created via `agentTeams.create(name, goal)` (agent-teams.ts:22).
28. Each team has:
    - `members[]` -- agent task IDs
    - `taskList[]` -- shared tasks with status (pending/in-progress/done/blocked)
    - `sharedContext[]` -- message log from team members
29. Agents can claim tasks via `claimTask()`, complete them via `completeTask()`.
30. When all tasks are done, team status becomes `completed` (agent-teams.ts:109-113).
31. Team context is formatted as a readable string by `getContext()` for injection into agent prompts.

### 8. Cancellation

32. **Graceful cancel** (`cancel()`, agent-runner.ts:161): Sets a flag; agent finishes current step.
33. **Hard stop** (`hardStop()`, agent-runner.ts:196): Aborts the `AbortController`, immediately marks as cancelled, kills in-flight HTTP requests.
34. **Hard stop all** (`hardStopAll()`, agent-runner.ts:244): Stops all running and queued agents.

## Default Personas

| ID | Name | Role | Expertise | Gemini Voice | Local Voice |
|----|------|------|-----------|-------------|-------------|
| `atlas` | Atlas | Research Director | research, analysis, fact-checking | Iapetus | af_heart |
| `nova` | Nova | Creative Strategist | draft-email, writing, brainstorming | Aoede | af_bella |
| `cipher` | Cipher | Technical Lead | code-review, architecture, debugging | Puck | am_adam |

## IPC Channels Used

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `agents:spawn` | Renderer -> Main | Create and queue a new agent task |
| `agents:list` | Renderer -> Main | List tasks, optionally filtered by status |
| `agents:get` | Renderer -> Main | Get a specific task by ID |
| `agents:cancel` | Renderer -> Main | Graceful cancel of a running/queued task |
| `agents:types` | Renderer -> Main | List available agent type definitions |
| `agents:update` | Main -> Renderer | Task state change (status, progress, logs, thoughts) |
| `agents:thought` | Main -> Renderer | Real-time chain-of-thought streaming |
| `agents:speak` | Main -> Renderer | Voice synthesis result (base64 audio + metadata) |

## State Changes

| State | Location | Trigger |
|-------|----------|---------|
| `AgentTask.status` | AgentRunner `tasks` Map | Queue -> running -> completed/failed/cancelled |
| `AgentTask.progress` | AgentRunner `tasks` Map | Agent calls `ctx.setProgress()` |
| `AgentTask.thoughts[]` | AgentRunner `tasks` Map | Agent calls `ctx.think()` |
| `AgentTask.logs[]` | AgentRunner `tasks` Map | Agent calls `ctx.log()` |
| `AgentTeam.taskList[]` | AgentTeamManager | Members claim/complete tasks |
| `AgentTeam.sharedContext[]` | AgentTeamManager | Members post messages |
| AwarenessMesh registry | AwarenessMesh | Agent register/deregister/update |
| CapabilityMap enabled flags | CapabilityMap | Symbiont self-healing disables underperformers |

## Error Scenarios

| Scenario | Handling | Location |
|----------|----------|----------|
| Unknown agent type | `Error: Unknown agent type` thrown | agent-runner.ts:72 |
| Max concurrency reached | Task stays queued until a slot opens | agent-runner.ts:324 |
| Agent throws during execution | Status set to `failed`, error logged | agent-runner.ts:491-495 |
| Hard stop while running | AbortController aborted, task marked cancelled immediately | agent-runner.ts:196-238 |
| Voice synthesis fails | Logged as warning, task still completes | agent-runner.ts:601-605 |
| LLM provider unavailable | LLMClient falls back to next available provider | llm-client.ts:211-237 |
| Team task assignment race | `claimTask()` checks status === 'pending' | agent-teams.ts:82-92 |
| Symbiont detects underperformance | Agent type auto-disabled in CapabilityMap | agent-runner.ts:521-526 |
