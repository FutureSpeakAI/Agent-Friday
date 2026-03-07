# Track F, Phase 1: "The Proof" — End-to-End Hermeneutic Circle

**Date:** 2026-03-06
**Test count:** 4,007 → 4,017 (+10)
**Safety Gate:** PASSED (tsc clean, 4,017 tests, 98 files)

## What Was Built

Integration test suite validating the complete hermeneutic circle:
OS events → ContextStream → ContextGraph → LiveContextBridge
→ ContextInjector → AppContext → Renderer → Execution → FeedbackWire → ContextStream (cycle)

**File:** `tests/sprint-2/integration/hermeneutic-circle.test.ts`

## The 10 Criteria

| # | Criterion | What It Proves |
|---|-----------|----------------|
| 1 | Context flow | Ambient event → stream → graph creates WorkStream |
| 2 | Briefing flow | Briefing delivery data reaches app context via injector |
| 3 | Injection flow | `getContextForApp()` returns enriched per-app context |
| 4 | Bridge flow | Stream update → debounce → IPC push to renderer |
| 5 | Execution feedback | `feedExecutionResult()` pushes tool-invoke back to stream |
| 6 | Circuit breaker | Rapid feedback is throttled (5s cooldown) |
| 7 | Full circle | Event → graph → bridge → renderer → feedback → stream |
| 8 | Shutdown safety | `stop()` cleans up without throwing |
| 9 | Error isolation | Stream works after graph stop |
| 10 | Destroyed window | Bridge handles destroyed BrowserWindow gracefully |

## Key Debugging Insights

### Singleton Throttle Persistence
The `contextStream` singleton's `lastEmitTime` map persists across tests. With
`vi.useFakeTimers({ now: Date.now() })` resetting to real time each test, the gap
between fake timer epochs was negligible, causing ambient events (8s throttle) to be
silently dropped. **Solution:** monotonically increasing epoch (`testEpoch += 60_000`
per test) ensures 60s gaps, well past all throttle windows.

### Graph Field Names
`contextGraph.handleAmbientEvent()` reads `event.data.activeApp`, not `event.data.app`.
The test helper initially used `app`, causing `getActiveStream()` to return null even
after successful push.

### Bridge Start Ordering
`liveContextBridge.start()` must be called BEFORE `contextStream.push()` for the
bridge's listener to trigger `refreshInjector()`. Tests that pushed before starting
the bridge got stale injector state.

### BriefingDelivery API
No public `deliver()` method exists. Briefings enter via the internal pipeline
(trigger → research → push to private array). Integration test injects directly
into `(briefingDelivery as any).briefings`.

## Hermeneutic Reflection

This phase proved the circle is closed. Every link in the chain — from raw OS
ambient events through graph processing, context enrichment, renderer delivery,
tool execution, and feedback — was validated with real module implementations.
Only Electron IPC and the BrowserWindow boundary were mocked.

The debugging process itself embodied the hermeneutic circle: understanding each
module's behavior (part) required understanding how it interacted with the whole
pipeline, and understanding the pipeline required understanding each module's
invariants (throttle windows, field names, start ordering).
