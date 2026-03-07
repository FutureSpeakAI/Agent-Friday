# Track G, Phase 1: "The Native Tongue" — OllamaProvider

**Date:** 2026-03-07
**Test count:** 4,017 → 4,028 (+11)
**Safety Gate:** PASSED (tsc clean, 4,028 tests, 99 files)

## What Was Built

A dedicated `OllamaProvider` that talks directly to Ollama's native `/api/*`
endpoints — not the OpenAI-compatible layer — giving full access to
Ollama-specific features like native tool calling. The provider is registered
alongside the existing Anthropic, OpenRouter, and HuggingFace providers and
takes routing priority when the user sets their preferred provider to `'ollama'`.

**Created:**
- `src/main/providers/ollama-provider.ts` — Full LLMProvider implementation
- `tests/sprint-3/ollama-provider.test.ts` — 11 tests (10 planned + 1 bonus)

**Modified:**
- `src/main/intelligence-router.ts` — Added `'ollama'` to ProviderName union
- `src/main/settings.ts` — Added `'ollama'` to preferredProvider type
- `src/main/providers/index.ts` — Registration, routing, re-export
- `tests/providers/initialize-providers.test.ts` — Updated from 3 to 4 providers

## The 10+ Criteria

| # | Criterion | What It Proves |
|---|-----------|----------------|
| 1 | Interface compliance | OllamaProvider has name, isAvailable, complete, stream, listModels, checkHealth |
| 2 | Available when healthy | isAvailable returns true after successful checkHealth via /api/tags |
| 3 | Unavailable when unreachable | isAvailable returns false when Ollama is down (ECONNREFUSED) |
| 4 | Non-streaming completion | complete() POSTs to /api/chat with stream:false, normalizes LLMResponse |
| 5 | Tool format mapping | Unified ToolDefinition maps to Ollama's `{type:'function', function:{...}}` format |
| 6 | Streaming | stream() parses NDJSON lines, yields text chunks, assembles fullResponse |
| 7 | Model listing | listModels() queries /api/tags and returns IDs with parameter sizes |
| 8 | Health check | checkHealth() hits /api/tags, returns true/false, caches for 60s |
| 9 | Provider registration | OllamaProvider is registered in initializeProviders() |
| 10 | Priority over HF-local | When preferred='ollama' and available, Ollama is set as default (before HF-local) |
| 11 | Four-provider count | initializeProviders registers exactly 4 providers (existing test updated) |

## Architecture Decisions

### Native API, Not OpenAI-Compat
Ollama exposes both `/api/chat` (native) and `/v1/chat/completions` (OpenAI-compat).
We chose the native endpoint because:
- Tool calls are first-class in Ollama's native wire format
- Model listing via `/api/tags` provides richer metadata (parameter sizes, families)
- Health checks are simpler (GET /api/tags vs. maintaining a separate endpoint)
- Future access to Ollama-specific features (embeddings at /api/embed, etc.)

### Coexistence with HuggingFace Provider
Both providers target local inference but at different layers:
- HuggingFaceProvider: Cloud HF Inference API or OpenAI-compat local endpoints
- OllamaProvider: Native Ollama API at localhost:11434

The routing in `initializeProviders` checks `'ollama'` before `'local'` so that
when both are available and the user prefers Ollama, it takes priority.

### Health Check Caching
`isAvailable()` is synchronous (per LLMProvider interface) but health checking
requires an HTTP call. Solution: `checkHealth()` performs the async check and
caches the result for 60 seconds. `isAvailable()` reads the cache, returning
false conservatively when no cache exists yet.

### No Provider-Side Queuing
Ollama processes requests sequentially via its internal queue, so the provider
sends requests directly without additional queuing logic.

## Key Type Expansion

Adding `'ollama'` required expanding types in three locations:
1. `ProviderName` union in `intelligence-router.ts`
2. `preferredProvider` field type in `settings.ts` (interface)
3. `getPreferredProvider()` return type in `settings.ts` (method)

The TypeScript compiler caught the mismatch at location 3 when the first two
were updated but the method return type was missed — a good example of the
type system preventing runtime bugs.

## Hermeneutic Reflection

The OllamaProvider completes the provider quartet. Each provider embodies a
different relationship between the user and AI compute: Anthropic (cloud API),
OpenRouter (federated cloud), HuggingFace (hybrid cloud/local), and now
Ollama (native local). The normalize-in/normalize-out pattern — established
by AnthropicProvider and followed faithfully here — means the intelligence
router and all downstream consumers remain oblivious to which backend serves
a given request. The part (each provider's wire format) serves the whole
(unified LLMClient interface) without the whole needing to know the part.
