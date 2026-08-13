# Phase A — Truth Flow

**Date:** 2026-08-13
**Branch:** `phase-a-truth-flow`, off `fix/toolcall-integrity-v5` at `656b70b`. **Unpushed, unmerged.**
**Decisions inherited from:** [`decisions-2026-08.md`](./decisions-2026-08.md) (D1–D10).
**Companions:** [`inference-discovery.md`](./inference-discovery.md), [`provisioning-report.md`](./provisioning-report.md).

**Evidence registers, as before:**
- **VERIFIED** — the author ran the command or read the cited line and saw the output.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

**Theme.** Every item makes some part of the system able to tell the truth about itself: a health endpoint that can fail, a test default that runs the real code, a gate that cannot be skipped by omission, a context budget that reflects the actual model, a memory store that refuses to corrupt itself silently, an OAuth URI that matches reality, a brain whose speed is measured rather than assumed, and a runtime root that is declared rather than invented.

---

## Commit list

| Commit | Item |
|---|---|
| `0f07ee5` | docs: record August 2026 architecture decisions (D1–D10) |
| `378e204` | A1: invert the provider-path test default (D9) |
| `663826c` | A2: make `/api/health` able to fail with a real inference probe (D1) |
| `3895436` | A3: close the orchestrator adapter's egress bypass (D2) |
| `afe8c50` | A4: budget context from the catalog, not a flat constant (D3) |
| `2a158ca` | A5: refuse writes that would mix embedding spaces (D5) |
| `6e0ddce` | A6: derive the Google OAuth redirect from the bound port (D10) |
| `7495c1c` | A8: give the local runtime stack a declared, overridable home (D7) |

**A7 has no commit — by design.** It is environment and configuration work (llama.cpp binaries, a GGUF, a provider descriptor in `~/.friday/providers/`, a settings key). It changed no repo source file. Its evidence is §A7 below plus `%USERPROFILE%\.friday\runtime\logs\ncpumoe-sweep.json`.

---

## Per-item status

| Item | Status | Test |
|---|---|---|
| A1 — invert `real_provider_paths` | **VERIFIED WORKING** | `tests/api/test_inverted_provider_default.py` |
| A2 — real health probe | **VERIFIED WORKING** | `tests/api/test_health_inference_probe.py` |
| A3 — close the egress bypass | **VERIFIED WORKING** | `tests/api/test_worker_adapter_egress.py` |
| A4 — catalog-driven context windows | **VERIFIED WORKING** | `tests/unit/test_context_window_budget.py` |
| A5 — embedding-dimension assertion | **VERIFIED WORKING** | `tests/unit/test_embedding_dimension_guard.py` |
| A6 — unpin the OAuth redirect | **VERIFIED WORKING** | `tests/api/test_oauth_redirect_port.py` |
| A7 — brain migration to llama-server | **VERIFIED WORKING** | measured, see §A7 |
| A8 — relocate the runtime root | **VERIFIED WORKING** | `tests/unit/test_runtime_dir.py` + 5 re-run smoke tests |

**Nothing was STOPPED.** One item (A7) was paused mid-flight for a resource decision only Stephen could make; he chose to reclaim the redundant Ollama copy, and A7 completed. That pause is recorded in §A7 rather than hidden.

**Default suite: green, offline, exit 0** — re-run after every item. **VERIFIED**: 5369 tests collected; final run `pytest -q` exit 0.

---

### A1 — Invert the provider-path test default (D9) — `378e204`

**The defect.** `tests/api/conftest.py` autouse-stubbed `_call_claude`, `_call_ollama`, `_call_openai`, `_generate_text`, `_generate_agent` and `_oai_agentic_loop` with lambdas unless a test opted in via `real_provider_paths`. Six files opted in, so **roughly 50 of 57 API test files never executed a line** of the request construction, header assembly, JSON parsing, error mapping or 429-retry logic they nominally covered.

**Fails-before, VERIFIED.** With the change stashed, the new tests failed on the property itself, not on a missing symbol:

```
E  AssertionError: _call_claude is stubbed by default — the D9 inversion regressed
   where 'conftest' = <function _no_real_llm.<locals>._stub_text>.__module__
E  requests.exceptions.ConnectionError: HTTPSConnectionPool(host='api.example.invalid', port=443)
```

That second failure is its own finding: **the old suite was not offline by construction.** `urllib.request.urlopen` was never patched, so a test could reach a developer machine's live Ollama daemon on `localhost:11434`, and `requests.post` genuinely attempted DNS resolution to an external host.

**The change.** `tests/fake_backends.py` supplies wire-shaped doubles for the three transports — the Anthropic SDK client, `requests.post`/`get`, and `urllib.request.urlopen`. The provider functions run for real against them; canned payloads carry `CANNED_TEXT` in each provider's true response shape, so existing assertions keep passing while the code beneath them widens from a lambda to the genuine body. Any URL without a double now raises.

Markers: `network` (needs live network or spends money — deselected by default, `--run-network` to include); `real_provider_paths` kept as a documented no-op so existing files import cleanly.

**A pre-existing bug this exposed, and fixed.** With the blanket re-stub gone, `tests/api/test_smoke.py` began failing on a leaked lambda. Diagnosed to a specific mechanism, **VERIFIED** with a temporary probe:

```
THREADS: ['MainThread', 'Thread-1', 'Thread-2', 'Thread-3']
LEAK: ('agent_friday.server', 'test_kg_reindex_route',
       'test_reindex_tier_b_sync.<locals>.<lambda>')
```

The knowledge-graph reindex route works on a background thread that outlives its test and can copy a patched reference into `agent_friday.server` **after** `monkeypatch` has run its undo. The old default hid this by re-stubbing every provider name at the start of every test — one stub overwriting another, so nothing downstream could tell. Healed explicitly by re-binding the canonical functions before each test (`_heal_leaked_provider_fns`).

Two meta-tests in `test_smoke.py` asserted the old contract (`test_llm_is_stubbed`, `test_anthropic_client_is_sentinel`) and were rewritten to assert the new one: the client must now be *usable* (the real body calls it) yet still never reach the network.

---

### A2 — `/api/health` must be able to fail (D1) — `663826c`

**The defect, VERIFIED against the old code.** With every local backend unreachable and both cloud keys blanked:

```
OLD /api/health status field -> ok
```

`routes/core_routes.py:208` returned a literal `"status": "ok"`, and `provider_health._check` returned `{"status": "ok", "detail": "key present"}` for Anthropic and Gemini on the strength of a non-empty string. `deep=True` was never passed by any route.

**The change.** `provider_health.inference_probe()` sends a genuine one-token generation per provider type:

| Provider type | Probe |
|---|---|
| `ollama` | `ollama_manager.health_check(model)` |
| `anthropic` | `messages.create(max_tokens=1)` |
| `openai-compatible` | `POST /chat/completions`, `max_tokens=1` |

That Ollama call site is the one D1 asks for: `health_check` did a real generation but had **zero callers anywhere in the tree** (Phase 1 §8, VERIFIED by grep). It now has one.

Results cache 60 s — every probe spends tokens and the tray watchdog polls this endpoint. Probe failures degrade to `down`; they never raise into the route. Key presence moved to a separate `configuration` block, and every check carries `proved_inference` so no caller can mistake a configured key for a working backend.

**Same test after the fix, VERIFIED:** `OLD /api/health status field -> degraded` (Ollama down, Anthropic reachable through the test double) — the endpoint can now report failure.

**A deliberate constraint, documented in code.** HTTP stays **200** even when `status` is `down`. `friday_tray.py:46` treats a non-2xx as "server is dead" and would restart a server that is running fine with an unreachable model backend. Liveness and inference health are different signals.

---

### A3 — Close the orchestrator adapter's egress bypass (D2) — `3895436`

**The defect, VERIFIED against the old code.** A worker prompt containing an SSN reached the wire with the gate never consulted in any form:

```
Payload that reached the wire: my SSN is <redacted — synthetic test value>
Egress gate consulted at all?  {'seal': False, 'text': False}
```

`worker_adapters/ollama_adapter.py` hand-built a payload and POSTed it to a hardcoded `localhost:11434` — the only provider call site in the tree skipping the fail-closed contract every other site enforces, and undocumented, unlike the Gemini dispatch gap which carries an explaining comment (`provider_registry.py:168`).

**The change, and why it is not just "call `seal_outbound`".** Adding the call would leave the same class of hole open to the next call site that forgets. `egress_gate.gate_worker_payload()` makes the local-vs-cloud decision **itself**, from the destination, so a call site can no longer opt out by omission:

- destination verified on-device → returned unchanged, no classifier run. That is D2's *"cheap for local traffic rather than optional"*: the traffic is gated, the gate simply has nothing to do.
- anything else → fully sealed, fail-closed; a gate that cannot verify itself raises rather than letting the payload out.

It also gates the Ollama-native `/api/generate` `prompt` field, which `seal_outbound` does not cover — that function only knows the Anthropic `system`/`messages`/`tools` keys, so a bare prompt would have sailed through **even if the adapter had called it**.

**A note on measuring the right thing.** The temporary before-probe spied on `seal_outbound`. After the fix it still "failed" — because the gate now correctly does *nothing* for verified-local traffic. That distinction is the entire point of D2, so the permanent test spies on `gate_worker_payload` (was the gate given the payload?) rather than on the sealing work. Recorded because the naive assertion would have been a misleading green.

**Not changed, recorded for Phase B:** `_OLLAMA_BASE` remains hardcoded, so this adapter ignores `settings.model_routing.ollama_url`. A divergence from the main path, not an egress hole — it pins traffic to loopback, the safest destination.

---

### A4 — Catalog-driven context windows (D3) — `afe8c50`

**The defect, VERIFIED against the old code.** A ~30K-token transcript produced the *same* answer for both models:

```
~30K-token transcript -> 4K-window model compacts? False
~30K-token transcript -> 1M-window model compacts? False
```

`compaction.py:77` used a flat 200,000 tokens and `model_router.py:798` a flat 2,000,000 characters — a threshold whose own comment justifies it for Opus 4.8 specifically. `maybe_compact(messages, model=...)` accepted the model and then ignored it when choosing the window.

**The change.** `model_catalog.context_window_for()` is the single lookup — disk cache only, memoised 5 minutes, never the network on the hot path. `compaction.resolve_context_window()` resolves catalog → configured → 200,000; `model_router._traj_char_limit_for()` derives from the catalog or falls back to the constant. Unknown models still return `None` and every caller falls back — D3's *"only where the catalog lacks a value"*.

**Scope judgement, recorded.** Wiring the lookup alone would have been *nominally* complete and *materially* useless for the case D3 exists for. Descriptors declare no `context_window` and API discovery does not cover Ollama, so every local model — the class with genuinely small windows — would still have hit the 200,000 constant. `OllamaManager.context_length()` reads the daemon's GGUF metadata, matching the architecture-prefixed key by suffix rather than guessing the architecture. **VERIFIED against the live daemon:**

```
gemma4.context_length = 131072
```

This stays inside A4's named surface (catalog-driven context windows) rather than extending it.

---

### A5 — Refuse writes that would mix embedding spaces (D5) — `2a158ca`

**The defect, VERIFIED against the old code.** A simulated 384-vs-1024 mismatch:

```
[MEMORY] index failed (non-fatal): Collection expecting embedding with dimension of 384, got 1024
index() returned: None  (None == 'nothing happened')
```

One console line, `None` returned — **indistinguishable from "memory is switched off"**. Every write and query would have failed forever with nothing reaching the caller. Provisioning measured `qwen3-embedding:0.6b` at **1024 dimensions** against Friday's hardcoded `all-MiniLM-L6-v2` at **384**, so this is a live hazard, not a hypothetical.

**The change.** The collection carries its embedding model and width in metadata, stamped at creation. `index()` asserts the configured embedder still matches **before** the broad `except Exception` that exists to keep transient memory failures out of the chat path. `EmbeddingDimensionMismatch` names the collection, both models and both dimensions.

This is the one failure the module deliberately does **not** swallow: a model swap is a configuration error, not a transient outage.

Two guards against overreach: collections created before the stamp existed are protected by recovering the width from a stored vector and backfilling it; and an **unverifiable** width is never treated as a mismatch, so missing information cannot break graceful degradation for no safety gain.

**Scope held.** Write-time assertion only, per D5. Full configurability stays gated on the re-index path, which does not exist.

---

### A6 — Derive the OAuth redirect from the bound port (D10) — `6e0ddce`

**The defect, VERIFIED against the old code:**

```
OLD single-account redirect: http://localhost:3000/api/google/auth/callback
OLD multi-account  redirect: http://localhost:3000/api/google/accounts/callback
```

Both literals, while `server.py:_resolve_bind_port` scans forward when 3000 is busy — so consent failed with `redirect_uri_mismatch` in exactly the situation the port scan exists to survive.

**The change.** `core.SERVER_PORT` / `core.server_base_url()`, set by `server.py` the moment the port resolves; both helpers build their URI from it. **VERIFIED across ports:**

| Bound port | Redirect |
|---|---|
| 3000 | `http://localhost:3000/api/google/auth/callback` |
| 3001 | `http://localhost:3001/api/google/auth/callback` |
| 8080 | `http://localhost:8080/api/google/auth/callback` |

**What deliberately did not change, and is now test-pinned.** The **host** stays loopback. Google rejects any plain-HTTP non-loopback `redirect_uri` outright, and Stephen reaches Friday through a hosts-file alias (`http://agent.friday/`) — a request-derived host is what broke consent before (`calendar_engine.py` docstring, 2026-08-13). Only the port is dynamic; Google accepts any port on a loopback redirect for installed apps. The reverse-proxy settings override keeps priority over both.

**How the flow was verified — and what was not.** Beyond the helpers, a test asserts the value that reaches the **consent URL** — the string Google actually compares against — carries the bound port, by parsing `redirect_uri` out of the authorization URL built by the real `Flow`. A **full consent round-trip against Google was NOT performed**: it needs interactive approval and would touch Stephen's real accounts. **UNKNOWN** until he runs one; the check that would settle it is a single connect from Settings → Connectors with the server on a non-3000 port.

---

### A7 — Brain migration to llama-server (environment; no commit)

#### Preflight and the pause

llama.cpp **build 10415** (`commit 1d2869c6e`), Windows **CUDA 13.3** x64 prebuilt — matching the detected driver (610.88 / CUDA UMD 13.3) exactly. GGUF: `unsloth/Qwen3.6-35B-A3B-GGUF` → `Qwen3.6-35B-A3B-UD-IQ4_NL.gguf`, **17,475,805,184 bytes (16.28 GB)**.

Mid-item, a resource fact surfaced that changed the risk profile. **VERIFIED**: free disk fell from 27.7 GB to **7.0 GB** while `qwen3.6:35b` was resident, then recovered to 22.1 GB when it unloaded. Cause:

```
Name            AllocatedBaseSize CurrentUsage
C:\pagefile.sys             32619         2332
```

The pagefile balloons to ~32 GB under a 29 GB resident model. Running llama-server at ~8.6 GB free risked exhausting the system drive on Stephen's live machine — a decision that was his to make, not mine. He chose to reclaim the now-redundant Ollama copy of the brain, which A7 migrates *off* Ollama anyway.

```
deleted 'qwen3.6:35b'
free disk after reclaim: 32.5 GB
```

Sidekick and embedder untouched, as the work order requires:

```
gemma4:e2b              7.2 GB
gemma4:e4b              9.6 GB
qwen3-embedding:0.6b    639 MB
```

#### The `--n-cpu-moe` sweep

Swept downward at `-ngl 99`, `--flash-attn on`, `-c 32768`. Lower `--n-cpu-moe` = fewer MoE expert layers held on CPU = more VRAM used.

| `--n-cpu-moe` | tok/s | VRAM (MiB) | host RAM (GB) |
|---:|---:|---:|---:|
| 48 | 13.96 | 4322 | 17.1 |
| 40 | 14.00 | 4320 | 17.0 |
| 32 | 19.21 | 7367 | 19.8 |
| 28 | 16.63 | 8823 | 21.0 |
| 24 | 19.20 | 10274 | 22.2 |
| **20** | **21.59** | **11685** | **23.6** |
| 16 | 5.45 | 11790 | 26.1 |
| 12 | 7.88 | 11752 | 28.7 |
| 8 | 6.05 | 11687 | 31.2 |
| 4 | 5.58 | 11798 | 31.5 |
| 0 | 5.89 | 11788 | 31.6 |

**The spill is unmistakable at 16**: throughput collapses from 21.6 to 5.5 tok/s while VRAM saturates (~11.8 GB of 12.28) and host RAM climbs to 31.6 GB of 31.9 — the allocator thrashes rather than erroring. **Backed off one step → `--n-cpu-moe 20`**, exactly as the work order specifies. Raw data: `%USERPROFILE%\.friday\runtime\logs\ncpumoe-sweep.json`.

#### Measurement — before and after

Same fixed prompt as the 14.4 tok/s baseline (*"Write one paragraph about the ocean."*, 200 tokens max).

| | Backend | tok/s | Resident | Placement |
|---|---|---|---|---|
| **Before** | Ollama `qwen3.6:35b` | **14.4** | 29 GB | 66% CPU / 34% GPU |
| **After** | llama.cpp IQ4_NL, `--n-cpu-moe 20` | **25.08** (median of 24.19 / 25.62 / 25.08) | 11.6 GB VRAM + 23.7 GB host RAM | GPU-dominant |

**+74% throughput.** Confirmed post-relocation at 23.75 tok/s.

**A contended reading, reported because it is instructive.** An interim "before" measurement taken while the 16 GB download was running returned **4.71 tok/s** with host RAM at 30.7 of 31.9 GB. It is not the baseline — it is evidence of what memory contention does to this model, and why the uncontended 14.4 figure is the honest comparator.

**Resident RAM after:** 23.7 GB of 31.9 GB. **VRAM: 11645 MiB used, 370 MiB free.**

> **The brain and the sidekick cannot be co-resident on this card.** At the optimum the brain holds ~11.6 GB of 12.28 GB. This hardens the never-co-resident rule from a provisioning convention into a measured constraint, and is the strongest single input to Phase C's residency scheduler.

#### Repointing Friday — existing configuration only

No routing logic was added. Two existing surfaces:

1. A provider descriptor at `~/.friday/providers/llama-cpp-brain.json` — the documented drop-in mechanism. **VERIFIED** the registry accepts it:

```
found         : True
base_url      : http://127.0.0.1:8081/v1
classification: local (local = stays on device)
adapter       : openai-compatible
available     : True
```

`classification: local` is earned, not claimed: `openai-compatible` is a local-capable adapter and the base URL is loopback, so the egress gate treats brain traffic as on-device — correct, and verified by the same call-time rule A3 relies on.

2. `settings.capability_routing.reasoning` → `{provider: llama-cpp-brain, model: qwen3.6-35b-a3b-iq4nl}`, with `orchestrator_model` kept in sync. Settings backed up to `settings.json.bak-phase-a` first. `subagent`, `embedding` and `local` were left untouched.

> This repoint was **not optional**. His `reasoning` capability pointed at `qwen3.6:35b` on `ollama-local` — the copy he approved removing — so leaving it would have pointed the brain at a model that no longer exists.

**End-to-end proof through Friday's own execution layer**, not just the helper:

```
capability_router reasoning -> {'provider': 'llama-cpp-brain',
   'model': 'qwen3.6-35b-a3b-iq4nl', 'available': True, ...}
REAL DISPATCH via Friday _call_openai -> 'BRAIN ONLINE'
```

Launch command recorded verbatim at `%USERPROFILE%\.friday\runtime\start-brain.ps1`:

```
llama-server.exe -m <runtime>\models\gguf\Qwen3.6-35B-A3B-UD-IQ4_NL.gguf `
  --alias qwen3.6-35b-a3b-iq4nl --host 127.0.0.1 --port 8081 `
  -ngl 99 --flash-attn on --n-cpu-moe 20 -c 32768 --jinja --no-webui
```

`--jinja` matters: without it the chat template is not applied and tool calling degrades. `--alias` was added because llama-server otherwise reports the full GGUF path as the model id.

**Open question, deliberately not solved:** nothing starts llama-server across reboots. Per the work order I did **not** invent a service wrapper — `start-brain.ps1` is the recorded command, not a supervisor. **Consequence Stephen should know:** until it is running, the `reasoning` capability is unreachable and the router's fallback ladder degrades to another provider. Chat keeps working; it just is not using the local brain.

---

### A8 — Relocate the runtime root (D7) — `7495c1c`

`core.DEFAULT_RUNTIME_DIR` = `~/.friday/runtime`, resolved by `core.runtime_dir()` in precedence order **`FRIDAY_RUNTIME_DIR` env → `settings.runtime_dir` → default**. The override is load-bearing, not decoration: the default lands on the system drive and this tree is **37.94 GB**. Resolution is lazy (settings are not loadable during module init) and an unreadable settings file falls back rather than raising.

Moved by same-volume rename. **Both venvs survived and re-rooted**, VERIFIED:

```
venv-voice : python 3.11.15  prefix %USERPROFILE%\.friday\runtime\venv-voice
             kokoro+faster_whisper+piper import OK
venv-comfy : python 3.11.15  prefix %USERPROFILE%\.friday\runtime\venv-comfy
             torch 2.13.0+cu130 cuda True
```

`sys.prefix` follows the interpreter's own location and `home` in `pyvenv.cfg` points at the unmoved base install, so a moved venv keeps working when invoked as `<venv>\Scripts\python.exe`. (Console entry-point shims such as `pip.exe` embed an absolute path and would break; `python -m pip` is unaffected.)

**One smoke test per relocated component, all re-run at the new location:**

| Component | Result |
|---|---|
| Kokoro TTS | 5.08 s audio in 2.40 s, **RTF 0.472** — PASS |
| Piper TTS | 4.16 s audio in 1.96 s, **RTF 0.472** — PASS |
| faster-whisper | **7/7 keywords**, verbatim transcript, RTF 3.600 on the 5 s clip — PASS |
| llama.cpp brain | **23.75 tok/s**, VRAM 11645 MiB — PASS |
| ComfyUI + Z-Image | 1024×1024 in **44.12 s** — PASS |

Every recorded path in `provisioning-report.md` was updated, with a note preserving the Phase 2 reasoning rather than silently rewriting it.

**Both Phase 1/2 audit reports are now under version control.** They had been left untracked by their own mission's no-commit rule. Committing them is what surfaced absolute `C:\Users\<name>\` paths to the pre-commit scanner; all are rewritten to `%USERPROFILE%` per the public-repo policy — equally precise, no username. One further scanner hit was a **false positive** on a quoted `api_key=` in prose, resolved by rephrasing rather than adding an allowlist pragma to a document.

---

## Definition of done

| Requirement | Status |
|---|---|
| Default suite green | **VERIFIED** — `pytest -q` exit 0, 5369 tests, fully offline |
| All eight items closed or honestly STOPPED | **8/8 closed**, none STOPPED |
| Decisions doc on disk | `docs/audits/decisions-2026-08.md`, committed `0f07ee5` |
| Phase A report on disk | this file |
| Branch unpushed and unmerged | **VERIFIED** — no `push`, `merge` or PR command was run |
| GPU left free | **VERIFIED** — 834 MiB used / 11179 MiB free; ports 8081 and 8188 closed; `ollama ps` empty |
| Brain before-and-after in the report | **14.4 → 25.08 tok/s** (§A7) |

Also verified at completion: Friday's live server on **:3000 still listening, untouched** throughout; no `python.exe`/`pythonw.exe` was broad-killed; the unpushed WIP commit `fb4daee` was never touched or pushed.

---

## Carried forward

Not in Phase A's scope; recorded so nothing is lost.

**Phase B**
1. **D4 — hardware profile** (VRAM, RAM, bandwidth class, CPU threads, OS) consulted by dispatch. A7 supplies its first hard input: the brain occupies 11.6 GB of 12.28 GB VRAM, so brain and sidekick cannot co-exist on this card.
2. **D6 — wire `.fridayhints` `preferred_model`** into dispatch (currently stored, served over HTTP, read by nothing).
3. **`_OLLAMA_BASE` hardcoded** in `worker_adapters/ollama_adapter.py` — ignores `settings.model_routing.ollama_url`.
4. **No conformance gate for cloud OpenAI-compatible endpoints** (Phase 1 §4) — a non-tool-calling endpoint has no detection or fallback. **UNKNOWN**: settled by pointing Friday at one.

**Phase C**
5. **D8 — routed local image generation**, once a residency scheduler exists. The measured VRAM ceiling makes evict-generate-reload mandatory rather than advisory.

**Unscheduled**
6. **Portability milestone** (D10): the `friday_tray.py` POSIX crash, plaintext credentials off-Windows, multi-GPU `nvidia-smi` parsing. Recorded in the decisions doc.
7. **Embedding re-index path** — the gate D5 requires before the embedding model becomes configurable.
8. **llama-server process management across reboots** — an open question by instruction, not an oversight.
9. **Descriptors declare no `context_window`.** A4 covers Ollama via the daemon and discovery-backed providers via the cache, but Anthropic models still fall back to the 200,000 constant. Adding windows to `model_meta` would close it.
10. **Pagefile pressure** — a 29 GB resident model balloons `pagefile.sys` toward 32 GB and can consume the system drive. Worth a documented minimum-free-disk note before any large local model runs.
