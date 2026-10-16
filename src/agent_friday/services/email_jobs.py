"""Scheduled jobs and workflows that SEND their emails.

draft_email used to raise a send card every time; it now saves a draft in the
owner's Gmail Drafts unless the call says send=true. A job written before that
change, to email someone, would quietly stop sending. So, once, on the first
start after the change, every existing job and workflow that was made to send
email is marked to keep sending (`email_send: true` on the schedule record; a
workflow's slug in this module's state file), and the owner gets ONE notice in
Activity naming them. A job made afterwards saves drafts unless the owner
switches it to send on its Workflows card.

How a job is judged to send (any one is enough; when unsure it counts as a
sender, because the point is to keep what it did):

  * a pre-approval scoped to that job (governance/send_grants), or a logged use
    of one by it: the owner already let it send;
  * its run history mentions draft_email, an approval card or a queued email;
  * its own words (the prompt, a workflow's steps, its name) ask to send, email,
    mail, reply to, forward or message someone, or name draft_email.

A job whose words never mention email (a news digest, a re-index) is left
alone: it never called draft_email, so nothing about it changes.

Sending is unchanged by this: a marked job's draft_email still raises the
approval card unless the owner pre-approved the recipients.
"""
from __future__ import annotations

import json
from pathlib import Path
import os
import logging
import re
import threading
import time as _time

_log = logging.getLogger("friday.email_jobs")

#: The flag on a schedule record (top level, never inside `task`: the task is
#: part of a job pre-approval's fingerprint, and marking a job must not void it).
FLAG = "email_send"
STATE_NAME = "email_send_migration.json"
NOTICE_KEY = "email-send-migration"
_LOCK = threading.RLock()

#: Words that ask a job to send something to someone. Broad on purpose.
_SEND_WORDS = re.compile(
    r"draft_email|send_email|\bsend(s|ing)?\b|\bsent\b|\bre-?ply\b|\breplies\b|\bforward(s|ing)?\b"
    r"|\be-?mail(s|ed|ing)?\s+(me|him|her|them|us|it|this|that|the|a|an|my|our|to|back)\b"
    r"|\be-?mail(ed)?\s+\S+@\S+|\bmail\s+(it|this|them|that)\s+to\b|\bmessage\s+(me|him|her|them|us)\b"
    r"|\bnotify\b.*\b(by|via)\s+e-?mail\b|\bnewsletter\b",
    re.I)
#: What a run of a sending job said about itself in its history.
_RUN_WORDS = re.compile(r"draft_email|approval card|queued .{0,40}(e-?mail|message)|"
                        r"(e-?mail|message) .{0,40}(queued|waiting for (your )?approval|sent)", re.I)


def _state_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / STATE_NAME


def state() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_state(d: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8", newline="\n")
    tmp.replace(p)


def words_say_send(*texts) -> bool:
    return any(_SEND_WORDS.search(str(t or "")) for t in texts)


def _granted(sid: str) -> bool:
    """A pre-approval scoped to this job, or a logged use of one by it."""
    try:
        from agent_friday.governance import send_grants as _sg
        if any(g.get("scope") == "job" and g.get("schedule_id") == sid
               for g in _sg.list_grants(include_revoked=True)):
            return True
        return any(str(e.get("schedule_id") or "") == sid for e in _sg.log(500))
    except Exception:
        return False


def _runs_say_send(sid: str) -> bool:
    try:
        from agent_friday.services import scheduler as _sched
        for r in _sched.run_history(sid, limit=25):
            if _RUN_WORDS.search(" ".join(str(r.get(k) or "") for k in ("summary", "error", "result"))):
                return True
    except Exception:
        pass
    return False


def _workflow_text(slug: str) -> list:
    try:
        from agent_friday.services import agent as _agent
        chain = _agent.load_workflow_chain(slug) or {}
    except Exception:
        chain = {}
    out = [chain.get("name"), chain.get("description"), chain.get("success_criteria")]
    for st in chain.get("steps") or []:
        out += [(st or {}).get("name"), (st or {}).get("prompt")]
    return out


def job_sends(rec: dict) -> str:
    """Why this saved job counts as one that sends email, or "" when it does not."""
    rec = rec or {}
    task = rec.get("task") or {}
    if rec.get("source") == "builtin" or task.get("kind", "builtin") == "builtin":
        return ""
    sid = str(rec.get("id") or "")
    if sid and _granted(sid):
        return "you pre-approved its sends"
    if sid and _runs_say_send(sid):
        return "its runs sent or queued email"
    texts = [rec.get("name"), task.get("prompt")]
    if task.get("kind") == "workflow" and task.get("ref"):
        texts += _workflow_text(str(task["ref"]))
    if words_say_send(*texts):
        return "its instructions ask it to send email"
    return ""


def migrate() -> dict:
    """The one-time mark. Idempotent: a second call finds the state file done
    and changes nothing. Returns the state."""
    with _LOCK:
        st = state()
        if st.get("done"):
            # Installs marked before the flag lived on the workflow record:
            # write it there once; the list stays as the migration record.
            if not st.get("flags_written"):
                _write_workflow_flags(st)
                st["flags_written"] = True
                _write_state(st)
            return st
        from agent_friday.services import scheduler as _sched
        jobs, workflows, seen_refs = [], [], set()
        for rec in _sched.list_schedules():
            why = job_sends(rec)
            task = rec.get("task") or {}
            if task.get("kind") == "workflow" and task.get("ref"):
                seen_refs.add(str(task["ref"]))
            if not why:
                continue
            if rec.get(FLAG) is not True:
                _sched._patch_record(rec["id"], **{FLAG: True})
            jobs.append({"id": rec["id"], "name": rec.get("name") or rec["id"], "why": why})
            if task.get("kind") == "workflow" and task.get("ref"):
                workflows.append({"slug": str(task["ref"]), "name": rec.get("name") or task["ref"],
                                  "why": why, "scheduled": True})
        try:
            from agent_friday.services import agent as _agent
            chains = _agent.list_workflow_chains()
        except Exception:
            chains = []
        for c in chains:
            slug = str(c.get("slug") or "")
            if not slug or slug in seen_refs:
                continue
            if words_say_send(*_workflow_text(slug)):
                workflows.append({"slug": slug, "name": c.get("name") or slug,
                                  "why": "its steps ask it to send email", "scheduled": False})
        st = {"done": True, "at": _time.time(), "jobs": jobs,
              "send_workflows": sorted({w["slug"] for w in workflows}), "workflows": workflows}
        _write_workflow_flags(st)
        st["flags_written"] = True
        _write_state(st)
        _log.info("email jobs: %d scheduled job(s) and %d workflow(s) keep sending their emails",
                  len(jobs), len([w for w in workflows if not w["scheduled"]]))
        _notify(st)
        return st


def _write_workflow_flags(st: dict) -> None:
    """The one source of truth is the workflow record's own `email_send`: every
    workflow the mark kept sending gets `email_send: true` written onto it,
    unless the owner already chose for it. `send_workflows` in the state file
    is only the record of what this migration did."""
    for slug in st.get("send_workflows") or []:
        try:
            from agent_friday.services import agent as _agent
            chain = _agent.load_workflow_chain(slug) or {}
            if not chain or isinstance(chain.get(FLAG), bool):
                continue
            set_workflow_email_send(slug, True)
        except Exception as e:
            _log.warning("email jobs: could not mark workflow %s as sending: %s", slug, type(e).__name__)


def _notify(st: dict) -> None:
    """ONE notice in Activity listing what was kept sending. None when nothing was."""
    names = [j["name"] for j in st.get("jobs") or []]
    names += [w["name"] for w in st.get("workflows") or [] if not w.get("scheduled")]
    if not names:
        return
    try:
        import agent_friday.notifications_engine as _ne
        listed = "; ".join(names[:12]) + ("; and %d more" % (len(names) - 12) if len(names) > 12 else "")
        _ne.push(title="Your email jobs keep sending", body=(
            "Emails Friday writes are now saved as drafts in Gmail unless you ask to send. These jobs were "
            "made to send email, so they still send (each send asks you first unless you pre-approved it): "
            + listed + ". New jobs save drafts. You can change a job on its card in Workflows."),
            priority="low", source="scheduler", kind="email_jobs_kept_sending", tier="fyi",
            emitter="MJ1", dedupe_key=NOTICE_KEY, target={"workspace": "workflows"})
    except Exception as e:
        _log.warning("email jobs notice not shown: %s", e)


def workflow_sends(slug: str) -> bool:
    """Does this saved workflow SEND the emails it writes when it runs on request?

    The owner's choice on its Workflows card is the workflow record's own `email_send` (kept outside
    the fields a job pre-approval fingerprints: send_grants._WORKFLOW_CONTENT). A workflow with no
    choice of its own saves drafts: the one-time mark writes `email_send: true` onto every workflow
    it kept sending, so the record is the one source of truth (the state file only records what
    the migration did)."""
    slug = str(slug or "")
    if not slug:
        return False
    try:
        from agent_friday.services import agent as _agent
        chain = _agent.load_workflow_chain(slug) or {}
    except Exception:
        chain = {}
    return chain.get(FLAG) is True


def set_workflow_email_send(slug: str, send: bool) -> dict | None:
    """The owner's switch on a saved workflow's card: its emails are sent (each asks first unless
    pre-approved) or saved as drafts. Written on the workflow record, outside the fingerprinted
    content and without a new revision (it changes nothing the workflow does but this choice), so a
    job pre-approval for a schedule that runs it is not voided and an open editor is not told the
    workflow changed. Returns the record, or None when there is no such workflow."""
    if not isinstance(send, bool):
        raise ValueError("email_send is true (send) or false (save as drafts)")
    from agent_friday.services import agent as _agent
    from agent_friday.services import workflow_operations as _ops
    with _ops.LOCK:
        chain = _agent.load_workflow_chain(slug)
        if not chain:
            return None
        chain = dict(chain, **{FLAG: send})
        base = os.path.realpath(str(_agent._workflows_dir()))
        name = "%s.json" % _agent._chain_slug(chain.get("slug") or slug)
        target_s = os.path.realpath(os.path.join(base, name))
        # The file stays directly inside the workflows folder, whatever the slug.
        if os.path.dirname(target_s) != base or not target_s.startswith(base + os.sep):
            raise ValueError("that workflow name does not name a file in the workflows folder")
        target = Path(target_s)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(chain, indent=2), encoding="utf-8", newline="\n")
        tmp.replace(target)
    _log.info("workflow %s: its emails are %s", chain.get("slug"), "sent" if send else "saved as drafts")
    return chain


def sends_for(ctx: dict | None) -> bool:
    """Is the running call part of a job or workflow marked to send its emails?"""
    ctx = ctx or {}
    sid = str(ctx.get("schedule_id") or "")
    if sid:
        try:
            from agent_friday.services import scheduler as _sched
            rec = _sched.get_schedule(sid) or {}
            if FLAG in rec:
                return rec.get(FLAG) is True
        except Exception:
            pass
    tid = ctx.get("task_id")
    if tid:
        try:
            from agent_friday.services import agent as _agent
            with _agent.TASKS_LOCK:
                slug = str((_agent.TASKS.get(tid) or {}).get("chain") or "")
        except Exception:
            slug = ""
        if slug and workflow_sends(slug):
            return True
    return False
