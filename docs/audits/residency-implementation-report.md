# Residency and orchestration — implementation report

**Date:** 2026-08-14
**Branch:** `residency-policy`, off `phase-a-truth-flow` @ `53dd414`. **Unpushed, unmerged.**
**Design:** [`docs/design/residency-policy.md`](../design/residency-policy.md)
**Phase 0:** [`residency-state-delta.md`](./residency-state-delta.md)

**Evidence registers, as in the prior audits:**
- **VERIFIED** — the author ran the command or read the cited line and saw the output.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 1. State delta summary

Full detail in [`residency-state-delta.md`](./residency-state-delta.md). The short form:

**Both STOP preconditions held.** A single egress choke point (`_seal_or_block` plus
`gate_worker_payload`) and backend classification earned from the base URL
(`provider_descriptors.py:150-167`, `is_private_host`). No conflicting residency mechanism
existed — `services/scheduler.py` is a job scheduler with no concept of a model or a device.

**All six named Phase A items had landed.** Five needed no further work. The sixth — the real
health probe — had a gap that turned out to be the most important finding of Phase 0: it
performed a genuine inference, computed the latency, and spent it **only on the detail string**.
Status was decided purely on "did text come back". A model paging against RAM returns text,
slowly, and reported **green** — the exact failure this layer exists to prevent, invisible to
the only sensor that would have seen it. It also had no baseline to compare against, which is
why catalog enrichment had to be sequenced *before* the threshold rather than beside it.

**Four contradictions with the mission's assumptions,** all recorded in Phase 0 and all since
resolved or acted on:

| # | Contradiction | Resolution |
|---|---|---|
| 1 | `gemma4:12b`/`:26b` matched the brief but were pulled hours before Phase 0 and had **zero measurements** anywhere | Measured this session (§3) |
| 2 | `qwen3.6:35b` was gone from Ollama but live as the `reasoning` capability via a 16.8 GB llama.cpp GGUF | Decommissioned on Stephen's instruction (§2) |
| 3 | `capability_routing.local` and `model_routing.local_model` pointed at an uninstalled `gemma3:4b` | Repointed, and the fallthrough that made it dangerous was fixed (§6) |
| 4 | Free disk was **2.8 GB** | 19.6 GB after the reclaim, then 36.3 GB with the Ollama 26b removed, then **20.0 GB** after fetching the 16.95 GB comparison GGUF — **VERIFIED** at the end of this report |

---

## 2. Decommissioning qwen3.6:35b

Authorized by Stephen. Executed and verified:

| Step | Result |
|---|---|
| Backups taken first | `~/.friday/decommission-backup-20260814/` — settings, descriptor, `start-brain.ps1` |
| GGUF deleted | **16.80 GB** reclaimed; free disk **2.8 → 19.6 GB**, **VERIFIED** |
| Descriptor removed | `llama-cpp-brain.json`; the drop-in directory is empty and the **mechanism is intact** |
| `reasoning` repointed | `{provider: ollama-local, model: gemma4:12b}`; `orchestrator_model` kept in sync, which `provider_health.py:306` specifically guards |
| Verified through Friday's own layer | `capability_router.resolve("reasoning")` → `available: True`, **VERIFIED** |
| `qwen3-embedding:0.6b` | **untouched**, as instructed — **VERIFIED** present at every checkpoint |

---

## 3. Measurements taken this session

None of these existed. Medians of 5 warm runs after a separate cold load; VRAM read from the
daemon's own `/api/ps` (`size_vram`, `size`), not inferred from `nvidia-smi` deltas.

| Model | num_ctx | tok/s | σ | ms/token | VRAM MiB | Total MiB | On GPU | Cold load |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `gemma4:e2b` | 8192 | **166.13** | 9.76 | 6.02 | 1763 | 1763 | 100 % | 20.97 s |
| `gemma4:e4b` | 8192 | **99.93** | 0.23 | 10.01 | 3081 | 3081 | 100 % | 27.52 s |
| `gemma4:12b` | 16384 | **49.36** | 0.12 | 20.26 | 8001 | 8001 | 100 % | 20.49 s |
| `gemma4:26b` | 16384 | **27.95** | 0.26 | 35.78 | 8586 | 17391 | 49 % | 55.10 s |
| `qwen3-embedding:0.6b` | 2048 | — | — | — | 2029 | 2029 | 100 % | ~3.0 s |

**Probe-condition baselines** (10-token generation, which is what health actually sends):
`e2b` **8.68**, `e4b` **9.99**, `12b` **18.27** ms/token. These differ from the sustained
figures and the difference matters — see §5.

**Host profile, measured:** Windows 11, i7-10700F 8C/16T, 32620 MiB DDR4-2667 dual channel
(≈42.7 GB/s, heuristic from SMBIOS), RTX 4070 12282 MiB with a **1261 MiB** idle compositor
floor, Samsung 870 EVO SATA SSD at **427 MiB/s** (410 MiB/s when re-measured by the shipped
detector).

**Load-time estimator**, fitted and shipped: `artifact_MiB / disk_rate + 3 s` lands within ~20 %
for dense models; the 26b ran ~35 % over, **INFERRED** to be MoE expert placement, so MoE
carries a multiplier. **UNKNOWN** whether that multiplier generalises — one data point.

### 3.1 Three findings that changed the design

**Artifact size is not VRAM footprint, and the error is 4×.** `gemma4:e2b` is a **7.2 GB
artifact occupying 1763 MiB**. `_pick_local_model` ranked candidates by artifact size, so on
this host the heuristic was not merely imprecise — it was inverted.

**num_ctx is itself a placement decision.** Left on Ollama's default the 26b reports `262144`
context and **79 % / 21 % CPU/GPU**. Pinned to 16384 the same model reports **51 % / 49 %**.
Roughly half its GPU residency was being surrendered to a context nobody asked for.

**Ollama silently evicts a model the policy considers pinned.** **VERIFIED** by loading in
sequence and reading `/api/ps` after every step:

```
STEP 1  load 12b @16384      gemma4:12b   vram=8001  100%
STEP 2  load e2b @8192       gemma4:e2b   vram=1763  100%     <- 12b gone
STEP 3  load embedder @2048  qwen3-embedding + gemma4:e2b

VERDICT   12b survived step 2?  False
          final resident set  :  ['gemma4:e2b', 'qwen3-embedding:0.6b']
```

8001 + 1763 = **9764 MiB** against **9997 MiB** available. The pair fits and Ollama refuses it
at 16384, 8192 **and** 4096, with `OLLAMA_MAX_LOADED_MODELS` unset so the default ceiling of 3
was never reached. This is why the Arbiter owns processes (rule R9) rather than asking a daemon.

---

## 4. The six fixture plans

Committed as golden files under `tests/golden/residency/`. P1 is checked against the rules;
P2–P6 are derived from them.

| Fixture | brain | sidekick | embedder | heavy_hitter | image |
|---|---|---|---|---|---|
| **P1** reference | `12b` gpu:0 pinned, ctx 16384, 8001 | `e2b` gpu:0 pinned, 1763 | **cpu** (R3) | `26b` leased, expert offload | leased, **all seats** |
| **P2** 8 GB / 16 GB | `e4b` pinned (R6 demotion) | `e2b` pinned | cpu | **refused (R2)** | leased, all seats |
| **P3** 24 GB / 64 GB | `12b` pinned | `e2b` pinned | **gpu:0** pinned | `26b` leased, **17391 whole, no offload** | leased, all seats |
| **P4** 24+12 asym | `12b` **gpu:1** | `e2b` gpu:0 | gpu:0 | `26b` **gpu:0 pinned, whole** | **gpu:1 only** |
| **P5** CPU-only | `e2b` cpu | **collapsed into brain** | cpu | `26b` leased, over-target | **refused (R5)** |
| **P6** unified 64 GB | refused | refused | refused | refused | refused |

**P1 departs from the specified plan in exactly one place, and the departure is forced.** The
mission states the embedder is resident; a GPU-resident embedder needs 2029 MiB against the
**233 MiB** left after the pinned pair — it exceeds R3 by 1796 MiB and costs more VRAM than the
sidekick for a 639 MB artifact. It is therefore resident **on CPU**. Raised as a question before
building and **confirmed by Stephen**. Everything else lands as specified.

Two derivations worth stating because they were produced by the rules rather than chosen:

- **P3** puts the embedder *on* the GPU where P1 takes it off — same policy, different budget.
- **P4** is the only fixture where image generation is not a full-system stall: because a second
  GPU exists, R5 gives image `gpu:1` while `gpu:0` keeps the 26b, sidekick and embedder serving.
  `gpu:0` totals 17391 + 1763 + 2029 = **21183 ≤ 23140**.

Two modeling errors were caught by running the fixtures rather than reasoning about them:

1. **VRAM requirement is `total_mib`, not the measured `vram_mib`.** The 26b reports 8586 MiB of
   VRAM against 17391 total *because it was measured on a 12 GB card that forced 51 % onto the
   CPU*. That number describes the old card, not the model; carrying it to a 24 GB fixture
   claimed the model needed 8.5 GB there.
2. **Capacity for a leased seat is the GPU's whole budget, not the residual after the pinned
   seats** — a lease is precisely when the Arbiter may evict them.

---

## 5. The 26b backend decision — both figures

Measured quant-for-quant: Ollama's `Q4_K_M` against Unsloth's `UD-Q4_K_M` (16.95 GB), at
`num_ctx 16384`. Ollama's figure was banked **before** its copy was removed to make disk room.

**Ollama, automatic split:** **27.95 tok/s** (median of 5, σ 0.26), 8586 of 17391 MiB on GPU
(49 %), 55.10 s cold load.

**llama-server sweep** (`-ngl 99 --flash-attn on -c 16384`, lower `--n-cpu-moe` = more VRAM):

| `--n-cpu-moe` | tok/s | GPU used MiB | host RAM GB |
|---:|---:|---:|---:|
| 40 | 17.15 | 5190 | 27.4 |
| 32 | 17.32 | 5190 | 27.4 |
| 24 | 22.59 | 7988 | 28.2 |
| **20** | **27.80** | **9802** | 28.7 |
| 16 | **31.34** | 11431 | 29.6 |
| 12 | 14.94 | 11679 | **31.5** |
| 8 | 7.86 | 11544 | 30.2 |

The collapse at 12 is unmistakable and has the same signature as Phase A's 35b sweep: throughput
falls by more than half while host RAM reaches 31.5 GB of 31.9 and the allocator thrashes rather
than erroring.

**Cold-load figures from this sweep are not comparable to Ollama's and are deliberately left out
of the table.** Only the first llama-server load (45.1 s at `--n-cpu-moe 40`) was genuinely cold;
every later row read the same 16.95 GB file from a warm OS page cache and reported 6.5–12.6 s.
The honest comparison is that one 45.1 s figure against Ollama's 55.10 s — llama-server somewhat
faster to load — and it did not influence the decision either way.

**The decision: llama-server at `--n-cpu-moe 20`.** Reasoning, in order:

1. **Throughput is a tie within the budget.** 27.80 vs 27.95 tok/s is a 0.5 % difference, inside
   the run-to-run spread. The peak of 31.34 at `--n-cpu-moe 16` is **not available**: it uses
   11431 MiB against R3's ceiling of 12282 − 1024 = **11258 MiB**, over by 173 MiB. A policy
   that refuses its own optimum would be incoherent.
2. **Disk breaks the tie.** Restoring Ollama's copy means re-pulling 17 GB against 20.1 GB free,
   leaving 3.1 GB — a straightforward **R8 violation**. The GGUF is already on disk.
3. **Control.** llama-server takes explicit `--n-cpu-moe` and `-c`; Ollama's split is automatic
   and, as §3.1 showed, its defaults actively work against placement.

**Recorded honestly:** on throughput alone Ollama is marginally ahead and uses ~1.2 GB less VRAM.
It was not chosen because the constraint set, not the tok/s number, decides — and both figures
are recorded here so the call can be re-litigated if the disk situation changes.

**A side benefit, VERIFIED:** llama-server loads Ollama's content-addressed blobs directly
(`-m ~/.ollama/models/blobs/sha256-…`), so pinned seats reuse weights already on disk and cost
no extra space.

---

## 6. Per-component status

| Component | Status | Commit | Tests |
|---|---|---|---|
| Phase 0 state delta | **VERIFIED WORKING** | `8f4745b` | — |
| Phase 1 design doc | **VERIFIED WORKING** | `e3d091c` | — |
| HardwareProfile | **VERIFIED WORKING** | `dc7e92a` | 25 unit |
| CatalogEntry | **VERIFIED WORKING** | `471502e` | 21 unit |
| ResidencyPolicy | **VERIFIED WORKING** | `c8a4387` | 86 golden + property |
| Health latency threshold | **VERIFIED WORKING** | `3596ea6` | 14 unit |
| `_pick_local_model` VRAM check + `preferred_model` | **VERIFIED WORKING** | `f82f196` | 10 unit |
| Arbiter | **VERIFIED WORKING** | `8d6c9cd` | 21 unit |
| Catalog sees llama-server models; degraded pins | **VERIFIED WORKING** | `31634c9` | covered above |
| Live residency cycles | **VERIFIED WORKING** | `0f4a6f4` | 5 opt-in integration, exit 0 (§7) |

### 6.1 Defects found and fixed along the way

Four of these were live on the machine, not hypothetical.

**The health probe was reporting `down` for a healthy daemon — twice over.**
`resident_model_for` fell back to "smallest installed model" when the configured one was
missing. `model_routing.local_model` pointed at the uninstalled `gemma3:4b`, so the fallback
selected **`qwen3-embedding:0.6b`** — an embedding model — and sent it to `/api/generate`, which
can only ever return empty. **VERIFIED** before the fix: `{"status": "down", "detail": "no
output from a real generation", "model": "qwen3-embedding:0.6b"}`. Separately, every gemma4
model declares `thinking`, and `num_predict=10` is consumed entirely by reasoning: **VERIFIED**
`response=''`, `done_reason='length'`, while the same call with `think:false` returns `'Hello!'`.
Both are now handled from the catalog's capability data. **VERIFIED after:** `status: ok`,
`proved_inference: true`.

**The dangling `gemma3:4b` pointer was not cosmetic.** With it unresolvable, the CODE/RESEARCH
branch fell through to "largest artifact wins" and selected **`gemma4:26b`** — 17391 MiB against
a 9997 MiB budget, the one model on the box guaranteed to spill. The config is repaired and the
fallthrough now filters by measured VRAM.

**The idle GPU floor could be poisoned and cached.** Running detection live while a model was
resident recorded a "baseline" of **11120 MiB** on a 12282 MiB card. Budgeting against that
leaves ~1 GB and refuses every placement. Detection now reports live usage separately and only
`refresh_baseline(assert_idle=True)` may write the floor.

**The disk rule charged the wrong thing.** `admit()` charged the model **artifact** on every
load, refusing a heavy lease that was perfectly fine. The artifact is already on disk; what a
load consumes is **pagefile**, which tracks the resident set (Phase A A7: free disk 27.7 → 7.0 GB
with a 29 GB model resident, recovering on unload). The charge is now the host-RAM portion.

**My own threshold was mis-calibrated on first measurement.** A healthy `e2b` probed at 21.86
ms/token against its 6.02 sustained baseline — 3.6× of a 5× budget — because a 10-token probe
pays fixed per-request overhead a 200-token run amortises away. Probe-condition baselines were
measured and are now the comparator; healthy readings sit near 1.0×.

**And then the threshold turned the whole offline suite yellow.** The final full run failed on a
*pre-existing* test — `test_v5_subsystem_routes.py::TestHealth::test_health_ok` — with
`assert 'degraded' == 'ok'`. Diagnosed rather than assumed: the offline suite's wire-shaped
transport doubles (`tests/fake_backends.py`, the D9 inversion) return canned payloads whose
`eval_count`/`eval_duration` are **invented**, implying **142.86 ms/token**. My rule compared
that fabricated number against the **real host's** seeded 9.99 ms/token baseline — 14.3× — and
reported the stub as paging.

Two things were wrong, and only one of them was the test: a stubbed timing is not a measurement,
and `_latency_verdict` was reaching `hwp.get()` (and therefore `nvidia-smi`) from inside an
offline unit test, which it has no business doing. The rule is now gated on
`_timings_are_real()` at the **probe** call site rather than inside the verdict, so the verdict
stays pure and fully unit-tested while its application to a fabricated measurement is
suppressed. The pre-existing assertion was left exactly as it was — it was right.

---

## 7. Live transition timings

Run on the reference instance with `pytest tests/integration/test_residency_live.py
--run-live-residency`. These load real multi-GB models and start real processes; they are
deselected by default so the offline suite stays offline and fast.

### 7.1 Heavy lease cycle — `12b → 26b → 12b`

| Direction | Transition | What happened |
|---|---:|---|
| **Grant** | **37.96 s** | evict the pinned 12b and e2b, then load the 26b GGUF on llama-server at `--n-cpu-moe 20` |
| **Release** | **47.98 s** | terminate the 26b, restore both pinned seats |

**VERIFIED** by the assertions around the timings, not just the clock: at grant,
`gemma4:26b` is in `llama.procs` and `gemma4:12b` is not; at release the reverse. The release is
slower than the grant because it restores **two** seats where the grant loads one.

**INFERRED:** these sit close to the load-time estimator's prediction — the 26b's 16.95 GB
artifact at 427 MiB/s predicts ~40 s + MoE placement, and the eviction is nearly free. The
estimator is what sets the transition timeout, so a cycle that runs at roughly its predicted
cost is the estimator working rather than a coincidence.

### 7.2 Image lease cycle — exclusive GPU, ComfyUI start and handback

| Direction | Transition |
|---|---:|
| **Grant** | **44.24 s** — evict every seat, start ComfyUI, wait for readiness |
| **Release** | **46.35 s** — stop ComfyUI, restore both pinned seats |

**A caveat on the GPU figure, because the printed number does not say what it looks like.**
The test logged `gpu 11721 -> 11721 MiB`. Those two readings are **identical and both high**
because both are taken with the pinned seats resident — before the lease and after the restore.
They therefore demonstrate nothing about handback on their own. What the test actually asserts,
and what is load-bearing, is that `comfy.running()` is `False` after release and both pinned
seats are back in `llama.procs`. **UNKNOWN from this run:** the intra-lease VRAM floor. The
check that would settle it is sampling `nvidia-smi` *during* the lease, which this test does not
do. Recorded rather than dressed up.

### 7.3 RAM-ceiling refusal, live

Simulated by shrinking the profile's RAM rather than by exhausting the machine:

```
refused: 23535 MiB projected (OS reserve 6144 + resident 0 + 17391 to load)
exceeds the 6144 MiB hard ceiling, 75% of 8192 MiB physical
```

**A real edge case this exposed.** On an 8 GB Windows host, R1's 6144 MiB OS reserve *equals*
the entire 75 % ceiling (6144 MiB), so the budget available to models is exactly zero and every
local model is refused. The arithmetic is correct and the conclusion is arguably right — an 8 GB
Windows machine genuinely cannot host these models — but the two thresholds colliding at the
same number is a coincidence of this fixture, not a designed behaviour. Raised as **Q7**.

### 7.4 Cleanup

**VERIFIED** after the run: ports 8090, 8091, 8110 and 8188 all closed; no llama-server or
ComfyUI process left behind. GPU settled at 3844 MiB, which is **Friday's own live server
reloading its sidekick during normal operation** — the same behaviour Phase A recorded at the
end of its provisioning run, not a leak from these tests.

---

## 8. Definition of done

| Requirement | Status |
|---|---|
| Default suite green | **VERIFIED** — `pytest -q` exit 0, **5788 tests**, zero FAILED/ERROR lines. Recorded because an earlier run in this session reported exit 0 from a `tail` rather than from pytest, and a second genuinely failed (§6.1) — the exit code is read from pytest itself. |
| Six fixture profiles produce committed golden plans | **VERIFIED** — `tests/golden/residency/P1..P6.json` |
| Reference instance boots to its plan | **VERIFIED** — §7, 5/5 live tests, exit 0 |
| Timed heavy lease cycle | **VERIFIED** — grant 37.96 s, release 47.98 s (§7.1) |
| Image lease cycle | **VERIFIED** — grant 44.24 s, release 46.35 s, ComfyUI started and stopped (§7.2) |
| Refuses a RAM-ceiling breach with a stated reason | **VERIFIED** — unit + live (§7.3) |
| Health can fail | **VERIFIED** — and no longer fails *wrongly* (§6.1) |
| Branch unpushed | **VERIFIED** — no `push`, `merge` or PR command was run |
| Zero unsupported claims | every number in this report carries its method |

---

## 9. Decision questions

Each answerable in one sentence.

**Q1 — Should the pinned pair move to llama-server permanently, given it now works?**
The Arbiter runs pinned seats as llama-server processes loading Ollama's own blobs, which means
two different runtimes serve local models depending on whether a seat is pinned or leased; the
alternative is moving everything to llama-server and using Ollama only as a model store.

**Q2 — Is `--n-cpu-moe 20` the right operating point, or should R3's ceiling bend?**
`16` is 12.7 % faster and exceeds the VRAM budget by 173 MiB (1.4 %), which is within the margin
the 1 GB reserve was chosen to protect.

**Q3 — Should `local_inference_slots` be given meaning or deleted?**
It sits in the settings defaults at `core/__init__.py:1484` with **zero readers**, and leaving a
phantom concurrency knob beside a real placement engine invites the wrong mental model.

**Q4 — Is 10 GB the right floor for R8?**
It is a judgement, not a measurement: large enough to absorb the pagefile growth Phase A A7
documented, small enough not to refuse routine loads on a machine sitting at ~22 GB free.

**Q5 — Should the residency plan drive `capability_routing` directly?**
Today the plan and the settings pointers are reconciled by hand — that is how `reasoning` came
to point at a decommissioned model and `local` at an uninstalled one; making the plan
authoritative would remove the class of defect entirely, at the cost of user-set pointers being
overridden by policy.

**Q9 — Nothing boots the Arbiter when the server starts; should it?**
This is the largest remaining gap and it is visible right now: with everything built, tested and
committed, the **live** Friday server on `:3000` is running `gemma4:12b` at **262144 context,
29 % / 71 % CPU/GPU** — **VERIFIED** by `ollama ps` at the end of this session. That is exactly
the placement the layer exists to prevent, and it persists because the running process predates
this branch and because `server.py` has no `Arbiter.boot()` hook. The policy engine, the
catalog, the profile and the arbiter are all real and proven; **nothing yet owns the running
system.** Wiring that is a deliberate next step rather than an oversight — it changes startup
behaviour on Stephen's daily driver — but until it happens the measured benefits are available
only to code that asks for them (`_pick_local_model` does, after a restart; `num_ctx` does not,
because setting it is the Arbiter's job).

**Q7 — On an 8 GB Windows host, R1's 6 GB OS reserve consumes the entire 75 % RAM ceiling, so
every local model is refused (§7.3); is that the intended answer or should the reserve scale?**
A fixed 6 GB reserve is right on a 32 GB machine and total on an 8 GB one, and the two
thresholds landing on the same number is a coincidence of arithmetic rather than a decision.

**Q8 — Should `gemma4:e2b` get a standalone GGUF so the sidekick can be genuinely pinned?**
Ollama stores it across multiple blobs, so llama-server cannot load it from the blob store
(`expected 2012 tensors, got 601`) and that seat currently runs as a **degraded pin** on Ollama —
which means the daemon may evict it, the exact behaviour R9 exists to prevent.

**Q6 — Does the embedder stay on `all-MiniLM-L6-v2`?**
`capability_routing.embedding` still names it while `qwen3-embedding:0.6b` sits installed and
measured at 1024 dimensions; D5 gates the switch on a re-index path that still does not exist.
