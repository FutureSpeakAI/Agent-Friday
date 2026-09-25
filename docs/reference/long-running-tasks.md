# Long-running tasks: context, compaction, the task ledger, and resume

> **Status:** implemented
> **Last verified:** 2026-09-25
> **Code:** `services/compaction.py`, `services/task_ledger.py`, `services/task_resume.py`, `pipeline/context_compressor.py`, the two agent loops and `_task_worker` in `services/agent.py`
> **Tests:** `tests/unit/test_compaction_privacy.py`, `test_compaction_mechanics.py`, `test_long_local_run_stays_bounded.py`, `test_ledger_resume.py`, `test_headroom_wrapper.py`, `tests/api/test_task_resume.py`, `tests/api/test_compression_stats_route.py`

A long job on a local seat outlives its context window many times over. Four
mechanisms keep it bounded, correct and alive.

## 1. Who writes a summary (privacy)

The middle of a transcript holds whatever its seat was allowed to see. The
summary is therefore written where the transcript already is:

| Caller | Summarizer |
|---|---|
| OpenAI-format loop (Ollama, llama-server, OpenRouter) | the same seat, through the loop's own `send_fn` |
| Anthropic loop | Claude, through the loop's client |
| anything else (`_default_summarizer`, chat's pre-routing `_compress_trajectory`, the 23:30 end-of-day summary) | a local model only: runs under `local_only_guard.local_only`; with no local model there is no summary |

A Claude-written summary is a cloud call like any round: it passes
`model_router._seal_or_block` (spending cap, size ceiling, egress seal) and is
metered. A refusal the router returns instead of generated text
(`model_router.RoutedRefusal`) is never taken as a summary, and summary and
ledger text is registered with the taint record as outside content
(`taint.note_carried`), under the task's own key.

A local seat's transcript never goes to another model to be summarised.

## 2. The window compaction budgets against

`compaction.resolve_context_window(model)`:

1. what a llama-server seat reports it is serving (`/props`, per slot);
2. when it is not serving, the window the arbiter would load it at
   (`models.json` `serve_num_ctx`, under `MAX_SEAT_NUM_CTX`);
3. the residency plan, the catalog, `compaction.context_window`, 200,000.

A model's `models.json` record can declare how it is served, and the
declaration wins over the arbiter's global defaults: `serve_args` carrying
`--cache-type-k`/`--cache-type-v` keeps its KV type (the global
`kv_cache_type` is not appended over it), and `"vision": "on_demand"` loads
the seat without its projector until `local_vision.describe` receives an
image, which reloads the seat once with the projector at the same port and
context. On the 12 GB card, Bonsai2 at 131,072 with q4_0 KV, `-ub 512` and
no projector left 1,455 MiB free and prefilled 16K tokens at 511 tok/s; the
live 49,152 q8_0 seat with its projector left 639 MiB and prefilled at 109.

The 4-characters-per-token estimate under-counts tool output (JSON counts
~1.5x). Every round feeds the provider's own prompt-token count back
(`compaction.observe`), and budgets use the calibrated count. Tool schemas and
the system prompt are counted and reserved like the reply. A request a seat
refuses as too long is compacted harder and sent once more.

Compaction runs before the first round and between tool rounds. It keeps tool
calls and their results on the same side of each cut, keeps the verbatim tail
to a quarter of the budget, summarises long middles in rolling chunks, and
trims oversized tool results (never the user's newest words) when summarising
cannot reach them.

Headroom (`pipeline/context_compressor.py`) compresses each round's new tool
output in-process before any of this. It is pinned to telemetry off, an
in-memory store (0.3x otherwise keeps uncompressed originals in plaintext
SQLite under `~/.headroom`), and Friday-owned workspace/tokenizer caches. A
wheel without the native core (`headroom._core`) reports itself unavailable
with the reason; it is not counted as compressing. A call that removes no
tokens is a pass-through, and a Headroom that has made nothing smaller after
three attempts reports itself unavailable too. The pin is `headroom-ai==0.38.0`
(base package; the `[all]` extra adds ~30 integrations). Its first use
downloads a tokenizer vocabulary (tiktoken) into Friday's home. Savings:
`GET /api/context/compression-stats` (`compression` for Headroom, `compaction`
for summaries and trims).

## 3. The task ledger

Every background task keeps `ledger.json` in its journal directory (encrypted
like the journal): goal, every tool step (with distinct-step signatures),
FACTS, PLAN, FILES, NEXT, the step in flight, and how the task was started.

- Steps are recorded mechanically after each tool round.
- Each compaction asks the seat for PLAN / FACTS / FILES / NEXT and replaces
  those sections; the summarizer is always handed the whole current ledger, so
  a fact can be restated but not dropped by omission.
- The ledger is pinned as the compaction summary block (the one place the
  transcript is rewritten anyway), sized to a quarter of the seat's window, so
  a local seat's prompt cache is not invalidated every round.

## 4. Continuing and resuming

- **Per-turn limits are leg boundaries for background work.** A leg that ends
  on the round, clock or token limit (or ran long) while still taking new
  distinct steps starts a fresh leg from the ledger, with no question to the
  user. It stops when a leg takes no new step, when the loop detector says it
  is going in circles, or when the user stops it.
- **After a crash, restart or reboot** the task is resumed from its transcript
  checkpoint (Anthropic loop) or, when there is none, from its ledger through
  its own worker. `task_resume_auto` is on by default and runs where the boot
  restore knows which tasks are resumable.
- **Guards:** `MAX_ATTEMPTS` resumes per task across both paths; a step in
  flight that is not a pure read (Ring 0) waits for a person.

Not resumable: an interactive chat turn (it has no task id), and live
attachments (a voice session, a streaming socket).
