# Socratic Forge Methodology — Agent Friday v2.2

## The Hermeneutic Circle

This build follows Heidegger's hermeneutic circle: understanding the **parts** (individual tracks) through the **whole** (the unified AGI OS), and the **whole** through the **parts**. Each track builds one dimension of intelligence. Each phase within a track deepens understanding of both that dimension and the system it serves.

The circle manifests at three levels:
1. **Phase level**: Each phase's Socratic questions force the agent to understand its piece *in relation to* the whole before writing code.
2. **Track level**: Session journals carry forward interpretive context — each new agent inherits the previous agent's understanding.
3. **System level**: Interface contracts expose how parts connect, so agents building one subsystem understand how their work reshapes the whole.

## Seven Socratic Question Types

| Type | Purpose | Example |
|------|---------|---------|
| **Boundary** | Define what's inside vs. outside scope | "What events does the conductor own vs. delegate?" |
| **Inversion** | Stress-test by imagining the opposite | "What if the briefing engine generated *wrong* priorities?" |
| **Constraint Discovery** | Surface hidden limits | "What happens when 5 apps emit events simultaneously?" |
| **Precedent** | Learn from existing patterns | "How does context-stream.ts handle backpressure?" |
| **Tension** | Resolve competing goals | "Proactive intelligence vs. not annoying the user — where's the line?" |
| **Synthesis** | Combine ideas into something new | "How do work streams + briefings become a unified daily flow?" |
| **Safety Gate** | Prevent harm or regression | "Can this new pipeline break the existing 3,769 tests?" |

## Question Quality Checklist

Every Socratic question must satisfy:
- [ ] Answerable by writing code (not opinion)
- [ ] Has a testable outcome
- [ ] Cannot be answered with "yes" or "no"
- [ ] Forces the agent to examine the existing codebase
- [ ] Connects the part being built to the larger whole
- [ ] Would survive a "so what?" challenge

## Test-First Protocol

1. Phase file lists **Validation Criteria** (plain-English test descriptions)
2. Agent writes **failing tests** before any implementation
3. Agent implements code to **make tests pass**
4. Agent verifies the **Safety Gate** (no regressions)

## Context Budget (per agent session)

| Component | Lines | Purpose |
|-----------|-------|---------|
| Methodology excerpt | ~80 | This file, pruned |
| Gap map (focused) | ~60 | Current state awareness |
| Phase file | 100-150 | The work order |
| Previous journal | 30-50 | Knowledge chain |
| Interface contracts (max 3) | ~90 | Cross-system APIs |
| **Total ceiling** | **~430** | Leaves room for code |

## Session Journal Protocol

At the end of every phase, the agent writes a session journal covering:
- What was built (files, line counts)
- Decisions made and rationale
- Patterns established or followed
- What the next agent should know
- Validation results (tests passing/failing)
- Interface changes (new exports, IPC channels, events)

## Interface Contract Protocol

After any phase that creates or modifies a public API:
1. Extract exports, IPC channels, event topics, and dependencies
2. Write a ~25-30 line contract in `contracts/`
3. Future phases read contracts instead of full source files

## Context Pruning

If an agent approaches context limits:
1. Summarize completed work to 10-15 lines
2. Refresh the gap map with current state
3. Continue with the next phase file + contracts only
