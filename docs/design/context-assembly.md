# Context assembly — the toolbox opens on demand, the transcript gets a budget, and nothing is trimmed in silence

**Date:** 2026-08-19
**Status:** design. **No implementation code exists for this document — it lands first, by
instruction.** Written for a fresh-context builder: every fact needed is here or at a cited
file:line.
**Revised:** 2026-08-19, three times. First written from the initial token audit; revised
when the audit's live turns landed final numbers and Stephen proposed the central mechanism
himself: *"Can we make a tool that opens the toolbox, allowing the model to find the right
one and only use those tokens?"* — which is deferred tool loading, and it is not
hypothetical: it is exactly how the harness these build sessions run in works today (§3.1).
Revised a third time the same day for Stephen's follow-up — *"would prompt caching help?"* —
which interacts with the toolbox in a way this spec must resolve: §1.4 verifies what each
backend actually offers, §3.7 resolves the interaction and orders the prompt around it.
**Measured against:** [`docs/audits/prompt-token-audit.md`](../audits/prompt-token-audit.md)
final form (commits `e5cf151`, `9504c8d` — live turns, sandboxed against real data), plus
commit `ed10711` (the display-reserve measurement). Cited as **AUDIT**.
**Branch note:** lands on the tree's active branch; doc-only; other sessions hold
uncommitted work here — this commit touches only this file.

**Evidence registers:** **MEASURED** / **VERIFIED** / **INFERRED** / **UNKNOWN**.

---

## 0. What this layer is for, in one paragraph

A real turn on Friday carries **65,799 tokens of overhead before the conversation counts**
(AUDIT §6, MEASURED live), which forced the tool seats to a 131,072-token window — and on
the card Friday actually runs on, even 65,536 eats the display reserve that has cost
Stephen his second monitor twice today (`ed10711`, MEASURED: 32,768 leaves 1,936 MiB free;
65,536 leaves 551, under the 1,024 reserve). The window cannot grow to fit the overhead;
**the overhead has to shrink to fit the card.** The two numbers that matter: tool schemas
are **14,041 tokens on every turn** — the largest fixed cost, growing with every MCP
connector he adds — and the transcript is **858–1,046 tokens today but 42,612 with
ordinary-length messages**, because its cap counts messages, not tokens: invisible now,
dominant later, the worst shape a cost can have. This document specifies the fix in the
order the measurement ranks it: **deferred tool schemas** (a small resident core, everything
else as names with a lookup tool — Stephen's own proposal), **a token budget for the
transcript** with relevance-first dropping, **assembly fitted to the actual seat**, a
**report for every trim**, **an order of assembly that keeps prompt caches warm** (stable
prefix first; the minute-resolution clock evicted from it), and **instrumentation that
never turns off** so none of this can silently regress.

---

## 1. The evidence base — final, live, and one correction

### 1.1 Where a turn's tokens go — MEASURED (AUDIT, live path)

| turn | history | **total** | transcript | tools | system | recall |
|---|---|---:|---:|---:|---:|---:|
| "what time is it?" | 95 real msgs | **19,752** | 858 | 14,041 | 4,165 | 688 |
| recall-triggering | 95 real msgs | **20,227** | 908 | 14,041 | 4,377 | 901 |
| tool-provoking | 95 real msgs | **19,908** | 1,046 | 14,041 | 4,149 | 672 |
| same question, **full-length history** | 100 real-prose msgs | **61,723** | **42,612** | 14,041 | 4,267 | 791 |

The earlier 3.4× dispute resolved cleanly: 13,804 and 47,309 were both right, measuring
different transcript states. Two structural facts under the table:

- **Tool schemas: 14,041 live vs 9,603 on a bare import** — the difference is MCP servers
  registering into the same registry (26 tools from GitHub alone). Every turn carries every
  schema; nothing selects; connecting another server raises every future turn's price.
- **The transcript cap counts messages, not tokens** (`CHAT_HISTORY[-100:]`, 2,000 chars
  per compressed turn): structural ceiling **~50,000 tokens of transcript**, ~68,300 per
  turn with fixed costs added. His median message today is 5 tokens, which is the only
  reason this is invisible.
- Memory recall: 672–901 tokens, never above 4.5% — **innocent, confirmed live**. The
  duplication hypothesis is dead and stays dead.

### 1.2 The collision that sets the target — MEASURED (`ed10711`)

The audit's recorded worst case set `MEASURED_INJECTED_TOKENS = 43,415` and pushed
`TOOL_SEAT_NUM_CTX` to 131,072 (65,536 misses the measured overhead **by 263 tokens**).
But the KV-flat "over-reserving is nearly free" premise fails on this card: the **compute
buffer** scales with context and dwarfs the KV cache at long windows —

```
gemma4:12b on the RTX 4070 (12,282 MiB):
  32,768  → 1,936 MiB free
  65,536  →   551 MiB free      ← under the 1,024 MiB display reserve
```

So the seat cap stays **32,768 on this card**, and a real turn at today's overhead
**cannot fit it at all** (32,768 − 65,799 = −33,031). That is the design target in one
line: **get a real turn's overhead under a 32,768 seat with room to converse.** §3.3 does
the arithmetic showing the mechanisms in this spec achieve it.

### 1.3 A correction to this spec's own first revision

The first revision claimed overflow is silent at the inference server. **Half wrong, and
the true failure is worse in a different way** (`ed10711`, observed live): llama-server
*rejects* an over-length request with a 400 naming the count — *"request (47448 tokens)
exceeds the available context size"* — and the dispatch ladder then answers from the
**cloud**, with the local-fallback notice. So on Arbiter-owned seats the failure is loud,
but its consequence is that **turns Stephen chose to keep local quietly become Anthropic
turns** whenever his conversation grows past what the seat holds. (The old Ollama daemon
path did truncate silently; those seats are nearly retired.) Either way the cure is the
same and stands: an assembler that never emits more than the seat holds.

### 1.4 What prompt caching actually offers — VERIFIED against both backends

Stephen asked whether prompt caching would help. Verified before designing, per the
commission's own rule: don't design against a capability that isn't there.

**Both backends cache by byte-identical prefix.** The cache covers the prompt from its
first byte up to the first byte that differs from the previous request; one changed byte
at position N invalidates everything after N. Everything in §3.7 follows from that one
sentence.

- **llama-server (the local seats):** prefix reuse is **already enabled** — `cache_prompt`
  defaults to on, and the server reuses the longest common prefix of the prior request in
  the same slot, re-processing only the differing suffix (VERIFIED, llama.cpp server
  docs). It is per-slot: Friday's seats run a single slot, so the cache holds one
  conversation's state at a time. Also available and currently unused: `--cache-reuse N`
  (salvages non-prefix chunks via KV shifting, off by default) and
  `/slots/{id}?action=save|restore` (parks a slot's KV cache to disk).
- **Anthropic (the cloud ladder):** opt-in per request via `cache_control` breakpoints
  (max 4). Cache **reads bill at ~0.1× the input price; writes at 1.25×** for the default
  5-minute TTL or **2×** for the 1-hour TTL; the TTL refreshes on use. Minimum cacheable
  prefix is per-model: **1,024 tokens on `claude-sonnet-5`** (the default cloud seat),
  512 on `claude-opus-5`, 4,096 on `claude-haiku-4-5`. Render order is
  tools → system → messages, and invalidation is hierarchical: a tool-set or model change
  invalidates everything; a system-prompt edit invalidates system+messages but the tools
  tier survives; message edits invalidate messages only. Every response reports hits in
  `usage.cache_read_input_tokens` (VERIFIED, current API reference). **Friday sends no
  `cache_control` anywhere today** — grep across `src/` finds zero occurrences — so every
  cloud token, including the heartbeat's ~42,654 hourly, bills at full price.
- **The clock defeats both backends, today.** `clock_context_block()` renders
  `%Y-%m-%d %H:%M` — minute resolution — and assembly adds it at **position 2**, directly
  after `FRIDAY_SYSTEM_PROMPT` (`model_router.py:2235`, `clock.py:47`). A byte change at
  position ~4,200 on any turn that crosses a minute boundary (that is: essentially every
  turn) invalidates everything after the system prompt on both backends. llama-server's
  enabled cache is thereby reduced to reusing the system prompt alone, and any Anthropic
  breakpoint placed below the clock would never hit. The audit's finding that three
  different turn types produced **identical system prompts** is the asset here: the
  prefix is already stable *except for what we inject into it*.
- **What caching does not do:** it skips **recomputation**, not **residency**. Cached
  tokens still occupy the KV cache, and the compute buffer still scales with the window —
  §1.2's numbers do not move. Caching changes nothing about seat sizing, and there is
  still no local heavy hitter on this card.

---

## 2. Settled questions, carried decisions

- **Gate A ran** (sandboxed against a copy of his live data; his server untouched). The
  disputed constant is retired; `MEASURED_INJECTED_TOKENS = 43,415` is a measurement.
- **The two-builders finding stands** (chat sends ~4,200; seat sizing measured 12,706 from
  a builder the chat path never calls). Convergence into the single assembler remains in
  the build order (§5.6); it is no longer suspected of hiding 25,000 tokens — the
  transcript was the variable — but two builders is still one too many.
- **Instrumentation exists and works** (`prompt_audit.py`, `record_turn_demand`,
  `turn_audit.json`). §3.6 makes it permanent instead of env-gated.
- The audit's asymmetry rule (over-reserve rather than silently truncate) served its
  purpose and retires with the dispute; the card's display reserve now sets the ceiling,
  and fitting under it is this spec's job.

---

## 3. The design

### 3.1 Deferred tool schemas — the toolbox that opens on demand

**The mechanism, from the working precedent.** The harness these build sessions run in
carries hundreds of tools without paying for them: most tools are *not* in context — a
system note lists their **names**, full schemas arrive only when a **search/fetch tool** is
called with the names or keywords needed, and from that moment the fetched tools are
callable like any other. The cost of the entire long tail is a list of names plus one
lookup call when one is actually wanted. That is Stephen's toolbox sentence, running in
production, serving this very build. Friday gets the same shape:

**The registry splits in two:**

- **CORE — resident, full schemas every turn.** Chosen by data with a guaranteed floor:
  - *Data:* the activity ledger already records every `tool_call` by name; the core is
    the smallest set covering **≥95% of calls over the trailing 30 days** (INFERRED from
    ledger shape: on the order of 12–18 tools).
  - *Floor, regardless of frequency:* `open_toolbox` itself (a lookup that cannot be
    looked up must be resident — a model that cannot reach the toolbox can do nothing at
    all); `search_web` and `browse_web` (the retrieve-cite directive lives in their
    descriptions and must never be a fetch away from a turn that needs to look something
    up); `navigate`, `spawn_task`, `search_wiki`, `read_wiki`, `write_file`. Stephen may
    edit the floor — Q1.
  - Estimated resident cost: **~3,000–3,800 tokens** (INFERRED from measured per-schema
    averages; step 1 of the build measures it exactly).
- **DEFERRED — names only.** Every other tool appears in a compact index block:
  `tool_name — one-line purpose`. Measured average entry ~15–20 tokens →
  **~1,000–1,400 tokens for the full index** of everything else, versus the ~10,500 their
  schemas cost today. **MCP tools land here by default** — every one, from every server,
  including future connectors (CA11). That is the specific fix for "every connector he
  adds taxes every future turn": a new server adds index lines, not schemas.

**`open_toolbox(query)` — the lookup.** Takes names or keywords; returns the full schemas
of matching tools, which the loop registers for the remainder of the turn (and pins for
the next N turns of the same conversation, so a task that uses a rare tool repeatedly
fetches once). Matching is name-exact first, then keyword over the one-liners — string
matching, no model call. An empty match returns the nearest names, so a miss teaches
rather than dead-ends.

**When the model guesses wrong**, in order of likelihood:
1. **Calls a deferred tool directly without fetching** — the unknown-tool seam catches
   it, and instead of an error the loop **auto-injects that one schema and retries the
   call**: cost, one extra iteration; recorded as a `toolbox_miss` in the assembly
   report. A miss is one retry, never a failure.
2. **Calls a tool that does not exist at all** — today's error text, now also carrying
   the index so the model can correct itself.
3. **Fetches too much** — `open_toolbox` responses are capped (≤10 schemas per call);
   the report records fetch volume so a model that shotguns the toolbox is visible.

**The round-trip economics, said honestly.** A toolbox fetch costs one extra loop
iteration on a local seat — ~3 s on the 12b, ~1–2 s on the e2b (INFERRED from the
measured chain rates: five dependent calls in 13.8 s / 10.6 s). When it is worth it:
- On **every** turn, resident overhead drops by ~9,500–10,500 tokens — which is the
  difference between a real turn fitting a 32,768 seat and bouncing to the cloud (§3.3).
- On **most** turns (≥95% by construction of the core), no fetch ever happens — the
  common path pays nothing.
- On the rare-tool turn, the fetch's seconds buy the seat's privacy: the alternative at
  today's overhead is not "faster local" but **"answered by Anthropic"** (§1.3).
- **RESOLVED (was UNKNOWN): prefill amortization.** llama-server's prefix cache is real
  and already enabled (§1.4) — whether resident schemas cost prefill every turn depends
  entirely on prompt *ordering*, and today the minute-resolution clock at position 2
  guarantees they do. §3.7 fixes the order and states plainly what that changes here:
  once caching works, deferral's **latency** argument becomes conditional (a warm prefix
  makes resident schemas prefill-free anyway), but its **context-headroom** argument is
  untouched and sufficient on its own — 14k of schemas occupy 14k of a 32,768 window
  whether or not they were cached. Step 1 still measures prefill at both registry shapes.

**How we would know tool-calling had broken** — instruments, not assurance, because this
subsystem has been the most fragile thing in the system all day and a model that cannot
call the lookup can do nothing at all:
- **Shadow mode first.** With the flag off, every turn computes its would-be core set and
  counts calls that would have needed a fetch. Enablement requires a week of shadow data
  showing the fetch rate where the ledger predicts it (~5%).
- **The dependent-chain battery extends.** The measured 15/15 five-call probe gains a
  deferred leg — a chain that *requires* fetch-then-call — run per local seat before
  enablement and in the offline suite forever. Below 15/15, the flag reverts. This is the
  instrument that caught the argument-dropping bug; it is a score, not a vibe.
- **The live miss alarm.** `toolbox_miss` counts surface on the overview weekly; two
  consecutive nonzero weeks above the shadow baseline auto-disable the flag with a
  notice. Misses cost one retry meanwhile — degraded, never broken.
- **The standing rule**: a model "failing at tools" after any registry change is our
  plumbing until proven otherwise. Three-for-three so far.
- **Hard prohibition carried forward:** description text carries behavioral law
  (retrieve-cite lives in `search_web`'s description). Nothing here trims or rewrites
  any description — deferral moves schemas, it does not edit them.

### 3.2 The transcript gets a token budget — defusing the time bomb

The cap changes dimension: from **100 messages** to a **token allowance** computed per
turn by the assembler:

```
transcript_budget = seat_budget − resident_fixed − retrieved_actual − safety_margin
```

At the §3.3 arithmetic that is roughly **18,000–20,000 tokens on a 32,768 seat** — about
20× today's real usage, and a hard wall where today there is none until ~50,000.

Within the allowance, the audit's praised instinct **generalises** (it was the one thing
the measurement found already right): relevance keeps content, age does not —
1. Semantic pruning (existing, embedding-scored against the current message) selects
   which turns survive, exactly as now but budget-driven rather than fixed top-k.
2. Trajectory compression (existing) squeezes what survives.
3. A verbatim floor: the last K turns (default 4) and every pinned message are never
   pruned or compressed — the immediate conversation is untouchable.
4. Anything dropped is dropped *knowing its token count*, into the report.

The per-conversation stores from the conversations build are the substrate — each
conversation's transcript is already its own file with its own pins; the assembler reads
and budgets per conversation.

### 3.3 Fitting the card — the arithmetic that makes 32,768 real

Budget on the card-safe seat: 32,768 − 4,096 output reserve = **28,672**.

| | today | under this spec |
|---|---:|---:|
| tool schemas | 14,041 | core ~3,400 + index ~1,200 ≈ **4,600** |
| system prompt | ~4,200 | ~4,200 (relevance-gating of `TODAY'S CONTEXT` may trim ~900 — Q2) |
| memory recall | ~900 | ~900 (innocent; untouched) |
| **fixed total** | **~19,100** | **~9,700** |
| **room for conversation** | ~9,500 | **~19,000** |

**INFERRED from measured components; step 1 re-measures the composed result.** The point
survives any reasonable error bar: deferred tools roughly **double the conversation room
on the seat this card can actually afford**, and a real turn stops being structurally
condemned to the cloud. The 131,072 seat remains available for what genuinely needs it;
it stops being the *mandatory* price of overhead.

Budgets come from the live residency plan (`num_ctx_for_model()`), so a 32,768 local
seat, a 131,072 seat, and a 200k cloud model each get assemblies fitted to what they
hold — the cloud simply gets looser budgets, not a different assembler.

### 3.4 Blocks, relevance, and the total drop order

Unchanged in structure from the first revision, restated compactly with the new numbers:

- Every contributor is a **block**: `PINNED` (persona core, clock, user message, safety
  text — CI-capped ≤2,500 tokens each) · `CONDITIONAL` (keyword-gated layers, as today,
  now counted) · `RETRIEVED` (relevance-scored by the **existing embedder** against
  cached block embeddings — never an LLM call in the hot path) · `TRANSCRIPT` (§3.2) ·
  `TOOLS` (§3.1). Vault tiers ride the blocks exactly as today; the gating seam is
  untouched (CA9).
- `TODAY'S CONTEXT` (942, unconditional) becomes line-scored RETRIEVED, pending Q2.
  The smart-wiki layer keeps its three-questions-three-selections test: a selector that
  returns the same output for different inputs is inert and is removed (CA10).
- **Drop order when over budget**, deterministic and total: retrieved-below-threshold →
  unmatched-conditional → transcript pruning/compression to its floor →
  retrieved-above-threshold → **refuse-with-choice** (the seat structurally cannot hold
  the turn: offer the larger seat or cloud under routing law, using the existing
  stated-choice shape). PINNED and the verbatim transcript floor never drop. Boilerplate
  gives before the live conversation — the inversion of today, where the conversation was
  the only thing that could give.

### 3.5 What Friday tells him — nothing silent, ever

Every assembly emits a report: budget, demand, per-block tokens, drops with reasons,
toolbox fetches and misses. Consumers: an **amber transcript line** whenever anything
beyond routine transcript aging was trimmed (*"⚙ Context trimmed to fit gemma4:12b
(32,768): 3 briefing lines and 6 older turns summarized. Nothing you typed was lost."*);
the **orb detail** carries the full report; and a llama-server 400 or any local→cloud
bounce for size is an **alarm with the assembler named as the defendant** — under CA2
that rejection should now be impossible, so its occurrence is a bug report that writes
itself.

### 3.6 Instrumentation that cannot be forgotten

`FRIDAY_PROMPT_AUDIT=1` retires as a flag; the measurement becomes the permanent path:
- Every turn's per-block demand feeds `record_turn_demand()` (a counter write, no
  measurable cost — the instrumented chokepoint already exists).
- `turn_audit.json` becomes a rotating per-day record instead of a one-shot.
- A **weekly overhead regression check** in the existing self-improvement loop: if the
  resident fixed cost (core schemas + index + system prompt) grows >10% week-over-week,
  Stephen gets a notification naming the source — which is exactly how the next
  connector's tax, or the next persona edit's creep, becomes visible the week it happens
  instead of at the next crisis audit.

### 3.7 Prompt caching — order the prompt so yesterday's tokens stay paid for

Caching pays only when the prefix is byte-identical between requests; deferred tool
loading makes the tool region *vary* on fetch-turns — exactly the kind of change that
breaks a prefix cache. The two designs compose, on one condition: **the prompt is ordered
by volatility**, stable content first, per-turn content last, with the cache boundary
sitting at the seam.

**The order (normative, both backends):**

```
STABLE PREFIX (identical bytes across turns and conversations; cache boundary at its end)
  1. FRIDAY_SYSTEM_PROMPT       — frozen; editing it is a versioned change (one cold turn)
  2. resident core tool schemas — deterministic serialization, sorted by name
  3. the deferred-tool index    — changes only when a connector is added or removed
APPEND-ONLY MIDDLE
  4. the transcript             — appending preserves the prefix; pruning rewrites it (below)
VOLATILE TAIL (may change every turn; nothing above it may depend on it)
  5. clock · TODAY'S CONTEXT · workspace · recall · fetched schemas · the user message
```

**The clock moves.** Content unchanged, position changed: from position 2 to the volatile
tail. Its authority, its `PINNED` drop class, and its TIER_1-by-contract vault status are
all untouched — render position is orthogonal to drop order and tier gating. On the cloud
mapping it must leave the `system` string entirely (a system edit invalidates the message
cache by the hierarchy) and ride as a content block in the final user turn — the portable
form that works on every Claude model, since mid-conversation `role:system` messages are
Opus-tier only and the default cloud seat is Sonnet-class.

**Cloud mapping.** `tools` = the core schemas in deterministic order; `system` =
`FRIDAY_SYSTEM_PROMPT` + the index, with one `cache_control` breakpoint on the last
system block (by render order that caches tools+system together); a second breakpoint on
the last block of the newest appended turn, so conversation history accrues
incrementally. TTL: the 5-minute default on interactive turns — break-even is two
requests, and consecutive turns in a live conversation land well inside it. The **hourly
heartbeat** sits outside the 5-minute window entirely; only the 1-hour TTL (2× write,
needs ≥3 refreshed reads to pay off) can serve it, and whether it actually pays is a
counter question, not a design question — enable it, watch `cache_read_input_tokens`,
drop it if the counters say no.

**Local mapping.** The same rendered order flows through the chat template, and
llama-server's already-enabled prefix reuse then covers regions 1–4 with no further work.
What still costs a full prefill, said honestly:
- **Seat swaps and lease displacement** evict the KV cache — an image job that takes the
  GPU costs the next chat turn its warm prefix. Caching pays *within* bursts of
  conversation, not across displacements. `/slots/.../save|restore` could park the cache
  to disk across leases — optional, measure the restore cost before adopting.
- **Conversation interleaving on the single slot** — a turn from conversation B after a
  turn from conversation A reuses only regions 1–3 (~9,700 tokens post-deferral), losing
  A's transcript warmth. That is precisely why regions 1–3 must be byte-identical
  *across conversations*, not merely across turns: the shared prefix is what survives
  the interleave that the conversations build makes routine.
- **More slots would fix interleaving but cannot be afforded:** slots split `num_ctx`,
  so a 32,768 seat at two slots is two 16,384 windows — under §3.3's arithmetic that
  fits nothing. One slot stands on this card.

**What invalidates the cache boundary** — enumerated, so anything else observed is a bug:
1. a model or seat change (caches are per-model on both backends);
2. seat restart or lease displacement (local only, above);
3. a persona/system-prompt edit — a versioned change; one cold turn, expected and
   reported;
4. a change to the resident set or the index — including a **toolbox fetch-turn** (next
   paragraph);
5. transcript pruning/compression — rewrites the middle, so the message-region cache is
   lost from the edit point; regions 1–3 survive (on Anthropic by the invalidation
   hierarchy, locally because the prefix match still reaches the end of region 3).
   Acceptable by construction: pruning fires when the transcript is over budget, and
   what must re-prefill is exactly what pruning just shrank.

**Deferred tools × caching, resolved.** A fetch-turn adds a schema to the tool region →
one cold prefill from that position. The fetched tool then **pins for the conversation**
(§3.1), so the very next turn re-warms with the new schema inside the stable prefix. At
the ledger-predicted ~5% fetch rate that is roughly one cold turn in twenty — bounded,
and reported (`cache_cold: toolbox_fetch` in the assembly report). What caching changes
about deferral's economics, stated once and plainly: **deferral's latency argument is
conditional** (on a warm prefix, resident schemas were free anyway — though on this card
cold prefixes stay common: displacement, interleaving, restarts), **its headroom argument
is not** — schemas occupy the context window and the KV cache cached or not, and headroom
is what makes 32,768 livable. Deferral stays on headroom alone. (Cloud note: Anthropic
now ships the cache-preserving form of exactly Stephen's mechanism — tools declared with
`defer_loading` and surfaced mid-conversation by `tool_addition` blocks, beta, Opus 5
onward. If the cloud seat is ever Opus 5, the assembler can adopt it and even fetch-turns
keep their cache; on Sonnet-class seats the bounded cold fetch-turn stands.)

**The answer to Stephen's question, in three parts:**
- **Latency: yes, and mostly locally.** The five-minute local call was prompt
  processing. With this order, turn N+1 re-prefills only its delta instead of
  re-reading the entire conversation — this is the single largest latency lever
  available on the 4070, and it costs a reordering, not hardware.
- **Cloud cost: yes, with real numbers.** The heartbeat ships ~42,654 tokens hourly at
  full price today; under this section the stable ~9,700 bills at ~0.1× on every hit,
  and interactive threads inside the TTL reuse their entire accrued history at ~0.1×.
  The discount is established from the API's own terms (§1.4), and the realized rate is
  read off the counters (CA15), never assumed.
- **VRAM: no.** Caching means those tokens aren't recomputed; it does not mean they
  don't occupy memory. The seat is still sized for its full window, §3.3's arithmetic
  and the 32,768 cap stand unchanged, and there is still no local heavy hitter (CA16).

**Verification (CA15).** Every cloud response's `cache_read_input_tokens` /
`cache_creation_input_tokens` land in the cost meter per conversation; every local turn
logs prompt-tokens-processed (the server's timings) against prompt-tokens-sent; the
assembly report gains a `prefix_reused` field. An unchanged prefix showing zero reuse
across a day **alarms** — it means a silent invalidator (the clock's successor) has crept
back into the prefix, and this is the instrument that catches it the week it happens.

---

## 4. Rules as data (updated)

| id | Rule |
|---|---|
| **CA1** | One assembler, one budget per turn, derived from the actual target seat's live num_ctx minus output reserve |
| **CA2** | Nothing is ever sent over budget. A llama-server over-length 400, or a local→cloud bounce for size, is an assembler bug and alarms as one |
| **CA3** | The drop order is total and deterministic; boilerplate gives before conversation; PINNED and the verbatim transcript floor never drop; past the floor, refuse-with-choice |
| **CA4** | Every trim, drop, fetch, and miss is reported — transcript line, orb report, demand record. Silent is forbidden |
| **CA5** | Relevance is judged by the existing embedder against cached embeddings — never an LLM call in the assembly hot path |
| **CA6** | Tool deferral never edits description text; behavioral law in descriptions moves only as its own reviewed change |
| **CA7** | Seat sizing consumes the live demand distribution; constants are bootstrap defaults, labeled |
| **CA8** | The toolbox lookup is CORE by definition — a lookup that must be looked up is a lockout. A deferred-tool miss costs one auto-retry, never a failure |
| **CA9** | The vault-tier gating seam is preserved exactly; no assembly optimisation reorders itself around the gate |
| **CA10** | A selector that returns identical output for different inputs is inert and is removed, not shipped |
| **CA11** | MCP tools are DEFERRED by default, present and future; promotion to core is earned through the ledger, never granted by connection |
| **CA12** | The transcript is budgeted in tokens, never in message count; within budget, relevance outranks recency; the last K turns are verbatim-inviolable |
| **CA13** | The instrumentation is always on. Turning it off is not a setting; the weekly overhead regression check notifies on >10% fixed-cost growth |
| **CA14** | The prompt is ordered by volatility: stable prefix (system core → resident schemas → index) → append-only transcript → volatile tail (clock, daily context, workspace, recall, fetched schemas, user message). No per-turn value may render before the transcript; the clock lives in the tail on every backend |
| **CA15** | Cache behavior is read off counters, never assumed: cloud cache-read/write fields into the cost meter, local prefill counts into the turn report. An unchanged prefix showing zero reuse across a day alarms |
| **CA16** | Caching never changes seat sizing. Cached tokens still occupy KV and compute; §3.3's arithmetic, the 32,768 cap, and the display reserve stand regardless of hit rate |

---

## 5. Build order

Each step one commit, offline-testable, real bodies / fake transport; UI via `ui_stage`.

1. **Measure the split registry** — serialize core-candidate and index forms; measure
   prefill at both shapes on the live 12b seat (the §3.1 UNKNOWN); ledger query for the
   frequency-derived core. Output: the real numbers for §3.3's table, committed as a
   fixture.
2. **The assembler, counting only** — blocks/budget/report wired through the existing
   sites, zero behavioral change, byte-identical prompts on the audit's test questions.
   Instrumentation becomes always-on here (CA13).
3. **Transcript token budget** (CA12) — the message-count cap retires; pruning becomes
   budget-driven; the verbatim floor lands. Test: the 42,612-token seeded history fits a
   32,768 seat with the floor intact and the report naming what was summarized.
4. **Drop order + honesty line** (CA3/CA4). Test: oversized synthetic turns shed in
   declared order; the amber line renders; PINNED survives to the refuse floor.
5. **The toolbox, shadow mode** — index + `open_toolbox` + auto-inject-on-miss built but
   OFF; shadow counters run for a week; the deferred-chain probe joins the offline
   battery. Test: fetch-then-call chains 15/15 per seat; miss auto-retry works.
6. **The toolbox, live** — flag on after shadow week passes; MCP defer-by-default
   (CA11); builder convergence (the 4× twin retires into the assembler). Live
   acceptance: a real turn on the 32,768 seat with conversation room ≥15,000 tokens and
   zero misses across a day of ordinary use.
7. **Relevance-gating of `TODAY'S CONTEXT` + smart-wiki test** (Q2, CA10).
8. **Seat sizing from the demand distribution** (CA7) — `TOOL_SEAT_NUM_CTX` becomes a
   measured percentile, and the roles-contract figure gets re-derived from it against
   this card's display reserve.
9. **Cache alignment** (§3.7, CA14–CA16) — assembly reordered to the volatility order
   (the clock leaves the prefix), Anthropic breakpoints added, counters wired into the
   cost meter and the turn report. May land any time after step 2 — it edits the same
   chokepoint — and is listed last only because the toolbox (steps 5–6) changes what the
   stable prefix contains. Acceptance: two consecutive turns on the live 12b seat show
   near-zero re-prefill in the server timings; a cloud turn pair shows nonzero
   `cache_read_input_tokens`; crossing a minute boundary no longer changes a single
   prefix byte.

---

## 6. The commission's asks, answered

1. **Deferred tool schemas** — §3.1: data-chosen core with a guaranteed floor, ~15–20
   token index entries, `open_toolbox` with string matching, one-retry misses, the
   lookup itself core by definition, four named instruments for detecting breakage, and
   the round-trip trade stated with its real alternative (the cloud bounce).
2. **MCP deferred by default** — CA11; a new connector adds index lines, not a tax.
3. **Transcript budgeted by tokens** — §3.2/CA12; relevance-first inside the budget,
   generalizing the one instinct the audit found already right.
4. **Seat-real assembly** — §3.3/CA1, with the card-collision arithmetic that makes
   32,768 livable again.
5. **Every trim reported** — §3.5/CA4, including the reframed loud-400 alarm.
6. **Instrumentation always-on** — §3.6/CA13, with the weekly regression notice.
7. **Prompt caching folded in** — §1.4/§3.7/CA14–16: both backends' terms verified
   (llama-server's cache is already on and defeated by the clock at position 2;
   Anthropic's is unused entirely), the prompt ordered by volatility with the cache
   boundary and its invalidators named, the deferral interaction bounded to ~one cold
   turn in twenty, and the honest no on record: VRAM and seating are unhelped.

---

## 7. Open questions for Stephen

Each answerable in a sentence.

**Q1 — The core floor.** Beyond the structural set (`open_toolbox`, `search_web`,
`browse_web`, `navigate`, `spawn_task`, `search_wiki`, `read_wiki`, `write_file`), are
there tools you want guaranteed always-resident regardless of usage frequency?

**Q2 — The briefing block.** May `TODAY'S CONTEXT` (942 tokens every turn) become
relevance-gated so small questions stop carrying it, or should it stay always-on as part
of how Friday feels?

**Q3 — The seconds for the seat.** On the rare turn that needs an uncommon tool, is ~3
extra seconds on a local seat an acceptable price for turns fitting the 32,768 window
your card can hold without sacrificing the second monitor?

**Q4 — The transcript floor.** Is 4 verbatim-inviolable recent turns the right floor, or
do you want more of the immediate conversation guaranteed untouchable?

**Q5 — The seat target.** Do you ratify 32,768-with-assembler as the standard tool-seat
target on this card (131,072 reserved for explicitly long work), accepting that the
display reserve outranks window size?

---

## 8. Sources

- [`docs/audits/prompt-token-audit.md`](../audits/prompt-token-audit.md) (final, live
  turns) — every MEASURED figure in §1.1, the 43,415 constant, the 65,799 overhead, the
  transcript ceiling.
- Commit `ed10711` — the display-reserve measurement, the compute-buffer finding, and
  the observed llama-server over-length 400 that corrected §1.3.
- The harness precedent for §3.1 — the deferred-tool mechanism these build sessions run
  under: names in a system listing, schemas fetched by a search tool on demand,
  fetched tools callable thereafter; described from direct operational use.
- §1.4's caching terms — llama.cpp's server documentation (`cache_prompt` default,
  per-slot prefix reuse, `--cache-reuse`, slot save/restore) and Anthropic's current
  prompt-caching reference (pricing multipliers, per-model prefix minimums, TTLs,
  breakpoint limits, the tools→system→messages invalidation hierarchy), both read
  2026-08-19. The clock finding: `services/clock.py:47` (minute-resolution render) and
  `services/model_router.py:2235` (position 2 in assembly).
- Prior design docs: [`residency-policy.md`](residency-policy.md) (seat num_ctx, the
  ladder), [`conversations-and-concurrency.md`](conversations-and-concurrency.md)
  (per-conversation transcripts; the stated-choice refuse shape),
  [`symphony-of-intelligence.md`](symphony-of-intelligence.md) (the 15/15 chain battery
  §3.1's instruments extend).
