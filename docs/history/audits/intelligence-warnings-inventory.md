# Intelligence settings — warning inventory, and TwiL-LM availability

**Date:** 2026-08-23
**Branch:** `higgsfield-integration`
**Status:** findings only. Nothing in this repo was changed to produce it.

Live readings taken read-only against the running server on loopback
(`GET /api/intelligence`, `GET /api/residency/status`), against the Ollama daemon
(`GET /api/tags`), and from `nvidia-smi`. Friday was not restarted and no model was
loaded or evicted. Stephen's resident seat (`functiongemma:270m`) was untouched.

---

## Part 1 — Is TwiL-LM available in Friday?

### Yes. Both variants. By accident, not by design.

Confirmed three ways, not inferred:

1. **Ollama inventory** (`/api/tags`) holds `hf.co/webAI-Official/TwIL-LM:Q4_K_M`
   (0.98 GB), `hf.co/webAI-Official/TwIL-LM3:Q4_K_M` (1.78 GB) and
   `hf.co/ggml-org/SmolLM3-3B-GGUF:Q4_K_M` (1.78 GB) — the ablation baseline.
2. **The payload** (`/api/intelligence`) lists all three with
   `roles = ["orchestrator", "subagent"]`, `local = true`, `available = true`,
   `state = "cold"`.
3. **The rendered UI**: typing `twil` into the top-bar quick switch returns
   **TwIL LM** and **TwIL LM3**, both selectable, described only as
   `local · cold · ~1s to wake (est.) · 1.1 GB` and
   `local · cold · ~2s to wake (est.) · 1.9 GB`.

### The mechanism

`services/model_catalog.py:267-269` — for any provider of type `ollama`, the live
daemon list **replaces** the declared statics outright:

```python
live = _live_ollama_models(provider.get("base_url"))
if live:              # daemon up, models installed → reality only
    ids = list(live)
```

`provider_registry.py:119-126` gives the Ollama provider
`"roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT]` and `"model_meta": {}`. With no
per-model override, **every** model the daemon reports inherits both roles.
`suitability()` (index.html) admits a model if *either* its modality or its role
matches, so an `orchestrator` role alone qualifies it for all eight tools-requiring
seats: Everyday conversation, Heavy thinking, Quick reflexes, Research & background,
Routing your work, Fast sidekick, Tool calling, Memory keeper, Deep research.

Nothing curated TwiL in. Nothing excluded it either. `ollama pull` is the whole
admission process.

### One surface, not two

`routes/intelligence.py` composes catalogue + residency + machine + cost ledger into a
single payload, and both the quick switch and Settings → Intelligence call
`useIntelligence()` against it (`index.html:32111`, `32180`, `32396`). **They cannot
disagree about which models exist.** This is the one place in the flow where the
two-source risk has already been closed deliberately.

The disagreements found below are *inside* that single payload, between the settings
store and the residency planner — a different seam.

### Licence exposure: currently clean

- `model_plan.VAULT_MODELS == ()` — verified in source and asserted by
  `tests/unit/test_model_plan.py:285`.
- `model_plan.plan()` is a pure function of a hardware profile over a static ladder.
  It cannot recommend a model merely because it is installed; live inventory is only
  used by `_have()` to mark what is already present.
- No occurrence of `TwIL`, `TwiL`, `twil` or `webAI` anywhere in `src/`.

So the planner and the installer do **not** offer TwiL to anyone. The exposure is
confined to Stephen's own machine, which the licence permits. The constraint to hold
going forward: whatever makes TwiL deliberate must not touch `model_plan`, the
installer manifest, or `packaging/`.

### There is no verifier seat to put it in — and `judge` is not it

`services/local_seats.py:37-43`:

```python
_ROLE_TO_CAPABILITY = {
    "brain": "reasoning",
    "judge": "reasoning",
    "sidekick": "subagent",
    "extractor": "subagent",
    "heavy": "heavy_hitter",
}
```

A `judge` role exists, but reading its consumer (`services/judgment_gate.py`) it is the
**egress privacy judge**: it decides whether a span of text is private before it leaves
the machine. That is a classification job, and it is a *gate*. Seating an entailment
checker there would be wrong twice over — wrong task, and wrong authority, given the
evaluation's explicit "never a gate" finding.

Note also that `judge` maps to the `reasoning` capability key. It has no seat of its
own; it borrows the conversational brain.

**There is no advisory reasoning-checker role in the seat contract.** That is the gap.

---

## Part 2 — The Intelligence warnings

### What is actually on screen: 13 amber boxes

All 13 render under one heading, **"WHAT WILL NOT FIT RIGHT NOW"**, in
`index.html:32587-32597`, one identically-styled amber box each. They come from
`machine.refusals`, which `routes/intelligence.py:238` passes through from
`/api/residency/status` **unfiltered**.

Live list, verbatim:

| # | Rule | Role | Text |
|---|------|------|------|
| 1 | R3 | sidekick | `gemma4:e2b` — no GPU has room beside the pinned brain: needs 1811 MiB, largest remaining budget is 0 MiB |
| 2 | R6 | heavy_hitter | `claude opus 5` — override names a model that is not installed; installed: … |
| 3 | R3 | interactive_brain | `gemma4:12b` — override needs 7814 MiB but only 0 MiB is available… |
| 4 | R3 | memory_manager | `Gemma 4 E4B …Aggressive` — override needs 3239 MiB but only 0 MiB is available… |
| 5 | R3 | orchestrator | `gemma4:12b` — override needs 7814 MiB but only 0 MiB is available… |
| 6 | R6 | researcher | `claude opus 5` — override names a model that is not installed; installed: … |
| 7 | R3 | sidekick | `gemma4:12b` — override needs 7814 MiB but only 0 MiB is available… |
| 8 | R3 | sidekick_fast | `gemma4:12b` — override needs 7814 MiB but only 0 MiB is available… |
| 9 | R6 | sidekick_heavy | `claude opus 5` — override names a model that is not installed; installed: … |
| 10 | R11 | orchestrator | — for orchestrator — no model assigned; this seat is chosen by the user… |
| 11 | R11 | sidekick_fast | — for sidekick_fast — no model assigned; this seat is chosen by the user… |
| 12 | R11 | memory_manager | — for memory_manager — no model assigned; this seat is chosen by the user… |
| 13 | R11 | researcher | — for researcher — no model assigned; this seat is chosen by the user… |

### The finding that reframes the job

**Ten of the thirteen are one arithmetic bug, and none of them is true.**

`/api/residency/status` reports, for the only GPU:

```
total_mib      12282
baseline_mib   13831      ← larger than the whole card
available_mib      0      ← max(0, 12282 − 1024 − 13831)
```

`nvidia-smi` at the same moment: **12282 MiB total, 1347 MiB used, 10666 MiB free.**
The Settings page prints "10.1 GB free of 12 GB" two lines above nine boxes that say
0 MiB is available.

Root cause is `hardware_profile.live_display_mib()` (line ~282). It sums the WDDM
performance counter `\GPU Process Memory(*)\Dedicated Usage` across every process that
is not `llama-server`, `ollama` or `python`. That counter is not bounded by physical
VRAM. Re-running the exact query now:

```
chrome                  25808 MiB   (4 counter instances)
dwm                       370
msedgewebview2             84
explorer                   41
…
TOTAL SUMMED            26459 MiB   on a 12282 MiB card
```

Chrome alone claims twice the card. The value stored in the profile when it was last
sampled was 13831; it is 26459 now. There is no clamp against `vram_total_mib`, so
`effective_baseline_mib()` returns it (it exceeds the measured idle floor), and
`gpu_budgets()` floors the result at zero.

The cascade:

1. Bad baseline → every GPU budget is 0 MiB.
2. Every GPU seat refuses R3 → boxes 1, 3, 4, 5, 7, 8.
3. Seats left unplaced → the `ASSIGNED_ROLES` loop at `residency_policy.py:891` sees
   `seats[role] is None` and appends R11 → boxes 10–13.

**The R11 text is false right now.** Every one of those four roles *does* have a model
in `capability_routing`: orchestrator = `gemma4:12b`, sidekick_fast = `gemma4:12b`,
memory_manager = `Gemma-4-E4B-…-Aggressive`, researcher = `claude-opus-5`. The seat was
not left unchosen; it failed to be placed. Boxes 5 and 10 are the same role
contradicting itself in the same list.

This also means the mechanism built to tell the two states apart is dead. In
`routes/intelligence.py:319-328`, `refusal_for` keeps only the **first** refusal per
role. For orchestrator that is the R3 (box 5), so `awaiting_choice` computes as `False`
and the role row shows a blocked message instead of "This seat is yours to choose."
Live payload confirms: `awaiting_choice = False` on all sixteen roles.

**So the brief's middle category — "unmade choices" — is currently empty.**
`function_manager` in particular emits no refusal at all; it holds `functiongemma:270m`
and renders green. The R11s that are firing are not unmade choices. They are
misdescribed failures.

### Classification

Applying the test "can anything the user does clear this?"

**Category A — real problems (2 distinct issues, 3 boxes)**

- **Boxes 2, 6, 9** — `claude-opus-5` assigned to `heavy_hitter`, `researcher` and
  `sidekick_heavy`, refused by the *local* residency planner as "not installed". A
  cloud model is being judged against a local VRAM plan. The user can act (pick a local
  model, or the planner should skip cloud-seated roles), so it passes the clearability
  test — but the message is wrong about why. Listing eleven installed local tags at a
  user who deliberately chose a cloud model is not an instruction, it is a dump.
  **Also note:** those "installed:" lists name `gemma4:12b`, `gemma4:26b`, `gemma4:e2b`,
  `gemma4:e4b` — none of which are in Ollama's inventory — and omit every model that
  is. The planner is reading a different backend's inventory (llama-server) and calling
  it "installed" without saying so.

**Category B — not warnings at all, currently misfiring (10 boxes)**

- **Boxes 1, 3, 4, 5, 7, 8, 10, 11, 12, 13** — all downstream of the baseline bug.
  Nothing the user can do in this panel clears them; the only fix is in code. By the
  clearability test these are developer instructions that leaked into the interface.
  They should not be recoloured — they should stop existing, by fixing the number.

**Category C — informational**

- Empty at present. The heading "What will not fit right now" is itself the
  informational frame, and it is currently lying.

### The other thing on that screen

`resident_llama_server` reports `gemma4:12b` as resident, and the page prints
"Loaded now: embeddinggemma:300m, functiongemma:270m, gemma4:12b, gemma4:26b,
gemma4:e4b, z-image-turbo-fp8". The card is holding 1347 MiB. A single `gemma4:12b`
is ~7.8 GB. Those models are not on the GPU. The arbiter's residency belief and the
hardware disagree — the "degraded-pin" problem the code already names at
`residency_policy.py:146-150`, showing up in the UI as fact.

### On the stated principle

The "ten warnings is wallpaper" formulation is **not** in `KNOWN_ISSUES.md`, and there
is no tool-disclosure precedent recorded there. What *is* there is the same reasoning,
twice:

- line 29 — a whole API surface dead for seven weeks and ~70 restarts: *"It logged one
  warning per boot. Nobody read it."*
- line 247 — the blueprint policy announces unavailability *"rather than logging a
  warning nobody reads."*

And §1's governing rule, line 47: **"Nothing in Friday may claim success it has not
verified."** Thirteen boxes asserting 0 MiB against a card with 10.4 GB free is the
inverse of that rule and the same defect class §1 exists to name — a comparison whose
inputs were never checked against the thing they describe.

I would rather flag the absence than proceed on a premise I could not confirm.

---

## Recommendation on sequencing

The brief scopes job 2 as "a UI and copy change, don't restructure the seat system".
That scope does not fit what is there. Ten of thirteen boxes are one wrong number.
Recolouring them, softening them, or grouping them would hide a live planning fault
that is currently preventing seats from being placed at all — and would train exactly
the ignore-the-yellow reflex the job exists to stop.

Suggested split, for decision:

1. **Fix the baseline** (`hardware_profile`): clamp the display reserve to something
   physically possible, and prefer the `nvidia-smi` used-memory reading over the WDDM
   counter sum, or subtract known GPU-process instances properly. Small, contained,
   testable. Ten boxes disappear on their own.
2. **Then** do the UI and copy pass on what survives — which will be a much shorter
   list, and one where the three-way classification actually has members.
3. **Separately**: stop the local residency planner adjudicating cloud-seated roles,
   and make "installed:" name which backend it means.
