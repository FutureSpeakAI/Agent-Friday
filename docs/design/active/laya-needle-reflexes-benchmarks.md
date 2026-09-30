# Reflex benchmarks: definitions

**Status:** definitions, in force. Companion to
[laya-needle-reflexes.md](laya-needle-reflexes.md), which holds the results.
**Written:** 2026-09-29.

This file defines every measurement the reflex spec quotes, so anyone can
re-run it on their own machine. The raw data, the owner's real phrasings and
every per-row result stay under `~/.friday/bench/reflexes/` and never enter
the repository. This file holds only definitions, grading rules and the
utterance sets that carry no personal content.

---

## 1. Where things live

| What | Path | In repo? |
|---|---|---|
| Definitions and grading (this file) | `docs/design/active/laya-needle-reflexes-benchmarks.md` | yes |
| Results, aggregate only | `docs/design/active/laya-needle-reflexes.md` §6 | yes |
| Usage miner (read-only over `~/.friday`) | `~/.friday/bench/reflexes/mine_usage.py` | no |
| Usage aggregates | `~/.friday/bench/reflexes/usage_taxonomy.json` | no (counts only; quoted in the spec) |
| Every user message, labelled | `~/.friday/bench/reflexes/raw_user_messages.jsonl` | **no, private** |
| Owner's short phrasings, brain-labelled | `~/.friday/bench/reflexes/command_phrasings.jsonl` | **no, private** |
| Friday's tool schemas, extracted | `~/.friday/bench/reflexes/friday_tools.json` | no (derivable from `services/agent.py`) |
| Needle bench | `~/.friday/bench/reflexes/needle_bench.py` | no (reproduced in §3) |
| Laya shape bench | `~/.friday/bench/reflexes/laya_resolver_bench.py` | no (reproduced in §4) |
| Per-row results | `~/.friday/bench/reflexes/results/*_rows.jsonl` | **no, private** |
| Aggregate results | `~/.friday/bench/reflexes/results/*.json` | no (copied into the spec) |
| Needle's isolated venv | `~/.friday/bench/reflexes/needle-venv/` | no |
| The 118-example tool set | `~/.friday/bench/kvq-bonsai2/t2_single_examples.json` | no (built by `build_t2.py` there from Friday-Models `eval/heldout/t2_single_tool.jsonl` and Friday's own schemas) |

Nothing in this work writes anywhere else under `~/.friday`, and nothing
touches the live server or the live checkout.

---

## 2. The usage taxonomy

**Sources read (read-only):** `chat_history.json`, `conversations/*/messages.jsonl`,
`trajectories.jsonl`, `memory/conversations/*`, `decisions.jsonl`,
`decision-bom.jsonl`, `approvals.json`, `forensics/tasks.jsonl`,
`schedule_runs.jsonl`, `costs.db` (`cost_calls`), `voice_debug.log`, and
store sizes for `wiki/`, `front_pages/`, `editorials/`, `news/archive/`,
`workflows/`, `routines/`, `tasks/`, `knowledge-graph/entities.json`,
`schedules.json`.

**Unit:** one user message. Messages logged in two stores within 60 s of each
other with the same text are one message; the record that carries the tools
the brain then called wins.

**Channel:** `voice` when the record says so (`via: voice` in the chat
history, `meta.via`/`meta.channel` in conversations), otherwise `text`.

**Intent classes** (first matching rule wins; regexes are in `mine_usage.py`):
`navigate_open`, `mail`, `calendar`, `news_briefing`, `wiki_knowledge`,
`files_code`, `web_research`, `creative_media`, `device_control`,
`workflow_task`, `settings_model`, `career`, `status_check`,
`approval_reply` (only when 6 words or fewer), `greeting_smalltalk` (6 words
or fewer), then `open_question` (question-shaped), `other_command`
(imperative-shaped), else `conversation`.

**Shape:** `command` (imperative verb first, or a navigate/device/approval
class), `question` (interrogative first word or a trailing `?`), `statement`,
`conversation`.

**Reflex candidate:** a `command`-shaped message of 12 words or fewer whose
whole turn used zero or one distinct tool, as the trajectory recorded.

The taxonomy is coarse on purpose. It is a map of volume, not a labelling of
truth; the spec says so wherever it quotes it.

---

## 3. Needle

**Install:** `cactus-needle` (3.0.6 at the time of writing) in its own venv.
Engine binaries and base weights are fetched from Hugging Face
(`Cactus-Compute/needle2`, `Cactus-Compute/needle3`) into
`~/.cache/cactus-needle/` on first use.

**Environment, always:** `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1` before
import. The package's telemetry is on by default and posts anonymous event
counts to a vendor endpoint; amendment A3 forbids that in Friday, so it is off
for every measurement and must be off in any build.

**Construction:** `needle.Needle(tools=<list of {name, description, parameters}>,
generation=<2|3>, stateless=True)`; one `complete("hello there")` warm-up,
then `complete(text)` per row, timed with `perf_counter` around the call.
Descriptions are clipped to 600 characters (Friday's own descriptions run to
~1,000 characters; the clip is noted as a limitation, not hidden).

### 3.1 Set `t2`, 118 rows, 8 tools

`t2_single_examples.json`: 118 distinct prompts from Friday-Models
`eval/heldout/t2_single_tool.jsonl` over Friday's 8 most common tools, with
Friday's own schemas. Tools: `search_web`, `browse_web`, `read_file`,
`search_files`, `write_file`, `write_clipboard`, `query_trust_graph`,
`annotate_calendar_events`. Every row expects exactly one call. The reference
answers carry no arguments, so:

- **name accuracy:** the first call's name equals the expected tool;
- **args accuracy:** name correct **and** every schema-required argument
  present and non-empty (a floor on usefulness, not a match to a reference).

### 3.2 Set `nocall`, 49 rows, the same 8 tools

Utterances none of the 8 tools should answer. They are the shapes the usage
mining found most often among non-tool turns: greetings and thanks,
conversation about Friday herself, questions she answers from her own
knowledge, edits to her last reply, device control with no matching tool,
approvals and cancellations, and one settings request. No personal content.

```
good morning
thanks, that's all for now
you're doing great today
haha that's funny
tell me a joke
how are you feeling
what do you think about that
never mind
let's talk about something else
I'm tired, long day
can you sing
what's your name
who made you
explain what a mutex is
what's the difference between a list and a tuple
summarise what we just discussed
repeat that more slowly
say that again
translate hello into french
count to five
what's 17 times 23
spell necessary
play some music
pause the music
turn the volume down
mute
set a timer for ten minutes
next track
stop talking
go to sleep
wake up
are you still there
what are you working on right now
how long will that take
why did you do that
that's wrong, try again
no, the other one
yes go ahead
cancel that
forget it
rewrite that paragraph in plain english
make it shorter
give me three options
what's the capital of peru
how many days until friday
what year is it
remind me what I asked you yesterday
be more concise from now on
switch to a local model
```

Grade: **no-call correctness** = share of rows where `function_calls` is
empty. **Over-call rate** is its complement.

### 3.3 Set `real`, the owner's phrasings, 20 tools (private)

Rows come from `command_phrasings.jsonl`: the owner's own messages of 14
words or fewer, command- or question-shaped, deduplicated, each labelled with
the tool the brain called **first** on that turn (`brain_first_tool`), or
`none` when the brain used no tool on a question-shaped message. A command
the brain answered with no tool is excluded: it is not a clean "none" (often
the deterministic navigate/open reflex answered it before the brain saw it).

The catalogue offered is 20 of Friday's real tools with their real schemas
from `services/agent.py`: `search_web`, `browse_web`, `read_file`,
`search_files`, `write_file`, `search_email`, `query_calendar`,
`find_calendar_events`, `search_wiki`, `read_wiki`, `knowledge_query`,
`search_news`, `open_url`, `open_path`, `navigate`, `spawn_task`,
`generate_image`, `run_command`, `workflow_status`, `get_briefing`.

Rows whose label is not one of those 20 or `none` are dropped. Grade as §3.1.

**Caveat, stated in the spec:** the label is what the brain did, not what
was right. It is the correct comparator for "could a reflex have made the
same call", and a floor, not a ceiling, for accuracy.

### 3.4 Conditions

- `idle`: nothing else of Friday's running on the CPU; the GPU brain not
  resident.
- `busy`: a llama.cpp seat serving Bonsai2-27B on the GPU **and generating**
  throughout the run, on a port of its own, so the CPU sees the seat's real
  sampling and tokenising load. The live server's seat is left alone.

### 3.5 Reported

Per set, generation and condition: `n`, `name_acc`, `args_acc`,
`over_call_rate` (nocall), `under_call_rate` (calls expected but none made),
`ms_p50`, `ms_p95`, `ms_max`, median confidence when right and when wrong,
and for thresholds 0.50 / 0.70 / 0.85 the share of rows the reflex would act
on and the precision among those.

---

## 4. Laya shapes

Laya 0.3.5, the ONNX fp32 encoder that ships (`~/.friday/models/laya-onnx/laya-fp32.onnx`,
agreement with torch fp32: 0 of 150 answers differ, from the artifact
manifest), decision head in torch, threads as stated per run. `FRIDAY_HOME`
points at a scratch directory so importing `agent_friday` never touches the
real home.

Ten neutral states (no personal content) are asked each question shape
twice after one warm-up call:

| Shape | Question | Bucket | Stands for |
|---|---|---|---|
| `yesno_1` | `direct_command` (noul) | noul:2 | command-or-conversation |
| `yesno_2` | `direct_command` + `touches_private`, one pass | noul:2 | + private-or-not |
| `choice_2` | `changes_outside` | choice:2 | outward-or-not |
| `choice_5` | action pick: open / search / send / organize / none | choice:3-5 | action and workspace pick |
| `choice_10` | "which of these ten items" | choice:6-10 | the item resolver's shortlist |
| `choice_12` | the same with twelve | choice:11+ | the clamped bucket, for the record only |

Reported per shape: `n`, `p50_ms`, `p95_ms`, `max_ms`, median confidence.
The reference numbers from commit `efb7a539` (gate 1 question, voice 1 and 2
questions, at 4 and 6 threads) are quoted beside them.

---

## 5. End-to-end budget

The spec's per-surface budgets are built from these measured parts:

| Part | Source |
|---|---|
| Laya question (per shape) | §4 |
| Needle call (per generation, per condition) | §3 |
| Hybrid tool shortlist | `services/tool_selector.py` docstring: 106–267 ms per turn, live 75-tool catalogue |
| Deterministic reflexes (navigate, open) | `routes/chat.py` fast paths, no model |
| Cloud chat turn | `costs.db` `cost_calls`, `kind = chat`, cloud providers, last 30 days |
| Local brain turn | `costs.db` local providers; `forensics/tasks.jsonl` elapsed for background work |
| Voice first audio | `services/voice_engine.py` docstring: 0.9 s first audio on the live model, 0.55 s on a resumed session |

A "reflex turn" is: transcript or text in → Laya shape(s) → template fill
from real IDs → gate → act → spoken/on-screen confirmation, with no brain
call. A "brain turn" is the same request through the agent loop.
