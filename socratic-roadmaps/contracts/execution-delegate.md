# Contract: ExecutionDelegate

## Module
`src/main/execution-delegate.ts`

## Purpose
Final link in the tool execution chain. Wires together safety evaluation, tool resolution, and handler execution into a single pipeline that never throws.

## Singleton
```typescript
import { executionDelegate } from './execution-delegate';
```

## API

### `execute(toolCall: ToolCall): Promise<ToolResult>`
Run a tool call through the full pipeline: safety → resolve → execute.

**Returns**:
- `approved` → resolves tool, runs handler, returns `ToolResult`
- `pending` → returns `ToolResult { is_error: true, content: "...pending (decisionId: sd-X)..." }`
- `denied` → returns `ToolResult { is_error: true, content: "...denied: reason..." }`
- Handler throws → returns `ToolResult { is_error: true, content: "Tool execution error: ..." }`

**Never throws.** Every code path returns a structured `ToolResult`.

### `executeAfterConfirmation(decisionId: string): Promise<ToolResult>`
Resume execution after a pending safety decision is confirmed.

**TOCTOU safety**: Reads the `ToolCall` from the stored `SafetyDecision`, not from a new request. This prevents modification between safety check and execution.

**Returns**:
- Decision approved → resolves tool, runs handler, returns `ToolResult`
- Decision denied → returns `ToolResult { is_error: true }`
- Decision not found → returns `ToolResult { is_error: true }`
- Decision still pending → returns `ToolResult { is_error: true }`

## Execute Flow
```
delegate.execute(toolCall)
  → safetyPipeline.evaluate(toolCall)
    ├── approved → toolRegistry.resolve(name) → handler(input) → ToolResult
    ├── pending  → ToolResult { is_error: true, content: "pending (decisionId: sd-X)" }
    └── denied   → ToolResult { is_error: true, content: "denied: reason" }
```

## Two-Phase Pending Flow
```
1. delegate.execute(toolCall)         → immediate pending ToolResult
2. safetyPipeline.confirm(decisionId) → mark decision approved
3. delegate.executeAfterConfirmation(decisionId) → resolve + run handler
```

## IPC Channels (via execution-delegate-handlers.ts)

| Channel | Direction | Payload |
|---------|-----------|---------|
| `tool:execute` | renderer → main | `ToolCall { id, type, name, input }` |
| `tool:confirm-response` | renderer → main | `{ decisionId: string, approved: boolean }` |
| `tool:list-tools` | renderer → main | (none) → `ToolRegistryDefinition[]` |

## Preload Namespace
```typescript
window.eve.toolExecution.execute(toolCall)
window.eve.toolExecution.confirmResponse(decisionId, approved)
window.eve.toolExecution.listTools()
```

## Dependencies
- `ToolRegistry.resolve(name)` — returns handler function
- `SafetyPipeline.evaluate(toolCall)` — returns SafetyDecision
- `SafetyPipeline.confirm/deny(decisionId)` — mutates decision status
- `SafetyPipeline.getDecision(decisionId)` — reads stored decision

## Types (from llm-client.ts)
```typescript
interface ToolCall {
  id: string;
  type: 'function' | 'tool_use';
  name: string;
  input: unknown;
}

interface ToolResult {
  tool_use_id: string;
  content: string | ContentPart[];
  is_error?: boolean;
}
```
