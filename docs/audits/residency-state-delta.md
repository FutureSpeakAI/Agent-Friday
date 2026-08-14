# Residency state delta — what the repo actually is, 2026-08-14

**Date:** 2026-08-14
**Branch at read time:** `phase-a-truth-flow` @ `53dd414`, working tree clean.
**Purpose:** Phase 0 of the residency-and-orchestration mission. Read-only. Records what
landed, what did not, and every contradiction between the mission's stated assumptions and
the machine/repo as measured today.

**Evidence registers, continued from the prior audits:**
- **VERIFIED** — the author ran the command or read the cited line and saw the output.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

**Inherited artifacts read in full:** [`inference-discovery.md`](./inference-discovery.md),
[`provisioning-report.md`](./provisioning-report.md),
[`decisions-2026-08.md`](./decisions-2026-08.md), [`phase-a-report.md`](./phase-a-report.md).

---

## 1. Verdict on the two STOP conditions

The mission says to stop and report instead of building if either precondition is missing.
**Neither is missing. Both hold. No STOP on these grounds.**

| Precondition | Status | Evidence |
|---|---|---|
| A single egress choke point | **PRESENT** | `services/model_router.py:88` `_seal_or_block()`, called at `model_router.py:162`, `:813` and `services/agent.py:1693`, `:4837` — every cloud text payload. Its local-traffic sibling `services/egress_gate.py:421` `gate_worker_payload()` closes the one bypass the discovery audit found (Phase A item A3). **VERIFIED by grep** — no provider call site constructs a payload outside one of these two. |
| Backend selected by base URL | **PRESENT** | `routing/provider_descriptors.py:150-167` — `"local"` is earned, not declared: a local-capable adapter **and** `is_private_host(base_url)`. A descriptor whose `base_url` is not verifiably on-device is demoted to cloud and egress-gated (`:381`). Descriptor files at `~/.friday/providers/*.json` carry `base_url` and are the documented drop-in mechanism. |

**Third STOP condition — a conflicting residency/scheduling mechanism — does not exist.**
**VERIFIED by grep** over `src/` for `residency`, `placement`, `arbiter`, `lease`, `evict`,
`keep_alive`, `num_gpu`, `n_cpu_moe`: zero hits in any model-lifecycle sense. Every hit was an
unrelated domain (budget reservation release, content-pipeline HELD-post release, ordinary
prose). `services/scheduler.py` is a **job** scheduler — interval/daily/weekly triggers over
`~/.friday/schedules.json` — with no concept of a model, a device, or VRAM. It is adjacent in
name only and will not be touched.

**Conclusion: the mission may proceed to Phase 1.** The open questions in §6 are inputs to the
design doc, not blockers on starting it.

---

## 2. What landed — the six items the mission asks about

All six are **in the tree and test-backed**. The Phase A report's claims were re-verified
independently rather than taken on trust.

| Item | Status | Evidence in code | Test |
|---|---|---|---|
| Real health probe | **LANDED, with a gap** — see §3 | `services/provider_health.py:313` `inference_probe()`; the Ollama branch at `:354` is the call site for `ollama_manager.health_check()`, which the discovery audit verified had zero callers | `tests/api/test_health_inference_probe.py` |
| Egress-gate closure | **LANDED** | `services/egress_gate.py:421` `gate_worker_payload()` — makes the local-vs-cloud decision from the destination, so a call site cannot opt out by omission | `tests/api/test_worker_adapter_egress.py` |
| Catalog-driven context windows | **LANDED** | `services/model_catalog.py:62` `context_window_for()`; `services/compaction.py:68` `resolve_context_window()` resolving catalog → configured → 200 000 | `tests/unit/test_context_window_budget.py` |
| Embedding dimension guard | **LANDED** | `conversation_memory.py:64` `EmbeddingDimensionMismatch`, raised at `:269` before the broad `except` that keeps transient memory faults out of chat | `tests/unit/test_embedding_dimension_guard.py` |
| OAuth unpin | **LANDED** | `core/__init__.py:225` `server_base_url()`, port-derived | `tests/api/test_oauth_redirect_port.py` |
| Brain llama-server migration | **LANDED — but see the contradiction in §4.2** | descriptor `~/.friday/providers/llama-cpp-brain.json` (`base_url` `http://127.0.0.1:8081/v1`, `context_window` 32768); GGUF present on disk at 16.8 GB; `start-brain.ps1` recorded | measured, no unit test (environment work, no commit — by design) |

Two supporting items also verified present: the inverted test default
(`tests/api/test_inverted_provider_default.py`, D9/A1) and the declared runtime root
(`core/__init__.py:580` `runtime_dir()`, D7/A8) resolving `FRIDAY_RUNTIME_DIR` env →
`settings.runtime_dir` → `~/.friday/runtime`.

---

## 3. What did NOT land — the prerequisite slice, itemised

The mission names three sensors. **One of the three is incomplete and must be built before
Phase 2; the other two are done.**

### (a) Health probe with a latency threshold — **NOT LANDED. Build it.**

The probe is real and it does inference. **It has no latency threshold at all.**
`provider_health.py:345-396` computes `ms = int((time.time() - t0) * 1000)` and spends it
**only on the human-readable detail string** — `f"generated in {ms}ms"`. The status is decided
purely by *did any text come back*:

```python
return _result("ok" if ok else "down",
               f"generated in {ms}ms" if ok
               else "no output from a real generation", bool(ok))
```

**INFERRED, and this is the finding that matters most for this mission:** a model paging
against RAM — the exact collapse the sweep in `phase-a-report.md` §A7 measured, where
throughput fell from 21.6 to 5.5 tok/s while the allocator thrashed rather than erroring —
returns text, slowly, and therefore reports **`ok`, green, healthy**. The one failure mode the
residency layer exists to prevent is invisible to the only sensor that would see it.

Two things are missing, not one:

1. **No threshold.** Nothing compares `ms` against anything.
2. **No baseline to compare against.** **VERIFIED by absence** — no per-model recorded token
   latency exists anywhere in `src/`. The measured numbers live only in the prose tables of
   `provisioning-report.md` §7 and `phase-a-report.md` §A7. Design requirement 2 (per-profile
   measured numbers in the catalog) is therefore a **hard prerequisite of** the 5× rule, not a
   parallel nicety. The catalog must carry the baseline before the probe can be strict.

Also carried forward: the probe's timeouts are 30 s (Ollama, `ollama_manager.py:256`) and 10 s
(`openai-compatible`, `provider_health.py:388`). A cold load of the brain measured 83–100 s
(`provisioning-report.md` §7), so **a probe fired against a cold model times out and reports
`down` when the model is merely loading.** The 5× rule needs a load-versus-collapse distinction
or it will produce false RED on every cold start. Recorded now so the prerequisite is designed
once rather than patched twice.

### (b) Context windows from the catalog — **LANDED.** No work required.

`services/model_catalog.py:62` `context_window_for()` (disk cache only, memoised 5 min, never
the network on the hot path) and `services/compaction.py:68` `resolve_context_window()`.
Unknown models return `None` and callers fall back to the constants, exactly as D3 specifies.

One residual, inherited from `phase-a-report.md` "Carried forward" #9 and re-verified: **the
locally-installed Ollama models have no descriptor-declared window.** A4 works around this via
`OllamaManager.context_length()` reading the daemon's GGUF metadata. That path is live and is
the right seam for the catalog enrichment in design requirement 2 — extend it, do not fork it.

### (c) Embedding dimension assertion on vector-store writes — **LANDED.** No work required.

`conversation_memory.py:269`. Collections stamp model and width in metadata at creation;
pre-stamp collections are backfilled from a stored vector; an *unverifiable* width is never
treated as a mismatch. Scope was correctly held to the write-time assertion per D5.

---

## 4. Contradictions with the mission's stated assumptions

Each of these is a place where the brief and the machine disagree. Recorded rather than
silently reconciled.

### 4.1 The model inventory is correct — but it is hours old and wholly unmeasured

`ollama list`, **VERIFIED** at Phase 0:

```
gemma4:26b              17 GB     2 minutes ago
gemma4:12b              7.6 GB    4 minutes ago
gemma4:e2b              7.2 GB    23 hours ago
gemma4:e4b              9.6 GB    23 hours ago
qwen3-embedding:0.6b    639 MB    23 hours ago
```

Ollama **0.32.11** (the provisioning report recorded 0.32.9 — the daemon was updated since).

The brief's Ollama inventory matches exactly. But `gemma4:12b` and `gemma4:26b` were pulled
**two and four minutes before this audit ran** — after every inherited document was written.
**Consequence:** the two models the mission's P1 fixture plan puts at the centre (12b pinned,
26b leased) are the **only two with zero measurements anywhere**. `provisioning-report.md` §7
and `phase-a-report.md` §A7 have real tok/s, VRAM, and load times for `gemma4:e4b`,
`qwen3-embedding:0.6b`, `qwen3.6:35b` on both backends, Kokoro, Piper, faster-whisper and
Z-Image — and **nothing** for `gemma4:12b`, `gemma4:26b`, or `gemma4:e2b`.

Design requirement 2 says to seed the catalog "from the provisioning report where measurements
exist." For P1's two load-bearing models, they do not exist. **Phase 2 must measure them**;
Phase 1's P1 fixture plan will otherwise be justified from rules applied to guessed numbers.

**One measurement did fall out of Phase 0 for free, and it is a warning.** `ollama ps` during
the read, **VERIFIED**:

```
gemma4:26b    18 GB    79%/21% CPU/GPU    262144
```

The 26B-A4B MoE, left to Ollama's own defaults, takes **18 GB against a 12 GB card and lands
79 % on CPU** at a 262 144-token context. That is the same shape as the brain's pre-migration
`66%/34%` spill that measured 14.4 tok/s. **INFERRED:** "26b leased with expert offload" in
the P1 target plan is not the default behaviour — it is a configuration the arbiter must
actively impose, and `num_ctx` is a first-class term in it, not a detail. Ollama's default
context alone is a placement decision being made by omission today.

### 4.2 "qwen3.6:35b was removed" is true of Ollama and false of the machine

The brief states the model was removed as unfit. **Half of that is VERIFIED:** the Ollama copy
was deleted in Phase A item A7 to reclaim disk, and it is absent from `ollama list`.

**The other half contradicts the live configuration.** Still present and still wired:

| Fact | Evidence |
|---|---|
| A 16.8 GB GGUF of the same model | `~/.friday/runtime/models/gguf/Qwen3.6-35B-A3B-UD-IQ4_NL.gguf`, **VERIFIED** on disk |
| A live provider descriptor for it | `~/.friday/providers/llama-cpp-brain.json`, **VERIFIED**, `enabled: true` |
| It is the **current `reasoning` capability** | `settings.capability_routing.reasoning` = `{provider: llama-cpp-brain, model: qwen3.6-35b-a3b-iq4nl}`, **VERIFIED** |
| llama-server is **not running** | port 8081 **closed**, **VERIFIED** |

So Friday's reasoning capability presently points at a backend that is not listening, on a
model the mission's premise treats as gone, and the mission's P1 target plan has **no role for
it** — the plan's interactive_brain is `12b`, its heavy_hitter is `26b`.

**This is an unresolved question, not a defect to fix unasked** — see §6, Q1. It carries a
16.8 GB disk consequence that §4.4 makes urgent.

### 4.3 Two config surfaces point at a model that is not installed

**VERIFIED** in `~/.friday/settings.json`:

```
capability_routing.local  -> {provider: ollama-local, model: gemma3:4b}
model_routing.local_model -> gemma3:4b
```

`gemma3:4b` is **not in `ollama list`**. `routing/model_router.py:_pick_local_model` guards
this — `if pref and pref in model_names` — so it falls through to picking by install size
rather than erroring. **INFERRED:** the fallthrough then selects by *artifact size alone with
no VRAM check whatsoever*, which on this host means the largest installed model wins for
`CODE`/`RESEARCH` tasks: `gemma4:26b`, the 18 GB / 79 %-CPU spill measured above. The user's
declared local seat is dangling and the silent substitute is the worst-placed model on the box.

`_pick_local_model` is the closest thing to a placement mechanism in the tree today. It is not
a *residency* mechanism (no pinning, no leases, no device concept) so it is not a STOP-condition
conflict — but it is the integration point the policy engine must take over from, and its
size-only heuristic is the behaviour being replaced.

### 4.4 Free disk is 2.8 GB. This constrains Phase 2.

**VERIFIED** at Phase 0:

| Measure | Value |
|---|---|
| C: free | **2.8 GB** |
| `~/.friday/runtime` tree | **37.94 GB** |
| `pagefile.sys` allocated / in use | **31.9 GB** / 1.0 GB |
| RAM total / free | 31.9 GB / 3.4 GB |
| GPU | RTX 4070, 12 282 MiB total, 11 393 MiB **used**, 620 MiB free (26b resident) |
| CPU | i7-10700F, 8 cores / 16 threads |
| Ports | 3000 **open** (live Friday), 11434 **open** (Ollama), 8081 closed, 8188 closed |

The pagefile is *allocated* at 31.9 GB while only 1.0 GB is in use, so most of that 32 GB is
reserved-not-consumed — but it is reserved **on disk**, and `phase-a-report.md` §A7 recorded the
mechanism directly: free disk fell 27.7 GB → **7.0 GB** with a 29 GB model resident and
recovered to 22.1 GB when it unloaded. At 2.8 GB free there is no such headroom left.

**INFERRED, high confidence:** the Phase 2 live integration tests the mission specifies — boot
to default plan, a 12b → 26b → 12b heavy-turn lease cycle, a ComfyUI image lease with GPU
handback — all load and unload multi-GB models on this exact machine. They are the workload
that inflates the pagefile. Running them at 2.8 GB free risks exhausting the system drive of
Stephen's live daily-driver machine, which is precisely the risk A7 paused for rather than
absorbed unilaterally.

This is a **decision for Stephen, not a blocker I should resolve** (§6, Q2). It does not stop
Phase 1: the design doc, the fixtures, the policy engine and its unit/property tests are all
pure computation and cost no disk.

Carried-forward item #10 from `phase-a-report.md` — "worth a documented minimum-free-disk note
before any large local model runs" — is now the live constraint it anticipated. The RAM-headroom
watcher in design requirement 6 has an obvious sibling: a **disk**-headroom refusal, because on
Windows a large resident model consumes disk through the pagefile as surely as it consumes RAM.

---

## 5. What exists to extend, and what must be built

Design requirement 1 says extend the detection the discovery audit found; do not fork a
parallel detector. Here is the inventory, re-verified.

| Existing | Location | State | Verdict |
|---|---|---|---|
| GPU name + total VRAM, RAM, platform | `routing/ollama_manager.py:187` `detect_hardware()` | Real. Cached in `self._hardware_cache`. **Multi-GPU parsing bug still present** — `:199` does `stdout.strip().split(",")` and reads `parts[0]`/`parts[1]` without iterating the one-line-per-GPU output | **Extend.** This is the detector to build `HardwareProfile` on, and the multi-GPU fix is mandatory because P4 (asymmetric 24 GB + 12 GB) is a required fixture |
| CUDA availability, device name, free/total VRAM | `services/nemo_voice.py:166` `gpu_tier_ready()` | Real, via `torch.cuda.mem_get_info`. Uses `torch.cuda.current_device()` with no device-index parameter | **Extend/absorb.** Voice cannot be pinned to a non-default GPU today |
| CPU cores + RAM | `services/compute_provider.py:97` `_compute_specs()` | `gpu_model` and `gpu_vram_gb` are declared and **never populated** — **VERIFIED**, the function sets RAM and returns | **Fix by wiring to the profile**, so federation peers stop advertising a GPU-less machine |
| VRAM/RAM → model ladder | `routing/ollama_manager.py:235` `recommend_models()` | Real logic, hardcoded qwen3 tiers, **never consulted by dispatch** | **Supersede.** This is install-time advice; the policy engine is the general form |
| Per-request local model choice | `routing/model_router.py:_pick_local_model` | Picks by install size, no VRAM check | **Supersede** (see §4.3) |
| Declared runtime root | `core/__init__.py:580` `runtime_dir()` | Overridable, lazy, fails soft | **Use as-is** for profile/catalog cache placement |
| Model catalog, 3-way merge | `services/model_catalog.py:247` `build_catalog()` | Static descriptors + live `ollama list` + disk cache | **Extend** into `CatalogEntry` (design req 2) |

**Genuinely absent, to be built:** `HardwareProfile` (**VERIFIED by absence** in the discovery
audit §9 and re-confirmed — `hardware_profile`, `HardwareProfile`, `device_profile` return zero
hits), memory-bandwidth class, disk read-rate measurement, per-profile measured performance,
roles-not-names, `PlacementPlan`, `ResidencyPolicy`, `Arbiter`, and any notion of pinned versus
leased.

**One more dead surface, residency-adjacent by name, found during this pass and worth recording
before it confuses someone:** `core/__init__.py:1484` declares
`"local_inference_slots": 3` in the settings defaults. **VERIFIED by grep** — that line is its
**only** occurrence in `src/`. Nothing reads it. It joins `capability_routing.embedding.model`
and `.fridayhints` `preferred_model` on the dead-config list (discovery audit R4). It sounds
like a residency knob and is not one; the policy engine should either give it meaning or it
should go, but silently leaving a third phantom concurrency setting beside a real placement
engine would be actively misleading.

---

## 6. Open questions for Stephen

Blocking ones first. Q1 and Q2 shape Phase 1's output; Q3 is the relayed gap.

**Q1 — What happens to the llama.cpp `qwen3.6:35b` brain?** (§4.2)
It is 16.8 GB on a disk with 2.8 GB free, it is the live `reasoning` capability, its server is
not running, and the mission's P1 plan has no role for it. Three coherent answers:
(a) decommission it — reclaim 16.8 GB, repoint `reasoning` at `gemma4:12b`, and let the
26b MoE be the heavy_hitter as the P1 plan says; (b) keep the artifact but treat
`llama-server` as a *second backend the arbiter owns* — design requirement 6 already names
llama-server process control, so the seam exists; (c) keep it as-is and accept a dangling
capability. I will not choose this unasked: it deletes 16.8 GB of Stephen's disk or leaves his
reasoning seat pointing at nothing.

**Q2 — Do the Phase 2 live integration tests run on this machine at 2.8 GB free?** (§4.4)
The heavy-turn and image-lease cycles inflate the pagefile on the system drive. Options:
reclaim first (Q1's 16.8 GB would take free disk to ~19.6 GB), run the integration tests with a
disk-headroom guard that refuses rather than risks, or defer the live tests and land Phase 2
with unit and property tests only. Phase 1 is unaffected either way and I will proceed with it.

**Q3 — The 26b backend instruction was cut off.** The brief ends mid-sentence at *"For the 26b
backend,"*. Not guessed at, per dispatch. The live evidence bearing on whatever the rest of it
says: the 26b currently runs **18 GB, 79 % CPU, 262 144 context** under Ollama's defaults
(§4.1), and the equivalent MoE tuning on the previous brain — llama.cpp `--n-cpu-moe`, swept —
bought **+74 % throughput** over Ollama (`phase-a-report.md` §A7). If the missing instruction
concerns Ollama-versus-llama-server for the heavy_hitter, that sweep is the precedent.

**Also carried, non-blocking:** `capability_routing.local` and `model_routing.local_model` both
point at the uninstalled `gemma3:4b` (§4.3). The policy engine's `preferred_model`-style
override wiring (design requirement 3) is the natural place to make that produce an **explained
refusal** instead of a silent fallthrough to the worst-placed model on the box. Flagging rather
than fixing it now, since it is exactly the behaviour the override work is meant to define.

---

## 7. Phase 0 definition of done

| Requirement | Status |
|---|---|
| All four inherited audit docs read | **VERIFIED** — discovery, provisioning, decisions, Phase A report |
| Six named items adjudicated | **VERIFIED** — §2; all six landed, one with a gap (§3a) |
| Installed models verified against the brief | **VERIFIED** — §4.1, `ollama list` + `ollama ps` |
| Runtime directory verified | **VERIFIED** — §4.4; tree present at 37.94 GB, contents enumerated |
| Contradictions recorded | **VERIFIED** — §4, four of them |
| STOP conditions evaluated | **VERIFIED** — §1; none met, proceed |
| Read-only | **VERIFIED** — no repo source touched; the only write in this phase is this file |
