## Interface Contract: EmbeddingPipeline
**Sprint:** 3, Phase G.2
**Source:** src/main/embedding-pipeline.ts (to be created)

### Exports
- `embeddingPipeline` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| start() | `(): Promise<void>` | Initialize, verify model available |
| stop() | `(): void` | Cleanup |
| isReady() | `(): boolean` | True after successful start |
| embed(text) | `(text: string): Promise<number[]>` | Single text → vector |
| embedBatch(texts) | `(texts: string[]): Promise<number[][]>` | Batch → vectors |
| similarity(a, b) | `(a: number[], b: number[]): number` | Cosine similarity (-1 to 1) |

### Behavior Notes
- Uses Ollama `/api/embed` via OllamaProvider
- Model: nomic-embed-text (768d) or all-minilm (384d) — configurable
- VRAM: ~0.5GB — always loaded alongside Tier 2 chat model
- Deterministic: same input → same vector
- Graceful degradation: isReady() → false when Ollama unavailable

### Dependencies
- Requires: OllamaProvider (G.1)
- Required by: ConfidenceAssessor (optional), ContextGraph (future)
