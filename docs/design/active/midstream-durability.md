# Midstream durability — resuming a task that died mid-flight

Status: BUILT (2026-09-22). Checkpointing on by default; auto-resume off.
Code: `services/task_resume.py`, hooks in `services/agent.py`, routes in
`routes/tasks.py`, storage helpers in `services/task_journal.py`.

## The problem

The desktop server dies with work in flight. Sometimes a real crash, sometimes
the recurring silent hang — process alive, `friday.log` goes quiet, nothing
moves. `task_journal.reconcile_on_boot` already noticed and was honest about
it:

> `[Interrupted] The process stopped before this task finished. Its record is
> complete up to the last checkpoint; it was not resumed.`
> `resume_hint: "Re-run the task from its prompt; nothing resumes automatically."`

That is a complete *record* and a total *loss*. Re-running from the prompt
throws away every search, read and draft the task had already produced, and
re-runs every side effect it had already taken. For a task that was ninety
percent finished that is the worst of both: the work is gone and the effects
happen twice.

## What makes resume possible

The agent loop's entire recoverable state is its `convo` list.

Anthropic's tool protocol is append-only and self-describing. A `tool_use`
block is answered by a `tool_result` block, and once that pair is in the
transcript the tool's work is a **fact in the conversation**. So restoring the
convo restores the work *and* guarantees no completed tool runs twice — the
model reads the result it already got and moves on.

We replay nothing. We re-enter the loop with the transcript it had.

## Where the checkpoint is taken, and why only there

One line, in `_call_claude_agent`, immediately after:

```python
convo.append({"role": "user", "content": tool_results})
```

At that instant every `tool_use` in the convo has its matching `tool_result`.
That is the only shape the API will accept back. A checkpoint one line earlier
saves a transcript that 400s on resume — which would look like "resume is
broken" rather than "the checkpoint was taken at the wrong moment", so
`task_resume._consistent()` asserts the property on the way back in too.

Cost: one atomic write per tool round. Storage: `resume.json` in the task's own
directory under `~/.friday/tasks/<id>/`, written through
`task_journal.write_blob` so it inherits the journal's atomic temp-file+rename,
its at-rest protection (vault key, then DPAPI), and its deletion — `delete()`
rmtrees the directory, so removing a task removes its transcript.

The system prompt is deliberately **not** stored. It is rebuilt at resume from
persona + vault; a copy on disk would be a second home for vault content. Only
a 16-char fingerprint is kept.

## What cannot be resumed

Stated here rather than discovered later.

**A tool that was in flight.** If the process died between "dispatch
`send_email`" and "got the result", the mail may or may not have gone. The
transcript cannot say and neither can anything else. So a tool is marked
pending *before* dispatch and cleared *after* — and cleared on the success path
only, never in a `finally`: if `_execute_tool` raised, the side effect is
exactly as unknown as it is after a process death, and a `finally` would erase
the one marker that says so.

On resume, a pending tool gates the whole thing unless it is Ring 0. The line
is at Ring 0, not Ring 2, because the question is not "how dangerous is this
tool" but "does running it twice differ from running it once". Ring 1 is a
local *write*; an append run twice is not an append run once. Ring 0 reads and
nothing else, so re-running it changes nothing and needs no question.

Everything above Ring 0 waits for a human answer (`confirm_pending`). This is
the one place where guessing is worse than stopping.

**Side effects already taken.** A file written, a calendar event created,
money spent. Those happened. They are not rolled back and resume does not try.
The transcript records them and the resumed task continues from after them.
That is the correct behaviour and also the only available one.

**Live attachments.** An open voice session, a browser the user was watching,
a streaming response half-delivered to a socket. The task resumes; those do
not come back.

**The first iteration.** A crash during the very first tool call leaves no
consistent checkpoint, so there is nothing to resume — re-run from the prompt,
which is what `/rerun` already does and which loses nothing at that point.

**The OpenAI-shaped loop** (`_oai_agentic_loop`). Not checkpointed yet. Its
transcript has the same append-only property so the same approach applies; it
is unbuilt, not impossible. Local seats and every OpenAI-compatible provider
run through it, so this is the largest remaining gap.

## Why auto-resume is off by default

`task_resume_auto` defaults to `False`.

A task that crashes Friday will crash it again on resume. Automatic resume
turns one crash into a boot loop that bills on every pass. So resume is
*offered*: `task_resume.announce()` pushes a medium-priority notice at boot
saying what survived, separate from the journal's existing interruption notice,
and the user clicks.

Even the opt-in path counts attempts. `MAX_ATTEMPTS = 2`: the second failure is
data, the fifth is a boot loop.

## Settings

| key | default | meaning |
| --- | --- | --- |
| `task_resume_enabled` | `True` | write the checkpoint at all |
| `task_resume_auto` | `False` | resume without being asked |

Both are top-level, matching the convention `prompt_cache` already uses for
`prompt_cache_enabled` / `max_call_input_tokens` — module-owned keys that are
not in `DEFAULT_SETTINGS` and not surfaced in the settings UI.

## Surfaces

* `GET /api/tasks/<id>/resumable` — can it be picked up, from which step, and
  the honest caveat. Separate from `/rerun` on purpose: rerun starts over from
  the prompt and repeats every side effect; this says whether the work itself
  survived. The UI needs both answers before it can offer the right button.
* `POST /api/tasks/<id>/resume` — continue it, in a worker. `409` with the
  reason when the checkpoint says no, including when a non-replay-safe tool was
  in flight and `confirm_pending` was not passed.
* Boot: `_restore_tasks_from_journal` now also returns `summary['resumable']`
  and announces it.

## What is NOT addressed here

This makes a crash survivable. It does not make crashes rarer.

The recurring **silent hang** — process alive, log quiet — is still
undiagnosed, and a hung process never reaches the exception path, so nothing
marks its task interrupted until the next boot. The hang watchdog and
single-instance guard commissioned for that are the other half of this, and
they are not this change.

## Tests

`tests/api/test_task_resume.py` — 16 cases. The crash is simulated by raising
out of the fake Anthropic client and then starting a **second** loop with a
**fresh** client and a fresh call counter, so "the tool did not run again" is
measured rather than asserted about a mock that was never reset.

Falsifiability: with `task_resume.enabled()` forced to `False`, 11 of the 16
fail. The five that survive are the guards (cap, off-switch, default) that are
about *not* writing.
