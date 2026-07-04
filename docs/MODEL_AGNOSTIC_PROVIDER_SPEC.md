# Model-Agnostic Provider Architecture Spec

**Status:** Draft v1.0 · 2026-07-04
**Owner:** Stephen Webster / FutureSpeak.AI
**Drafted by:** Agent Friday (STORM methodology — perspectives: security engineer, cost-conscious operator, OSS self-hoster, end user, maintainer)
**Scope:** `friday-desktop` provider layer — registry, routing, execution, catalog, health, egress, Settings UI

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State & Gap Analysis](#2-current-state--gap-analysis)
3. [Architecture Overview](#3-architecture-overview)
4. [Provider Descriptor Schema v2](#4-provider-descriptor-schema-v2)
5. [Adapter Layer & the LiteLLM Decision](#5-adapter-layer--the-litellm-decision)
6. [Model Discovery & Catalog](#6-model-discovery--catalog)
7. [Routing Intelligence](#7-routing-intelligence)
8. [Provider Health Monitoring](#8-provider-health-monitoring)
9. [Egress Gate Compatibility](#9-egress-gate-compatibility)
10. [API Design](#10-api-design)
11. [Settings UI](#11-settings-ui)
12. [Built-in vs Config-Only: the Ship Matrix](#12-built-in-vs-config-only-the-ship-matrix)
13. [Migration Path](#13-migration-path)
14. [Implementation Phases](#14-implementation-phases)
15. [Security & Privacy Considerations](#15-security--privacy-considerations)
16. [Open Questions](#16-open-questions)
- [Appendix A: Example Descriptors](#appendix-a-example-descriptors)
- [Appendix B: Provider API Cheat Sheet](#appendix-b-provider-api-cheat-sheet)

---

## 1. Executive Summary

Agent Friday already has the *bones* of a model-agnostic system: a declarative
`ProviderRegistry` with JSON-drop registration, a routing layer split from an
execution layer, a capability router, an egress gate that every cloud call
passes through, and a raw OpenAI-compatible transport that can technically
reach OpenRouter today. What it does **not** have:

- **First-class aggregators.** OpenRouter is a disabled template with an empty
  model list. There is no model discovery, no fallback-chain support, no
  rate-limit/usage header handling, no free-tier awareness.
- **HuggingFace, at all.** No descriptor, no adapter, no discovery against the
  Hub's 200K+ models.
- **Multi-provider concurrency.** Exactly ONE OpenAI-compatible endpoint can be
  active at a time (`settings.model_routing.openai_base_url`). You cannot run
  Groq for fast tasks and OpenRouter for breadth simultaneously.
- **Correct model→provider resolution.** `provider_family()` infers provider
  from the model *name*. Any id containing `:` is classified local — which
  misroutes every OpenRouter variant id (`meta-llama/llama-4-maverick:free`,
  `anthropic/claude-sonnet-5:beta`) to Ollama.
- **Objective-driven routing.** No route-by-cost, route-by-speed,
  route-by-privacy beyond the vault→local force. Fallback chains are a static,
  Anthropic-only tuple.
- **Live model metadata.** Context windows, pricing, and modality support are
  hand-maintained dicts that are already stale.

The key strategic insight from surveying the 2026 provider landscape:
**~90% of the providers Stephen wants are already OpenAI-compatible at the
wire level.** OpenRouter, HuggingFace's router, Groq, Together, Fireworks,
Mistral, DeepSeek, xAI, Perplexity, Cohere (compat endpoint), vLLM, LM Studio,
TGI — all speak `POST {base}/v1/chat/completions` with `Authorization: Bearer`.
Friday's existing `_call_openai()` transport is therefore *already* the
universal adapter. What must be built is not a new protocol layer but the
**management plane around it**: per-provider registration, discovery, metadata,
health, budgets, objective routing, and UI.

**LiteLLM recommendation (short version, full analysis in §5): do NOT adopt
LiteLLM as the core adapter. Build the thin native layer (it is ~80% built),
and optionally expose LiteLLM as one more adapter *type* for the long tail
(Bedrock, Vertex, Replicate). Reuse LiteLLM's open pricing/context dataset as
a metadata source without taking the runtime dependency.**

### Design Goals

| # | Goal |
|---|------|
| G1 | Adding a provider whose wire protocol matches a shipped adapter requires **zero code** — a JSON descriptor (file drop or Settings UI) is sufficient. |
| G2 | Any number of providers can be **enabled and used concurrently**; routing addresses providers by name, not by a single global slot. |
| G3 | Model lists, context windows, pricing, and modalities are **discovered from provider APIs** and cached with TTL; static lists become fallbacks. |
| G4 | Routing can optimize for **capability, cost, speed, or privacy**, with config-driven cross-provider fallback chains. |
| G5 | **Every** cloud provider passes the egress gate; local classification is earned (registry type + loopback/LAN base_url), never inferred from a name. |
| G6 | Provider health (latency, error rate, availability) is **measured, not assumed**, and feeds both routing and the UI. |
| G7 | Existing installs migrate **losslessly**; legacy settings keys keep working for two minor versions. |

### Non-Goals

- Replacing the Anthropic-native and Gemini-native adapters (they carry
  first-party features — native tool use, Live voice, Veo/Lyria — that the
  OpenAI shape cannot express).
- Building a proxy/server mode (LiteLLM-proxy style). Friday is a desktop
  agent; the provider layer stays in-process.
- Provider-side BYOK passthrough (OpenRouter BYOK, HF billing delegation) —
  noted in §16 as future work.
- Training/fine-tuning APIs. Inference only.

---

## 2. Current State & Gap Analysis

### 2.1 What exists (verified against source, 2026-07-04)

| Component | File | Role today |
|---|---|---|
| Provider registry | `src/agent_friday/services/provider_registry.py` | Declarative descriptors. 6 shipped providers (anthropic, openai, ollama-local, google-gemini, local-voice-lite, nvidia-nemo) + 5 disabled templates (openrouter, together, groq, fireworks, custom). JSON drop-in at `~/.friday/providers/*.json`. |
| Routing layer | `src/agent_friday/routing/model_router.py` | WHERE decisions. Modes cloud_only/smart/local_preferred/local_only; vault tier forcing; `provider_family()` name heuristics; `_apply_cloud_provider()` retag; static `CLOUD_MODEL_FALLBACK_CHAIN` (Anthropic-only); in-memory `CostTracker`. |
| Execution layer | `src/agent_friday/services/model_router.py` | HOW. `_call_claude` (anthropic SDK), `_call_ollama` (daemon, shared `_oai_agentic_loop`), `_call_openai` (raw `requests` → `{base}/chat/completions`, single global endpoint from `model_routing.openai_*`, OpenRouter etiquette headers already present), `_generate_text` (3-rung fallback ladder), `_seal_or_block` egress wrapper on every cloud call. |
| Model catalog | `src/agent_friday/services/model_catalog.py` | `build_catalog()` → `GET /api/models`. Registry statics + live Ollama merge. Role grouping, dedupe, `needs_key` hints. No remote discovery. |
| Capability router | `src/agent_friday/services/capability_router.py` | capability → {provider, model} from `settings.capability_routing`. 9+ capabilities incl. reasoning, subagent, creative_image/video/music, voice, asr, tts, embedding, local. |
| Provider health | `src/agent_friday/services/provider_health.py` | Shallow (key present) / deep (GET `{base}/models`) checks, 20s TTL cache. No latency/error-rate stats. |
| Egress gate | `src/agent_friday/services/egress_gate.py` | `seal_outbound(payload, provider)` before every cloud HTTP call. Fail-closed. `_LOCAL_PROVIDERS = {"ollama", "local"}` hardcoded. |
| Settings | `src/agent_friday/core/__init__.py` | `DEFAULT_SETTINGS.model_routing` (mode, cloud_provider, openai_base_url/model/key, vault policy), `capability_routing` (canonical), flat `*_model` mirrors via `_sync_capability_routing()`, `providers` dict (enabled/base_url only, never secrets). |
| Provider API | `src/agent_friday/routes/platform.py` | `GET/POST /api/providers`, `DELETE /api/providers/<name>`, `/api/providers/templates`, `/api/providers/health`, `POST/DELETE /api/providers/<name>/key` (encrypted credential store), `/api/providers/<name>/reload-key`, `/api/capabilities`, `/api/health/full`. |
| Credentials | `src/agent_friday/services/credential_store.py` | Encrypted at-rest key storage; env bootstrap at launch. |
| Cost/budget | `src/agent_friday/services/cost_meter.py`, `budget_enforcer.py`, `routes/costs.py`, `routes/budget_policy.py` | Per-call metering (provider, model, usage, duration) + budget ceilings. |
| Dependencies | `pyproject.toml` | `anthropic>=0.40`, `google-genai>=1.0`, `requests`. No `openai` SDK (compat path is raw requests). `litellm` 1.82.3 present in venv only as a transitive dep of `headroom-ai`. |

### 2.2 Gap analysis

| # | Gap | Evidence | Severity |
|---|---|---|---|
| GAP-1 | OpenRouter is template-only: no discovery, no `models[]` fallback array, no rate-limit/usage handling, no `:free` awareness | `PROVIDER_TEMPLATES["openrouter"]` ships `enabled: False, models: []` | High |
| GAP-2 | No HuggingFace support in any form | no descriptor, no adapter | High |
| GAP-3 | Single global OpenAI-compat slot — one endpoint at a time | `_call_openai` reads `model_routing.openai_base_url` | High |
| GAP-4 | `provider_family()` misroutes aggregator ids: `:` → local, `/` unrecognized | `routing/model_router.py` — the docstring itself flags the caveat | **Critical** (egress-adjacent) |
| GAP-5 | Fallback chain is static + Anthropic-only | `CLOUD_MODEL_FALLBACK_CHAIN` tuple | Medium |
| GAP-6 | No cost/speed/privacy routing objectives | `route()` handles mode + vault only | Medium |
| GAP-7 | Model metadata (price, context, modality) hand-maintained and stale | `CLOUD_COST_PER_1K`, `cost_per_1k` blended per-1k numbers | Medium |
| GAP-8 | Health has no latency/error-rate/availability memory | `provider_health.py` returns point-in-time status only | Medium |
| GAP-9 | Egress local-classification is a hardcoded set, not registry-driven | `_LOCAL_PROVIDERS = {"ollama", "local"}` | Medium (currently safe because conservative, but blocks legit local providers like LM Studio from bypass and — worse — a descriptor typed `ollama` pointing at a REMOTE url would classify local) |
| GAP-10 | No streaming on the OpenAI-compat path | `_call_openai` posts non-streaming | Low (UX) |
| GAP-11 | No per-provider budget caps | budget_enforcer is global/per-feature | Low |
| GAP-12 | Provider descriptor has no schema_version / validation | `PROVIDER_SCHEMA_KEYS` is a bare key set, unused for validation | Low |

---

## 3. Architecture Overview

### 3.1 Layer diagram (target state)

```
                    ┌──────────────────────────────────────────────────────────┐
                    │                        UI (app.html)                     │
                    │  Model pickers · Providers panel · Model browser · Costs │
                    └───────┬──────────────────────────────────┬───────────────┘
                            │ GET /api/models                  │ /api/providers/*
                            ▼                                  ▼
┌──────────────────────────────────────┐   ┌─────────────────────────────────────┐
│           MODEL CATALOG              │   │        PROVIDER MANAGEMENT API      │
│  services/model_catalog.py           │   │  routes/platform.py                 │
│  registry + DISCOVERY CACHE merge    │   │  add/remove/test/key/budget/models  │
└──────────────┬───────────────────────┘   └───────────────┬─────────────────────┘
               │ reads                                     │ writes
               ▼                                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         PROVIDER REGISTRY (descriptor store)                 │
│  services/provider_registry.py                                               │
│  built-in descriptors  +  ~/.friday/providers/*.json  +  Settings-created    │
│  schema v2: adapter, auth, discovery, pricing, classification, budgets       │
└───────┬───────────────────────────┬──────────────────────────┬───────────────┘
        │                           │                          │
        ▼                           ▼                          ▼
┌───────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│  MODEL DISCOVERY  │   │   PROVIDER HEALTH    │   │      MODEL RESOLVER      │
│  per-adapter      │   │  rolling latency /   │   │  model id → provider     │
│  fetchers + TTL   │   │  error rate / avail  │   │  (registry-first; kills  │
│  cache on disk    │   │  ring buffers        │   │  the name heuristics)    │
└─────────┬─────────┘   └──────────┬───────────┘   └────────────┬─────────────┘
          │                        │                            │
          └──────────────┬─────────┴────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      ROUTING LAYER (WHERE) — routing/model_router.py         │
│  vault/privacy force-local  →  objective scoring (cost·speed·quality)        │
│  →  capability filter  →  fallback chain assembly  →  RouteDecision          │
│     RouteDecision = {provider_name, model, chain[], flags}                   │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    EXECUTION LAYER (HOW) — services/model_router.py          │
│                                                                              │
│   dispatch by ADAPTER TYPE of RouteDecision.provider_name:                   │
│   ┌────────────┬────────────┬──────────────────┬──────────────┬───────────┐  │
│   │ anthropic  │  google    │ openai-compatible│  litellm     │  ollama   │  │
│   │ (SDK)      │  (SDK)     │ (requests, /v1)  │  (optional)  │  (daemon) │  │
│   └─────┬──────┴─────┬──────┴────────┬─────────┴──────┬───────┴─────┬─────┘  │
│         │            │               │                │             │        │
│         └────────────┴───────┬───────┴────────────────┘             │        │
│                              ▼                                      │        │
│               ┌──────────────────────────────┐                      │        │
│               │   EGRESS GATE (fail-closed)  │            (local — bypass,   │
│               │   _seal_or_block(payload,    │             data stays on     │
│               │   provider) — ALL cloud      │             device)           │
│               └──────────────┬───────────────┘                      │        │
│                              ▼                                      ▼        │
│                        cloud HTTPS                            localhost      │
└──────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                     ┌──────────────────────────┐
                     │ COST METER + HEALTH FEED │  every call reports
                     │ (provider, model, usage, │  {latency, ok, cost}
                     │  latency, cost)          │  back to health + budgets
                     └──────────────────────────┘
```

The two-layer split (routing decides WHERE, execution decides HOW, egress gate
decides WHAT the provider may see) is **preserved exactly** — this spec widens
each layer without moving the boundaries. The egress gate remains the single
enforcement point (R3 invariant from the Fable 5 adversarial review).

### 3.2 Request flow (chat turn, target state)

```
 user turn
    │
    ▼
 1. classify: vault/sensitivity tier ──── TIER_2/3 ──► force local chain only
    │ (public)
    ▼
 2. resolve objective: settings.routing_policy.objective
    (quality | cheapest | fastest | private | balanced)
    │
    ▼
 3. candidate set = catalog models matching {capability, modalities, tools}
    filtered by: provider enabled + available + healthy + within budget
    │
    ▼
 4. score candidates:  score = w_q·quality + w_c·(1-cost̂) + w_s·(1-latencŷ)
    weights from objective preset; pinned model (UI picker) short-circuits
    │
    ▼
 5. RouteDecision {provider: "groq", model: "llama-4-maverick",
                   chain: [groq/…, openrouter/…, ollama-local/…]}
    │
    ▼
 6. execute via adapter for provider.type
    │  ├─ success ──► meter cost + record health ──► reply
    │  └─ failure (timeout / 429 / 5xx / budget) ──► record health,
    │        advance chain ──► next provider (respecting egress class:
    │        a "private" objective NEVER advances to a cloud rung)
    ▼
 7. all rungs exhausted ──► structured error with per-rung reasons
```

### 3.3 Discovery flow

```
 boot / TTL expiry / user clicks ⟳ Refresh
    │
    ▼
 for each enabled provider with discovery.mode == "api":
    │   GET {base_url}{discovery.endpoint}     (adapter-specific parser)
    │       openrouter: /api/v1/models         → id, ctx, pricing, modalities,
    │                                             supported_parameters (tools!)
    │       hf-router:  /v1/models             → id, providers[]
    │       openai-compat: /v1/models          → id (+ together/groq extras)
    │       anthropic:  /v1/models             → id, display_name
    │       google:     /v1beta/models         → name, methods, token limits
    │       ollama:     /api/tags (live daemon)
    │
    ▼
 normalize → ModelInfo {id, label, context_window, max_output,
                        modalities[], supports_tools, price_in, price_out,
                        free: bool, deprecated: bool}
    │
    ▼
 enrich (first hit wins):
    1. provider-reported metadata (OpenRouter is authoritative for its ids)
    2. descriptor model_meta overrides (user's word beats the wire)
    3. LiteLLM open dataset (model_prices_and_context_window.json, vendored)
    4. _humanize() inference (today's fallback, unchanged)
    │
    ▼
 write ~/.friday/cache/models/{provider}.json  {fetched_at, ttl_s, models[]}
    │
    ▼
 catalog build_catalog() merges: registry statics ∪ discovery cache ∪ live
 Ollama — dedupe rules unchanged (available first, one entry per id per role)
```

Cache TTL default: 24h for cloud lists, 30s for the Ollama daemon (existing
behavior). Discovery is **never** on the chat hot path: a stale cache serves
until the refresh daemon thread replaces it. Failures keep the last good
cache (`stale-while-revalidate`), so offline boots still show models —
dimmed by health, not absent.

---

## 4. Provider Descriptor Schema v2

The descriptor is the contract that makes G1 (zero-code providers) true:
**a provider is config-only if and only if its wire protocol matches a shipped
adapter.** v2 is a strict superset of today's descriptor — every existing
JSON in `~/.friday/providers/` remains valid (missing fields get defaults,
`schema_version` absent ⇒ 1 ⇒ migrated on read).

### 4.1 Schema (annotated JSON)

```jsonc
{
  "schema_version": 2,

  // ── Identity ──────────────────────────────────────────────────────────
  "name": "openrouter",              // unique key, [a-z0-9-]+
  "label": "OpenRouter",             // UI display name
  "adapter": "openai-compatible",    // WHICH SHIPPED TRANSPORT SPEAKS TO IT:
                                     //   anthropic | google | openai-compatible
                                     //   | ollama | litellm | local-voice | nemo-local
                                     // (v1 field `type` aliases to `adapter`)

  // ── Wire ──────────────────────────────────────────────────────────────
  "base_url": "https://openrouter.ai/api/v1",
  "auth": {
    "type": "env_var",               // env_var | none
    "key": "OPENROUTER_API_KEY",     // env var; encrypted store fallback by
                                     //   provider name (credential_store)
    "key_aliases": ["OR_API_KEY"],   // extra env names accepted (HF_TOKEN vs
                                     //   HUGGINGFACE_API_KEY problem)
    "header": "Authorization",       // default "Authorization"
    "scheme": "Bearer"               // default "Bearer"; "" = raw key,
                                     //   "x-api-key" style via header override
  },
  "extra_headers": {                 // static headers sent on every request
    "HTTP-Referer": "https://futurespeak.ai",
    "X-Title": "Agent Friday"
  },
  "network": {
    "timeout_s": 180,
    "retries": 2,                    // transport-level retries (429/5xx, jittered)
    "rate_limit_style": "openrouter" // none | openrouter | anthropic — which
                                     //   response headers to parse for backoff
  },

  // ── Egress classification (SECURITY — see §9) ─────────────────────────
  "classification": "cloud",         // cloud | local. DEFAULT: cloud.
                                     // "local" is only honored when base_url
                                     // resolves to loopback/RFC1918 (§9.2).

  // ── Model discovery (§6) ──────────────────────────────────────────────
  "discovery": {
    "mode": "api",                   // api | static | merge (api ∪ statics)
    "endpoint": "/models",           // relative to base_url
    "parser": "openrouter",          // openai | openrouter | hf-router |
                                     //   anthropic | google | ollama
    "ttl_s": 86400,
    "max_models": 0,                 // 0 = unlimited; HF sets a cap + filters
    "filters": {}                    // parser-specific (e.g. HF: warm-only)
  },

  // ── Static model data (fallback + overrides) ──────────────────────────
  "models": ["anthropic/claude-sonnet-5", "meta-llama/llama-4-maverick"],
  "model_format": "org/model",       // plain | org/model — display + dedupe hint
  "model_meta": { /* unchanged from v1 — per-model label/short/roles/modalities;
                     v2 adds: context_window, max_output, price_in, price_out,
                     supports_tools, free */ },

  // ── Capabilities & UI roles (unchanged semantics from v1) ─────────────
  "capabilities": ["tools", "vision"],
  "roles": ["orchestrator", "subagent"],

  // ── Pricing (§6.4) — replaces blended cost_per_1k ─────────────────────
  "pricing": {
    "source": "discovery",           // discovery | dataset | static
    "currency": "USD",
    "static": {                      // per-1M tokens, in/out split
      "some-model": {"in": 3.00, "out": 15.00}
    }
  },
  "cost_per_1k": {},                 // v1 field — read as blended fallback,
                                     //   written no more

  // ── Budgets (§10, enforced by budget_enforcer) ────────────────────────
  "budget": {
    "daily_usd": null,               // null = no cap
    "monthly_usd": null,
    "on_exceed": "block"             // block | warn | degrade (drop to chain)
  },

  // ── Aggregator features (adapter consults only what it understands) ───
  "features": {
    "fallback_models_param": true,   // OpenRouter `models: []` body param
    "usage_accounting": true,        // OpenRouter `usage: {include: true}`
    "provider_routing": {},          // OpenRouter `provider: {...}` prefs
    "model_suffixes": [":free", ":nitro", ":floor", ":thinking"]
  },

  "enabled": true,
  "priority": 50                     // resolver tie-break, lower wins (§7.2)
}
```

### 4.2 Field rules

| Field | Required | Default | Validation |
|---|---|---|---|
| `name` | yes | — | `^[a-z0-9][a-z0-9-]{1,63}$`, unique |
| `adapter` | yes | `openai-compatible` | must be a registered adapter type; unknown ⇒ descriptor rejected with actionable error (never silently ignored, unlike today's bare `except`) |
| `base_url` | for network adapters | — | http(s) URL; https required when `classification: cloud` |
| `auth.type` | yes | `env_var` | `env_var \| none` |
| `classification` | no | **`cloud`** | `local` requires loopback/RFC1918 base_url at load time AND at call time (§9.2) |
| `discovery.mode` | no | `static` | api-mode requires `endpoint` + `parser` |
| `pricing` | no | `{source: "dataset"}` | per-1M floats ≥ 0 |
| `priority` | no | 50 | 0–100 |

Validation lives in `provider_registry.validate_descriptor(data) -> (ok, errors[])`
and runs on: JSON file load (bad file ⇒ skipped + logged + surfaced in
`/api/health/full`, today it's a silent `pass`), `POST /api/providers`, and
`POST /api/providers/validate` (dry-run for the UI).

### 4.3 YAML equivalence

Descriptors may also be `*.yaml` in `~/.friday/providers/` (PyYAML is already
a core dep). Same schema; JSON remains canonical for `add_provider()` writes.

---

## 5. Adapter Layer & the LiteLLM Decision

### 5.1 Adapter contract

An adapter is a Python class registered in `ADAPTERS: dict[str, ProviderAdapter]`
(new module `src/agent_friday/services/provider_adapters.py`):

```python
class ProviderAdapter(Protocol):
    adapter_type: str                       # "openai-compatible", …

    def chat(self, provider: dict, model: str, messages: list,
             system: str | None, tools: list | None,
             max_tokens: int, temperature: float | None,
             stream_cb=None) -> AdapterResult: ...
    def list_models(self, provider: dict) -> list[ModelInfo]: ...   # discovery
    def probe(self, provider: dict) -> HealthProbe: ...             # deep health

# AdapterResult = {text, tool_calls[], usage{in,out}, raw, finish_reason}
```

Invariants every adapter MUST hold (enforced by a shared base + tests):

1. **Egress**: assemble the full payload, then pass it through
   `_seal_or_block(payload, provider_name)` before ANY network write when the
   provider's effective classification is cloud. Local-classified adapters
   skip the gate but MUST verify loopback/LAN at call time (§9.2).
2. **Metering**: report `(provider_name, model, usage, duration_ms)` to
   `cost_meter.meter()` on success AND to health on success/failure.
3. **Tool loop**: OpenAI-shaped adapters reuse the existing shared
   `_oai_agentic_loop` (vault gate + `_execute_tool` governance intact).
   Anthropic keeps its native loop.
4. **No secrets in errors**: adapter exceptions carry provider name + status
   code, never headers/keys.

Five adapters ship: `anthropic` (SDK), `google` (SDK), `openai-compatible`
(the workhorse — today's `_call_openai` generalized to take a provider
descriptor instead of the global settings slot), `ollama` (existing manager),
and `litellm` (optional, §5.3). `local-voice` / `nemo-local` remain
engine-backends outside the chat adapter set, unchanged.

### 5.2 Should we use LiteLLM as the universal adapter? — **No. Recommendation: native-first, LiteLLM as an optional escape hatch.**

**What LiteLLM would buy us**

| Benefit | Weight |
|---|---|
| 100+ providers day one (incl. Bedrock, Vertex, Azure, Replicate, Sagemaker) | High if we needed them — we need ~12, and 11 are OpenAI-compatible already |
| Maintained provider quirks (auth schemes, param mapping, streaming shapes) | Medium |
| Built-in Router: retries, fallbacks, cooldowns, load balancing | Medium — but Friday needs custom logic here anyway (vault forcing, egress classes, budget gates) |
| Cost map (`model_prices_and_context_window.json`) | High — and available WITHOUT the runtime dep (MIT, plain JSON) |

**What it would cost us**

| Cost | Why it bites Friday specifically |
|---|---|
| Heavy dependency graph (openai SDK, tokenizers/tiktoken, httpx, jinja2, aiohttp…) becomes a CORE dep | Friday's core deps are deliberately minimal; `[all]` already fights install-size battles on Windows |
| Fast-moving releases, frequent breaking changes | friday-desktop pins loosely; a v1.x bump breaking chat is a sovereignty failure for a local-first agent |
| Abstraction hides the HTTP boundary | The egress gate's fail-closed guarantee (R2/R3) is auditable today because Friday owns the exact line where payload → network. LiteLLM inserts its own retry/fallback/logging machinery (with its own network callbacks) between seal and send. Sealing before `litellm.completion()` is possible, but retries and provider fallbacks INSIDE LiteLLM would resend sealed-once payloads under conditions Friday no longer controls, and its logging integrations are an exfil surface that must be audited every upgrade. |
| Duplicate subsystems | Friday already owns cost metering, budgets, credential storage, health, and an agentic tool loop wired to Ring governance. LiteLLM's equivalents would either fight ours or rot disabled. |
| Wrong default posture | LiteLLM optimizes for "any model, however". Friday optimizes for "the user's models, provably gated". |

**Decision**

1. **Primary path (build, ~small):** generalize the existing
   `_call_openai()` into the `openai-compatible` adapter driven by descriptors.
   This single adapter covers OpenRouter, HuggingFace router, Groq, Together,
   Fireworks, Mistral, DeepSeek, xAI, Perplexity, Cohere-compat, vLLM,
   LM Studio, TGI, Azure-OpenAI-compat. The marginal code over today is
   parameterization + discovery parsers, not protocol work.
2. **Escape hatch (config-only):** ship a `litellm` adapter type that is
   import-guarded (soft dep, `pip install agent-friday[litellm]`). A descriptor
   with `"adapter": "litellm"` + `"litellm_prefix": "bedrock/"` routes through
   `litellm.completion()` — sealed BEFORE the call, LiteLLM's own retries
   disabled (`num_retries=0`), fallbacks disabled, telemetry/logging callbacks
   disabled. This covers Bedrock/Vertex/Replicate/Cohere-native for users who
   want them, without making LiteLLM load-bearing for anyone else.
3. **Data reuse (free):** vendor a snapshot of LiteLLM's
   `model_prices_and_context_window.json` (MIT) at
   `src/agent_friday/data/model_prices.json` as enrichment tier 3 (§3.3), with
   an optional weekly refresh fetch. We take the dataset, not the dependency.

This keeps the audited egress line ours, keeps core installs lean, and still
gives Stephen "anything else that makes sense" through configuration.

---

## 6. Model Discovery & Catalog

### 6.1 Per-provider discovery matrix

| Provider | Endpoint | Auth needed | What we get | Parser notes |
|---|---|---|---|---|
| OpenRouter | `GET /api/v1/models` | none | id, name, description, `context_length`, `pricing.prompt/completion` (per token), `architecture.input_modalities/output_modalities`, `supported_parameters` (contains `"tools"` — authoritative tool-support signal), `top_provider.max_completion_tokens` | Richest source in the industry. `:free` suffix ⇒ `free: true`. Variants (`:nitro`, `:floor`, `:thinking`) kept as distinct ids. |
| HuggingFace router | `GET https://router.huggingface.co/v1/models` | HF_TOKEN | chat-capable ids + backing `providers[]` per model | Serverless "Inference Providers" catalog only — see §12.2 for the Hub-search strategy covering the 200K long tail. |
| Anthropic | `GET /v1/models` | key | id, display_name, created_at | No pricing on the wire ⇒ dataset tier. |
| Google | `GET /v1beta/models` | key | name, supported methods, `inputTokenLimit`/`outputTokenLimit` | Filter to `generateContent`-capable for chat roles; keep Veo/Lyria/Live ids under existing model_meta roles. |
| Groq / Together / Fireworks / Mistral / DeepSeek / xAI / Cohere-compat | `GET {base}/models` | key | ids (Together adds context/pricing extras; others ids only) | Generic `openai` parser; enrich from dataset. |
| Perplexity | — (no models endpoint) | — | — | `discovery.mode: static` — showcase for statics-only cloud providers. |
| Ollama | `/api/tags` live | none | installed tags + sizes | Unchanged (30s effective TTL via manager). |

### 6.2 Cache

- Location: `~/.friday/cache/models/{provider}.json`
  `{schema: 1, provider, fetched_at, ttl_s, etag?, models: [ModelInfo…]}`
- Refresh triggers: boot (async, off hot path), TTL expiry (daemon sweep),
  `POST /api/providers/<name>/models/refresh`, provider enable/key change.
- Failure policy: stale-while-revalidate; a provider with an expired cache and
  a failing discovery keeps serving the stale list with
  `catalog_stale: true` on its entries (UI shows a subtle ⚠ on the group).
- The 200K-model problem: discovery NEVER dumps a full aggregator catalog into
  the picker. `discovery.max_models` caps what is cached (OpenRouter: all
  ~300+, fine; HF: top ~100 warm/trending). Beyond the cap, models are reached
  via **search-on-demand** (§10 `/api/models/search` proxies the provider's
  search API and any hit can be pinned, which persists it into `models[]`).

### 6.3 ModelInfo normalization

```jsonc
{
  "id": "meta-llama/llama-4-maverick",
  "provider": "openrouter",
  "label": "Llama 4 Maverick",
  "context_window": 1048576,
  "max_output": 16384,
  "modalities": ["text", "vision", "tools"],
  "supports_tools": true,
  "price_in": 0.20,            // USD per 1M input tokens
  "price_out": 0.60,           // USD per 1M output tokens
  "free": false,
  "deprecated": false,
  "roles": ["orchestrator", "subagent"],   // model_meta override or inferred
  "source": "discovery"        // discovery | static | pinned
}
```

`build_catalog()` keeps its exact output shape (`roles`, `models`, `providers`,
`voice_engines`) so the UI contract is untouched; entries simply gain the new
metadata fields. Picker-hygiene invariants (dedupe per id, voice engines not
pickable, available-first sort) carry over verbatim.

### 6.4 Pricing service

`services/pricing.py` — one lookup used by cost_meter, routing, and the UI:

```
price(provider, model) -> {in_per_1m, out_per_1m, source} | None
  1. discovery cache (OpenRouter/Together report live prices)
  2. descriptor pricing.static
  3. vendored LiteLLM dataset (matched on bare id and on org/id)
  4. None → cost_meter logs tokens with cost=null (unknown ≠ free);
     routing treats unknown price as median-of-candidates, never as 0
```

Replaces both static dicts (`CLOUD_COST_PER_1K` in routing,
`cost_per_1k` blended per-provider) — see migration §13.4. `CostTracker`
and `cost_meter` switch to in/out split pricing (they already capture both
token counts).

---

## 7. Routing Intelligence

### 7.1 Routing policy (new settings block)

```jsonc
"routing_policy": {
  "objective": "balanced",       // quality | cheapest | fastest | private | balanced
  "max_cost_per_call_usd": null, // hard filter when set
  "latency_slo_ms": null,        // p95 gate against provider health stats
  "allow_free_tiers": true,      // permit :free variants (rate-limit tolerant)
  "pin_overrides_objective": true, // an explicit UI model pick always wins
  "fallback_chains": {
    // per-capability, ordered, provider-qualified. "::" separates provider
    // from model because "/" and ":" both occur INSIDE aggregator model ids.
    "reasoning": [
      "anthropic::claude-sonnet-5",
      "openrouter::anthropic/claude-sonnet-5",
      "groq::llama-4-maverick",
      "ollama-local::gemma3:12b"
    ],
    "subagent": ["anthropic::claude-sonnet-4-6", "openrouter::deepseek/deepseek-chat", "ollama-local::gemma3:4b"]
  }
}
```

Objective presets set the scoring weights (w_quality, w_cost, w_speed) and
hard constraints:

| Objective | Behavior |
|---|---|
| `quality` | Highest quality tier that supports the required capability; cost ignored up to budget caps. (Today's implicit behavior — remains the default for `orchestrator`.) |
| `cheapest` | Min `price_in+price_out` among candidates that satisfy capability + context fit; `:free` variants first when `allow_free_tiers`. |
| `fastest` | Rank by rolling p50 latency from health stats; cold providers assume descriptor-declared class (LPU/local fast, aggregator medium). Groq/local win in practice. |
| `private` | Candidate set = providers with effective classification `local` ONLY. No cloud rung may ever be appended to the chain. Equivalent to vault forcing, available on demand ("this is sensitive"). |
| `balanced` | Weighted blend (default 0.5/0.25/0.25) with budget + SLO filters. |

Quality signal: ordinal `quality_tier` (frontier=4, strong=3, mid=2, small=1)
derived from dataset + descriptor override — deliberately coarse; Friday does
not pretend to benchmark.

### 7.2 Model resolver (kills GAP-4)

`resolve_model(model_id, hint=None) -> (provider_name, model_id)` replaces
name-only `provider_family()` as the authority:

```
1. explicit "provider::model" → that provider (error if disabled)
2. exact id match in exactly one enabled provider's catalog → it
3. exact id match in several → precedence: classification local first,
   then ascending `priority`, then declaration order   (deterministic)
4. id contains "/" → enabled aggregators (openrouter, huggingface) whose
   cache contains it; else settings.default_aggregator; else error
5. id contains ":" with no "/" → ollama-local iff the daemon lists it
6. provider_family() heuristics (existing) as last resort
7. unresolved → RoutingError surfaced to UI ("model X belongs to no enabled
   provider") — NEVER a silent fallback to a different brain
```

Rule ordering fixes both current hazards: `meta-llama/llama-4-maverick:free`
hits rule 4 (aggregator) before any `:`-means-local guess, and a local model
named `claude-x:latest` hits rule 2/5 (registry/daemon truth) before the
`claude` prefix heuristic — the existing `_is_registry_local()` egress
protection becomes a consequence of the resolver instead of a patch.
`provider_family()` remains exported for callers that only need a family
label, but no dispatch decision may use it directly (lint rule).

### 7.3 Fallback chain semantics

- Chains are per-capability; a routed request assembles: `[pinned or scored
  winner] + policy chain (dedup) + [last-resort local]`.
- Rung advance on: connect/timeout errors, 401/403 (mark provider `missing`),
  408/429 (respect `Retry-After`/rate-limit headers when
  `rate_limit_style` knows them; retry same rung once, then advance),
  5xx, budget `on_exceed: degrade`, and egress gate REDACT-refusals never
  advance (a block is a block — R3).
- Privacy invariant: when the request is vault-tiered or objective=`private`,
  cloud rungs are stripped from the assembled chain BEFORE execution, not
  skipped at failure time.
- OpenRouter native fallback (`models: []` body param) is used WITHIN its rung
  when `features.fallback_models_param` — one HTTP call covers N model
  fallbacks server-side; Friday's chain then only handles provider-level
  failure. Cost is attributed to the model OpenRouter reports back
  (response `model` field), not the requested one.
- Every rung outcome is health-recorded and orb-visible (`process_update`
  label shows "falling back: groq → openrouter").

### 7.4 Capability routing (unchanged surface, wider reach)

`settings.capability_routing` stays canonical and keeps its shape —
`{provider, model}` per capability — which is already provider-qualified.
The flat `*_model` mirrors (`orchestrator_model`…) remain for legacy readers;
`_sync_capability_routing()` gains resolver awareness so a flat pick of an
aggregator id maps `provider` correctly (today `_provider_for_model()` only
knows the four families).

---

## 8. Provider Health Monitoring

`services/provider_health.py` grows from point-in-time checks to a
measurement plane (in-memory ring buffers; zero new deps):

```python
record(provider, ok: bool, latency_ms: int, status: int | None, kind="chat")
# called from every adapter completion/failure (same seam as cost_meter)

stats(provider) -> {
  "window": "15m",
  "requests": 42, "errors": 3, "error_rate": 0.071,
  "latency_p50_ms": 640, "latency_p95_ms": 2100,
  "availability": "ok" | "degraded" | "down",   # ok <5% err, degraded <25%, else down
  "consecutive_failures": 0,
  "last_ok_at": 1780600000.0
}
```

- Ring buffer: last 256 calls per provider (≈bounded memory), plus a
  consecutive-failure counter for fast trip.
- Circuit breaker: 5 consecutive failures ⇒ `down` for a 60s cooldown ⇒
  half-open probe. Routing scoring multiplies candidates by availability
  (down ⇒ excluded unless it is the ONLY rung, in which case try anyway —
  a desktop agent should limp, not refuse).
- Existing `check_provider(deep=...)` stays for the wizard/Settings poll;
  `deep` probes route through `adapter.probe()` (OpenAI-compat: GET /models;
  Anthropic/Google: models list; Ollama: daemon ping).
- Persistence: none (session-local). Cross-session trends are cost_meter's
  job.
- Surfaced at `GET /api/providers/health` (existing route, enriched payload)
  and in `/api/health/full`.

---

## 9. Egress Gate Compatibility

### 9.1 Invariants (unchanged, restated as requirements on this work)

1. `_seal_or_block(payload, provider)` wraps EVERY cloud network write —
   every adapter, every retry, every fallback rung. One enforcement point.
2. Fail-closed: gate error/self-test failure blocks cloud sends entirely.
3. Local bypass is a privilege of data-stays-on-device transports only.

### 9.2 Registry-driven classification (replaces the hardcoded set)

```python
# egress_gate.py — target
def is_local_provider(name: str) -> bool:
    p = registry.get_provider(name)
    if p is None:
        return name in {"ollama", "local"}          # legacy belt-and-braces
    if p.get("classification") != "local":
        return False
    if p.get("adapter") not in LOCAL_CAPABLE_ADAPTERS:   # ollama, local-voice,
        return False                                     # nemo-local, openai-compatible
    return _is_private_host(p.get("base_url"))     # loopback / RFC1918 / .local
```

- **Default cloud**: a descriptor that says nothing is gated. (G5)
- **`classification: "local"` must be earned twice**: at registry load
  (descriptor with local + public base_url is demoted to cloud with a logged
  warning + Settings banner) and at call time (the adapter re-checks the
  resolved host before skipping the seal — a settings edit between load and
  call cannot open a hole). DNS names resolving off-LAN fail the check.
- This closes the inverse of GAP-9 too: a malicious/typo descriptor
  `{"type": "ollama", "base_url": "https://evil.example"}` classifies CLOUD
  and gets sealed, where today the executor would treat any `provider:"local"`
  route as bypass on the strength of a name.
- LM Studio / vLLM / TGI on localhost: `adapter: openai-compatible` +
  `classification: local` now legitimately bypass — fixing today's
  false-gating of genuinely local OpenAI-compat servers — while the SAME
  adapter pointed at OpenRouter stays gated. Classification is per-provider,
  not per-adapter.
- Vault force-routing (`_route_vault`) widens from "Ollama or refuse" to "any
  effective-local provider or refuse", preferring Ollama, in that order of
  trust: ollama > local openai-compat > none.
- The egress log (`~/.friday/vault/egress-log.jsonl`) gains the provider
  NAME (not just family) per decision, so an audit can distinguish
  openrouter from groq traffic.

---

## 10. API Design

Existing routes keep their contracts; new surface is additive
(`routes/platform.py` + `routes/core_routes.py`).

### 10.1 Provider management

```
GET    /api/providers                       # + health summary, budget status, model_count
POST   /api/providers                       # v2 descriptor; validate + persist JSON
POST   /api/providers/validate              # dry-run: {ok, errors[], warnings[]} — no write
GET    /api/providers/templates             # one-click templates (expanded set, §12)
DELETE /api/providers/<name>
PATCH  /api/providers/<name>                # partial update (enable, budget, base_url…)
POST   /api/providers/<name>/key            # existing — encrypted credential store
DELETE /api/providers/<name>/key
POST   /api/providers/<name>/test           # NEW: deep probe + optional 1-token
                                            #   completion ("ping") → {status, latency_ms,
                                            #   models_seen, detail}. The Test Connection btn.
GET    /api/providers/health                # enriched: stats() per provider (§8)
GET    /api/providers/<name>/usage          # spend today/month vs budget caps
```

`POST /api/providers/<name>/test` request/response:

```jsonc
// → {"ping": true}          ping=false does a keyless-safe GET /models only
// ←
{
  "provider": "openrouter", "status": "ok", "latency_ms": 412,
  "auth": "valid", "models_seen": 312,
  "ping": {"model": "meta-llama/llama-4-maverick:free", "ok": true, "cost_usd": 0.0},
  "detail": null
}
```

### 10.2 Models & discovery

```
GET  /api/models                            # existing catalog — entries gain
                                            #   context_window, price_in/out, free,
                                            #   supports_tools, source, catalog_stale
POST /api/providers/<name>/models/refresh   # force discovery now → {fetched, count}
GET  /api/models/search?q=&provider=&capability=&max_price_in=&min_context=&tools=1&free=1
     # searches the merged catalog; for aggregators additionally proxies
     # provider search (HF Hub API) — results marked source:"remote"
POST /api/models/pin                        # {provider, model} — persist a remote
                                            #   search hit into descriptor models[]
```

### 10.3 Routing policy

```
GET  /api/routing/policy                    # current routing_policy block
POST /api/routing/policy                    # partial update, validated
POST /api/routing/explain                   # {messages?|capability, objective?} →
     # dry-run RouteDecision: winner, full chain, per-candidate scores &
     # exclusion reasons ("over budget", "no tool support", "down").
     # Powers the "why this model?" hover in the UI and makes routing testable.
```

### 10.4 Route decision object (internal + /explain wire shape)

```jsonc
{
  "provider": "groq", "model": "llama-4-maverick",
  "adapter": "openai-compatible",
  "objective": "fastest", "capability": "reasoning",
  "chain": [
    {"provider": "groq", "model": "llama-4-maverick"},
    {"provider": "openrouter", "model": "meta-llama/llama-4-maverick"},
    {"provider": "ollama-local", "model": "gemma3:12b"}
  ],
  "is_local": false, "vault_allowed": false, "scrub_pii": true,
  "vault_access": false, "refuse": false, "warning": null,
  "scores": {"groq": 0.91, "openrouter": 0.78, "anthropic": 0.74},
  "excluded": {"huggingface": "no API key", "openai": "over daily budget"}
}
```

Backward compatibility: every flag `_finalize()` sets today (`is_local`,
`vault_allowed`, `scrub_pii`, `vault_access`, `refuse`, `warning`) is
preserved; `provider` widens from the enum {cloud, openai, local} to the
provider NAME, with a shim mapping names → legacy enum for un-migrated
callers during the transition (§13.3).

---

## 11. Settings UI

### 11.1 Settings → Providers panel

```
┌─ Settings ─────────────────────────────────────────────────────────────────┐
│ … │ Providers │ …                                                          │
├────────────────────────────────────────────────────────────────────────────┤
│  MODEL PROVIDERS                                   [＋ Add Provider ▾]     │
│                                                     ├ OpenRouter           │
│  ● Anthropic (Claude)      cloud   ✓ key   12ms p50 ├ HuggingFace          │
│    6 models · $1.42 today          [Test] [Models]  ├ Groq                 │
│                                                     ├ Together AI          │
│  ● OpenRouter              cloud   ✓ key   410ms    ├ Mistral · DeepSeek   │
│    312 models · $0.31 today · cap $2/day ▓▓▓░ 16%   ├ xAI · Perplexity     │
│    [Test] [Models] [⟳ Refresh list] [Budget…]       ├ Fireworks · Cohere   │
│                                                     └ Custom endpoint…     │
│  ● Local (Ollama)          local   daemon ✓  9ms                           │
│    4 models installed              [Test] [Models]                         │
│                                                                            │
│  ◐ HuggingFace             cloud   ⚠ add HF_TOKEN                          │
│    [Add key…] [Test] [Remove]                                              │
│                                                                            │
│  ○ Groq                    cloud   disabled        [Enable]                │
│                                                                            │
│  Legend: ● enabled+healthy  ◐ enabled, needs attention  ○ disabled         │
│  Status dot = health (§8): green ok · amber degraded · red down            │
└────────────────────────────────────────────────────────────────────────────┘
```

### 11.2 Add Provider modal

```
┌─ Add Provider ──────────────────────────────────────────────┐
│ Template:   [OpenRouter            ▾]   or  [Custom]        │
│ Name:       [openrouter           ]  (a-z, 0-9, -)          │
│ Base URL:   [https://openrouter.ai/api/v1              ]    │
│ Adapter:    [OpenAI-compatible ▾]  (Anthropic/Google/…)     │
│ API key:    [sk-or-••••••••••••••]  → stored ENCRYPTED,     │
│             never written to settings.json                  │
│ Model format: (•) org/model   ( ) plain                     │
│ Classification:  (•) Cloud — gated by the egress gate       │
│                  ( ) Local — requires a LAN/loopback URL    │
│                     ⓘ greyed out unless base_url qualifies  │
│ Budget cap: [$ 2.00 /day ▾]  (blank = none)                 │
│                                                             │
│ [Test Connection]   → ✓ Auth OK · 312 models · 410ms        │
│                                                             │
│              [Cancel]                    [Save & Enable]    │
└─────────────────────────────────────────────────────────────┘
```

Save path: descriptor → `POST /api/providers/validate` → key →
`POST /api/providers/<name>/key` → descriptor → `POST /api/providers` →
auto-`test` → discovery kickoff. On success the new provider's models appear
in the pickers with no restart (registry reload hook exists today).

### 11.3 Model Browser (reached from [Models] or the picker's "Browse all…")

```
┌─ Model Browser ────────────────────────────────────────────────────────────┐
│ Search: [llama 4              ]  Provider [All ▾]  Capability [Tools ▾]    │
│ ☑ Free only   Max $/1M in: [1.00]   Min context: [128K ▾]                  │
├────────────────────────────────────────────────────────────────────────────┤
│  MODEL                     PROVIDER     CTX     $/1M in/out   TOOLS  SPEED │
│  Llama 4 Maverick          Groq         1M      0.20/0.60      ✓    ⚡⚡⚡  │
│  Llama 4 Maverick          OpenRouter   1M      0.19/0.58      ✓    ⚡⚡    │
│  Llama 4 Maverick :free    OpenRouter   256K    0 / 0          ✓    ⚡     │
│  Llama 4 Scout              HF (Hub)    10M     0.11/0.34      ✓    ⚡⚡    │
│    └ source: remote search — [Pin to catalog]                              │
├────────────────────────────────────────────────────────────────────────────┤
│  Selected: Llama 4 Maverick @ Groq                                         │
│  [Set as Orchestrator] [Set as Subagent] [Use once in this chat]           │
│  Why-this hover → /api/routing/explain scores                              │
└────────────────────────────────────────────────────────────────────────────┘
```

### 11.4 Routing objective control (chat header / Settings → Routing)

```
Routing: ( ) Best quality  (•) Balanced  ( ) Cheapest  ( ) Fastest  ( ) Private
         Private = local models only — nothing leaves this machine.
Fallback chains: [Edit chains…]  (per-capability drag-to-reorder list)
```

All panels render from `/api/models`, `/api/providers`, `/api/routing/*` —
consistent with the existing rule that the frontend hardcodes no model lists.

---

## 12. Built-in vs Config-Only: the Ship Matrix

Tier definitions:
- **T1 Built-in, enabled** — full descriptor ships enabled; works the moment a
  key exists.
- **T2 Built-in template, one click** — full descriptor ships as a template
  (disabled); Settings enables it. Zero code, zero JSON authoring.
- **T3 Config-only** — user writes/edits a descriptor (or picks Custom).
- **T4 Via litellm adapter** — optional extra installed.

| Provider | Tier | Adapter | Key env | Notes |
|---|---|---|---|---|
| Anthropic | T1 | anthropic | ANTHROPIC_API_KEY | unchanged; native tool loop |
| Google Gemini | T1 | google | GEMINI_API_KEY | unchanged; voice/creative roles |
| Ollama | T1 | ollama | — | unchanged; local |
| **OpenRouter** | **T1 (promoted)** | openai-compatible | OPENROUTER_API_KEY | §12.1 |
| OpenAI | T1 | openai-compatible | OPENAI_API_KEY | current native entry converges onto the compat adapter |
| **HuggingFace** | **T2 (new)** | openai-compatible | HF_TOKEN (alias HUGGINGFACE_API_KEY) | §12.2 |
| Groq | T2 | openai-compatible | GROQ_API_KEY | the `fastest` objective's star; LPU p50 <300ms |
| Together AI | T2 | openai-compatible | TOGETHER_API_KEY | /models includes pricing |
| Fireworks AI | T2 | openai-compatible | FIREWORKS_API_KEY | ids `accounts/fireworks/models/…` |
| Mistral | T2 | openai-compatible | MISTRAL_API_KEY | api.mistral.ai/v1 |
| DeepSeek | T2 | openai-compatible | DEEPSEEK_API_KEY | deepseek-chat / deepseek-reasoner; reasoning-tier bargain |
| xAI (Grok) | T2 | openai-compatible | XAI_API_KEY | api.x.ai/v1 |
| Perplexity | T2 | openai-compatible | PERPLEXITY_API_KEY | static model list (no /models); search-augmented — tag `modalities: [text, web]` so routing can prefer it for RESEARCH task type |
| Cohere | T2 | openai-compatible | COHERE_API_KEY | compat endpoint `api.cohere.ai/compatibility/v1`; native API only via T4 |
| vLLM / LM Studio / TGI self-hosted | T3 (Custom template) | openai-compatible | optional | `classification: local` when LAN — legit egress bypass (§9.2) |
| Azure OpenAI | T3 | openai-compatible | AZURE_OPENAI_API_KEY | needs api-version query + deployment-name model format — descriptor `extra_query` field (v2.1) |
| Replicate | T4 | litellm | REPLICATE_API_TOKEN | predictions API, not chat-shaped |
| AWS Bedrock / GCP Vertex | T4 | litellm | AWS_*/GCP ADC | enterprise long tail |

### 12.1 OpenRouter — first-class integration detail

Descriptor: Appendix A.1. Behaviors beyond the generic compat adapter,
all keyed off `features.*` (so any future aggregator can reuse them):

1. **Discovery**: `/api/v1/models` parser captures pricing (per-token →
   per-1M), context, modalities, `supported_parameters` → `supports_tools`.
   No auth required for the list — discovery works before a key is added
   (models show dimmed with `needs_key`, existing UX pattern).
2. **Model fallback**: rung executes with `models: [primary, alt1, alt2]`
   drawn from the same-provider tail of the chain; response `model` field is
   what gets metered. One HTTP round trip instead of N.
3. **Usage accounting**: request `usage: {include: true}`; response
   `usage.cost` feeds cost_meter directly (authoritative, beats local price
   math). Periodic `GET /api/v1/key` reconciles remaining credits →
   Providers panel.
4. **Rate limits**: `rate_limit_style: openrouter` parses `X-RateLimit-*`
   headers; 429 waits `Retry-After` once then advances the chain.
   `:free` variants get a stricter internal ceiling (they are shared pools)
   and are excluded from `quality` objective candidates.
5. **Attribution**: keeps sending `HTTP-Referer: https://futurespeak.ai`,
   `X-Title: Agent Friday` (already implemented — moves from code into the
   descriptor's `extra_headers`).
6. **Variant hygiene**: `:free/:nitro/:floor/:thinking` are distinct catalog
   entries; the picker groups them under the base model with a variant badge.

### 12.2 HuggingFace — integration detail

Three distinct HF surfaces, three descriptor stances:

1. **Serverless (Inference Providers router)** — the T2 template.
   `base_url: https://router.huggingface.co/v1`, standard chat completions,
   `HF_TOKEN` auth. Model ids are Hub ids (`meta-llama/Llama-3.3-70B-Instruct`)
   optionally suffixed with a backing provider (`:groq`, `:together`,
   `:hf-inference`) — same suffix-variant handling as OpenRouter. Discovery
   via router `/v1/models` (warm, chat-capable). The legacy
   `api-inference.huggingface.co` endpoint is superseded by the router and is
   NOT targeted.
2. **The 200K+ Hub long tail** — never enumerated. `/api/models/search`
   proxies `GET https://huggingface.co/api/models?search=&pipeline_tag=text-generation&inference=warm`;
   hits are pinnable (§10.2) into `models[]`. This gives "any HF model"
   reach with a bounded catalog.
3. **Dedicated Inference Endpoints & self-hosted TGI** — just custom
   OpenAI-compat descriptors (the endpoint URL the user already has);
   `classification: local` when on-prem. No special code.

Embedding note: HF serverless also serves feature-extraction; Friday's
embedding capability stays local (MiniLM) by default — HF embedding routing
is out of scope here (§16).

---

## 13. Migration Path

Principle: **two minor versions of read-compat for every legacy key; writes
go to the new shape immediately; no user-visible breakage on upgrade day.**

### 13.1 Settings migration (automatic, on first `_load_settings()` post-upgrade)

| Legacy | Becomes |
|---|---|
| `model_routing.cloud_provider: "openai"|"openrouter"|"compatible"` + `openai_base_url/model/key` | A registered provider: host `openrouter.ai` → enable built-in `openrouter`; `api.openai.com` → `openai`; anything else → a `custom-migrated` descriptor. `openai_api_key` (if set in settings — historically possible) moves into the encrypted credential store and is wiped from settings.json. `openai_model` seeds that provider's chain rung. |
| `model_routing.mode` | kept as-is (still meaningful: local preference) + seeds `routing_policy.objective` (`local_preferred` → chains reordered local-first; `cloud_only` → unchanged) |
| flat `*_model` keys | unchanged (still mirrored); resolver makes aggregator ids in them work correctly |
| `cost_per_1k` in descriptors | read as blended fallback until v+2; discovery/dataset pricing takes precedence |
| `providers: {}` settings block | absorbed into descriptor `enabled`/`base_url` overrides |

### 13.2 Registry migration

- v1 JSON drops keep loading (`type` → `adapter` alias, defaults injected,
  `classification` inferred: type ollama/local-voice/nemo-local + private
  host → local, else cloud).
- `add_provider()` writes v2. A one-time sweep rewrites `~/.friday/providers/*.json`
  to v2 with a `.bak` of each original.

### 13.3 Code migration (internal, staged with shims)

1. `RouteDecision.provider` becomes a provider NAME. Shim
   `legacy_provider_enum(name)` → `"cloud"|"openai"|"local"` feeds
   un-migrated call sites (`chat.py`, `_generate_text` ladder) until each is
   moved to adapter dispatch. Grep-able TODO tag: `# MAPS-shim`.
2. `_call_openai()` signature gains `provider: dict|None`; `None` keeps
   today's global-settings behavior (shim), descriptor path is the new normal.
3. `provider_family()` demoted to heuristic-of-last-resort inside
   `resolve_model()`; direct dispatch usage forbidden (ruff custom rule /
   grep in CI).
4. `_generate_text`'s hardcoded 3-rung ladder (claude → openai → ollama)
   becomes "walk the `reasoning` chain" — same guarantee (anything up ⇒
   text generated), config-driven order.
5. Egress `_LOCAL_PROVIDERS` set retained as final fallback inside
   `is_local_provider()` (defense in depth), never the primary check.

### 13.4 Pricing migration

`CLOUD_COST_PER_1K` (routing) and per-provider `cost_per_1k` freeze as
seed data for `pricing.static` and then delete in v+2. `CostTracker.record()`
and `cost_meter.meter()` switch to `pricing.price()` lookups with in/out
split; historical ledger rows keep their old blended numbers (append-only,
no rewrite).

### 13.5 Rollback

Feature flag `settings.provider_layer_v2: true` (default on). Off ⇒ legacy
routing/dispatch paths (kept intact through the shim period). The flag and
shims are deleted together in v+2.

---

## 14. Implementation Phases

Each phase is independently shippable and leaves the suite green.

| Phase | Scope | Key files | Acceptance criteria |
|---|---|---|---|
| **P0 — Foundation** (schema + resolver + egress) | Descriptor v2 + validation; `resolve_model()`; registry-driven `is_local_provider()` with private-host check; `classification` field | `provider_registry.py`, `routing/model_router.py`, `egress_gate.py` | v1 JSONs load; `meta-llama/llama-4-maverick:free` resolves to openrouter, `claude-x:latest` (installed) to ollama; descriptor with type ollama + public URL is GATED; all existing tests green |
| **P1 — OpenRouter first-class** | Promote template→T1; discovery parser + cache; usage accounting; rate-limit handling; `models[]` fallback param; `:free` tagging | `provider_adapters.py` (new), `model_catalog.py`, `services/pricing.py` (new) | Add key → 300+ models in picker with prices/context; pick one → chat + tools work; cost ledger matches OpenRouter dashboard within 2%; kill network mid-call → clean chain advance |
| **P2 — Multi-provider dispatch** | Adapter layer; RouteDecision carries provider name; per-provider concurrent use; `_generate_text` chain-walk; health `record()/stats()` + breaker | `services/model_router.py`, `provider_health.py` | Groq subagent + OpenRouter orchestrator + Ollama vault simultaneously in one session; `/api/providers/health` shows real p50s |
| **P3 — HuggingFace** | T2 template; router discovery; Hub search-on-demand + pin; endpoint/TGI custom recipes documented | adapters, `routes/platform.py` | HF_TOKEN → warm models listed; search "qwen3" → pin → route a chat to it; TGI-on-LAN descriptor bypasses gate ONLY on private host |
| **P4 — Catalog & Settings UI** | Providers panel rev (health dots, budgets, test); Add Provider modal; Model Browser; refresh endpoints | `ui_parts/app.html`, platform routes | Add Groq via UI in <60s with no restart; browser filters by free/tools/price; test button reports latency + model count |
| **P5 — Routing intelligence** | `routing_policy` block; objective scoring; per-capability chains; `/api/routing/explain`; per-provider budgets with `on_exceed` | `routing/model_router.py`, `budget_enforcer.py` | `cheapest` picks a `:free` variant when eligible; `fastest` picks Groq/local by measured p50; `private` NEVER emits a cloud call (asserted by egress log in tests); explain endpoint justifies every exclusion |
| **P6 — Long tail + polish** | Remaining T2 templates (Mistral, DeepSeek, xAI, Perplexity, Cohere, Together, Fireworks); optional `[litellm]` extra + adapter (sealed, retries off); vendored pricing dataset refresh job; streaming on compat adapter | pyproject extras, adapters | Each T2 enables with key-only; litellm absent ⇒ everything else unaffected; SSE streaming behind a setting |

Sizing (relative): P0 ★★☆ · P1 ★★★ · P2 ★★★★ · P3 ★★☆ · P4 ★★★ · P5 ★★★ · P6 ★★☆.
P0→P1→P2 is the critical path; P3/P4 can parallelize after P2; P5 needs P2's
health data; P6 is cleanup + breadth.

Testing spine (added at P0, grown per phase): descriptor validation
round-trips; resolver truth table (the GAP-4 cases as regression tests);
egress classification matrix (name×adapter×host); a `FakeProvider` HTTP
fixture speaking /v1 for chain/fallback/429 tests offline; `routing/explain`
golden files per objective.

---

## 15. Security & Privacy Considerations

1. **Key handling** — unchanged rules, wider scope: keys live in env or the
   encrypted credential store, never in descriptors/settings.json, never in
   logs or adapter exceptions. `POST /api/providers` REJECTS descriptors
   containing raw `api_key` fields (400 + hint at the key endpoint).
2. **Descriptor injection surface** — descriptors are DATA. Validation
   whitelists adapters and schemes (https for cloud), caps header count/size,
   and forbids `extra_headers` from setting `Authorization` (auth flows only
   through `auth`). A dropped JSON can point traffic at a hostile endpoint —
   but everything sent there was sealed by the egress gate first, and the
   Providers panel surfaces every registered provider with its origin
   (built-in / file / UI) so nothing rides invisibly.
3. **Local classification abuse** — covered by §9.2 double verification;
   DNS-rebind is mitigated by re-resolving at call time within the adapter.
4. **Aggregator data policies** — OpenRouter routes to downstream providers
   with varying retention. Surface `top_provider`/policy metadata in the
   Model Browser; the `private` objective is the hard answer, and OpenRouter's
   `provider: {data_collection: "deny"}` preference is exposed via
   `features.provider_routing` for the soft one.
5. **Free-tier exhaust** — `:free` pools are shared and rate-limited;
   Friday backs off politely (§12.1.4) so the app doesn't get keys flagged.
6. **Cost runaway** — per-provider caps + global budget_enforcer; unknown
   price ≠ $0 (§6.4); `on_exceed: degrade` falls to cheaper/local rungs,
   never silently upgrades spend.
7. **Supply chain** — no new required deps in the core install (adapters use
   stdlib + `requests`); `litellm` and even `openai` SDK stay out of core;
   the vendored pricing JSON is data, reviewed in diffs like code.

---

## 16. Open Questions

| # | Question | Current lean |
|---|---|---|
| Q1 | Should `openai` (first-party) keep a dedicated native adapter for Responses-API features (built-in tools, reasoning params) that the bare chat-completions shape misses? | Stay compat-only until a concrete Friday feature needs it. |
| Q2 | OpenRouter BYOK (bring-your-own upstream keys) — worth surfacing? | Defer; niche, and it blurs cost attribution. |
| Q3 | Embeddings/rerank via cloud providers (HF, Cohere, Together)? | Keep embeddings local (sovereignty + they index the vault-adjacent memory); revisit only for opt-in workspace search. |
| Q4 | Quality tiers — import a public benchmark index (e.g., aggregate arena scores) for `quality_tier`, or keep the coarse manual ordinal? | Coarse ordinal now; benchmark import is a P6+ nicety with licensing questions. |
| Q5 | Should chains be editable per-workspace (Studio vs Research) like temperatures are? | Yes eventually — schema already allows it (`fallback_chains` keys could take `workspace:` prefixes); not in P5. |
| Q6 | Streaming-first refactor of the chat pipeline (SSE end-to-end)? | Separate spec; this one only requires the compat adapter not to PRECLUDE it (stream_cb in the contract). |

---

## Appendix A: Example Descriptors

### A.1 `openrouter.json` (T1, as shipped)

```json
{
  "schema_version": 2,
  "name": "openrouter",
  "label": "OpenRouter",
  "adapter": "openai-compatible",
  "base_url": "https://openrouter.ai/api/v1",
  "auth": {"type": "env_var", "key": "OPENROUTER_API_KEY"},
  "extra_headers": {"HTTP-Referer": "https://futurespeak.ai", "X-Title": "Agent Friday"},
  "network": {"timeout_s": 180, "retries": 2, "rate_limit_style": "openrouter"},
  "classification": "cloud",
  "discovery": {"mode": "api", "endpoint": "/models", "parser": "openrouter", "ttl_s": 86400},
  "models": [],
  "model_format": "org/model",
  "capabilities": ["tools", "vision"],
  "roles": ["orchestrator", "subagent"],
  "pricing": {"source": "discovery"},
  "budget": {"daily_usd": null, "monthly_usd": null, "on_exceed": "block"},
  "features": {
    "fallback_models_param": true,
    "usage_accounting": true,
    "model_suffixes": [":free", ":beta", ":nitro", ":floor", ":thinking"]
  },
  "enabled": true,
  "priority": 40
}
```

### A.2 `huggingface.json` (T2 template)

```json
{
  "schema_version": 2,
  "name": "huggingface",
  "label": "Hugging Face (Inference Providers)",
  "adapter": "openai-compatible",
  "base_url": "https://router.huggingface.co/v1",
  "auth": {"type": "env_var", "key": "HF_TOKEN",
           "key_aliases": ["HUGGINGFACE_API_KEY", "HUGGING_FACE_HUB_TOKEN"]},
  "network": {"timeout_s": 180, "retries": 2, "rate_limit_style": "none"},
  "classification": "cloud",
  "discovery": {"mode": "api", "endpoint": "/models", "parser": "hf-router",
                 "ttl_s": 86400, "max_models": 100,
                 "filters": {"status": "warm", "task": "conversational"}},
  "models": [],
  "model_format": "org/model",
  "capabilities": ["tools"],
  "roles": ["orchestrator", "subagent"],
  "pricing": {"source": "dataset"},
  "features": {"hub_search": true,
                "model_suffixes": [":hf-inference", ":groq", ":together", ":fireworks-ai"]},
  "enabled": false,
  "priority": 45
}
```

### A.3 `my-vllm.json` (T3 — user-authored, LOCAL)

```json
{
  "schema_version": 2,
  "name": "my-vllm",
  "label": "vLLM (basement box)",
  "adapter": "openai-compatible",
  "base_url": "http://192.168.1.40:8000/v1",
  "auth": {"type": "none"},
  "classification": "local",
  "discovery": {"mode": "api", "endpoint": "/models", "parser": "openai", "ttl_s": 300},
  "models": [],
  "capabilities": ["tools"],
  "roles": ["orchestrator", "subagent"],
  "enabled": true,
  "priority": 10
}
```

### A.4 `bedrock.json` (T4 — litellm adapter, optional extra installed)

```json
{
  "schema_version": 2,
  "name": "bedrock",
  "label": "AWS Bedrock",
  "adapter": "litellm",
  "litellm_prefix": "bedrock/",
  "auth": {"type": "env_var", "key": "AWS_ACCESS_KEY_ID"},
  "classification": "cloud",
  "discovery": {"mode": "static"},
  "models": ["bedrock/anthropic.claude-sonnet-5-v1:0"],
  "capabilities": ["tools"],
  "roles": ["subagent"],
  "pricing": {"source": "dataset"},
  "enabled": false,
  "priority": 80
}
```

## Appendix B: Provider API Cheat Sheet

| Provider | Base URL | Key env | Chat path | Models path | OpenAI-compat |
|---|---|---|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | OPENROUTER_API_KEY | /chat/completions | /models (no auth) | ✓ |
| HF router | `https://router.huggingface.co/v1` | HF_TOKEN | /chat/completions | /models | ✓ |
| Groq | `https://api.groq.com/openai/v1` | GROQ_API_KEY | /chat/completions | /models | ✓ |
| Together | `https://api.together.xyz/v1` | TOGETHER_API_KEY | /chat/completions | /models (rich) | ✓ |
| Fireworks | `https://api.fireworks.ai/inference/v1` | FIREWORKS_API_KEY | /chat/completions | /models | ✓ |
| Mistral | `https://api.mistral.ai/v1` | MISTRAL_API_KEY | /chat/completions | /models | ✓ |
| DeepSeek | `https://api.deepseek.com/v1` | DEEPSEEK_API_KEY | /chat/completions | /models | ✓ |
| xAI | `https://api.x.ai/v1` | XAI_API_KEY | /chat/completions | /models | ✓ |
| Perplexity | `https://api.perplexity.ai` | PERPLEXITY_API_KEY | /chat/completions | — (static) | ✓ |
| Cohere (compat) | `https://api.cohere.ai/compatibility/v1` | COHERE_API_KEY | /chat/completions | /models | ✓ |
| Anthropic | `https://api.anthropic.com` | ANTHROPIC_API_KEY | /v1/messages | /v1/models | native |
| Google | `https://generativelanguage.googleapis.com` | GEMINI_API_KEY | /v1beta/…:generateContent | /v1beta/models | native |
| Ollama | `http://localhost:11434` | — | /v1/chat/completions | /api/tags | ✓ (local) |
| Replicate | `https://api.replicate.com/v1` | REPLICATE_API_TOKEN | /predictions | /models | ✗ → litellm |

*(Endpoint details current as of 2026-07; discovery-driven design means drift
here degrades to a failed probe + Settings hint, not broken chat.)*

---

*End of spec. Related reading: `docs/ARCHITECTURE.md` (layer map),
`docs/FABLE5_INTEGRATION_STORM_REPORT.md` (egress R-invariants),
`services/provider_registry.py` (v1 descriptor reference implementation).*
