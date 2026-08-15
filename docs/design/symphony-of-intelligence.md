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

**UNKNOWN:** the same probe against the 26b (not run). **UNKNOWN:** multi-step tool-loop
reliability — how often a local model completes a 3–5 call chain without losing the thread. The
structural check is single-turn only. The check that would settle it is a scripted multi-step
task with read-only tools, scored over repeats.

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
- **Multi-step tool chains are unproven.** **UNKNOWN** — single-call conformance is measured,
  chains are not. This is the highest-value unknown in the whole design, because "local executes
  the plan" *is* a multi-step tool chain.
- **Frontier-grade judgment on ambiguity.** Not a gap to close; it is why the frontier tier
  exists in the arrangement.
- **The heavy hitter is only 47 % on GPU.** It spills to CPU on this card, so it is
  ~2.2× slower than the 12b. On a 24 GB card it would run entirely in VRAM and the calculus
  would change completely.

---

## Part 3 — Recommendations, in order of value

1. **Raise the interactive context.** 32 768 → 131 072 for the pinned brain: 96 MiB, 4× the
   working room, still inside R3. Highest value-to-cost ratio available.
2. **Measure the multi-step tool chain.** Until that number exists, "local executes the plan" is
   an assumption. Score a scripted 3–5 call task over repeats, per model.
3. **Build the work queue with classes** (§2.5). Reflex / interactive / heavy / image /
   background, each knowing its seat's price, draining heavy and image work in batches.
4. **Make the task spec a real object.** The frontier→local handoff works because structured
   output is reliable; give it a schema, a place to live, and a report-back shape.
5. **Surface the lease.** When a heavy or image lease takes the card, say so — otherwise depth
   reads as the machine having gone slow for no reason.

---

## Part 4 — Open questions

**S1.** Raise the interactive context to 131 072 now, or wait for evidence that 12 552 tokens is
actually pinching?

**S2.** Should heavy work queue *by default* — parked until a batch is worth it — or run
immediately unless told otherwise? Batching is more efficient; immediate is more predictable.

**S3.** Who decides a turn is "heavy"? The e2b as a classifier (fast, and it is already resident
for exactly this kind of job), an explicit user gesture, or the frontier scoper as part of the
spec?

**S4.** On a heavy or image lease the interactive seat goes away for the duration. Acceptable, or
should the e2b stay pinned through leases as a "someone is still home" seat? It costs 1 811 MiB
of the heavy model's budget.
