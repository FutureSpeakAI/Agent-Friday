## Interface Contract: OllamaProvider
**Sprint:** 3, Phase G.1
**Source:** src/main/providers/ollama-provider.ts (to be created)

### Exports
- `OllamaProvider` — class implementing `LLMProvider`

### Implements LLMProvider
| Method | Signature | Description |
|--------|-----------|-------------|
| name | `readonly 'local'` | Provider identifier |
| isAvailable() | `(): boolean` | True when Ollama responds to /api/tags |
| complete(request) | `(LLMRequest): Promise<LLMResponse>` | Chat via /api/chat |
| stream(request) | `(LLMRequest): AsyncGenerator<LLMStreamChunk>` | Streaming via /api/chat |
| listModels() | `(): Promise<OllamaModel[]>` | Query /api/tags |
| checkHealth() | `(): Promise<boolean>` | Ping Ollama |

### Ollama-Specific Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| embed(text) | `(text: string): Promise<number[]>` | Single embedding via /api/embed |
| embedBatch(texts) | `(texts: string[]): Promise<number[][]>` | Batch embed |

### Dependencies
- Requires: Ollama running on `localhost:11434` (configurable via settings)
- Required by: EmbeddingPipeline, OllamaLifecycle, IntelligenceRouter
