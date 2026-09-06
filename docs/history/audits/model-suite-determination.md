# Model Suite Determination — this machine, first case

**Date:** 2026-08-17
**Machine:** i7-10700F (8c/16t), 32 GB DDR4, RTX 4070 12 GB (12,282 MiB), Windows 11 Pro
**Branch:** `model-suite-determination` (worktree, off `residency-policy`)
**Status:** Phase 0 complete. Phase 1 measured under a 3 GB desktop reserve. Three fixes applied and tested.

Tags: **MEASURED** (I ran it), **VERIFIED** (read on disk or from a live API), **INFERRED**
(reasoned — from what, stated), **UNKNOWN** (with what would resolve it).

---

## The short version

**The thing that endangered your monitor is Friday's own brain, not the stray Ollama process.**

That correction is the most important sentence in this document, and it reverses my first
conclusion. The Ollama stray was real and made things worse, but it was a passenger. Here is the
decisive measurement: with the card idle at 1,701 MiB, I asked Friday's brain on port 8090 one
question. The card went to **11,353 MiB and stayed there** — 660 MiB free. Your compositor wants up
to 2,773 MiB when Chrome is open.

So `gemma4:12b` at 131,072 context, which is Friday's *current default seat*, **does not fit this
machine alongside a working desktop.** It doesn't need a stray process to starve the screen. It
does that on its own, every time you use it.

Three fixes are committed, tested, and explained below. But the seating conclusion is the finding
that matters: **the brain and the sidekick cannot both be resident on a 12 GB card that is also
driving two displays**, and today they are both pinned.

---

## What I did, and what I did not

You gave three calls. Two I followed. One I did not, and here is why.

**1. Clear the stray — I did not kill it, and you'd have made the same choice with what I found.**
You were right that it was your dropdown selection: `model_routing.local_model` is set to
`hf.co/HauhauCS/…Balanced:Q4_K_M` (**VERIFIED** in `~/.friday/settings.json`). But it was not an
orphan of one seat swap. It **kept coming back all afternoon**, because every local turn re-seated
it. And when I checked for genuine activity as you asked, the answer was yes — but not from you.
Two processes running `verify_pipeline.py` from **another Claude session's scratchpad** were driving
the Ollama daemon, and at one point had `gemma4:26b` resident at 18 GB. Killing it would have
destroyed a live session's verification run.

It became moot: I fixed the cause instead, and the card cleared itself (**MEASURED** — 10,312 MiB
free, nothing resident in Ollama).

**2. Ollama's daemon — still running, deliberately.** You said to leave it if something genuinely
needed it. Something did: that other session, actively. Stopping it mid-run was not a trade worth
making for a tidier process list. **This remains open and is a one-line action once the other
session is done.** I did remove the daemon's ability to seat an unbounded model, which was the part
that actually hurt.

**3. Reserve 3 GB and extrapolate — done.** Measurements ran under a hard 3 GB desktop reserve with
an automatic stop. That stop fired once, exactly as intended, and is why one sweep is short.

---

## Phase 1 — measurement

All four candidates, `num_ctx` 4096, temperature 0, one model resident at a time, evicted between.
**MEASURED.**

| model | decode tok/s | prefill tok/s | TTFT | VRAM resident | cold load | tool calls |
|---|---|---|---|---|---|---|
| `gemma4:12b` (stock) | 53.2 | 515.2 | 0.61 s | 7,689 MiB | 26.0 s | **1 / 3** |
| HauhauCS Balanced 12B | 52.9 | 351.5 | 0.68 s | 7,689 MiB | 25.0 s | **3 / 3** |
| `qwen3.5:9b` | **69.8** | 447.5 | **0.39 s** | 5,235 MiB | 20.0 s | 1 / 3 |
| `gemma4:e2b` | **169.1** | **1,090.8** | 0.47 s | 1,629 MiB | 23.6 s | 1 / 3 |

`gemma4:e2b` at **169.1 tok/s** corroborates the prior ~166 tok/s figure closely. Note that figure
was always a **GPU-resident** number — the seat runs `-ngl 99` — so the mission's framing of the
sidekick as "CPU-pinned" (Q8) does not describe what is actually running.

### The surprise, and it is the whole argument for the finetune (Q6, Q7)

The Balanced finetune called the tool **3 times out of 3.** Stock `gemma4:12b`, `qwen3.5:9b` and
`e2b` each managed **1 out of 3.**

The failures are not malformed JSON. They are **refusals to use a tool the model was handed a
schema for**:

> stock 12b: *"I don't have access to real-time weather information through…"*
> e2b: *"I do not have the ability to search the web."*

They had `get_weather` and `web_search` in their tool list — Friday's real schemas, loaded from
`agent.CLAUDE_TOOLS` — and declined anyway.

So the ablation demonstrably delivers, but **not in the way anyone framed it.** Its practical value
here is not permissive content. It is that the model stops arguing with its own capabilities. On a
machine where the local seat exists to *do things*, a brain that refuses one tool call in three is
the more serious defect.

**The honest caveat:** n = 3 tool prompts per model, temperature 0, one run. That is a signal, not a
proof. The mission asked for ten prompts; three real Friday schemas loaded cleanly, so three ran.
**A ten-prompt, multi-seed rerun is the single cheapest thing that would firm this up**, and it
needs no GPU risk at 4096 context.

Set against it: the finetune **drops the `thinking` capability** (VERIFIED, Q4), and its prefill is
32% slower (351.5 vs 515.2 tok/s). That is a real trade, not a free win.

### VRAM, the ceiling, and the spill point (Q5, Q20)

Two anchors, both **MEASURED**:

- `gemma4:12b` via Ollama at `num_ctx` 4096: **7,689 MiB, 100% GPU.** KV is negligible here, so
  this is effectively the weights.
- Friday's own `llama-server` at `-c 131072`, exercised: card **1,701 → 11,353 MiB**, a delta of
  **9,652 MiB**. This lands within 27 MiB of the arbiter's own `pinned_vram_mib: 9625`, which
  independently confirms both numbers.

From those: **KV cost ≈ 15.3 MiB per 1,000 tokens** (INFERRED, two anchors across two backends —
the weakest link in this document, see Q19). That is strikingly low, and the architecture explains
it: `gemma4` uses **sliding-window attention**, `sliding_window = 1024`, with `key_length_swa` /
`value_length_swa` of 256 against 512 for the global layers (VERIFIED from GGUF metadata). Most
layers' cache is window-capped rather than context-linear. **This is why a 12B can be offered a
131k window at all on a 12 GB card** — and why the naive "context is what costs you" intuition is
wrong for this family.

Now the ceiling arithmetic. Desktop draw **MEASURED** at **2,773 MiB** for `dwm` alone (2,778 in an
earlier sample — stable), plus ~290 MiB for explorer and Chrome:

| | MiB |
|---|---|
| brain `gemma4:12b` @ 131,072 | 9,652 (MEASURED) |
| sidekick `gemma4:e2b` @ 32,768 | 1,629 (MEASURED) |
| desktop, Chrome open | 3,061 (MEASURED) |
| **total** | **14,342** |
| **card** | **12,282** |
| **over by** | **2,060** |

**INFERRED, not measured — and deliberately so.** Running the brain and sidekick together while the
desktop was live is precisely the experiment that would have dropped your monitor again. Every
component is measured; only the sum is arithmetic. Note it lands within 30 MiB of the 14,313 MiB
I measured this morning by accident, from a completely different composition. Two independent
routes to the same 2 GB overdraft.

**Both stated ceilings fail.** The mission's "11 GB pinned VRAM" leaves 1.2 GB for a desktop that
wants 3.1 GB. The arbiter's own `baseline_mib: 542` was worse. Neither survives contact.

**Spill points, INFERRED** (weights 7,689 MiB, KV 15.3 MiB/1k):

| reserve | model budget | max context that fits |
|---|---|---|
| 3.0 GB desktop (recommended) | 9,282 MiB | ~**106,000** tokens — brain alone, no sidekick |
| 3.0 GB desktop + e2b sidekick | 7,653 MiB | **does not fit at all** (weights alone are 7,689) |
| 1.0 GB desktop (mission's ceiling) | 11,258 MiB | ~239,000 tokens — but the desktop dies first |

The middle row is the seating conclusion: **weights alone (7,689) exceed the brain's budget (7,653)
once the sidekick is also pinned.** Not "tight" — negative, before a single token of context.

### Q11 — does every backend route through one choke point?

**Answered by demonstration, and the answer is no.** The Ollama daemon seated a 9.9 GB model while
the arbiter reported `governing: true` and `lease: null`. It never saw it.

The gap was never an adapter needing a wrapper — a *second resource manager* held the same card. Fix
3 below closes the damaging half (nothing can be seated unbounded any more). Closing it fully means
stopping the daemon and invoking Ollama's engine binary directly, which is already how the e2b seat
works today (PID 27856) — proven, in production, not a new pattern.

### Still UNKNOWN

- **Q9, Q10** — heavy-hitter throughput and lease transition times. Not run: the card could not hold
  a 17-18 GB artifact under a 3 GB reserve without the expert-offload recipe, and validating that
  recipe needs the card clear of both the brain and another session's work.
- **Q12** — embedder re-index path, Z-Image lease integrity, STT distil comparison. Unprobed; each
  needs GPU allocation. **Recommend, do not install:** revisit only after the seating below lands.
- **Q13** — `qwen3.8:latest` **is not installed**. Nothing for `ollama rm` to reclaim.
- **Q7, speculative decoding** — untested. The draft-model pairing needs both artifacts resident,
  which the ceiling forbids today.

---

## Three fixes, applied and tested

Committed in `beea650`. Full unit suite green (2,000+ tests), plus 7 new ones.

**1. The display reserve is sampled, not remembered.** `effective_baseline_mib` returned a floor
measured once at boot on an idle desktop — 542 MiB against a compositor holding 2,773. The arbiter
planned seats into memory Windows needed to draw the screen.

Sampling now lives in `refresh_display_reserve()`, called by the arbiter from `compute_plan()`,
right beside the live prompt-overhead read it already did. `effective_baseline_mib` stays **pure** —
it reads a profile field and never probes — so `rp.plan` remains a deterministic function of the
profile and its golden fixtures keep meaning something. **MEASURED effect: 542 → 3,241 MiB, i.e.
2,699 MiB it will no longer hand out.**

I got this wrong twice before it was right, and both are worth recording. First I put the live probe
*inside* the pure function, which broke 14 golden-fixture tests — correctly, because it made
planning depend on what was open on the desktop. Then my probe summed **12,821 MiB** on a 12,282 MiB
card, because the performance counter's instance names are `pid_1234_luid_…` and carry no process
name, so my exclusion filter matched nothing and counted the model servers as display. Both were
caught by running the thing rather than reasoning about it.

**2. `is_moe` reads the artifact, not an allowlist.** `glm-4.7-flash` was refused the heavy_hitter
seat by rule R6 as "dense". It is `glm4moelite`, 29.9B. `detect_moe()` now finds **`expert_count=64`
in its own GGUF metadata** (MEASURED). `gemma4:26b` independently confirms at **128 experts** —
better evidence than the hand-entered `4.0` the two-entry table relied on. Both catalog paths carry
an `is_moe_basis` so a refusal can state why it believes what it believes.

**3. Nothing seats a model unbounded.** This was the actual engine of the 262,144-context reloads.
`chat_completion` defaulted `num_ctx` to `None`, which fell through to `/v1/chat/completions` — an
endpoint that **silently discards `options.num_ctx`** (the repo already documented this and used it
anyway). So any caller that named no context got the artifact's declared maximum. Requests now carry
a bounded context and a bounded `keep_alive`, and the `/v1` branch is **removed** rather than left
as a trapdoor. Its test asserted `/v1` was tried first, so it was rewritten to the new contract
rather than deleted.

---

## Phase 2 — determination

Seating mechanics defer to the residency arbiter, which landed. This is a recommendation to its
policy, and **the ceiling arithmetic forces a real choice you have not had to make before.**

| Role | Seat | Evidence | Confidence |
|---|---|---|---|
| interactive_brain | `gemma4:12b` @ **32,768**, llama-server, GPU | MEASURED 7,689 MiB weights + ~490 MiB KV. Retains `thinking`. Fits beside a sidekick and a 3 GB desktop with ~470 MiB spare. | Good |
| sidekick | `gemma4:e2b` @ 32,768, Ollama engine binary, GPU | MEASURED 1,629 MiB, 169.1 tok/s, 1,090 tok/s prefill | Good |
| heavy_hitter | `gemma4:26b`, leased, expert-offload | VERIFIED MoE at 128 experts; prior 53.5 s wake. Unmeasured this session. | Weak |
| embedder | `qwen3-embedding:0.6b` | VERIFIED 639 MB, Q8_0. Never plan-bound (D5). | Good |
| stt / tts / image | whisper / Piper / Z-Image | Prior figures only; unprobed. | Weak |

**Q15 — the numbers, and the one that changes.** The brain's `num_ctx` must come down from
**131,072 to 32,768.** That is the whole recommendation. At 131,072 it takes 9,652 MiB and the
machine is 2 GB oversubscribed the moment the sidekick and your desktop are also real. At 32,768 the
KV cost is roughly 490 MiB (INFERRED at 15.3 MiB/1k), the brain fits in ~8,180 MiB, and
brain + sidekick + a 3 GB desktop lands near 11,810 MiB against 12,282 — the first configuration
in this document that actually closes.

You lose window, and it is a real loss: with **22,449 tokens** of measured overhead (12,846 system
prompt + 9,603 tool schemas — corroborating the prior ~20k, low by ~12%), a 32,768 seat leaves about
**10,300 tokens** of working room per turn. That is the price of two monitors. It is worth stating
plainly rather than discovering later.

**Benched, one line each:**

- **HauhauCS Balanced 12B** — 3/3 on tool calls against stock's 1/3 is the strongest single result
  here, but n=3, and it drops `thinking` and 32% of prefill. **Rerun at ten prompts before seating
  it.** This is the most promising bench candidate you have.
- `E4B-Aggressive` — 7.52B vs stock e4b's 8.0B: a different artifact, not a control arm.
- `glm-4.7-flash` — no longer misclassified, now genuinely seatable as heavy_hitter. Unmeasured.
- `qwen3.5:9b` — fastest decode of the 12B-class candidates (69.8 tok/s) at 5,235 MiB, but 1/3 on
  tools. Interesting if the tool-refusal pattern proves fixable by prompt.
- `gemma4:e4b` — the honest control arm for Aggressive; unmeasured.
- `qwen3.8:latest` — not installed.

---

## Phase 3 — Wiggum pass

**Q17. What resisted an evidence-backed answer?** The heavy-hitter tier (Q9, Q10) and the support
tiers (Q12), all for the same reason: a 3 GB desktop reserve on a 12 GB card leaves no room to
stage a 17 GB artifact while another session holds the machine. Also `llama-server --version`, which
returns nothing.

**Q18. What should have been asked and wasn't?** *"What else can claim this GPU, and does Friday
know about it?"* Every mission question concerns models Friday manages. None asks what else is
running — which is exactly where the problem lived. A close second, and the one that would have
saved the monitor: *"how much video memory does the desktop itself need?"* The mission's own 11 GB
ceiling leaves 1.2 GB for a desktop that wants 3.1 GB, so every measurement taken inside that
ceiling would have been taken beneath a number that was already unsafe.

A third, which only surfaced by accident: *"do two backends serving the same model report VRAM the
same way?"* They do not, and I nearly drew a wrong conclusion from it — see below.

**Q19. Which single measurement, if wrong, would most change the seating?** The **KV cost of 15.3
MiB per 1,000 tokens.** It is the weakest number here and everything downstream leans on it: the
32,768 recommendation, the ~106,000-token ceiling, and the claim that brain and sidekick don't
co-fit. It is INFERRED from two anchors taken on **different backends** — Ollama at 4,096 and
llama-server at 131,072 — and I have already been caught once today assuming those two report memory
alike. Friday's brain reads 1,229 MiB idle and 9,652 MiB exercised; a reading taken at the wrong
moment is off by a factor of eight. **Resolving it properly costs one clean sweep on one backend
with the card otherwise empty**, and it should be done before the seating is treated as settled.

**Q20. Do both ceilings hold, and what would you regret in thirty days?** Neither holds. The card is
oversubscribed by ~2 GB under the worst measured combination, by two independent routes. The RAM
ceiling was never threatened — 18,321 MiB available against a 24,465 MiB hard ceiling.

Four things you would regret going unsaid:

1. **That your brain's default seat is what endangers your monitor.** Everyone spent today hunting a
   stray process. The stray was real, but the brain at 131,072 does it alone, every time you use it,
   and it stays resident afterward. If this document says one thing, that is it.
2. **That the Ollama daemon is still running.** Its ability to seat an *unbounded* model is fixed.
   Its ability to seat models outside the arbiter's knowledge is not. It is one command, once the
   other session finishes.
3. **That the Balanced finetune's 3/3 is n=3.** It is the most interesting result in this audit and
   the least robust. Someone will quote it as settled within a month if this line is missing.
4. **That the 32,768 recommendation costs you window.** ~10,300 tokens of working room per turn
   after overhead. If that turns out to be too little in practice, the answer is a smaller *brain*,
   not a bigger window — and it is better to know that now than to discover it as a regression.

---

## What transfers to other machines

For the onboarding wizard, five things generalise beyond this box:

1. **Reserve for the desktop dynamically, not once at boot.** Sample at plan time, floor at ~2.5–3
   GB on any machine driving a display. Now implemented; it was worth 2,699 MiB here.
2. **Enumerate other GPU claimants before seating anything.** On Windows this means performance
   counters — **`nvidia-smi` cannot attribute per-process VRAM under WDDM.** The wizard will hit
   this on every Windows install, and the instance names carry PIDs, not process names.
3. **Measure a seat under load, never at idle.** Friday's brain reads 1,229 MiB idle and 9,652 MiB
   working. A wizard that probes a quiet machine will seat models that don't fit the busy one.
4. **Derive model structure from the artifact, never a hardcoded table.** Architecture strings and
   `expert_count` are self-declaring; allowlists rot silently and their failures look like
   legitimate capacity decisions.
5. **Never let a context default to the artifact's maximum.** A 262,144 default on a consumer card
   is not a generous setting, it is an unbounded allocation wearing a number.

---

## Evidence register

| # | Claim | Tag | Source |
|---|---|---|---|
| 1 | Brain @131,072 takes card 1,701 → 11,353 MiB and stays | MEASURED | `nvidia-smi` before/after a live 8090 request |
| 2 | That 9,652 MiB delta matches arbiter `pinned_vram_mib: 9625` | MEASURED | `/api/residency/status` |
| 3 | dwm holds 2,773 MiB (2,778 earlier) | MEASURED | `Get-Counter '\GPU Process Memory(*)\Dedicated Usage'` |
| 4 | 14,313 MiB demanded on a 12,282 MiB card (morning) | MEASURED | same |
| 5 | Four-model perf table @4096 | MEASURED | Ollama `/api/chat` timing fields |
| 6 | Balanced 3/3 tool calls, stock 1/3, failures are refusals | MEASURED | real `agent.CLAUDE_TOOLS` schemas |
| 7 | e2b 169.1 tok/s (corroborates prior ~166) | MEASURED | same |
| 8 | gemma4:12b weights ≈ 7,689 MiB, 100% GPU @4096 | MEASURED | `/api/ps` |
| 9 | KV ≈ 15.3 MiB per 1k tokens | INFERRED | #1 and #8, across two backends — weakest link |
| 10 | brain+sidekick+desktop = 14,342 MiB | INFERRED | sum of #1, #3, and e2b's 1,629 — deliberately not run |
| 11 | gemma4 uses sliding-window attention, window 1024 | VERIFIED | GGUF metadata |
| 12 | glm-4.7-flash is MoE, expert_count 64 | MEASURED | `detect_moe` on GGUF metadata |
| 13 | gemma4:26b expert_count 128 | MEASURED | same |
| 14 | Display reserve 542 → 3,241 MiB after fix | MEASURED | `refresh_display_reserve` in-process |
| 15 | `/v1` discards num_ctx; `/api/chat` honours it | VERIFIED | `ollama_manager.py:317-321`, pre-existing |
| 16 | `local_model` = Balanced finetune | VERIFIED | `~/.friday/settings.json` |
| 17 | Another session drove the daemon (`verify_pipeline.py`) | MEASURED | `Get-NetTCPConnection` + `Win32_Process` |
| 18 | Overhead 22,449 tokens (12,846 + 9,603) | MEASURED | `/api/residency/status` |
| 19 | Seat gate blocks nothing | VERIFIED | `model_seat_gate.py:8`, `core_routes.py:618` |
| 20 | All named models installed except `qwen3.8:latest` | VERIFIED | `ollama list` |
| 21 | Per-model `requires` floors; 12b needs 0.30.5 not 0.22 | VERIFIED | `ollama show` |
| 22 | Balanced drops `thinking`; Aggressive is 7.52B vs 8.0B | VERIFIED | `ollama show` |

## Open questions

1. **KV cost on a single backend with an empty card** — the one measurement that would firm up the
   seating (Q19).
2. **Balanced finetune at ten prompts, multi-seed** — cheap, no GPU risk, would settle the most
   interesting result here.
3. Heavy-hitter tier: 26b expert-offload and glm-4.7-flash, both now seatable, both unmeasured.
4. Why two backends report the same model's VRAM so differently.
5. Stopping the Ollama daemon — one command, blocked only on the other session finishing.
6. glm-4.7-flash's active parameter count, for size estimation (`expert_count` settles *whether*
   it's MoE; not *how big* the active path is).
7. Z-Image lease integrity, embedder re-index path, STT distil comparison.
