# Session Journal: Track D, Phase 2 — "The Switchboard"

## Date
2026-03-06

## What was built
- Modified `src/main/index.ts` — added 4 handler imports (lines 182-185), `ContextPushCleanup` type import (line 186), module-level cleanup variable (line 195), 4 registration calls (lines 703-706), cleanup call in shutdown (line 949)
- Created `tests/track-d/ipc-registration.test.ts` (8 tests)

## Key Decisions

### Handler placement: Phase B (after vault unlock)
All 4 handlers are registered in the same IPC registration block as the existing 30+ handlers. This block runs inside `completeBootAfterUnlock()`, which executes after the vault is decrypted. This is correct because:
- `registerExecutionDelegateHandlers` — tool execution may need vault-protected API keys
- `registerAppContextHandlers` — reads from `liveContextBridge` which starts in Phase B
- `registerContextPushHandlers` — subscribes to context stream, active only after Phase B
- `registerBriefingDeliveryHandlers` — briefing system operates post-unlock

### Context push cleanup in shutdown
`registerContextPushHandlers(mainWindow!)` returns a `ContextPushCleanup` function that unsubscribes from the context stream. Stored in module-level `contextPushCleanup` variable and called during shutdown before `liveContextBridge.stop()`. Order: contextPushCleanup → liveContextBridge.stop → contextGraph.stop → stopContextStreamBridge.

### Non-null assertion on mainWindow (same as D.1)
`registerContextPushHandlers(mainWindow!)` — mainWindow is guaranteed non-null by this point in boot Phase B.

## Patterns Followed
- Same import location as other IPC handlers (barrel import block)
- Same registration pattern: simple function calls in the registration block
- `registerDelegationEngineHandlers(mainWindow ?? undefined)` already showed the pattern for handlers needing dependencies
- Test file uses `vi.hoisted()` for mock declarations (Sprint 1 pattern)

## Handler Signatures
| Handler | Params | Returns | IPC Channels |
|---------|--------|---------|-------------|
| `registerExecutionDelegateHandlers` | none | void | tool:execute, tool:confirm-response, tool:list-tools |
| `registerAppContextHandlers` | none | void | app-context:get |
| `registerContextPushHandlers` | `mainWindow: BrowserWindow` | `ContextPushCleanup` | context:subscribe, context:unsubscribe |
| `registerBriefingDeliveryHandlers` | none | void | briefing:list, briefing:dismiss |

## Test Count
- Before: 3,953 tests
- After: 3,961 tests (+8)
- TypeScript errors: 0
