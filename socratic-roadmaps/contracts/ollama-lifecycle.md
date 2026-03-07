## Interface Contract: OllamaLifecycle
**Sprint:** 3, Phase G.3
**Source:** src/main/ollama-lifecycle.ts (to be created)

### Exports
- `ollamaLifecycle` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| start() | `(): void` | Begin health polling (30s interval) |
| stop() | `(): void` | Stop polling, clear timers |
| getHealth() | `(): OllamaHealth` | Current health snapshot |
| getAvailableModels() | `(): OllamaModel[]` | Cached model list |
| getLoadedModels() | `(): LoadedModel[]` | Currently in VRAM (/api/ps) |
| pullModel(name) | `(name: string): AsyncGenerator<PullProgress>` | Stream download |
| isModelAvailable(name) | `(name: string): boolean` | Check from cache |
| on(event, cb) | `(event: string, cb: Function): () => void` | Subscribe to events |

### Types
```typescript
interface OllamaHealth { running: boolean; modelsLoaded: number; vramUsedMB: number; vramTotalMB: number; }
interface OllamaModel { name: string; size: number; parameterSize: string; quantization: string; }
interface LoadedModel { name: string; sizeVram: number; }
interface PullProgress { status: string; completed?: number; total?: number; }
```

### Events
- `health-change` — Ollama comes online or goes offline
- `model-loaded` — New model loaded into VRAM
- `model-unloaded` — Model unloaded from VRAM

### Dependencies
- Requires: HTTP access to Ollama endpoints
- Required by: IntelligenceRouter (model availability), Settings UI (model management)
