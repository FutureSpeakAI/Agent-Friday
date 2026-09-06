# Install readiness — 8 GB machines (RTX 4060), v5.6.0

> ## ⚠ Re-check 2026-08-29 — read this before the body
>
> This note was written **2026-08-25 against v5.6.0**. The repository has since
> shipped 5.6.1 through **v5.7.0**, and **its central recommendation has been
> implemented**. Claims the code has since falsified are marked inline as
> **[SUPERSEDED 2026-08-29]** or **[CORRECTED 2026-08-29]**. The argument has not
> been rewritten — only the facts under it.
>
> | This note said | Today |
> |---|---|
> | Don't let the installer pull `gemma3:4b`; run with `-SkipOllama` | **Implemented.** `install.ps1` now asks outright — "use your Claude key" vs "also download a local model" — and defaults to cloud-first on any card that can only hold the floor rung. An 8 GB 4060 has 5.5 GiB usable, so the pick is `qwen3:4b`, `$localIsComfortable` is false, and **cloud-first is the default on exactly this hardware**. `-SkipOllama` still works but is no longer required knowledge. |
> | Add `gemma4:e4b` to `BRAIN_MODELS` | **Superseded.** `e4b` measured 9.61 GB on disk and was rejected. The ladder is now `qwen3:4b` (2.50 GB, tool-capable) → `qwen3:8b` → `gemma4:12b` → `qwen3:14b` → `qwen3:32b`. |
> | The worst configuration on an 8 GB card is the default one | **No longer true**, and it is the same fix: the default pull is a tool-capable model, and `gemma3:4b` stays in the table **only so the planner can recognise and refuse it** (`tools: False`, so `_pickable()` can never select it). |
> | Every shipped default names `gemma3:4b` by hand (defect H3) | **Fixed.** `FLOOR_MODEL` is derived (`next(m["id"] for m in BRAIN_MODELS if m["tools"])`) and `cli.BUNDLED_MODEL` imports it. A test forbids any shipped default naming a model that cannot call tools. |
> | The installer has never been run for real, here or anywhere | **False now.** A rehearsal harness lives in `packaging/windows/tests/rehearsal/` (commit `09b8114`), and 5.6.5, 5.6.6 and 5.7.0 were each proved by installing a *published* build and upgrading into it. |
> | `RELEASE_NOTES.md` is still 5.5.0 text | **Mostly fixed.** Now v5.6.6 — one release behind, not five. |
> | `model_plan.py` says tools are disabled on a non-tool model | **Fixed.** The line now states she does not disable them and points at the note explaining why. |
> | The usable-VRAM arithmetic (4,604 MiB) | **Recomputed.** Runtime overhead is now folded into each model's own footprint instead of being subtracted a second time as a separate reserve — the old figures double-counted it. The installer computes `card − 2.5 GiB`; on this card, 5.5 GiB. **The *tier* argument is unaffected**: a fixed reserve is still a far larger share of an 8 GB card than of a 12 GB one. |
>
> **Re-checked and still true:** finding 1's routing table, finding 5 (no
> residency seat is assigned on non-reference hardware, silently), finding 6's
> underlying behaviour (the registry is still passed with no capability check),
> the NeMo/voice section, and every "Not verified" item at the foot of this
> document. Those remain open.

**Answer: yes, via the cloud-first path Stephen described — with one specific
change to how the install is run.** An earlier revision of this note led with
"no". That was wrong, and it was wrong because it assessed the *local* seat and
treated the cloud seat as the fallback rather than as the day-one product.

Occasion: the first time anyone has asked whether Friday runs on hardware other
than the author's. Reference machine throughout is P1 — RTX 4070, 12,282 MiB,
32,620 MiB RAM. Target is an RTX 4060, 8,188 MiB.

**Method, and its limit.** Everything marked *verified* was produced by
executing the shipped code — the planners and the router are pure enough to
drive directly. No clean Windows box was available, so the installer was never
run end-to-end and no model was pulled or prompted.

---

## The headline, in three findings

### 1. "No local model" is a SUPPORTED configuration, not an error condition

This is the question that decides whether cloud-first works, and the answer is
good. Executed against `ModelRouter` with the shipped defaults
(`vault_local_only: True`, `vault_cloud_fallback: "redact"`):

| State | Route | `refuse` | Outcome |
|---|---|---|---|
| **No local model at all** (the 8 GB machine, cloud-first) | `cloud` / `claude-sonnet-5` | `False` | works; vault content redacted downstream |
| Model present, seat dead (Stephen, this morning) | `local` / `gemma4:12b` | `False` | **single local attempt, no cloud fallback → fails** |
| No local model, `deny` fallback (not the default) | `cloud` | `True` | refuses |

**The two states invert.** `_route_vault` only force-routes local when
`_local_candidates()` returns something. With an empty list it falls through to
the `redact` branch and routes cloud. So **having zero local models is strictly
safer than having a broken one** — Stephen's morning failure is unreachable on a
machine that never had a local seat to break.

The vault path does not treat a missing local seat as failure. It treats it as
"there is nothing local to route to", which is the correct reading.

### 2. Self-healing is real, and it is genuinely Claude-first — at install time

Stephen's memory is accurate. `packaging/windows/lib/Heal.ps1` is a real
Claude-powered repair loop: `model = 'claude-sonnet-5'`, 12 heals max, 25-minute
budget, a fixed 13-item remediation menu, and validators that refuse anything
the model returns which isn't on the menu.

And the key is genuinely **first**. Step 2 of the installer — before Python,
before dependencies, before Ollama, before models:

> "Friday can use a Claude key to fix problems by herself if setup runs into
> one, instead of stopping and asking you to sort it out."

It then carries the key into `$env:ANTHROPIC_API_KEY` (process scope only,
never written to disk) so the setup wizard pre-fills and she is not asked twice.
That is Claude-first onboarding at the install layer, already built, working as
he remembers.

### 3. But healing repairs *installation*, not *capability* — and has never run

The 13 remediations are: install a missing pip package, retry pip with
different flags, refetch a corrupt download, free or change a port, fix a file
permission, create a missing directory, clear the pip cache, repair the Python
`.pth`, start Ollama, pull a missing model tag, wait and retry, skip an optional
step, give up with a plain message.

**There is no VRAM remediation and no step-down-to-a-smaller-model.**
`pull_missing_model` fetches a tag *already in this install's plan*
(`Assert-ModelTag` refuses anything outside `HealAllowedModelTags`). It cannot
diagnose "this card is too small" and elect a different model.

It also runs **only during the installer** — it is PowerShell in
`packaging/windows/lib/`, invoked by `Invoke-Step`. Once setup finishes, nothing
heals.

**And it has never executed.** *[CORRECTED 2026-08-29: true when written, false now. `packaging/windows/tests/rehearsal/` drives real installs, and 5.6.5/5.6.6/5.7.0 were each proved by upgrading a published build in place. `%LOCALAPPDATA%\AgentFriday` is still absent on this machine — the rehearsals use a redirected install root — so the sentence below is right about the path and wrong about the conclusion.]* There is no install at
`%LOCALAPPDATA%\AgentFriday` on Stephen's own machine — the installer has never
been run for real, here or anywhere. `Test-Installer.ps1` covers the *validators*
(hostile paths, bad tags, argument quoting are all refused correctly); it does
not drive a single failure → Claude → remediate → verify cycle.

**This does not damage the plan.** Healing never needed to step down on 8 GB,
because the planner already picks correctly for 8 GB (finding 4). The gap is not
in healing.

---

## What a Claude-key-only first run actually gives her

Walked through the code, day one, fresh `~/.friday`:

**She types:** her Claude key at Step 2 (arms self-repair), then the same key is
pre-filled in the setup wizard, plus a name and a vault passphrase (skippable,
can be auto-generated).

**Works on day one:**
- Chat, on `claude-sonnet-5`, with the **full tool registry** — the cloud path
  passes `CLAUDE_TOOLS` and runs the full agentic loop.
- Files, news, web search, wiki writes, tasks, workspaces.
- Memory/embeddings — `all-MiniLM-L6-v2` runs on CPU via `sentence-transformers`,
  a declared pip dependency. No GPU, no LLM needed.
- Voice — Tier-1 CPU (`faster-whisper` + Piper). See below; this is genuinely
  fine on her card.

**Does not work on day one:** local/offline conversation, local image generation.
Both are downloads she can add later.

**The nuance worth stating rather than hiding:** as she accumulates personal
wiki content, that content is classified `_T2` at minimum
(`model_router.py:2531`, the fail-closed fix from this release) and is therefore
**withheld from cloud prompts by design**. On day one her wiki is empty, so
nothing is withheld and the question does not arise. Later, that withholding is
the sovereignty guarantee doing its job — and it is the real reason to add a
local seat. That is a coherent upgrade story, not a defect: *the cloud seat runs
the product; the local seat unlocks your private corpus.*

---

## The one change that makes this work — and it is counterintuitive

> **[SUPERSEDED 2026-08-29 — this section describes a problem that no longer
> exists.]** The installer now asks the cloud-first question outright and
> defaults to cloud-first on an 8 GB card, and the model it would pull is
> tool-capable either way. Kept because the *reasoning* is why the fix took
> the shape it did.

**Do not let the installer pull `gemma3:4b` on her machine.**

Left alone, the installer pulls it (Step 9, ~3.3 GiB), which puts her in the
*"model present"* state — the bad one from finding 1. Vault-touching turns then
force-route to `gemma3:4b` instead of Claude. And `gemma3:4b` has
`tools: False`, so those turns land on a model that cannot call tools, with the
full registry passed to it anyway (see finding 6).

So the worst configuration on an 8 GB card is the *default* one, and the fix is
to install with **no local model at all** until she wants one:

- Run the installer with `-SkipOllama`, **or** decline the model step, **or**
  remove the tag afterwards.
- Everything routes cloud. The vault path takes the `redact` branch. Tools work.
- Later, when she wants offline: pull a tool-capable model that fits (below) and
  it becomes the local seat.

That is a one-flag change, not a rebuild.

---

## The 8 GB arithmetic (unchanged — this is the roadmap, not the blocker)

Two independent planners agree within 4 MiB.

```
  8,188 MiB  card total (nvidia-smi on a 4060 8 GB)
- 2,560 MiB  MIN_DISPLAY_RESERVE_MIB["windows"]   (hardware_profile.py:248)
- 1,024 MiB  VRAM_RESERVE_MIB, rule R3            (residency_policy.py:110)
-------------
  4,604 MiB  usable
```

`model_plan` independently: `8.0 - 2.5 - 1.0 = 4.5 GiB` = 4,608 MiB.

The fixed 3,584 MiB reserve is **43.8 %** of her card against **29.2 %** of
Stephen's. Same absolute reserve, nearly half her card. 8 GB is a different
tier, not a smaller one.

| Model | Needs | Fits 4,604? | Tools |
|---|---|---|---|
| `gemma3:4b` | 3,379 MiB | yes | **no** |
| `gemma4:e4b` | 3,081 MiB *(measured, 8,192 ctx, P1)* | yes, ~1.5 GiB spare | yes |
| `qwen3:8b` | 5,325 MiB weights alone | **no** | yes |
| `gemma4:12b` | 8,745 MiB *(measured, P1)* | **no** — 1.9x her whole card | yes |

### 4. The install planner is sound

Executed against the 4060 profile at 8, 16 and 32 GiB RAM: it picks `gemma3:4b`
every time and refuses local image generation with the arithmetic. **Nothing
impossible is chosen.** The "largest model on disk wins" defect Stephen
remembers lives in the runtime arbiter's heavy-hitter pick, where rule R6
refuses it. `services/model_plan.py` is a different, sounder piece of code — and
the installer correctly defers to it rather than hardcoding a tag.

**Recommendation for when she wants a local seat:** *[SUPERSEDED 2026-08-29 — `gemma4:e4b` was measured at 9.61 GB on disk and rejected; the ladder now starts at `qwen3:4b`, 2.50 GB and tool-capable. Original recommendation kept below as written.]* add `gemma4:e4b` to
`BRAIN_MODELS`. 3,081 MiB measured at 8,192 ctx on P1, gemma4 family so tool
calling is present, ~1.5 GiB spare. KV growth on this family is near-flat (e2b
moves 1,763 → 1,811 MiB across 8,192 → 32,768), so a 16,384 seat — above the
~8,534-token tool registry, so definitions are not truncated — should land near
3,130 MiB. *Verify by measuring on a 4060; those numbers are P1's.*

---

## Two defects that remain, now scoped as roadmap

### 5. No residency seat is assigned on non-reference hardware — silently

Seed VRAM measurements are keyed by machine fingerprint; the only key present is
`"NVIDIA GeForce RTX 4070|12282|32620"`. The 4060 machine's is
`"NVIDIA GeForce RTX 4060|8188|16384"`, so every model is unmeasured.
`context_for()` then returns `vram_mib: null`, and the `interactive_brain`
qualification (`residency_policy.py:736`) requires it non-null. `brain` stays
`None`, and because the refusal is recorded *inside* the `if brain is not None:`
block, **no refusal is emitted**.

It is a deadlock, not a cold start: `_measure_resident` is the only writer of
measurements and both its call sites take a loaded seat. No measurement → no
seat → no load → no measurement. No CLI or script escape hatch exists.

**Impact under cloud-first: near zero**, which is why this is roadmap and not a
blocker. With no local model there is no seat to assign anyway. It only bites
once she adds a local model, at which point the residency layer stays inert and
every local turn pays a cold load (~20 s) via the Ollama fallback path.

### 6. The installer says tools are disabled on a non-tool model. They are not.

`model_plan.py` prints "lacks native tool calling, so Friday disables tools for
local turns". At runtime `_via_ollama` (`agent.py:226`) passes the full
budget-trimmed registry with **no capability check**, and `model_router.py:458`
says so directly: *"the seat gate is GONE … What replaces it is nothing at
dispatch time."* That was a defensible decision; the user-facing sentence was
just never updated. The real mitigation —
`tool_integrity.find_pseudo_toolcalls` plus a corrective-note retry — is genuine
but is detection, not prevention.

### Minor

- The installer prints `OK  Managed model seats (residency layer)` on a 16 GiB
  box. That is a RAM check only; per finding 5 the layer places nothing.
- *[CORRECTED 2026-08-29: `RELEASE_NOTES.md` is now v5.6.6 — one release behind `v5.7.0`, not five. The point below stood when written.]*
  `RELEASE_NOTES.md` is still 5.5.0 text pointing at `AgentFriday.exe` — the
  6 July v5.4.0 build, Layers 1a+1b only. `docs/INSTALLATION.md` is correct and
  blunt about this; the release notes contradict it.

---

## Voice: the NeMo concern does not apply

**NeMo is not installed by the Windows installer.** It appears in no
requirements tier — `core.txt`, `memory.txt`, `recommended.txt`, `wheelhouse.txt`
all read in full. Its only occurrence in `packaging/windows/` is a path in the
*uninstaller's* cleanup list.

What ships is Tier-1 CPU voice: `faster-whisper`, `piper-tts`, `onnxruntime`.
**None of it touches VRAM.** Voice is straightforwardly fine on her card, and it
works on a cloud-first install.

Pre-warmed at install rather than mid-sentence: MiniLM ~90 MB, faster-whisper
~460 MB, Piper ~60 MB — about 610 MB, on top of ~2.5 GB of Python dependencies.

---

## What she gets that Stephen does not have to think about

- **Honest degraded-layer report.** `privacy_layers.describe()` says
  `"N/4 layers active … DEGRADED — not running: X"` and refuses to say four
  unless four are loaded.
- **`docs/INSTALLATION.md`** states the two build variants are not equivalent
  privacy products and that no model downloads behind her back.
- **`docs/FILE_GRANTS.md`** documents the permission model.
- **The installer defers to the app's own planner** and verifies every step
  through a `-Verify` block rather than trusting the command's exit code.
- **Onboarding is real and nothing is mandatory** — keys and vault passphrase
  are both skippable.

---

## To close the gap properly

Cloud-first works today with the `-SkipOllama` change. To make it the *designed*
path rather than a flag someone has to know about:

1. ✅ **DONE (shipped by 2026-08-29).** **Make cloud-first an explicit install choice.** One question after the key:
   *"Run Friday on your Claude key for now, or also download a local model?"*
   Default to cloud-first when usable VRAM is under ~5 GiB. Today the default
   silently produces the worse configuration on an 8 GB card. — ~half a day
2. ✅ **DONE differently (2026-08-29)** — the floor rung is `qwen3:4b`, not `gemma4:e4b`. **Add `gemma4:e4b` to `BRAIN_MODELS`** so the later local upgrade lands on a
   tool-capable model. — ~half a day
3. **Record a refusal when the brain seat goes unfilled** (finding 5). Even
   without fixing the deadlock, end the silence. — ~1 day for the full fix,
   ~1 hour for the refusal alone
4. ✅ **DONE (2026-08-29).** **Fix the tools sentence** in `model_plan.py`. — ~1 hour
5. ◐ **PARTLY DONE (2026-08-29)** — regenerated through v5.6.6, still one release behind. **Regenerate `RELEASE_NOTES.md` for 5.6.0.** — ~1 hour
6. ✅ **DONE (2026-08-29)** — via the rehearsal harness against published builds, not a physically clean box. **Run the installer once on a clean box** — including one deliberate induced
   failure, to exercise the healing loop that has never run. — ~half a day

Items 1 and 2 are what stand between today and handing this over deliberately
rather than with a caveat. Items 3–6 are follow-up.

---

## What was verified, and how

| Claim | How |
|---|---|
| No local model → cloud, `refuse=False` | executed `ModelRouter.route()`, empty `_local_candidates` |
| Model present + dead seat → local only, no fallback | executed; reproduces this morning's message |
| `deny` fallback refuses; `redact` (default) does not | executed both |
| Healing is Claude-powered, key asked at Step 2 | read `Heal.ps1:123`, `install.ps1:176–222` |
| Menu has no VRAM / step-down remediation | enumerated all 13 entries |
| Healing is installer-only | only `install.ps1` loads `Heal.ps1`; `Common.ps1` stubs it absent |
| Healing has never run | no `%LOCALAPPDATA%\AgentFriday`; tests cover validators only |
| Planner picks `gemma3:4b` on 8 GB | executed `model_plan.plan()` @ 8/16/32 GiB RAM |
| `primary_capable = False` on 8 GB | executed `brain_is_primary_capable()` |
| The 4060 machine has zero seed measurements | executed `profile_fingerprint()` vs `SEED_MEASUREMENTS` |
| No `interactive_brain` seat, no refusal | executed `residency_policy.plan()`, 3 model sets |
| Only `_measure_resident` writes measurements | repo-wide grep; 2 call sites, both take a seat |
| Tools passed with no capability check | read `agent.py:226`, `model_router.py:458–472` |
| Wiki context floors at `_T2` | read `model_router.py:2531` |
| NeMo absent from installer | read all four requirements tiers in full |

Not verified: the installer end-to-end on clean Windows; the healing loop
against a real failure; `gemma4:e4b` on a 4060; whether `gemma3:4b` fabricates
tool calls in practice; the real dwm reserve at her monitor count.
