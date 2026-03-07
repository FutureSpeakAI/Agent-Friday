# Phase G.3: "The Caretaker" — OllamaLifecycle

**Track:** G — The Local Spine
**Hermeneutic Focus:** A mind that cannot monitor its own body is fragile. The OllamaLifecycle module gives the OS awareness of its local inference substrate — which models are loaded, how much VRAM they consume, whether the backend is healthy. This self-awareness (knowing the state of the parts) enables the whole system to make intelligent routing decisions.

## Current State

`discoverLocalModels()` in the intelligence router queries `/api/tags` at startup, but:
- No ongoing health monitoring (Ollama could crash mid-session)
- No VRAM tracking (the OS doesn't know if loading another model will OOM)
- No model pull/unload management (user must use CLI)
- No awareness of which models are currently loaded vs available

## Architecture Context

```
OllamaLifecycle (singleton)
├── start()                    → Begin health polling
├── stop()                     → Stop polling, cleanup
├── getHealth(): HealthStatus  → { running, modelsLoaded, vramUsed, vramTotal }
├── getAvailableModels()       → Cached /api/tags result
├── getLoadedModels()          → Currently in VRAM (Ollama /api/ps)
├── pullModel(name)            → Stream /api/pull progress
├── isModelAvailable(name)     → Quick check from cache
└── on('health-change', cb)    → Emit when status changes

Polling: every 30s check /api/tags + /api/ps
Events: 'healthy' | 'unhealthy' | 'model-loaded' | 'model-unloaded'
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `OllamaLifecycle` is a singleton with `start()` / `stop()` lifecycle
2. `getHealth()` returns `{ running: true, ... }` when Ollama responds to `/api/tags`
3. `getHealth()` returns `{ running: false, ... }` when Ollama is unreachable
4. `getAvailableModels()` returns parsed model list from `/api/tags`
5. `getLoadedModels()` returns currently loaded models from `/api/ps`
6. Health polling emits `health-change` event when Ollama comes online
7. Health polling emits `health-change` event when Ollama goes offline
8. `pullModel(name)` streams progress events from `/api/pull`
9. `isModelAvailable(name)` returns true/false from cached model list
10. `stop()` clears polling interval and removes listeners

## Socratic Inquiry

**Precedent:** How does `systemMonitor` (if it exists) poll system state? Follow the same interval + event pattern. How does `contextStream` manage listeners — the `on()` / unsubscribe pattern?

**Boundary:** OllamaLifecycle monitors — it doesn't decide. Routing decisions belong to the intelligence router. The lifecycle module reports facts: "Ollama is running", "llama3.1:8b is loaded", "6.2GB VRAM in use". Who consumes these facts?

**Constraint Discovery:** Ollama's `/api/ps` endpoint shows loaded models and their VRAM usage. On an RTX 4070 (12GB), loading an 8B Q4 model (~5.5GB) + embedding model (~0.5GB) leaves ~6GB. What happens if the user manually loads a 13B model that pushes past VRAM limits?

**Tension:** Frequent health polling (every 5s) gives responsive status updates but adds unnecessary HTTP requests. Infrequent polling (every 60s) misses state changes. What's the right interval, and should it be adaptive (faster when state is changing)?

**Safety Gate:** Health polling must not interfere with active inference requests. Ollama handles requests sequentially — will a health check block an ongoing generation? Test with concurrent mock requests.

**Inversion:** What if the user never installs Ollama? The lifecycle module must handle the "permanently unavailable" state gracefully — no error spam, no repeated connection attempts, just a clean `{ running: false }` status.

## Boundary Constraints

- **Create:** `src/main/ollama-lifecycle.ts` (~130-160 lines)
- **Create:** `tests/sprint-3/ollama-lifecycle.test.ts` (~10 tests)
- **Read:** `src/main/providers/ollama-provider.ts` (G.1 output — HTTP patterns)
- **Read:** `src/main/context-stream.ts` lines 1-40 (event listener pattern)

## Files to Read

- `socratic-roadmaps/journals/track-g-phase-2.md` (knowledge chain)
- `socratic-roadmaps/contracts/llm-client.md` (provider interface)

## Session Journal Reminder

Before closing, write `journals/track-g-phase-3.md` covering:
- Polling interval choice and rationale
- VRAM tracking approach (parsed from /api/ps or estimated)
- Health state machine transitions
- How this module connects to the router (read path, not write path)
