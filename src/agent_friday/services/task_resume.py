"""Midstream durability: pick a task back up where the crash left it.

THE PROBLEM. Friday's desktop server dies with work in flight — sometimes a
real crash, sometimes the recurring silent hang where the process is alive and
``friday.log`` simply stops. ``task_journal.reconcile_on_boot`` already notices
and is honest about it: every running task is marked ``interrupted`` with
"Nothing was resumed: re-run any of them from the Task Tray." Re-running from
the prompt throws away everything the task had already done — twenty minutes of
searches, file reads, drafted output — and re-runs every side effect it had
already taken. For a task that was mostly finished, that is the worst of both.

WHAT MAKES RESUME POSSIBLE. The agent loop's entire recoverable state is its
``convo`` list. Anthropic's tool protocol is append-only and self-describing: a
``tool_use`` block is answered by a ``tool_result`` block, and once that pair is
in the transcript the tool's work is a FACT in the conversation. So restoring
the convo restores the work AND guarantees no completed tool is executed twice —
the model sees the result it already got and moves on. We do not replay
anything. We re-enter the loop with the transcript it had.

That is why the checkpoint is taken at one specific instant: immediately after
``tool_results`` are appended, when every ``tool_use`` in the convo has its
matching ``tool_result``. A checkpoint anywhere else can be inconsistent, and
an inconsistent transcript is a 400 from the API, not a resumed task.

WHAT CANNOT BE RESUMED — stated here rather than discovered later:

  * **A tool that was in flight.** If the process died between "dispatch
    send_email" and "got the result", the mail may or may not have gone. The
    transcript cannot tell us and neither can anything else. So a tool is
    marked pending BEFORE dispatch and cleared after; if a checkpoint is found
    with a pending tool, resume is GATED unless that tool is Ring 0 (a pure
    read, safe to re-run). Everything else waits for a human answer. This is
    the one place where guessing is worse than stopping.
  * **Side effects already taken.** A file written, a calendar event created,
    money spent — those happened, they are not rolled back, and resume does not
    try. The transcript records them; the resumed task continues from after
    them. That is the correct behaviour and also the only available one.
  * **Live attachments.** An open voice session, a browser the user was
    watching, a streaming response half-delivered to a socket. The task
    resumes; those do not come back.
  * **The OpenAI-shaped loop** (``_oai_agentic_loop``) is not checkpointed yet.
    Its transcript has the same append-only property, so the same approach
    applies — it is unbuilt, not impossible.

WHY IT DOES NOT AUTO-RESUME BY DEFAULT. ``task_resume_auto`` is off. A task
that crashes Friday will crash it again on resume, and an automatic resume
turns one crash into a boot loop that burns money on every pass. Resume is
offered, one click, with the attempt counted (``MAX_ATTEMPTS``) so even the
opt-in path cannot loop forever.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

BLOB = "resume.json"
VERSION = 1

#: A transcript bigger than this is not checkpointed. At the measured ~164k
#: tokens per live iteration a convo runs ~700 KB; 8 MB leaves generous room
#: for screenshots-as-vision-blocks without letting one pathological task
#: write a gigabyte to disk every iteration.
MAX_BLOB_BYTES = 8 * 1024 * 1024

#: Even opt-in auto-resume stops after this many tries at the same task. A
#: task that crashes the process crashes it again; the second failure is data,
#: the fifth is a boot loop.
MAX_ATTEMPTS = 2

_LOCK = threading.Lock()


def _journal():
    from agent_friday.services import task_journal as _tj
    return _tj


def _settings() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def enabled() -> bool:
    """Checkpointing is on by default: it costs one atomic write per tool
    round and it is the only thing standing between a crash and lost work."""
    return bool(_settings().get("task_resume_enabled", True))


def auto_enabled() -> bool:
    """Resuming WITHOUT being asked is off by default. See the module note."""
    return bool(_settings().get("task_resume_auto", False))


# ─────────────────────────────────────────────────────────────────────────────
#  Which tools are safe to re-run
# ─────────────────────────────────────────────────────────────────────────────
def replay_safe(tool_name: str) -> bool:
    """True only for Ring 0 — a pure local read with no mutation and no reach.

    Ring 1 is a local WRITE. ``write_file`` re-run is not the same as
    ``write_file`` run once, and an append is visibly not. So the line is drawn
    at 0, not at 2: the question is not "how dangerous", it is "does running it
    twice differ from running it once".
    """
    try:
        from agent_friday.services.agent import TOOL_RINGS
        ring = TOOL_RINGS.get(tool_name)
    except Exception:
        return False
    return ring == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Writing the checkpoint
# ─────────────────────────────────────────────────────────────────────────────
def _fingerprint(text) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def checkpoint(task_id, *, convo, tool_trace=None, iteration=0, model=None,
               max_tokens=None, system=None, orb_label=None,
               orb_category="default", orb_icon="🧠", loop="anthropic") -> bool:
    """Save the transcript at a consistent boundary. Never raises.

    Call this ONLY where every ``tool_use`` in ``convo`` has its matching
    ``tool_result`` — in practice, right after the tool_results turn is
    appended. Returns False when nothing was written (disabled, no task id,
    too large, or the journal refused).
    """
    if not task_id or not enabled():
        return False
    try:
        blob = {
            "version": VERSION,
            "loop": loop,
            "convo": convo,
            "tool_trace": tool_trace or [],
            "iteration": int(iteration or 0),
            "model": model,
            "max_tokens": max_tokens,
            # The system prompt is NOT stored: it is rebuilt at resume from
            # persona + vault, and a copy on disk would be a second home for
            # vault content. The fingerprint is kept so a resume can say the
            # prompt moved underneath it.
            "system_sha": _fingerprint(system),
            "orb_label": orb_label,
            "orb_category": orb_category,
            "orb_icon": orb_icon,
            "pending_tool": None,
            "saved": time.time(),
            "attempts": (read(task_id) or {}).get("attempts", 0),
        }
        size = len(json.dumps(blob, default=str, ensure_ascii=False).encode("utf-8"))
        if size > MAX_BLOB_BYTES:
            _log.warning("task resume: transcript for %s is %s bytes, over the "
                         "%s cap - not checkpointed", task_id, size, MAX_BLOB_BYTES)
            return False
        with _LOCK:
            return bool(_journal().write_blob(task_id, BLOB, blob))
    except Exception as e:
        _log.warning("task resume: checkpoint failed for %s (%s)", task_id, e)
        return False


def mark_tool_pending(task_id, tool_name, tool_use_id=None) -> None:
    """Record that a tool is ABOUT to run. Never raises.

    The window this closes is small and the consequence is not: a crash inside
    ``_execute_tool`` leaves a transcript whose last assistant turn asked for a
    tool nobody can say ran. Writing the marker first means the resume path
    knows it is in that window instead of assuming it is not.
    """
    if not task_id or not enabled():
        return
    try:
        with _LOCK:
            blob = _journal().read_blob(task_id, BLOB)
            if not blob:
                return
            blob["pending_tool"] = {"name": tool_name, "tool_use_id": tool_use_id,
                                    "started": time.time()}
            _journal().write_blob(task_id, BLOB, blob)
    except Exception as e:
        _log.debug("task resume: could not mark %s pending (%s)", tool_name, e)


def clear_tool_pending(task_id) -> None:
    if not task_id or not enabled():
        return
    try:
        with _LOCK:
            blob = _journal().read_blob(task_id, BLOB)
            if not blob or not blob.get("pending_tool"):
                return
            blob["pending_tool"] = None
            _journal().write_blob(task_id, BLOB, blob)
    except Exception:
        pass


def read(task_id) -> Optional[Dict[str, Any]]:
    if not task_id:
        return None
    try:
        return _journal().read_blob(task_id, BLOB)
    except Exception:
        return None


def clear(task_id) -> None:
    """Drop the checkpoint. Called when the task ENDS - a finished task has
    nothing to resume, and a stale transcript on disk is an invitation to
    replay work that is already done."""
    if not task_id:
        return
    try:
        _journal().delete_blob(task_id, BLOB)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Reading it back
# ─────────────────────────────────────────────────────────────────────────────
def resumability(task_id) -> Dict[str, Any]:
    """Can this task be picked up, and what is the honest caveat?

    Returns ``{resumable, reason, iteration, pending_tool, needs_confirmation,
    attempts}``. ``needs_confirmation`` is the important one: it is True when a
    tool was in flight and re-running it is not provably harmless, and a caller
    that ignores it is the bug this function exists to prevent.
    """
    blob = read(task_id)
    if not blob:
        # "Missing" and "there but unreadable" are different answers and the
        # user deserves the right one. A vault passphrase rotation on this
        # machine already left 20+ older task records failing to decrypt; a
        # checkpoint in that state is lost work, not absent work, and saying
        # "no checkpoint was written" about it would be a lie.
        unreadable = False
        try:
            unreadable = _journal().blob_exists(task_id, BLOB)
        except Exception:
            pass
        return {"resumable": False,
                "reason": ("a checkpoint exists but cannot be read - most "
                           "likely written under a previous vault passphrase"
                           if unreadable else "no checkpoint was written"),
                "iteration": 0, "pending_tool": None,
                "needs_confirmation": False, "attempts": 0,
                "unreadable": unreadable}
    out = {
        "resumable": True,
        "reason": "",
        "iteration": int(blob.get("iteration") or 0),
        "pending_tool": blob.get("pending_tool"),
        "needs_confirmation": False,
        "attempts": int(blob.get("attempts") or 0),
        "saved": blob.get("saved"),
        "loop": blob.get("loop"),
    }
    if blob.get("version") != VERSION:
        return {**out, "resumable": False,
                "reason": f"checkpoint format v{blob.get('version')} is not v{VERSION}"}
    if out["attempts"] >= MAX_ATTEMPTS:
        return {**out, "resumable": False,
                "reason": f"already resumed {out['attempts']} times without finishing"}
    convo = blob.get("convo")
    if not isinstance(convo, list) or not convo:
        return {**out, "resumable": False, "reason": "the saved transcript is empty"}
    if not _consistent(convo):
        return {**out, "resumable": False,
                "reason": "the saved transcript has a tool call with no result - "
                          "it would be rejected by the API"}
    pt = blob.get("pending_tool")
    if pt:
        name = pt.get("name") or "?"
        if replay_safe(name):
            out["reason"] = (f"'{name}' was running when the process stopped. "
                             f"It only reads, so re-running it changes nothing.")
        else:
            out["needs_confirmation"] = True
            out["reason"] = (
                f"'{name}' was running when the process stopped, and nothing on "
                f"disk can say whether it finished. Resuming re-runs it. If it "
                f"had already taken effect, it will take effect twice.")
    else:
        out["reason"] = (f"{out['iteration']} step(s) of work are saved and will "
                         f"not be redone.")
    return out


def _consistent(convo: List[dict]) -> bool:
    """Every ``tool_use`` answered by a ``tool_result``.

    Anthropic rejects a transcript where an assistant asked for a tool and the
    next user turn does not answer it, so an inconsistent checkpoint is not a
    degraded resume - it is a 400. Checked here rather than discovered there.
    """
    want = set()
    try:
        for msg in convo:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            if msg.get("role") == "assistant":
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        want.add(b.get("id"))
            else:
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        want.discard(b.get("tool_use_id"))
    except Exception:
        return False
    return not want


# ─────────────────────────────────────────────────────────────────────────────
#  Doing it
# ─────────────────────────────────────────────────────────────────────────────
class ResumeRefused(RuntimeError):
    """The checkpoint exists but resuming from it is not safe or not possible.

    Always carries the reason in its message, because "cannot resume" without
    a why is indistinguishable from the silent loss this module exists to end.
    """


def _bump_attempts(task_id) -> None:
    try:
        with _LOCK:
            blob = _journal().read_blob(task_id, BLOB)
            if blob:
                blob["attempts"] = int(blob.get("attempts") or 0) + 1
                _journal().write_blob(task_id, BLOB, blob)
    except Exception:
        pass


def resume(task_id, *, confirm_pending: bool = False,
           session_ctx: Optional[dict] = None) -> Tuple[Optional[str], list]:
    """Re-enter the agent loop from the checkpoint.

    Raises ``ResumeRefused`` when the checkpoint says no - including when a
    non-replay-safe tool was in flight and ``confirm_pending`` was not passed.
    A caller that wants to proceed anyway must say so explicitly; there is no
    "probably fine" path, because the thing being guessed at is whether an
    email was sent.
    """
    verdict = resumability(task_id)
    if not verdict["resumable"]:
        raise ResumeRefused(verdict["reason"])
    if verdict["needs_confirmation"] and not confirm_pending:
        raise ResumeRefused(verdict["reason"])

    blob = read(task_id) or {}
    convo = blob.get("convo") or []
    tool_trace = blob.get("tool_trace") or []

    ctx = dict(session_ctx or {})
    ctx.setdefault("task_id", task_id)
    ctx["resumed_from_iteration"] = verdict["iteration"]

    _bump_attempts(task_id)
    try:
        _journal().append(task_id, "checkpoint",
                          summary=f"Resumed from step {verdict['iteration']} "
                                  f"after an interrupted run",
                          phase="resume")
    except Exception:
        pass

    from agent_friday.services.agent import _call_claude_agent
    return _call_claude_agent(
        convo,
        model=blob.get("model"),
        max_tokens=blob.get("max_tokens") or 16384,
        session_ctx=ctx,
        orb_label=blob.get("orb_label"),
        orb_category=blob.get("orb_category") or "default",
        orb_icon=blob.get("orb_icon") or "🧠",
        resumed_tool_trace=tool_trace,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Boot
# ─────────────────────────────────────────────────────────────────────────────
def resumable_after_boot() -> List[Dict[str, Any]]:
    """Every interrupted task that still has a usable checkpoint.

    Called after ``task_journal.reconcile_on_boot`` has marked the casualties,
    so this reads the same index and adds the one thing the journal could not
    previously say: which of them can be picked back up.
    """
    out: List[Dict[str, Any]] = []
    try:
        tj = _journal()
        for tid, row in tj.index_read().items():
            if row.get("status") != "interrupted":
                continue
            v = resumability(tid)
            if v["resumable"]:
                out.append({"task_id": tid, "name": row.get("name") or "", **v})
    except Exception as e:
        _log.warning("task resume: could not scan for resumable tasks (%s)", e)
    return out


def announce(resumable: List[Dict[str, Any]]) -> None:
    """Tell the user what survived. Separate from task_journal's interruption
    notice, which correctly says nothing was resumed - this one says what
    still CAN be."""
    items = [r for r in resumable if r.get("resumable")]
    if not items:
        return
    gated = [r for r in items if r.get("needs_confirmation")]
    names = ", ".join((r.get("name") or r["task_id"])[:40] for r in items[:5])
    more = f" and {len(items) - 5} more" if len(items) > 5 else ""
    body = (f"{names}{more} stopped midstream but their work is saved and can "
            f"be picked up where they left off.")
    if gated:
        body += (f" {len(gated)} had a tool running at the moment of the crash "
                 f"and will ask before re-running it.")
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=f"{len(items)} interrupted task"
                      f"{'s can be resumed' if len(items) != 1 else ' can be resumed'}",
                body=body, priority="medium", source="task-resume",
                kind="tasks_resumable",
                dedupe_key=f"tasks_resumable:{int(time.time())}",
                target={"workspace": "system", "tab": "tasks"})
    except Exception as e:
        _log.warning("task resume: announcement failed (%s)", e)
