# Task observation — how an orchestrator watches Friday's work

> **Status:** implemented
> **Last verified:** 2026-09-06
> **Design:** [Task visibility](../design/active/task-visibility.md) (rules TV1–TV14)
> **Code:** `services/task_journal.py`, `services/observer_access.py`, `routes/tasks.py`, `core/__init__.py` (`check_auth`)

Every background task Friday runs writes a **journal**: an append-only,
sequence-numbered record of what it did, decided, called, spent and (by
default) reasoned, on disk under `~/.friday/tasks/<task_id>/` and encrypted
under the vault key when a passphrase is set. The task tray in the UI and
the routes below read the **same record**. An orchestrator such as Fable or
Astra does not get a different, thinner view of a task than the user; it
gets the same journal with two restrictions, both enforced in code rather
than by convention:

1. **Read-only.** The orchestrator's credential can list, read, digest and
   tail. It cannot steer, stop, cancel, delete, re-run, change retention or
   mint another credential. Those stay with the user.
2. **Sealed.** Every free-text field served to a non-user principal passes
   through the egress gate first, the way a tool result would. Withheld
   material is marked `[withheld by the privacy gate]`, a read that redacted
   or withheld anything is journaled as a `gate` decision on the task, and
   every read is a row in the activity ledger.

## The credential

The user mints it once, in **System → Task records → Mint read-only token**,
or:

```
POST /api/tasks/observer-token          → {"token": "fobs_…", "header": "X-Friday-Observer", …}
DELETE /api/tasks/observer-token        → revokes it
```

Only the SHA-256 of the token is stored (`~/.friday/security/observer_token.sha256`).
The plaintext is shown once. Minting again replaces the earlier token; there
is exactly one at a time.

Present it on every request:

```
GET /api/tasks HTTP/1.1
X-Friday-Observer: fobs_…
```

Presenting the header makes the request an **observer for the whole
request, before loopback trust is considered**. It can only demote, never
elevate: a process on the same machine that would have been trusted as the
user becomes read-only the moment it presents the header. An unrecognised
token is `401`; a recognised token on any route outside the allowlist, or on
any method other than `GET`, is `403` with `"principal": "observer"` in the
body.

Routes an observer may `GET`: anything under `/api/tasks` (every free-text
field sealed) and `/api/activity` (whitelisted metadata only). Process orbs
and orchestrator worker outputs are not on the allowlist: they do not seal
their text yet, so they are refused rather than served raw.

## The three reads

All three come from the journal, so all three survive a restart of Friday.

### 1. What is running — `GET /api/tasks`

One row per task the process knows about, plus the live process orbs
(flagged `process: true`; skip those). For rows whose status is `running`,
`queued` or `interrupted` the row also carries liveness from the record:

| Field | Meaning |
|---|---|
| `status` | `queued`, `running`, `complete`, `completed_unverified`, `failed`, `cancelled`, `interrupted` |
| `now` | the latest checkpoint summary, e.g. `Reasoning (step 4) on gemma4:e4b` |
| `last_seen` | epoch seconds of the last heartbeat (every 10 s while the loop is alive) |
| `stalled` | `true` when `running` and the heartbeat is older than 30 s: the process may be wedged or gone |
| `cost_usd` | metered spend so far |
| `stop_requested` | the user has asked it to stop after the current step |

`interrupted` means Friday restarted while the task was running. The record
ends with a `halt` naming the last checkpoint; nothing resumes on its own.
Only the user can re-run it (`POST /api/tasks/<id>/rerun`, refused to the
observer).

`GET /api/tasks/<id>` returns the same row for one task.

### 2. What happened, sized for a prompt — `GET /api/tasks/<id>/digest`

About 2 KB by default: status, cost, model and seat, the last `n` (default
20, max 200) checkpoints and decisions, and any halts. **It does not carry
the model's reasoning.** Reasoning prose is served only on an explicit
`?reasoning=1`, and for an observer that request passes through the gate and
lands in the activity ledger as a `journal_read` row with `reasoning: true`.
The default digest is what an orchestrator should pass to its own model.

Decision points that appear in a digest, each with the reason the code
computed: `seat_select`, `ladder_fallback`, `gate`, `approval`, `spend_cap`,
`retry`, `chain_advance`, `evaluate`, `rerun` / `rerun_of`.

### 3. The tail — `GET /api/tasks/<id>/events?since=<seq>`

Everything after sequence number `seq`, as JSON, or as Server-Sent Events
with `?stream=1`. Each SSE frame carries `id: <seq>`, so a reconnecting
client sends `Last-Event-ID` and resumes. `max_wait=<seconds>` bounds a
stream; an `event: end` frame closes it once the task is terminal.

**Gaps are reported, never hidden.** If the sequence between your cursor
and the record does not join up, the JSON response lists them under `gaps`
(`missing_from` / `missing_to`) and the stream emits an `event: gap` frame
before any data. `cursor_ahead: true` means the cursor is beyond the record:
the journal was replaced or deleted. A tail that looked complete and was not
would defeat the reason the journal exists, so treat a gap as a fact about
the record, not a transport hiccup.

`GET /api/tasks/<id>/journal?since=<seq>` is the same cursor read without
the streaming option; the UI drawer uses it.

## Event kinds

| kind | fields | written by |
|---|---|---|
| `created`, `started`, `ended` | `name`, `prompt`, `status`, `model` | the task worker |
| `checkpoint` | `iteration`, `phase`, `summary` | both agentic loops before every model call; task-log lines |
| `model_call` | `model`, `provider`, `seat`, `tokens_in`, `tokens_out`, `cost_usd`, `duration_ms` | both loops after every model call |
| `reasoning` | `text`, `thinking` | both loops, when `task_journal.capture_reasoning` is on (default) |
| `tool_call` | `name`, `ok`, `duration_ms`, `args`, `result` | the tool chokepoint |
| `decision` | `point`, `chosen`, `reason`, `alternatives` | the nine decision points |
| `steer` | `message`, `source` | `/api/agent/steer`, stop-after-step |
| `halt` | `cause`, `detail`, `resume_hint` | spend cap, stop-after-step, boot reconciliation (`interrupted`) |
| `heartbeat` | — | once a minute while alive (state file every 10 s) |

## Steering is the user's

`POST /api/agent/steer` `{task_id, message, source?}` queues a message into
the running loop and journals it as a `steer` event. The observer credential
is refused on this route. An orchestrator that steers does so **through the
user's own session** (loopback, no observer header), and should name itself
in `source` so the record is honest about which hand the user used: the
value is stored as `agent:<name>` (lower-case, `[a-z0-9_-]`, 32 chars) and
defaults to `user`. The same applies to `POST /api/tasks/<id>/stop-after-step`
(finish the current step, then stop, with a `halt` naming the step),
`DELETE /api/tasks/<id>` (cancel and keep the record while running; delete
the record when finished), and `POST /api/tasks/<id>/rerun`.

## Settings

Under `task_journal` in settings, all reversible and all visible in
**System → Task records**:

| key | default | effect |
|---|---|---|
| `retention_days` | `0` (keep forever) | journals of tasks that finished more than N days ago are deleted, and only those |
| `capture_reasoning` | `true` | when off, no reasoning prose is written anywhere in the journal |
| `encrypt_at_rest` | `true` | journal lines are protected under the vault key (falls back to DPAPI, then plaintext, in that order, like every other secret store) |

`GET /api/tasks/retention` returns all three; `POST` with `{"retention_days": N}`
sets the first and applies it immediately.

## A minimal observer loop

```python
import requests, time

H = {"X-Friday-Observer": TOKEN}
BASE = "http://127.0.0.1:3000"

running = [t for t in requests.get(f"{BASE}/api/tasks", headers=H).json()
           if not t.get("process") and t["status"] in ("running", "interrupted")]
for t in running:
    d = requests.get(f"{BASE}/api/tasks/{t['task_id']}/digest", headers=H).json()
    print(t["task_id"], t["status"], t.get("now"), d.get("cost_usd"),
          "STALLED" if t.get("stalled") else "")

# follow one
cursor = 0
while True:
    r = requests.get(f"{BASE}/api/tasks/{tid}/events", params={"since": cursor}, headers=H).json()
    if r["gaps"] or r["cursor_ahead"]:
        print("record gap:", r["gaps"], r["cursor_ahead"])   # do not paper over this
    for ev in r["events"]:
        print(ev["seq"], ev["kind"], ev.get("summary") or ev.get("point") or ev.get("name") or "")
    cursor = r["last_seq"] or cursor
    if r["complete"]:
        break
    time.sleep(2)
```

A steer or a stop from that loop would be `403`. If the orchestrator needs
one, it asks the user, or acts from the user's session with `source` set.
