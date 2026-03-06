# Phase B.1: "The Workbench" — Tool Registry

**Track:** B — The Sandbox
**Hermeneutic Focus:** Understanding the parts — what OS primitives already exist, and how do they become tools that an LLM can reason about and invoke?

## Current State

The system has several OS-level capabilities: `FileSearch` (Windows Search), `FilesManager` (directory listing, open, show-in-folder), `SystemMonitor` (CPU, memory, disk, processes), `OSEvents` (power, display, process tracking), and `FileWatcher` (file change monitoring). These are all accessible via IPC, but they exist as disconnected backends. The `LLMClient` defines `ToolDefinition`, `ToolCall`, and `ToolResult` types, but no registry maps tool names to actual backends.

## Architecture Context

```
[NEW: ToolRegistry]
  ├── registers available tools with name, description, parameters schema
  ├── catalogs which tools are read-only vs. destructive
  ├── provides getToolDefinitions() for LLM context injection
  └── provides resolveTool(name) → backend function reference
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `toolRegistry.register(definition, handler)` adds a tool with its execution handler
2. `toolRegistry.getDefinitions()` returns all registered `ToolDefinition[]` for LLM context
3. `toolRegistry.resolve(toolName)` returns the handler function, or throws for unknown tools
4. Each tool definition includes `safetyLevel: 'read-only' | 'write' | 'destructive'`
5. `toolRegistry.getDefinitions({ safetyLevel: 'read-only' })` filters by safety level
6. At least 5 tools are registered at startup: `file_search`, `list_directory`, `system_stats`, `system_processes`, `weather_current`
7. Tool definitions conform to the `ToolDefinition` type from `llm-client.ts`
8. `toolRegistry` is a singleton exported as `toolRegistry`
9. Duplicate tool name registration throws an error

## Socratic Inquiry

**Boundary:** A tool is a named capability with a typed input schema and a typed output. What distinguishes a "tool" from a regular backend function? Is it the schema? The safety classification? The discoverability?

**Precedent:** How does `llm-client.ts` define `ToolDefinition`? The registry must produce definitions that match this exact shape, so the LLM receives well-formed tool descriptions. Read the type and follow it precisely.

**Inversion:** What if every function in the system were registered as a tool? The LLM would have too many options and make poor choices. What's the curation principle? Only register tools that are *useful from a user's perspective*.

**Constraint Discovery:** The `ToolDefinition.parameters` field is a JSON Schema object. How complex should parameter schemas be? Simple flat objects, or nested structures? What does the LLM handle well?

**Tension:** Expressiveness vs. safety. More tools = more capability, but also more surface area for mistakes. How does `safetyLevel` classification help the safety pipeline (Phase B.2) make decisions?

**Safety Gate:** Can a malformed tool definition crash the LLM client? What validation should `register()` perform on the definition before accepting it?

## Boundary Constraints

- **Max new lines:** 130 (one file: `src/main/tool-registry.ts`)
- **No execution** — this phase only catalogs tools, doesn't run them
- **No IPC channels** — internal module consumed by the execution delegate (B.3)
- **Import but don't modify** `llm-client.ts` types

## Files to Read

- `contracts/llm-client.md` (ToolDefinition type shape)
- `contracts/file-search.md` (example tool capability)
- `contracts/system-monitor.md` (example tool capability)

## Session Journal Reminder

Before closing, write `journals/track-b-phase-1.md` covering:
- The 5+ tools registered and their safety classifications
- How tool definitions map to LLM ToolDefinition type
- What Phase B.2 needs to know about safety levels
