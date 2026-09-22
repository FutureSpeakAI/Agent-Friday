# Restart runbook — the 2026-09-22 three-workstream reboot

**DO NOT EXECUTE WITHOUT THE GO SIGNAL.** Three workstreams land in one
restart: this branch (budget / resume / seats / tray), Laya's send-gate second
opinion plus approval-loop fix, and the Bonsai2 max-iterations diagnosis.

Written for the branch `fix/advisory-token-budget-and-resume`, 13 commits,
unpushed. Every fact below was measured on this machine on 2026-09-22, not
recalled.

---

## 0. What the machine looks like right now

```
27268 pythonw  friday_tray            (parent 24696)
 └ 15248 pythonw  friday_tray
    └ 35604 python  server.py         started 08:49:42
       └ 11616 python  server.py
          └ 19260 llama-server.exe    bonsai2:27b, port 8090, 11.6 GB
```

RTX 4070, 12,282 MiB total, **11,600 used, 413 free**. Ollama is not running.

**The brain is a grandchild of the web server.** A restart takes it down with
the server — but `residency_arbiter.adopt_or_reap` looks before it loads, and
an orphan serving a model the plan wants is **adopted rather than reloaded**.
`model_routing.local_model` and `capability_routing.reasoning` both name
`bonsai2:27b`, so adoption should apply and there should be **no 11.6 GB
reload**. If adoption fails, expect a cold load on a card with 413 MiB free,
which is the slow path.

The Arbiter's own comment records why this exists: eight orphaned
llama-servers accumulated in one day, one per restart, holding ~4.1 GB
between them.

## 1. There is no auto-restart, by design

`friday_tray._watchdog` polls every 5s, notifies on a crash, and deliberately
does **not** resurrect: "resurrecting a crashed process on a loop can mask a
repeating fault, and whether Friday restarts herself is the user's call."

So killing the server from a shell leaves Friday **down** until someone uses
the tray menu. The supported path is the tray's **Restart** item
(`stop_server` → 0.5s → `start_server`).

## 2. Sequence

| # | Step | Who |
| --- | --- | --- |
| 1 | Confirm all three workstreams are committed and the tree is clean | **Stephen decides** it is time; Claude verifies the tree |
| 2 | Pre-flight snapshot: process tree, GPU, tray/task counts, `git log`, `git status` | Claude |
| 3 | Full test sweep on the merged tree | Claude |
| 4 | Verify `index.html` + `app.html` still parse (`tests/unit/test_ui_parses.py`) | Claude |
| 5 | **Click Restart in the tray** | Claude via computer-use *(needs an access grant)*, else Stephen |
| 6 | Watch boot: confirm the Arbiter **adopted** rather than reloaded; confirm the seat answers | Claude |
| 7 | **Hard-reload the Friday tab** (`Ctrl+Shift+R` on `http://127.0.0.1:3000`) | Stephen, or Claude via computer-use |
| 8 | Post-flight: confirm the tray, a live task's journal, and the step counter | Claude |

**Step 7 is not optional.** The API token rotates on every restart, so a tab
left open from before the restart holds a dead token and presents as "the UI
will not load". This has been misdiagnosed before.

**Step 5 ordering note.** Nothing here needs the brain stopped first, and
nothing needs a gate toggled. Do **not** stop `llama-server` by hand — leaving
it running is what lets the Arbiter adopt it and skip the reload.

## 3. What to expect immediately after

* **Tray: 82 cards → 22.** Measured against the live list: 35 `complete`,
  22 `completed_unverified` and 3 old `interrupted` fall outside their
  windows. Kept: 12 interrupted, 4 complete, 4 timeout, plus live rows.
  Nothing is deleted — only the cards are hidden.
* **No resume offers.** All 15 interrupted tasks report
  `"no checkpoint was written"`; they predate checkpointing. The Resume
  button appears only for tasks interrupted *after* this restart.
* **No re-queued work.** Nothing on disk is `queued-for-seat`, so
  `readmit_queued()` is a no-op this time.
* **One `tasks_interrupted` notification**, as on every boot.
* **A long chat turn now holds its seat.** The fifteen-minute release is gone;
  the composer stays yours and shows "Still working (7m) — search_web…".

## 4. Known gap this restart does NOT close

`reconcile_tasks()` is alive again (it had been raising ImportError on every
boot since it was written) — but **boot ordering means it still reports
nothing**.

`server.py` starts `reconcile.run_at_boot` on a daemon thread at ~line 414,
while `_restore_tasks_from_journal()` runs synchronously at ~line 445. By the
time TASKS is populated, `task_journal.reconcile_on_boot` has already flipped
every `running` row to `interrupted` — and `reconcile_tasks` only looks for
`running`. Either it runs early against an empty dict, or late against rows
that are already terminal. Both are no-ops.

Consequence: the **conversation-level** interruption report ("X was
interrupted when Friday restarted…", written into the chat that asked for the
work) still does not fire. The notification does; the chat message does not.
That is the exact failure recorded in `_spawn_task`'s docstring, where a
workflow step died twice, the reason was written down both times, and the
assistant in the other chat correctly said it had no idea why.

Fixing it is not a one-line reorder — after the restore there are no `running`
rows left to find, so `reconcile_tasks` has to consume
`_restore_tasks_from_journal`'s summary instead of scanning TASKS. Small, but
a design change, and deliberately not made under a restart freeze.

## 5. Rollback

Reversible at every point. The branch is unpushed and unmerged; `main` is
untouched. If the restart misbehaves:

1. Tray → **Quit**, then relaunch the tray from its shortcut.
2. If the new code is implicated: `git checkout main` in the working tree and
   restart again. The server runs from the tree, so the checkout *is* the
   rollback.
3. Nothing in this branch writes a migration or changes an on-disk format.
   `resume.json` is additive, per task, inside the existing journal directory,
   and `main` ignores it.
