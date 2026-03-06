# Session Journal: Track B, Phase 1 — "The Workbench"

**Date:** 2026-03-06
**Tests added:** 18 (total: 3,826 across 81 files)
**New lines:** ~155 (`src/main/tool-registry.ts`)

## What Was Built

`ToolRegistry` — a catalog that registers OS-level capabilities as tools with typed definitions, safety classifications, and execution handlers for LLM function calling.

### Architecture Decision: Catalog Only, No Execution

The registry stores tool definitions and handlers but does NOT execute tools. It is the *catalog* — Phase B.3 (ExecutionDelegate) will resolve tools from the registry, pass them through the safety pipeline (Phase B.2), and then execute. This separation means the registry is a pure data structure with no side effects beyond registration.

### Key Design Choices

1. **Map-based storage** — `Map<string, { definition, handler }>` gives O(1) lookup by tool name, preserves insertion order for `getDefinitions()`, and prevents name collisions.

2. **Extended ToolDefinition** — `ToolRegistryDefinition extends ToolDefinition` adds `safetyLevel: 'read-only' | 'write' | 'destructive'`. This classification is consumed by Phase B.2's safety pipeline to make approve/deny decisions.

3. **Five startup tools** — All read-only, registered at module load:
   | Tool | Backend | Safety |
   |------|---------|--------|
   | `file_search` | `fileSearch.search()` | read-only |
   | `list_directory` | `filesManager.listDirectory()` | read-only |
   | `system_stats` | `systemMonitor.getStats()` | read-only |
   | `system_processes` | `systemMonitor.getProcesses()` | read-only |
   | `weather_current` | `weather.getCurrent()` | read-only |

4. **Duplicate prevention** — `register()` throws if a tool name is already taken, ensuring the catalog has no ambiguous entries.

5. **Filter by safety level** — `getDefinitions({ safetyLevel: 'read-only' })` enables the safety pipeline to selectively inject only safe tools into LLM context.

## Bug Found and Fixed

**FileSearchQuery.limit vs maxResults**: The contract suggested `maxResults` but the actual `FileSearchQuery` interface uses `limit`. Fixed the handler to map `maxResults` (the tool's input schema name) to `limit` (the backend's expected field).

## Patterns Established

- **ToolHandler type**: `(input: unknown) => Promise<string>` — simple, universal handler signature. Handlers receive parsed JSON input and return string results.
- **Schema-in-definition**: Each tool's `input_schema` is a JSON Schema object embedded in the definition, allowing LLMs to generate valid tool calls.
- **Singleton + class export**: `toolRegistry` singleton for production use, `ToolRegistry` class for test isolation.

## What Phase B.2 Should Know

1. `toolRegistry.getDefinitions({ safetyLevel })` filters by safety level — use this to decide which tools require approval.
2. `toolRegistry.resolve(toolName)` returns the handler — but B.2 should intercept before calling it.
3. All 5 startup tools are `read-only` — no `write` or `destructive` tools exist yet.
4. The `safetyLevel` is set at registration time and is immutable.
5. Tool handlers are `async` — they may involve I/O (file system, system calls, network).

## Interface Changes

### New Exports (src/main/tool-registry.ts)
- `ToolRegistry` — class
- `toolRegistry` — singleton instance with 5 built-in tools
- `ToolRegistryDefinition` — interface extending `ToolDefinition` with `safetyLevel`
- `ToolHandler` — type `(input: unknown) => Promise<string>`
- `SafetyLevel` — type `'read-only' | 'write' | 'destructive'`
- `DefinitionFilter` — interface `{ safetyLevel?: SafetyLevel }`

### No IPC Channels (internal module, consumed by ExecutionDelegate in B.3)
### No Modifications to Existing Files
