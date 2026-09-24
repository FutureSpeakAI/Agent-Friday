# Reasoning traces

> **Status:** implemented
> **Last verified:** 2026-09-24
> **Code:** `services/reasoning_trace.py`, `routes/traces.py`, hooks in `services/agent.py`, `services/model_router.py`, `routing/ollama_manager.py`, `routes/chat.py`, `services/scheduler.py`
> **Tests:** `tests/unit/test_reasoning_trace.py`, `tests/unit/test_reasoning_trace_emission.py`, `tests/api/test_reasoning_traces_api.py`

Every model call Friday makes records the reasoning it returned into a
**trace**. A trace is one unit of work: a chat turn, a subagent task, a
scheduled job run, or a background call nothing else wraps. It holds, in
order, each reasoning segment, each tool call with its arguments and a result
summary, the words the model said before calling tools, and the model, seat
and token counts of every call. A subagent's trace names the trace that
spawned it, so a turn and everything it started read as one tree.

## Where traces come from

| Path | How it is traced |
|---|---|
| `/api/chat`, `/api/chat/send`, `/api/chat/stream` | `_traced_turn` opens a `chat` trace per turn; the reply carries `trace_id` and `reasoning_sources`; the stream sends `{"trace_id"}` before the first token |
| Subagents (`_spawn_task`, `spawn_task` tool, scheduled `agent_prompt` jobs) | the spawning thread records `parent_trace_id` on the task; `_task_worker` opens a `subagent` (or `scheduled`) trace under it |
| Builtin scheduled jobs | `scheduler._run_task` opens a `scheduled` trace per run |
| Anything calling `_generate_text` / `_generate_agent` with no active trace | a `background` trace named after the orb label |

The context is a contextvar plus a thread-local. Work handed to a new thread
carries the trace id explicitly; a fresh thread never inherits a trace.

## What a label means

Every reasoning segment carries a source key, and the UI shows its label
verbatim. Nothing is paraphrased, summarised or generated to fill a gap.

| Key | Label | When |
|---|---|---|
| `full` | full reasoning (local) | a seat on this machine returned `reasoning_content` / `reasoning` / Ollama `thinking` |
| `provider` | reasoning as returned by provider (may be summarized) | a cloud OpenAI-compatible provider returned reasoning text |
| `summary` | provider's reasoning summary | a Claude `thinking` block with text |
| `not_exposed` | reasoning not exposed by provider | a cloud call returned no readable reasoning (including an empty Claude `thinking` block) |
| `redacted` | reasoning not exposed by provider (redacted/encrypted) | a `redacted_thinking` block or encrypted reasoning details; the payload is not stored |
| `none` | no reasoning produced | a local model that did not reason, or a Claude model with thinking not enabled |

Claude 5 models (Opus, Sonnet, Fable, Mythos) think by default and return
empty thinking text unless asked. Friday asks for
`thinking: {"type": "adaptive", "display": "summarized"}` on those models,
which changes what comes back and not how much is thought or billed. Older
Claude models are not switched into thinking. The raw chain of thought of a
Claude model is never available; what is shown is the provider's summary.

## Live view

Reasoning deltas from a streamed seat reach the trace as they arrive and are
coalesced per trace (every 240 characters or 150 ms) before they enter the
feed. `GET /api/traces/live?since=<cursor>` returns every event after the
cursor across all traces; `?snapshot=1` returns running traces and those
finished in the last ten minutes, with their events. The UI store polls only
while something is subscribed, notifies at most once per animation frame, and
renders long reasoning in virtualized chunks.

## Archive

A finished trace with at least one event is appended to
`<friday_home>/traces/ledger.jsonl`. Each line is:

```
{"v":1,"type":"trace","trace_id":…,"ts":…,"body":<base64 of the encrypted record>,
 "seq":n,"prev":<hash of line n-1>,"hash":sha256(line minus hash/sig),"sig":hmac(key, hash)}
```

- **Encrypted.** `body` is the record sealed by `credential_store.protect`
  (the keystore). If protection is unavailable or the keystore is locked, the
  trace waits in memory (up to 500) and is written with the next successful
  write; it is never written as plaintext.
- **Tamper-evident.** Editing, removing, inserting or reordering a line breaks
  `GET /api/traces/verify`, which reports the first bad line and why.
- **Stable key.** The HMAC key is derived (HMAC with a fixed label) from the
  file-grants ledger's persisted signing key, which is minted once, stored
  through the keystore and never read from the environment. Restarts and
  launcher edits do not change it.
- **In the activity ledger.** Each archived trace also writes a metadata-only
  `reasoning_trace` row (ids, model, seat, source, counts, ledger seq and
  hash) to `activity_ledger.jsonl`. Reasoning text, labels and tool arguments
  stay in the encrypted record.

## Retention and export

`reasoning_traces.retention_days` defaults to `0`, which keeps everything.
With a threshold set, retention runs at most once a day on the write path and
drops whole records older than the threshold. The file is rewritten to open
with a signed anchor naming the hash and sequence number the surviving chain
resumes after, and how many records have been pruned, so the chain still
verifies. Retention refuses to prune a chain that already fails verification.

`GET /api/traces/export` downloads every archived trace, decrypted, as NDJSON;
the first line is the verification report.

## Who can read traces

The `/api/traces` routes answer the local user on this machine only. An
observer credential is refused, and so is an authenticated remote session,
because serving the archive over a tunnel would be the archive leaving the
machine. The module itself has no network code.
