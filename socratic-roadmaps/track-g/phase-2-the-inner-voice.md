# Phase G.2: "The Inner Voice" — EmbeddingPipeline

**Track:** G — The Local Spine
**Hermeneutic Focus:** Embeddings are the silent inner voice of intelligence — they transform raw text into meaning-vectors that power search, classification, and similarity without ever needing words. By running embeddings locally (Tier 1: always-on, invisible), the OS develops an inner understanding of its user's world that never leaves the machine.

## Current State

No embedding pipeline exists. The intelligence engine calls `llmClient.text()` for all operations — even tasks like "is this briefing relevant?" that could be answered by cosine similarity between embeddings. The `LLMProvider` interface has no `embed()` method. Ollama's `/api/embed` endpoint is unused.

## Architecture Context

```
Tier 1: Always-Local, Silent
┌─────────────────────────────────┐
│  EmbeddingPipeline (singleton)  │
│  ├── embed(text): number[]      │  ← Single text → vector
│  ├── embedBatch(texts): [][]    │  ← Batch for efficiency
│  ├── similarity(a, b): number   │  ← Cosine similarity
│  └── isReady(): boolean         │  ← Model loaded check
│                                 │
│  Model: nomic-embed-text (~275MB) or
│         all-minilm (~45MB)      │
│  VRAM: ~0.5GB                   │
└─────────────────────────────────┘
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `EmbeddingPipeline` is a singleton with `start()` / `stop()` lifecycle
2. `isReady()` returns false before start, true after successful start
3. `embed(text)` returns a `Float32Array` (or `number[]`) of fixed dimension
4. `embedBatch(texts)` returns array of vectors, same length as input
5. `similarity(vecA, vecB)` computes cosine similarity (range -1 to 1)
6. Identical texts produce identical embeddings (deterministic)
7. Semantically similar texts produce similarity > 0.7 (mock model)
8. Semantically unrelated texts produce similarity < 0.3 (mock model)
9. `embed()` throws or returns null when pipeline is not ready
10. Pipeline gracefully degrades when Ollama is unavailable (isReady → false, no crash)

## Socratic Inquiry

**Precedent:** How does `contextGraph` manage its start/stop lifecycle? The embedding pipeline should follow the same singleton + lifecycle pattern. What model format does Ollama expect for `/api/embed`?

**Boundary:** The embedding pipeline is Tier 1 — always local, silent, no user interaction. It should never trigger a cloud request, never show a consent dialog, never appear in the UI. Where does the boundary lie between "embedding for search" and "embedding for classification"? Both use the same vectors.

**Constraint Discovery:** Embedding a single text takes ~5-50ms depending on model size. Batch operations are significantly faster per-item. What batch sizes does Ollama support? What happens if the embedding model isn't pulled yet?

**Tension:** Smaller models (all-minilm, 45MB) load instantly but produce lower-quality embeddings. Larger models (nomic-embed-text, 275MB) are better but consume more VRAM. On a 12GB card running an 8B chat model (~5-6GB), which embedding model fits?

**Synthesis:** How do embeddings connect to the existing context graph? Work streams could be represented as embedding vectors. Briefing relevance could use similarity instead of keyword matching. Entity resolution could use embedding distance. What's the first consumer?

**Safety Gate:** The embedding pipeline must not block the main process. Inference should be async. A slow or stuck Ollama request must not freeze the OS.

## Boundary Constraints

- **Create:** `src/main/embedding-pipeline.ts` (~120-150 lines)
- **Create:** `tests/sprint-3/embedding-pipeline.test.ts` (~10 tests)
- **Read:** `src/main/providers/ollama-provider.ts` (G.1 output — Ollama HTTP patterns)
- **Read:** `src/main/context-graph.ts` lines 1-30 (lifecycle pattern precedent)

## Files to Read

- `socratic-roadmaps/journals/track-g-phase-1.md` (knowledge chain)
- `socratic-roadmaps/contracts/llm-client.md` (interface context)
- Ollama API: `/api/embed` accepts `{ model, input }`, returns `{ embeddings: number[][] }`

## Session Journal Reminder

Before closing, write `journals/track-g-phase-2.md` covering:
- Embedding model choice and rationale
- Dimension size and vector storage format
- How batch vs single embed performance differs
- Planned consumers (which systems will use embeddings)
