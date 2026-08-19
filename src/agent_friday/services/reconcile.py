"""What survives a restart, and what has to be admitted.

docs/design/conversations-and-concurrency.md §2.5/§3.4 and build steps 7-8.

Stephen's Q6 was "do background tasks survive a restart?" The honest answer
tonight was: at STORAGE yes, at EXECUTION no. Commissions were written to disk
faithfully and nothing ever picked them up again, so one sat frozen at the
`grinding` stage while the app that started it had long since restarted. It was
neither finished nor failed nor running — it simply stopped, silently, which is
the exact shape of defect this codebase has spent a day removing.

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
RUNNING_STAGES = {"scoping", "grinding", "verifying", "writing", "queued", "running"}


def _report(conversation_id: str, text: str, meta: dict | None = None) -> None:
    """Put a line in a conversation's transcript, server-side.

    Server-side is the point (§3.5): the old delivery path needed a live
    browser to witness a transition, so a task that completed while nothing was
    watching reported to nobody. Writing into the store means the message is
    there whenever he next opens that conversation — including after a restart.
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


def reconcile_tasks() -> dict:
    """Free-form agentic work cannot resume; it can only be admitted.

    A task left `running` in the ledger by a process that no longer exists is
    not running. Marking it interrupted — and saying so in the conversation
    that started it — is the honest outcome. Silently leaving it `running`
    produces exactly the stuck orbs he was looking at tonight.
    """
    touched = []
    try:
        from agent_friday.core import TASKS, TASKS_LOCK
    except Exception:
        return {"interrupted": []}
    now = time.time()
    with TASKS_LOCK:
        for tid, t in list(TASKS.items()):
            if (t or {}).get("status") != "running":
                continue
            t["status"] = "interrupted"
            t["ended"] = now
            t.setdefault("log", []).append(
                "Interrupted by a restart — this was a free-form run and its "
                "state lived in a process that no longer exists.")
            touched.append(tid)
    for tid in touched:
        owner = (TASKS.get(tid) or {}).get("conversation_id")
        name = (TASKS.get(tid) or {}).get("name") or tid
        _report(owner, f"**{name}** was interrupted when Friday restarted. "
                       f"It was a free-form run, so I cannot pick it up mid-way — "
                       f"say the word and I will start it again.",
                {"kind": "interruption_notice", "task_id": tid})
    return {"interrupted": touched}


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
    n = len(out.get("research", {}).get("resumed") or []) \
        + len(out.get("tasks", {}).get("interrupted") or [])
    print(f"  Reconciliation: {n} item(s) adopted after restart")
    return out
