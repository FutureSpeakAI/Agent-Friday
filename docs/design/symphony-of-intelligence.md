# The symphony of intelligence — how Friday should divide work across her models

**Date:** 2026-08-15
**Status:** design. No implementation code written for this document.
**Method:** STORM — multi-perspective questioning first, cited synthesis second.
**Registers:** **VERIFIED** (file:line or captured output), **INFERRED**, **UNKNOWN**.

---

## Part 0 — What is actually true today

Everything in this section was measured or read on the reference instance today. It exists
because the design that follows is only worth as much as the facts under it.

### 0.1 Local models get the SAME tools as cloud models. All of them.

**VERIFIED.** There is one tool registry, `CLAUDE_TOOLS` (52 tools), and both paths receive it
unmodified:

- cloud — `services/agent.py:174` → `_call_openai(..., tools=CLAUDE_TOOLS, ...)`
- local — `services/agent.py:187` → `_call_ollama(..., tools=CLAUDE_TOOLS, ...)`

**VERIFIED by absence:** there is no provider-conditional tool filtering anywhere in
`agent.py` or `model_router.py`. No reduced set, no "local models don't get X".
`routing/model_router.anthropic_to_openai_tools()` converts the one Anthropic-shaped registry to
OpenAI format on demand, and `_oai_agentic_loop` is the single shared loop serving both Ollama
and OpenAI-compatible providers.

### 0.2 The one asymmetry runs the *other* way — local can do MORE

**VERIFIED** `services/agent.py:5113-5117`:

> "ONLY vault-tier (TIER_2/TIER_3) data is gated here; the provider determines whether
> sensitive content may flow (**local = allowed, cloud = denied**). Everything non-sensitive
> passes untouched, so navigation / file ops / app launch / task spawn are available to every
> model."

So a local model can read and act on vault-tier material that a frontier model is structurally
forbidden from seeing. This is not a limitation to work around — it is the single most important
capability the local tier has, and it should drive placement more than speed does.

### 0.3 The real constraint is context overhead, not capability

**VERIFIED**, measured against the live registry and Friday's actual system prompt:

| | tokens |
|---|---:|
| Tool schemas (52 tools) | ~8 534 |
| Friday's system prompt | ~11 681 |
| **Fixed overhead per turn** | **~20 216** |
| Window at the current tool-seat context (32768) | 32 768 |
| **Working room left for the actual conversation** | **~12 552 (38 %)** |

Nearly two-thirds of the window is spent before the user types anything.

**And it is nearly free to fix.** The gemma4 KV curve is close to flat — **VERIFIED** on the 12b:

| num_ctx | VRAM |
|---:|---:|
| 32 768 | 7 718 MiB |
| 65 536 | 7 750 MiB |
| 131 072 | 7 814 MiB |

**96 MiB buys a 4× larger window.** At 131 072 the working room goes from ~12 552 to ~110 856
tokens. The pinned pair would still fit: 7 814 + 1 811 = 9 625 MiB against 9 997 MiB available
(rule R3). **INFERRED:** the tool-seat context should be raised, and the only reason it is 32 768
today is that I sized it from the tool registry alone rather than from the whole prompt.

### 0.4 The speed ladder, measured

| Model | tok/s | Resident | On GPU | Cold load |
|---|---:|---:|---:|---:|
| `gemma4:e2b` | **166.13** | 1 811 MiB | 100 % | 21.0 s |
| `gemma4:e4b` | **99.93** | 3 081 MiB | 100 % | 27.5 s |
| `gemma4:12b` | **49.36** | 7 718 MiB | 100 % | 20.5 s |
| `gemma4:26b` | **22.44** | 17 423 MiB | **47 %** | **53.5 s** |

**The 26b's price is not its speed — it is its 53.5-second cold load.** A single question costs a
minute before the first token. That one number determines almost everything about how heavy work
should be scheduled.

### 0.5 Local models produce reliable structured output

**VERIFIED** — strict JSON, exact key set, typed fields, 3 attempts each at temperature 0:

```
gemma4:e2b   3/3
gemma4:e4b   3/3
gemma4:12b   3/3
```

This matters more than it looks: a frontier model handing work down needs the local model to
*consume and emit a machine-readable task spec*. That mechanism works today.

**UNKNOWN:** the same probe against the 26b (not run).

### 0.6 The multi-step tool chain — measured 2026-08-15, and it was our bug

This was the load-bearing unknown: "local executes the plan" *is* a chain of dependent tool
calls, and only single-call conformance had ever been measured.

A scripted task that cannot be answered without four dependent calls — list, then fetch each
item, then look up the winner's owner — run through Friday's real dispatch (`_call_ollama` with
tools, the same `_oai_agentic_loop`, the same `num_ctx`), five repeats per model:

| Model | Correct | Reached the last call | Median chain |
|---|---:|---:|---:|
| `gemma4:e2b` | **5/5** | 5/5 | 10.6 s |
| `gemma4:e4b` | **5/5** | 5/5 | 12.1 s |
| `gemma4:12b` | **5/5** | 5/5 | 13.8 s |

**15/15.** A 2-billion-parameter model walks a five-call dependent chain in under eleven seconds.

**But the first run of the probe scored 0, and the cause was ours.** `_oai_agentic_loop` did
`json.loads(fn.get("arguments") or "{}")`. OpenAI's spec says `arguments` is a JSON *string*;
Ollama's native `/api/chat` returns it as an already-parsed *object*. `json.loads()` on a dict
raises, a bare `except` substituted `{}`, and **every local tool call ran with no arguments at
all** — silently, with no log. Verified against the raw daemon that the models were emitting
correct arguments every time.

The symptom was models reporting that their tools had failed and then apologising, which reads
exactly like a model too weak to chain tool calls. It was a type check. Fixed; the table above is
the same probe afterwards.

Worth recording as a method note: **this is what "the model is bad at tools" usually turns out to
be.** The honesty gate condemned models on the same class of evidence.

---

## Part 1 — STORM: the question set, from five seats

### The journalist
What gets done while I wait, and what can I walk away from? When is a slower local answer better
than a faster cloud one? What am I giving up by staying on-device?

### The systems engineer
What is genuinely scarce? What contends? If two things want the GPU, who wins and who waits?
What is the cost of a wrong placement — a stall, or a wrong answer?

### The sovereignty perspective
What must never leave the machine? Where is that enforced, and would I notice if it stopped
being enforced?

### The economist
Frontier tokens cost money; local tokens cost time and electricity. Where is the exchange rate
favourable, and where is it absurd?

### The failure analyst
What breaks? How does it present? Would Stephen be able to tell a local model got it wrong, or
would it look like a confident answer?

---

## Part 2 — Synthesis: the symphony

### 2.1 The five voices, by what they are actually for

Placement follows **latency class and privilege**, not parameter count.

**`e2b` — reflex (166 tok/s, pinned, always resident).** Sub-second work that happens *between*
your sentences: classifying intent, deciding where a turn should go, extracting an entity,
deciding whether a notification is worth raising. It is resident precisely so this costs nothing.

**`e4b` — the workhorse (99.93 tok/s, leased).** Small but real jobs: summarise this page, draft
this reply, tag these files. Better instruction adherence than the e2b, still cheap.

**`12b` — the conversational brain (49.36 tok/s, pinned).** Everyday chat and tool loops. Fast
enough to feel live, capable enough to hold a multi-step task, and it is the seat with the
largest affordable window (§0.3).

**`26b` — the heavy hitter (22.44 tok/s, leased, 53.5 s to summon).** Coding, hard reasoning,
anything where quality beats latency. **Its economics are batch economics.**

**Frontier (Claude) — the scoper.** Ambiguity, judgment, architecture, work that must be right
the first time, and anything where being wrong is expensive to discover later.

### 2.2 The core division: frontier scopes, local executes

This is Stephen's instinct and it is the right one, for a reason that is now measurable — §0.5.
A frontier model is best at the part of the work where the *shape* is unclear, and worst
value-for-money at the part where the shape is already known. Local models are the reverse.

So the handoff is a **task spec**, not a conversation:

1. **Scope (frontier).** Turn a vague intention into an explicit plan: what done looks like, the
   ordered steps, which tools each step needs, what could go wrong, and how to verify.
2. **Hand down (structured).** The spec goes to local as machine-readable JSON — which local
   models parse and emit reliably (3/3, §0.5).
3. **Execute (local).** Each step is now a well-specified, verifiable job. This is the part that
   is *bulk* — and bulk is exactly where local wins, because the marginal token is free.
4. **Report back (structured).** Local returns what it did, what it couldn't, and its evidence.
5. **Review (frontier, only if needed).** Ambiguity or failure goes back up. Success does not.

The economic logic: **you pay frontier prices for judgment, and pay in local seconds for volume.**
A 40-step refactor scoped once by Claude and executed by the 26b costs one scoping call instead
of forty.

### 2.3 What decides whether work goes up or stays down

Four tests, in priority order. The first is not negotiable.

**1. Does it touch the vault?** Then it is local. Full stop. Already enforced —
`routing/model_router.py:_route_vault` forces a local route regardless of configured mode, and
`services/agent.py:186-191` refuses to let a vault-forced route fall back to cloud, with the
comment *"A vault-forced local route must NEVER retry on a cloud provider."* The zero-trust check
at `agent.py:5113` re-applies per tool call. **This is the strongest reason the local tier has to
be good** — it is not a cheaper substitute, it is the only tier allowed to see some of your work.

**2. Is the spec clear and is verification cheap?** Then local. "Rename these files", "extract
the quotes", "run this refactor across 40 call sites" — the answer is checkable, so a wrong one
is caught rather than believed.

**3. Is it ambiguous, or is being wrong expensive to discover?** Then frontier. Architecture,
editorial judgment, anything where a plausible-but-wrong answer would be acted on.

**4. Is it bulk?** Then local, always. Volume is what local is *for*.

### 2.4 Priority and the GPU — why the lease model is the whole game

The GPU is the only genuinely scarce resource here: **9 997 MiB usable**, against a 26b that
wants 17 423 MiB and an image model that wants ~14 GB of weights. Nothing else contends
meaningfully — CPU, disk and network are all slack by comparison.

This is why the Arbiter's **lease** model is what makes any scheduling possible. Without it
there is no way to express "the heavy model needs the card for the next two minutes, everything
else stands down" — the models simply fight, and Ollama's own scheduler evicts whatever it likes
(measured: it silently dropped a model the policy considered pinned, at every context tried).

The natural priority order falls out of latency class:

1. **Interactive turns** — you are waiting. The pinned pair (12b + e2b) exists so this never
   queues behind anything.
2. **Heavy leases** — you asked for depth and accepted the wait.
3. **Image leases** — exclusive; everything stands down (R5).
4. **Background/batch** — yields to all of the above.

A heavy or image lease *evicts the pinned pair*, so the interactive seat is unavailable while it
runs. That is the real cost of depth on a single 12 GB card, and it should be visible to you
when it happens rather than felt as unexplained sluggishness.

> **Superseded 2026-08-15 by S4.** The sidekick now survives every lease — see §5.3. Friday
> stays awake and answering while the heavy model works. The 12b brain still stands down.

### 2.5 Batching — the 53-second rule

**The single most important scheduling fact: summoning the 26b costs 53.5 seconds before the
first token.** At 22.44 tok/s, a 500-token answer takes ~22 seconds of generation. So a
cold-start single question is **70 % waiting for the model to load**.

Therefore: **never summon the heavy hitter for one thing.** Accumulate heavy work and spend the
load once.

That implies a queue Friday does not have yet:

- Work that wants the heavy seat is *tagged and parked*, not executed immediately.
- The queue drains when it is worth it — enough items, a deadline, or an idle machine.
- Once resident, the model stays and chews through everything queued before handing the card
  back. Ollama's `keep_alive` is the mechanism; the Arbiter's lease is the authority.
- Same logic for image: ComfyUI's cold start was measured at 180 s originally and ~93 s in a
  warm run today. Generating six images in one lease is roughly the cost of generating one.

**INFERRED, and this is the design's main recommendation:** the residency layer is finished
enough to *place* models; what it lacks is a **work queue with classes** that knows the price of
each seat and spends it deliberately. Leases are the mechanism. Nothing accumulates work into
them yet.

### 2.6 What local genuinely cannot do yet — honestly

- **Long-context work is currently cramped.** ~12 552 usable tokens at today's setting (§0.3).
  A long article, a big diff, or a deep conversation will compact aggressively. Fixable for
  96 MiB and it should be fixed.
- ~~**Multi-step tool chains are unproven.**~~ **Settled 2026-08-15: 15/15 across all three
  models** (§0.6). The gap was never in the models — it was a type check in our own loop that
  dropped every tool argument.
- **Frontier-grade judgment on ambiguity.** Not a gap to close; it is why the frontier tier
  exists in the arrangement.
- **The heavy hitter is only 47 % on GPU.** It spills to CPU on this card, so it is
  ~2.2× slower than the 12b. On a 24 GB card it would run entirely in VRAM and the calculus
  would change completely.

---

## Part 3 — Recommendations, in order of value

1. **Raise the interactive context.** 32 768 → 131 072 for the pinned brain: 96 MiB, 4× the
   working room, still inside R3. Highest value-to-cost ratio available.
2. ~~**Measure the multi-step tool chain.**~~ **Done — 15/15** (§0.6), after fixing the argument
   parsing it exposed.
3. **Build the work queue with classes** (§2.5). Reflex / interactive / heavy / image /
   background, each knowing its seat's price, draining heavy and image work in batches.
4. **Make the task spec a real object.** The frontier→local handoff works because structured
   output is reliable; give it a schema, a place to live, and a report-back shape.
5. **Surface the lease.** When a heavy or image lease takes the card, say so — otherwise depth
   reads as the machine having gone slow for no reason.

---

## Part 4 — The four questions, answered

Answered by Stephen, 2026-08-15, verbatim where it matters.

**S1 — context.** *"Do expand the context window, yes."* Sized from the **whole** prompt — system
prompt plus tools plus real conversation room — not from the tool list alone, which was the
error that produced 32 768.

**S2 — does heavy work queue by default?** Neither. **Friday asks.** *"when something that might
be heavy work is being considered, that should cause Friday to ask if the user wants it to be
scheduled for local execution when the user is away or if the user needs it immediately then it
can execute either locally (slower) or in the cloud (faster)."* Three options, he picks. Never a
silent decision in either direction.

**S3 — who decides a turn is heavy?** *"The user decides when a task is heavy."* Friday's
judgement only *raises the question*; it never settles it. What Friday owes him is a clear
picture to decide from: *"perhaps Friday should present a custom workflow UI with a
representation of the tasks it will execute, and a series of config options for the workflow, so
the user can choose or select 'choose for me' as an option as well."* Friday proposes; Stephen
disposes — including the option to hand the decision back.

**S4 — does the sidekick survive a lease?** *"keep e2b awake so Friday is always alive."* Yes.
The heavy and image leases may take the brain; they may not take the sidekick.

---

## Part 5 — Build spec

Shape on record before code. Six components, one commit each.

### 5.1 Context sized from the whole prompt

The mistake being corrected: `TOOL_SEAT_NUM_CTX = 32768` was derived from the ~8 534-token tool
registry alone (×4 for headroom). It ignored the ~11 681-token system prompt, which is the
larger of the two. Correct arithmetic:

```
overhead = system prompt + tool schemas      (measured, ~20 216)
want     = overhead + conversation room      (room is per-role)
num_ctx  = largest ladder rung that is >= want AND fits the VRAM budget
           AND is <= the model's own declared context window
```

- **Ladder:** powers of two, 8192 … 262144. A rung, not an arbitrary integer, because backends
  allocate KV in blocks and a tidy number is easier to reason about in a bug report.
- **Floor:** `overhead + 8192`. A seat that cannot hold the tools *and* a short conversation
  cannot do tool-using work at all. Below the floor, refuse with the arithmetic (R7 already says
  the number is explicit; this says it must also be *sufficient*).
- **Room targets:** interactive brain gets the most — it is the seat that reads documents and
  holds long conversations. The heavy seat needs room for code. The sidekicks need enough to be
  useful without spending budget the brain needs.
- **Fitting:** VRAM at an unmeasured context is **extrapolated from the model's own measured KV
  slope**, never guessed from a family constant, and never extrapolated downward into optimism.
  With fewer than two rows the pessimistic "largest measured" rule stands.
- Every rung actually chosen gets **measured at load and recorded**, so the next plan uses truth
  rather than the estimate.

### 5.2 Heavy work proposes; it never decides

A `WorkflowProposal` is the object Friday puts in front of Stephen:

```
proposal = {
  id, title, summary,
  tasks: [ { id, title, detail, cls, seat_hint,
             est_s_local, est_s_cloud, tools, touches_vault } ],
  options: { execution: [ when_away | now_local | now_cloud ], ... },
  blocked:  [ { option, reason } ],       # e.g. cloud, because a task reads the vault
  recommendation: { execution, why },     # what "choose for me" would pick
}
```

Three executions, exactly as specified:

| Option | Meaning | Cost |
|---|---|---|
| `when_away` | Parked; drains under one lease while the machine is idle | Free to wait, slowest to finish |
| `now_local` | Runs now on local seats | Slower, private, no bill |
| `now_cloud` | Runs now on the frontier | Faster, costs money, **vault-blocked** |

**The vault rule constrains the menu.** If any task in a proposal touches vault-tier material,
`now_cloud` is not offered for that task and the reason is stated on the option, not hidden. This
is the existing rule made visible: `routing/model_router._route_vault` forces local regardless of
configured mode, and `services/agent.py:186-191` refuses to let a vault-forced route fall back to
cloud. The UI must not offer a choice the router would overrule.

**"Choose for me"** picks by a stated heuristic and *shows which one it picked and why*, so a
handed-back decision is still legible: vault work → `now_local`; long work with the machine idle
→ `when_away`; short work → `now_local`; work he is visibly waiting on → `now_cloud`.

### 5.3 The sidekick survives every lease

`Arbiter._evict_pinned()` currently stands down `interactive_brain`, `sidekick` and `embedder`.
The sidekick comes off that list, for both lease kinds.

**This is not free and the cost must be measured, not assumed.** The heavy model was placed
against the full 9 997 MiB budget and settled at 9 802 MiB with `--n-cpu-moe 20`. Holding
1 811 MiB back for the sidekick leaves **8 186 MiB**, so the heavy seat must push more experts to
the CPU and will run slower. The operating point is re-swept and the new number recorded — R6's
`n_cpu_moe` becomes a function of the budget actually available at lease time rather than a
constant. The image lease gets the same treatment and the same question asked of it: does
Z-Image still generate with 1 811 MiB held back?

If the answer for either is "no", that is reported as a cost of the decision, not worked around
silently.

> **Live, 2026-08-15.** The Arbiter no longer evicts the sidekick, and the plan subtracts its
> 1 811 MiB from the lease budget (9 997 → 8 186). See §6 for what the running machine actually
> does, including a separate problem the change surfaced.

### 5.4 The work queue

`services/work_queue.py`. Classed, persisted, drained under one lease.

- **Classes:** `reflex`, `interactive`, `heavy`, `image`, `background` — the priority order from
  §2.4.
- **An item** carries its spec, its class, its disposition, its provenance (which proposal), and
  after execution its seat, timings and result.
- **Drain** is the whole point: take one lease, run *every* queued item of that class in order,
  release. The 53.5 s wake is paid once for the batch instead of once per item, and the drain
  records both figures so the saving is visible rather than claimed.
- **The away-drain** is what `when_away` means. Idle is measured from real user activity; the
  drain fires when the machine has been quiet long enough that taking the card is free.
- Persisted, because a queue that dies with the process is not a promise Friday can make.

### 5.5 The workflow UI

A panel rendering the proposal: the task list Friday intends to execute, per-task class and
estimate, the three execution options with anything blocked shown as blocked *and why*,
per-task overrides, and **Choose for me**. Plus the live queue: what is parked, what is running,
what the last drain cost.

### 5.6 The tool-chain measurement

Load-bearing and still **UNKNOWN**. A scripted task requiring 3–5 dependent read-only tool calls,
run N times per local model, scored on whether the chain completed and the answer was right.

Per Stephen's standing rule: **a model that scores badly is a prompting-and-template problem to
fix, not a model to exclude.** The number tells us where to work, never who to bar.
