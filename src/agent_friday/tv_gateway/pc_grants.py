"""Standing grants from the owner's phone or tablet (K5 PR D): see them, take one back, stop an offer.

Who: an owner-bound phone or tablet (``pc_common.allowed``).
* the list shows what Friday may do without asking, so it needs ``pc_approve`` with an active key, the same as the
  detail of a private card;
* a revoke and a stop-offering only narrow what Friday may do, so, like a deny, they need an owner device and no
  signature and no press.
What it never does: make a grant. A grant is made by the owner's press on an offer card, and an offer card is
approved through ``/tv/v1/approvals`` with a signature. The device names an id or a pattern; the path is built here.

Every revoke and stop writes a ``device_act`` receipt (device, action, grant, ``via``, outcome); a list writes a
``device_read`` receipt. 503 when the desktop failed, since it may already have acted.
Each rule is held by a test in tests/unit/test_tv_pc_grants.py.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify, request

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pc_read, tailnet
from agent_friday.tv_gateway.pc_common import ID_PATTERN, allowed, refuse

_log = logging.getLogger(__name__)

MAX_PATTERN = 200


def _desk(method: str, path: str, body: Optional[dict] = None) -> Tuple[int, dict]:
    """One call to this PC's own app, in process, with its own token. ``path`` is built here."""
    from agent_friday import core
    kw = {"headers": {"X-Friday-Token": core._current_api_token()}}
    if body is not None:
        kw["json"] = body
    reply = getattr(core.app.test_client(), method)(path, **kw)
    answer = reply.get_json(silent=True)
    return reply.status_code, answer if isinstance(answer, dict) else {}


def _owner_device() -> Optional[Response]:
    if not allowed(getattr(g, "device", None)):
        return refuse(403, "this device cannot manage the PC's standing grants", code="pc_grants_not_for_this_device")
    return None


def _act(action: str, subject: dict, method: str, path: str, body: Optional[dict] = None) -> Response:
    try:
        status, answer = _desk(method, path, body)
    except Exception as e:
        _log.warning("pc_grants %s failed: %s", action, type(e).__name__)
        status, answer = 503, {}
    device_pairing._receipt("device_act", g.device, right="owner_device", action=action, outcome=status,
                            via=tailnet.via(), **subject)
    if status >= 500:
        return refuse(503, "the PC could not do that right now", code="pc_unavailable")
    out = jsonify({k: answer[k] for k in ("ok", "error", "revoked", "stopped") if k in answer})
    out.status_code = status
    return out


def register(app: Flask) -> None:
    @app.get("/tv/v1/grants")
    def pc_grants_list():
        denied = _owner_device()
        if denied is not None:
            return denied
        current = device_pairing.get_device(g.device["id"]) or {}
        if not device_pairing.can_approve(current):
            return refuse(403, "this device has not been allowed to approve for the PC; see grants on the PC",
                          code="needs_right", needs_right="pc_approve")
        device_pairing._receipt("device_read", g.device, area="grants", right="pc_approve", via=tailnet.via())
        return pc_read._json_from_desk("/api/governance/standing-grants", None, "grants")

    @app.delete("/tv/v1/grants/<gid>")
    def pc_grant_revoke(gid):
        denied = _owner_device()
        if denied is not None:
            return denied
        if not ID_PATTERN.match(gid):
            return refuse(400, "not a grant id", code="bad_id")
        return _act("revoke_grant", {"grant": gid}, "delete", "/api/governance/grants/%s" % gid)

    @app.post("/tv/v1/grants/stop-offering")
    def pc_grant_stop_offering():
        denied = _owner_device()
        if denied is not None:
            return denied
        body = request.get_json(silent=True)
        pattern = body.get("pattern") if isinstance(body, dict) else None
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_PATTERN:
            return refuse(400, "send {pattern}: the pattern from the grants list", code="bad_pattern")
        return _act("stop_offering", {}, "post", "/api/governance/standing-grants/stop-offering",
                    {"pattern": pattern})
