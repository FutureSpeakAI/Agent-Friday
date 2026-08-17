# Model Suite Determination — this machine, first case

**Date:** 2026-08-17
**Machine:** i7-10700F (8c/16t), 32 GB DDR4, RTX 4070 12 GB (12,282 MiB), Windows 11 Pro
**Branch:** `model-suite-determination` (worktree, off `residency-policy`)
**Status:** Phase 0 complete. **Phase 1 blocked — see "The blocker".** Phase 2 seating is provisional.

Every claim below is tagged **MEASURED** (I ran it), **VERIFIED** (read on disk or from a live
API), **INFERRED** (reasoned — from what, stated), or **UNKNOWN** (with what would resolve it).

---

## The short version

The most important thing I found is not about models. It is that **your GPU was oversubscribed
the entire time I was working, and I found the mechanism that most likely dropped your second
monitor.** It is still happening as I write this.

It was not Friday's brain. Friday's brain is well-behaved — it holds 2,199 MiB and stays there.
The problem is a model that Ollama's background daemon seated **behind the residency arbiter's
back**, at a 262,144-token context, consuming 8,697 MiB of video memory and spilling another
4,246 MiB back into system RAM.

Underneath that are two defects in the arbiter itself. One explains why it let the machine get
into this state. The other explains why your most promising heavy model has been refused a seat.
Both are small fixes with precise root causes, and both are findings that will transfer to every
machine your onboarding wizard ever runs on.

---

## The blocker

**Phase 1 measurement did not run, and I did not fake it.**

The mission asks me to find each model's spill point, which means deliberately pushing video
memory to the ceiling. I asked whether to clear the card first and whether spill-testing was
authorised. No answer came back — the session appears to be non-interactive. Two decisions were
therefore not mine to make, and I made neither:

1. **I did not kill the stray process.** Its keep-alive timer kept refreshing the whole time,
   which means something was actively using it — plausibly one of the two other sessions live in
   this repo. Killing it could have destroyed someone else's in-flight work.
2. **I did not push VRAM to the ceiling.** With 270 MiB free and a compositor already starved,
   loading a 7-9 GB model would have been the most reliable way to reproduce your monitor failure
   rather than diagnose it.

Free VRAM over the session: 334 → 322 → 289 → 270 MiB. It got worse, never better.

**What unblocks Phase 1:** clear the stray load (`Stop-Process -Id 39236`), and decide whether the
Ollama daemon should keep running at all. Then either accept a 3 GB desktop reserve — measurements
up to ~9 GB of model VRAM, spill points extrapolated and labelled INFERRED — or drop to a single
monitor and close Chrome, which frees roughly 2 GB and lets the real cliff be observed directly.

---

## The live incident

**MEASURED**, via Windows `GPU Process Memory` performance counters (`nvidia-smi` cannot attribute
per-process VRAM under WDDM — noted because the wizard will hit this on every Windows box):

| Process | PID | Dedicated VRAM | What it is |
|---|---|---|---|
| llama-server | 39236 | **8,697 MiB** | HauhauCS Balanced 12B, seated by **Ollama's daemon** at 12:12, context **262,144** |
| dwm | 1216 | **2,778 MiB** | Desktop Window Manager — draws your monitors |
| llama-server | 9272 | 2,199 MiB | Friday's brain, gemma4-12b, port 8090 |
| chrome | 7188 | 356 MiB | |
| explorer | 3312 | 203 MiB | |
| claude | 40860 | 80 MiB | |
| **Total demand** | | **14,313 MiB** | against a **12,282 MiB** card |

That is **2,031 MiB oversubscribed**. PID 39236 additionally shows **4,246 MiB of Shared Usage** —
GPU memory paged out to DDR4. That paging is the "heavy slowdown" you felt.

**INFERRED** (from the oversubscription plus the compositor's 2.8 GB demand, not observed
directly): a display driver reset under this pressure is the most probable cause of the monitor
dropping off Windows while the panel held a stale frame. A stale frame is the signature of the
compositor losing its device without the panel losing signal. I did not reproduce it, and
deliberately did not try.

**Three** `llama-server` processes were running, not the two anyone expected:

- PID 9272 — Friday's own binary, `gemma4-12b`, `-ngl 99 -c 131072`. Correct and governed.
- PID 27856 — Ollama's *engine binary* serving Friday's `e2b` GGUF on port 8092. This is the
  legitimate arrangement described in dispatch: llama.cpp upstream can't read the e-series tensor
  layout, so Ollama's binary loads it. Note it runs `-ngl 99` — **on the GPU, not CPU-pinned**,
  which contradicts the mission's framing of the sidekick as a CPU-pinned seat (Q8).
- PID 39236 — the stray. Ollama's daemon, its own blob store, `-c 262144`. Ungoverned.

---

## Phase 0 — ground truth

### Q1. What is the source of truth, and are the version floors met?

**The mission's premise is stale, and so is part of dispatch's correction.** `ollama list` is *not*
the source of truth for what Friday runs. Friday owns her own processes and her own store at
`~/.friday/runtime/models/gguf/` (**VERIFIED**, listed on disk). But Ollama is not retired either:
**its daemon is alive** (PID 28456, running since 07:44) and it can still seat models on your GPU,
which is exactly what went wrong today.

So the accurate statement is: *Ollama is retired as Friday's inference path, but not as a process
with the power to claim the card.*

Versions (**MEASURED**): Ollama **0.32.14**. Friday's `llama-server` returned no version string to
`--version` — **UNKNOWN**, resolved by checking the build stamp in `~/.friday/runtime/llama.cpp/`.
ComfyUI version **UNKNOWN** — not probed, as it would have meant touching the GPU.

Floors, per each artifact's own `requires` field (**VERIFIED** via `ollama show`):

| Model | Requires | Met by 0.32.14 |
|---|---|---|
| gemma4:12b | **0.30.5** | yes |
| gemma4:e2b / e4b / 26b | 0.20.0 | yes |
| qwen3.5:9b | 0.17.1 | yes |
| glm-4.7-flash | 0.15.0 | yes |

The mission states a "0.22+ floor the gemma4 family needs". That is wrong in both directions: most
of the family declares **0.20.0**, but `gemma4:12b` declares **0.30.5** — higher than the mission
assumed. Every floor is satisfied regardless. The glm-4.7-flash 0.15.0 figure is correct.

**Dispatch was also wrong about availability.** It warned several models might not be installed.
All of them are, downloaded about five hours before this audit: `qwen3.5:9b`, `glm-4.7-flash`,
both HauhauCS finetunes, `E4B-Aggressive`. **Only `qwen3.8:latest` is absent** (Q13) — so there is
nothing to reclaim with `ollama rm` and nothing to preserve a note about. If a 24 GB card ever
arrives, that evaluation starts from zero.

### Q2. What does the repo believe?

**VERIFIED.** A catalog exists and is substantial: `model_catalog.py`, `residency_catalog.py`,
`hosted_catalog.py`, `model_store.py`, `model_discovery.py`. Roles are defined in
`residency_policy.py:32` as `interactive_brain`, `heavy_hitter`, `sidekick`, `sidekick_heavy`, and
others.

`preferred_model` **is** wired end to end — `hints.py` reads it, `routing/model_router.py:628`
passes it to dispatch, and `residency_policy.py:837` records that a silent ignore is precisely what
used to break it. The Phase A work landed.

The arbiter is **live and governing** (**MEASURED**, `GET /api/residency/status` returns
`"governing": true`). It also **corroborates and sharpens a prior figure**: dispatch carried
"~20,000 tokens of every local turn" as overhead. The arbiter now reports it as measured —
**12,846 system prompt + 9,603 tool schemas = 22,449 tokens** before the conversation starts. The
old estimate was low by about 12%.

### Q3. Does a tool-conformance layer survive that could block a model?

**VERIFIED — no. Nothing blocks. The standing decision holds.**

`services/model_seat_gate.py` opens by stating it plainly: *"Nothing here blocks anything"*, and
*"There is no `honesty` axis and no `dual_green` — a seat is never refused."*

The one remaining call site, `_check_local_model_seat_gate` in `routes/core_routes.py:618`,
`return None` unconditionally and documents why it was kept as an honest no-op rather than deleted.

`services/tool_integrity.py` is observation only — `find_pseudo_toolcalls`, `_strip_code`,
`scrub_retry_artifacts`. It scrubs retry artifacts from text; it never refuses a model.

**One piece of cleanup worth doing:** the comment block at `core_routes.py:655-663`, sitting
directly above the dead call, still describes the *old blocking* behaviour — "A red model is
rejected, not silently substituted". The code is inert but the comment reads as though the gate is
live. That is a trap for the next reader. Smallest change: delete the call and that comment.
Clean removal would touch `routes/core_routes.py`, `routes/seat_gate.py`,
`services/model_seat_gate.py`, and their tests.

### Q4. What do the artifacts actually expose, and do the finetunes match stock?

**VERIFIED** via `ollama show` (metadata only — no model was loaded).

**HauhauCS Balanced 12B vs stock gemma4:12b — matches on the four axes asked about, differs
elsewhere in a way that matters:**

| | stock 12b | Balanced |
|---|---|---|
| architecture | gemma4 | gemma4 ✓ |
| context length | 262144 | 262144 ✓ |
| embedding length | 3840 | 3840 ✓ |
| quantization | Q4_K_M | Q4_K_M ✓ |
| parameters | 11.9B | 11.9B ✓ |
| projector | clip 52.38M | clip 52.38M ✓ |
| **capabilities** | completion, vision, audio, tools, **thinking** | completion, vision, audio, tools — **no thinking** |
| stop tokens | (none shown) | `<bos>`, `<\|turn>` declared |
| `requires` | 0.30.5 | absent |

**The finetune has lost the `thinking` capability.** That is the headline. If the interactive brain
seat is expected to reason before answering, the ablation costs you that, and no refusal-behaviour
gain offsets a capability that simply isn't advertised any more.

**E4B-Aggressive vs stock e4b — does *not* match:**

| | stock e4b | Aggressive |
|---|---|---|
| **parameters** | **8.0B** | **7.52B** |
| context length | 131072 | 131072 ✓ |
| embedding length | 2560 | 2560 ✓ |
| projector | — | clip 478.09M, emb 768 |
| capabilities | incl. **thinking** | **no thinking** |

A 0.48B parameter difference is not an ablation artifact — it is a structurally different model.
Treat it as its own artifact, not as a drop-in control arm against stock e4b.

**A note on the mission's pairing:** Q8 pairs "stock e2b" against "E4B-Aggressive". Those are not
comparable — e2b is 5.1B with a 1536 embedding, Aggressive is 7.52B with a 2560 embedding. The
honest control arm for Aggressive is stock **e4b**, and the honest question for the sidekick seat
is a three-way: e2b, e4b, Aggressive.

---

## Two defects in the arbiter

### Defect A — the display reserve is wrong, and this is what breaks monitors

**VERIFIED.** The arbiter budgets `baseline_mib: 542` for everything that is not a model. Your
compositor alone wants **2,778 MiB**. The arbiter is under-reserving by roughly **2.2 GB**, and it
will cheerfully seat models into memory Windows needs to draw your screen.

Root cause, in `hardware_profile.py:242` and `refresh_baseline`: the baseline is measured **once,
at boot, with `assert_idle=True`**, then cached. The code's reasoning is sound as far as it goes —
its comment argues that "a poisoned floor is worse than a defaulted one." But a *static* number
cannot model a *dynamic* compositor. DWM's appetite scales with monitor count, resolution, HDR, and
how much browser is open. A boot-time idle floor systematically under-reserves for every state the
desktop later enters.

**This is the single most transferable finding in this audit.** It is not specific to your machine —
it is specific to *running models on a box someone is also using as a desktop*, which is the exact
situation the onboarding wizard will face every time. A headless server has no DWM and a 542 MiB
baseline is fine. Your machine is not that, and neither are your users'.

Suggested direction (not implemented — the hard gate holds): reserve against a *dynamic* display
figure, sampled at seat time rather than boot time, with a floor of ~2.5-3 GB on any Windows box
driving a desktop. Prior measurement of "11.4 GB of 12,282 MiB with seats resident" should be read
as *already over the safe line*, not as headroom.

### Defect B — a MoE is being refused as dense, which is why your heavy hitter has no seat

**VERIFIED.** The arbiter currently refuses `glm-4.7-flash` for the `heavy_hitter` role:

> rule R6 `moe-offload` — *"dense model needs 0 MiB and the largest GPU budget is 10716 MiB; dense
> models must fit or be demoted, only MoE may expert-offload"*

But `glm-4.7-flash` **is** a MoE. `ollama show` reports its architecture as **`glm4moelite`**, 29.9B
parameters.

Root cause is a two-entry lookup table. `residency_catalog.py:148`:

```python
KNOWN_ACTIVE_PARAMS_B: dict = {
    "gemma4:26b": 4.0,          # 26B-A4B
    "gemma-4-26b-a4b": 4.0,
}
```

and at line 363, `is_moe = bool(active_b and total_b and active_b < total_b)`. With no entry,
`active_b` is `None`, so `is_moe` is `False`, so R6 refuses it as dense. The "needs 0 MiB" in the
refusal text is the same gap showing twice — no catalog entry means no size estimate either, so the
rule fires on a zero.

The fix is one dictionary entry. **UNKNOWN:** glm-4.7-flash's true active parameter count — I did
not want to guess a number that feeds a residency decision. Resolved by reading
`glm.expert_used_count` and the expert dimensions straight from the GGUF metadata, which
`gguf_extract.py` already exists to do.

**A better fix than the entry:** derive `is_moe` from the architecture string the artifact already
reports, rather than from a hand-maintained allowlist. Any name containing `moe` is self-declaring.
A hardcoded two-model table will keep silently mis-seating every new MoE that arrives, and the
failure mode is quiet — a refusal that reads like a legitimate capacity decision.

---

## Phase 1 — measurement

**Not run.** See "The blocker". Every Phase 1 question below is **UNKNOWN**, with the resolving
step stated.

- **Q5** (decode/prefill/TTFT/VRAM at 4096 and at max GPU-resident, per candidate) — UNKNOWN.
  Resolved by a clear card plus a decision on the desktop reserve.
- **Q6** (first-emission tool-call validity across 10 fixed prompts) — UNKNOWN. This one needs no
  spill risk and could run at 4096 ctx on a cleared card cheaply. Prior evidence to corroborate:
  local models passed four-dependent-call chains 15/15 after the argument-parsing bug was fixed.
- **Q7** (Balanced refusal delta vs stock; speculative decoding acceptance rate) — UNKNOWN, and
  partly answered adversely already: **Balanced does not advertise `thinking`**, so part of what
  the ablation costs is visible without running it.
- **Q8** (sidekick on eight cores) — UNKNOWN. Also mis-specified: the resident e2b seat runs
  `-ngl 99`, i.e. **on the GPU**, not CPU-pinned. Prior figure of ~166 tok/s for e2b is a
  *GPU-resident* number and should not be read as a CPU-pinned one.
- **Q9, Q10** (heavy hitter throughput, RAM ceiling, lease transitions) — UNKNOWN. Prior figures to
  corroborate when unblocked: 26b wake 53.5s, ~22s per answer, 47% GPU at 32768 ctx.
- **Q12** (embedder re-index, Z-Image lease, whisper RTF) — UNKNOWN, deliberately: probing ComfyUI
  or Z-Image means GPU allocation, which was the one thing I would not do today.

**Q11 — does every backend route through the single egress choke point?**

**Answered, and the answer is no — demonstrated rather than argued.** Today's incident *is* the
proof: Ollama's daemon seated a 9.9 GB model on the GPU while the arbiter reported `governing:
true` and `lease: null`. The arbiter did not refuse it. It never saw it.

The gap is not an adapter that needs a wrapper. It is that a **second resource manager is running**
with independent authority over the same card. No amount of adapter work inside Friday closes it,
because the bypass does not go through Friday. The smallest change that actually closes it is to
stop the Ollama daemon and invoke its engine binary directly — which is already exactly how the
e2b seat works today (PID 27856), so the pattern is proven and in production.

---

## Phase 2 — determination (PROVISIONAL)

The mission says to defer seating mechanics to the residency arbiter if it landed. **It landed.**
So this is a recommendation to the arbiter's policy, not a set of writes.

**No seats are being applied.** Applying seats on unmeasured evidence is what this whole exercise
exists to stop, and two of the three inputs that would change the answer are unmeasured.

| Role | Provisional seat | Basis | Confidence |
|---|---|---|---|
| interactive_brain | `gemma4:12b` (stock) | **MEASURED** resident at 2,199 MiB, `-c 131072`, stable across the session. Retains `thinking`, which Balanced drops. | Reasonable |
| sidekick | `gemma4:e2b` | **MEASURED** resident and serving on 8092 via Ollama's engine binary. Prior ~166 tok/s (GPU-resident). | Reasonable |
| heavy_hitter | `gemma4:26b`, leased | **VERIFIED** the only MoE the catalog can currently classify; prior 53.5s wake. `glm-4.7-flash` is a live contender blocked only by Defect B. | Weak — unmeasured |
| embedder | `qwen3-embedding:0.6b` | **VERIFIED** on disk, 639 MB, Q8_0, 32768 ctx. Must never be plan-bound (prior decision D5). | Reasonable |
| stt | whisper (existing) | Prior RTF 0.869. Not re-measured. | Weak |
| tts | Piper (existing) | Not probed. | Weak |
| image | Z-Image (exclusive GPU lease) | Prior ~93s warm / 28.1s. Lease integrity **UNKNOWN**. | Weak |

**Benched, one line each:**

- `HauhauCS Balanced 12B` — **drops `thinking`**; refusal-behaviour gain unmeasured. Keep as control arm.
- `E4B-Aggressive` — 7.52B vs stock 8.0B; a different artifact, not a clean control. Also drops `thinking`.
- `glm-4.7-flash` — refused by R6 on a misclassification, not on merit. **Unbench once Defect B is fixed**; it is the most interesting untested model you own.
- `qwen3.5:9b` — installed, entirely unmeasured; no basis to seat or reject.
- `gemma4:e4b` — the honest control arm for Aggressive; unmeasured.
- `qwen3.8:latest` — **not installed.** Nothing to reclaim.

**Q15 — num_ctx, keep_alive, backend, device.** The one number I will assert now: the stray load's
**262,144 context is indefensible on this card** and is the proximate cause of today's incident.
Whatever else changes, no 12 GB card should seat a 12B model at 262k. Friday's own brain runs
131072 and behaves. The rest awaits Phase 1.

---

## Phase 3 — Wiggum pass

**Q17. Which questions resisted an evidence-backed answer, and why?**

All of Phase 1, for one reason: the card was full the entire time, and the two actions that would
have freed it were not mine to take. That is not a tooling failure — it is the safety note in the
mission working exactly as intended. The mission asked me to find spill points on a machine whose
owner had just lost a monitor to VRAM pressure. Those two instructions are in direct tension, and
I resolved it toward the monitor.

Q1's version question also resisted cleanly: Friday's own `llama-server --version` returned nothing.

**Q18. What question should have been asked that was not?**

**"What else on this machine can claim the GPU, and does Friday know about it?"**

Every question in the mission is about models Friday manages. None asks what *else* is running.
That blind spot is precisely where the real problem lived — an 8.7 GB allocation from a daemon
nobody thought was still in play, invisible to an arbiter that reported itself as governing.

A close second: **"how much video memory does the desktop itself need?"** The mission's ceiling is
stated as "11 GB pinned VRAM" on a 12,282 MiB card, which leaves ~1.2 GB for Windows. Your
compositor wants 2,778 MiB. **The mission's own ceiling is unsafe**, and no measurement inside it
would have revealed that, because every measurement would have been taken beneath a ceiling that
was already too high.

**Q19. Which single measurement, if wrong, would most change the seating?**

The **display baseline**. Everything cascades from it. At 542 MiB the arbiter believes it has
10,716 MiB to allocate; at a realistic 2,800 MiB it has roughly 8,400 MiB. That difference is
larger than the entire interactive brain seat. Every seating decision, every spill point, and both
stated ceilings are computed against a number that is wrong by more than a brain's worth of memory.

**Q20. Do both ceilings hold, and what would you regret in thirty days?**

**The VRAM ceiling does not hold. It was breached today, by 2,031 MiB, and it stayed breached.**
The RAM ceiling was never approached — the arbiter reports 18,321 MiB available against a 24,465
MiB hard ceiling, and nothing I saw threatened it.

Three things you would regret in thirty days if left unstated:

1. **That the Ollama daemon can still seat models.** Today it cost you a monitor and an audit. In
   thirty days, when the arbiter's decisions are trusted and nobody is watching `nvidia-smi`, it
   will cost you something with less obvious symptoms — a slow degradation blamed on the models.
2. **That `is_moe` is a two-entry hardcoded table.** Every future MoE gets silently refused with a
   plausible-sounding capacity message. The failure looks like a decision, not a bug — the most
   expensive kind to find later.
3. **That the seating in this document is provisional.** If it gets read as settled, the exercise
   inverts: you would have unmeasured seats carrying an audit's authority, which is worse than no
   audit. Nothing here was applied for exactly that reason.

---

## What transfers to other machines

For the onboarding wizard, four things generalise beyond this box:

1. **Reserve for the desktop dynamically, not once at boot.** Sample at seat time, floor at ~2.5-3
   GB on any Windows machine driving a display. A boot-time idle baseline is a systematic
   under-reservation on every desktop-class machine.
2. **Enumerate other GPU claimants before seating anything.** Ask what else can allocate, not just
   what we intend to. On Windows this means `GPU Process Memory` performance counters —
   **`nvidia-smi` cannot attribute per-process VRAM under WDDM**, which will bite the wizard on
   every Windows install.
3. **Derive model structure from the artifact, never from a hardcoded table.** Architecture strings
   are self-declaring; allowlists rot silently.
4. **Read each artifact's own `requires` floor.** Family-wide assumptions were wrong here in both
   directions — the mission's stated 0.22 floor was too high for most of the family and too low
   for `gemma4:12b`, which needs 0.30.5.

---

## Evidence register

| # | Claim | Tag | Source |
|---|---|---|---|
| 1 | 14,313 MiB demanded on a 12,282 MiB card | MEASURED | `Get-Counter '\GPU Process Memory(*)\Dedicated Usage'` |
| 2 | PID 39236 holds 8,697 MiB + 4,246 MiB spilled | MEASURED | same, Shared Usage |
| 3 | dwm holds 2,778 MiB | MEASURED | same |
| 4 | Stray is HauhauCS Balanced at 262144 ctx, 29%/71% CPU/GPU | MEASURED | `ollama ps` |
| 5 | Ollama daemon PID 28456 live since 07:44 | MEASURED | `Get-Process ollama` |
| 6 | Three llama-server processes, cmdlines captured | MEASURED | `Win32_Process` |
| 7 | Ollama 0.32.14 | MEASURED | `ollama --version` |
| 8 | Per-model `requires` floors | VERIFIED | `ollama show` |
| 9 | Balanced lacks `thinking`; matches stock on arch/ctx/emb/quant | VERIFIED | `ollama show` |
| 10 | Aggressive is 7.52B vs stock e4b 8.0B | VERIFIED | `ollama show` |
| 11 | glm-4.7-flash arch is `glm4moelite`, 29.9B | VERIFIED | `ollama show` |
| 12 | Arbiter refuses glm as dense, R6, "needs 0 MiB" | VERIFIED | `GET /api/residency/status` |
| 13 | `KNOWN_ACTIVE_PARAMS_B` has two entries | VERIFIED | `residency_catalog.py:148` |
| 14 | `is_moe` derived from that table only | VERIFIED | `residency_catalog.py:363` |
| 15 | baseline_mib 542, measured once at boot, cached | VERIFIED | `/api/residency/status`; `hardware_profile.py:242` |
| 16 | Seat gate blocks nothing; call site returns None | VERIFIED | `model_seat_gate.py:8`, `core_routes.py:618` |
| 17 | Stale comment describes removed blocking behaviour | VERIFIED | `core_routes.py:655-663` |
| 18 | Overhead 12,846 + 9,603 = 22,449 tokens | MEASURED | `/api/residency/status` (corroborates prior ~20k, low by ~12%) |
| 19 | All named models installed except `qwen3.8:latest` | VERIFIED | `ollama list` |
| 20 | Driver reset as monitor-drop cause | INFERRED | from #1-#3; not reproduced, deliberately |

## Open questions

1. glm-4.7-flash's true active parameter count — from GGUF metadata via `gguf_extract.py`.
2. Friday's `llama-server` build version — `--version` returned nothing.
3. What was refreshing PID 39236's keep-alive throughout the session?
4. Whether Z-Image's exclusive GPU lease is intact — unprobed, needed GPU.
5. Whether a distil-class STT checkpoint would help — RTF 0.869 not re-measured. Recommend only after Phase 1.
6. All of Phase 1.
