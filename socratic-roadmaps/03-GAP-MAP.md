# Gap Map — Sprint 3: "The Local Mind"

## Baseline

- **Tests**: 4,017 (all passing)
- **TypeScript errors**: 0
- **Sprint 2 delivered**: 3 tracks (D: The Wiring, E: The Mesh, F: The Proof) — 7 phases, +72 tests
- **Cumulative**: Sprints 1-2 delivered 6 tracks, 16 phases, hermeneutic circle closed end-to-end

## What Exists (Built but Cloud-First)

### LLM Infrastructure (Pre-Sprint 1)
| Module | File | Lines | Status |
|--------|------|-------|--------|
| LLMClient | `llm-client.ts` | ~1,000 | Singleton, provider abstraction, fallback chain |
| IntelligenceRouter | `intelligence-router.ts` | ~1,328 | Task classification, model scoring, circuit breaker |
| IntelligenceEngine | `intelligence.ts` | ~238 | Research/briefing generation via `llmClient.text()` |
| AnthropicProvider | `providers/anthropic-provider.ts` | — | Direct SDK, default provider |
| OpenRouterProvider | `providers/openrouter-provider.ts` | — | HTTP/OpenAI-compatible |
| HuggingFaceProvider | `providers/hf-provider.ts` | — | Dual-mode: HF cloud + local (Ollama) |

### Local Model Support (Partial)
| Feature | Status |
|---------|--------|
| `localInferenceEndpoint` setting | Exists (`http://localhost:11434/v1`) |
| `localModelEnabled` feature flag | Exists (default: `false`) |
| `localModelPolicy` routing | Exists (`disabled` / `conservative` / `all` / `background`) |
| `discoverLocalModels()` | Exists — queries Ollama `/api/tags` |
| OpenAI-compatible completions | Works via HuggingFaceProvider |
| Dedicated Ollama provider | **MISSING** — no native Ollama API support |
| Embedding inference | **MISSING** — no local embedding pipeline |
| Gated cloud consent | **MISSING** — no user approval flow |
| Confidence-based routing | **MISSING** — scoring is static, not output-quality-aware |
| Model lifecycle management | **MISSING** — no pull/load/unload from OS |

### Hardware Target
- **GPU**: RTX 4070 (12GB VRAM)
- **RAM**: 16GB system
- **Capacity**: Tier 1 (~0.5-1GB) + Tier 2 (~5-6GB Q4) simultaneously, ~5GB headroom

## Gaps

### Gap G: "The Local Spine" — Dedicated Ollama provider + embedding pipeline
The existing HuggingFaceProvider routes to Ollama via the OpenAI-compatible `/v1/chat/completions` endpoint, but this misses Ollama-native features: `/api/generate` (faster for non-chat), `/api/embed` (embeddings), `/api/tags` (model discovery), `/api/pull` (model management), and sequential request awareness. A dedicated `OllamaProvider` unlocks Tier 1 (always-local embeddings) and makes Tier 2 first-class.

**Subgaps:**
- G.1: `OllamaProvider` — native Ollama API provider implementing `LLMProvider` interface
- G.2: `EmbeddingPipeline` — local embedding generation via Ollama `/api/embed`
- G.3: `OllamaLifecycle` — model discovery, health monitoring, VRAM-aware loading

### Gap H: "The Gatekeeper" — Confidence routing + gated cloud consent
The intelligence router scores models statically (capability tables), but doesn't assess whether a local model's *output* was good enough. When a local 8B model produces malformed tool calls or low-confidence responses, the system should escalate to cloud — but only with explicit user consent. This requires a confidence assessor, a consent dialog, and policy storage.

**Subgaps:**
- H.1: `ConfidenceAssessor` — evaluate local model output quality (tool call validity, coherence signals)
- H.2: `CloudGate` — consent dialog + policy engine (per-request, per-category, always-allow)
- H.3: Router policy flip — change default from cloud-first to local-first with gated escalation

### Gap I: "The Living Mind" — Integration proof of local-first intelligence
No test verifies the full local-first pipeline: user action → local embedding → local 8B inference → confidence check → (optional gated cloud escalation) → result delivery. The Sprint 2 hermeneutic circle proved context flow; Sprint 3 must prove intelligence flow.

**Subgap:**
- I.1: End-to-end integration test of the local-first intelligence pipeline
