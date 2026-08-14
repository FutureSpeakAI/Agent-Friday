# Residency policy and GPU arbitration — design

**Date:** 2026-08-14
**Branch:** `residency-policy`, off `phase-a-truth-flow` @ `53dd414`.
**Status:** design. **No implementation code exists yet — this document lands first, by instruction.**
**Inherits:** [`decisions-2026-08.md`](../audits/decisions-2026-08.md) (D1–D10, notably **D4** — a
first-class hardware profile consulted by dispatch — and **D8** — routed image generation once a
residency scheduler exists), [`residency-state-delta.md`](../audits/residency-state-delta.md) (Phase 0).

**Evidence registers:**
- **VERIFIED** — the author ran the command or read the cited line and saw the output.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. What this layer is for, in one paragraph

Friday must compose a local model stack differently on every machine it runs on. Today it does
not: `routing/model_router.py:_pick_local_model` chooses among installed models **by artifact
size, with no VRAM check**, and hardware detection feeds only Ollama install advice and a
binary voice CPU/GPU gate. This document specifies three things that do not exist — a detected
**HardwareProfile**, a pure deterministic **ResidencyPolicy** that maps roles to models and
devices, and an **Arbiter** that owns the GPUs at runtime — and the **CatalogEntry** enrichment
they both depend on. The dispatcher stays deterministic code throughout: no LLM sits in the
placement or arbitration path.

---

## 1. Measured evidence base — the reference instance (P1)

Every number in this section was measured on 2026-08-14 on the reference instance, by this
mission. **None of it existed before**: `gemma4:12b`, `:26b` and `:e2b` were pulled hours
before Phase 0 ran, after every prior audit document was written.

### 1.1 Host

| Property | Value | Method |
|---|---|---|
| OS | Windows 11 Pro 10.0.26200 | **VERIFIED** |
| CPU | Intel i7-10700F, 8 cores / 16 threads | **VERIFIED** `Win32_Processor` |
| RAM | 32620 MiB (2 × 16 GiB Mushkin **DDR4-2667**, dual channel) | **VERIFIED** `Win32_PhysicalMemory`, `SMBIOSMemoryType=26` |
| Memory bandwidth class | **DDR4 dual-channel, ≈42.7 GB/s theoretical** (2667 MT/s × 8 B × 2 ch) | **INFERRED** from verified module speed and channel count — heuristic, not a microbenchmark. Method recorded per design requirement 1. |
| GPU | 1 × RTX 4070, **12282 MiB**, driver 610.88 | **VERIFIED** `nvidia-smi` |
| GPU baseline load | **1261 MiB** used with zero models resident (desktop compositor) | **VERIFIED**, repeated across five runs |
| Disk | Samsung SSD 870 EVO 1 TB, **SATA** SSD | **VERIFIED** `Get-PhysicalDisk` |
| Disk read rate | **427 MiB/s** sequential | **VERIFIED** — 2.00 GiB of a real Ollama blob, `buffering=0`. Page cache not explicitly dropped, so this is a warm-ish **upper bound**; recorded as such. |
| Free disk | 19.6 GB (after the 16.8 GB qwen3.6 reclaim) | **VERIFIED** |

### 1.2 Model measurements

`vram_mib` and `total_mib` are read from the daemon's own `/api/ps` (`size_vram`, `size`), not
inferred from `nvidia-smi` deltas. tok/s is the **median of 5 warm runs** after a separate cold
load; single-sample figures are marked as such and are not used for decisions.

| Model | num_ctx | tok/s (median, n=5) | σ | ms/token | VRAM MiB | Total MiB | On GPU | Cold load |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `gemma4:e2b` | 8192 | **166.13** | 9.76 | 6.02 | 1763 | 1763 | **100 %** | 20.97 s |
| `gemma4:e4b` | 8192 | **99.93** | 0.23 | 10.01 | 3081 | 3081 | **100 %** | 27.52 s |
| `gemma4:12b` | 16384 | **49.36** | 0.12 | 20.26 | 8001 | 8001 | **100 %** | 20.49 s |
| `gemma4:26b` | 16384 | **27.95** | 0.26 | 35.78 | 8586 | 17391 | 49 % | 55.10 s |
| `qwen3-embedding:0.6b` | 2048 | n/a (embed) | — | — | 2029 | 2029 | 100 % | ~3.0 s |

**VRAM against num_ctx — the KV curve is nearly flat**, **VERIFIED** for the 12b:

| num_ctx | 4096 | 8192 | 16384 |
|---|---:|---:|---:|
| `gemma4:12b` VRAM MiB | 7690 | 7985 | 8001 |

**INFERRED:** gemma4's interleaved local/global attention makes KV cheap — 311 MiB across a 4×
context increase. **Weights dominate placement; num_ctx is nearly free on this family.** This
is a family-specific property and must live in the catalog per model, not as a global assumption.

### 1.3 Three findings that change the design

**(a) Artifact size is not VRAM footprint, and the error is 4×.** `gemma4:e2b` is a **7.2 GB
artifact** that occupies **1763 MiB** of VRAM — the per-layer-embedding architecture keeps most
of the file out of accelerator memory. `_pick_local_model` ranks by artifact size. **INFERRED:**
on this host that heuristic is not merely imprecise, it is inverted — it would rank the e2b as
"larger" than the e4b (3081 MiB) and nearly as large as the 12b. The catalog must carry
**measured VRAM at a stated num_ctx**, and artifact size may never be used as a placement proxy.

**(b) Setting num_ctx explicitly is itself a placement decision.** Left to Ollama's default the
26b reports `262144` context and **79 % / 21 % CPU/GPU**. Pinned to 16384 the same model reports
**51 % / 49 %** — **VERIFIED**, both from `ollama ps`. **INFERRED:** roughly half this model's
GPU residency was being surrendered to a context nobody asked for. This is the concrete case
behind the rule that no model ever runs on a backend default, in either direction.

**(c) Ollama's scheduler silently evicts a model the policy considers pinned.** This is the
load-bearing finding for the Arbiter's design. **VERIFIED** by loading in sequence and reading
`/api/ps` after every step:

```
BASELINE                      (nothing resident)              nvidia used=1261
STEP 1  load 12b  @16384      gemma4:12b   vram=8001  100%    nvidia used=9976
STEP 2  load e2b  @8192       gemma4:e2b   vram=1763  100%    nvidia used=4278
STEP 3  load embedder @2048   qwen3-embedding + gemma4:e2b    nvidia used=6469

VERDICT   12b survived step 2?  False
          12b survived step 3?  False
          final resident set  :  ['gemma4:e2b', 'qwen3-embedding:0.6b']
```

The eviction is **not** explained by a model-count limit — `OLLAMA_MAX_LOADED_MODELS`,
`OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE` and `OLLAMA_GPU_OVERHEAD` are all **unset**
(**VERIFIED**), so the default ceiling of 3 was never reached. And it is not explained by the
budget: sweeping the 12b's num_ctx down does not help, **VERIFIED**:

```
12b@16384  vram=8001  -> after e2b arrives resident=['gemma4:e2b']  BOTH=False
12b@8192   vram=7985  -> after e2b arrives resident=['gemma4:e2b']  BOTH=False
12b@4096   vram=7690  -> after e2b arrives resident=['gemma4:e2b']  BOTH=False
```

8001 + 1763 = **9764 MiB**, against **9997 MiB** the budget rule (§4, R3) makes available.
**The pair fits and Ollama refuses it at every context length tried.**

**INFERRED, and this is why the Arbiter exists:** a residency policy cannot delegate placement
to a backend scheduler that makes its own eviction decisions on different criteria and reports
them nowhere. Either the Arbiter owns the process (llama-server, one process per pinned seat)
or "pinned" is a word with no mechanism behind it. This is recorded as decision question **Q2**
rather than settled here, because it determines the 26b's backend and that measurement is not
yet taken.

**(d) Load time is predictable from artifact size and the measured disk rate.** Required by
design requirement 6. Predicted = `artifact_MiB / 427 MiB/s`:

| Model | Artifact | Predicted | Measured | Error |
|---|---:|---:|---:|---:|
| `gemma4:e2b` | 7.2 GB | 17.3 s | 20.97 s | +21 % |
| `gemma4:12b` | 7.6 GB | 18.2 s | 20.49 s | +13 % |
| `gemma4:e4b` | 9.6 GB | 23.0 s | 27.52 s | +20 % |
| `gemma4:26b` | 17 GB | 40.8 s | 55.10 s | **+35 %** |

**INFERRED:** `load_s ≈ artifact_MiB / disk_rate + 3 s` holds for dense models within ~20 %. The
26b's extra ~14 s is **INFERRED** to be MoE expert placement across the CPU/GPU boundary, which
the dense models do not pay. The estimator therefore carries a per-backend multiplier, and MoE
entries carry their own. **UNKNOWN** whether the multiplier is stable across MoE models — one
data point. Settled by measuring a second MoE.

---

## 2. Schemas

Serialized as JSON under `runtime_dir()/residency/`. `core.runtime_dir()` already resolves
`FRIDAY_RUNTIME_DIR` env → `settings.runtime_dir` → `~/.friday/runtime` (**VERIFIED**,
`core/__init__.py:580`) — the profile cache uses it rather than inventing a path.

### 2.1 HardwareProfile

Detected, cached, serializable, refreshed on change. **Extends** `OllamaManager.detect_hardware`
(`routing/ollama_manager.py:187`) rather than forking a second detector — including fixing its
multi-GPU bug (`:199` splits `nvidia-smi` output on commas without iterating the one-line-per-GPU
result), because P4 is a required fixture.

```
HardwareProfile
  profile_id        str    stable hash of the identity fields below
  detected_at       iso8601
  os                {family: windows|linux|darwin, version: str}
  cpu               {threads: int, physical_cores: int, model: str}
  ram               {total_mib: int, available_mib: int}
  memory_bandwidth  {class: ddr4|ddr5|lpddr5|unified|unknown,
                     gb_s_estimate: float,
                     method: heuristic-smbios|microbenchmark|declared}
  gpus              [ {index, name, vram_total_mib, vram_baseline_mib,
                       compute_class: str, driver: str} ]      # ordered, may be empty
  disk              {free_mib: int, read_mib_s: float,
                     method: sequential-blob-read|declared}
  refresh_triggers  [gpu_count_change, vram_change, ram_change, os_upgrade]
```

`vram_baseline_mib` is measured, not assumed — the P1 compositor holds 1261 MiB and a budget
that ignores it overcommits the card by exactly that much. `compute_class` is a coarse
capability bucket (fp16/bf16/fp8 support, tensor-core generation), used to decide whether an FP8
image build is loadable at all.

**Multi-GPU and asymmetric pairs are first-class from day one:** `gpus` is an ordered list, every
budget in §4 is computed **per GPU index**, and no rule assumes `len(gpus) == 1`.

### 2.2 CatalogEntry

Extends `services/model_catalog.py:build_catalog()` (**VERIFIED** — already a three-way merge of
descriptors, a live `ollama list`, and a disk cache), not a parallel store.

```
CatalogEntry
  model_id          str
  backend           ollama | llama-server | comfyui | cpu-service
  artifact_bytes    int
  quantization      str                       # e.g. Q4_K_M
  params_total      float                     # billions
  params_active     float                     # billions — equals params_total for dense
  is_moe            bool                      # params_active < params_total
  context_window    int                       # from the catalog (A4/D3), never a constant
  modalities        [text, tools, vision, thinking, embed, image, audio]
  measured          { <profile_id>: [ {num_ctx, vram_mib, total_mib, pct_gpu,
                                       tok_s_median, tok_s_stdev, ms_per_token,
                                       cold_load_s, measured_at} ] }
```

`ms_per_token` is the **baseline the health probe's 5× rule compares against** — the prerequisite
slice's latency threshold cannot exist without this field, which is why catalog enrichment is
sequenced before the strict probe (Phase 0 §3a).

`params_active` is what makes MoE handling possible: the 26b is `params_total=25.8`
(**VERIFIED**, `ollama show`) with 4 B active. `ollama show` does **not** report active
parameters — **UNKNOWN** for models not documented upstream; the field is nullable and
`is_moe` falls back to a descriptor declaration.

### 2.3 Roles, and PlacementPlan

Roles, not names. The policy binds each to an installed model per profile:

`interactive_brain` · `heavy_hitter` · `sidekick` · `embedder` · `stt` · `tts` · `image`

```
PlacementPlan
  profile_id     str
  generated_at   iso8601
  policy_version str
  seats          { <role>: Placement | null }
  refusals       [ {role, model, rule_id, explanation} ]

Placement
  role, model_id, backend
  device         gpu:<index> | cpu | on-demand
  num_ctx        int                    # ALWAYS explicit. Never a backend default.
  offload        {n_cpu_moe: int|null, n_gpu_layers: int|null, expert_offload: bool}
  status         pinned | leased
  vram_mib       int                    # measured, or estimated with a flag
  est_load_s     float
```

A `null` seat is a first-class outcome carrying a refusal with its rule id — never a silent
omission.

### 2.4 The user override — an explained refusal, never a silent ignore

D6 says `.fridayhints` `preferred_model` gets wired, not deleted. It binds a **model to a role**.
An override that violates a constraint produces a refusal naming the rule and both numbers:

> `heavy_hitter` override `gemma4:26b` refused on profile P2 by **R2** (RAM ceiling): pinned
> 17 391 MiB + OS reserve 6144 MiB = 23 535 MiB exceeds the 12 288 MiB hard ceiling (75 % of
> 16 384 MiB). Nearest permitted: `gemma4:e4b` at 3081 MiB.

This is the surface that fixes the live defect Phase 0 found — `capability_routing.local` and
`model_routing.local_model` both point at `gemma3:4b`, which is not installed, and
`_pick_local_model` falls through to artifact-size selection that picks the worst-placed model on
the box (§1.3a). Under this design the dangling pointer produces a refusal that names itself.

---

## 3. Roles → the reference instance

For P1, from §1.2. Justification is in §5.1.

| Role | Model | Why |
|---|---|---|
| `interactive_brain` | `gemma4:12b` | fastest model that is 100 % GPU-resident at a usable context (49.36 tok/s, 8001 MiB) |
| `heavy_hitter` | `gemma4:26b` | 25.8 B total / 4 B active; MoE, so R6 permits expert offload |
| `sidekick` | `gemma4:e2b` | 166 tok/s at 1763 MiB — cheapest useful seat measured |
| `embedder` | `qwen3-embedding:0.6b` | the only embedding model installed |
| `stt` | faster-whisper (CPU) | already shipping, `services/local_voice.py` |
| `tts` | Kokoro, Piper fallback (CPU) | RTF 0.472 both, `phase-a-report.md` §A8 |
| `image` | Z-Image Turbo FP8 / ComfyUI | the only local image backend |

`gemma4:e4b` is bound to no seat on P1: at 3081 MiB and 99.93 tok/s it is strictly dominated by
the e2b for the sidekick role (cheaper **and** faster is not available — the e2b is cheaper and
faster) and by the 12b for the brain role (better quality per the family ordering). It stays
catalogued as **CPU-optional**, per the mission's P1 statement.

---

## 4. ResidencyPolicy — rules as inspectable data

A pure, deterministic function `(HardwareProfile, Catalog, overrides) -> PlacementPlan`. No I/O,
no clock, no randomness: the same three inputs always produce byte-identical output. Rules are
data, each with a stable id so a refusal can cite one.

| id | Rule | Threshold |
|---|---|---|
| **R1** | OS memory reserve, subtracted before any RAM budget | **6 GB** Windows, **4 GB** Linux/darwin |
| **R2** | Resident-set RAM ceiling: pinned artifact bytes + KV + OS reserve | **hard ≤ 75 %** of physical RAM; **plan targets 65 %** |
| **R3** | Per-GPU VRAM budget: pinned weights + KV at the configured num_ctx + buffers | **≤ VRAM − 1 GB**, where measured `vram_baseline_mib` counts against the same budget |
| **R4** | Never tensor-split one model across GPUs | absolute |
| **R5** | Image generation takes an **exclusive GPU lease** — unless a second GPU exists, in which case it takes one GPU and the others keep serving | absolute |
| **R6** | MoE models **may** expert-offload; dense models must fit **or be demoted** to a smaller model in the same role | absolute |
| **R7** | `num_ctx` is explicit for every placement | no backend default, in either direction (§1.3b) |
| **R8** | **Disk-headroom refusal**: refuse a load if free disk afterwards would fall below `max(10 GB, artifact_size)` | see §4.1 |
| **R9** | A `pinned` seat may not be delegated to a backend scheduler that evicts on its own criteria | see §1.3c |

### 4.1 R8 — why disk is a residency resource on Windows

Not an obvious rule, so its evidence is recorded. `phase-a-report.md` §A7 **VERIFIED** that free
disk fell from 27.7 GB to **7.0 GB** while a 29 GB model was resident, and recovered to 22.1 GB
when it unloaded — `pagefile.sys` inflating under memory pressure. Phase 0 found the machine at
**2.8 GB free** with the pagefile allocated at 31.9 GB. A RAM-headroom watcher that ignores this
will happily approve a load that exhausts the system drive. R8 is the sibling refusal, with the
same explained-refusal behaviour as R2.

### 4.2 Worked budget — P1

```
GPU total                              12282 MiB
R3 reserve                    − 1024 =  11258 MiB   ceiling on total VRAM in use
measured compositor baseline  − 1261 =   9997 MiB   available to models
```

| Candidate pinned set | Sum | Verdict |
|---|---:|---|
| 12b @16384 + e2b @8192 | **9764** | **fits**, 233 MiB spare |
| … + embedder @2048 (2029) | **11793** | **exceeds by 1796 MiB — refused by R3** |

---

## 5. The six fixture profiles

P1's plan is stated by the mission and is checked against the rules below. **Every other plan is
derived from §4 and nothing else.** Fixture hardware for P2–P6 is declared, not measured; their
model VRAM figures are the P1 measurements carried across, which is sound for weights and
**INFERRED** for KV.

### 5.1 P1 — the reference instance (measured)

`Windows · 8C/16T · 31.9 GB DDR4-2667 · 1 × RTX 4070 12282 MiB · SATA SSD 427 MiB/s`

| Role | Model | Device | num_ctx | Offload | Status | VRAM |
|---|---|---|---:|---|---|---:|
| `interactive_brain` | `gemma4:12b` | gpu:0 | **16384** | — | **pinned** | 8001 |
| `sidekick` | `gemma4:e2b` | gpu:0 | **8192** | — | **pinned** | 1763 |
| `embedder` | `qwen3-embedding:0.6b` | **cpu** | 2048 | — | resident | 0 |
| `heavy_hitter` | `gemma4:26b` | gpu:0 + cpu | **16384** | expert offload | **leased** | 8586 of 17391 |
| `image` | Z-Image Turbo FP8 | gpu:0 | n/a | — | **leased, exclusive** | ~8000 |
| `stt` | faster-whisper | cpu | n/a | — | on-demand | 0 |
| `tts` | Kokoro (Piper fallback) | cpu | n/a | — | on-demand | 0 |
| `e4b` | `gemma4:e4b` | cpu | 8192 | — | **CPU-optional**, unbound | 0 |

**This differs from the plan the mission specifies in exactly one place, and the difference is
forced by measurement.** The mission states the embedder is "resident"; §4.2 shows that a
GPU-resident embedder exceeds R3 by 1796 MiB. It is therefore **resident on CPU**. Two supporting
facts: the embedder costs **2029 MiB of VRAM for a 639 MB artifact** — the worst
footprint-per-byte of anything measured — and embedding is a throughput task tolerant of CPU
latency, unlike the interactive seat. Recorded as decision question **Q1** rather than quietly
substituted.

Everything else lands as specified: 12b pinned on GPU with an explicit num_ctx, e2b pinned
beside it, 26b leased with expert offload, image leased exclusive, whisper and TTS on CPU, e4b
CPU-optional.

**Caveat carried forward, not hidden:** R9. The pinned pair fits the budget but **Ollama refuses
to hold it** (§1.3c). On the Ollama backend this plan is not achievable as written; achieving it
requires either llama-server processes the Arbiter owns, or daemon tuning that has not yet been
tested. Q2.

### 5.2 P2 — 8 GB VRAM / 16 GB RAM laptop

```
GPU 8192 − 1024 reserve − ~800 baseline = 6368 MiB available
RAM 16384 × 0.75 = 12288 hard; × 0.65 = 10650 target; − 6144 Windows reserve
```

| Role | Model | Device | num_ctx | Status | Rule |
|---|---|---|---:|---|---|
| `interactive_brain` | `gemma4:e4b` | gpu:0 | 8192 | pinned (3081) | **R6 demotion** — the 12b needs 8001 > 6368 and is dense, so it must fit or be demoted |
| `sidekick` | `gemma4:e2b` | gpu:0 | 4096 | pinned (1763) | 3081 + 1763 = 4844 ≤ 6368 ✓ |
| `embedder` | `qwen3-embedding:0.6b` | cpu | 2048 | resident | R3 — no VRAM headroom |
| `heavy_hitter` | **none** | — | — | **refused** | **R2** — 17391 + 6144 reserve = 23535 > 12288 hard ceiling. Escalates to cloud. |
| `image` | Z-Image FP8 | gpu:0 | n/a | leased, **evicts both pinned seats** | R5 |
| `stt` / `tts` | whisper / Piper | cpu | n/a | on-demand | — |

The refusal is the point: on a 16 GB laptop the honest answer for `heavy_hitter` is "not locally",
stated with its arithmetic, rather than a model that pages until the machine is unusable.

### 5.3 P3 — 24 GB GPU / 64 GB RAM desktop

```
GPU 24576 − 1024 − ~800 = 22752 MiB available
RAM 65536 × 0.75 = 49152 hard; × 0.65 = 42598 target
```

| Role | Model | Device | num_ctx | Status | VRAM |
|---|---|---|---:|---|---:|
| `interactive_brain` | `gemma4:12b` | gpu:0 | 32768 | pinned | 8001 |
| `sidekick` | `gemma4:e2b` | gpu:0 | 8192 | pinned | 1763 |
| `embedder` | `qwen3-embedding:0.6b` | **gpu:0** | 2048 | pinned | 2029 |
| `heavy_hitter` | `gemma4:26b` | gpu:0 | 32768 | **leased** | 17391, **fully GPU** |
| `image` | Z-Image FP8 | gpu:0 | n/a | leased, exclusive | ~8000 |

Pinned total 11793 ≤ 22752 ✓ — so unlike P1 the embedder **is** GPU-resident here; the rule, not
taste, moves it. The heavy lease needs 17391, and 11793 + 17391 = 29184 > 22752, so the Arbiter
evicts **only the 12b**: 3792 + 17391 = 21183 ≤ 22752 ✓. The sidekick and embedder keep serving
throughout the lease — a materially better transition than P1's, derived purely from the budget.
**R6 note:** the 26b fits entirely in VRAM, so expert offload is *permitted but not used*.

### 5.4 P4 — asymmetric dual GPU, 24 GB + 12 GB

```
gpu:0  24576 − 1024 − 800 = 22752 available
gpu:1  12282 − 1024 − 800 = 10458 available
```

| Role | Model | Device | num_ctx | Status | VRAM |
|---|---|---|---:|---|---:|
| `heavy_hitter` | `gemma4:26b` | **gpu:0** | 32768 | **pinned** (fits whole) | 17391 |
| `sidekick` | `gemma4:e2b` | gpu:0 | 8192 | pinned | 1763 |
| `embedder` | `qwen3-embedding:0.6b` | gpu:0 | 2048 | pinned | 2029 |
| `interactive_brain` | `gemma4:12b` | **gpu:1** | 16384 | pinned | 8001 |
| `image` | Z-Image FP8 | **gpu:1** | n/a | **leased, evicts only gpu:1** | ~8000 |

gpu:0 = 17391 + 1763 + 2029 = **21183 ≤ 22752** ✓. gpu:1 = 8001 ≤ 10458 ✓.

This fixture is where three rules become visible at once. **R4**: the 26b is placed whole on
gpu:0 rather than split across both, even though 17391 would "fit" across 22752 + 10458 of
aggregate VRAM — aggregate VRAM is not a resource. **R5**: because a second GPU exists, the image
lease takes gpu:1 exclusively and **gpu:0 keeps serving chat throughout** — the only fixture
where image generation is not a full-system stall. **R6**: the heavy_hitter is *pinned*, not
leased, because it fits without offload; lease-versus-pin is an outcome of the budget, not a
property of the model.

### 5.5 P5 — CPU-only mini PC, 32 GB

```
no GPUs.  RAM 32768 × 0.75 = 24576 hard; × 0.65 = 21299 target; − 4096 Linux reserve
```

| Role | Model | Device | num_ctx | Status | Rule |
|---|---|---|---:|---|---|
| `interactive_brain` | `gemma4:e2b` | cpu | 8192 | resident | smallest viable seat; DDR4 bandwidth governs |
| `sidekick` | **collapses into `interactive_brain`** | cpu | — | — | when the brain is already the cheapest viable model, brain and sidekick are one seat rather than two copies of it |
| `embedder` | `qwen3-embedding:0.6b` | cpu | 2048 | resident | — |
| `heavy_hitter` | `gemma4:26b` | cpu | 16384 | **leased, never pinned** | **R2** — 4096 + 17391 = 21487 ≤ 24576 hard ✓ but > 21299 target, so permitted transiently, refused as a pin |
| `image` | **none** | — | — | **refused** | **R5** — no GPU to lease. Escalates to cloud. |
| `stt` / `tts` | whisper / Piper | cpu | n/a | on-demand | measured RTF 0.869 / 0.472 |

The MoE architecture is what makes the heavy seat viable at all here: 4 B active parameters
against DDR4 bandwidth, not 25.8 B. **INFERRED** — no CPU-only measurement of the 26b was taken;
**UNKNOWN** until one is, and the plan flags the seat as unmeasured on this profile.

The target-versus-hard distinction earns its keep in this fixture: 65 % is what the planner aims
for, 75 % is what the Arbiter refuses to cross, and a transient lease is allowed to sit between
them.

### 5.6 P6 — 64 GB unified memory

**Backend seam: UNKNOWN. Not built.** No MLX, Metal, or ROCm backend exists in the tree
(**VERIFIED by absence** — the discovery audit found the only local backends are Ollama HTTP,
llama-server via the openai-compatible adapter, and in-process CPU voice libraries). This fixture
exists to keep the schema honest about a machine class Friday cannot yet serve, not to imply one.

| Role | Model | Device | num_ctx | Status |
|---|---|---|---:|---|
| `interactive_brain` | `gemma4:12b` | unified:0 | 32768 | pinned |
| `heavy_hitter` | `gemma4:26b` | unified:0 | 32768 | **pinned** |
| `sidekick` | `gemma4:e2b` | unified:0 | 8192 | pinned |
| `embedder` | `qwen3-embedding:0.6b` | unified:0 | 2048 | pinned |
| `image` | Z-Image | unified:0 | n/a | **compute lease, weights not evicted** |

Pinned 29184 MiB of 65536 — under the 75 % ceiling with the whole set resident, which is the
defining property of the class.

**Two rules change shape here and the design must not pretend otherwise.** R2 and R3 are the
same budget on unified memory rather than two independent ones, so a naive implementation would
double-count the reserve. And R5's "exclusive **GPU** lease" was written for discrete cards where
the constraint is memory; on unified memory the scarce resource is **compute**, so the image job
takes an exclusive compute lease while the weights stay resident. Both are called out as
`UNKNOWN` in the fixture rather than encoded, and the golden plan for P6 asserts the refusal
`backend_unavailable` for every seat until a backend exists.

---

## 6. The Arbiter

Runtime owner of the GPUs. Single-threaded transition executor: **all transitions are serial**,
with timeouts and rollback. Concurrency is the enemy of a correct residency layer, and nothing
here needs it.

### 6.1 State machine

```
                    ┌───────────────────────────────────────────┐
                    │                                           │
   [BOOT] ──plan──▶ DEFAULT ──grant──▶ TRANSITIONING ──ok──▶ LEASED
                       ▲                    │                  │
                       │                    │ fail/timeout     │ release / lease expiry
                       │                    ▼                  │
                       └──────────── ROLLING_BACK ◀────────────┘
                                            │
                                     unrecoverable
                                            ▼
                                        DEGRADED
```

| State | Meaning |
|---|---|
| `DEFAULT` | the plan's pinned seats are resident; leases available |
| `TRANSITIONING` | executing an ordered step list; **no other grant is accepted** |
| `LEASED` | a capability lease is held; the plan records what was displaced |
| `ROLLING_BACK` | a step failed or timed out; restoring the default plan |
| `DEGRADED` | rollback itself failed; seats are reported unavailable **with the reason**, never as healthy |

**Boot** computes the plan, then reconciles: whatever is already resident and matches the plan is
adopted rather than reloaded — on P1 that avoids a needless 20.5 s load.

### 6.2 Leases

| Lease | Grants | Displaces on P1 |
|---|---|---|
| `heavy_turn` | `heavy_hitter` for one turn | both pinned seats (R3) |
| `image_job` | exclusive GPU + ComfyUI running | everything (R5) |
| `batch` | a named model for N requests or T seconds | per plan |

Every lease carries a deadline. Expiry triggers the same restore path as an explicit release, so
a crashed holder cannot strand the GPU.

### 6.3 Transitions

A transition is an ordered list of `evict` / `load` / `run` / `restore` steps, each with a
timeout **derived from the estimator in §1.3d** rather than a fixed constant — a 55 s cold load
must not share a timeout with a 21 s one. Failure at any step rolls back to `DEFAULT`.

Backend control, per backend:

| Backend | Evict | Load |
|---|---|---|
| `ollama` | `keep_alive: 0` | generate/embed with explicit `num_ctx` + `keep_alive` |
| `llama-server` | terminate the process | spawn with `-c`, `-ngl`, `--n-cpu-moe`, `--jinja`, `--alias` |
| `comfyui` | stop the process, confirm the port closed | start, wait for readiness |
| `cpu-service` | in-process; no GPU implication | — |

The llama-server column is why the `llama-cpp-brain` descriptor **mechanism** was preserved when
the qwen3.6 brain was decommissioned this session: `routing/provider_descriptors.py` already
classifies a loopback `base_url` as `local` and routes it through the openai-compatible adapter
(**VERIFIED**, and proven end-to-end in `phase-a-report.md` §A7). The Arbiter spawns the process;
the existing descriptor mechanism dispatches to it. No new dispatch path is required.

### 6.4 The headroom watchers

Two refusals, one shape. Both run **before** a load is attempted and both state their arithmetic:

- **RAM (R2)** — refuse if projected resident set + OS reserve would cross 75 % of physical RAM.
- **Disk (R8)** — refuse if free disk after the load would fall below `max(10 GB, artifact_size)`,
  because on Windows the pagefile makes a large resident model a disk consumer (§4.1).

A refusal is a structured object carrying `rule_id`, both numbers, and the nearest permitted
alternative — the same contract as the override refusal in §2.4, so callers handle one shape.

### 6.5 Where this attaches to what exists

- `_pick_local_model` (`routing/model_router.py`) becomes a **consumer** of the plan's
  `interactive_brain` / `sidekick` seats instead of ranking by artifact size.
- `capability_router` resolves `reasoning` → the plan's `interactive_brain`, so the settings
  pointer and the residency plan cannot disagree.
- `provider_health.inference_probe` gains the 5× threshold against
  `CatalogEntry.measured[profile].ms_per_token`.
- `recommend_models` (the hardcoded qwen3 ladder) is superseded by the policy.

---

## 7. Test plan for Phase 2

- **Golden plans** — all six fixtures produce committed `PlacementPlan` JSON; any policy change
  that moves a plan must move a committed file in the same commit.
- **Property tests** — over generated profiles: no plan ever exceeds R2 or R3 on any device;
  image exclusivity holds whenever `len(gpus) == 1`; no model is ever split across GPUs;
  `policy(profile, catalog, overrides)` is byte-identical across 100 repeat calls.
- **Live integration (P1)** — boot to plan; a timed `12b → 26b → 12b` heavy lease cycle with both
  transitions measured; an image lease with ComfyUI start and GPU handback verified by
  `nvidia-smi`; a simulated low-RAM refusal and a simulated low-disk refusal, each asserting the
  explanation text.
- **Prerequisite** — the 5× latency threshold with a test that fails before and passes after,
  using a catalog baseline and a deliberately paged model.

---

## 8. Decision questions

Each answerable in one sentence.

**Q1 — Is a CPU-resident embedder on P1 acceptable?** R3 refuses it on the GPU by 1796 MiB
(§4.2), and it costs 2029 MiB of VRAM for a 639 MB artifact, so the alternative is dropping the
pinned sidekick to make room for it — which trades a 166 tok/s interactive seat for embedding
latency the user never sees.

**Q2 — On the Ollama backend, "pinned" has no mechanism (§1.3c). Which fix?** (a) the Arbiter
runs pinned seats as llama-server processes it owns; (b) tune the Ollama daemon
(`OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_GPU_OVERHEAD`) and re-test, which needs a daemon restart on
your live machine; or (c) accept that P1's pinned pair is aspirational and plan for one resident
model at a time.

**Q3 — The 26b backend measurement needs disk I do not want to take without asking.** Ollama's
number is measured (27.95 tok/s, Q4_K_M). The fair comparator is Unsloth's **UD-Q4_K_M at
16.9 GB** against 19.6 GB free — that leaves **2.7 GB**, which my own R8 rule would refuse. The
clean path is to delete the Ollama `gemma4:26b` copy (17 GB) *after* its measurement is banked,
download the GGUF, and re-pull only if llama-server loses; the cheap path is the **QAT
UD-Q4_K_XL at 14.2 GB** (leaves 5.4 GB) at the cost of comparing different quantizations, which
weakens a decision made "by number." Which?

**Q4 — Should `local_inference_slots` be given meaning or deleted?** It sits in the settings
defaults at `core/__init__.py:1484` with **zero readers** (**VERIFIED**), and leaving a phantom
concurrency knob beside a real placement engine invites exactly the wrong mental model.

**Q5 — Is 10 GB the right floor for R8?** It is a judgement, not a measurement: large enough to
absorb the pagefile growth §4.1 documents, small enough not to refuse routine loads on a machine
that today sits at 19.6 GB free.

---

## 9. Sources

- [unsloth/gemma-4-26B-A4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF)
- [unsloth/gemma-4-26B-A4B-it-qat-GGUF](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-qat-GGUF)
- [Gemma 4 — How to Run Locally, Unsloth](https://unsloth.ai/docs/models/gemma-4)
