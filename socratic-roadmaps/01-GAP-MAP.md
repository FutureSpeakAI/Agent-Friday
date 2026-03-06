# Gap Map — Agent Friday v2.2.0

**Generated:** 2026-03-06
**Baseline:** 3,769 tests passing, 78 test files, 0 TS errors

## What Exists (The Whole)

### Main Process Backend
- **LLM Client** (`llm-client.ts`, 327 lines) — Multi-provider abstraction with Anthropic, OpenRouter, HuggingFace
- **Intelligence Router** (`intelligence-router.ts`, 1,328 lines) — Task classification, model scoring, cost estimation, local routing
- **Intelligence Engine** (`intelligence.ts`, 238 lines) — Briefing generation from research topics via scheduler
- **Context Stream** (`context-stream.ts`, 537 lines) — Real-time event pipeline from OS/app activity
- **Context Graph** (`context-graph.ts`, 855 lines) — Work stream clustering, entity extraction from context events
- **Context Stream Bridge** (`context-stream-bridge.ts`, 200 lines) — Wires OS events to context stream
- **OS Events** (`os-events.ts`, 568 lines) — Power, session, display, process monitoring
- **File Search** (`file-search.ts`, 367 lines) — Windows Search via PowerShell
- **File Watcher** (`file-watcher.ts`, 295 lines) — fs.watch wrapper with debouncing
- **Memory System** (`memory.ts` + consolidation + episodic) — Semantic memory with consolidation
- **Scheduler** (`scheduler.ts`) — Cron-like task scheduling
- **Notes Store** (`notes-store.ts`, 134 lines) — JSON-backed CRUD
- **Files Manager** (`files-manager.ts`, 89 lines) — File browser with tilde resolution
- **Weather** (`weather.ts`, 187 lines) — Open-Meteo API + geolocation
- **System Monitor** (`system-monitor.ts`, 161 lines) — CPU/memory/disk/process stats

### IPC Layer (14 handler groups registered)
context-stream, context-graph, intelligence, intelligence-router, memory, scheduler, settings, notes, files, weather, system-monitor, os-events, file-search, file-watcher

### Renderer (22 Apps in Registry)
All wired to preload API. 4 fully backed (notes, files, weather, monitor). Others use mock/placeholder data.

## What's Missing (The Gaps)

### Gap 1: Proactive Intelligence Loop (Track A target)
The intelligence engine generates briefings on a schedule, but:
- No trigger from context graph → intelligence (work-stream-aware briefings)
- No priority scoring based on current user activity
- No delivery pipeline to surface briefings in the dashboard
- Briefings are generic; they don't reflect what the user is *currently doing*

### Gap 2: Superpower Execution (Track B target)
The LLM client + providers exist, but:
- No execution delegate that turns LLM tool calls into real OS actions
- No safety pipeline between "LLM wants to run a command" and "command actually runs"
- Connectors are declared in app registry but not wired to any execution backend
- File search, file watcher, OS events exist but aren't routable from LLM tool use

### Gap 3: Cross-App Context (Track C target)
Context graph clusters events into work streams, but:
- No cross-app injection (App A doesn't know what App B just did)
- No renderer-side context subscription (apps can't react to work stream changes)
- No contextual pre-fill (opening Notes doesn't know you were just editing a file)
- Entity references aren't surfaced in app UIs

## Dependency Map

```
Track A (Conductor) ──uses──→ intelligence.ts, context-graph.ts, scheduler.ts
Track B (Sandbox)   ──uses──→ llm-client.ts, os-events.ts, file-search.ts
Track C (Weaver)    ──uses──→ context-graph.ts, context-stream.ts

Track C.2 depends on Track A.1 (briefing contract)
Track C.3 depends on Track B.2 (execution results feed context)
```
