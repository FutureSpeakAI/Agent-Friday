# Phase G.1: "The Native Tongue" — OllamaProvider

**Track:** G — The Local Spine
**Hermeneutic Focus:** The system already speaks to Ollama through a translator (HuggingFaceProvider's OpenAI-compatible shim), but translation loses nuance. A native speaker — a dedicated OllamaProvider — can use Ollama's own language: its native `/api/generate` endpoint, streaming format, and model metadata. Understanding the part (Ollama's API) reshapes the whole (the intelligence architecture becomes local-first).

## Current State

`HuggingFaceProvider` handles local inference by routing to `http://localhost:11434/v1/chat/completions` — Ollama's OpenAI-compatible endpoint. This works but:
- No access to Ollama's native streaming (faster for non-chat)
- No access to `/api/embed` (embeddings blocked)
- No `/api/tags` integration beyond discovery
- No sequential request awareness (Ollama processes one request at a time)
- HF provider conflates two very different backends (cloud HF vs local Ollama)

## Architecture Context

```
                    LLMClient (singleton)
                    ├── AnthropicProvider   → Anthropic API
                    ├── OpenRouterProvider  → OpenRouter API
                    ├── HuggingFaceProvider → HF cloud / Ollama (shim)
                    └── OllamaProvider      → Ollama native API  ← NEW
                         ├── /api/generate  (completions)
                         ├── /api/chat      (chat completions)
                         ├── /api/embed     (embeddings)
                         └── /api/tags      (model discovery)
```

## Validation Criteria

Write failing tests first, then make them pass:

1. `OllamaProvider` implements `LLMProvider` interface (name, isAvailable, complete, stream)
2. `isAvailable()` returns true when Ollama is running (mock HTTP check to `/api/tags`)
3. `isAvailable()` returns false when Ollama is not reachable (timeout/connection refused)
4. `complete()` sends request to `/api/chat` and normalizes response to `LLMResponse`
5. `complete()` maps tool definitions to Ollama's tool format
6. `stream()` yields `LLMStreamChunk` from Ollama's streaming response
7. `listModels()` queries `/api/tags` and returns model IDs with parameter sizes
8. `checkHealth()` returns true/false based on Ollama reachability
9. Provider is registered with `llmClient` during `initializeProviders()`
10. When both HF-local and Ollama providers exist, Ollama takes priority for local routing

## Socratic Inquiry

**Precedent:** How does `AnthropicProvider` implement `LLMProvider`? Follow the same normalize-in/normalize-out pattern. How does `HuggingFaceProvider` currently handle local inference — what can be reused, what must change?

**Boundary:** Should OllamaProvider replace HuggingFaceProvider for local inference, or coexist? If both exist, how does the router choose? The HF provider should remain for HuggingFace cloud; Ollama provider owns `localhost:11434`.

**Constraint Discovery:** Ollama processes requests sequentially. What happens when two parts of the system (briefing pipeline + user chat) request inference simultaneously? Does the provider need a queue, or is Ollama's internal queue sufficient?

**Tension:** Ollama's native `/api/generate` is simpler but doesn't support tool calls. The `/api/chat` endpoint supports tools but uses a different format than Anthropic's. How do we map the unified `ToolDefinition` to Ollama's tool format without losing fidelity?

**Safety Gate:** Adding a new provider must not break any existing tests. The HuggingFaceProvider continues to handle HF cloud inference. Ensure `initializeProviders()` doesn't double-register for local routing.

**Inversion:** What if Ollama isn't installed? The provider must gracefully degrade — `isAvailable()` returns false, router skips it, system falls back to cloud providers. The OS must work fully without Ollama.

## Boundary Constraints

- **Create:** `src/main/providers/ollama-provider.ts` (~150-200 lines)
- **Create:** `tests/sprint-3/ollama-provider.test.ts` (~10 tests)
- **Modify:** `src/main/providers/index.ts` (register OllamaProvider)
- **Read:** `src/main/providers/anthropic-provider.ts` (implementation pattern)
- **Read:** `src/main/providers/hf-provider.ts` (local inference precedent)

## Files to Read

- `socratic-roadmaps/evolution/sprint-2-review.md` (Sprint 2 final state)
- `socratic-roadmaps/contracts/llm-client.md` (LLMProvider interface)
- `src/main/providers/anthropic-provider.ts` (implementation pattern)
- `src/main/providers/hf-provider.ts` (local handling to understand + separate)

## Session Journal Reminder

Before closing, write `journals/track-g-phase-1.md` covering:
- OllamaProvider API surface decisions
- Native vs OpenAI-compatible endpoint choice rationale
- Tool call format mapping approach
- How sequential request handling is addressed
