# Track H Phase 2: CloudGate - "The Threshold"

## Summary
Implemented CloudGate, a consent-based cloud escalation system that acts as
the guardian between local-first sovereignty and cloud intelligence. When the
ConfidenceAssessor flags a response for escalation, CloudGate ensures the user
explicitly consents before any data leaves the machine.

## What Was Built
- **src/main/cloud-gate.ts** (~184 lines)
  - Exported types: TaskCategory, PolicyScope, EscalationContext, GateDecision,
    GatePolicy, EscalationStats
  - CloudGate singleton class with start()/stop() lifecycle
  - Policy map with three scopes: once (consumed), session (memory), always (persisted)
  - IPC consent request/response flow via BrowserWindow.webContents.send
  - Stats counters for local, allowed, and denied escalations

- **tests/sprint-3/cloud-gate.test.ts** (11 tests)

## Policy Scopes
| Scope | Storage | Lifetime |
|-------|---------|----------|
| once | Memory Map | Consumed after single use |
| session | Memory Map | Cleared on stop() |
| always | electron-store via settingsManager | Persists across restarts |

## Design Decisions
1. **Singleton pattern** matches OllamaLifecycle/EmbeddingPipeline conventions
   with getInstance()/resetInstance() and start()/stop() lifecycle.
2. **Sovereign-first default**: no renderer = no consent = no cloud. When in
   doubt, stay local.
3. **IPC consent flow**: mainWindow.webContents.send for request, ipcMain.once
   for response. Unique channel per request prevents cross-talk.
4. **Stats are defensive copies**: getStats() returns a spread copy so callers
   cannot mutate internal counters.
5. **Settings integration**: always-scope policies persist via settingsManager
   using a cloudGatePolicies key, loaded on start().

## Test Coverage (11 tests)
1. Singleton with start()/stop() lifecycle
2. requestEscalation returns Promise<GateDecision> with allowed boolean
3. Emits IPC cloud-gate:request-consent when no policy exists
4. Returns allowed=true from existing allow policy without IPC
5. Returns allowed=false from existing deny policy without IPC
6. Once policy is consumed after single use
7. Session policy persists until stop() is called
8. Always policy persists to disk via settingsManager
9. getStats returns accurate counts of decisions
10. Returns denied with no-renderer reason when no mainWindow
11. Loads persisted always-policies from settings on start

## Safety Gate
- tsc --noEmit: clean
- Test count: 4,058 -> 4,069 (+11)
- All 103 test files passing
