# Contract: ModelOrchestrator

## Module
`src/main/hardware/model-orchestrator.ts`

## Singleton
`ModelOrchestrator.getInstance()`

## Types

```typescript
interface LoadedModel {
  name: string;
  vramBytes: number;
  loadedAt: number;     // timestamp
  lastUsedAt: number;   // timestamp, updated on inference
  purpose: string;
}

interface OrchestratorState {
  tier: TierName;
  loadedModels: LoadedModel[];
  estimatedVRAMUsage: number;   // bytes
  actualVRAMUsage: number | null; // from nvidia-smi, may be null
  vramBudget: number;            // effective VRAM from profile
  vramHeadroom: number;          // budget minus estimated usage
}
```

## Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `loadTierModels` | `(tier: TierName) → Promise<LoadedModel[]>` | Load all core models for tier |
| `getLoadedModels` | `() → LoadedModel[]` | Currently loaded models |
| `getVRAMUsage` | `() → number` | Estimated VRAM consumption |
| `canLoadModel` | `(model: ModelRequirement) → boolean` | Check VRAM budget |
| `loadModel` | `(name: string) → Promise<LoadedModel>` | Load single, evict if needed |
| `unloadModel` | `(name: string) → Promise<void>` | Unload specific model |
| `evictLeastRecent` | `() → Promise<string \| null>` | Unload LRU model, return name |
| `getOrchestratorState` | `() → OrchestratorState` | Full state snapshot |
| `markUsed` | `(name: string) → void` | Update lastUsedAt |

## Events
- `model-loaded` → `LoadedModel`
- `model-unloaded` → `{ name: string }`
- `vram-warning` → `{ usage: number, budget: number }`

## Loading Strategy
1. **Eager:** Embeddings + LLM loaded at startup (core tier models)
2. **Lazy:** Vision model loaded on first vision request
3. **Eviction:** LRU when VRAM budget exceeded
4. **Reconcile:** On startup, check Ollama's loaded models vs expected

## Boundary
- Manages model fleet, not individual inference
- Uses Ollama REST API for load/unload (`POST /api/generate` with `keep_alive`)
- Does NOT download models — assumes already present
- VRAM tracking is estimated, cross-checked with nvidia-smi periodically
