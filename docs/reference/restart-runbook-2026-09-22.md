# Restart runbook — the 2026-09-22 three-workstream reboot

**STATUS: BLOCKED on one dependency (§1). Everything else is ready.**

Three workstreams are merged and on `main`: the advisory-budget / crash-resume
/ seat / tray work, Laya's union send-gate, and Bonsai2's front-page
max-iters fix. This document is the sequence for bringing them up. It is
written to survive the session that produced it — every fact was measured on
this machine on 2026-09-22, not recalled.

---

## 1. THE BLOCKER — read this first

**The restart cannot happen until the uncommitted `news_engine.py` work lands.**

The shared working tree (the repo checkout the tray launches from) carries
live, wanted, uncommitted work added 2026-09-22 at the maintainer's request:

| file | real change | what it is |
| --- | --- | --- |
| `services/news_engine.py` | **+160 lines** | Austin-local RSS feeds (KUT, KXAN, KVUE, Texas Tribune); Local beat bumped 3 → 4 |
| `services/scheduler.py` | **+16 lines** | `brutalist_morning` / `brutalist_evening` builtin tasks at 06:45 / 16:45 |

`git diff` reads **2887 insertions / 2736 deletions** on `news_engine.py`
because the whole file was rewritten LF → CRLF. That is churn, not content.
Use `git diff -w --ignore-cr-at-eol` to see the real 160 lines.

**Why it blocks.** Bonsai2's merge changes `news_engine.py` too. `git checkout
main` in the live tree would refuse, or clobber 176 lines of live work. The
tree cannot be brought onto the new code until this lands.

**Scrub before committing.** A comment in `news_engine.py` justifies the
feed choice by naming the maintainer's home town — the small town itself,
not just the metro area. The town name is the problem; "Austin, TX" is not.

(The line is deliberately not reproduced here. This runbook is itself
committed to the public repo, so quoting it would publish the very thing it
tells you to remove. Search `news_engine.py` for the word `location`.)

**The repo is public** —
verified, not assumed: unauthenticated `api.github.com/repos/FutureSpeakAI/
Agent-Friday` returns `private: false, visibility: public`. Checked
2026-09-22: the string is in the working tree **only** — not in `main`, not on
`origin/main`, not in any push range. Nothing is exposed yet. The comment's
useful content survives as "wired to Austin, TX at the maintainer's request".

---

## 2. What the machine looks like

```
27268 pythonw  friday_tray
 └ 15248 pythonw  friday_tray
    └ 35604 python  server.py
       └ 11616 python  server.py
          └ 19260 llama-server.exe   bonsai2:27b, port 8090, ~11.6 GB
```

RTX 4070, 12,282 MiB. With bonsai2 resident, `display_at_risk()` reports
~2,276 MiB free against a 2,560 MiB desktop reserve. Ollama is **not** running.

### Brain and seat ordering — the one thing that must not be improvised

**Do NOT stop `llama-server` by hand.** It is a grandchild of the web server,
so a restart takes it down — but `residency_arbiter.adopt_or_reap` looks
*before* it loads and **adopts** an orphan already serving a model the plan
wants. `model_routing.local_model` and `capability_routing.reasoning` both
name `bonsai2:27b`, so adoption applies and there should be **no 11.6 GB
reload**. Killing it by hand forfeits that and forces a cold load onto a card
with a few hundred MiB free.

The Arbiter's own comment records the failure this prevents: eight orphaned
llama-servers accumulated in one day, one per restart, ~4.1 GB between them.

### There is no auto-restart, by design

`friday_tray._watchdog` polls every 5s, notifies on a crash, and deliberately
does not resurrect — "whether Friday restarts herself is the user's call".
So **killing the server from a shell leaves Friday down.** The supported path
is the tray menu's **Restart** (`stop_server` → 0.5s → `start_server`).

`start_server` does handle an externally-launched server
(`if _port_in_use(PORT): treat as healthy`), and `server.py` holds its own
single-instance lock, so a manual start cannot produce a duplicate — but it
leaves the tray not owning the process, and its Restart item then no-ops until
the tray is quit and relaunched. Prefer the menu.

---

## 3. Sequence

| # | Step | Who |
| --- | --- | --- |
| 0 | **Land the `news_engine.py` / `scheduler.py` WIP, town name scrubbed** | **Maintainer decides**; whoever owns that work commits it |
| 1 | Bring the live tree onto `main` (`git checkout main` or fast-forward the branch) | Claude, once step 0 clears |
| 2 | Pre-flight: process tree, GPU free, `git log`, `git status` | Claude |
| 3 | Full suite, `-n 2`, from a clean worktree | Claude |
| 4 | `tests/unit/test_ui_parses.py` — both HTML files still parse | Claude |
| 5 | **Tray → Restart** | **Maintainer** (computer-use cannot be granted in a scheduled run; one click) |
| 6 | Watch boot: Arbiter **adopted** rather than reloaded; seat answers | Claude |
| 7 | **Hard-reload the Friday tab** — `Ctrl+Shift+R` on `http://127.0.0.1:3000` | Maintainer |
| 8 | Post-flight checks (§4) | Claude |

**Step 7 is not optional.** The API token rotates on every restart, so a tab
left open from before presents as "the UI will not load". That has been
misdiagnosed before.

---

## 4. What to look at first when it comes up

In this order, because each one is a thing that was broken this morning:

1. **A long chat turn keeps its seat.** Ask for something that runs minutes.
   The composer stays yours and the indicator reads
   `Still working (7m) — search_web…`. It must **never** say "I have released
   this chat". The fifteen-minute release is gone.
2. **The tray is short.** 82 cards → **22** (measured). Nothing was deleted;
   finished cards aged out. Every finished card now has a **✕** that hides it
   and keeps the record, plus **CLEAR FINISHED** in the TASKS header.
3. **A task's drawer shows its journal.** Expand any task card — real events
   (checkpoints, tool calls, model calls with cost), not "— no journal yet —".
4. **Progress reads `step 7`, not `0%`.** Especially on a **local** task; the
   local loop never reported progress at all before.
5. **Interrupted cards offer Resume** — but only for tasks interrupted *after*
   this restart. All 15 existing ones correctly say "no checkpoint was
   written"; they predate checkpointing. This is expected, not a fault.
6. **No task is killed for token count.** `max_task_input_tokens` is advisory:
   it notifies and keeps running.

---

## 5. What "working" looks like for the Laya-vs-Bonsai2 hand test

The point of this reboot is to let the maintainer judge two things by hand and
then decide on an installer. "Server started" is not the finish line.

### Laya — the union send-gate

It ships **on**. The property to check is the union: Laya can only ever **add**
an approval card, never remove one. So:

* A send that the rules gate must still be gated. Laya cannot wave it through.
* A send that the rules would pass but Laya flags must now produce a card.
* Every gate decision is written down — check the decisions log, not just the
  UI.

Working = you are asked **more** often than before, never less, and each ask
names why. If you are ever asked *less*, that is the failure, and it is the
one worth stopping for.

### Bonsai2 — the front page

Its finding was that "Max iters" was never max_iters: `_generate_text` passed
`tools=None`, so the loop ran exactly **once**, and the seat is a reasoning
model whose `reasoning_content` deltas were discarded at the transport —
burning `max_tokens` on a channel nothing read. Nine consecutive editions had
silently been the fallback.

Working = a **curated** edition, not the fallback. Its own verification was a
170s run producing 14,914 characters of reasoning against what had been an
1,800-token ceiling. So: generate a front page, confirm it is curated, and
confirm the run takes minutes rather than returning instantly. An instant
edition is the fallback wearing the same clothes.

---

## 6. Rollback

Reversible throughout. `main` is pushed, but the live tree runs from a
checkout:

1. Tray → **Quit**, relaunch the tray from its shortcut.
2. If new code is implicated: `git checkout 0647aaa` in the working tree and
   restart. The server runs from the tree, so the checkout *is* the rollback.
3. Nothing in this range writes a migration or changes an on-disk format.
   `resume.json` is additive, per task, inside the existing journal directory,
   and older code ignores it.

---

## 7. Known-red tests, so nobody re-diagnoses them

Five, all pre-existing, none blocking, none from this range:

* `test_task_journal::test_a_full_run_leaves_a_complete_journal_and_state` —
  event-ordering assertion. Journals still write 27–234 events per task and
  the endpoint serves them (verified live).
* `test_models_refresh_route` ×2 — wants the undated `claude-haiku-4-5` alias
  surfaced. Both the undated and dated ids are correctly priced in
  `cost_meter` and correctly sized in `prompt_cache`, so this is cosmetic.
* `test_creative_pipeline_routes` ×2 — one stale expectation (the route
  returns `201 CREATED`, the test wants `200`; the route is right) and one
  genuine minor gap: creating a project with no name succeeds where it should
  error.

Plus ~19 environmental (VRAM, credential store resolving `keystore`) and a
handful order-dependent under xdist.

**Do not** re-file the Ollama `/api/generate` vs `/api/chat` failures as a
dispatch regression. They were fixed 2026-09-22: production posts `/api/chat`
with `options.num_ctx`; the `/api/generate` the tests caught is a deliberate
seat-release (`keep_alive: 0`) that fires only when the card is under the
desktop reserve, and the tests were asserting on the last POST instead of the
chat one. See `gotcha_ollama_tests_fail_when_gpu_is_full`.
