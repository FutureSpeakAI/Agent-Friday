# The Front Page that "hit max iterations" — what actually happened

Status: DIAGNOSED and FIXED (2026-09-22). Reproduced live against the running
bonsai2:27b seat three times before anything was changed.

The report: *"Bonsai2 churned on putting together a new front page for nearly
30 minutes and when I refreshed localhost the process had failed due to max
iters."*

Every part of that is what the software said. None of it is what happened.

## The run

`~/.friday/forensics/orbs.jsonl`, orb `openai-e8f20a1d`:

| field | value |
| --- | --- |
| label (first 14 captures) | `📰 Front Page` |
| model | `bonsai2:27b` via `arbiter-local` |
| started | 2026-09-22 09:55:45 |
| ended | 2026-09-22 10:24:46 |
| elapsed | **1,741 s (29 min 1 s)** |
| progress | `0` for the entire run, then `1.0` |
| steps | `[]` — empty, start to finish |
| final status | `error`, `orb_failed: true` |
| final label | **`Max iters`** |
| result | `[Agent hit max tool iterations without completing.]` |

`costs.db` has one row for it: `10:24:46 arbiter-local bonsai2:27b in=0 out=0
dur=0s`. The edition file `~/.friday/front_pages/2026-09-22-morning.json` was
written at 10:24 with `headline: "Your Front Page"`, the verbatim fallback
lead note, zero section context, no Contrarian Corner and no day-in-context.

## It was not a loop, and there was no iteration budget

The Front Page editorial is a **single-shot** call:
`news_engine._editorialize_front_page` → `_generate_text` → (seat is
Arbiter-owned, so) `_call_openai` → `_oai_agentic_loop` with `tools=None`.

```python
loops = max_iters if oai_tools else 1     # tools is None → loops = 1
```

One round. `steps: []` and the single cost row agree. Whatever ended this
call, it was not fifty iterations of anything.

## What it actually did, measured

Instrumenting `send_fn` at the loop seam and re-running the real
`_editorialize_front_page` against the live seat:

```
LOOP ENTER: oai_tools=None max_iters=50 model=bonsai2:27b
ROUND 1: {"secs": 320.8, "finish_reason": "length", "content_len": 0,
          "reasoning_len": 0, "keys": ["content", "role"]}
ROUNDS=1
fallback=True
```

`finish_reason: "length"` — the model spent its **entire** `max_tokens=1800`
budget — and delivered **zero characters**. Prompt: 25,410 tokens (88,221
characters of it the vault/persona system prompt, against 9,083 characters of
actual editorial request).

Probing the seat directly with a one-line prompt shows where the tokens went:

```
delta keys seen: {'role': 1, 'content': 16, 'reasoning_content': 49}
```

`bonsai2:27b` is a reasoning seat. It splits its output across `content` and
`reasoning_content`, `max_tokens` is spent on **both**, and
`_consume_sse_completion` collected only `content`. So 1,800 tokens of real
work were thrown away at the transport, and everything above it saw a blank.

## Five links, each one silent

1. **`max_tokens=1800` was sized against the JSON**, not against the JSON plus
   a reasoning seat's scratchpad. On this seat the budget is exhausted before
   the answer begins. *(`news_engine.py`)*
2. **`_consume_sse_completion` dropped `reasoning_content`.** The turn arrived
   as `{"role": "assistant", "content": ""}` — indistinguishable from a model
   that said nothing at all. *(`model_router.py`)*
3. **The empty-completion retry never ran.** The guard's own comment promises
   "one retry that tells the model what happened, then an honest failure". Its
   `continue` spent the only round, so the `for` loop ended instead.
   *(`agent.py`)*
4. **The fall-through blamed an iteration budget that did not exist**, setting
   the orb label to `Max iters` and returning `[Agent hit max tool iterations
   without completing.]`. This is the sentence Stephen read. *(`agent.py`)*
5. **The caller swallowed it.** `_extract_json_block` could not parse that
   string, so `if not isinstance(data, dict): return fallback` — no log, no
   record, no notice. The un-curated edition was written to disk and a normal
   `📰 Friday's Front Page — Morning edition` notification was pushed.
   *(`news_engine.py`)*

## It had been happening for four days

Every edition in `~/.friday/front_pages/` since `bonsai2:27b` became the seat
is the fallback. Detection: the lead note equals the hard-coded fallback
string and `section_context` is empty.

| edition | curated? |
| --- | --- |
| 2026-09-18 morning | **yes** (gemma4:e2b-fridayweaver, 39 s) |
| 2026-09-18 evening → 2026-09-22 morning | **no — nine consecutive** |

Nine "your Front Page is ready" notifications, nine pages that looked exactly
like curated ones, and no signal anywhere that the editor had not run.

## What was changed, and what was deliberately not

**Not** raised: `max_iters`. It was never the binding constraint, and a bigger
number would have bought a longer identical failure.

| change | file |
| --- | --- |
| Keep `reasoning_content` / `reasoning` deltas on the reassembled message (out of `content` — a scratchpad is not an answer) | `model_router.py` |
| Grant the repair round instead of spending the last one on it, so the promised retry happens on tool-less calls too | `agent.py` |
| Tell a reasoning seat that hit the ceiling to stop deliberating and answer, rather than the generic "your response was empty" | `agent.py` |
| Report truncation-while-thinking as itself, naming the seat, the budget and the remedy | `agent.py` |
| `Max iters` only when a tool budget really ran out, and say how many steps it took | `agent.py` |
| Size the editorial's output budget to what the schema asks for, with headroom for the scratchpad | `news_engine.py` |
| Both soft-fail exits return the fallback **marked** `degraded` (reason, the reply quoted, seat, seconds) and log once | `news_engine.py` |
| Store `editorial_status` on the edition | `news_engine.py` |
| A failed re-run cannot overwrite a curated edition; the write is atomic | `news_engine.py` |
| The notification for a degraded edition says so, names the seat, and goes out at `high` | `news_engine.py` |
| The Front Page shows a "Ranked, not curated" banner with the reason and the reply | `index.html` |

## Still open, deliberately

**The 88,221-character system prompt.** The Front Page editorial gets the full
vault/persona prompt — 22k of the 24k prompt tokens — for a task whose input
is a list of headlines. It is most of the 320 s per attempt and most of the
prompt-cache churn. Narrowing it is a product decision about what the editor
is allowed to know, not a bug fix, and it is Stephen's call. The last
**curated** edition (2026-09-18, gemma4:e2b-fridayweaver) took 39 seconds.

**One llama-server slot.** `total_slots: 1`, so any concurrent local work
serialises behind the editorial. The 09:55 run overlapped a heartbeat and
several chat turns; `machine_monitor` logged `thrash breached` at 09:59:21.
The same call, run alone at 10:42, took 154 s. This is why the same defect
produced 320 s once and 1,741 s another time.

## Relationship to the other two

This is the third instance this week of a crude threshold ending healthy work,
after the 4,000,000-token ceiling and the fifteen-minute chat-seat timer (both
replaced with liveness-based signals in
`docs/design/active/midstream-durability.md`). It is the same family with a
twist: here the threshold that bound (`max_tokens`) and the threshold that got
blamed (`max_iters`) were not the same threshold.

`midstream-durability.md` names `_oai_agentic_loop` as "the largest remaining
gap" for checkpointing. This change does not close that gap. It fixes the
loop's tool-less leg, which has no partial transcript to checkpoint — one
round, one answer — and the work that was being lost there was being lost at
the transport, which is where it is now kept.

## Verification, and what could not be verified

**Reproduced, before any change** — three times against the live
`bonsai2:27b` seat, each ending in the fallback. The third run was
instrumented at the loop's send seam and produced the `ROUND 1` line quoted
above.

**The mechanism, measured at the wire** — a direct probe of
`http://localhost:8090/v1/chat/completions` with a one-line prompt returned
`{'role': 1, 'content': 16, 'reasoning_content': 49}`. That is the whole
diagnosis in one line: the seat splits its output, `max_tokens` covers both
halves, and the reassembler was reading one.

**Tests** — 26 across the three seams, each shown able to fail:

| revert | result |
| --- | --- |
| the one-line repair-round grant | 6 of 7 loop tests fail, and the failure text reproduces the original `[Agent hit its 1-step tool limit …]` shape |
| the `reasoning_content` capture | 3 of 4 transport tests fail |
| the `degraded` marking + the re-run guard | 5 of 12 front-page tests fail |

The tests that survive each revert are the guards — "a good round is still
one round", "no reasoning means no key", "a better re-run still replaces a
degraded edition" — which are about *not* changing behaviour and should
survive.

**Regression** — the 98 test files under `tests/` that import any of the
three changed modules, run in three batches: all green except
`tests/api/test_creative_pipeline_routes.py::test_project_crud_and_bible`
and `::test_create_project_requires_name`, which fail identically at
`25ddfbf` (the commit this branch starts from) and are unrelated to this
change.

**The UI** — the real markup and CSS rendered against four edition shapes:
degraded with a seat and a timing, degraded with neither, curated, and an
edition written before `editorial_status` existed. Only the two degraded
cases render anything; the re-run button dispatches.

**The honest failure fired live**, in `~/.friday/friday.log`, a line that
did not exist before this change:

```
WARNING friday.news_engine — front page (morning): no editorial — the
editorial call failed. model=? after 237.7s. RuntimeError: No model provider
could generate text (tried local (bonsai2:27b): Connection broken…
```

Note `model=?`: nothing had recorded a generation, so it declined to name a
seat rather than printing the configured default.

**NOT verified: a live curated Front Page.** Three attempts after the fix
all died the same way — the llama-server seat dropped the connection
mid-generation and restarted. That is a condition of the machine at the
time, not of this change: `machine_monitor` logged `display breached --
1528 MiB free against a 2560 MiB display reserve` at 11:31:05 and `thrash
breached` at 11:32:20, the system volume had been driven to 259 MiB free at
11:13 by a concurrent test run, and `friday.server` logged repeated
single-instance-lock collisions. The seat needs to be stable before the
success path can be demonstrated end to end; until then the claim here is
"the failure path is now honest", not "the editorial works".
