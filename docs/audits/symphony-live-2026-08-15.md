# What the running machine actually does — 2026-08-15

Live verification of the six components built today. Server restarted, real turns driven
through real seats, output captured. Written to be checkable, including where it went wrong.

Branch `residency-policy`, unpushed.

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
Measured in §9.

## 7. Still unverified

- Whether Z-Image still generates with the sidekick retained.
- The away-drain firing on its own after 15 minutes of real idleness.
- Whether the sidekick's 22–24 s probes are Ollama evicting and reloading it (§9). The cold-load
  arithmetic fits, but nothing confirms it.

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
| 22 | 7 969 MiB | 11 659 MiB | 10.30 | 24.73 s |
| 24 | 7 895 MiB | 11 585 MiB | 9.44 | 23.43 s |
| 28 | 2 462 MiB | 6 152 MiB | 10.29 | 22.69 s |

**Friday stays awake and answering while the heavy model works — R10 holds.** That was the
requirement and it is met.

Three things this changed my mind about:

1. **My extrapolation was badly wrong.** The policy asked for 32 layers on the basis of a linear
   fit through two points. The curve is not linear: 28 puts only 2 462 MiB on the card against an
   8 186 MiB budget, wasting 5.7 GB to run *slower*. The plan labelled it `extrapolated`, which is
   why it was checkable — but it was a guess and the guess was poor.
2. **The sidekick's latency is not explained by `n_cpu_moe`, and my first reading of it was
   wrong.** I initially wrote this up as CPU contention — more expert layers on the CPU starving
   the seat meant to stay awake. The 22-layer run killed that: 18 and 22 hold near-identical VRAM
   (7 973 vs 7 969 MiB) and their sidekick probes differ by 23 seconds. The 22–24 s figures match
   the e2b's measured **20.97 s cold load** almost exactly. A second signal points the same way:
   the `n_cpu_moe 20` row reads 6 014 MiB where its neighbours read ~7 900, and
   6 014 + 1 810 (the sidekick) = 7 824. The heavy figure is computed as `used − baseline`, so a
   row low by exactly one sidekick is a row where the sidekick was **not resident** when the GPU
   was sampled. Likeliest reading: Ollama evicts the sidekick under memory pressure and each
   probe pays a reload. R10 stops the *Arbiter* evicting it; it cannot stop the daemon — the same
   degraded-pin problem as §4. Consistent with two independent signals, **still not directly
   confirmed**; the check is polling `ollama ps` during a lease.
3. **Holding the sidekick costs the heavy model a lot of speed** — 27.80 tok/s previously at
   `n_cpu_moe 20` versus 11.47 here. **Caveat, and it matters:** the earlier figure was a median
   of five warm runs on an otherwise idle machine; these are single runs with a browser and
   preview pane also on the card (baseline 3 690 MiB rather than 712 MiB). Some of that gap is
   the sidekick and some is everything else. **Not cleanly attributed** — do not quote the 2.4×
   as the cost of R10 until it is re-measured on a quiet machine.

Current best operating point on this evidence: **18** — it fits the 8 186 MiB budget and is now
in `MOE_SWEEP` as a measured point, replacing the extrapolated 32. One run per candidate, so it
is a direction, not a settled number.

The old sweep — `(16, 10170, 31.34)` and `(20, 9802, 27.80)` — was **dropped rather than merged**.
It measured total GPU on an idle machine with no sidekick, which is a different quantity against
a different budget; keeping both bases in one table would produce a number that looks measured
and is not.


---

# Round two — owning the runtime (2026-08-15, later)

## 10. The brain is ours now. The small seats are not, for a measured reason.

23 GB of GGUFs extracted from Ollama's blob store in 151 s, against 336 GB free.

```
load-pinned            interactive_brain  gemma4:12b   6.58s
load-pinned-degraded   sidekick           gemma4:e2b   23.5s
owned processes: ['gemma4:12b']
drift: []
```

`load-pinned`, not `load-pinned-degraded`. The brain runs at 131 072 in a process the Arbiter
spawns and kills; Ollama cannot evict it because Ollama does not know it exists. **The ~13 s
first-message cold load is gone.**

Also found while measuring: llama-server took **11 351 MiB** for that seat at the default batch
against Ollama's 7 813. The gap is the *compute buffer*, which scales with context and dwarfs KV
at long windows. Ollama runs `-b 512 -ub 512`; we now do too, and the seat costs **7 607 MiB**.
That 3.7 GB was the difference between the pinned pair fitting and not.

**Two wrong diagnoses, corrected by reading the GGUF headers:**

```
gemma4-12b   arch=gemma4   667 tensors   chat template: EMBEDDED
gemma4-e2b   arch=gemma4  2012 tensors   chat template: NONE
gemma4-e4b   arch=gemma4  2131 tensors   chat template: NONE
```

`expected 2012, got 601` was never sharding, and never the projector I then blamed. The e2b file
declares exactly the 2012 tensors llama.cpp expects; **upstream's gemma4 reader recognises only
601 of the names in it.** Ollama's engine binary loads the identical file.

Two further defects fell out, both of which would have looked like model incapacity:

- e2b and e4b carry **no embedded chat template**, so llama-server fell back to ChatML — leaking
  `<|im_end|>` into replies and handing the seat a template with **no tool definitions in it**.
  Fixed by borrowing the 12b's (same architecture; refuses to borrow across architectures).
- Even with the right template verified live in `/props`, the e-series emits
  `<|channel>thought ...` rather than OpenAI-shaped tool calls, and **that parser lives in
  Ollama's daemon**. Under our own process: `tool_calls: None`. Through the daemon: 5/5 on a
  five-call chain.

So `gemma4:e2b` and `e4b` stay on the daemon, in `DAEMON_SERVED` with the reason attached. They
still report as unenforced pins — a seat we *chose* not to own must look the same in the status
output as one we *failed* to own.

## 11. The fix made things worse before it made them better

```
before extraction   gemma4:12b, seat=local,        16.2s
after extraction    claude-sonnet-4-6, seat=cloud, 2m05s
```

The brain was resident, healthy and **unreachable**: dispatch still asked Ollama on :11434. A
seat that is resident and unreachable is worse than one that is neither — it holds 7.6 GB and
serves nothing. `owned_provider()` builds a descriptor from the seat's live port (it must be
built at call time; the port is assigned at process start). Verified through ordinary dispatch
with the full 52-tool registry:

```
seat=gemma4:12b  took=7.59s  result='ready.'   drift: []
```

## 12. Friday warns before she goes silent

```
POST /api/work/forecast {"kind":"local_turn"}
  will_pause=False
  why: gemma4:12b is loaded in a process Friday owns, so it cannot be taken away.

POST /api/work/forecast {"kind":"image"}
  will_pause=True  confidence=possible  ~186s
  why: Generating an image takes the graphics card for about 3.1 minutes. The image
       engine is not running yet, so this includes starting it - that part varies a lot.
  stays awake: ['sidekick']
```

The composer **holds** the message and shows the three options rather than sending and hoping —
a warning you cannot act on is a notification. "Use the cloud instead" is honoured for one turn
via `route_mode`, and the chat route refuses that override for vault-forced turns with a log line
rather than trusting the UI to have hidden the button.

## 13. Image generation with the sidekick held back — it works

```
before: sidekick=True   GPU=11120 MiB
status=ok in 83.2s  provider=local-comfyui  local=True
  friday_local_00003_.png -> /api/creations/friday_local_00003_.png  (1270 KB on disk)
after:  sidekick=False  GPU=11543 MiB
```

**Z-Image still generates with 1 811 MiB held back.** R10 survives the image lease.

**But the sidekick did not survive it.** `sidekick=False` afterwards: Ollama evicted it under
ComfyUI's memory pressure. R10 stops the *Arbiter* taking it; it cannot stop the daemon. Same
limitation as section 10, and the strongest argument for parsing `<|channel>` ourselves — that
one change would let both small seats become owned processes and close this properly.

The first attempt returned **HTTP 500 after a successful 108-second generation**. The local path
returned `files: ["C:\...png"]` where every caller does `files[0].get('filename')`, and the file
was only written to ComfyUI's output folder, which is not where `/api/creations/<filename>`
serves from. The picture existed and was unviewable. Both fixed.

## 14. The away-drain, watched firing

```
threshold via the API: 900 -> 20.0
2 items parked, activity marked. Hands off.
  t+ 5s  idle=7   away=False
  t+10s  idle=14  away=False
  t+15s  idle=21  away=True
  DRAIN FIRED: ran=2  why=away for 23s with 2 item(s) queued
    say ready 1    seat=gemma4:12b   8.66s  'ready.'
    say ready 2    seat=gemma4:12b   0.9s   'ready'
```

8.66 s then **0.9 s** — the second item rode in on the first's warm model. That is the batching
argument in two lines.

Getting here needed a third instance of an old defect: `away_drain_after_s` was missing from
`DEFAULT_SETTINGS`, so every save deleted it. The API returned success and the value vanished —
exactly what happened to `heavy_hitter`, and to `preferred_model` before that. **A key not in
`DEFAULT_SETTINGS` is not "unconfigured", it is actively erased.**

## 15. Still not verified

- The `<|channel>` parser — designed, argued for, not written.
- Whether the sidekick survives a *heavy* lease now that the brain is an owned process (only the
  image lease was retested).
- The re-run of the `--n-cpu-moe` sweep. It was queued as away-work and then cancelled rather
  than left armed on the machine unattended; the operating point is still one run per candidate.


---

# Round three — free (2026-08-15, later still)

Stephen: *"do it. I want to be free."*

## 16. The answer: yes, Ollama can be uninstalled. One caveat, and it is small.

**Verified by stopping the daemon and running Friday against it.** Not reasoned about — the
Ollama processes were killed, the port confirmed dead, and the server restarted from cold:

```
0. is the daemon actually down?          yes: URLError

1. does Friday know what models she has?
   interactive_brain  gemma4:12b            ctx=131072  pinned
   sidekick           gemma4:e2b            ctx=32768   pinned
   sidekick_heavy     gemma4:e4b            ctx=65536   leased
   heavy_hitter       gemma4:26b            ctx=32768   leased
   embedder           qwen3-embed:0.6b-q8   ctx=2048    resident
   owned processes: ['gemma4:12b', 'gemma4:e2b']
   ollama resident: {}
   drift          : []

2. a real turn, with tools, on a local seat
   seat=gemma4:12b   4.5s  'ready'

3. the pause forecast
   will_pause=False - gemma4:12b is loaded in a process Friday owns,
                      so it cannot be taken away.

4. can she acquire a NEW model?
   repo listing: 2 GGUFs, e.g. Qwen3-Embedding-0.6B-Q8_0.gguf

5. conversation memory (embeddings)
   1316 conversations, available
```

And the load-bearing one, from a **separate process** with the daemon still stopped — six
dependent five-call tool chains:

```
gemma4:12b   3/3 correct, 5 calls each, 11.5-28.1s
gemma4:e2b   3/3 correct, 5 calls each, ~39s
```

**The caveat: Ollama's ENGINE BINARY is still what loads `gemma4:e2b` and `e4b`.** Upstream
llama.cpp cannot read their tensor layout. We run that binary as a process the Arbiter owns, so
the *daemon* is irrelevant — but the file at
`AppData/Local/Programs/Ollama/lib/ollama/llama-server.exe` has to survive the uninstall. If it
does not, those two seats stop loading until upstream gains support or another build is
installed. **The 12b and the 26b load on upstream llama.cpp and are unaffected**, so the brain
and the heavy hitter keep working regardless.

Two notes rather than caveats:

- `~/.ollama/models` is now only an **import source**. Every model Friday uses has been copied
  into `~/.friday/runtime/models/gguf/`, which is what she actually loads. Confirm those five
  files are there before deleting anything; there is nothing else to preserve.
- **Embeddings were never an Ollama dependency.** Conversation memory uses sentence-transformers
  with `all-MiniLM-L6-v2` and always did. The `qwen3-embedding:0.6b` sitting in Ollama was never
  the live embedder — so an item I earlier listed as "medium effort, D5 re-index hazard" turned
  out to require no work at all.

## 17. What made it possible

**The channel parser.** gemma4's e-series emits
`<|tool_call>call:get_weather{city:Oslo}<tool_call|>` rather than OpenAI-shaped tool calls, and
only Ollama's daemon parsed it. That single gap was why two of four seats could not be owned.
`services/channel_toolcalls.py` reads it directly — schema-aware, because values contain commas
and the only reliable boundary is a declared parameter name — and thinking is disabled for tool
turns, because with it on the model closes its thought channel on an end-of-generation token
before the call is ever emitted.

**Friday's own store.** `services/model_store.py` keeps a registry whose every fact is read from
the GGUF header. Two defects only reading the FILE could catch:

- An embedding model is one that declares a **pooling type**. Not one with "embed" in its name,
  and not — as I first had it — one with a pooling type *and* no chat template.
  Qwen3-Embedding-0.6B has both, and was classified as a chat model eligible to hold a seat and
  answer questions. It has no output head.
- Size lives in **either** `general.size_label` **or** `general.parameter_count`, depending on
  the publisher. Reading one key left half the catalogue at None, everything sorted as zero, and
  the planner seated the 2B model as the interactive brain and the 4B as the heavy hitter.

**Direct acquisition.** `services/model_fetch.py` searches Hugging Face, lists a repo's
quantizations, and downloads with resume and integrity verification. Proven: 609.5 MB in 15.2 s,
`verified: sha256 against the publisher's checksum`.

The verifier earned its keep by firing on **my own mistake**. I trusted the plain `ETag` from the
resolve endpoint; it is a git blob id, not a content hash, and it is 64 hex characters so it
passes a shape check and then fails against a perfectly good file. The authoritative hash is the
tree API's LFS `oid`. A verifier that rejects valid downloads gets switched off, so being wrong
there is worse than not checking at all.

## 18. Nothing was broken on the way

The sequencing held. Ollama stayed the source of truth until the direct path was proven on a real
model, and `residency_catalog` still consults the daemon as a **fallback** for anything not yet
imported — so a model pulled five minutes ago still appears, and there was never a commit where
local inference was unavailable.

One gap the sequencing did surface, one layer out: an in-memory seat-to-port map served the
server process and nothing else, so a probe in a separate process raised "Ollama is not running"
about a model that was answering real turns two ports away. The Arbiter now publishes
`runtime/residency/endpoints.json`, health-checked before it is trusted.

## 19. Still not verified

- Whether the sidekick survives a **heavy** lease now that both seats are owned processes. Only
  the image lease was retested, and that was before the channel parser landed.
- The `--n-cpu-moe` operating point is still one run per candidate.
- The 26b and e4b have not been loaded as owned processes since the channel parser landed. The
  12b and e2b have, repeatedly. Nothing suggests they differ; nothing has proven it either.
- Ollama is currently **stopped, not uninstalled**. The uninstall itself has not been performed,
  so "it survives an uninstall" rests on the daemon being down and the files being copied — which
  is the same thing in every respect testable without deleting his files, and is stated as that
  rather than as more.
