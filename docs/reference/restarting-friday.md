# Restarting Friday

How the desktop server is restarted, what must not be improvised while doing
it, and what to check once it is back.

## The process tree

```
pythonw  friday_tray
 └ pythonw  friday_tray
    └ python  server.py
       └ python  server.py
          └ llama-server.exe        the pinned local seat
```

The llama-server holding the local brain is a **grandchild** of the web
server, so restarting the server takes it down with it.

## Do not stop llama-server by hand

`residency_arbiter.adopt_or_reap` runs before anything is loaded. An orphaned
llama-server already serving a model the plan wants is **adopted**, not
reloaded — which on a 12 GB card is the difference between a few seconds and a
cold multi-gigabyte load onto a card that may have only hundreds of MiB free.
Killing it manually forfeits that.

The same function reaps orphans the plan does *not* want, which is what stops
one leaked seat accumulating per restart.

## There is no auto-restart

`friday_tray._watchdog` polls every five seconds and notifies when the server
dies, but deliberately does not resurrect it: whether Friday restarts herself
is the user's decision, not the watchdog's. **Killing the server from a shell
leaves Friday down.** Use the tray menu's **Restart** item.

`start_server` tolerates a server started outside the tray — `_port_in_use`
makes it treat the port as healthy — and `server.py` holds a single-instance
lock, so a manual start cannot produce a duplicate. But the tray then does not
own the process, and its Restart item no-ops until the tray is quit and
relaunched. Prefer the menu.

## Reload the browser tab

The API token rotates on every restart. A tab left open from before the
restart holds a dead token and presents as "the UI will not load".
`Ctrl+Shift+R` on the Friday tab after every restart.

## What to check once it is up

* A long chat turn keeps its seat. The composer stays with the user and the
  indicator names the current step and elapsed time; a turn is released only
  when its worker is gone or the hang watchdog reports a stall, never on
  elapsed time alone.
* Task cards show their journal — checkpoints, tool calls, model calls with
  cost. A row backed by a live process rather than a journalled task shows
  that process's own activity instead.
* Progress reads as a step number. An agent loop has no denominator, so it
  reports `step N` rather than a percentage; a percentage appears only where
  something can compute a real fraction.
* Finished task cards age out of the tray and carry a dismiss control.
  Interrupted tasks holding a resume checkpoint never expire on a clock and
  offer to resume from their last step.
* A task is never terminated for its cumulative token count. That budget is
  advisory: it notifies and the work continues.

## Verifying the local seat and the send gate by hand

**The send gate** is a union: a second opinion may only ever *add* an approval
card, never remove one. So a send the rules gate stays gated, a send the rules
would pass but the second opinion flags now produces a card, and every
decision is written to the decisions log. Being asked *less* often than before
is the failure mode worth stopping for.

**The front page** should be a curated edition, not the fallback. A curated
run takes minutes, because the reasoning channel carries the work; an edition
that returns instantly is the fallback. `max_tokens` covers the reasoning
channel as well as the reply, and a seat whose reasoning deltas are dropped at
the transport spends its budget on output nothing reads.

## Rollback

The server runs from the working tree, so checking out an earlier commit and
restarting *is* the rollback. Nothing in the task-journal or resume path
writes a migration or changes an on-disk format: `resume.json` is additive,
per task, inside the existing journal directory, and older code ignores it.
