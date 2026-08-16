# What the running machine actually does — 2026-08-15

Live verification of the six components built today. Server restarted, real turns driven
through real seats, output captured. Written to be checkable, including where it went wrong.

Branch `phase-a-truth-flow`, unpushed.

---

## 1. The context window expanded, and it is live on the dispatch path

The plan, read from the running server (`GET /api/residency/status`, a route that did not exist
this morning):

```
overhead: tools=8118  system=11681  total=19799  (measured)

ROLE               MODEL                  CTX      VRAM     STATUS   BASIS
interactive_brain  gemma4:12b             131072   7814     pinned   measured
sidekick           gemma4:e2b             32768    1811     pinned   measured
sidekick_heavy     gemma4:e4b             65536    3081     leased   below-range
heavy_hitter       gemma4:26b             32768    8586     leased   below-range
embedder           qwen3-embedding:0.6b   2048     0        resident -
pinned: {'gpu:0': 9625}
```

A real chat turn, through `/api/chat`, no special flags:

```
$ curl -X POST /api/chat -d '{"message":"Reply with exactly one word: ready."}'
{"model":"gemma4:12b","response":"ready.","seat":"local", ...}
real 0m16.242s

$ ollama ps
gemma4:12b   ctx=131072   7813 MiB   100% GPU
```

**131 072, on the real path, at 100% GPU.** Working room went from ~12 552 tokens to ~111 273 —
a 4× increase for 96 MiB, exactly as the KV curve predicted. The overhead figure is *measured*
at plan time, not a constant, so it tracks the system prompt as it grows.

## 2. Local models are not worse at tool chains. Our loop was dropping their arguments.

A dependent 4-call task through Friday's real dispatch, five repeats each:

| Model | Correct | Reached the last call | Median |
|---|---:|---:|---:|
| `gemma4:e2b` | 5/5 | 5/5 | 10.6 s |
| `gemma4:e4b` | 5/5 | 5/5 | 12.1 s |
| `gemma4:12b` | 5/5 | 5/5 | 13.8 s |

The first run scored **0**. `_oai_agentic_loop` did `json.loads(fn["arguments"])`; OpenAI sends
that as a JSON string, Ollama's `/api/chat` sends an already-parsed object, `json.loads()` on a
dict raises, and a bare `except` substituted `{}`. **Every local tool call ran with no arguments,
silently.**

```
before:  tool get_project <- {}          (model emitted {"name":"alpha"})
         "I cannot find the email address because the tool calls ... failed"
after:   tool get_project <- {'name': 'alpha'}
         dana@example.com
```

Verified against the raw daemon that the models emitted correct arguments every time. This is the
second time in two days that "the model can't use tools" has turned out to mean "we broke the
tools" — the honesty gate condemned four models on the same class of evidence.

## 3. The proposal flow, end to end, against the live server

```
1. Friday proposes a 3-step workflow, one step reading the vault
   options offered : ['when_away', 'now_local']
   blocked         : now_cloud — "1 task(s) read vault-tier material, which never
                     leaves this machine: Cross-reference my private notes..."
   totals          : ~134s local / ~39s cloud
   choose-for-me   : now_local — "some of this reads your vault, so it stays on
                     this machine. Roughly 2m of local work."

2. Choosing the cloud anyway
   HTTP 409, with the reason. Not a silent downgrade.

3. Parking it instead
   HTTP 200, 3 items queued

4. The queue, ordered by latency class
   reflex       List the files                    when_away
   interactive  Cross-reference my private notes  when_away  [vault]
   heavy        Summarise each paper              when_away
   heavy batch: "1 item(s) waiting until you are away; idle for 160s of the 900s needed"

5. Cancelled — pending now: 0
```

Everything Stephen asked for is in that transcript: the steps laid out, the three options, an
unavailable one shown *with its reason*, "choose for me" saying what it chose and why, and a
queue that explains its own stillness rather than looking broken.

## 4. What went wrong — the pinned pair does not stay pinned

The first boot after the restart left the brain missing. The new status route diagnosed it in one
call:

```
transitions: load-pinned-degraded  interactive_brain  gemma4:12b  21.21s
             load-pinned-degraded  sidekick           gemma4:e2b  18.44s
             boot                  plan                           39.69s
resident:    {'gemma4:e2b': 1811}
drift:       [{"role":"interactive_brain","problem":"planned as pinned but not resident"}]
```

The plan was right and both seats loaded. **Ollama then evicted the 12b when the e2b arrived** —
R9's documented failure, the exact behaviour the Arbiter exists to prevent.

It happens because neither seat has a GGUF the Arbiter can serve itself. `llama-server` refuses
Ollama's multi-blob storage (`wrong number of tensors; expected 2012, got 601`), so both fall to
the **degraded pin** path, where placement is delegated to the daemon's own scheduler and R9 is
unenforced. The seat carries a `pin_unenforced` note saying exactly this; nothing was hiding it,
but nothing was surfacing it either.

**Consequence in practice:** the brain is not resident between turns. It cold-loads on the first
turn — 16.2 s in the measurement above, of which ~13 s is loading — and then evicts the sidekick.
The machine works; it is not *placed*.

**This is not fixed.** Options, in order of preference:

1. Extract single-file GGUFs for the 12b and e2b so the Arbiter can own their processes and R9
   becomes enforceable. Costs disk, and disk is already an R8 constraint.
2. Raise Ollama's own loaded-model allowance so it stops evicting inside a budget that fits.
   Cheap, but it is asking the daemon nicely — the thing R9 exists because we cannot do.
3. Accept the cold load and say so in the UI.

Needs a decision. It is a pre-existing condition, not something today's work caused, but today's
work is what made it visible.

## 5. Test suite

`tests/unit` + `tests/api` green, except one pre-existing `XPASS(strict)` in
`test_notifications_engine` that turns on whether two writes land in the same wall-clock second.

Along the way, 12 tests asserting the removed seat gate — red since the gate came out — were
rewritten to pin the inverse. A permanently red suite stops being a signal.

## 6. R10: the sidekick through a lease

The Arbiter no longer evicts the sidekick on either lease kind, and the plan subtracts its
1 811 MiB from the lease budget (9 997 → 8 186 MiB), which moves the heavy model's offload point.

*Sweep in progress at the time of writing; see the follow-up commit for the measured
`--n-cpu-moe` operating point and whether Z-Image still generates with 1 811 MiB held back.*

## 7. Still unverified

- The `--n-cpu-moe` operating point for the 8 186 MiB lease budget. The plan currently asks for
  32 with basis `extrapolated`, which is a starting guess for a sweep, not a result.
- Whether Z-Image still generates with the sidekick retained.
- The workflow panel rendered in a browser. It builds (JSX precompiles cleanly, which is the
  check that catches the silent-mount failure) and every endpoint behind it is verified above,
  but nobody has looked at it.
- The away-drain firing on its own after 15 minutes of real idleness.

---

## 8. The workflow panel, rendered

Settings → Work, read out of the running page rather than asserted:

```
Rewrite the research notes            4 steps · 1 heavy
1 List the source files               reflex · ~2s local · ~4s cloud
2 Summarise each paper                heavy · gemma4:26b · ~2m local · ~33s cloud
3 Draft the overview                  chat · ~20s local · ~17s cloud
4 Cross-reference my private notes 🔒 vault
                                      chat · ~9s local · ~8s cloud
total ~3m locally · ~62s in the cloud

[🌙 While I'm away]  [🏠 Now, locally]  [☁️ Now, in the cloud — DISABLED]

  cloud button: "unavailable — 1 task(s) read vault-tier material, which
  never leaves this machine: Cross-reference my private notes. The router
  forces those local regardless of the configured mode, so a cloud run would
  not do what the label says."

[✨ Choose for me]  would pick Now, locally — some of this reads your vault,
                    so it stays on this machine. Roughly 3m of local work.
[Not now]
```

Every element asked for is there: the steps Friday intends to execute, per-step config, three
options, the unavailable one shown **with its reason** rather than hidden, and a "choose for me"
that states its pick before you click it. Queue section reads live —
`0 parked · idle 8m · you are here — away-work is holding`.

Verified by reading rendered page text. A screenshot was not possible: the browser pane is not
being displayed in this session, so the page is not compositing frames.

## 9. R10 measured — and the cost is CPU, not VRAM

Sweeping `--n-cpu-moe` **with the sidekick resident**, which is the condition the number has to
hold under. Sidekick answers in **0.61 s** with nothing else running:

| `n_cpu_moe` | heavy on GPU | total GPU | heavy tok/s | sidekick answers in |
|---:|---:|---:|---:|---:|
| 18 | 7 973 MiB | 11 663 MiB | 10.58 | **1.24 s** |
| 20 | 6 014 MiB | 9 704 MiB | 11.47 | 24.08 s |
| 28 | 2 462 MiB | 6 152 MiB | 10.29 | 22.69 s |

**Friday stays awake and answering while the heavy model works — R10 holds.** That was the
requirement and it is met.

Three things this changed my mind about:

1. **My extrapolation was badly wrong.** The policy asked for 32 layers on the basis of a linear
   fit through two points. The curve is not linear: 28 puts only 2 462 MiB on the card against an
   8 186 MiB budget, wasting 5.7 GB to run *slower*. The plan labelled it `extrapolated`, which is
   why it was checkable — but it was a guess and the guess was poor.
2. **Pushing work onto the CPU to save VRAM is what makes Friday unresponsive.** The sidekick
   answers in 1.24 s at 18 layers and 22–24 s at 20 and 28. More expert layers on the CPU means
   the CPU is saturated, and the sidekick needs CPU too. The instinct to "free up VRAM for the
   sidekick" is exactly backwards.
3. **Holding the sidekick costs the heavy model a lot of speed** — 27.80 tok/s previously at
   `n_cpu_moe 20` versus 11.47 here. **Caveat, and it matters:** the earlier figure was a median
   of five warm runs on an otherwise idle machine; these are single runs with a browser and
   preview pane also on the card (baseline 3 690 MiB rather than 712 MiB). Some of that gap is
   the sidekick and some is everything else. **Not cleanly attributed** — do not quote the 2.4×
   as the cost of R10 until it is re-measured on a quiet machine.

Current best operating point on this evidence: **18**, which fits the budget and keeps the
sidekick fast. It is one run per candidate, so it is a direction, not a settled number.
