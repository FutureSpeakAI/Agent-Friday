# Did Friday really send 4,090,829 input tokens?

2026-09-22. Measured against `~/.friday/costs.db` and `~/.friday/friday.log`,
not reasoned about.

## The question

A Sonnet 5 chat turn came back as:

> `[Friday offline] '6 am and 4 pm, but only ' has sent 4,090,829 input tokens
> to cloud providers, past its ceiling of 4,000,000. The task is stopped rather
> than billed further.`

Two things to answer: did four million tokens actually go out, and why did a
chat turn get killed over it.

## Yes, the tokens went out. No, they did not cost what that number implies.

There were **two** kills that morning, not one:

| time | log line | label | counted |
| --- | --- | --- | --- |
| 06:29 | `friday.log:53032` | `'Yeah, and you're approve'` | 4,050,351 |
| 08:33 | `friday.log:56076` | `'6 am and 4 pm, but only '` | 4,090,829 |

Walking `costs.db` backwards from the 08:33 kill until the presented-token sum
matches the counter:

```
calls            25            (08:28:20 → 08:33:24, five minutes)
presented        4,090,886     vs the counter's 4,090,829 — a 57-token gap
  fresh input            50
  cache WRITE       145,643
  cache READ      3,945,193    ← 96.4%
output              20,166
REAL COST           $3.14
if it had all been fresh, at $3/Mtok   $12.27
```

The same walk from the 06:29 kill: 29 calls, 4,106,863 presented, **99.3% cache
reads, $1.61**.

So the counter was not double-counting, not summing across tasks, and not
counting a router path and a provider path twice. It was accurate to 0.0014%.
It was measuring the wrong thing.

### The accounting bug

`prompt_cache.estimate_payload_tokens` counts every byte of
`system + messages + tools` on every call. In an agent loop the transcript is
re-sent on every iteration, so the tally grows by the whole conversation each
step. That is a real count of tokens presented to Anthropic — but **prompt
caching bills a re-sent prefix at 0.1x**, and the counter has no idea.

At the observed ~164,000 tokens per iteration, the 4,000,000 default is reached
on **iteration 25** of a loop whose `max_iters` is **999**. A normal long turn
with two dozen tool calls hits it. It was going to keep happening.

### A trap in costs.db worth writing down

On Anthropic rows, `input_tokens` is ~2 per call — because Anthropic reports
*fresh* input there and puts the rest in `cache_read_input_tokens` /
`cache_creation_input_tokens`. Summing `input_tokens` alone says Friday sent
1,176 tokens to Anthropic today. It sent 75.3 million and spent $81.29. Always
read `cache_read_tokens` and `cache_write_tokens` alongside it.

Today, for context: 588 Anthropic calls, 71.4M cache reads, 4.0M cache writes,
$81.29. Caching is working — 94.7% of the traffic is hitting it.

## The label was not a second bug

`'6 am and 4 pm, but only '` is `routes/chat.py:784`:

```python
_orb_label = ((message or '').strip().splitlines() or ['Chat'])[0][:24] or 'Chat'
```

A 24-character slice of the first line of the message, used as the **display
label on the process orb**. It is never an identity key — task ids are uuids
from `_spawn_task`, and nothing is keyed off message text. The error message
simply quoted a UI label back at the user and called a chat turn a "task".

## What changed

`max_task_input_tokens` is advisory. It warns once, surfaces a notification at
the costs tab, exposes `status()` for the UI, and **does not stop the task**.
The warning now carries the real billed dollars from `cost_meter` alongside the
token count, so the number cannot look like a catastrophe when it is $3.14.

`services/spend_guard` — dollar-denominated, off by default, chosen by the user,
halts at the *next* call rather than mid-turn — remains the stop that stops.

## Still open

**The per-CALL ceiling still raises.** `max_call_input_tokens` is 180,000 and
live turns are running ~164,000 per call. That is a 10% margin before the next
kind of cutoff appears, and it is the same shape of mistake: a fixed number
where a model-derived one belongs (the ceiling should come from the model's
real context window, not a constant). Not changed here, because the ask was
specifically the cumulative budget, but it should be next.
