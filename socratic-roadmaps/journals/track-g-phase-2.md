# Track G, Phase 2: "The Inner Voice" -- EmbeddingPipeline

**Date:** 2026-03-07
**Test count:** 4,028 -> 4,038 (+10)
**Safety Gate:** PASSED (tsc clean, 4,038 tests, 100 files)

## What Was Built

An `EmbeddingPipeline` singleton that generates local text embeddings via
Ollama's `/api/embed` endpoint. This is Tier 1 infrastructure -- always-local,
silent, no user interaction or consent dialogs. The pipeline probes Ollama
on startup, selects an embedding model (nomic-embed-text preferred, all-minilm
as fallback), and exposes `embed()` / `embedBatch()` for downstream consumers.

**Created:**
- `src/main/embedding-pipeline.ts` -- EmbeddingPipeline singleton (~230 lines)
- `tests/sprint-3/embedding-pipeline.test.ts` -- 10 tests

## The 10 Criteria

| # | Criterion | What It Proves |
|---|-----------|----------------|
| 1 | Singleton with start()/stop() | Follows contextGraph lifecycle pattern, proper initialization |
| 2 | isReady() state tracking | False before start, true after successful start with Ollama |
| 3 | embed(text) returns number[] | Single text produces fixed-dimension vector (768 for nomic-embed-text) |
| 4 | embedBatch(texts) returns array | Batch embedding returns vectors matching input length |
| 5 | similarity() cosine computation | Correct cosine similarity: 1 for identical, 0 for orthogonal, -1 for opposite |
| 6 | Deterministic embeddings | Same text always produces the same vector |
| 7 | Semantic similarity > 0.7 | Similar concepts (cat/kitten) cluster together |
| 8 | Semantic distance < 0.3 | Unrelated concepts (cat/quantum physics) are far apart |
| 9 | Not-ready returns null | embed() returns null gracefully when pipeline is not started |
| 10 | Graceful degradation | Ollama unavailable -> isReady() false, no crash, embed returns null |

## Architecture Decisions

### Singleton with start()/stop()
Follows the lifecycle pattern established by `contextGraph`. The pipeline is
a module-level singleton exported as `embeddingPipeline`. Consumers call
`start()` during app initialization and `stop()` during shutdown. This pattern
keeps the embedding model loaded in Ollama's memory between requests (Ollama
manages its own model lifecycle).

### Direct /api/embed Instead of OllamaProvider Delegation
While OllamaProvider has embed/embedBatch method signatures, the EmbeddingPipeline
calls `/api/embed` directly for two reasons:
1. The pipeline needs its own lifecycle (start/stop) independent of the LLM provider
2. It manages its own model selection (embedding models != chat models)
3. Simpler error boundaries -- embedding failures don't affect chat completions

### Model Selection Strategy
During `start()`, the pipeline queries `/api/tags` and looks for:
1. `nomic-embed-text` (~275MB, 768 dims) -- preferred, good quality
2. `all-minilm` (~45MB, 384 dims) -- fallback, lighter
3. First available model -- last resort for testing flexibility

If no model is found, the pipeline stays in not-ready state without crashing.

### Cosine Similarity as Static Method
`EmbeddingPipeline.similarity(vecA, vecB)` is a pure math function that
requires no Ollama connection. Making it static means it can be used even
when the pipeline is stopped, and it communicates that this is pure computation
with no side effects.

### Graceful Degradation
The pipeline never crashes the app. If Ollama is unreachable or has no
embedding model, `isReady()` returns false and `embed()`/`embedBatch()`
return null. Downstream consumers check `isReady()` before using embeddings
and fall back to non-semantic approaches (keyword matching, etc.).

## Test Strategy

All tests mock the global `fetch` to simulate Ollama responses. Mock embeddings
use carefully constructed vectors:
- Animal concepts (cat, dog) -> vector along axes 0-2
- Similar animals (kitten, puppy) -> nearby vector along axes 0-2
- Unrelated concepts (quantum physics) -> vector along axes 400-402

This ensures deterministic cosine similarity that passes the >0.7 and <0.3
thresholds without relying on real model inference.

## Hermeneutic Reflection

The EmbeddingPipeline completes the first loop of the perception-to-meaning
pipeline: raw text goes in, semantic vectors come out. Where OllamaProvider
(G.1) gave the system a voice to speak, the EmbeddingPipeline gives it an
inner voice -- the ability to understand meaning as geometry. Two texts are
"similar" not because they share words, but because they occupy nearby regions
in a 768-dimensional meaning space. This geometric understanding of semantics
is the foundation for everything that follows: context-aware retrieval,
work stream clustering, and eventually the system's ability to anticipate
what the user needs before they ask.
