## Interface Contract: Tool Registry
**Generated:** 2026-03-06
**Source:** src/main/tool-registry.ts (~155 lines)

### Exports
- `toolRegistry` — singleton instance of `ToolRegistry` with 5 built-in tools
- `ToolRegistry` — class for test isolation
- `ToolRegistryDefinition` — interface extending `ToolDefinition` with `safetyLevel`
- `ToolHandler` — type: `(input: unknown) => Promise<string>`
- `SafetyLevel` — type: `'read-only' | 'write' | 'destructive'`

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| register(def, handler) | `(def: ToolRegistryDefinition, handler: ToolHandler): void` | Add tool; throws on duplicate name |
| getDefinitions(filter?) | `(filter?: { safetyLevel? }): ToolRegistryDefinition[]` | List tools, optionally filtered |
| resolve(toolName) | `(toolName: string): ToolHandler` | Get handler; throws for unknown tool |

### Built-in Tools
| Name | Backend | Safety Level |
|------|---------|-------------|
| `file_search` | `fileSearch.search()` | read-only |
| `list_directory` | `filesManager.listDirectory()` | read-only |
| `system_stats` | `systemMonitor.getStats()` | read-only |
| `system_processes` | `systemMonitor.getProcesses()` | read-only |
| `weather_current` | `weather.getCurrent()` | read-only |

### Safety Levels
- `read-only` — no state changes, safe to auto-approve
- `write` — creates or modifies files/data, requires user awareness
- `destructive` — deletes or irreversibly changes data, requires explicit approval

### Dependencies
- Requires: llm-client (ToolDefinition type), file-search, files-manager, system-monitor, weather
- Required by: safety-pipeline (Track B.2), execution-delegate (Track B.3)
