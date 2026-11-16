"""Controlling jobs on the PC from the owner's phone or tablet (Mobile v3; wider brief step 3, ``pc_act``).

Who: an owner-bound phone or tablet that holds ``pc_act``. The right is its own screen-only card at the PC, never part
of the pairing bundle; a device that asks without it raises that card (once an hour) and gets 403 ``needs_right``.
What: three actions on a job, each one fixed desktop POST with no body from the device, so the PC applies the same
checks it applies to its own button: stop after the current step, resume an interrupted job, run a finished one again.
A resume that needs the owner's word about a tool that was mid-flight is refused with the desktop's reason; the device
cannot give that word (``confirm_pending`` is never sent). Reading a job's steps and whether it can resume needs only
``pc_read``. Every action writes a ``device_act`` receipt (device, right, action, job, ``via``, outcome).
Each rule is held by a test in tests/unit/test_tv_pc_act.py.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pc_read, tailnet
from agent_friday.tv_gateway.pc_common import ID_PATTERN, allowed, refuse

_log = logging.getLogger(__name__)

RIGHT = "pc_act"
_KEEP = ("ok", "error", "reason", "status", "stop_requested", "task_id", "rerun_of", "resuming", "resumable",
         "needs_confirmation")


def _desk_post(path: str) -> Tuple[int, dict]:
    """One POST with no body to this PC's own app, in process, with its own token. ``path`` is built here."""
    from agent_friday import core
    reply = core.app.test_client().post(path, json={}, headers={"X-Friday-Token": core._current_api_token()})
    body = reply.get_json(silent=True)
    return reply.status_code, body if isinstance(body, dict) else {}


def _guard() -> Optional[Response]:
    dev = getattr(g, "device", None)
    if not allowed(dev):
        return refuse(403, "this device cannot control the PC", code="pc_act_not_for_this_device")
    if not device_pairing.has_right(dev, RIGHT):
        from agent_friday.tv_gateway import pairing
        pairing.offer_right(dev["id"], RIGHT)
        return refuse(403, "this device has not been allowed to control the PC; the PC is asking on its screen",
                      code="needs_right", needs_right=RIGHT)
    return None


def _act(action: str, job_id: str, path: str) -> Response:
    denied = _guard()
    if denied is not None:
        return denied
    if not ID_PATTERN.match(job_id):
        return refuse(400, "not a job id", code="bad_id")
    try:
        status, body = _desk_post(path % job_id)
    except Exception as e:
        _log.warning("pc_act %s failed: %s", action, type(e).__name__)
        return refuse(503, "the PC could not do that right now", code="pc_unavailable")
    if status >= 500:
        return refuse(503, "the PC could not do that right now", code="pc_unavailable")
    device_pairing._receipt("device_act", g.device, right=RIGHT, action=action, job=job_id, outcome=status,
                            via=tailnet.via())
    answer = jsonify({k: body[k] for k in _KEEP if k in body})
    answer.status_code = status
    return answer


def register(app: Flask) -> None:
    @app.get("/tv/v1/jobs/<jid>/steps")
    def pc_job_steps(jid):
        denied = pc_read._guard("jobs")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(jid):
            return refuse(400, "not a job id", code="bad_id")
        limit = pc_read._int("limit", 0, 500)
        return pc_read._json_from_desk("/api/tasks/%s/steps" % jid, {"limit": limit} if limit else None, "job_steps")

    @app.get("/tv/v1/jobs/<jid>/resumable")
    def pc_job_resumable(jid):
        denied = pc_read._guard("jobs")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(jid):
            return refuse(400, "not a job id", code="bad_id")
        return pc_read._json_from_desk("/api/tasks/%s/resumable" % jid, None, "job_resumable")

    @app.post("/tv/v1/jobs/<jid>/stop-after-step")
    def pc_job_stop_after_step(jid):
        return _act("stop_after_step", jid, "/api/tasks/%s/stop-after-step")

    @app.post("/tv/v1/jobs/<jid>/resume")
    def pc_job_resume(jid):
        return _act("resume", jid, "/api/tasks/%s/resume")

    @app.post("/tv/v1/jobs/<jid>/rerun")
    def pc_job_rerun(jid):
        return _act("rerun", jid, "/api/tasks/%s/rerun")
