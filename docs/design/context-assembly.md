# Context assembly — one budget, a drop order for everything, and no silent overflow

**Date:** 2026-08-19
**Status:** design. **No implementation code exists for this document — it lands first, by
instruction.** Written for a fresh-context builder: every fact needed is here or at a cited
file:line.
**Commission (Stephen, verbatim):** *"why can't you find that out and have fable optimize my
prompt queueing?"*
**Built from measurement:** [`docs/audits/prompt-token-audit.md`](../audits/prompt-token-audit.md)
(2026-08-18, cited below as **AUDIT**, its evidence-register rows as AUDIT-1..13), plus this
author's independent read of the assembly path. Per the commission's own rule — *the
measurement wins over the framing* — and it does: §1.2 lists where the numbers overturned
the brief.
**Branch note:** lands on the tree's active branch; doc-only; cherry-pick freely. Other
sessions hold uncommitted work here — this commit touches only this file.

**Evidence registers:** **MEASURED** / **VERIFIED** / **INFERRED** / **UNKNOWN**, as in the
audit; AUDIT-n cites its evidence table.

---

## 0. What this layer is for, in one paragraph

Friday assembles every turn's prompt in two uncoordinated places with no total budget
anywhere: a ten-layer tiered builder (`_build_context_prompt`) with per-layer *character*
caps, then three memory/continuity/tone blocks appended separately by the chat route. Nothing
knows the target seat's capacity; nothing fits to it; and when a turn is too big, the only
mechanisms that shorten anything act on the transcript — 5% of the turn — while the 93% that
is system prompt and tool schemas never shrinks (AUDIT §3). Overflow is then handled by the
inference server, below Friday, silently: spans fall out and nobody is told, which violates
the honesty rule that governs everything else in this system. This document specifies the
**Context Assembler**: one entry point, one token budget derived from the actual seat, a
deterministic and defensible drop order over *every* block including the boilerplate, a
report whenever anything was trimmed, and — before any of that — the two measurement gates
without which optimisation would be aimed at a number that may not exist.

---

## 1. The evidence base

### 1.1 What a turn actually costs — MEASURED (AUDIT §1)

Reconstructed from the real functions, tokens at 4 chars/token:

| contributor | tokens | share | bounded? |
|---|---:|---:|---|
| **tool schemas** (58 tools) | **9,603** | **70%** | no — grows with the registry, no per-turn selection |
| system prompt (`_build_context_prompt`) | 3,209 | 23% | per-layer char caps, no total |
| transcript (95 msgs) | 747 | 5% | 100-message cap (count, not chars — latent risk, AUDIT-7) |
| user model + heuristics | 144 | 1% | yes |
| memory recall block | 101 | 0.7% | `max_chars=1800` (AUDIT-5) |
| **total, reconstructed** | **≈13,804** | | |

Inside the 3,209-token system prompt: core persona 1,869 · `TODAY'S CONTEXT` 942
(**unconditional, every turn**, AUDIT §3) · smart wiki 173 · clock 122 · epistemic 84.

### 1.2 Where the measurement overturned the brief — stated plainly

The commissioning brief carried four claims. The audit's numbers contradict three:

1. **"~25,000 tokens of injected memory and source context."** Not found. The full
   reconstruction is ≈13,804 tokens against a reported live demand of 47,309 — a **3.4×
   disagreement between two measurements of the same thing** (AUDIT-13). The audit
   instrumented the live path to settle it (§2 below); until that one turn runs, the
   25,000 is a disputed constant, deliberately over-reserved, not a fact.
2. **"Every local seat sized 14,541 tokens short."** Disputed by (1) — and undermined from
   the other side by the audit's sharpest independent finding: **seat sizing measures a
   prompt the chat path never sends.** `context_budget.system_prompt_tokens()` calls
   `_get_friday_system_prompt` (12,706 tokens); `/api/chat` sends `_build_context_prompt`
   (3,209). Four-fold apart (AUDIT-2,3 — AUDIT §2). On the chat path the seats may be
   **over**-provisioned against a phantom prompt. Which callers use the big builder is
   **UNKNOWN** and is this spec's Gate B.
3. **"Memory recall may be re-injecting the transcript."** It cannot amount to anything:
   the recall block is capped at 1,800 chars and measured at **101 tokens — 0.7%** of the
   turn (AUDIT-4,5). Deduplication is demoted to a nearly-free guard, not a workstream.
4. **"Drop order appears oldest-first, boilerplate preserved — exactly backwards."**
   Confirmed, in a *sharper* form: within the transcript the order is actually better than
   feared (semantic pruning keeps the relevant, not the recent — AUDIT-9), but the
   transcript is the **only thing with a drop order at all**. System prompt and tools —
   93% of the turn — are never reduced by anything, and overflow beyond them happens inside
   the inference server where Friday cannot see or report it (AUDIT-10).

What the brief did not predict and the numbers scream: **the tool registry is 70% of every
turn** — 58 schemas on "what time is it?" — and it is the only unbounded contributor.

### 1.3 The mechanism, from this author's read — VERIFIED

- Assembly is split across two sites with no shared accounting:
  `_build_context_prompt` (`services/model_router.py:2194-2451`) builds tier-tagged
  sections through a single `add()` chokepoint (now instrumented, AUDIT §5); then
  `routes/chat.py` appends memory/continuity/tone blocks to the returned string.
- Relevance-ranked retrieval already exists for exactly one source: memory recall
  (ChromaDB embedding search, `n=5, min_relevance=0.30`,
  `model_router.py:1406`). Everything else is fixed or keyword-gated
  (`_detect_context_needs`), and the "smart" wiki layer returned **identical prompts for
  three different questions** in the audit's harness (AUDIT-12) — a selector that always
  selects the same thing is not selecting.
- The transcript's two shorteners: `_compress_trajectory` (2,000 chars/turn) and semantic
  pruning (top-k 10, on by default). Nothing else shrinks anything.
- Vault tiering happens at assembly (sections carry tiers; cloud is gated per span). Any
  redesign must preserve that seam exactly — it is load-bearing law (MC8 lineage).

---

## 2. The gates: measure first, then build

The audit's own conclusion, adopted as a hard precondition: *"no optimisation should be
specced before"* the disagreement resolves — so this spec is structured as **gates, then
mechanism**. The mechanism (§3) is designed to be correct under either resolution; the
gates decide its constants and its first targets.

**Gate A — one live turn.** Restart Friday with `FRIDAY_PROMPT_AUDIT=1` and take one
ordinary turn. It writes `~/.friday/runtime/residency/turn_audit.json` with every
contributor named and sized from the live path (AUDIT §6). This settles whether the
injected component is ~800 tokens or ~25,000. It requires a restart of Stephen's live
server, so it is his to trigger — Q1.

**Gate B — the builder census.** Enumerate every caller of `_get_friday_system_prompt` vs
`_build_context_prompt` (a grep and a read, not a project). The 4× gap is the most likely
home of the missing 25,000 (AUDIT §2): if the agentic loop, voice, or background paths use
the big builder, *those* paths cost ~9,500 tokens more per turn than chat — and the fix is
convergence, not budgeting. Also inside Gate B: the audit's open question 2 — whether the
tool loop replays tool *results* into context across iterations, which would be a per-turn
multiplier nothing here counts.

Until both gates close, the seat-sizing constant stays deliberately over-reserved at the
larger value — the audit's asymmetry argument (§7 there) is correct and this spec adopts
it: too-large costs one ladder rung (~32 MiB on this KV-flat family); too-small silently
truncates conversations. Ratified as CA8.

---

## 3. The design: the Context Assembler

### 3.1 One entry point, one budget

A new `services/context_assembler.py` becomes the only way a turn's prompt is built.
Signature in intent:

```
assemble(turn) -> AssemblyResult
  turn:    {message, conversation_id, workspace, provider, seat, tools_requested}
  result:  {system_prompt, messages, tools, report}

TokenBudget
  capacity        = num_ctx of the ACTUAL target seat        # residency plan, live —
                                                             # num_ctx_for_model() exists
  reserve_output  = max_tokens for the reply (existing per-path values)
  budget          = capacity − reserve_output
```

A 65k local seat and a 200k cloud model stop receiving identical injection because the
budget is an *input*, not an assumption. The assembler subsumes both current sites: the
ten layers of `_build_context_prompt` and the three blocks `routes/chat.py` appends
become **blocks** under one accounting — the audit's instrumented `add()` chokepoint grows
into the real thing. The two-builder split (Gate B) converges here: one builder, every
path, with per-path block configs rather than per-path prompt functions.

Token counting uses the existing 4-chars-per-token approximation for block sizing
(consistent with everything measured so far) — cheap, and the budget carries a 5% safety
margin against approximation error. **Never** a tokenizer call per block in the hot path.

### 3.2 Blocks, classes, and how relevance is judged

Every contributor declares itself:

```
Block
  name           today_context | clock | persona_core | smart_wiki | memory_recall |
                 workspace | trust | transcript | tools | ...
  klass          PINNED | CONDITIONAL | RETRIEVED | TRANSCRIPT
  tier           vault tier, exactly as today (the gating seam is untouched)
  tokens         measured at assembly
  relevance      0..1 where applicable
  drop_rank      derived from klass + relevance (§3.3)
```

- **PINNED** — never dropped, small by obligation: persona core, the clock block (its
  TIER_1-by-contract rule stands), the user's message, safety/policy text. A PINNED block
  over 2,500 tokens fails CI — pinning is a privilege with a weight limit.
- **CONDITIONAL** — the current keyword-gated layers (career, todos, trust, workspace…),
  unchanged in mechanism, now counted.
- **RETRIEVED** — relevance-scored content. **Relevance is judged by the embedder Friday
  already runs** — the same all-MiniLM/ChromaDB machinery that scores memory recall
  (`min_relevance=0.30`) and semantic pruning today. One query embedding per turn
  (already computed for recall), cosine against block embeddings cached by content hash.
  **No LLM call in the hot path, ever** (CA5) — the 12b judgment measured at ~12 s is
  disqualified from assembly by the same arithmetic that disqualified it from per-turn
  escalation judging.
- **TRANSCRIPT** — the conversation, shortened exactly as today (compression + semantic
  pruning), now inside the same budget as everything else.

Two measured defects get fixed by reclassification, contingent on Q2/Q3:

- **`TODAY'S CONTEXT` (942 tokens, unconditional)** becomes RETRIEVED at line granularity:
  briefing headlines, countdowns, and trust-circle lines are scored against the turn and
  only relevant lines ship. "What time is it?" stops paying 942 tokens for a briefing.
  (If Stephen wants the ritual always present — Q2 — it becomes PINNED-lite with a
  trimmed fixed form instead.)
- **The smart-wiki layer** gets the audit's finding treated as a defect: three different
  questions must produce three different selections, pinned by test. Its keyword router
  is replaced by the same embedding scoring; if it still returns identical content for
  distinct queries, the layer is declared inert and dropped rather than shipped as
  ballast (a selector that does not select is a subsystem that runs and produces
  nothing).

**Deduplication** (demoted per §1.2): at assembly, a block whose content hash matches a
span already present in the kept transcript is skipped and noted in the report. Nearly
free; expected saving per the audit: trivial. It exists as a guard, not a strategy.

### 3.3 The drop order — deterministic, defensible, and total

When `sum(blocks) > budget`, the assembler sheds in this order, each step logged:

1. **RETRIEVED below threshold** — relevance-ascending (the least relevant retrieved
   line goes first).
2. **CONDITIONAL blocks not matched by the current message** that shipped only on
   workspace affinity — whole blocks, least-recently-referenced first.
3. **Transcript shortening** — exactly the existing ladder: trajectory compression
   deeper, then semantic pruning tighter (top-k stepping down, floor 4 turns). The
   audit's verdict stands: *within* the transcript, relevance-keeps-what-matters is the
   right instinct and is preserved.
4. **RETRIEVED above threshold** — only now, relevance-ascending.
5. **Tool schema tiering** (§3.4) — the 70% finally participates.
6. **Never**: PINNED blocks, the user's message, the final `top_k` floor of live
   conversation. If the budget cannot hold even these, the turn does not dispatch —
   it returns the stated-choice shape (smaller seat is overloaded → offer the bigger
   seat or cloud under routing law), because sending a turn the seat structurally
   cannot hold is the silent-truncation bug with extra steps.

The inversion the brief demanded is now structural: **the live conversation is the
third thing to give, after the boilerplate has already given twice** — the exact
opposite of today, where it is the only thing that can give at all (AUDIT §3).

### 3.4 The tool registry — 70%, handled with the fragility it has earned

The single biggest number in the audit meets the single most burned subsystem in the
codebase (the argument-dropping bug, the channel dialect, the seat gate's false
condemnations — tool-calling has been fragile all day and all month). So the design is
conservative by construction:

1. **Measure before selecting.** The activity ledger already records every `tool_call`
   with its name. One query yields per-tool usage frequency over real history; per-tool
   schema cost is one serialization pass. That produces the **core set** from data —
   INFERRED target: the ~15 tools that serve >95% of turns — instead of anyone's guess.
2. **Conservative subsetting.** A turn ships: the core set + any tool whose
   name/description keywords match the message + the workspace's mapped tools + anything
   the conversation used in its last N turns. Inclusion errs wide; the point is dropping
   the long tail of never-called schemas, not minimalism.
3. **The safety net that makes it reversible:** if the model emits a call to a tool that
   exists but was withheld this turn — detectable at the existing pseudo-toolcall /
   unknown-tool seam — the loop **re-dispatches the turn once with the full registry**
   and the report records the miss. A withheld tool costs one retry, never a failure,
   and misses drive the core set's evolution.
4. **A hard prohibition:** tool descriptions carry behavioral law (the retrieve-cite
   directive lives in `search_web`'s description; the primary-source rule in
   `browse_web`'s). **No description text is trimmed or summarized by this work.** Any
   token saving from schema wording is out of scope until the behavioral text is first
   relocated to the system prompt deliberately, as its own reviewed change.
5. Rollout: subsetting ships behind `settings.context_assembly.tool_tiering` (default
   OFF), enabled after the miss-rate over a trial week measures near zero. The audit's
   number says this is where the tokens are; the codebase's history says walk, don't run.
6. **How we would know it had not broken** — stated as instruments, not assurance:
   - **Shadow mode first**: with the flag off, every turn computes the subset it *would*
     have shipped and counts tools the model actually called that the subset would have
     withheld. Nothing changes for the model; the counter is the evidence. Enablement
     requires a week at ~zero.
   - **The dependent-chain probe as a regression gate**: the measured 15/15
     five-call-chain battery (symphony §0.6) runs against a subsetted registry before
     and after enablement; any drop below 15/15 reverts the flag. This is the exact
     instrument that caught the argument-dropping bug — a chain score, not a vibe.
   - **The live miss alarm**: every withheld-tool miss in production (caught at the
     unknown-tool / pseudo-toolcall seam, one automatic full-registry retry) is counted
     in the assembly report and the ledger. A nonzero weekly miss count is a visible
     number on the overview, not a log line — and two consecutive nonzero weeks
     auto-disable the flag with a notice.
   - **The failure smell this system has learned**: a model "refusing" or "failing" at
     tools after any registry change is treated as our plumbing until proven otherwise
     — the standing rule that has been right three times running.

### 3.5 What Friday tells him — the honesty surface

Every `AssemblyResult` carries a report: budget, demand, per-block tokens, what was
dropped/trimmed at which step, and which tools were withheld. Three consumers:

- **The transcript, when it matters:** any drop at steps 2–5 beyond routine transcript
  aging appends the existing amber system-line shape:
  *"⚙ Context trimmed to fit gemma4:12b (65,536): dropped briefing lines (740 tok),
  tightened history to 8 turns. Nothing you typed was lost."* Never a silent trim — the
  same rule that governs seat changes and fallbacks.
- **The turn's orb/meta** carries the full report for the detail panel.
- **`record_turn_demand()`** (already built, AUDIT §5) receives every turn's real demand,
  so seat sizing consumes a live distribution instead of a disputed constant — the
  constant demotes to a bootstrap default the day the distribution exists.

And the inverse guarantee: because the assembler never emits over budget, **server-side
context overflow becomes a defect with an alarm** — if the inference server ever reports
truncation (or output shows the context-shift signature), that is logged as a bug in the
assembler's accounting, loudly, not absorbed.

---

## 4. Rules as data

| id | Rule |
|---|---|
| **CA1** | One assembler, one budget per turn; the budget derives from the *actual* target seat's live num_ctx minus output reserve. No path builds a prompt outside it |
| **CA2** | Nothing is ever sent over budget. Server-side truncation is, by definition, an assembler bug and alarms as one |
| **CA3** | The drop order is total and deterministic: retrieved-irrelevant → unmatched-conditional → transcript aging → retrieved-relevant → tool tiering → refuse-with-choice. Boilerplate gives before the live conversation, twice |
| **CA4** | Every drop, trim, and withheld tool is reported — transcript line for the user, report for the orb, demand record for seat sizing. Silent is forbidden |
| **CA5** | Relevance is judged by the existing embedder against cached block embeddings — never an LLM call in the assembly hot path |
| **CA6** | Tool subsetting is conservative, defaults off, re-dispatches once with the full registry on any withheld-tool miss, and never touches description text carrying behavioral rules |
| **CA7** | Seat sizing consumes measured live demand once it exists; constants are bootstrap defaults, labeled as such |
| **CA8** | While the 3.4× disagreement stands, the disputed constant stays at the larger value — over-reservation costs a ladder rung; under-reservation silently truncates. (Audit §7, ratified) |
| **CA9** | The vault-tier gating seam is preserved exactly: blocks carry tiers, cloud sealing happens per span, and no assembly optimisation may reorder itself around the gate |
| **CA10** | A selector that returns the same output for different inputs is inert and is removed, not shipped (the smart-wiki test) |

---

## 5. Build order

Gates first; each step one commit, offline-testable; real bodies / fake transport.

1. **Gate A** — Stephen runs the one live turn (`FRIDAY_PROMPT_AUDIT=1`, restart, one
   message). Zero code. Its `turn_audit.json` becomes a committed fixture.
2. **Gate B** — the builder census + the tool-loop replay check (audit open Q2). Output:
   a table in the commit message; if a path uses the 12,706-token builder, converging it
   is this step's fix.
3. **The assembler, counting only** — blocks/classes/budget/report wired through both
   current sites with **zero behavioral change**: same content ships, now measured
   per-turn. Test: byte-identical prompts before/after on the three audit questions.
4. **Drop order + honesty line** — CA3/CA4 live. Test: an oversized synthetic turn sheds
   in the declared order, the transcript line appears, and PINNED content survives every
   budget down to the refuse floor.
5. **Relevance for `TODAY'S CONTEXT` + smart-wiki** — embedding-scored lines; the
   three-questions-three-selections test (CA10) pins the smart layer or kills it.
6. **Tool tiering, dark** — ledger-derived core set, subsetting behind the off-by-default
   flag, the miss-and-redispatch net, a shadow-mode counter measuring would-have-missed
   before anything is withheld for real.
7. **Seat sizing from demand** — `record_turn_demand` distribution replaces the constant
   per CA7/CA8 once Gate A's number and a week of live turns agree.

---

## 6. The brief's questions, answered from the numbers

- **What gets injected, decided how:** blocks with declared classes; fixed only where
  pinned by obligation; retrieval scored by the embedder already in the process — no new
  model calls (§3.2).
- **Budget-aware assembly:** the budget is an input from the live residency plan; a 65k
  seat and a 200k model receive different assemblies by construction (§3.1).
- **Drop order:** total, deterministic, boilerplate-first, reported; the transcript's
  existing relevance-keeping survives as step 3 of 6 (§3.3).
- **Deduplication:** a content-hash guard, demoted to nearly-free hygiene — the audit
  measured the feared duplication at 0.7% of the turn (§1.2).
- **What Friday tells him:** an amber transcript line whenever anything beyond routine
  aging was dropped, plus the full report on the orb (§3.5).
- **Tool schemas:** the real prize at 70%, approached with measurement, conservative
  inclusion, an automatic full-registry retry, an off-by-default flag, and a prohibition
  on touching the behavioral text — because this subsystem's failure mode is silent and
  has burned every session that touched it (§3.4).

---

## 7. Open questions for Stephen

Each answerable in a sentence.

**Q1 — The one live turn.** Will you restart Friday once with `FRIDAY_PROMPT_AUDIT=1` and
send one ordinary message, so the 3.4× disagreement (13,804 vs 47,309 tokens) is settled by
your machine instead of argued by two documents?

**Q2 — The daily briefing block.** May `TODAY'S CONTEXT` (942 tokens on every turn) become
relevance-gated so "what time is it?" stops carrying your briefing — or do you want it
always present as part of how Friday feels?

**Q3 — Tool tiering.** Once the shadow counter shows a near-zero miss rate, may Friday
withhold rarely-used tool schemas per turn (with the automatic full-set retry on any miss),
or should all 58 always ship?

**Q4 — The trim notice.** Is the amber in-chat line the right visibility for a trimmed
turn, or would you rather it live only in the orb detail unless something *you typed* was
at risk (which CA3 forbids anyway)?

**Q5 — Standing over-reservation.** Do you ratify keeping seat sizing at the larger
disputed number until Gate A and a week of live demand agree — costing ~one ladder rung of
VRAM on this KV-flat family rather than risking silent truncation?

---

## 8. Sources

- [`docs/audits/prompt-token-audit.md`](../audits/prompt-token-audit.md) — every MEASURED
  figure; its instrumentation (`services/prompt_audit.py`, `FRIDAY_PROMPT_AUDIT=1`,
  `record_turn_demand`) is the substrate steps 1 and 7 stand on.
- Assembly-path read, 2026-08-19: `services/model_router.py:2194-2451`
  (`_build_context_prompt`), `:1406` (memory recall, `min_relevance=0.30`), `:1180/:1252`
  (tone/continuity blocks), `routes/chat.py` (append site, compression, pruning),
  `services/context_budget.py`.
- Prior design docs this composes with:
  [`residency-policy.md`](residency-policy.md) (seat num_ctx, ladder rungs, KV-flat
  measurements), [`conversations-and-concurrency.md`](conversations-and-concurrency.md)
  (per-conversation transcripts the assembler reads; the stated-choice shape CA3's refuse
  floor reuses), [`deep-research.md`](deep-research.md) (the judgment-gate latency that
  disqualifies LLM calls from the hot path).
