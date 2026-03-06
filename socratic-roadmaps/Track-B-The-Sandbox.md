# Track B: "The Sandbox" — Superpower Execution Engine

## Hermeneutic Position

The Sandbox is the system's *hands* — the faculty that turns understanding into action. An AGI OS that can only observe but never act is incomplete. The whole (autonomous assistance) requires this part; this part (safe execution) derives its constraints from the whole.

This track closes the loop: `LLM Tool Call → Safety Check → OS Action → Result`.

## The Circle

```
Phase B.1: "The Workbench"
  Understanding the parts: What OS primitives exist as potential tools?
  → Builds a tool registry that catalogs available system capabilities

Phase B.2: "The Guardrails"
  Understanding through the whole: What must never happen?
  → Builds a safety pipeline that gates every action before execution

Phase B.3: "The Craftsman"
  Synthesis — the whole understood through all parts:
  → Builds the execution delegate that routes approved tool calls to real OS actions
```

## Estimated Scope

| Phase | New Files | New Lines | New Tests |
|-------|-----------|-----------|-----------|
| B.1 | 1 | ~130 | ~10 |
| B.2 | 1 | ~200 | ~16 |
| B.3 | 2 (main + IPC) | ~180 | ~14 |
| **Total** | **4** | **~510** | **~40** |

## Dependencies

- **Reads from**: llm-client.ts (ToolDefinition, ToolCall, ToolResult types), os-events.ts, file-search.ts, file-watcher.ts, files-manager.ts
- **Writes to**: New tool-registry, safety-pipeline, execution-delegate modules
- **Contracts needed**: llm-client (tool types), os-events, file-search

## Success Criteria (Track-Level)

After all three phases:
1. The LLM can request file searches, directory listings, and system queries through structured tool calls
2. Every tool call passes through the safety pipeline before execution
3. Destructive operations (write, delete, execute) require user confirmation
4. Read-only operations (search, list, query) execute without confirmation
5. Execution results are typed and flow back to the LLM as ToolResult
6. Existing 3,769+ tests still pass
7. `npx tsc --noEmit` reports 0 errors
