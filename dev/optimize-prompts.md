# Optimize Prompts — Evolve Agent Persona Prompts

## Objective
Improve the output quality of all three agent personas (Atlas, Nova, Cipher)
by iteratively evolving their system prompts. Each persona's personality
instruction is the primary lever for output quality.

## Editable Surface
- src/main/agents/agent-personas.ts (personality and speakingStyle fields)

## Metric
Judge-scored output quality on a standard query suite — higher is better (maximize).
Evaluate by running 5 queries through each persona and scoring 0-10 via a separate
Claude call acting as judge. Primary metric is average score across all queries.

## Loop
1. Read current persona prompts from agent-personas.ts
2. Select the lowest-scoring persona
3. Generate a prompt mutation (add instruction, rephrase, add constraint)
4. Run the test query suite through the mutated prompt
5. Score each response with the judge LLM
6. If average score improved: update the persona file, commit
7. If no improvement: revert, try a different mutation
8. Move to next persona. Repeat.

## Constraints
- Never change persona names, IDs, or voice mappings
- Never change the expertise arrays (those control routing)
- Personality instructions must stay under 500 words
- Prompts must not include jailbreak attempts or safety bypasses
- Judge scores are final — no arguing with the judge

## Budget
2 minutes per cycle, 15 cycles

## Circuit Breaker
- A persona's score drops by more than 2 points from baseline
- A prompt exceeds 500 words
- The mutation introduces instructions contradicting cLaw
