"""Has this conversation or task ever read outside content? A flag that only goes on.

A pre-approved send (governance/send_grants) is used only for a CLEAN call:
nothing in it, and nothing the work behind it read, came from outside content
(an email, a web page, a document, a connector). The provenance ledger
(services/taint) is the wrong place to answer the second half on its own: it
is held in memory, keeps the last 200 entries for six hours, is forgotten on a
restart or when 64 other conversations push it out, and a task spawned from a
chat starts a ledger of its own. A conversation that read an injected email an
hour, a day or a restart ago still holds that text in its history, and a task
it spawned with a paraphrased goal carries it on.

So the answer is kept here, as a flag that is set the first time outside
content is read and is never cleared:

* on the conversation record (conversations/<id>/conversation.json,
  "outside_content") and the task record (the task journal's state,
  "outside_content"), and
* in an append-only marks file beside the grants
  (outside_content_marks.jsonl), which also covers a ledger with no record
  (a voice session, a context with no conversation).

What sets it (`mark`): a tool result recorded as content (agent's post hook),
carried text written from outside content, and a task inheriting it at spawn
(`inherit_at_spawn`: from the conversation or task whose tool call spawned it,
and from the parent task of a chain step or follow-up). A task that reads
outside content also flags the conversation it reports into: its result
lands there.

What only counts for the current turn (`note_turn`, `turn_outside`): a chat
turn whose prompt carried recalled memory or conversation recall (stamped by
routes/chat as `outside_in_prompt`), and a read of the wiki, contacts or task
results, which the model can have written itself (correct_wiki applies a
model's text, a task's result is written from what it read) and which carry
no record of who wrote each line.

Used only by the pre-approved send check; nothing here grants anything.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Optional

_log = logging.getLogger("friday.outside_content")
_LOCK = threading.RLock()

MARKS_NAME = "outside_content_marks.jsonl"

#: Tools whose output the model may have written itself (or that report work
#: that read outside content) but that are listed as the owner's own data for
#: argument provenance. A read of one counts as outside content for a
#: pre-approved send in that turn.
TURN_SCOPED_TOOLS = {"read_wiki": "Friday's notes (which Friday can write)",
                     "search_wiki": "Friday's notes (which Friday can write)",
                     "search_contacts": "contact notes (which Friday can write)",
                     "list_tasks": "task results (written from what the tasks read)",
                     "workflow_status": "workflow results (written from what the steps read)"}

_CACHE: dict = {}                    # marks path -> {key: why}
_TURN: "OrderedDict[str, tuple]" = OrderedDict()   # ledger key -> (turn id, why)
_TURN_MAX = 512


def _path():
    from agent_friday.governance.action_gate import _gov_dir
    return _gov_dir() / MARKS_NAME


def _marks() -> dict:
    p = _path()
    k = str(p)
    with _LOCK:
        got = _CACHE.get(k)
        if got is not None:
            return got
        got = {}
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("key"):
                    got.setdefault(str(row["key"]), str(row.get("why") or "outside content"))
        except FileNotFoundError:
            pass
        except Exception as e:
            _log.warning("outside-content marks unreadable (%s); treating every key as marked", e)
            got = {"*": "the outside-content record could not be read"}
        _CACHE[k] = got
        return got


def _append(key: str, why: str) -> None:
    marks = _marks()
    if key in marks:
        return
    p = _path()
    with _LOCK:
        if key in marks:
            return
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "key": key, "why": why},
                                ensure_ascii=False) + "\n")
        marks[key] = why


def _ledger_key(ctx) -> str:
    from agent_friday.services import taint
    return taint.ledger_key(ctx or {})


# ── records ─────────────────────────────────────────────────────────────────

def _mark_conversation(cid: str, why: str) -> None:
    try:
        from agent_friday.services import conversations as _cv
        _cv.mark_outside_content(cid, why)
    except Exception as e:
        _log.debug("conversation %s not flagged on its record: %s", cid, e)


def _mark_task(tid: str, why: str) -> None:
    try:
        from agent_friday.services import agent as _agent
        with _agent.TASKS_LOCK:
            rec = _agent.TASKS.get(tid)
            have = bool((rec or {}).get("outside_content"))
        if rec is not None and not have:
            _agent._task_set(tid, outside_content={"at": time.time(), "why": why})
    except Exception as e:
        _log.debug("task %s not flagged on its record: %s", tid, e)


def _conversation_flag(cid: str) -> str:
    try:
        from agent_friday.services import conversations as _cv
        rec = _cv.load(cid) or {}
        oc = rec.get("outside_content")
        return str((oc or {}).get("why") or "outside content") if oc else ""
    except Exception:
        return ""


def _task_flag(tid: str) -> str:
    try:
        from agent_friday.services import agent as _agent
        with _agent.TASKS_LOCK:
            oc = (_agent.TASKS.get(tid) or {}).get("outside_content")
        return str((oc or {}).get("why") or "outside content") if oc else ""
    except Exception:
        return ""


# ── the flag ────────────────────────────────────────────────────────────────

def mark(ctx, why: str = "outside content") -> None:
    """This conversation or task has read outside content. Never raises."""
    sc = ctx or {}
    why = str(why or "outside content")[:200]
    try:
        _append("ledger:" + _ledger_key(sc), why)
        if sc.get("task_id"):
            tid = str(sc["task_id"])
            _append("task:" + tid, why)
            _mark_task(tid, why)
        if sc.get("conversation_id"):
            cid = str(sc["conversation_id"])
            _append("conversation:" + cid, why)
            _mark_conversation(cid, why)
    except Exception as e:
        _log.warning("could not record that outside content was read: %s", e)


def mark_task(task_id: str, why: str) -> None:
    """Flag one task (inherited at spawn). Never raises."""
    if not task_id:
        return
    try:
        _append("task:" + str(task_id), why)
        _append("ledger:task:" + str(task_id), why)
        _mark_task(str(task_id), why)
    except Exception as e:
        _log.warning("could not flag task %s: %s", task_id, e)


def task_marked(task_id: str) -> str:
    """Why this task counts as having read outside content, or ""."""
    if not task_id:
        return ""
    marks = _marks()
    return (marks.get("*") or marks.get("task:" + str(task_id))
            or marks.get("ledger:task:" + str(task_id)) or _task_flag(str(task_id)))


def is_marked(ctx) -> str:
    """Why this call's conversation or task counts as having read outside
    content (ever), or "". A task answers for itself (its own reads, and what
    it inherited when it was spawned); a chat or voice call for its
    conversation and its ledger."""
    sc = ctx or {}
    try:
        marks = _marks()
        if marks.get("*"):
            return marks["*"]
        why = marks.get("ledger:" + _ledger_key(sc), "")
        if why:
            return why
        if sc.get("task_id"):
            return task_marked(str(sc["task_id"]))
        cid = str(sc.get("conversation_id") or "")
        if cid:
            return marks.get("conversation:" + cid) or _conversation_flag(cid)
        return ""
    except Exception as e:
        return "the outside-content check could not run (%s)" % e


def inherit_at_spawn(task_id: str, parent_task_id: Optional[str] = None,
                     spawning_ctx: Optional[dict] = None) -> str:
    """A task spawned from a flagged conversation or task (by a tool call in
    it, or as the next step or follow-up of a flagged task) starts flagged:
    its goal may paraphrase what was read there. Returns why, or ""."""
    why = ""
    try:
        if spawning_ctx:
            w = is_marked(spawning_ctx) or turn_outside(spawning_ctx)
            if w:
                why = "spawned from work that read outside content (%s)" % w
        if not why and parent_task_id:
            w = task_marked(str(parent_task_id))
            if w:
                why = "continues a task that read outside content (%s)" % w
        if why:
            mark_task(task_id, why[:200])
    except Exception as e:
        _log.warning("could not carry the outside-content flag to task %s: %s", task_id, e)
    return why


# ── this turn only ──────────────────────────────────────────────────────────

def turn_id(ctx) -> Optional[str]:
    """The turn a call belongs to, as `agent._execute_tool` names it: a typed
    turn's own id, a spoken turn's, or "task-<id>" for a background task."""
    sc = ctx or {}
    try:
        from agent_friday.services import turn_plan
        t = turn_plan.current_turn_id()
        if t:
            return str(t)
    except Exception:
        pass
    if sc.get("turn_id"):
        return str(sc["turn_id"])
    if sc.get("task_id"):
        return "task-%s" % sc["task_id"]
    return None


def note_turn(ctx, why: str) -> None:
    """Something read in this turn counts as outside content for a
    pre-approved send until the turn ends. A task has no turns but itself, and
    a call with no turn id cannot be told apart from the next one: both are
    flagged for good instead."""
    sc = ctx or {}
    t = turn_id(sc)
    if sc.get("task_id") or not t:
        mark(sc, why)
        return
    with _LOCK:
        key = _ledger_key(sc)
        _TURN[key] = (t, str(why)[:200])
        _TURN.move_to_end(key)
        while len(_TURN) > _TURN_MAX:
            _TURN.popitem(last=False)


def turn_outside(ctx) -> str:
    """Why this turn counts as having outside content, or ""."""
    sc = ctx or {}
    if sc.get("outside_in_prompt"):
        return str(sc["outside_in_prompt"])
    with _LOCK:
        got = _TURN.get(_ledger_key(sc))
    if got and got[0] == turn_id(sc):
        return got[1]
    return ""


def end_turn(ctx) -> None:
    sc = ctx or {}
    keys = {_ledger_key(sc)}
    if sc.get("conversation_id"):
        keys |= {str(sc["conversation_id"]), "conversation:%s" % sc["conversation_id"]}
    with _LOCK:
        for k in keys:
            _TURN.pop(k, None)


def reset_cache() -> None:
    """Tests: forget the in-memory copy of the marks (the file stays)."""
    with _LOCK:
        _CACHE.clear()
        _TURN.clear()
