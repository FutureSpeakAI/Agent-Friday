"""Start a background task from a prompt the owner typed on another device (PW-G12).

There is one way a task starts: ``agent._spawn_task``. This gives the owner's phone that same door with what an owner
turn would give a task the owner asked for: principal ``owner``, the default tool set (every tool still behind the
action gate), the cloud and local policy in force, no model pin. Where the task came from is a journal decision, so the
task record and ``_spawn_task`` are unchanged.

The PC enforces the limits, not the device: nothing starts while everything is stopped, and at most
``DEVICE_JOB_CAP`` device-started tasks run at once.

``origin`` is ``pc`` for the owner at this PC (not counted against the cap) or ``device:<id>``. A device never names
its own origin: the gateway builds ``device:<id>`` from the id it authenticated (tv_gateway/pc_act), and a caller that
may reach this route with the PC's token is the owner, who needs no job started for them to start one.
The cap holds because the only caller that names a device origin is that gateway door, and its test
``test_the_device_cannot_choose_the_origin_or_send_anything_else`` (PR #967, tests/unit/test_tv_pc_jobs_start.py)
fails if it ever forwards an origin from the device's own body.
"""
from __future__ import annotations

import logging
import re
import threading

ORIGIN = re.compile(r"^(pc|device:[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,95})$")
MAX_PROMPT = 4000
NAME_CHARS = 60
DEVICE_JOB_CAP = 3

_log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_FROM_DEVICE: dict = {}


class Refused(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _running_from_devices() -> int:
    """Device-started jobs that have not ended. A job waiting on an approval card, for a seat, or paused is still
    the device's job: only a terminal status (or a record that is gone) frees its place."""
    from agent_friday.services.agent import TASKS, TASKS_LOCK
    from agent_friday.services.task_journal import is_terminal
    with TASKS_LOCK:
        live = {tid for tid in _FROM_DEVICE
                if tid in TASKS and not is_terminal((TASKS[tid] or {}).get("status"))}
    for tid in set(_FROM_DEVICE) - live:
        _FROM_DEVICE.pop(tid, None)
    return len(live)


class DeviceSlot:
    """The cap for any other door that starts a NEW task for a device (a re-run): ``with DeviceSlot(origin) as slot:``
    holds the job lock, refuses with 409 when devices already run ``DEVICE_JOB_CAP`` jobs, and
    ``slot.adopt(task_id)`` counts the new task as a device's. No origin, or ``pc``, is not capped. The cap is
    across all devices together. Resuming an existing task starts no new task and is not counted here."""

    def __init__(self, origin):
        if origin is not None and (not isinstance(origin, str) or not ORIGIN.fullmatch(origin)):
            raise Refused(400, "not an origin")
        self.origin = None if origin in (None, "pc") else origin

    def __enter__(self):
        _LOCK.acquire()
        try:
            if self.origin is not None and _running_from_devices() >= DEVICE_JOB_CAP:
                raise Refused(409, "%d jobs from your devices are already running; wait for one to finish" % DEVICE_JOB_CAP)
        except BaseException:
            _LOCK.release()           # __exit__ does not run when __enter__ raises
            raise
        return self

    def __exit__(self, *exc):
        _LOCK.release()
        return False

    def adopt(self, task_id) -> None:
        if self.origin is not None and task_id:
            _FROM_DEVICE[task_id] = self.origin


def _name(prompt: str) -> str:
    first = " ".join(prompt.split())
    return first if len(first) <= NAME_CHARS else first[:NAME_CHARS - 1].rstrip() + "…"


def start(prompt, origin, conversation_id=None) -> str:
    """Start the task and return its id, or raise Refused(status, words the owner can read)."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise Refused(400, "write what the job should do")
    if len(prompt) > MAX_PROMPT:
        raise Refused(400, "that is longer than a job can be; keep it under %d characters" % MAX_PROMPT)
    if not isinstance(origin, str) or not ORIGIN.fullmatch(origin):
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
        try:
            task_id = _spawn_task(_name(prompt), prompt, conversation_id=conversation_id, principal="owner")
        except Exception as e:
            _log.warning("task_start: the PC could not start a job: %s", type(e).__name__)
            raise Refused(409, "the PC could not start that job right now") from e
        if not task_id:
            raise Refused(409, "the PC would not start that job right now")
        if origin != "pc":
            _FROM_DEVICE[task_id] = origin
    try:
        task_journal.append(task_id, "decision", point="origin", chosen=origin,
                            reason="started by the owner from %s" % ("this PC" if origin == "pc" else origin))
    except Exception as e:
        # The job is already running: a journal that will not take the note must not make it look failed.
        _log.warning("task_start: could not journal the origin of %s: %s", task_id, type(e).__name__)
    return task_id

