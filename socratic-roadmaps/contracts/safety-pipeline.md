## Interface Contract: Safety Pipeline
**Generated:** 2026-03-06
**Source:** src/main/safety-pipeline.ts (~200 lines)

### Exports
- `safetyPipeline` — singleton instance of `SafetyPipeline`
- `SafetyPipeline` — class for test isolation
- `SafetyDecision` — interface: `{ id, status, toolCall, message?, reason?, createdAt }`
- `DecisionStatus` — type: `'approved' | 'denied' | 'pending'`
- `SafetyPolicy` — interface: `{ autoApprove, requireConfirmation, pendingTimeoutMs }`

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| evaluate(toolCall) | `(toolCall: ToolCall): SafetyDecision` | Classify tool call → approve/deny/pending |
| confirm(decisionId) | `(decisionId: string): boolean` | Upgrade pending → approved; clears timer |
| deny(decisionId) | `(decisionId: string): boolean` | Upgrade pending → denied; clears timer |
| getDecision(decisionId) | `(decisionId: string): SafetyDecision \| undefined` | Look up decision by ID |
| getPolicy() | `(): SafetyPolicy` | Return current policy for UI inspection |

### Decision Logic
| Safety Level | Decision | Message |
|-------------|----------|---------|
| `read-only` | `approved` (immediate) | none |
| `write` | `pending` | `Tool "X" wants to modify data. Confirm to proceed.` |
| `destructive` | `pending` | `WARNING: Destructive action "X" requested...` |
| unknown tool | `denied` | reason: `Unknown tool "X"` |

### Timeout Behavior
- Pending decisions expire after 60 seconds
- Expired decisions auto-deny with reason: `Confirmation timeout expired`
- Fail-closed: ambiguity → denial

### Dependencies
- Requires: llm-client (ToolCall type), tool-registry (toolRegistry, SafetyLevel)
- Required by: execution-delegate (Track B.3)
