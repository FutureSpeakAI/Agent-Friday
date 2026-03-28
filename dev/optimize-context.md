# Optimize Context — Minimize Token Usage

## Objective
Reduce the token count of LLM requests while maintaining output quality.
Every token saved means faster responses and lower API costs. Focus on
system prompts, context injection, and tool definitions.

## Editable Surface
- src/main/agents/agent-personas.ts (personality field length)
- src/main/friday-profile.ts (condensed profile generation)
- src/main/agents/orchestrator.ts (planning prompt compression)
- src/main/agents/builtin-agents.ts (agent prompt templates)

## Metric
Average tokens per LLM request — lower is better.
Secondary metric: output quality score must not drop below 7/10.
Track via intelligence router decision logging.

## Loop
1. Measure baseline: average token count across standard query types
2. Identify the largest token consumer (system prompt? tool definitions? history?)
3. Compress it: remove redundancy, use abbreviations, tighten instructions
4. Re-measure token count AND output quality
5. If tokens decreased AND quality stayed above 7/10: commit
6. If quality dropped below 7/10: revert even if tokens decreased

## Constraints
- Output quality gate: minimum 7/10 on judge evaluation
- Never remove safety-relevant instructions from system prompts
- Never truncate conversation history beyond the LLM's context window management
- Tool definitions must remain complete (partial defs cause hallucinated parameters)
- Friday profile condensation must keep top 8 trusted people

## Budget
3 minutes per cycle, 10 cycles

## Circuit Breaker
- Output quality drops below 6/10 (hard floor)
- A system prompt is compressed below 50 words (too terse to be useful)
- Tool definitions are modified (these must stay exact)
