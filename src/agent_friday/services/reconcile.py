"""What survives a restart, and what has to be admitted.

docs/design/implemented/conversations-and-concurrency.md §2.5/§3.4 and build steps 7-8.

"Do background tasks survive a restart?" Without this module the honest
answer is: at STORAGE yes, at EXECUTION no. Commissions are written to disk
faithfully and nothing picks them up again, so one can sit frozen at the
`grinding` stage while the app that started it has long since restarted —
neither finished nor failed nor running, simply stopped, silently, which is
the exact shape of defect this codebase works to remove.

Two rules from the spec, and the distinction between them is the whole design:

  * **Resumable work** is structured and stage-checkpointed — a research
    commission knows which stage it reached. Resume it.
  * **Non-resumable work** is a free-form agentic turn that was mid-flight. Its
    state lives in a process that no longer exists. It cannot be resumed, and
    pretending otherwise would be worse than saying so: report it interrupted,
    into the conversation that asked for it, with an offer to run it again.

Never silence. A job that stopped must say it stopped.
"""
from __future__ import annotations

import threading
import time


# Stages that mean "this was running when the process died".
RUNNING_STAGES = {"scoping", "grinding", "synthesizing", "verifying", "writing",
                  "queued", "running"}


def _report(conversation_id: str, text: str, meta: dict | None = None) -> None:
    """Put a line in a conversation's transcript, server-side.

    Server-side is the point (§3.5): the old delivery path needed a live
    browser to witness a transition, so a task that completed while nothing was
    watching reported to nobody. Writing into the store means the message is
    there whenever the user next opens that conversation — including after a restart.
    """
    try:
        from agent_friday.services import conversations as conv
        conv.append(conv.resolve(conversation_id), {
            "role": "system_report",
            "text": text,
            "meta": dict(meta or {}, kind=(meta or {}).get("kind") or "task_report"),
        })
    except Exception as e:
        print(f"  [reconcile] could not report into {conversation_id}: {e}")


def report_task_result(conversation_id: str, title: str, body: str,
                       task_id: str | None = None, kind: str = "task_report") -> None:
    """Public entry: finished background work reports into its own chat.

    Called by whatever completes the work. Deliberately not a broadcast — the
    owner is stamped on the job, so the conclusion lands where it was asked
    for rather than in "the chat panel, singular", which is the address that
    stopped existing the moment there was more than one conversation.
    """
    _report(conversation_id, f"**{title}**\n\n{body}",
            {"task_id": task_id, "kind": kind})


# ── Boot reconciliation ─────────────────────────────────────────────────────

# Commissions whose record could not be parsed at all, with the reason. These
# are still frozen work — they just cannot be resumed automatically — so they
# are reported rather than silently skipped.
UNREADABLE: dict = {}


def _commissions():
    """Every commission on disk, skipping — not dying on — unreadable ones.

    The first version wrapped the whole enumeration in one try/except, so a
    single stale record (an older schema: SubQuestion got `cloud_allowed`)
    returned an empty list and reconciliation did nothing at all. One bad
    record must cost that record, exactly as one bad line costs one message.
    """
    from pathlib import Path
    try:
        from agent_friday.services.research.objects import Commission
        from agent_friday.core import FRIDAY_DIR
    except Exception as e:
        print(f"  [reconcile] research module unavailable: {e}")
        return []
    root = Path(FRIDAY_DIR) / "research"
    if not root.exists():
        return []
    out = []
    UNREADABLE.clear()
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        try:
            c = Commission.load(d.name)
        except Exception as e:
            UNREADABLE[d.name] = f"{type(e).__name__}: {e}"
            continue
        if c is not None:
            out.append(c)
    if UNREADABLE:
        print(f"  [reconcile] {len(UNREADABLE)} commission record(s) unreadable: "
              + ", ".join(sorted(UNREADABLE)))
    return out


def reconcile_research(resume: bool = True) -> dict:
    """Adopt commissions that were running when the process died.

    Resumed rather than restarted: `run()` re-enters at the commission's own
    recorded stage, so a grind that had already read 40 pages does not read
    them again.
    """
    adopted, reported = [], []
    for c in _commissions():
        stage = str(((getattr(c, "progress", None) or {}).get("stage")
                     or getattr(c, "status", "")) or "").lower()
        if stage not in RUNNING_STAGES:
            continue
        cid = getattr(c, "id", None) or getattr(c, "commission_id", None)
        owner = getattr(c, "conversation_id", None)
        if resume:
            adopted.append(cid)
            print(f"  [reconcile] resuming research {cid} from stage {stage!r}")
            task_id = getattr(c, "task_id", None)
            if task_id and _resume_research_task(task_id, cid):
                # The task the person was watching resumes and finishes; the
                # commission's own delivery reports into its conversation.
                continue

            def _run(_cid=cid, _owner=owner):
                try:
                    from agent_friday.services import research as _r
                    _r.run(_cid)
                    _report(_owner, f"Research **{_cid}** finished after a restart. "
                                    f"It picked up where it left off.",
                            {"kind": "task_report", "task_id": _cid})
                except Exception as e:
                    _report(_owner, f"Research **{_cid}** could not be resumed after "
                                    f"the restart: {e}. Nothing was lost — ask me to "
                                    f"run it again and I will start from its last "
                                    f"recorded stage.",
                            {"kind": "interruption_notice", "task_id": _cid})

            threading.Thread(target=_run, daemon=True,
                             name=f"resume-research-{cid}").start()
        else:
            reported.append(cid)
            _report(owner, f"Research **{cid}** was interrupted at the {stage} "
                           f"stage when Friday restarted. It has not been lost — "
                           f"ask me to resume it.",
                    {"kind": "interruption_notice", "task_id": cid})
    for cid, why in UNREADABLE.items():
        stage_note = (f"Research **{cid}** is on disk but its record cannot be "
                      f"read by this build ({why}). It has not been lost; it "
                      f"also cannot be resumed automatically. Ask me and I will "
                      f"start that question fresh.")
        _report(None, stage_note, {"kind": "interruption_notice", "task_id": cid})
        reported.append(cid)
    return {"resumed": adopted, "reported": reported,
            "unreadable": sorted(UNREADABLE)}


#: How long boot reconciliation waits for the task cache to be rebuilt from
#: the journal before it re-attaches a research run to its task.
TASK_RESTORE_WAIT_S = 120.0


def _resume_research_task(task_id: str, commission_id: str) -> bool:
    """Resume a deep_research commission on the task that started it.

    Waits (in the background) for the task cache to be restored from the
    journal, because server boot starts this reconciliation before that
    restore runs, and the restore marks the task interrupted: resuming first
    would be overwritten by it. Returns False if resuming on the task is not
    possible, so the caller falls back to resuming the bare commission.
    """
    try:
        from agent_friday.services import agent as _agent
    except Exception as e:
        print(f"  [reconcile] cannot reach the task runner: {e}")
        return False

    def _go():
        _agent.TASKS_RESTORED.wait(TASK_RESTORE_WAIT_S)
        ok = False
        try:
            ok = _agent.resume_runner_task(task_id, _agent._research_runner(commission_id))
        except Exception as e:
            print(f"  [reconcile] could not resume research task {task_id}: {e}")
        if not ok:
            try:
                from agent_friday.services import research as _r
                _r.run(commission_id)
            except Exception as e:
                print(f"  [reconcile] research {commission_id} could not resume: {e}")

    threading.Thread(target=_go, daemon=True,
                     name=f"resume-research-{commission_id}").start()
    return True


def _tasks():
    """The live task ledger and its lock.

    They live in ``services/agent``, NOT in ``core``. This module imported them
    from ``core`` inside a ``try/except ImportError`` that returned an empty
    result, so ``reconcile_tasks`` has never marked a single task interrupted
    in production — it raised ImportError on every boot, swallowed it, and
    reported "0 items adopted". A function whose whole purpose is "a job that
    stopped must say it stopped" was itself stopping silently.

    Its test did not catch it because it created the attribute the production
    code was looking for:

        monkeypatch.setattr(core, "TASKS", {...}, raising=False)

    ``raising=False`` on a name that does not exist builds the world the code
    wants instead of the one it runs in, and the test then passes in that
    world. Resolved in one place now, so there is one thing to get wrong and
    the tests patch the same thing production reads.
    """
    from agent_friday.services.agent import TASKS, TASKS_LOCK
    return TASKS, TASKS_LOCK


def _resumability(task_id):
    """What services/task_resume says about this task. Never raises."""
    try:
        from agent_friday.services import task_resume as _tr
        return _tr.resumability(task_id)
    except Exception:
        return {"resumable": False, "reason": "resume support unavailable"}


def readmit_queued() -> dict:
    """Tasks that were WAITING for a local seat when the process died.

    These never ran. Nothing was spent on them and no side effect was taken,
    so re-admitting them is lossless — the honest opposite of the mid-flight
    case below, where the question is delicate.

    They also cannot simply be re-wired: ``_spawn_task`` parks the worker
    Thread OBJECT in ``_PENDING_TASK_THREADS`` while a task waits, and a thread
    object does not survive the process that made it. So re-admission means
    spawning the task again from its recorded prompt and pointing the old
    record at the new one.

    Without this a ``queued-for-seat`` task is worse off than a running one:
    ``task_journal.reconcile_on_boot`` only looks at ``running``/``queued``, so
    a queued-for-seat record is not even marked interrupted. It keeps a status
    that says it is about to start, forever, with nothing left that could start
    it. That is a silent stall, which is the one outcome this codebase refuses.
    """
    out = {"readmitted": [], "failed": []}
    try:
        TASKS, TASKS_LOCK = _tasks()
        from agent_friday.services.agent import _spawn_task
        from agent_friday.services import task_journal as _tj
    except Exception as e:
        print(f"  [reconcile] seat-queue re-admission unavailable: {e}")
        return out
    with TASKS_LOCK:
        waiting = [(tid, dict(t)) for tid, t in TASKS.items()
                   if (t or {}).get("status") == "queued-for-seat"]
    for tid, t in waiting:
        prompt = (t.get("prompt") or "").strip()
        if not prompt:
            out["failed"].append(tid)
            with TASKS_LOCK:
                rec = TASKS.get(tid)
                if rec is not None:
                    rec["status"] = "interrupted"
                    rec["status_reason"] = (
                        "Was waiting for a local seat when Friday restarted, and "
                        "no prompt was recorded, so it cannot be re-queued.")
            continue
        try:
            new_id = _spawn_task(t.get("name") or "Task", prompt,
                                 description=t.get("description") or "",
                                 chain=t.get("chain"),
                                 chain_step=int(t.get("chain_step") or 0),
                                 model=t.get("model"))
        except Exception as e:
            out["failed"].append(tid)
            print(f"  [reconcile] could not re-queue {tid}: {e}")
            continue
        with TASKS_LOCK:
            rec = TASKS.get(tid)
            if rec is not None:
                rec["status"] = "superseded"
                rec["ended"] = time.time()
                rec["status_reason"] = (
                    f"Was waiting for a local seat when Friday restarted. It had "
                    f"not started, so nothing was lost — re-queued as {new_id}.")
        try:
            _tj.append(tid, "decision", point="readmit", chosen=new_id,
                       reason="was queued for a local seat when the process died; "
                              "nothing had run, so it was re-queued")
        except Exception:
            pass
        out["readmitted"].append({"task_id": tid, "new_task_id": new_id})
    for item in out["readmitted"]:
        t = TASKS.get(item["task_id"]) or {}
        _report(t.get("conversation_id"),
                f"**{t.get('name') or item['task_id']}** was still waiting for the "
                f"local seat when Friday restarted. It had not started yet, so "
                f"nothing was lost — I have put it back in the queue.",
                {"kind": "requeue_notice", "task_id": item["task_id"],
                 "new_task_id": item["new_task_id"]})
    return out


def reconcile_tasks() -> dict:
    """Free-form agentic work that was mid-flight: resume it if we can, and
    say plainly when we cannot.

    This function used to be unconditional — "its state lived in a process
    that no longer exists... I cannot pick it up mid-way". That was true when
    it was written and is no longer true for a task that got far enough to
    leave a checkpoint: services/task_resume saves the transcript at every
    tool boundary, and a restored transcript carries every completed tool's
    result with it, so resuming re-buys nothing.

    Three outcomes, and the difference between the last two is the whole
    reason this is careful:

      * a checkpoint exists and nothing was in flight -> offer to resume
        (or resume outright, if task_resume_auto is on),
      * a checkpoint exists but a side-effecting tool was RUNNING when the
        process died -> offer, and say what re-running it would risk,
      * no checkpoint -> the original message, unchanged, because it is still
        the honest one.
    """
    touched = []
    try:
        TASKS, TASKS_LOCK = _tasks()
    except Exception as e:
        # LOUD. This used to be a bare `return` and it is why this function
        # was dead for its whole life.
        print(f"  [reconcile] cannot reach the task ledger: {e}")
        return {"interrupted": [], "resumable": [], "error": str(e)}
    now = time.time()
    with TASKS_LOCK:
        for tid, t in list(TASKS.items()):
            if (t or {}).get("status") != "running":
                continue
            t["status"] = "interrupted"
            t["ended"] = now
            _why = ("Interrupted by a restart — this was a free-form run and "
                    "its state lived in a process that no longer exists.")
            t.setdefault("log", []).append(_why)
            # ON THE RECORD ITSELF, not only in the log and a notice.
            #
            # `chain_run_status` reports a step's STATUS and nothing else, so
            # "interrupted" reached the assistant as a bare word with no cause
            # attached: a workflow step can die twice with the reason written
            # down both times, and the assistant reading the status can only
            # say it has no idea why - which is true, and is the bug.
            t["status_reason"] = _why
            touched.append(tid)
    resumable = []
    for tid in touched:
        t = TASKS.get(tid) or {}
        owner = t.get("conversation_id")
        name = t.get("name") or tid
        v = _resumability(tid)
        if not v.get("resumable"):
            # Unchanged, and still correct: with no checkpoint there is
            # genuinely nothing to pick up.
            _report(owner, f"**{name}** was interrupted when Friday restarted. "
                           f"It was a free-form run with no checkpoint, so I "
                           f"cannot pick it up mid-way — say the word and I "
                           f"will start it again.",
                    {"kind": "interruption_notice", "task_id": tid,
                     "resumable": False, "why": v.get("reason")})
            continue
        with TASKS_LOCK:
            rec = TASKS.get(tid)
            if rec is not None:
                rec["resumable"] = True
                rec["resume_reason"] = v.get("reason")
                rec["status_reason"] = (
                    f"Interrupted by a restart at step {v.get('iteration')}. "
                    f"Its work is checkpointed and can be resumed.")
        resumable.append(tid)
        if v.get("needs_confirmation"):
            _report(owner,
                    f"**{name}** was interrupted at step {v.get('iteration')} "
                    f"and its work is saved — but {v.get('reason')} "
                    f"Tell me to resume it and I will, and I will re-run that "
                    f"tool; or start it over instead.",
                    {"kind": "interruption_notice", "task_id": tid,
                     "resumable": True, "needs_confirmation": True})
            continue
        auto = False
        try:
            from agent_friday.services import task_resume as _tr
            auto = _tr.auto_enabled()
        except Exception:
            auto = False
        if auto:
            _report(owner,
                    f"**{name}** was interrupted at step {v.get('iteration')}. "
                    f"Its work is saved, so I am picking it back up rather than "
                    f"starting over — nothing already done gets redone.",
                    {"kind": "resume_notice", "task_id": tid})
            _resume_in_background(tid)
        else:
            _report(owner,
                    f"**{name}** was interrupted at step {v.get('iteration')} "
                    f"when Friday restarted — but its work is saved. "
                    f"{v.get('reason')} Say the word and I will pick it up "
                    f"where it stopped instead of starting over.",
                    {"kind": "interruption_notice", "task_id": tid,
                     "resumable": True, "needs_confirmation": False})
    return {"interrupted": touched, "resumable": resumable}


def _resume_in_background(task_id: str) -> None:
    """Auto-resume, off the boot thread.

    Off the boot thread because a resumed turn can run for many minutes and
    boot must not block on it, and because one task that fails to resume must
    not take the rest of reconciliation with it.
    """
    def _run():
        TASKS, TASKS_LOCK = _tasks()
        from agent_friday.services import task_resume as _tr
        from agent_friday.services import task_journal as _tj
        _tj.push_task(task_id)
        try:
            with TASKS_LOCK:
                rec = TASKS.get(task_id)
                if rec is not None:
                    rec["status"] = "running"
                    rec["ended"] = None
            text, _trace = _tr.resume(task_id)
            # A task resumed from its ledger ran its own worker, which has
            # already recorded how it ended; `settle` only fills in a status
            # nothing else set.
            _tr.settle(task_id, text, TASKS=TASKS, TASKS_LOCK=TASKS_LOCK)
        except Exception as e:
            with TASKS_LOCK:
                rec = TASKS.get(task_id)
                if rec is not None:
                    rec["status"] = "interrupted"
                    rec["status_reason"] = f"Resume failed: {e}"
            print(f"  [reconcile] resume of {task_id} failed: {e}")
        finally:
            try:
                _tj.pop_task()
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True,
                     name=f"resume-{task_id}").start()


def run_at_boot() -> dict:
    """Everything above, once, on startup. Never raises."""
    out = {}
    try:
        out["research"] = reconcile_research(resume=True)
    except Exception as e:
        print(f"  [reconcile] research reconciliation failed: {e}")
        out["research"] = {"error": str(e)}
    try:
        out["tasks"] = reconcile_tasks()
    except Exception as e:
        print(f"  [reconcile] task reconciliation failed: {e}")
        out["tasks"] = {"error": str(e)}
    # AFTER the mid-flight pass, not before: re-queuing spawns new tasks, and
    # a task spawned by this boot must not then be swept up as a casualty of
    # the previous one.
    try:
        out["queued"] = readmit_queued()
    except Exception as e:
        print(f"  [reconcile] seat-queue re-admission failed: {e}")
        out["queued"] = {"error": str(e)}
    n = len(out.get("research", {}).get("resumed") or []) \
        + len(out.get("tasks", {}).get("interrupted") or [])
    n += len(out.get("queued", {}).get("readmitted") or [])
    _res = len(out.get("tasks", {}).get("resumable") or [])
    print(f"  Reconciliation: {n} item(s) adopted after restart"
          + (f" ({_res} can be resumed from a checkpoint)" if _res else ""))
    return out
