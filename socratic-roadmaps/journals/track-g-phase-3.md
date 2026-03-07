# Track G, Phase 3: OllamaLifecycle -- The Caretaker

**Date:** 2026-03-07
**Sprint:** 3
**Phase:** G.3
**Status:** COMPLETE

## What Was Built

`src/main/ollama-lifecycle.ts` (~342 lines) -- Singleton that monitors Ollama
health, tracks available/loaded models, and provides VRAM awareness via periodic
polling.

### Key Components
- **HealthStatus** type: { running, modelsLoaded, vramUsed, vramTotal }
- **OllamaLifecycle** singleton with start()/stop() lifecycle
- Periodic polling (30s) of /api/tags and /api/ps
- Event emission on health transitions: healthy, unhealthy, health-change
- Event emission on model transitions: model-loaded, model-unloaded
- pullModel() async generator for streaming /api/pull progress (NDJSON)
- isModelAvailable() quick check from cached model list
- Graceful degradation when Ollama is not installed (no error spam)

### Architecture Decisions
- **30s polling interval:** Balances responsiveness vs HTTP overhead; Ollama
  processes requests sequentially so frequent polling would block inference
- **VRAM from /api/ps:** Ollama reports actual loaded model sizes; total VRAM
  not exposed by Ollama API (left as 0 for future)
- **Health state machine:** Boolean running flag; transitions emit events
- **No model decision-making:** Reports facts only; IntelligenceRouter consumes
- **Singleton pattern:** Matches EmbeddingPipeline (module-level instance export)
- **Event pattern:** Map-based listeners with on(event, cb) returning unsubscribe

## Test Results

`tests/sprint-3/ollama-lifecycle.test.ts` -- 10 tests, all passing

1. Singleton with start()/stop() lifecycle
2. getHealth() returns running: true when Ollama responds
3. getHealth() returns running: false when Ollama unreachable
4. getAvailableModels() returns parsed model list from /api/tags
5. getLoadedModels() returns currently loaded models from /api/ps
6. Health polling emits health-change when Ollama comes online
7. Health polling emits health-change when Ollama goes offline
8. pullModel(name) streams progress events from /api/pull
9. isModelAvailable(name) returns true/false from cache
10. stop() clears polling interval and removes listeners

## Safety Gate

- **tsc --noEmit:** Clean (0 errors)
- **Test count:** 4,038 -> 4,048 (+10 tests)
- **All tests pass:** 101 files, 4,048 tests, 0 failures
