# Caching audit — what gets cheaper, what gets faster, and what a cap has to do instead

**Date:** 2026-08-26
**Commissioned:** after a crash-fallback re-sent a blown-context turn to Anthropic and billed
roughly 1.43M input tokens on one task. *"What we need to do is cache, cache, and cache some
more… I never want to have that happen again."*
**Measured against:** `~/.friday/costs.db` (5,486 rows, live), the local seat's own log
`~/.friday/runtime/logs/llama-gemma4_12b-8090.log` (16 h), and the tree at `0c47906`.
**Friday was not restarted.** Every number here comes from a file she had already written.

**Evidence registers:** **MEASURED** / **VERIFIED** / **INFERRED**.

---

## 0. The one thing to read

**Caching is the efficiency answer. A hard pre-call cap is the "never again" answer. They are
different guarantees and Friday needs both.**

Caching makes the common case cheap — measured, an **80% cut** to the Anthropic input line.
It does nothing whatsoever to prevent the next runaway, because a runaway is not expensive
per call; it is expensive per *thousand* calls, and each of those calls can be individually
cheap and cached. Only a limit prevents a catastrophe.

The specific reason a cap is not optional: `_call_claude_agent` defaults to
**`max_iters=999`** with nothing in the code bounding spend. At the measured median of ~91,000
input tokens per iteration that is a theoretical **90 million tokens on a single task**. The
1.43M incident was that machinery running about sixteen times. Both are now capped
(`services/prompt_cache.py`, enforced in `model_router._seal_or_block`).

---

## 1. What the money actually is — MEASURED

`~/.friday/costs.db`, all providers, all time:

| provider | calls | input tokens | output tokens | USD |
|---|---:|---:|---:|---:|
| anthropic | 3,994 | **263,520,347** | 1,715,418 | **$1,352.25** |
| local / arbiter-local | 1,492 | 27,070,201 | 780,007 | $0 |

**Input:output on the cloud line is 154:1.** Ninety-nine per cent of the bill is prompt, not
answer. That single ratio decides the whole audit: prompt caching discounts input at ~0.1×,
and input is essentially all of it.

The last fourteen days, Anthropic only — 1,982 calls, 168,887,596 input tokens, **$1,057.22**:

| model | calls | input tokens | USD | avg input/call |
|---|---:|---:|---:|---:|
| claude-opus-5 | 502 | 43,230,543 | **$669.63** | 86,117 |
| claude-sonnet-5 | 1,004 | 87,438,906 | $269.62 | 87,091 |
| claude-fable-5 | 369 | 36,136,405 | $111.92 | 97,931 |
| claude-sonnet-4-6 | 107 | 2,081,742 | $6.05 | 19,456 |

Per-call input distribution: **p50 90,951 · p95 136,740 · p99 166,799 · max 195,875**.
**94.7% of all input tokens sit in calls above 50,000 tokens.** By kind, `chat` is $594.53 and
`scheduled` is $451.90 — this is not one bad task, it is the shape of ordinary use.

### 1.1 Where those 91,000 tokens come from

A turn's fixed overhead is ~19,000 tokens (tool schemas 14,041 MEASURED live, system prompt
~5,000 on the chat path). The other ~72,000 is the **accrued transcript inside one agent
loop** — every iteration re-sends every prior tool result. Grouping the 14 days' calls into
bursts (same model, <120 s apart) gives 393 bursts at an average of 5.0 calls each, with
**38 bursts above 1M input tokens**; the largest was 10,588,146 tokens across 107 calls, for
$33.38, in about an hour.

That transcript is **append-only**. `agent.py` appends the assistant turn and the tool
results and never rewrites the list mid-loop (`convo.append` at two sites, and
`maybe_compact` runs once *before* the loop). Iteration N+1's prompt therefore contains
iteration N's prompt as an exact byte prefix — which is precisely the shape an incremental
cache reads at a tenth of the price.

### 1.2 What that is worth — MEASURED against the real rows

Replaying the actual 14 days with a rolling message breakpoint, cache reads at 0.1× and cache
writes at 1.25×:

| | billable-equivalent input | share of today | ≈ USD (was $1,057.22) |
|---|---:|---:|---:|
| today (no caching) | 168,887,596 | 100% | $1,057.22 |
| 5-min TTL, messages only | 38,908,086 | 23.0% | $243.56 |
| **5-min TTL, + tools & system reuse** | **33,336,689** | **19.7%** | **$208.68** |
| 1-hour TTL | 24,693,837 | 14.6% | $154.58 |

**~80% off the input line, which is ~99% of the spend. About $60/day at current usage.**

Friday sent **no `cache_control` anywhere** before this audit — a grep across `src/` found
zero occurrences outside documentation and one test.

---

## 2. The thing that would have silently defeated all of it — MEASURED

Caching on both backends works by **byte-identical prefix**: one changed byte at position N
invalidates everything after N.

`clock.clock_context_block()` renders `%Y-%m-%d %H:%M` — **minute resolution** — and
`_build_context_prompt` added it at **position 2**, immediately after `FRIDAY_SYSTEM_PROMPT`,
about 2,500 tokens in. So on any turn that crossed a minute boundary — essentially every turn
— everything below 2,500 tokens was cold, on both backends. A cloud breakpoint placed below
it would have hit *never*, and the bill would not have moved.

The local seat's own log measures the cost of this exactly. Look at the pair, one hour apart,
in the same slot:

```
f_sim_best = 0.469 → prompt eval time = 16,279.10 ms /  25,366 tokens
f_sim_best = 0.983 → prompt eval time =    695.77 ms /     430 tokens
```

Same prompt. The 16.3-second version differs from a warm one only in the clock line. Across
the whole log, **54% of prompt-eval events re-process more than 5,000 tokens, and those
account for 96% of all prompt-processing time** — 351.7 s of 365.1 s. Twenty-one events took
over 10 s; the worst was 21.2 s.

**Fixed.** The clock now renders last, in the volatile tail
(`model_router._build_context_prompt`). Content, authority, TIER_1 status and drop class are
all unchanged — only its position. `prompt_cache.VOLATILE_MARKER` splits the cloud payload on
the `== AUTHORITATIVE CLOCK ==` header, which already existed in the prompt, so nothing new
reaches the model.

**One honest correction to the brief.** The seven-minute answer is not all prefill. The worst
single prompt-eval in 16 hours of log was **21.2 s**. Prefill is real, it is worth removing,
and it is not seven minutes — that has another cause and this audit does not know it.

### 2.1 The half-anticipated gap: closed

A previous session found that `system` as a list of blocks was scrubbed but not tier-gated,
and noted it is "exactly the shape prompt caching needs." **That gap is closed**
(`egress_gate.seal_outbound`, with a test at `test_egress_envelope.py:309` asserting
`cache_control` survives gating). Block-form system is safe to send, and nothing blocks
caching.

Two further prerequisites checked, both clean:
- `core._pii_hash` is **unsalted blake2b of the value** — so `[PII:addr:1a2b3c4d]`
  placeholders are byte-identical across turns and the scrub does not break the prefix. **VERIFIED.**
- `CLAUDE_TOOLS` is built once at import and MCP tools are appended at registration, so the
  tool list serializes identically call to call. **VERIFIED.**

---

## 3. Local model caching — smaller than hoped, real, and now free

The seat is launched with `-ngl 99 --flash-attn on -c 32768 --jinja -b 512 -ub 512`. Its own
banner reports `n_slots = 4, n_ctx_slot = 32768, kv_unified = 'true'` — so this llama.cpp
build runs **four slots over a shared KV pool** with LCP-similarity slot selection, not the
single slot the earlier design note assumed. `cache_prompt` is on by default; **`--cache-reuse`
is not set** (that is the KV-shifting salvage for *non*-prefix chunks, and it is a separate,
measurable question — see §7).

Prefix reuse was therefore already enabled and already working when the prefix held: the p25
prompt-eval re-processes **430 tokens in 0.7 s**. It simply never held across a minute
boundary. With the clock moved, the hourly job's 16.3 s should collapse toward that 0.7 s.

**This is latency, not cost — local tokens are free.** In wall-clock terms the log shows 365 s
of prompt processing in 16 hours, ~96% of it in cold re-prefills. Removing them is worth about
**six minutes of GPU per day** on this workload, and up to **16 seconds off any local turn**
that would otherwise have gone cold. Worth having; not the headline.

---

## 4. Tool-result and data caching — what is safe, what is not

There is **no tool-result cache anywhere in Friday.** The judgment below is the substance of
this section, not a caveat: a stale answer is sometimes worse than a slow one.

### Safe to cache — the inputs change on a slower clock than the calls

| what | today | recommendation |
|---|---|---|
| `voice_engine._load_live_context()` (`TODAY'S CONTEXT`) | **was uncached** — 5 file reads, a sorted `iterdir`, 2 JSON parses **per chat turn**, for content that changes hourly | **DONE: 60 s TTL**, with an `invalidate_live_context()` for briefing writes |
| `local_seats.installed()` | 30 s TTL, but **empty answers were never cached** — see §5 | **DONE: 5 s negative TTL** |
| `news_engine._wiki_title_index()` | 120 s TTL, **empty never cached** | **DONE: gate on timestamp, not truthiness** |
| `model_catalog.build_catalog()` | **uncached**, called on the chat path | 30–60 s TTL. It enumerates providers and probes seats; nothing in it changes inside a minute |
| RSS fetches (`news_engine._RSS_CACHE`) | 300 s TTL, locked | leave alone — already right |
| `model_catalog._ctx_window` | 300 s TTL | leave alone |
| `egress_gate._TOOL_TIER_CACHE` | keyed on text, bounded | leave alone — it already took tool gating from 2,930 ms to 700 ms per call |
| Wiki page reads (`read_wiki`, `search_wiki`) | uncached | mtime-keyed cache. Files on disk; mtime is exact, so there is no staleness window at all |
| `get_briefing`, `get_career_pipeline`, `query_trust_graph`, `knowledge_query` | uncached | mtime-keyed, same argument |

### Must NOT be cached — a stale answer is a wrong answer

- **`search_email`, `search_drive`, `search_contacts`, `query_calendar`, `find_calendar_events`.**
  The Gmail merge's 45 s TTL in `message_triage` is defensible because it backs an *inbox
  view* a human is scrolling. A tool result is different: Friday acts on it. "Is there a
  meeting at 3?" answered from a 45-second-old cache, after the meeting moved, is a wrong
  answer delivered confidently — the failure mode this codebase has spent two days learning
  to hate.
- **Every write tool** — `create_calendar_event`, `draft_email`, `write_file`, `run_command`,
  `create_task`, and the Ring-3 control tools. Caching a side effect means not performing it.
- **`search_web` / `browse_web` / `search_news` at the tool boundary.** The retrieve-cite
  directive lives in these descriptions; a citation must point at what was actually fetched
  for this answer. Cache the HTTP layer (RSS already does), never the tool result.
- **`epistemic_score`, `personality_check_sycophancy`, and the egress gate's message
  classification.** The gate's own comment states the rule and it is correct: *"the
  never-send check is left outside the cache… a floor that a stale cache can hold open is not
  a floor."* Do not touch this for speed.
- **Embeddings** — there is no embedding cache to fix because Presidio and the embedding path
  are absent from the frozen build entirely. Different problem, different audit.

---

## 5. Caching bugs found — the same bug in two more costumes

Both known bugs this week were one value carrying two meanings: an import failure cached as
`None`, where `None` also meant *not yet attempted*; and a span registered before a scrub, so
the cached string never matched the one the gate saw. Two more of the first shape, both now
fixed:

**`local_seats.installed()`** — read `if _CACHE["rows"] and …`, wrote `if rows: _CACHE.update(…)`.
So an **empty inventory was uncacheable**, and empty is precisely the state that costs the
most to produce: no daemon means `urlopen("/api/tags")` runs to its **4-second timeout**, and
the next caller pays it again immediately. It is on the chat path (`routes/chat.py:552`,
seat-still-exists check). It bites hardest on a machine with nothing installed yet — a fresh
install, or the cloud-first 8 GB setup. **That is the machine Friday goes onto tomorrow.**

**`news_engine._wiki_title_index()`** — identical shape. An empty index re-walked both wiki
trees with `rglob("*")` on every call instead of once every two minutes.

Both now gate on the timestamp rather than on the truthiness of the result. "We looked and
found nothing" is an answer worth remembering.

**One more, reported not fixed:** the egress gate re-classifies the **entire message history**
on every iteration of the agent loop (`_gate_messages`, deliberately uncached). At the
measured ~700 ms per call after the tool-tier cache, a 20-iteration task spends ~14 s inside
the gate. The current design is *correct* — an unbounded, content-bearing cache on the
never-send check is exactly what must not exist. But the *earlier* messages in a loop are
byte-identical to what the gate already cleared one iteration ago, and a content-keyed,
bounded, positive-only cache scoped to a single loop would be sound. Specced, not built: it
touches the privacy boundary and deserves its own review, not a midnight patch.

---

## 6. The agent half — what makes working on Friday expensive

The Claude Code sessions that build Friday cache their own prompts; that is not adjustable
from here. What *is* adjustable is how much they have to re-read.

| file | size | ≈ tokens |
|---|---:|---:|
| `index.html` | 1.52 MB | **~380,000** |
| `ui_parts/app.html` | 930 KB | ~232,000 |
| `src/agent_friday/services/agent.py` | 334 KB | ~83,000 |
| `src/agent_friday/routes/voice.py` | 151 KB | ~37,000 |

**`index.html` is larger than a 200,000-token context window.** It cannot be read in one pass
by any model, at any price. Every UI change is therefore done by grep-and-patch against a file
nobody has seen whole — which is exactly how a build script came within one command of
deleting the conversations panel and the model picker. `agent.py` at ~83,000 tokens is 40% of
a window on its own.

**The single cheapest fix is a `CLAUDE.md` at the repo root, and there isn't one.** Every
session rediscovers, from scratch and at full price: that `index.html` is the UI source of
truth and `app.html` is a strict subset; that `python` on PATH is the wrong interpreter and
`./venv/Scripts/python.exe` is right; that `_seal_or_block` is the one cloud chokepoint; that
`_get_friday_system_prompt` callers are pre-commit-checked. All of that lives in Stephen's
*personal* memory, which no other checkout and no fresh agent can see. A repo-level index is
a cache of navigation knowledge, and it is the one cache here that costs an hour to build and
pays on every session forever.

Second: **split `index.html`.** Not tonight, and not by resurrecting `build_ui.py` — that
script is a known hazard. But a 380,000-token single file is a structural tax on every future
change, and it compounds.

---

## 7. Ranked — benefit per unit of work

Effort is honest: **S** ≈ under an hour, **M** ≈ a day, **L** ≈ a spec first.

| # | change | effect | kind | size | status |
|---|---|---|---|---|---|
| 1 | `cache_control` on tools + system + rolling message breakpoint | **~80% off Anthropic input**, ≈ $60/day | cost | S | **DONE** |
| 2 | Clock out of position 2 into the volatile tail | unblocks #1's system tier; **16.3 s → ~0.7 s** on cold local turns | both | S | **DONE** |
| 3 | Hard per-call + per-task ceiling in `_seal_or_block` | makes the 1.43M incident impossible | cost | S | **DONE** |
| 4 | `cache_read` / `cache_write` columns + `cache_hit_rate` in the cost ledger | makes #1 falsifiable | measurement | S | **DONE** |
| 5 | Negative caching: `local_seats`, `_wiki_title_index` | removes a 4 s stall from the chat path on a fresh install | latency | S | **DONE** |
| 6 | 60 s TTL on `_load_live_context()` | ~5 file reads off every chat and voice turn | latency | S | **DONE** |
| 7 | **`CLAUDE.md` at the repo root** | every future session starts oriented | agent cost | S | **TODO — do this next** |
| 8 | TTL on `model_catalog.build_catalog()` | provider enumeration off the chat path | latency | S | TODO |
| 9 | mtime-keyed cache for wiki / briefing / trust-graph reads | zero staleness window by construction | latency | M | TODO |
| 10 | 1-hour TTL for the hourly heartbeat only | $155 vs $209 in the 14-day model | cost | M | spec — needs the §4 counters first |
| 11 | Measure `--cache-reuse N` on the local seat | salvages non-prefix chunks; **unmeasured** | latency | M | spec — requires a restart |
| 12 | Loop-scoped positive cache for `_gate_messages` | ~14 s off a 20-iteration task | latency | L | spec — privacy boundary |
| 13 | Deferred tool schemas (`open_toolbox`) | ~10,500 tokens of headroom per turn | headroom | L | spec exists (`docs/design/context-assembly.md`) |
| 14 | Split `index.html` | stops a 380k-token file taxing every UI change | agent cost | L | spec |

### Leave alone, with reasons

- **RSS 300 s, tool-tier 700 ms, `_ctx_window` 300 s, `judgment_gate` mtime-keyed.** All
  already correct. The tool-tier cache in particular is load-bearing.
- **The never-send check, and `_gate_messages`' *negative* results.** Never cache a refusal to
  a "yes". A floor a stale cache can hold open is not a floor.
- **Calendar, email, drive and contact lookups at the tool boundary.** Friday acts on these.
- **`max_iters=999` itself.** Left as-is deliberately: iteration count is the wrong dimension
  to limit, because a long cheap task is fine and a short expensive one is not. The **token**
  ceiling is the right instrument and is now in place. Flagged so the choice is visible.

---

## 8. How to know whether any of this worked

Nothing here is true until the counters say so, and **none of it is live until Friday next
restarts** — which this audit did not do, and should not, with sessions active and an install
on other hardware in the morning.

After the next restart, one query answers it:

```python
from agent_friday.services import cost_meter
cost_meter.summary("today")["cache_hit_rate"]
```

Expected: **0.6–0.8** on a normal day of chat. Below ~0.2 with caching enabled means a
breakpoint is landing below something volatile — check whether anything new was inserted above
`== AUTHORITATIVE CLOCK ==` in the assembled prompt. That is the one failure mode, and it is
silent: the calls still succeed, at today's price.

The local half checks the same way, from the seat's own log:

```
grep "prompt eval time" ~/.friday/runtime/logs/llama-*.log | tail -20
```

Today's baseline for comparison: **p50 18,359 tokens re-processed, 54% of events above 5,000,
worst 21.2 s.**

---

## 9. Open questions for Stephen

1. **The per-task ceiling is 4,000,000 input tokens.** That would have refused the largest
   real burst in 14 days (10.6M, $33.38, ~1 hour). Deliberate — that burst should have asked
   first. Say if it should be higher.
2. **The per-call ceiling is 180,000 tokens**, above the measured p99 (166,799) and below the
   200k window. It refuses nothing Friday does today.
3. **Opus-5 is $669.63 of the last fortnight's $1,057.22 across 502 calls** at an average
   86,117 input tokens each. Caching cuts that to roughly $130. The routing question — how
   many of those 502 needed Opus — is a separate audit, and probably a larger number than
   this one.
