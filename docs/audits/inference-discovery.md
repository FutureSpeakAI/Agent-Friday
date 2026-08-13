# Inference Discovery Audit — Agent Friday reference instance

**Date:** 2026-08-13
**Scope:** read-only inspection of `%USERPROFILE%\Projects\friday-desktop` at commit `656b70b` (branch `fix/toolcall-integrity-v5`, tree clean at audit start).
**Method:** file reading, ripgrep, directory listing, `git ls-files`. No code executed against the repo; no files modified. Four parallel read-only explorations, with every load-bearing claim independently re-verified by the author via direct `grep`/`sed` before inclusion.

**Evidence markers used throughout:**
- **VERIFIED** — the author read the cited line(s) or ran the cited command and saw the output.
- **INFERRED** — a conclusion drawn from verified facts; the reasoning is shown inline.
- **UNKNOWN** — not determinable by static reading; listed in §Unknowns with the runtime check that would settle it.

---

## Summary

Friday has a genuine, well-documented two-layer inference abstraction — `routing/model_router.py` decides *where* a request goes and `services/model_router.py` executes it — and all three text backends (Anthropic SDK, Ollama, OpenAI-compatible HTTP) funnel their cloud payloads through a single egress choke point, so the security contract does not drift between providers. Tool calling is native on every path, backed by one Anthropic-shaped schema registry converted on demand to OpenAI format, and a real conformance gate that measures whether a local model can actually emit structured tool calls before trusting it with tools. Below that solid core, three things are genuinely absent: there is no hardware-profile concept anywhere in the codebase, no context-window awareness (three separate truncation layers each use a hardcoded constant while real per-model window data sits unread in the catalog), and no model-artifact catalog with checksums. Hardware *detection* exists and is real, but its output feeds only Ollama install recommendations and a binary CPU-vs-GPU voice gate — it never influences chat, image, or embedding model choice. The health surface is the weakest area: `/api/health` returns `"status": "ok"` unconditionally, and provider health for Anthropic and Gemini reduces to "is the API key string non-empty," so a revoked key reports healthy; meanwhile the one function in the tree that would actually prove inference works has zero call sites. Two configuration surfaces are stored, exposed over HTTP, and never read by the dispatch path — `capability_routing.embedding.model` and `.fridayhints`'s `preferred_model` — meaning users can change them and nothing happens. The orchestrator's `OllamaAdapter` is a second, hand-rolled Ollama client that hardcodes the base URL, uses a non-tool-calling endpoint, and bypasses the egress gate that every other path enforces. Portability is Windows-first by construction: `friday_tray.py` will crash on import under POSIX, credential-at-rest falls back to plaintext off-Windows, and Google OAuth pins `localhost:3000` as a literal while the server itself will silently bind a different port if 3000 is busy. Test coverage over the routing and provider layers is strong, but an autouse fixture stubs the real provider call bodies out of ~50 of the ~57 API test files, so a regression inside the actual HTTP request construction would ship green. None of the APPENDIX provisioning components except faster-whisper and Piper have any integration point in the code today.

---

## Findings

### 1. Every code path that calls a model

#### Text / chat / completion

| Path | Client | Base URL source | Model name source |
|---|---|---|---|
| Anthropic | official `anthropic` SDK | SDK default (never overridden in-repo) | settings → env → hardcoded |
| Ollama | stdlib `urllib.request` | settings, default `localhost:11434` | settings / router heuristic |
| OpenAI-compatible | `requests` library | provider descriptor / settings | descriptor / settings / hardcoded |

- **VERIFIED** `services/agent.py:4504` — `_call_claude_agent(messages, system=None, model=None, max_tokens=16384, ...)` is the Anthropic tool-using entry point. The client is constructed once at `core/__init__.py:738`, passing only the key from `ANTHROPIC_API_KEY` — **no `base_url=` and no `timeout=` argument is passed anywhere in the repo**, so both run on the SDK's built-in defaults.
- **VERIFIED** `services/model_router.py:121-182` — `_call_claude()`, the non-tool single-shot sibling. Model resolution: `_load_settings().get("orchestrator_model") or ANTHROPIC_MODEL_DEFAULT`, where `ANTHROPIC_MODEL_DEFAULT = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")` (`core/__init__.py:722`). Three-tier precedence: user setting → env var → hardcoded literal.
- **VERIFIED** `routing/ollama_manager.py:208-251` — `chat_completion()` POSTs via stdlib `urllib.request` to `{base_url}/v1/chat/completions` (OpenAI-compatible shape), falling back to `{base_url}/api/chat` (Ollama-native) on exception. Timeout **120s** on both (`routing/ollama_manager.py:228`, verified: `with urllib.request.urlopen(req, timeout=120) as resp:`).
- **VERIFIED** `services/model_router.py:380` — base URL resolution: `get_manager(routing_cfg.get('ollama_url', 'http://localhost:11434'))`. Overridable via `settings.model_routing.ollama_url`; hardcoded loopback fallback.
- **VERIFIED** `services/model_router.py:539-791` — `_call_openai()` uses `requests.post(f"{base_url}/chat/completions", ...)`. Base URL from the provider descriptor `prov.get('base_url')`, else `settings.model_routing.openai_base_url`, else `'https://api.openai.com/v1'`. Model falls back to a hardcoded `'gpt-4o-mini'` in the legacy path.

#### Audio / voice

- **VERIFIED** `services/voice_engine.py:550-614` — `_synthesize_tts_wav_gemini()` calls `genai.Client(...).models.generate_content(model="gemini-2.5-flash-preview-tts", ...)`. Model string is **hardcoded at the call site**.
- **VERIFIED** `services/voice_engine.py:632` — Live speech-to-speech: `LIVE_MODEL = os.environ.get("FRIDAY_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")`, with a hardcoded two-step fallback chain and a retired-model blocklist.
- **VERIFIED** `services/local_voice.py:45-47` — Tier-1 local: `LOCAL_VOICE_DIR = _HOME / ".friday" / "local_voice"`, `WHISPER_DIR`, `PIPER_DIR`. ASR is `faster-whisper` (`WhisperModel(..., device="cpu", compute_type="int8", download_root=...)`); TTS is Piper (`PiperVoice.load(...)`). Both are **in-process Python library calls with no HTTP at inference time**.
- **VERIFIED** `services/nemo_voice.py:275,340` — Tier-2 GPU: NVIDIA NeMo, artifacts cached under `NEMO_CACHE_DIR`. Lazily imported; in-process.

#### Image / video / music

- **VERIFIED** `services/creative_engine.py:559-656` — image generation via `google.genai` `client.models.generate_content(...)`, model resolved through an alias table (`IMAGE_MODEL_ALIASES`).
- **VERIFIED** by absence — `grep -rniE "dall-e|stability|stable-diffusion|midjourney|comfyui" src/agent_friday` returns **zero hits**. There is no local image-generation backend of any kind in the codebase; all image generation is a cloud Gemini call.

#### Embeddings

- **VERIFIED** `conversation_memory.py:44` — `EMBED_MODEL = "all-MiniLM-L6-v2"`, a module constant. See §6.

---

### 2. Backend assumptions and abstraction integrity

**There is one real abstraction, and it is well-built.** Two layers, each with an explicit docstring stating its role:

- **VERIFIED** `routing/model_router.py:318` — `def route(self, messages, task_context=None):` — the *routing layer* ("WHERE to send").
- **VERIFIED** `services/model_router.py:88` — `def _seal_or_block(payload, provider):` — the shared egress choke point. All three provider primitives (`_call_claude`, `_call_ollama`, `_call_openai`) pass outbound cloud payloads through this one function. Its own docstring states the intent: *"Centralizing the wrapper means the fail-closed contract cannot drift between the Anthropic and OpenAI-compatible paths again."*

**Two documented exceptions and one undocumented bypass:**

- **Gemini is not dispatchable through the router at all** — creative/voice/music Gemini calls each construct their own `genai.Client`. **VERIFIED**: this is explicitly acknowledged in-code at `services/provider_registry.py:168-172`, which notes `roles:[]` is set for Gemini text models *"until a Gemini text/agentic dispatch exists in routing/model_router.py."* This is a known, commented scope boundary, not an oversight.
- **`capability_router.py` is a resolver, not a dispatcher** — it maps capability → provider+model for UI availability badges and executes nothing. Correctly scoped per its own docstring.
- **`worker_adapters/ollama_adapter.py` is an undocumented second Ollama client.** **VERIFIED**: `_OLLAMA_BASE = "http://localhost:11434"` is a hardcoded module constant at line 26 — it does **not** read `settings.model_routing.ollama_url`. It POSTs to `{_OLLAMA_BASE}/api/generate` (line 115) — the Ollama-native *non-chat, non-tool-calling* endpoint — with a hardcoded default model `"gemma4:latest"` (line 91). **VERIFIED by absence**: `grep -n "egress\|seal_outbound"` over that file returns nothing. It bypasses the egress gate, the PII scrub, provider-health recording, cost metering, and the tool loop that every other path enforces. Unlike the Gemini gap, there is no comment explaining or scoping this.

---

### 3. Routing, model selection, fallback, retry, timeout

**Routing is real logic, not a passthrough.**

- **VERIFIED** `routing/model_router.py:318` — `route()` branches on vault tier first (a vault-forced local route takes precedence over every configured mode), then on mode (`cloud_only` / `smart` / `local_preferred` / `local_only`), then on task classification.
- **VERIFIED** `routing/ollama_manager.py:182` — `def recommend_models(self, hardware=None):` — a real VRAM/RAM-tiered recommendation ladder. **But see §10: this is install-time advice only and is never consulted by the dispatch path.**

**Fallback: exists in both directions, with one deliberate refusal.**

- `services/model_router.py:_generate_text()` and `services/agent.py:_generate_agent()` both build an ordered `attempts` list (routed provider first, then the others) and iterate until one returns non-empty text.
- **VERIFIED** `services/agent.py:186-191` — a vault-forced local route explicitly refuses cloud fallback, with an inline comment stating *"A vault-forced local route must NEVER retry on a cloud provider."* This is the correct, security-preserving exception.

**Circuit breaker: real.**

- **VERIFIED** `services/provider_health.py:43` — `_BREAKER_THRESHOLD = 5   # consecutive failures that trip the breaker`, with a 60s cooldown then half-open. Attempt ordering demotes a "down" provider to last, but still tries it as a last resort.

**Timeouts — an inventory, and one gap:**

| Call site | Timeout | Evidence |
|---|---|---|
| Ollama chat completion | 120s | **VERIFIED** `routing/ollama_manager.py:228` |
| Ollama availability probe | 3s | `routing/ollama_manager.py:73-86` |
| Ollama model pull | 600s | `routing/ollama_manager.py:55` |
| OpenAI-compatible chat | 180s (descriptor-overridable) | **VERIFIED** `services/model_router.py:620` |
| Provider deep health probe | 6s | **VERIFIED** `services/provider_health.py:236` |
| Piper voice download | 30s | **VERIFIED** `services/local_voice.py:384` |
| **Anthropic `messages.create`** | **none set in-repo** | **VERIFIED by absence** — no `timeout=` passed to `Anthropic(...)` or `messages.create(...)` anywhere |

**Retry:** there is no generic exponential-backoff retry loop. The only same-provider retry is a single 429-triggered retry honouring `Retry-After` capped at 15s (`services/model_router.py:723-735`). All other resilience is *provider substitution*, not retry.

---

### 4. Tool calling

**Native on every path. No prompt-parsed-JSON tool calling is used as a mechanism.**

- **VERIFIED** `services/agent.py:4640-4691` — Anthropic native: `kwargs["tools"] = CLAUDE_TOOLS`, loop inspects `resp.stop_reason == 'tool_use'` and `b.type == 'tool_use'` blocks.
- **VERIFIED** `services/agent.py:4793` — `def _oai_agentic_loop(convo, oai_tools, send_fn, *, provider, model, ...)` — one shared OpenAI-format loop serving *both* Ollama and OpenAI-compatible providers, reading structured `msg.get("tool_calls")`. **VERIFIED by grep**: `_oai_agentic_loop` appears in exactly two files (definition in `agent.py`, use in `services/model_router.py`) — confirming a single shared loop, not per-provider copies.
- **Schema location:** `CLAUDE_TOOLS` in `services/agent.py` is the single Anthropic-shaped registry; `anthropic_to_openai_tools()` (`routing/model_router.py:597-611`) converts it to OpenAI function schemas on demand. One registry, one converter.

**What happens on a model without native tool support — this is genuinely engineered, not left to break:**

- **VERIFIED** `services/tool_integrity.py:46` — `def find_pseudo_toolcalls(text: str, tool_names) -> list[str]:` — regex-scans model prose (outside code fences) for tool names written as bracket-syntax pseudo-calls. The module docstring records the originating incident: gemma3:4b, lacking native Ollama tool calling, *confabulated an entire briefing* by narrating fake tool calls in prose.
- **VERIFIED** `services/model_seat_gate.py:197` — `def resolve_local_seat(requested_model: str, *, provider: str = "local") -> dict:` — called on every tool-using local dispatch. A candidate model must pass a 10-prompt conformance gate scoring *both* "emitted a real structured tool call" and "leaked zero pseudo-calls in prose." Outcomes: green → used as-is; not green but a known-green model exists → **silently substituted** with a system note instructing the model to disclose the substitution; nothing green → **tools disabled for the turn** and the model is told to say plainly it cannot use tools rather than guess.

**Gap:** this gate is local-path-only. **VERIFIED by absence** — there is no conformance equivalent for `_call_openai`; cloud OpenAI-compatible endpoints are assumed tool-capable (`tool_choice: "auto"` sent unconditionally). Pointing Friday at a non-tool-calling OpenAI-compatible endpoint (some LM Studio / vLLM configurations) has no code-level detection or fallback. Anthropic models are likewise assumed tool-capable by registry declaration.

---

### 5. Context handling

History is assembled at `routes/chat.py:231-238` from the last 100 turns, then passed through **three independent, overlapping reduction layers**, none of which knows the actual model's context window:

| Layer | Trigger | Constant | Evidence |
|---|---|---|---|
| `_compress_trajectory` | char count | `_TRAJ_CHAR_LIMIT = 2_000_000` | **VERIFIED** `services/model_router.py:798` |
| `ContextPruner.prune` | turn count | `max_turns=50`, `keep_recent=4`, `top_k=10` | **VERIFIED** `core/__init__.py:1384-1389` |
| `maybe_compact` | token estimate ÷ window | `context_window` default **200000** | **VERIFIED** `services/compaction.py:77` |

- **VERIFIED** `services/model_router.py:798` — the trajectory constant carries its own justification in the comment: `# ~500K tokens; Opus 4.8 has 1M ctx — only compress at this threshold`. That reasoning is sound for exactly one model and is applied globally.
- **VERIFIED** `services/compaction.py:138` — `def maybe_compact(messages, model=None, summarizer=None):` accepts `model`, but the window comes from `cfg.get("context_window", 200000)` at line 77 — **the `model` parameter is used only to pick the summarizer, never to look up the window.**
- **The data exists and is unread.** **VERIFIED** — `services/model_discovery.py:102,133` genuinely parse real per-model `context_window` from provider APIs, and it flows into `services/model_catalog.py:202` and out to the UI at `routes/platform.py:464,469,485` for filtering and sorting. **VERIFIED by grep**: the only consumers of `context_window` outside discovery/catalog/UI are `services/compaction.py:77` (which uses its own default, not the catalog) and `routes/voice.py:1366` (a Gemini Live SDK config, unrelated). **INFERRED:** a user on a 4K-window local model receives the same 200,000-token compaction threshold as a user on Claude Opus — compaction will essentially never fire before the real window overflows.

Output caps are separately hardcoded per path: `max_tokens=4096` for `_call_openai`/`_call_ollama`, `16384` for the agent loop (**VERIFIED** `services/agent.py:4504`). These are output limits, not window awareness, but confirm the same pattern.

---

### 6. Embeddings and memory

- **Model:** `all-MiniLM-L6-v2`, 384-dimensional. **VERIFIED** it is hardcoded as a module constant in *three independent places*: `conversation_memory.py:44`, `pipeline/context_pruner.py:24`, `services/sensitivity_classifier.py:116`.
- **Dimension is not hardcoded in a schema.** **VERIFIED by absence** — no bare `384` literal exists in the tree. ChromaDB collections are created via `get_or_create_collection(...)` with no fixed-size declaration; HNSW locks dimensionality implicitly at first insert.
- **The configuration key is dead.** **VERIFIED** `core/__init__.py:1469` declares `"embedding": {"provider": "local", "model": "all-MiniLM-L6-v2"}` inside `capability_routing`. **VERIFIED by grep** — `grep -rn "capability_routing" src/agent_friday --include=*.py | grep -i embed` returns **zero matches**: no code anywhere reads `capability_routing.embedding.model`. Changing this setting through the UI or API has **no effect whatsoever** on the embedding model actually used.
- **Two collections, two embedding code paths.** `conversation_memory.py:144-160` wires an explicit `SentenceTransformerEmbeddingFunction`; `services/knowledge_graph/indexer.py:586` and `retrieval.py:61` call `get_or_create_collection("knowledge-graph")` with **no `embedding_function` argument**, silently falling back to Chroma's bundled ONNX `DefaultEmbeddingFunction`. Dimensions happen to match today, so nothing breaks — but the two stores' vectors are produced by different runtimes and are not guaranteed mutually comparable.
- **What breaks on a model change:** **INFERRED from verified error handling** — `conversation_memory.py` wraps index/search in broad `except Exception: print(...); return None/[]`, per its stated design rule that *"a chat must NEVER fail because memory is unavailable."* A dimension mismatch against an existing on-disk collection would therefore surface as **silent permanent memory loss** — every write and query failing, no user-visible error, just console output. No re-embedding or migration path exists.

---

### 7. Voice and image touchpoints

**Voice — three tiers plus a fallback, all present:**

| Tier | File | Engine | Invocation |
|---|---|---|---|
| Cloud live | `services/voice_engine.py` | Gemini Live (native audio) | `google.genai` SDK over WebSocket |
| Cloud TTS | `services/voice_engine.py:550-614` | `gemini-2.5-flash-preview-tts` | SDK `generate_content` |
| Local Tier-1 (default) | `services/local_voice.py` | faster-whisper ASR + Piper TTS | in-process Python libs, CPU |
| Local Tier-2 (GPU) | `services/nemo_voice.py` | NeMo Nemotron ASR + FastPitch/HiFi-GAN | in-process, CUDA |
| Last-resort TTS | `services/voice_engine.py:427-497` | `pyttsx3` (Windows SAPI5) | in-process |

- `services/voice_installer.py:109-131` installs the Tier-2 stack via `subprocess.Popen` running pip, driven from `POST /api/voice/setup/install`. **This is an existing in-app provisioning mechanism — see §11 and §Provisioning gap.**

**Image:** cloud-only, Gemini (`services/creative_engine.py`). **VERIFIED by absence:** no local image backend, no ComfyUI, no Stable Diffusion anywhere in the tree.

---

### 8. Lifecycle and health — the weakest surface

**`/api/health` cannot fail.**
- **VERIFIED** `routes/core_routes.py:207-208` — the response dict opens with a literal `"status": "ok"`, unconditional. A completely unconfigured install with no API keys returns `status: ok`.
- Provider "active" flags at `core_routes.py:154-157` are `bool(core.ANTHROPIC_API_KEY)` / `bool(core.GEMINI_API_KEY)` — **presence of a non-empty string**, nothing more.

**Provider health for the two primary providers is key-presence only.**
- **VERIFIED** `services/provider_health.py:222-243` — read in full. The function returns `{"status": "missing"}` if no key; performs a live check **only** when `deep=True` **and** `ptype == "openai-compatible"` — and even that live check is `GET {base}/models`, a *listing* call, not an inference call. The final line is an unconditional `return {"provider": name, "status": "ok", "detail": "key present"}`.
- **VERIFIED** `routes/platform.py:683` — `/api/health/full` calls `provider_health.check_all(deep=False)`. **`deep=True` is never passed by any route.**
- **INFERRED, with high confidence:** a revoked, rate-limited, or zero-credit Anthropic or Gemini key reports `"ok"` on every health surface Friday exposes. This is exactly the "healthy while inference is broken" failure mode. It is also the failure mode Stephen has hit before — the memory record `project_voice_mode_fix` documents a revoked Gemini key diagnosed only by a `1008` error at call time, not by any health check.

**The one check that would actually prove inference works is dead code.**
- **VERIFIED** `routing/ollama_manager.py:196` — `def health_check(self, model):` POSTs a real `/api/generate` request (`"Say hello in one word."`, `num_predict: 10`) and returns whether non-empty text came back.
- **VERIFIED by grep** — `grep -rn "health_check" src/agent_friday --include=*.py` returns **exactly one line: the definition itself.** Zero call sites. The only genuine inference-proving check in the codebase is never invoked.

**Other health endpoints** (`/api/memory/health`, connectors health, `local_voice.health()`) are all file-existence, count, or token-presence checks — none exercises the subsystem it reports on.

**A better mechanism exists but is not surfaced:** `services/provider_health.py:54-146` maintains a rolling 15-minute window of *real* call outcomes recorded by the actual adapters. This genuinely reflects inference success. But the health routes call `check_all()` (the shallow key-presence path), not `stats()`/`availability()`.

---

### 9. Configuration surface

**Model overrides — six distinct surfaces, two of them dead:**

1. **VERIFIED** `core/__init__.py:1456` — `"capability_routing": {` — the canonical capability→`{provider, model}` map covering `reasoning`, `subagent`, `creative_image`, `creative_video`, `creative_music`, `voice`, `asr`, `tts`, `embedding`, `local`.
2. Legacy flat mirror keys (`orchestrator_model`, `subagent_model`, `creative_model`, `music_model`, `voice_model`), reconciled with the above by `_sync_capability_routing()`. Two overlapping surfaces for one concern.
3. `model_routing` block: `mode`, `local_model`, `task_overrides`, `default_cloud_model`, `openai_model`, `openai_base_url`, `ollama_url`.
4. HTTP: `GET/POST /api/settings`, `GET /api/models`, `GET /api/capabilities`, full provider CRUD at `/api/providers*`.
5. Dropping a descriptor file into `~/.friday/providers/<name>.json|yaml` adds providers and models with zero code change.
6. CLI: `friday model`, `friday config set/get/list`, plus the interactive `setup_wizard.py`.

**Dead surfaces (stored, exposed, never read by dispatch):**
- **VERIFIED** `capability_routing.embedding.model` — zero readers (§6).
- **VERIFIED** `.fridayhints` `preferred_model` — `grep -rn "preferred_model" src/agent_friday --include=*.py` returns exactly three lines: the parse (`services/hints.py:17`), the merge (`hints.py:30`), and the API echo (`routes/platform.py:564`). **Nothing in `services/agent.py`, `services/model_router.py`, or `routing/model_router.py` reads it.** A user can set a per-workspace preferred model, see it returned by `GET /api/hints`, and have it influence nothing.

**Env vars never carry model ids** — only API keys. **VERIFIED by grep.** (`ANTHROPIC_MODEL` at `core/__init__.py:722` and `FRIDAY_LIVE_MODEL` at `voice_engine.py:632` are the two exceptions, both defaults rather than a general mechanism.)

**Device/hardware profile concept: ABSENT.** **VERIFIED by grep** — `device_profile`, `hardware_profile`, `DeviceProfile`, `HardwareProfile` return **zero matches** across `src/`. Broader `profile` hits are all unrelated domains (interest profile, persona profile, vault access profile, federation trust profile). The nearest analog is the voice-engine tier setting (`auto|local|local-gpu|gemini`), a single subsystem-scoped CPU/GPU gate.

---

### 10. Hardware awareness

**Detection exists and is real:**

- **VERIFIED** `routing/ollama_manager.py:134` — `def detect_hardware(self):` — GPU name and total VRAM via `nvidia-smi --query-gpu=name,memory.total`, RAM via `psutil.virtual_memory()` with `wmic` (Windows) and `/proc/meminfo` (Linux) fallbacks.
- **VERIFIED** `services/nemo_voice.py:166` — `def gpu_tier_ready() -> bool:` — CUDA availability, device name, free/total VRAM via `torch.cuda.mem_get_info`, gated on `MIN_VRAM_GB = 4.0`.
- **VERIFIED** `services/compute_provider.py:97` — `def _compute_specs() -> dict:` — CPU cores and RAM. Its `gpu_model`/`gpu_vram_gb` keys are declared and **never populated** — no GPU detection runs in this path, so federation peers are advertised a machine with no GPU.

**Where detected values are actually used — two narrow places, neither touching chat:**

1. **Ollama install recommendations.** `recommend_models()` (`routing/ollama_manager.py:182`) tiers VRAM/RAM → qwen3 4b/8b/14b/32b, surfaced via `GET /api/ollama/models`, `/api/health/full`, and the setup wizard. **Critically: `recommend_models()` is never called by the dispatch path.** `routing/model_router.py:_pick_local_model` chooses among *already-installed* models by reported install size and re-detects nothing about the host.
2. **Voice tier gate.** `gpu_tier_ready()` decides CPU (Whisper+Piper) vs GPU (NeMo) for the voice subsystem only.

**Nothing detected feeds chat, image, or embedding model selection.** Those are purely user-declared. **INFERRED:** the "assess the hardware, recommend models that fit" direction Stephen describes exists today only as a hardcoded qwen3 ladder for Ollama installs — the mechanism is present but is neither general nor connected to routing.

**Single-GPU assumption, with a concrete parsing bug:** `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` emits **one line per GPU**. `detect_hardware()` does `result.stdout.strip().split(",")` and reads `parts[0]`/`parts[1]` — it does not iterate lines. On a multi-GPU host, VRAM parsing is undefined. `nemo_voice.py` likewise uses `torch.cuda.current_device()` with no device-index parameter — there is no way to pin voice inference to a non-default GPU. (Not a defect on this single-4070 instance; recorded for the server/multi-GPU form factor.)

---

### 11. Model acquisition

**What exists today:**

| Mechanism | Downloads | Checksum? | Cache |
|---|---|---|---|
| `ollama_manager.pull_model()` | delegates to Ollama daemon `/api/pull` | daemon's own | Ollama's store |
| `local_voice.PiperTTS._ensure_voice_file()` | raw `urllib` GET from HF | **none** | `~/.friday/local_voice/piper/` |
| `faster_whisper.WhisperModel(download_root=...)` | third-party lib | lib's own | `~/.friday/local_voice/whisper/` |
| `nemo_voice` `from_pretrained()` | third-party lib | lib's own | `~/.friday/models/nemo/` |
| `voice_installer.py` | pip via `subprocess.Popen` | pip's own | app venv |

- **VERIFIED** `services/local_voice.py:361-384` — `_ensure_voice_file()` streams `f"{_PIPER_HF_BASE}/{rel}"` (`_PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"`, line 61) to a `.part` temp file via `urllib.request.urlopen(url, timeout=30)`, then atomically renames. **No hash or signature verification of the downloaded bytes anywhere in this function.**
- **VERIFIED by absence** — `huggingface_hub` / `snapshot_download` appear **nowhere** in `src/`. All HF interaction is either raw `urllib` or delegated to a library's internal downloader.
- **No module performs checksum verification of any model artifact.** A model-catalog-with-integrity would be new code regardless of placement.

**Where `model_catalog.py`'s data comes from — a three-way merge, verified:**

- **VERIFIED** `services/model_catalog.py:247` — `def build_catalog() -> dict:` merges (a) static hardcoded `models` lists from `provider_registry.DEFAULT_PROVIDERS` plus any user-dropped descriptor files, (b) a **live** query of locally-installed models via `ollama_manager.list_models()` → `GET /api/tags`, and (c) a disk cache at `~/.friday/cache/models/{provider}.json`.
- **VERIFIED** `services/model_discovery.py:257` — `def refresh_models(provider, timeout: float = 20.0) -> dict:` performs the real network fetch (OpenRouter `/api/v1/models`, OpenAI-compatible `/models`, HF router `/v1/models`), TTL 86400s, run by a background thread and by `POST /api/providers/<name>/models/refresh`. The catalog builder itself reads only the cache, never the network — this separation is documented in the module docstring.

**Where a catalog would have to live:** `services/model_catalog.py` (already the documented "single source of truth for the model picker," already multi-source by design) plus `services/model_discovery.py`'s parser/TTL/background-refresh infrastructure. There is currently **no discovery of what Ollama models exist to be pulled** — only the hardcoded qwen3 ladder and a listing of what is already installed.

---

### 12. Portability bindings

**Correctly guarded (informational):** `mcp_client.py:54`, `core/__init__.py:124`, `routing/ollama_manager.py:14,160`, `server.py:266,301-306` (`msvcrt` vs `fcntl`), `services/agent.py:1339` (`os.startfile` / `open` / `xdg-open` — a well-built cross-platform branch).

**Unguarded — `friday_tray.py` will crash on POSIX. VERIFIED:**
- `friday_tray.py:33` — `CREATE_NO_WINDOW = 0x08000000  # Windows: suppress child console`
- `friday_tray.py:86` — passed as `creationflags=CREATE_NO_WINDOW` to `subprocess.Popen`
- `friday_tray.py:131,133` — `os.startfile(...)`
- **VERIFIED by grep**: `grep -n "sys.platform\|os.name" friday_tray.py` returns **nothing**. Every other module that touches `creationflags` guards it; this one does not. On POSIX, `Popen(creationflags=...)` raises `ValueError` — the tray dies before the server it launches ever starts.

**Credential storage degrades to plaintext off-Windows. VERIFIED** `services/credential_store.py:151` — `def protect(data: bytes) -> tuple[bytes, str]:` has exactly three tiers: vault-key AES-GCM → Windows DPAPI → **plaintext with a printed warning**. There is no macOS Keychain and no Linux Secret Service backend. A macOS/Linux install without `FRIDAY_PASSWORD` set stores provider API keys and OAuth tokens in plaintext at rest.

**Port 3000 is pinned as a literal while the server may bind elsewhere. VERIFIED:**
- `services/calendar_engine.py:967` — `GOOGLE_DESKTOP_REDIRECT_URI = "http://localhost:3000/api/google/auth/callback"`
- `services/google_accounts.py:808` — `MULTI_DESKTOP_REDIRECT_URI = "http://localhost:3000/api/google/accounts/callback"`
- `server.py:230-253` — the port picker will silently move the server to 3001+ if 3000 is occupied.
- **INFERRED:** if the server ever falls back off port 3000, Google OAuth breaks, because the redirect URIs are literals rather than being derived from the actually-bound port. `google_oauth.redirect_base_override` exists as an escape hatch but is empty by default.

**Windows-only launch and ops layer:** `ops/*.ps1` (hosts-file edits, `LocalMachine\Root` cert store, SYSTEM scheduled task, `C:\ProgramData\AgentFriday\`) has no launchd/systemd counterpart. The env bootstrap `_bootstrap_env_from_launch_scripts()` parses Windows `.bat` `set NAME=VALUE` lines — there is no `.sh` equivalent, so on POSIX the mechanism is a silent no-op.

**Correction to a claim worth recording:** an initial pass flagged live plaintext API keys and `FRIDAY_PASSWORD` as committed to version control in `start.bat` / `friday_startup.bat`. **This is false and was verified false.** `git ls-files --error-unmatch start.bat` errors (untracked), and `.gitignore:2-8` ignores `start.bat`, `friday_startup.bat`, `launch_now.bat`, `do_commit.bat`, and `*.bat` with `!install.bat` re-included. The secrets are local-only, on-disk plaintext in gitignored launcher scripts — a real hardening consideration, but **not** a repository leak. Recorded here precisely because the stronger claim would have been alarming and wrong.

**Single-user by construction:** `core/__init__.py:204` — `FRIDAY_USERNAME = os.environ.get("FRIDAY_USERNAME", "admin")` and the companion `FRIDAY_PASSWORD` are process-wide module globals compared with `hmac.compare_digest`. No user table, no tenant namespace, no per-user session isolation. The singleton `OllamaManager` (shared model/hardware caches), the process-global settings cache, and the machine-wide single-instance file lock (`server.py:275-322`) are the other three blockers for a multi-tenant server form factor.

---

### 13. Ops reality

**The running app never starts, supervises, or restarts Ollama.**

- **VERIFIED by absence** — `server.py` contains zero references to `ollama serve` and never spawns the Ollama binary; its only Ollama mention is an informational print string. `friday_tray.py`'s `start_server()` spawns `server.py` and nothing else.
- The app instead probes at call time: `OllamaManager.is_available()` (3s HTTP probe of `GET /api/tags`, cached 30s) is consulted throughout routing, health, and catalog code purely to decide whether to *offer* local models.
- On failure it fails soft with a remediation string — `services/worker_adapters/ollama_adapter.py:43` returns literally *"Ollama is not running. Start it with `ollama serve`."*

**Provisioning happens once, at install time, best-effort:**
- `scripts/install.ps1:144-190` — checks `Get-Command ollama`; installs via `winget install --id Ollama.Ollama` or a direct `OllamaSetup.exe` download; then `Start-Process ollama serve -WindowStyle Hidden`; then pulls `gemma3:4b`. Explicitly non-fatal, skippable via `FRIDAY_SKIP_MODEL=1`.
- `scripts/install.sh:138-175` — the POSIX equivalent (`brew install ollama` / `curl … | sh`).
- `scripts/install.bat:64-78` — the weakest path: only *prints* "install from https://ollama.com then run: ollama pull gemma3:4b".

**A real first-run flow exists** (`setup_wizard.py`, `friday setup`): it probes Ollama, delivers privacy-posture messaging conditioned on its presence, and calls `detect_hardware()` for size recommendations — but it never launches the daemon.

**In-app provisioning precedent exists for voice:** `voice_installer.py` pip-installs the Tier-2 GPU stack from a UI action. This is the closest thing to a general provisioning mechanism in the codebase and is the natural seam for the broader "help the user download and install models" direction.

---

### 14. Test coverage over §§1–13

**Strong:**
- Routing layer — `tests/unit/test_model_router.py` (~700 lines): `classify_task`, `needs_vault_access`, `_pick_local_model`, `anthropic_to_openai_tools`, `route()` for cloud_only / local_preferred / vault-forced modes.
- `tests/unit/test_ollama_manager.py`, `test_provider_descriptors.py`, `test_provider_health_stats.py` (circuit-breaker trip/cooldown/half-open/ordering).
- Voice — dedicated files for both tiers (`test_local_voice.py`, `test_nemo_voice.py`) plus live-helpers, tuning, model-validation, PII-gate, and a `tests/smoke/test_voice_tiers.py`.
- Image — `tests/unit/test_creative_engine.py` including safety-gate cases; API-level generation routes with a `mock_gemini` fixture.
- Onboarding — `tests/unit/test_onboarding.py` + `tests/api/test_onboarding_routes.py`.
- Health — `/api/health` shape test; `/api/health/full` exercised indirectly.

**The named regression gap:** `tests/api/conftest.py:78-107` defines an **autouse** `_no_real_llm` fixture that patches `_generate_text`, `_call_claude`, `_generate_agent`, `_call_claude_agent`, `_call_ollama`, `_call_openai`, `_oai_agentic_loop`, and `get_anthropic_client` **into every loaded project module**, unless a test carries `@pytest.mark.real_provider_paths`. Only **7 files** carry that marker. **INFERRED, and this is the highest-value testing finding:** a defect introduced inside the real body of `_call_claude` / `_call_ollama` / `_call_openai` — HTTP request construction, header assembly, JSON parsing, error mapping, the 429 retry — on any path not covered by those 7 files would ship with the other ~50 API test files passing green, because they never execute the real function.

**Would ship today with nothing to catch it:**
1. A bug in provider HTTP request construction outside the 7 `real_provider_paths` files (above).
2. Any health-check regression that makes health *more* optimistic — the existing test asserts `200` and dict shape, and `"status": "ok"` is a literal, so it cannot fail.
3. Context-window handling: no test asserts that compaction fires appropriately for a small-window model, because no code consults the window.
4. The two dead config surfaces: no test asserts that setting `capability_routing.embedding.model` or `preferred_model` changes behaviour — correctly so, since it does not.
5. POSIX breakage such as the `friday_tray.py` crash — **UNKNOWN** whether CI runs a non-Windows leg; `.github/workflows/` was not inspected.

---

## Risk register — ranked by blast radius

| # | Risk | Blast radius | Evidence |
|---|---|---|---|
| **R1** | **Health checks report `ok` while inference is broken.** `/api/health` hardcodes `"status": "ok"`; provider health for Anthropic/Gemini is key-presence only; `deep=True` is never passed; the one inference-proving function has zero call sites. | **Whole system.** Every operator decision, dashboard, and automated recovery path built on health is built on a signal that cannot report failure. Stephen has already lost time to a revoked key that health would have called healthy. | §8, VERIFIED |
| **R2** | **No context-window awareness.** Three reduction layers use hardcoded constants (200k tokens / 2M chars / 50 turns) while real per-model window data sits unread in the catalog. | **Every non-Claude-Opus model.** Small-window local models overflow with no compaction; large-window models get compacted needlessly. Scales directly against the multi-device direction, where small models are the norm. | §5, VERIFIED |
| **R3** | **`worker_adapters/ollama_adapter.py` bypasses the egress gate**, hardcodes the base URL, and uses a non-tool-calling endpoint. | **Security contract.** The one architectural invariant the codebase explicitly protects — that the fail-closed egress contract cannot drift between paths — has an undocumented hole. Local-only today, so no live data exfiltration, but the invariant is not actually invariant. | §2, VERIFIED |
| **R4** | **Two live configuration surfaces are dead**: `capability_routing.embedding.model` and `.fridayhints` `preferred_model`. Both are stored, both are returned over HTTP, neither is read. | **User trust.** A setting that visibly exists and silently does nothing is worse than an absent one; it produces confident, wrong mental models and unreproducible bug reports. | §6, §9, VERIFIED |
| **R5** | **No hardware-profile concept**, and hardware detection feeds only Ollama install advice and a voice CPU/GPU gate — never chat/image/embedding selection. | **The stated architectural direction.** This is the single largest gap between where the code is and "assess the hardware and recommend models that fit." | §9, §10, VERIFIED by absence |
| **R6** | **~50 of ~57 API test files stub the real provider call bodies.** | **Regression detection.** The most security- and correctness-critical code in the repo is the least exercised by the bulk of the suite. | §14, VERIFIED |
| **R7** | **Google OAuth pins `localhost:3000` as a literal** while the server may bind 3001+. | Auth breaks entirely, non-obviously, whenever port 3000 is occupied at launch. | §12, VERIFIED |
| **R8** | **`friday_tray.py` crashes on import under POSIX** (unguarded `creationflags` / `os.startfile`). | Blocks the macOS/Linux form factor at the very first step. Zero-cost fix; currently invisible because CI appears Windows-only. | §12, VERIFIED |
| **R9** | **No checksum verification on any downloaded model artifact**; Piper voices are fetched by raw `urllib` from an HF `resolve/main` URL. | Supply-chain integrity. A compromised or corrupted artifact is accepted silently. Grows with every component the provisioning story adds. | §11, VERIFIED |
| **R10** | **Credential storage falls back to plaintext off-Windows** (no Keychain / Secret Service). | Blocks a trustworthy macOS/Linux release. Windows-only today, so not currently live. | §12, VERIFIED |
| **R11** | **No Anthropic client timeout set in-repo.** | A hung Anthropic request has no in-repo bound; the SDK default governs. Low severity, trivially fixed, listed for completeness. | §3, VERIFIED by absence |
| **R12** | **Multi-GPU `nvidia-smi` parsing is undefined** (splits by comma without iterating lines); no CUDA device-index selection. | Server / multi-GPU form factor only. Not a defect on this instance. | §10, VERIFIED |
| **R13** | Local plaintext secrets in gitignored launcher `.bat` files. **Not a repo leak** — verified untracked and gitignored. | Local disk exposure only. Recorded for completeness and to correct the record. | §12, VERIFIED |

---

## Provisioning gap — APPENDIX components mapped to integration points

| APPENDIX component | Integration point in code today | Gap |
|---|---|---|
| **Ollama brain** (`qwen3.6:35b`) | **Present and wired.** `routing/ollama_manager.py` (list/pull/chat), `services/model_router.py:_call_ollama`, `model_seat_gate` conformance. Base URL configurable. | None structurally. But `_pick_local_model` picks by *install size*, not by measured capability or fit — a 23GB brain on a 12GB card will be selected on size alone with no VRAM check at dispatch time. |
| **Ollama sidekick** (`gemma4:e4b`) | Same path. `settings.model_routing.local_model` selects it. | No concept of a *resident small model* distinct from the brain — the router picks one local model per request; there is no "keep the sidekick warm, load the brain on demand" orchestration. |
| **Ollama embedding** (`qwen3-embedding:0.6b`) | **No integration point at all.** Embeddings are hardcoded to in-process `all-MiniLM-L6-v2` in three places; `capability_routing.embedding` is dead (§6). | Total. Wiring an Ollama embedding model requires (a) making the dead config key live and (b) a migration path, since changing dimensions silently destroys the existing Chroma collections. |
| **Kokoro TTS** | **Absent.** No reference anywhere. | The TTS seam exists and is clean — `local_voice.PiperTTS` is one class behind `LocalVoiceEngine.resolve_tier()`, so Kokoro would slot in as a peer class. Provisioning precedent: `voice_installer.py`. |
| **faster-whisper STT** | **Already integrated and shipping.** `services/local_voice.py:317-331`, `WhisperModel(device="cpu", compute_type="int8", download_root=WHISPER_DIR)`. Models cache to `~/.friday/local_voice/whisper/`. | None. Phase 2 will install a **second, isolated copy** in a dedicated venv — see the overlap note below. |
| **Piper TTS** | **Already integrated and shipping.** `services/local_voice.py:348-463`, `PiperVoice.load(...)`, voices auto-downloaded to `~/.friday/local_voice/piper/`. | None. Same duplication note. |
| **ComfyUI + Z-Image Turbo** | **Absent — and the architecture assumes its absence.** All image generation is a cloud Gemini SDK call in `creative_engine.py`; verified zero hits for any local image backend. | Total. There is no local-image seam, no "image provider" abstraction, and `capability_routing.creative_image` currently only ever resolves to a cloud provider. This is the largest single integration gap in the APPENDIX. |
| **Cloud escalation path** | **Present and mature.** Ordered attempt ladder, health-aware ordering, circuit breaker, vault-forced-local refusal. | None. This is the most complete part of the stack. |

**Overlap note (not a conflict).** Two existing provisioning mechanisms touch APPENDIX components: `scripts/install.ps1` installs Ollama and pulls `gemma3:4b`, and `services/voice_installer.py` pip-installs a voice stack into the app's own venv from a UI action. The APPENDIX instructs installing faster-whisper and Piper into a **new dedicated venv outside the repo**, which duplicates libraries the app already carries. This is duplication, not contradiction — the isolated venv proves the reference stack works standalone without touching the running app's environment, and nothing in the APPENDIX countermands what the installers do. **Assessment: no HARD GATE conflict; Phase 2 proceeds.** The duplication is deliberate and is recorded again in the Phase 2 report.

**Placement convention.** The repo's implied convention for model artifacts is `~/.friday/` (`core/__init__.py:527` `FRIDAY_DIR = HOME / ".friday"`; `local_voice.py:45-47`; `nemo_voice.py` `NEMO_CACHE_DIR`). However, `~/.friday` is the **live application's data home** and this audit's operating constraints place it out of scope for writes. Phase 2 therefore creates a single new directory outside both the repo and `~/.friday`, and records its absolute path. This deviation from the implied convention is deliberate and is raised as decision question Q7.

---

## Unknowns requiring runtime verification

1. **Anthropic SDK default timeout** — no `timeout=` is set in-repo. Resolve with `pip show anthropic` and reading that version's `DEFAULT_TIMEOUT`.
2. **Full contents and count of `CLAUDE_TOOLS`** — its single-registry role is verified from every call site, but the literal was not enumerated. Resolve by reading the definition in `services/agent.py`.
3. **Behaviour of `_call_openai` against a non-tool-calling OpenAI-compatible endpoint** — no conformance gate exists for that path. Resolve by pointing Friday at such an endpoint and observing whether it errors, hangs, or silently drops tools.
4. **Whether any UI surface reads `provider_health.stats()`/`all_stats()`** (the real measurement plane) rather than the shallow `check_all()`. Resolve by grepping the built UI bundle and remaining routes.
5. **Whether `worker_adapters/ollama_adapter.py`'s egress-gate bypass is a deliberate scope decision** — the code carries no comment either way, unlike the Gemini gap which is explicitly annotated. Resolve by asking Stephen (see Q2).
6. **Whether CI runs a non-Windows leg** — `.github/workflows/` was not inspected. Determines whether R8 would ever be caught.
7. **Actual behaviour on context overflow with a small-window local model** — whether Ollama errors or silently truncates. Determines whether R2 manifests as a crash or as quiet quality loss.

---

## Decision questions for Stephen

Each is answerable in one sentence.

1. **Should `/api/health` be able to fail?** — i.e. should health perform a real inference probe (the dead `ollama_manager.health_check` already does exactly this) and report `degraded`/`down`, accepting the cost and latency of a live call?
2. **Is `worker_adapters/ollama_adapter.py`'s egress-gate bypass intentional** (orchestrator subtasks deemed always-local, never sensitive), or an unreviewed gap to close?
3. **Should context-window handling become model-aware** by reading the `context_window` the catalog already fetches, or is the single 200k assumption acceptable until multi-device work begins?
4. **Do you want a real hardware-profile concept now**, or should hardware stay advisory (install recommendations only) until the routing work this audit is meant to enable?
5. **Should the embedding model become configurable** (making `capability_routing.embedding.model` live), accepting that changing it requires a Chroma re-index/migration path that does not exist today?
6. **Should `.fridayhints` `preferred_model` be wired into dispatch, or deleted** — is a dead per-workspace model override worth keeping as a stub?
7. **Is placing the Phase 2 runtime stack outside `~/.friday` correct**, or would you rather the reference stack live under `~/.friday/` alongside the app's existing model caches, following the repo's own convention?
8. **Should local image generation (ComfyUI) become a routed capability** behind `capability_routing.creative_image`, or remain a standalone tool outside the router until the abstraction is designed?
9. **Should the `real_provider_paths` marker be inverted** — i.e. real provider bodies exercised by default with stubs opt-in — given that ~50 API test files currently cannot catch a provider-layer regression?
10. **Do you want the `friday_tray.py` POSIX crash and the `localhost:3000` OAuth pin fixed now** as cheap portability groundwork, or deferred until a macOS/Linux target is actually scheduled?
