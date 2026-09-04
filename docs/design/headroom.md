# Headroom — the machine stays usable while Friday works

**Date:** 2026-09-04
**Branch:** `docs/headroom-spec`, off `integration/release-2026-09-03` @ `f000f07`. **Doc-only.
Pre-approved for build: a Sonnet 5 session executes §12 after this lands. Nothing in this
document exists yet unless marked VERIFIED.**
**Subject:** Stephen, 2026-09-04, verbatim:

> *"you know, maybe hardware-specific model fetching is a function we should add to the Friday
> desktop settings menu!"*

and, sharpening it:

> *"we need to ensure the user's machine runs capably for at least surface level work while
> Friday does stuff with local models, including juggling local models such as a user, engaging
> a local voice model and then executing against the outputs with a local reasoning model, which
> then fires a workflow with local image or video models, etc ... so: headroom measurement is
> vital."*

**Method:** STORM — ground truth read from the codebase first (§2), the week's evidence stated
with its provenance (§3), simulated disagreement at full strength (§10), cited synthesis. Every
claim about the tree was checked on 2026-09-04 at `f000f07` unless marked otherwise. No code was
run, no GPU was touched, nothing under `Friday-Models` was opened.

**Inherits, and does not restate:**

- [`residency-policy.md`](residency-policy.md) — HardwareProfile, CatalogEntry, the pure
  `plan()`, rules **R1–R11**, the six fixtures P1–P6, and the Arbiter's lease model. **This
  document is an extension of that one.** Where it changes a rule it says so by number.
- [`symphony-of-intelligence.md`](symphony-of-intelligence.md) — §2.4 the lease model and
  latency classes; §2.5 the 53-second rule, which is the origin of the work queue.
- [`../audits/model-suite-determination.md`](../audits/model-suite-determination.md) — the
  finding this document leans on hardest: **a seat reads 1,229 MiB idle and 9,652 MiB
  exercised.** Measure under load, never at idle.
- [`../audits/residency-implementation-report.md`](../audits/residency-implementation-report.md)
  §7 — the only measured lease transitions: **grant 37.96 s, release 47.98 s.**
- [`../contracts/roles-and-model-identity.md`](../contracts/roles-and-model-identity.md) — the
  two alias tables and the rule that a residency class is not a model count.
- [`vault-first-onboarding.md`](vault-first-onboarding.md) §2.4 and Q-V9; and
  [`onboarding-interview.md`](onboarding-interview.md) (branch
  `worktree-spec-onboarding-interview`, `1c22361`, not on this branch) §16.2 (V1–V3) and D5. §9
  here supplies what those two documents ask for from the hardware side.
- `KNOWN_ISSUES.md` §1 — the invisible-success failure class. §2.9 of this document lists three
  live instances found on the way.

**Evidence registers**

- **VERIFIED** — the cited file/line was read during this audit, 2026-09-04, at `f000f07`.
- **MEASURED** — a number produced by a stated method, by a named prior session, in a cited
  document or source comment. None were re-measured here.
- **REPORTED** — a number relayed in the brief for this document from Stephen's own week. Not
  in the tree, not re-measured. Used as design evidence, labelled as such every time.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.
- **PROPOSED** — design in this document. Nothing marked PROPOSED exists.

---

## 0. The position, up front

**Stephen is asking for something smaller than what already exists, on top of something that
does not exist at all.** Both halves matter, and the second is the work.

1. **The measuring machinery is largely built — for text.** A detected, cached HardwareProfile
   with per-GPU baselines (`services/hardware_profile.py`, 875 lines); a pure placement policy
   with eleven inspectable rules and six golden fixtures (`services/residency_policy.py`, 1,526
   lines, `tests/golden/residency/P1–P6.json`); an Arbiter that owns llama-server processes and
   grants exclusive leases with timeouts and rollback (`services/residency_arbiter.py`, 1,624
   lines); a catalog of VRAM measured at stated contexts (`residency_catalog.SEED_MEASUREMENTS`);
   a pause forecaster that warns before silence with the three-way choice Stephen approved
   (`services/pause_forecast.py`); a work queue with an idle-aware drain (`services/work_queue.py`);
   and a Settings panel called **THE MACHINE** that already draws the VRAM bar with a reserve
   marker (`index.html:33019`). All **VERIFIED**. "Hardware-specific model fetching in Settings"
   is a thin surface over this, and §8 specifies it in a page.

2. **Four things are absent, and they are the product he described.**
   - **No single number for "the machine stays usable."** There are **six reserve constants for
     five concepts** (§2.2), and the one that guards the last line before a load — the
     `R-DISPLAY-RESERVE` check in `Arbiter.grant` — resolves to **256 MiB** on a single-monitor
     Windows machine while the planner assumes at least **2,560**. RAM is budgeted from
     *total*, never from what is *available* right now (§2.3). Disk is checked against a cached
     figure on whichever volume holds the model store, while the incident that took the live
     app down this week filled the *system* volume with something that was not a model (§2.4).
   - **No notion of a chain.** A lease is one thing taking the card. The sequence Stephen
     described — speak, transcribe, reason, render, speak back — is four or five stages with
     different residency needs, and today each would take and release its own lease with the
     brain reloading between them, or be refused outright by `_local_brain_ready()` while an
     image lease holds the card (§2.6).
   - **No footprint for anything that is not a language model.** The image seat is planned with
     `vram_mib: None` (**VERIFIED**, `residency_policy.py:906`); the two figures that exist for
     Z-Image disagree by nearly 2× and neither was measured under the Arbiter (§2.5); no local
     video backend exists in the tree at all; voice runs on CPU and its RAM is counted nowhere.
   - **Nothing can see degradation.** Every check in the tree asks *will it fit*. The training
     run that turned 7-second steps into 57-second steps did not fail to fit — it fit with
     354 MiB to spare and then thrashed, at 100 % reported utilisation and a quarter of its
     power budget (§3, REPORTED). Nothing in the tree reads utilisation, power, clocks, or the
     WDDM shared-memory counter that is the paging tell (**VERIFIED by absence**, §2.8).

3. **The design consequence is one sentence.** "Fits" is not a verdict; it is one of three axes,
   and the other two — *runs well* and *worth it* — need measurements the catalog does not yet
   hold and telemetry the Arbiter does not yet sample. Every surface this document specifies
   renders those three axes with a basis or renders **unknown**, and never the word
   "compatible" (§5, HR1–HR2).

4. **Three drifts were found on the way and are cheap to fix first** (§2.9): the setup wizard
   still hard-codes `gemma3:4b` — defect H3, fixed in five Python sites on 2026-08-26, alive in
   the UI; the installer carries its own five-rung Qwen ladder that the 2026-09-03 Gemma-only
   decision removed from `model_plan._BRAINS`; and the Settings panel draws a 1 GB reserve the
   planner does not use.

**What only Stephen decides** is in §13. The largest is D1: the default posture, which is a
trade between Friday's quality and the user's machine, and on his own card it means the
resident brain is smaller than the one he runs today (§6.3, §14.4).

---

## 1. What Stephen asked for, itemised

| # | Ask, in his words or close to them | Where answered |
|---|---|---|
| R1 | "hardware-specific model fetching … in the settings menu" | §8.2 — the fetch surface and the pre-fetch card |
| R2 | "the user's machine runs capably for at least surface level work" | §4 — the Headroom Contract, as numbers a machine can be held to |
| R3 | "juggling local models" — voice, then reasoning, then image or video | §6 — the chain planner; §2.6 what happens today |
| R4 | "headroom measurement is vital" | §4.3 — the monitor; §5 — footprints measured under load |
| R5 | (standing) both paths always available, user chooses | §6.4 — per-stage cloud substitution, never silent |
| R6 | (standing) the user always knows what is happening to their data and which model serves them — extended to their hardware, before it happens | §8.1 — before, during, after |
| R7 | (from the brief) connect to onboarding: detect the machine, propose a starting set | §9 |
| R8 | (from the brief) design around "fits is not binary" — LTX on 32 GB RAM, a degraded quant, a licence | §5.2 — three axes; §10.5 the adversary |

---

## 2. Ground truth

### 2.1 What exists, module by module

| Module | What it does | Consumed by | Register |
|---|---|---|---|
| `services/hardware_profile.py` | Detects OS/CPU/RAM/GPUs/disk; measures the idle VRAM floor at boot; samples the live desktop draw from the WDDM per-process counter; rejects impossible readings and falls back to device `memory.used` minus our own seats | Arbiter (`compute_plan`, `boot`), policy (`gpu_budgets`), CLI | VERIFIED |
| `services/residency_catalog.py` | `SEED_MEASUREMENTS` for P1: `gemma4:e2b/e4b/12b/26b`, `qwen3-embedding:0.6b` at stated `num_ctx`, VRAM from the daemon's own `/api/ps`; MoE detection from GGUF metadata; load-time estimator from disk rate | policy, Arbiter, `model_plan` (by copy — see §2.9) | VERIFIED |
| `services/residency_policy.py` | Pure `plan()` → seats + refusals; rules R1–R11 as data; per-GPU budgets; RAM and disk headroom checks; context ladder capped at the largest *measured* context | Arbiter, `/api/residency/*`, `/api/intelligence` | VERIFIED |
| `services/residency_arbiter.py` | Boots to plan; `grant("heavy_turn" \| "image_job")` with serial lock, timeouts derived from `est_load_s`, rollback; `admit()` runs R2+R8 before a load; `_ours_resident_mib()` for the display probe | `local_image.generate`, `work_queue.drain`, `routes/residency.py` | VERIFIED |
| `services/gpu_headroom.py` | `check(need_mib)` and `display_at_risk()` against a **fixed 1,024 MiB** reserve; **only reports, never evicts** | `/api/intelligence` machine panel, `scheduler.py` away-drain (`check(6000)`), `research/harness.py` | VERIFIED |
| `services/pause_forecast.py` | "Will Friday go quiet, for how long, how sure" — `local_turn`, `heavy_lease`, `image`, `drain`; confidence + basis; the three-way options | `/api/work/forecast`, `routes/chat.py:634`, `local_image.generate` (eta on the orb) | VERIFIED |
| `services/work_queue.py`, `workflow_plan.py` | Classes `reflex/interactive/heavy/image/background`; dispositions `when_away/now_local/now_cloud`; `idle_seconds()`, `is_away()`; proposals Friday raises and Stephen decides | `scheduler.py`, `/api/work/*`, chat UI | VERIFIED |
| `services/model_plan.py` | The Gemma-4 `_BRAINS` ladder with **measured** `vram_gib`; `plan()` returns tiers with `ready/install/refused` and the rule; `FLOOR_MODEL` derived | `cli.cmd_models`, `install.ps1` (by copy), `core.DEFAULT_SETTINGS` | VERIFIED |
| `services/model_setup.py`, `model_store.py` | Install reports success only when the daemon lists the tag; Friday's own GGUF store reads facts from headers | CLI, Arbiter (`gguf_models`) | VERIFIED |
| `services/local_image.py` | Z-Image Turbo FP8 and SD 3.5 Medium via ComfyUI under the exclusive lease; `licence` field on SD 3.5; cancel as a job flag | `creative_engine.generate_image` | VERIFIED |
| `services/local_voice.py`, `nemo_voice.py` | faster-whisper `small` int8 **CPU**, Piper CPU; NeMo GPU tier gated on `MIN_VRAM_GB = 4.0` free | `routes/voice.py` | VERIFIED |
| `routes/intelligence.py` | Composes catalog + plan + machine + costs into one payload; humanises refusals into problem/choice/info | Settings → Intelligence | VERIFIED |
| `index.html:32880–33072` | **THE MACHINE**: VRAM bar with `reserve_mib`, RAM bar, "Loaded now", three refusal groups | — | VERIFIED |
| `index.html:40716–40745` | Chat path calls `/api/work/forecast` before a local turn and shows the three-way card; remembered per seat; `pause_warnings_off` | — | VERIFIED |

**INFERRED:** the residency layer is finished enough that this document adds no new *kind* of
component. It adds a contract, a monitor, a catalog extension, a chain planner, and a surface —
each attached to a named module above.

### 2.2 Six reserve constants for five concepts

Every one of these is "how much VRAM the desktop keeps." **VERIFIED**, each at its line:

| Constant | Value | Who reads it | What it guards |
|---|---:|---|---|
| `gpu_headroom.DEFAULT_DISPLAY_RESERVE_MIB` | 1,024 | THE MACHINE panel (`display_at_risk`, `intelligence.py:587`); away-drain `check(6000)` (`scheduler.py:1072`); research harness | the bar the user sees; the drain gate |
| `hardware_profile.DEFAULT_VRAM_BASELINE_MIB["windows"]` | 1,024 | `effective_baseline_mib` when no idle floor was measured | the planner's floor, unmeasured case |
| `hardware_profile.MIN_DISPLAY_RESERVE_MIB["windows"]` | 2,560 | `live_display_mib` clamps the WDDM sample up to this | the planner's floor, measured case |
| `hardware_profile.display_reserve_mib()` | 256 + 192 per extra monitor + 128 per HiDPI + 256 per indirect adapter | `vram_headroom()` → **`Arbiter.grant` R-DISPLAY-RESERVE** (`residency_arbiter.py:1308`); `liveness_audit.py:358` | **the last check before a lease takes the card** |
| `residency_policy.VRAM_RESERVE_MIB` (R3) | 1,024 | `gpu_budgets`, *on top of* the baseline | buffers and slack in the plan |
| `model_plan.DISPLAY_RESERVE_GIB` | 2.5 GiB | installer ladder pick, `friday models` | which rung is offered |

**INFERRED, and this is the finding:** on Stephen's single-monitor 4070 the planner refuses to
plan below **2,560 + 1,024 = 3,584 MiB** of headroom, the Settings panel tells him the reserve
is **1,024**, and the gate that actually stands between a lease and the display driver accepts
**256 MiB** free. The 2026-08-17 monitor loss happened at 322 MiB free
(`residency_arbiter.py:1300–1306`). The last-line check would pass at 322 MiB today.

The four modules were written by four sessions across three weeks, each fixing the incident
in front of it. None is wrong in isolation. Together they mean there is no number a machine can
be held to, which is exactly R2.

### 2.3 RAM is budgeted from total, never from available

`residency_policy.ram_budget()` reads `profile.ram.total_mib` and subtracts the OS reserve and
the 75 %/65 % ceilings (**VERIFIED**, `:414–427`). `check_ram_headroom()` adds the projected load
to the OS reserve and *our* resident host portion (**VERIFIED**, `:1467–1481`). The profile
carries `ram.available_mib` (**VERIFIED**, `hardware_profile.py:121–139`) and the Intelligence
panel renders it — **and no planning or admission path reads it** (**VERIFIED**: the only
`available_mib` readers outside `hardware_profile` and the panel are GPU budget rows, which are
a different field of the same name).

**INFERRED:** a user with a browser holding 8 GB, a game, or a second Friday is invisible to the
RAM rule. The rule protects the machine from Friday's own resident set, not from Friday's
resident set *plus the user's day*. R2 is necessary and not sufficient for R2 in Stephen's sense.

### 2.4 Disk: three checks, one cached figure, and the wrong volume

- **R8** refuses a load if `profile.disk.free_mib` minus the host-RAM portion would fall below
  `max(10 GiB, artifact)` (**VERIFIED**, `residency_policy.py:1484–1499`,
  `residency_arbiter.admit`). `free_mib` is written by `detect_disk()` into the cached profile;
  the profile's `refresh_triggers` are GPU count, VRAM, RAM and OS (`residency-policy.md` §2.1).
  **UNKNOWN:** how often `free_mib` is re-read between profile refreshes. The check: read
  `hardware_profile.get()` and `detect_disk(prior=…)` for the cadence. If it is boot-time only,
  R8 is checking a figure that can be a day old.
- The pull preflight warns below **15 GB** on the volume holding the Ollama store
  (**VERIFIED**, `routes/skills.py:240–275`).
- `model_plan` refuses the vault tier below **2 GiB** (**VERIFIED**, `model_plan.py`, the
  `vault_need` block).

**MEASURED by another session, 2026-09-04 (memory `gotcha-test-suite-leaks-temp-homes`):** C:
reached 0 bytes with 3,858 leaked `friday_test_home_*` directories while the live app ran, and
the app went down. **INFERRED:** none of the three checks above would have seen it coming —
none watches the system volume continuously, and the consumer was not a model. The contract in
§4 watches the system volume, always, because the pagefile, the logs and the databases live
there whatever `OLLAMA_MODELS` points at.

### 2.5 What is measured for image, video and voice

| Modality | What the tree knows | Register |
|---|---|---|
| **Image** | The seat is planned as `vram_mib: None, est_load_s: None` (`residency_policy.py:896–909`). `residency-policy.md` §5.1 says "~8000". `local_image.py:10–11` says "Z-Image's weights are ~14.5 GB against a 12282 MiB card, so the language seats must be out of VRAM before it loads." ComfyUI start 93 s warm / ~180 s cold, render 93 s at 1024² (`pause_forecast.py:61–62`, 2026-08-15). SD 3.5 Medium carries a `licence` string; Z-Image does not. | VERIFIED (the disagreement); MEASURED (the timings); **UNKNOWN** (resident VRAM under the lease — the check is one `nvidia-smi` sample mid-render under the Arbiter, recorded into `SEED_MEASUREMENTS` with the workflow named) |
| **Video** | **No local video backend exists.** `creative_engine.py:853–865` dispatches video to Higgsfield only; no `ltx`, `wan`, `hunyuan` or `cogvideo` reference anywhere under `src/` outside the Higgsfield catalog names `flux_3_video`, `veo3`, `gemini_omni`. | **VERIFIED by absence** |
| **Voice, CPU tier** | faster-whisper `small`, `device="cpu", compute_type="int8"` (`local_voice.py:330`); Piper `en_US-amy-medium`; Kokoro measured RTF 0.472, Piper RTF 0.472, whisper RTF 3.6 on a 5 s clip (`phase-a-report.md:332–334`). Planned as `_cpu_seat` with `vram_mib: 0` and no host-RAM figure (`residency_policy.py:1338–1341`). | VERIFIED; host RAM **UNKNOWN** (check: RSS delta of the server process across `WhisperASR.load()` and `PiperTTS.load()`) |
| **Voice, GPU tier** | NeMo requires torch-CUDA the venv lacks and `MIN_VRAM_GB = 4.0` free (`nemo_voice.py:69`). It decides readiness by asking the card directly; the Arbiter is not consulted and does not know the tier exists. | VERIFIED |

**INFERRED:** the three modalities the chain needs are, respectively, *unmeasured*, *absent*,
and *uncounted*. The chain planner in §6 cannot be built honestly until §5's footprints exist,
which is why §12 sequences measurement before planning.

### 2.6 What the chain does today, stage by stage

Take Stephen's sentence literally on P1 with the code as it stands:

1. **Speak.** `routes/voice.py:902` refuses a local session unless `_local_brain_ready()` names a
   `reasoning` seat (**VERIFIED**). So a voice session presupposes a resident brain.
2. **Transcribe.** faster-whisper on CPU. No lease, no GPU, RAM uncounted.
3. **Reason.** The resident brain answers. If the tool loop decides to fire an image workflow:
4. **Render.** `local_image.generate` takes `image_job`, which runs `_evict_pinned()` then
   `_evict_all_but_retained()` — everything leaves except the sidekick (R10) — then starts
   ComfyUI (**VERIFIED**, `residency_arbiter.py:1349–1356`). **The brain is gone for the
   duration.** A second voice utterance during those ~3 minutes hits step 1 and is refused, or
   is answered by whichever seat `capability_router` resolves `reasoning` to, which after the
   eviction is nothing.
5. **Release.** `_restore_pinned()` reloads the brain: 20–28 s on P1 (`SEED_MEASUREMENTS`
   `cold_load_s`).
6. **Speak back.** Piper on CPU.

Measured costs of the only lease cycle anyone has timed: **grant 37.96 s, release 47.98 s** for
the heavy seat (`residency-implementation-report.md` §7). For the image lease: **93 s** start
warm, **~180 s** cold, **93 s** render, plus the brain's reload. **INFERRED:** one spoken
request that produces one image costs roughly **four minutes** of wall-clock on P1 today, during
which Friday can hear but cannot think, and nothing tells the user any of this until the
image orb appears with an ETA.

That is not a bug in any one module. It is what "no notion of a chain" looks like from the
chair.

### 2.7 What the user can see today

- **THE MACHINE** (`index.html:33019–33071`): the VRAM bar with a 1,024 reserve marker, the RAM
  bar with the OS reserve, "Loaded now", and refusals split into *Needs your attention* /
  *choices* / *notes* (**VERIFIED**). It draws the reserve from `gpu_headroom`, not from the
  planner (§2.2).
- **Before a local turn**: the forecast card with *Wait for it / Use the cloud instead / Do it
  while I'm away*, answered once per seat (**VERIFIED**, `index.html:40729–40745`).
- **During**: one orb per job with model and `eta_s` (`core.process_register`,
  `local_image.py` "Image: …" orb).
- **The wizard's Hardware Check** (step 4, `index.html:30126`, `:30839–30934`): GPU name, RAM,
  VRAM, `suggested_models` from `ollama_manager.recommend_models`, and `WizardGemmaPull`.
- **The installer**: card size, "about N GB left for Friday", the pick, and the two-way
  choice (`install.ps1:318–400`).

Nothing shows a *fetch* consequence before a fetch, nothing shows a *chain* before it runs, and
nothing shows the state of the contract in §4 because there is no contract.

### 2.8 Degradation is invisible

**VERIFIED by absence:** no file under `src/` queries `utilization.gpu`, `power.draw`,
`clocks.sm`, or the `GPU Adapter Memory(*) / Shared Usage` counter (system RAM being used as GPU
memory — the WDDM paging tell). The only foreign-tenant awareness is
`hardware_profile._foreign_occupancy_mib()`, and it is consulted **only as a fallback** when the
WDDM display reading is rejected as impossible (`hardware_profile.py:397–423`, `:498–511`). The
comment there records the case: on 2026-09-01 the Arbiter believed 8,451 MiB were available
while `nvidia-smi` showed 11,557 of 12,282 in use by a fine-tuning run. That is the same run
class as §3, seen from Friday's side, and it was seen only because a *different* reading
happened to be rejected first.

The catalog carries `probe_ms_per_token` baselines for the health probe
(`residency_catalog.py:322–334`). **UNKNOWN** whether `provider_health.inference_probe` compares
against them today; the check is to read that function for a threshold.

### 2.9 Three drifts, all instances of KNOWN_ISSUES §1

| Drift | Where | What it does | Register |
|---|---|---|---|
| **H3 lives on in the UI.** `const BUNDLED_MODEL = 'gemma3:4b'` and a family-prefix match `startsWith('gemma3')` | `index.html:30137`, `:30149`, `:30237` | The wizard offers to pull, and reports ready, the one model the ladder cannot select. The 2026-08-26 sweep (`7da7798`) fixed five Python sites and a test walks `DEFAULT_SETTINGS`; the HTML was outside its reach | VERIFIED |
| **Two ladders.** `$brainLadder` is `qwen3:4b / qwen3:8b / gemma4:12b / qwen3:14b / qwen3:32b` and `$localIsComfortable` tests `-ne 'qwen3:4b'` | `install.ps1:338–357` | The 2026-09-03 decision removed every Qwen row from `_BRAINS` (`model_plan.py`, the note above the tuple). The installer will name a model the app's planner no longer knows, then `friday models` will pick a Gemma. On an 8 GB card the installer's "floor rung" is a model that will not be installed | VERIFIED (the divergence); INFERRED (the effect) |
| **The reserve the panel shows is not the reserve the planner uses** | `intelligence.py:587–591` → `gpu_headroom.display_at_risk()` | 1,024 drawn; ≥2,560 planned; 256 gated | VERIFIED, §2.2 |

Also noted, out of scope, carried from memory `project_gpu_tenancy_port_8090_collision`:
`PORT_BASE = 8090` is the port the Friday-Models eval server defaults to, and `boot()` evicts
everything unconditionally before measuring the baseline (`residency_arbiter.py:1254–1258`,
**VERIFIED**). The monitor in §4.3 is how Friday would *see* that tenant; moving the port is a
one-line change this document does not make.

---

## 3. The week's evidence, and what it changes

**REPORTED, from the brief.** A training run held **11,928 of 12,282 MiB** on Stephen's RTX
4070. It did not fail. Step time went from **~7 s to ~57 s**. The GPU drew **51 W of 200 W**
while reporting **100 % utilisation** — cores stalled on memory — and the kernel logged repeated
residency failures as Windows paged allocations in and out. **354 MiB** was the difference
between working and thrashing. Separately (§2.4, MEASURED by another session), a disk filling to
zero crashed the live app outright.

Three design facts follow, and every later section is built on them.

**3.1 The failure mode is degradation, not refusal.** Everything in `residency_policy` is a
refusal rule: it either places a seat or explains why not. The run above would have been
*placed* by every rule in the file — 354 MiB clears the 1,024 reserve on paper. A system that
only refuses cannot protect a machine from something that fits. It needs a second kind of
check that runs *after* placement, against the live machine, and can say "this is fitting and
it is still wrong."

**3.2 The margin is smaller than any single allocation.** 354 MiB is less than the KV cache of
a small context bump. A reserve computed once and planned into exactly is a reserve of zero the
moment anything else moves. Hence §4's *slack* — an amount the planner is forbidden to fill —
distinct from the display reserve, which is an amount the *desktop* fills.

**3.3 The signature is measurable, on this vendor, on this OS.** Utilisation at 100 % with
power at a quarter of limit is a specific and cheap signal: `nvidia-smi
--query-gpu=utilization.gpu,power.draw,power.limit,clocks.sm` returns all four in one call, the
same call shape `gpu_headroom.gpu_memory()` already makes. **INFERRED:** it is a proxy, not a
measurement of paging; a legitimately memory-bound kernel at low power looks the same for a
moment. So §4.3 requires the signature to be *sustained* and pairs it with a throughput check
Friday can make on her own seats. The Windows kernel residency-failure log is **UNKNOWN** to
this document as a programmatic source; the check is whether `Get-WinEvent` exposes the
`Microsoft-Windows-DxgKrnl` provider with a usable event for it. It is not promised.

---

## 4. The Headroom Contract — "capable for surface-level work" as numbers

**PROPOSED.** A contract is a set of floors the machine must stay above *while Friday holds any
resource*, stated per resource, each with the measurement that checks it and the response when
it is breached. It is named so it can be cited in a refusal, shown in Settings, and tested
against fixtures. It replaces the six constants in §2.2 with one function.

### 4.1 What "surface-level work" means, in behaviours before numbers

The user can, without noticing Friday: keep the desktop drawn on every attached display; scroll
a browser with a video playing; hold a video call at its frame rate; type in an editor without
input lag; open a file dialog. They cannot expect to: play a GPU game, run their own training
job, or edit 4K video. That boundary is the *default* level; the levels below move it.

### 4.2 The floors

`headroom_contract(profile, level) -> Contract`, pure, in a new
`services/headroom_contract.py`. Three levels. Values are PROPOSED and the defaults are D1.

| Resource | Floor, `working` (user present) | Floor, `away` (idle ≥ `work_queue.away_after_s()`) | Floor, `yield` (user pressed "I need my machine") | Measured by |
|---|---|---|---|---|
| **VRAM, display** | live display reserve: `refresh_display_reserve` sample, clamped to `MIN_DISPLAY_RESERVE_MIB` (2,560 Windows), plus `display_reserve_mib()`'s per-monitor increments | same — **the screen must draw when they come back** | same | `hardware_profile` (exists) |
| **VRAM, slack** | **1,024 MiB** free above the display reserve, never planned into | **512 MiB** | 1,024 and every lease released at the next boundary | `nvidia-smi memory.free` (exists, `gpu_headroom.gpu_memory`) |
| **RAM, available** | live `available_mib` after the projected load ≥ **4,096 MiB** | ≥ **2,048 MiB** | ≥ 4,096 and leases released | `psutil.virtual_memory().available` (exists in `detect_ram`) |
| **RAM, ceiling** | R2 unchanged: resident host portion + OS reserve ≤ 75 % of total | unchanged | unchanged | exists |
| **Disk, system volume** | free ≥ **10 GiB** on the volume holding `%SystemRoot%` (the pagefile, `~/.friday`, logs), sampled live, *in addition to* R8 on the store volume | same | same | `shutil.disk_usage(Path(os.environ["SystemRoot"]).anchor)` |
| **Responsiveness** | no *thrash* signature (§4.3) sustained > 10 s while Friday holds a lease | same, but the response is to log and continue unless the display floor is also breached | same as working | new sampler |

Why these shapes and not others:

- **Display reserve is not slack.** The desktop *uses* the reserve; nothing may use the slack.
  Collapsing them is how 322 MiB free came to be a state the Arbiter could reach.
- **512 MiB is the away floor, not zero,** because of 3.2: the thrash margin this week was 354.
  Below ~500 MiB the card is one allocation from the failure it is meant to prevent, whether or
  not anyone is watching.
- **RAM is checked on `available`, not `total`.** §2.3. The OS reserve stays because it is a
  different claim (what Windows needs to not page *itself*); the available floor is what the
  *user's* processes need to not be paged by Friday.
- **Disk on the system volume is unconditional** because §2.4's incident had nothing to do
  with models.
- **The `yield` level exists so the user has a button** (§8.1), and so the chain runner has a
  target state to reach at the next stage boundary rather than an interrupt to improvise.

### 4.3 The monitor

`services/machine_monitor.py`, PROPOSED. A sampler, not a decider — the same rule
`gpu_headroom` states about itself: **it only ever reports.**

- `sample() -> Sample`: `{ts, gpus:[{index, free_mib, used_mib, util_pct, power_w, power_limit_w,
  sm_mhz}], ram_available_mib, disk_system_free_mib, foreign_vram_mib, wddm_shared_mib | None}`.
  One `nvidia-smi` call for the GPU fields (extends the query string in
  `gpu_headroom.gpu_memory`); `psutil` for RAM; `shutil.disk_usage` for disk;
  `_foreign_occupancy_mib` promoted from fallback to a first-class field; the WDDM
  `Shared Usage` counter read through the same PowerShell path as `live_display_mib`, `None` when
  it cannot be read — **UNKNOWN** whether that counter is reliable enough to act on; until the
  check in §12 Phase 1 settles it, it is displayed and never gates.
- `verdict(sample, contract) -> Verdict`: per resource, `ok | at_risk | breached` with the two
  numbers, in the refusal shape `residency_policy._refusal` already uses, so the panel's
  `_humanise_refusal` renders it with no new vocabulary.
- **Thrash signature** (PROPOSED, to be validated against the reported run before it gates
  anything): `util_pct ≥ 90 and power_w ≤ 0.4 × power_limit_w` on ≥ 3 consecutive samples at a
  5 s cadence, **or** a served seat's measured ms/token ≥ 5× its catalog `probe_ms_per_token`
  baseline over the last three calls. Either alone is `at_risk`; both is `breached`. The power
  ratio and the 5× are judgements, and the fixture in §12 Phase 1 is how they get corrected
  without re-arguing them.
- Cadence: every **60 s** at rest (the Arbiter already samples the display reserve on that
  cadence, `hardware_profile.py:374–379`), every **5 s** while a lease is held or a chain is
  running. The rate-capped rejection log pattern (`_log_rejection`) applies to every repeated
  verdict.
- Published at `GET /api/machine` (PROPOSED) and folded into `/api/intelligence`'s `machine`
  block so THE MACHINE reads one source (§8.3).

### 4.4 How the contract survives being wrong on someone else's hardware

This is the requirement in the brief, and it is met by four properties rather than by better
numbers:

1. **Every floor is live-sampled or measured on the machine it governs.** Nothing in §4.2 is a
   P1 figure carried across. The display reserve is sampled; RAM is `available`; disk is the
   real volume; the slack is a policy, not a measurement.
2. **The direction of error is chosen.** Every clamp rounds toward *the user's machine*: a
   reading that cannot be taken is `None` and reads as "cannot verify," never as "plenty"
   (`gpu_headroom.gpu_memory` docstring, kept as HR1). An impossible reading is discarded loudly,
   never scaled (`refresh_display_reserve`, kept).
3. **Friday yields; she never fights.** A breach makes Friday smaller, never anything else
   smaller (§7, HR7). A wrong floor therefore costs a seat, not a screen.
4. **Unknown never fits.** A model with no footprint is `unknown`, and a chain may not plan a
   stage into `unknown` (HR1). This is the 2026-08-18 "coerced to 0 MiB" bug
   (`model-suite-determination` memory) written as a rule.

**INFERRED:** with these four, a machine on which every constant is wrong still ends in a state
where Friday has refused or yielded too early, which is recoverable by the user in Settings,
rather than in a state where the display driver has reset.

---

## 5. Footprints — what a model costs, for every modality

### 5.1 The record

PROPOSED extension to `CatalogEntry` (`residency-policy.md` §2.2), one `footprint` per
`(model_id, profile_fingerprint)`, living in `SEED_MEASUREMENTS` for the reference instance and
in `runtime_dir()/residency/measurements.json` for everyone else, through the existing
`record_measurement`:

```
Footprint
  modality        text | embed | image | video | stt | tts
  device          gpu | cpu
  vram_mib        int | None        # UNDER LOAD, mid-job, not idle (HR6)
  host_ram_mib    int | None        # RSS delta across load, for CPU services
  artifact_bytes  int
  load_s          float | None      # cold start, measured or est_load_s with basis
  unit            token | image | second_video | second_audio
  work_s_per_unit float | None      # ms/token, s/image at a stated size and steps, RTF
  requires        {ram_min_mib, ram_recommended_mib, vram_min_mib, compute_class} | None
  licence         {name, note, url} | None
  quality_note    str | None        # "Q4 quant; visibly degraded on text rendering"
  basis           measured | derived | declared | unknown
  measured_at     iso8601 | None
```

`requires` is where a vendor's "recommended 64 GB RAM" lives, marked `declared`, so §5.2's
verdict can cite it without believing it (§10.5).

### 5.2 Three axes, five verdicts, no "compatible"

`verdicts(entry, profile, contract) -> {fits, runs_well, worth_it}`, pure, PROPOSED in
`residency_policy`:

| Axis | Question | Decided from | Verdict values |
|---|---|---|---|
| **fits** | Can it be placed under the contract at all? | R2, R3, R8, the contract floors, `vram_mib` | `ready` · `ready-but` (fits only if named seats stand down) · `refused` (rule + arithmetic) · `unknown` (no footprint) |
| **runs well** | Will the machine stay usable and will it finish in reasonable time? | `host_ram_mib` vs `available`; `requires.ram_recommended`; `work_s_per_unit`; the thrash history for this model on this profile | `ready` · `degraded` (reason: "recommended 64 GB RAM, this machine has 32") · `unknown` |
| **worth it** | Is the output something the user wants? | `quality_note`; `licence`; quantisation | `ready` · `ready-but` (licence or quality named) · `unknown` |

Rules that make this honest rather than decorative:

- **HR1** — a verdict without a `basis` is not a verdict. `unknown` renders as "not measured on
  this machine" with the action that would measure it, never as a green tick.
- **HR2** — no surface renders a single combined "compatible / incompatible". Three axes, or
  nothing. The one-word summary is permitted only as the *worst* of the three, with the axis
  named ("Degraded — RAM").
- **HR16** — `licence` is shown and never enforced. Stephen's standing rule: build the dial, he
  points it (`feedback_content_policy_is_stephens_to_direct`). FLUX's output restriction is
  a `ready-but`, worded from the licence text, with the URL.
- A `degraded` on *runs well* still offers the fetch, with the reason on the button. The user
  may want a slow LTX. What they may not get is a green tick and a surprise (D4).

### 5.3 Where the numbers come from, per modality

| Modality | Measurement job | Who runs it | Register |
|---|---|---|---|
| Text | Exists: daemon `/api/ps` at stated `num_ctx`, plus **a load sample** — the arbiter records `nvidia-smi` `memory.used` delta mid-generation on first use of each seat (the 1,229 vs 9,652 lesson) | Arbiter, automatically, on first use | PROPOSED extension |
| Image | Under `image_job`: `nvidia-smi` sample at the render's midpoint, ComfyUI start wall-clock, render wall-clock at 1024², steps as configured; per model in `local_image.MODELS` | **Requires the GPU. Not the Sonnet session's to run** — recorded by Stephen or a GPU-permitted session, then committed to `SEED_MEASUREMENTS` with the workflow named. Until then the image seat is `unknown` and the surface says so | PROPOSED |
| Video | No local backend; no row. A candidate (LTX, Wan) gets a `declared` row with `requires` from its card only when a backend exists to serve it. Until then the chain's video stage is cloud, and the surface says "video runs in the cloud on every machine today" | — | PROPOSED; §14.2 |
| STT / TTS | `host_ram_mib` from the server's RSS delta across `WhisperASR.load()` / `PiperTTS.load()`; RTF already measured; NeMo tier declared `vram_min_mib: 4096` from `MIN_VRAM_GB` | Sonnet session can measure host RAM on CPU without a GPU | PROPOSED |
| Embed | Exists (`qwen3-embedding:0.6b` 2,029 MiB); `all-MiniLM-L6-v2` 90 MB declared (`model_plan.EMBEDDER`) | — | VERIFIED |

**HR6** — an idle reading is never written as a footprint. The `measured_at` field is written
only by a job that ran the model.

---

## 6. Chains — planning the sequence Stephen described

### 6.1 The shape

A chain is an ordered list of stages, each naming a **role** from `residency_policy.ROLES` (or
`video`, added) and a unit of work: `[{role: "stt"}, {role: "interactive_brain"},
{role: "image", units: 1}, {role: "tts"}]`. `workflow_plan.build()` already produces a proposal
with tasks and classes; a chain is a proposal whose tasks have roles. Nothing new is invented
for where chains come from.

### 6.2 The planner

`plan_chain(profile, entries, stages, contract, resident) -> ChainPlan`, pure, PROPOSED in
`residency_policy`, beside `plan()` and sharing its budgets:

```
ChainPlan
  stages        [ {role, model_id, where: resident|leased|cpu|cloud|refused,
                   footprint_mib, basis, est_work_s, refusal|None} ]
  transitions   [ {before_stage: i, evict: [model_id], load: [model_id],
                   est_s, basis} ]
  retained      [model_id]        # what keeps answering throughout (R10 and §6.3)
  peak_mib      int | None        # the largest single-stage footprint; None if any unknown
  total_est_s   float | None
  contract_ok   bool | None       # every stage's peak under the contract; None if unknown
  alternatives  [ChainPlan]       # same stages with one stage moved to cloud, or when_away
```

Rules:

1. **One lease at a time.** The Arbiter's serial lock is not relaxed. A chain is executed as
   `grant → run stage → release` per leased stage, with resident and CPU stages between them
   needing nothing. This keeps every existing invariant.
2. **Reload versus refuse is decided by number, and reload usually wins.** Refuse only when a
   stage cannot fit under the contract even with everything else evicted. Otherwise the plan
   carries the reload cost in `transitions` — from `est_load_s` and the measured grant/release
   figures — and the *user* decides between local-with-reloads, cloud-for-that-stage, and
   when-away, from the same three-way card that exists. The threshold at which Friday *asks*
   rather than runs is the existing `workflow_plan.ASK_ABOVE_S = 60`; a chain whose transitions
   sum above it is a question, never a default.
3. **No stage plans into `unknown`.** HR1. On P1 today that means the image stage is a question
   until §5.3's measurement lands.
4. **The retained set is a stage property.** R10 retains the sidekick through every lease. A
   chain adds: **the seat a live voice session is bound to is retained through every stage**, so
   the failure in §2.6 step 4 — voice refused mid-chain — cannot happen. On a card where the
   brain cannot be retained beside the image model, the retained seat *is* the sidekick, and
   the plan says "answered by the small model while the picture renders."
5. **Vault stages cannot move to cloud.** `_route_vault` forces local (`workflow_plan.py`
   docstring, VERIFIED). A chain whose brain stage reads vault-tier material offers no cloud
   alternative for that stage and says why before it starts. A cloud alternative for a *later*
   stage is still offered, because the image prompt is not the vault (the composites-inherit
   rule from `project_news_egress_provenance` applies: if the prompt was derived from vault
   material, the stage inherits the restriction — the planner asks the egress gate, it does not
   decide).

### 6.3 The fixtures, worked

Numbers are P1 measurements carried across as `residency-policy.md` §5 does; image is
**UNKNOWN** and shown as `?` so the tables do not lie. Available VRAM uses the planner's own
arithmetic with a 2,560 Windows display reserve and the 1,024 R3 reserve; the live figure will
differ and the planner's is authoritative.

**P1 — RTX 4070 12,282 / 32 GB DDR4 (Stephen).** Available ≈ 12,282 − 1,024 − 2,560 =
**8,698 MiB**. `gemma4:12b` at 32k (7,718) + `gemma4:e2b` (1,811) = 9,529 **does not fit under
an honest reserve** (MEASURED, `model-suite-determination` memory, 2026-08-18). The honest
resident pair is `e4b` (3,081) + `e2b` (1,811) = 4,892, leaving 3,806.

| Stage | Where | Retained | Transition before | Basis |
|---|---|---|---|---|
| stt | cpu | — | — | measured RTF |
| brain (`e4b`) | resident | e2b | — | measured |
| image (Z-Image) | leased, exclusive of e4b | **e2b** — voice keeps answering | evict e4b; ComfyUI start 93 s warm / ~180 s cold | timings measured; **VRAM unknown** |
| tts | cpu | — | reload e4b ~27.5 s | measured `cold_load_s` |

Total wall-clock ≈ **3–5 min** for one spoken request that produces one picture, with the screen
and browser inside the contract throughout and Friday answering on `e2b`. **INFERRED.** The
alternative the card shows: image in the cloud, ~30 s, brain never stands down.

**P2 — 8 GB laptop / 16 GB RAM.** Available ≈ 8,188 − 1,024 − 2,560 = **4,604**. `e4b` (3,081)
*or* `e2b` (1,811), not both. Image: **refused** locally under the contract unless a measured
Z-Image footprint proves otherwise (INFERRED: unlikely at ~4.6 GB). Chain: stt → `e4b` → **cloud
image** → tts. Honest and complete; nothing stands down.

**P3 — 24 GB / 64 GB.** Available ≈ 22,752. `12b` + `e2b` + embedder = 11,558 resident. If
Z-Image measures near the "~8,000" figure, the image stage **fits beside the brain** with no
eviction — which is a change to **R5** ("image takes an exclusive lease"): under this document
the lease is exclusive *only when the budget says so*, the same way R6 decides pin-versus-lease
from the numbers rather than from the model. Stephen accepted R5 as written on 2026-08-14; the
relaxation is **D7**.

**P4 — 24 + 12 dual.** Image on `gpu:1`, everything else keeps serving (R5 already). The chain
has no transitions at all. The only fixture where Stephen's sentence runs as he imagines it.

**P5 — CPU-only 32 GB.** stt/tts cpu; brain `e2b` on CPU (slow, DDR4-bound); image **refused**
(R5, no GPU) → cloud. The RAM contract governs: 32,768 − 6,144 OS − 4,096 available floor.

**P6 — unified 64 GB.** Every seat refused `backend_unavailable` until a backend exists
(unchanged). The chain is entirely cloud and says so.

### 6.4 Both paths, always

Every `ChainPlan` carries `alternatives`: the same chain with each non-vault stage moved to the
cloud, and the whole chain as `when_away`. A stage that is `refused` locally is rendered with
its cloud alternative *selected*, not merely available, because a refusal with no next step is
the thing Stephen's principle forbids. Cloud availability is checked the way
`work_plan._cloud_available()` does today — a key must exist — so the card never offers a
choice that cannot be honoured (HR8).

### 6.5 Execution

`Arbiter.run_chain(plan, on_stage)` (PROPOSED): for each stage, re-check the contract
(§4.3 sample) at the boundary; if breached, stop *before* the stage, release, and raise the
three-way through the existing proposal path (`workflow_plan.decide`) with the stage named;
otherwise grant if the stage is leased, run, release. Cancellation reuses `local_image`'s
job-flag pattern (a flag readable from the first line to the last, set by the route, checked
at every stage). Every stage's actual footprint and wall-clock are recorded through
`record_measurement` — a chain is the cheapest measurement job there is (HR10).

---

## 7. When the user opens something heavy mid-workflow

**The principle:** Friday is the guest on this machine. When headroom goes, Friday gets smaller.
Nothing else does. **HR7 — Friday never terminates, evicts, or throttles a process that is not
hers**, extending `gpu_headroom`'s "only ever REPORTS" to the whole layer.

The monitor (§4.3) samples every 5 s during any lease or chain. The response is a ladder,
chosen by what is breached and what Friday is doing:

| State | Friday is… | Response |
|---|---|---|
| `at_risk` (slack eaten, display reserve intact) | anything | Log; show amber on THE MACHINE and the orb; continue. Next stage boundary re-checks. |
| `breached` — VRAM slack gone, display reserve still intact | between stages | Do not start the next leased stage. Release. Raise the three-way with the reason: "Something else is using the graphics card — a browser or a game, most likely. Finish this in the cloud, wait until it is free, or stop?" |
| `breached` — VRAM slack gone | inside a resident-seat turn | Finish the turn (seconds). Then as above. |
| `breached` — VRAM slack gone | inside an image render | **D3.** Default proposed: notify and continue, because a render is bounded (~93 s) and cancelling loses it; cancel *only* if the display reserve itself is breached. |
| **display reserve breached** | anything | Cancel any in-flight render (`local_image.request_cancel`), release every lease, evict leased seats. Keep the retained sidekick only if it still fits inside the reserve; otherwise it goes too. Say so, with the number. This is the 2026-08-17 monitor incident, and a lost picture is cheaper than a lost display. |
| RAM available floor breached | anything | Same as VRAM slack, on the RAM row. Additionally refuse any new CPU service load (whisper, NeMo) until it clears. |
| Disk system-volume floor breached | anything | Refuse every load and every fetch; release leases at the boundary (the pagefile shrinks when the resident set does — `phase-a-report` A7); surface a *problem* on THE MACHINE naming the volume. |
| thrash signature `breached` | holding a lease | Treat as VRAM slack breached. Also mark the model's footprint on this profile `degraded` with the sample attached, so `runs_well` reflects it next time (§5.2). |

**The `yield` button** (§8.1) is the user asserting the last row's response without waiting
for a breach: release at the next boundary, contract level `yield` until they say otherwise.

**What is not done:** no "nice" priority games with the GPU scheduler, no CUDA MPS, no
per-process VRAM limits. WDDM does not expose them to a user-mode process in any form the tree
could use, and pretending otherwise is the invisible-success class again.

---

## 8. What Friday tells them

### 8.1 Before, during, after

**Before any fetch, lease, or chain** — one card, the shape of the existing forecast card, with
five lines the user can read in the time it takes to decide:

1. **What** — "Make an image with Z-Image (on this machine)."
2. **Where** — which models, which device, from the `ChainPlan`.
3. **What stands down** — "Your main model steps aside for ~3 minutes. Friday keeps answering
   on the small model." Or, for a fetch: "Nothing changes until you choose to use it."
4. **How long** — `total_est_s` with its basis and confidence, in `pause_forecast`'s words.
5. **What the machine will feel like** — the contract verdict in a sentence: "Your screen and
   browser keep their memory. 1.0 GB of headroom stays free." Or the amber version.

Then the three-way. This extends `pause_forecast` with `before_chain(plan)` and reuses the
chat-path card (`index.html:40729`). **HR9:** the forecast is served *before* the action and the
action does not start until it is answered or the setting `pause_warnings_off` is on — which
already exists and already means "the user has chosen not to be asked."

**During** — the orb per stage (exists) gains the machine state as a small second line:
"headroom 1.2 GB · ok" / "headroom 0.4 GB · tight". THE MACHINE shows the same. A **"I need my
machine"** button on the orb and on THE MACHINE sets contract level `yield` (§7).

**After** — the receipt: what ran where, estimated versus measured per stage, what it recorded
into the catalog. This is the `completion_receipts` pattern and the `work_queue.drain` rule that
a saving claimed is a saving measured.

### 8.2 The Settings surface — R1

In **Settings → Intelligence**, below THE MACHINE, a new section **LOCAL MODELS ON THIS
MACHINE**. It is not a new tab, because Stephen's `65f70ce` fixed a round of links to tabs that
did not exist and the Intelligence tab is where the machine already lives.

One row per model Friday knows how to run locally, whether or not it is installed — text from
`model_plan.BRAIN_MODELS`, image from `local_image.MODELS`, voice from the two engines, embed
from the catalog. Each row:

| Column | Content | Source |
|---|---|---|
| Model | canonical id, label, size on disk | catalog |
| Installed | yes / no / partial (files missing, named) | `model_store`, `local_image.is_installed` |
| **Fits** | verdict + basis | §5.2 |
| **Runs well** | verdict + reason | §5.2 |
| **Worth it** | verdict + licence / quality note | §5.2 |
| Action | **Fetch** · **Remove** · **Measure** (runs the §5.3 job) · **Use for …** (binds a seat via the existing picker) | — |

**Fetch** opens the pre-fetch card (§8.1's five lines, with line 3 reading "disk after: N GB;
system volume after: M GB") and a single confirm. The fetch itself goes through the existing
paths — `/api/ollama/pull` for Ollama artifacts, `model_store` for direct downloads — and reports
done only when `model_setup`'s rule is satisfied: the store lists it. Then the row's *Measure*
action becomes the next thing to do, and the row says so, because a fetched model is `unknown`
until measured (HR1, HR10).

No row renders the word "compatible". A row with all three axes `unknown` reads: *"Not measured
on this machine. Fetch it and Friday will measure it the first time it runs."*

**Video** has no rows and one sentence: *"Video runs in the cloud on every machine today."*
(§14.2, D8).

### 8.3 THE MACHINE, corrected

- The VRAM bar's reserve marker reads the **contract's display reserve**, not
  `gpu_headroom`'s 1,024 (HR3). The slack is drawn as a second, lighter band.
- A third bar: **system disk**, with the 10 GiB floor.
- A fourth line: **contract level** — `working` / `away` / `yield` — with the idle timer from
  `work_queue.idle_seconds()` and the yield button.
- The `problems / choices / notes` groups gain the monitor's verdicts, humanised by the
  existing `_humanise_refusal` with two new rule ids (`H-VRAM-SLACK`, `H-RAM-AVAIL`,
  `H-DISK-SYS`, `H-THRASH`), each with a *why* and an *action* or an admitted gap, per that
  function's own rule.

### 8.4 The guard against a control that looks informative and is not

This codebase has a static verifier for seats — `role_consumers.verify()` proves a named module
reads the routing key it claims to (**VERIFIED**, `role_consumers.py:262–330`) — and a liveness
audit that asks RAN / PRODUCED / CONSUMED of subsystems (`liveness_audit.py`). The same two
instruments apply here, and §12 requires both:

- **Static:** a test walks every verdict the surface can render and asserts it carries a
  `basis` in `{measured, derived, declared, unknown}` and, when `measured`, a `measured_at`. A
  verdict literal in `index.html` with no basis field fails the test — the same shape as
  `test_no_shipped_default_names_a_model_that_cannot_call_tools`.
- **Liveness:** `liveness_audit` gains a `headroom` entry: RAN (a sample in the last two
  minutes), PRODUCED (the sample has a non-`None` GPU row when a GPU exists), CONSUMED (the
  Arbiter's last chain boundary check cites a sample id). A monitor that samples and nothing
  reads is `ORPHANED`, loudly.

---

## 9. Onboarding — the starting set

The Hardware Check (wizard step 4) and the installer both propose models today, from two
different ladders, one of them stale (§2.9). The onboarding interview spec asks two things of
the hardware side: D5 (how much of the interview runs with no local model) and Q-V9 (whether
the wizard should pull a model on capable hardware). This section supplies the mechanism; the
flow stays theirs.

**PROPOSED:** `/api/health/full`'s `hardware` block gains `starter_set`, computed by one call
chain — `hardware_profile.get()` → `model_plan.plan()` for the brain → `plan_chain()` for the
interview's own chain `[stt, interactive_brain, tts]` and, separately, `[…, image]` — and
returns:

```
starter_set
  brain     {model_id, verdicts, download_gib, why}       # from model_plan tiers
  voice     {stt, tts, where: cpu, host_ram_mib, verdicts}
  image     {model_id, verdicts, or refused → "cloud"}
  video     "cloud"
  chain     ChainPlan for the interview   # so D5 can be answered from data
  contract  the default level and its floors on this machine
  floor_model  model_plan.FLOOR_MODEL     # replaces the literal at index.html:30137
```

The wizard renders the same three-axis rows as §8.2, pre-selected to the starter set, with the
copy rules from `vault-first-onboarding.md` §7 (no shaming of the cloud choice; the entry rung
needs roughly a 6.5 GiB card and most laptops do not have one). `WizardGemmaPull` reads
`floor_model` and the family-prefix match at `:30149` becomes an exact-tag match, per the
`_resolves` rule in `model_setup`.

The installer's `$brainLadder` is **generated** from `model_plan.BRAIN_MODELS` by a script at
release time, with a test that diffs the two (HR14). The installer cannot import Python before
the venv exists, which is why it carries a copy; the copy is fine, the *hand-maintained* copy is
the defect.

**What this gives the interview:** D5 becomes answerable per machine — the `chain` field says
whether `[stt, brain, tts]` runs locally, and if the brain stage is `cloud` the interview knows
before its first beat. That is the V3 precondition in that spec's §16.2, computed rather than
discovered mid-sentence.

---

## 10. STORM — the disagreement, argued at strength

### 10.1 The kernel engineer: "you cannot measure thrash from user mode on WDDM"

*Utilisation-at-low-power is a heuristic. A memory-bound but healthy kernel looks identical for
a moment. `Shared Usage` counts commitments, not residency — the same counter class that
reported 26 GB on a 12 GB card. The DxgKrnl residency events are not documented for this use.
You are about to build a detector on three proxies and call it measurement.*

Conceded, in full, and the design already reflects it: the signature must be *sustained*
(§4.3), it pairs with a throughput measurement Friday can make honestly on her own seats, the
WDDM counter is displayed and never gates until validated, and a `breached` thrash verdict
acts only as VRAM-slack-breached — which is a state the contract would have reached anyway.
What the proxies buy is *earlier* notice, not a new authority. The fixture in §12 Phase 1 is
built from the reported run precisely so the thresholds are corrected by data rather than
defended by argument.

### 10.2 The product person: "three verdicts is a spreadsheet; people want a button"

*Nobody reads three columns. Give them Install and a green tick. You are designing for the
one user who lost a monitor.*

Partly conceded. The row's *summary* is one word — the worst axis, with the axis named — and
the fetch is one button. What is refused is the green tick with nothing behind it, because
this codebase shipped several and a guard now exists to catch them (§8.4). And the one user who
lost a monitor is the one whose machine every other user's resembles more than it resembles a
datacentre: single card, shared with the desktop, in use all day. A control that reads
"compatible" and is wrong costs that user their evening; a control that reads "Degraded — RAM"
costs them one line of reading.

### 10.3 The privacy engineer: "a chain that moves one stage to the cloud is an egress with a nicer name"

*You will render an image in the cloud from a prompt the local brain wrote out of vault
material, and call it "both paths always available."*

Right, and §6.2 rule 5 is the answer: the stage inherits the restriction of what it was derived
from, decided by the egress gate's provenance rule, not by the planner. A vault-derived image
prompt has no cloud alternative, the card says so, and the choice is local-or-not. The stricter
position — no cloud substitution inside any chain that touched the vault at any stage — is
available as a setting and is **D2**. The looser position — that a prompt is not the vault —
is the one `_route_vault` already takes for text and is not extended by this document.

### 10.4 The new user on the 8 GB laptop: "everything you show me is refused"

*Brain: one small model. Image: refused. Video: cloud. Voice: fine. Your Settings page is a list
of things my computer cannot do.*

This is the argument that shaped §6.4 and §8.2 most. A refusal is rendered with its cloud
alternative *selected*, so the row reads as a working choice, not an apology. The starter set
on P2 is a complete product: local voice, a local brain, cloud pictures — and the copy says
"pictures run in the cloud on this machine" as a fact about the machine, in the register
`vault-first-onboarding.md` §7 requires. What is not done is hiding the rows, because the user
who buys a bigger card next year should see the same page change under them.

### 10.5 The adversary: "the model card lies, and you just built a UI that repeats it"

*`requires.ram_recommended` comes from a README. Vendors round down for adoption and up for
liability. A quant's quality note is somebody's opinion. Your "worth it" column is marketing
with a basis field.*

Conceded, and it is why the field is marked `declared` and rendered as "the model's own
guidance says…", never as Friday's finding. The only way `runs_well` becomes `measured` is a
run on this machine (§5.3, §6.5). The adversary's real point is sharper: *a declared number
should never gate.* It does not — a `declared` requirement produces `degraded`, which still
offers the fetch (§5.2), and a `measured` thrash produces `degraded` with the sample attached.
Only measured numbers refuse.

### 10.6 The user, on the default: "why is my brain smaller now?"

*On my own card you have just told me the resident model is `e4b`, not the `12b` I have been
running for a month.*

Because `12b` + a sidekick does not fit under a display reserve honest enough to keep the second
monitor alive (§6.3, MEASURED 2026-08-18), and this document's premise is that the machine
comes first. But this is exactly the trade that is not the author's to make. It is **D1** and
**D6**, stated plainly rather than buried in a budget: run the 12b alone with no sidekick, run
e4b + e2b with headroom, or run the 12b and accept a smaller reserve than this document
recommends. The planner will place any of the three and say what each costs.

### 10.7 Synthesis

The disagreements resolve into four commitments the rest of the document already carries:
measured numbers gate, declared numbers inform, unknown numbers never fit; Friday yields and
never fights; every refusal is rendered as a working alternative; and the trade between
Friday's quality and the machine's headroom is Stephen's, made visible, not the planner's,
made quietly.

---

## 11. Guard rules

| # | Rule | Enforced by |
|---|---|---|
| **HR1** | A verdict without a basis is not a verdict. `unknown` never renders as fit, and no chain plans a stage into `unknown`. | static test §8.4; `plan_chain` |
| **HR2** | No surface renders "compatible". Three axes, or the worst axis named. | static test §8.4 |
| **HR3** | One reserve. `headroom_contract()` is the only definition; `gpu_headroom`, `residency_policy.VRAM_RESERVE_MIB`, `model_plan.DISPLAY_RESERVE_GIB`, `display_reserve_mib()` read from it. A test asserts no module defines a reserve constant of its own. | test, the `FLOOR_MODEL` sweep pattern |
| **HR4** | RAM is admitted on live `available`, never on `total` alone. | `check_ram_headroom` |
| **HR5** | The system volume is watched regardless of where models live. | monitor |
| **HR6** | An idle reading is never written as a footprint. `measured_at` is set only by a job that ran the model. | `record_measurement` |
| **HR7** | Friday never terminates, evicts, or throttles a process that is not hers. | code review; no such call exists in the layer |
| **HR8** | A chain never substitutes cloud silently. Vault-derived stages have no cloud alternative and say so before start. A cloud alternative is offered only when a key exists. | `plan_chain`, `_cloud_available` |
| **HR9** | The forecast is served before the action, and the action waits for the answer unless `pause_warnings_off`. | route ordering, chat path |
| **HR10** | A fetch reports success only when the store lists the artifact; a chain records what it measured. | `model_setup`, `run_chain` |
| **HR11** | Every estimate carries basis and confidence. | `pause_forecast` shape |
| **HR12** | Every refusal names its rule and its arithmetic. | `_refusal` |
| **HR13** | The planner stays pure; sampling stays in the Arbiter and the monitor. Golden fixtures move only with a committed diff. | `tests/golden/residency` |
| **HR14** | The installer's ladder is generated from `model_plan.BRAIN_MODELS`; a test fails on drift. | release script + test |
| **HR15** | Any surface proposing a starting set reads the planner, not `recommend_models`. `recommend_models` is deleted or made a thin reader. | test |
| **HR16** | Licence is shown, never enforced. | surface |
| **HR17** | An in-flight render is cancelled automatically only when the display reserve is breached (D3 may tighten this). Everything else is a question at a boundary. | `run_chain` |
| **HR18** | A `declared` number never refuses. Only `measured` numbers refuse. | `verdicts` |

---

## 12. Phasing — the handover to the Sonnet 5 session

Each phase lists files, tests, and the acceptance check. **The session has no GPU and must not
fabricate a measurement**: anything unmeasured stays `unknown` and the surface says so. Items
marked **[Stephen / GPU]** are recorded by him or by a session with GPU permission, then
committed. Work in a worktree off `integration/release-2026-09-03`; do not touch
`gauntlet-audit-2026-09-03`. UI edits go through `scripts/ui_stage.py` (`index.html` is the
served source of truth, **VERIFIED**, `ui_stage.py:SERVED`); whether `ui_parts/app.html` must
mirror a given edit is **UNKNOWN** to this document — check `git log -5 -- ui_parts/app.html`
and follow the pattern the Workflows dock used (memory: it edits both).

### Phase 0 — the drifts (small, test-backed, no design risk)

1. `index.html:30137` — `BUNDLED_MODEL` read from `/api/health/full` → `hardware.floor_model`
   (add the field to `routes/platform.py:763` from `model_plan.FLOOR_MODEL`); `:30149` exact-tag
   match. Test: a grep-style test in `tests/unit/` that fails if `gemma3:4b` appears as a literal
   in the wizard block of `index.html` (the `test_higgsfield_catalog` phantom pattern).
2. `scripts/gen_installer_ladder.py` writes the `$brainLadder` block of `install.ps1` from
   `model_plan.BRAIN_MODELS`; `tests/unit/test_installer_ladder_matches_plan.py` parses the
   block and diffs. `$localIsComfortable` compares against `FLOOR_MODEL`'s id.
3. `services/headroom_contract.py` with `contract(profile, level)`; the five reserve sites in
   §2.2 read from it; `intelligence.py:587` reports the contract's display reserve and slack.
   Regenerate goldens with `tests/golden/residency/_generate.py` and **review the diff in the
   commit message** — P1's plan will move if the effective reserve changes. HR3's sweep test.
   Acceptance: `pytest tests/unit/test_residency_policy.py tests/unit/test_hardware_profile.py
   tests/unit/test_model_plan.py` green; `df -h /c` checked first (memory: the suite leaks temp
   homes and a full disk looks like a code bug).

### Phase 1 — the contract and the monitor

1. `services/machine_monitor.py`: `sample()`, `verdict()`, the thrash signature, cadence,
   `_log_rejection`-style rate capping. `nvidia-smi` query extended in one place;
   `gpu_headroom.gpu_memory` becomes a reader of the monitor's last sample.
2. `GET /api/machine`; `/api/intelligence` `machine` block reads the same sample.
3. Arbiter: sample at boot, every 60 s at rest, every 5 s under lease; `grant()`'s
   R-DISPLAY-RESERVE check reads the contract (closes §2.2's 256 MiB hole).
4. Tests: `tests/unit/test_machine_monitor.py` with recorded-sample fixtures, including one
   **labelled REPORTED** built from §3's figures (11,928/12,282 used, util 100, power 51/200)
   asserting `thrash: breached` after three samples, and one healthy-load fixture asserting it
   does not fire. The WDDM `Shared Usage` read is behind a flag and its fixture asserts it is
   *displayed only*. Acceptance: the monitor's liveness entry (§8.4) reports RAN and PRODUCED on
   a machine with a GPU, and `CONSUMED` once Phase 3 lands.

### Phase 2 — footprints

1. `residency_catalog`: `Footprint` record, `footprint(model_id, profile)`, `verdicts()` in
   `residency_policy` with HR1/HR18. `SEED_MEASUREMENTS` gains the shape; text rows are
   migrated, not retyped.
2. Voice host RAM: measure `WhisperASR.load()` / `PiperTTS.load()` RSS delta on CPU; record as
   `measured` for the CPU-only fingerprint the session is on, `derived` elsewhere. NeMo row
   `declared` from `MIN_VRAM_GB`.
3. Image: **[Stephen / GPU]** — one render per model in `local_image.MODELS` under the Arbiter
   with `nvidia-smi` sampled at the midpoint; ComfyUI start and render wall-clock. Until
   recorded, both rows are `unknown` and §8.2 says so. The session writes the measurement job
   (`friday measure <model_id>`) and the test that the job refuses to write an idle reading (HR6).
4. Licence: generalise `local_image.MODELS[...]["licence"]` into the record; Z-Image's licence
   is **UNKNOWN** to this document — the session records it from the model's own repository or
   leaves the field `None` with the row reading "licence not recorded".
5. Tests: `test_residency_catalog.py` extended; property test that `verdicts()` never returns
   `ready` on any axis whose basis is `unknown`.

### Phase 3 — the chain planner

1. `residency_policy.plan_chain()` per §6.2; `video` added to `ROLES` as a permanently refused
   seat until a backend exists (the P6 pattern).
2. `tests/residency_fixtures.py`: the four chains in §6.3 for P1–P6;
   `tests/golden/residency/chains/*.json` generated and committed; property tests: one lease at
   a time, retained set never empty when a voice session is bound, no stage into `unknown`,
   vault-derived stages carry no cloud alternative.
3. `Arbiter.run_chain()`; `pause_forecast.before_chain()`; `workflow_plan.build()` accepts
   roles; `/api/work/forecast` gains `kind: "chain"`.
4. Acceptance: the P1 chain golden shows `e2b` retained through the image stage and the image
   stage `unknown` until Phase 2.3 lands, then `leased`.

### Phase 4 — the surface

1. Settings → Intelligence: **LOCAL MODELS ON THIS MACHINE** (§8.2), the pre-fetch card,
   `GET /api/models/fetch/preflight?model=` returning the five lines; THE MACHINE corrections
   (§8.3); the yield button → `POST /api/machine/level`.
2. The chain card on the chat path reuses the forecast card with the stage list.
3. Static verdict test (§8.4). Every fetch path ends in `model_setup`'s verification.
4. Acceptance: on a machine with no GPU the section renders every GPU row as `unknown` or
   `refused` with the cloud alternative selected, and no row says "compatible" — checked by the
   static test, not by eye.

### Phase 5 — intrusion response

1. Monitor → Arbiter hooks per §7's table; the `yield` level; cancellation of an in-flight
   render only on display-reserve breach (HR17, D3).
2. Tests drive the ladder with fixtures: a sample sequence that eats the slack between stages
   produces a proposal, not a load; a sequence that breaches the display reserve mid-render
   produces `request_cancel` and `release`.

### Phase 6 — onboarding hook

1. `/api/health/full` `starter_set` per §9; wizard step 4 renders it; `recommend_models` becomes
   a reader or is deleted (HR15).
2. Hand the `chain` field to the onboarding spec's D5.

**Sequencing note.** Phases 0–2 are independent of each other and can run in parallel
worktrees. Phase 3 needs 2's record shape (not its measurements). Phase 4 needs 1 and 3. Phases
5 and 6 need 4.

---

## 13. Decisions only Stephen makes

| # | Decision | Options | This document's recommendation |
|---|---|---|---|
| **D1** | The default contract level and the slack numbers (§4.2) | `working` with 1,024 / 4,096; smaller; larger | `working` as written. It is the level at which his own second monitor survives. |
| **D2** | May a chain that touched the vault at any stage offer cloud for a later, non-derived stage? | Provenance decides (§6.2 rule 5); or no cloud anywhere in a vault-touching chain | Provenance. The stricter rule is available as a setting. |
| **D3** | Automatic cancel of an in-flight render | Only on display-reserve breach (HR17); also on VRAM-slack breach; never — always ask | HR17 as written. |
| **D4** | Are models with `degraded` on *runs well* offered for fetch at all? | Offered with the reason on the button; hidden behind "show everything"; never | Offered. Hiding is the phantom in reverse. |
| **D5** | Licence display | Shown on the row; requires an acknowledgement before fetch; nothing | Shown. An acknowledgement is a legal posture he should choose knowingly. |
| **D6** | The resident brain on his own card | `12b` alone; `e4b` + `e2b` with headroom; `12b` with a reserve below this document's | `e4b` + `e2b`. It is the only one that closes under the contract. He may not like it. |
| **D7** | Relax R5 so the image lease is exclusive only when the budget requires it (P3 runs image beside the brain) | Relax; keep R5 absolute | Relax, once an image footprint is measured. Same logic as R6. |
| **D8** | Whether video appears in the local list before a backend exists | One sentence, no rows (§8.2); a `declared` row per candidate with `requires` | One sentence. A row for a model nothing can serve is the seat-that-serves-nothing defect. |
| **D9** | Move `PORT_BASE` off 8090 in the same pass | Yes; separately | Yes, one line, because the monitor will now *see* the collision and it should not be a known one. |

**Not his to decide, named so the list is honest:** HR1, HR6, HR7, HR10 and HR13 are invariants
that follow from `KNOWN_ISSUES.md` §1 and the residency layer's existing commitments, not
settings.

---

## 14. Where the framing has a problem

### 14.1 "Hardware-specific model fetching in Settings" is the visible tenth

The sentence describes §8.2. Everything that makes §8.2 honest — a contract, a monitor,
footprints for three modalities, a chain planner — is nine tenths of the work and none of it is
a menu. Stephen's second sentence knew this ("headroom measurement is vital"); this document
takes the second sentence as the brief.

### 14.2 There are no local video models to juggle

The chain he described ends in "local image or video models." Image exists (two models, one
backend, no measured footprint). Video does not exist in the tree in any form (§2.5). The chain
planner handles video as a stage that is always cloud, and the surface says so in one sentence.
Adding a local video backend is a separate specification with its own measurement burden — LTX
and Wan-class models are exactly where "fits on paper, unusable with 32 GB of RAM" lives — and
this document declines to imply it is near.

### 14.3 The only measured machine is his

P2–P6 are declared fixtures with P1's numbers carried across. Every claim in §6.3 about another
machine is INFERRED. The design's answer is §4.4: the contract is built from live samples and
chosen error directions rather than from carried numbers, so being wrong costs a seat rather
than a screen. The honest test is a second real machine, and `vault-first-onboarding.md` §11
already asks for it.

### 14.4 Headroom and quality pull against each other, on his own card first

The honest resident set on P1 under an honest reserve is smaller than the model he has been
using (§6.3, D6). A document that specified headroom without saying that would be specifying a
control that looks informative and is not. He may decide the reserve is too generous for his own
machine; the planner will do what he says and show the number.

### 14.5 "Fits" was the wrong verb in every existing module, and they were right anyway

Every module in §2.1 asks "fits." They were correct to, because they were built after failures
to fit. This week's failure was a fit that thrashed, and the fix is not to replace those modules
but to add the second check they could not have known to make. Nothing in this document deletes
a rule.

---

## 15. Provenance

**Read, 2026-09-04, at `f000f07`:** `services/{hardware_profile, residency_catalog,
residency_policy, residency_arbiter, gpu_headroom, pause_forecast, work_queue, workflow_plan,
model_plan, model_setup, model_store, local_seats, local_image, local_voice, nemo_voice,
creative_engine, creative_policy, role_consumers, liveness_audit, seat_binding, scheduler,
local_call}.py`; `routes/{intelligence, residency, work_plan, skills, platform, voice}.py`;
`routing/ollama_manager.py`; `index.html` at the cited lines; `packaging/windows/install.ps1:318–400`;
`scripts/ui_stage.py`; `tests/residency_fixtures.py`; `tests/golden/residency/`;
`docs/design/{residency-policy, symphony-of-intelligence, vault-first-onboarding,
grow-button}.md`; `docs/audits/{residency-implementation-report, model-suite-determination,
decisions-2026-08, install-readiness-8gb-2026-08-25, phase-a-report}.md`;
`docs/contracts/roles-and-model-identity.md`; `KNOWN_ISSUES.md` §0–§1; the onboarding
interview spec at `1c22361` in the `spec-onboarding` worktree.

**Not read:** anything under `Friday-Models`; the `gauntlet-audit-2026-09-03` worktree beyond
its tip commit hash; `provider_health.inference_probe`'s body; `hardware_profile.get()`'s
refresh cadence for disk.

**UNKNOWN, with the check named, collected:**

| # | Unknown | Check |
|---|---|---|
| U1 | Z-Image and SD 3.5 resident VRAM under the Arbiter lease | one `nvidia-smi` sample mid-render, §12 Phase 2.3 |
| U2 | Whether `provider_health.inference_probe` compares against `probe_ms_per_token` | read the function |
| U3 | How often `profile.disk.free_mib` is refreshed | read `hardware_profile.get()` / `detect_disk(prior=)` |
| U4 | Reliability of the WDDM `Shared Usage` counter as a paging tell | log it beside the thrash signature for a week; correlate |
| U5 | Whether DxgKrnl residency-failure events are readable via `Get-WinEvent` | try it on P1 during a known-thrash run |
| U6 | Host RAM of whisper `small` int8 and Piper | RSS delta across `load()`, §12 Phase 2.2 |
| U7 | Z-Image's licence | the model's own repository |
| U8 | Whether `ui_parts/app.html` must mirror Intelligence-tab edits | `git log -5 -- ui_parts/app.html` |
| U9 | The thrash thresholds (90 % / 0.4 / 5×) | the REPORTED fixture plus one healthy-load fixture, §12 Phase 1.4 |

**REPORTED (from the brief, not in the tree):** 11,928 / 12,282 MiB; ~7 s → ~57 s; 51 W of
200 W at 100 % utilisation; kernel residency failures; 354 MiB.
