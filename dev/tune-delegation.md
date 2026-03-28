# Tune Delegation — Improve Agent Routing Accuracy

## Objective
Improve the accuracy of task-to-agent routing in the orchestrator and
capability map. When a user gives Friday a complex goal, the orchestrator
decomposes it and assigns sub-tasks to agents. Better routing means
fewer re-delegations, faster completion, and higher quality results.

## Editable Surface
- src/main/agents/capability-map.ts (scoring weights and domain tags)
- src/main/agents/agent-personas.ts (expertise arrays)
- src/main/agents/orchestrator.ts (planning prompt only)

## Metric
Delegation success rate — percentage of sub-tasks completed on first assignment.
Higher is better (maximize).

## Loop
1. Define a suite of 10 standard goals that exercise different agent capabilities
2. Run each goal through the orchestrator and record which agents are assigned
3. Evaluate: did each agent succeed on first try, or was re-delegation needed?
4. Identify the most common misrouting pattern
5. Adjust the capability map weights or planning prompt to address it
6. Re-run the suite and compare success rate
7. If improved: commit. If degraded: revert.

## Constraints
- Never add or remove agent types — only adjust routing weights
- Never modify the delegation engine's trust-tier logic
- Never change the orchestrator's wave-based execution model
- The planning prompt must always include the agent capability list
- Changes must not break existing tests

## Budget
5 minutes per cycle, 8 cycles

## Circuit Breaker
- Delegation tests fail
- An agent type receives zero tasks (routing bypassed it entirely)
- Success rate drops below 50%
