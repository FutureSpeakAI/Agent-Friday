# Phase D.2: "The Switchboard" — Register Missing IPC Handlers

**Track:** D — The Wiring
**Hermeneutic Focus:** IPC handlers are the nervous system — they translate between main process intelligence and renderer experience. Four handler groups were built in Sprint 1 but never connected to the switchboard. Without registration, the renderer's calls go unanswered.

## Current State

In `index.ts` lines 654-694, IPC handlers are registered in a block. Four Sprint 1 handlers are exported from `ipc/index.ts` but missing from this block:
- `registerExecutionDelegateHandlers` (Track B.3 — tool:execute, tool:confirm-response, tool:list-tools)
- `registerAppContextHandlers` (Track C.3 — app-context:get)
- `registerContextPushHandlers` (Track C.1 — context push subscriptions)
- `registerBriefingDeliveryHandlers` (Track A.3 — briefing delivery channels)

## Validation Criteria

Write failing tests first, then make them pass:

1. `registerExecutionDelegateHandlers()` is called during IPC initialization
2. `registerAppContextHandlers()` is called during IPC initialization
3. `registerContextPushHandlers()` is called during IPC initialization
4. `registerBriefingDeliveryHandlers()` is called during IPC initialization
5. IPC channel `tool:execute` responds after handler registration
6. IPC channel `app-context:get` responds after handler registration
7. All 4 new handlers are imported from the barrel export `ipc/index.ts`
8. Registering handlers twice doesn't create duplicate listeners (idempotency)

## Socratic Inquiry

**Precedent:** How are the existing 30+ handlers registered in index.ts? Follow the exact same pattern — import from barrel, call in the registration block.

**Boundary:** Some handlers need dependencies (e.g., `registerDelegationEngineHandlers(mainWindow ?? undefined)`). Do any of the 4 missing handlers need injected dependencies? Check their function signatures.

**Constraint Discovery:** `registerContextPushHandlers` returns a cleanup function (`ContextPushCleanup`). Should this cleanup be called during shutdown? Where?

**Tension:** Adding 4 handler registrations is simple, but should they be in Phase A (before vault) or Phase B (after vault)? Tool execution likely needs vault-protected API keys.

**Safety Gate:** Registering handlers that subscribe to singletons (like `liveContextBridge`) — are the singletons initialized by the time handlers register? Order matters.

## Boundary Constraints

- **Max new lines:** ~15 (4 import additions, 4 call additions, test file)
- **Modify:** `src/main/index.ts` — add imports and registration calls
- **Create:** `tests/sprint-2/ipc-registration.test.ts`
- **Depends on:** D.1 (bridge must be started before app-context handler is useful)

## Files to Read

- `journals/track-d-phase-1.md` (previous phase journal)
- `contracts/execution-delegate.md` (handler API)
- `contracts/live-context-bridge.md` (app-context handler API)
- `src/main/ipc/context-push-handlers.ts` (check cleanup signature)

## Session Journal Reminder

Before closing, write `journals/track-d-phase-2.md` covering:
- Which handlers were added and their dependency requirements
- Boot phase placement rationale (Phase A vs Phase B)
- Context push cleanup handling
