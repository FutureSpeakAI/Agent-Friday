"""Start a background task from a prompt the owner typed on another device (PW-G12).

There is one way a task starts: ``agent._spawn_task``. This gives the owner's phone that same door with what an owner
turn would give a task the owner asked for: principal ``owner``, the default tool set (every tool still behind the
action gate), the cloud and local policy in force, no model pin. Where the task came from is a journal decision, so the
task record and ``_spawn_task`` are unchanged.

The PC enforces the limits, not the device: nothing starts while everything is stopped, and at most
``DEVICE_JOB_CAP`` device-started tasks run at once.
"""
from __future__ import annotations

import re
import threading

ORIGIN = re.compile(r"^(pc|device:[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,95})$")
MAX_PROMPT = 4000
NAME_CHARS = 60
DEVICE_JOB_CAP = 3

_LOCK = threading.Lock()


class Refused(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _running_from_devices() -> int:
    """Device-started jobs still queued or running, read off the task records (each carries its origin)."""
    from agent_friday.services.agent import TASKS, TASKS_LOCK
    with TASKS_LOCK:
        return sum(1 for t in TASKS.values()
                   if str(t.get("origin") or "").startswith("device:") and t.get("status") in ("queued", "running"))


def _name(prompt: str) -> str:
    first = " ".join(prompt.split())
    return first if len(first) <= NAME_CHARS else first[:NAME_CHARS - 1].rstrip() + "…"


def start(prompt, origin, conversation_id=None) -> str:
    """Start the task and return its id, or raise Refused(status, words the owner can read)."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise Refused(400, "write what the job should do")
    if len(prompt) > MAX_PROMPT:
        raise Refused(400, "that is longer than a job can be; keep it under %d characters" % MAX_PROMPT)
    if not isinstance(origin, str) or not ORIGIN.match(origin):
        raise Refused(400, "not an origin")
    if conversation_id is not None:
        from agent_friday.services import conversations
        if not isinstance(conversation_id, str) or not conversations.load(conversation_id):
            raise Refused(404, "that conversation is not on this PC")
    from agent_friday.services import global_stop, task_journal
    if global_stop.active():
        raise Refused(409, "everything is stopped on this PC; nothing can start until it is let go")
    from agent_friday.services.agent import _spawn_task
    with _LOCK:
        if origin != "pc" and _running_from_devices() >= DEVICE_JOB_CAP:
            raise Refused(409, "%d jobs from your devices are already running; wait for one to finish" % DEVICE_JOB_CAP)
        task_id = _spawn_task(_name(prompt), prompt, conversation_id=conversation_id, principal="owner")
        if not task_id:
            raise Refused(409, "the PC would not start that job right now")
        from agent_friday.services.agent import TASKS, TASKS_LOCK
        with TASKS_LOCK:
            if task_id in TASKS:
                TASKS[task_id]["origin"] = origin
    task_journal.append(task_id, "decision", point="origin", chosen=origin,
                        reason="started by the owner from %s" % ("this PC" if origin == "pc" else "a paired device"))
    return task_id

