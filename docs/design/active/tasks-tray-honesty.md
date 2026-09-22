# The tasks tray: saying only what is known, and letting work be picked back up

Status: BUILT (2026-09-22). Four complaints, one theme for the first two.

> "Bonsai2 is doing a kickass job at building my front page in the news
> workspace, but the task in the notifications tray shows '- no journal yet -'
> as the reasoning output, so I cannot actually see what it is doing (I want
> to). Similarly, the process says 0% complete even after letting it run a
> long time... there are tons of leftover notifications for interrupted and
> completed processes we ran previously; they never went away... This also
> seems like a good place to put a resume button for interrupted processes."

---

## 1. "— no journal yet —"

**Not a missing journal.** The journals were being written, richly: sampled
across the eight most recent tasks on this machine, 27 to 234 events each
(checkpoints, tool calls, model calls with cost, decisions, reasoning). The
endpoint served them correctly for every id tried.

**The panel was asking the wrong id.** `routes/tasks.list_tasks` merges two
sources: real tasks, and live PROCESSES — the orbs that briefings, vault
access, compression and every agent loop register. A process row's `task_id`
is the *orb pid*. `TaskCard` handed that straight to
`TaskTimeline → /api/tasks/<id>/journal`, which 404s, which the poller
swallows, which leaves `events.length === 0`, which renders "— no journal
yet —". The word "yet" was the tell: a live process is not a journalled task
and never will have a journal, so it was a promise that could not be kept.

This is the same defect family as the "— waiting for activity —" bug already
documented at length in `routes/tasks.py`, which was raised five times and
diagnosed wrong twice before anyone noticed the row being clicked was the orb
and not the task. The row already carried everything needed —
`linked_task_id`, the orb's own `log`, its `steps` thread — and the renderer
read none of it.

**Fixed** by reading `task.linked_task_id || task.task_id`, falling back to
the orb's own activity when there is no journal, and replacing the empty
state with one that is true: "— live process · its own activity, not a task
journal —", or "— nothing was recorded for this one —" when there is
genuinely nothing.

## 2. 0% forever

`progress` is a fraction, and a fraction needs a denominator. An agent loop
does not have one: it runs until the model stops asking for tools, and
`max_iters` is 999 — a ceiling, not an expectation.

* The Anthropic loop reported `0.05 + 0.1 * (iteration - 1)`: a straight line
  to 90% that quietly asserts "about ten steps", then parks.
* The **local** loop — the one bonsai2 runs on — reported nothing at all, at
  any point, so a local task sat at 0 for its entire life however well it was
  going. This is the one the complaint was about.
* `list_tasks` then did `p.get('progress', 0)`, turning "unknown" into "none
  done" on the way out.
* The orb's ring drew `floor(ringSegs * progress)` — empty, all run.

**Fixed** by reporting what is actually known instead of inventing what is
not:

* `step_n` on every process record, set by **both** loops, every round.
* `step_total` is `None` when unknown; `progress` stays `None` unless
  something can compute a real fraction (image generation can — honesty is
  not a ban on percentages, only on inventing one).
* The card renders "step 7" (and "of N" when a total exists) next to elapsed.
* The orb ring draws a travelling arc when the fraction is unknown and the
  process is running: it reports activity without claiming an amount. A real
  fraction still fills the ring as before.

## 3. Cards that never left

**Measured first, and the obvious target was innocent.** The notification
queue was healthy: 200 entries at its cap, 197 already dismissed, 3 unread.
The tray's TASKS section was the problem — **81 task records, every one
terminal, none of them ever leaving**. The ✕ only appeared while a task was
running, where it means *cancel*, so a finished card had no control at all.
"Delete record" existed, but only inside the drawer, and it destroys the
journal rather than tidying the view.

Two controls, two meanings, deliberately kept apart:

* **dismiss** hides the card. Record, journal and result all stay, reachable
  from the records view. This is what the ✕ should have been doing.
* **delete** destroys the record. Unchanged, still in the drawer.

`services/tray_lifecycle` gives each class the lifetime its *purpose*
deserves, rather than one `max_age_hours` for everything:

| class | tray lifetime | why |
| --- | --- | --- |
| running / queued | forever | age is not death — the fifteen-minute chat release in another costume |
| complete | 6h | a receipt; once seen there is nothing to act on |
| completed_unverified | 12h | may still want checking |
| superseded | 1h | replaced by another row that is doing the work |
| cancelled | 6h | the user already knows |
| failed / timeout / killed | 48h | someone may still want to look |
| **interrupted WITH a checkpoint** | **never** | **a handle on unfinished work** |

That last row is the whole reason this is a module and not a setting. Fifteen
of the 81 were interrupted, and some hold resume checkpoints. A uniform sweep
would have quietly deleted exactly what the checkpoint work exists to
preserve. "Clear finished" spares them too.

## 4. Resume

An interrupted card that still holds a checkpoint now offers **Resume from
step N**, wired to `POST /api/tasks/<id>/resume`. Every tool it already ran is
answered in the saved transcript, so nothing already done is redone and
nothing already paid for is bought twice.

The reason is shown on the card rather than summarised away. When a
side-effecting tool was in flight at the crash, nothing on disk can say
whether it landed — so the button confirms first and sends `confirm_pending`,
because that is the user's call, not a detail to bury. An interrupted task
that *cannot* be resumed says why instead of silently offering nothing.

## Sequencing

His note: "1 and 2 make the panel honest, which is what lets him trust 3 and
4. A resume button on a panel that lies about progress is worse than no
button." Built in that order, and the honesty tests are pinned structurally
in **both** HTML files the way the Approvals card is, so the surface cannot
disappear from one of them.

## What is not done

* **The notification queue's own expiry.** It is capped at 200 and manual
  dismissal works, so it is not currently the problem — but nothing ages an
  entry out by time, and `clear_dismissed` must be called by hand.
* **Reasoning density.** The journal records a `reasoning` event per round,
  but only when the model emitted prose; a round that is nothing but a tool
  call has none to record. Live tasks showed 1–2 reasoning events per ~25
  model calls. Worth a look once he can actually see the timeline.
* The tray still shows only the 8 newest cards; with expiry in place that is
  now a window on a much shorter list, but it is still a fixed slice.
