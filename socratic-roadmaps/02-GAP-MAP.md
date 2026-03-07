# Gap Map — Sprint 2: "The Wiring"

## Baseline

- **Tests**: 3,945 (all passing)
- **TypeScript errors**: 0
- **Sprint 1 delivered**: 3 tracks (A: Proactive Intelligence, B: Safe Tool Execution, C: Cross-App Context Weaving) — 9 phases, ~151 new tests

## What Exists (Built but Unwired)

### Main Process Modules (Sprint 1 output)
| Module | File | Singleton | Status |
|--------|------|-----------|--------|
| BriefingPipeline | `briefing-pipeline.ts` | Yes | Built, not started in index.ts |
| BriefingScoringEngine | `briefing-scoring-engine.ts` | Yes | Built, used by pipeline |
| BriefingDelivery | `briefing-delivery.ts` | Yes | Built, not started in index.ts |
| ToolRegistry | `tool-registry.ts` | Yes | Built, used by delegate |
| SafetyPipeline | `safety-pipeline.ts` | Yes | Built, used by delegate |
| ExecutionDelegate | `execution-delegate.ts` | Yes | Built, no feedback loop |
| ContextInjector | `context-injector.ts` | No (class) | Built, used by bridge |
| LiveContextBridge | `live-context-bridge.ts` | Yes | Built, NOT started or stopped |

### IPC Handlers (Exported but Not Registered)
| Handler | Barrel Export Line | Called in index.ts? |
|---------|-------------------|---------------------|
| `registerAppContextHandlers` | ipc/index.ts:44 | **NO** |
| `registerExecutionDelegateHandlers` | ipc/index.ts:41 | **NO** |
| `registerContextPushHandlers` | ipc/index.ts:21 | **NO** |
| `registerBriefingDeliveryHandlers` | ipc/index.ts:26 | **NO** |

### App Components (22 in apps/)
| Category | Count | Examples |
|----------|-------|---------|
| Partially backed (window.eve.*) | 17 | Notes, Calendar, Tasks, Files, Monitor, Weather... |
| Pure client-side (no IPC) | 5 | Calc, Camera, Canvas, Maps, Recorder |
| Using useAppContext hook | **0** | — |
| Using useWorkContext hook | **0** | — |

## Gaps

### Gap D: "The Wiring" — Connect Sprint 1 modules to main process lifecycle
The three tracks built standalone modules with full test coverage, but none are wired into `index.ts`. The LiveContextBridge isn't started, 4 IPC handler groups aren't registered, and the execution delegate doesn't feed results back to the context graph.

**Subgaps:**
- D.1: Start/stop LiveContextBridge in index.ts lifecycle
- D.2: Register missing IPC handlers (app-context, execution-delegate, context-push, briefing-delivery)
- D.3: Connect execution feedback loop (delegate → bridge → context graph)

### Gap E: "The Mesh" — Connect app components to context injection
All 17 IPC-backed apps call `window.eve.*` directly but none use `useAppContext()`. The context injection system delivers enriched, per-app context (active work stream, relevant briefings, top entities), but no app consumes it. The hermeneutic circle stops at the bridge.

**Subgaps:**
- E.1: Add useAppContext integration to core productivity apps (Notes, Tasks, Calendar, Files)
- E.2: Add useAppContext integration to intelligence apps (Browser, Code, Forge, Comms)
- E.3: Add useAppContext integration to media/system apps (Monitor, Weather, Gallery, Media)

### Gap F: "The Circle" — End-to-end integration testing
No test verifies the full hermeneutic circle: user action → context stream → context graph → briefing pipeline → context injector → app context → tool execution → feedback → context stream. Individual modules are tested in isolation but the system-level flow is untested.

**Subgap:**
- F.1: Integration test suite for the full hermeneutic circle
