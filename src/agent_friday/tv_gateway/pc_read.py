"""The PC's read door for the owner's phone and tablet (Mobile v3, Addendum B, ``pc_read``).

Stephen's order (2026-11-15, Q11): the chat list, then workspaces, then jobs, then media. These routes only READ.
Nothing here runs a verb, changes a setting or decides an approval; those wait for ``pc_act`` / ``pc_approve``.

* Who: an owner-bound phone or tablet, never a watch, a TV, a family device or an unbound one, and only one that
  holds the ``pc_read`` right. The right is its own screen-only card at the PC (never part of the pairing bundle,
  never preselected); a device that asks without it raises that card, once an hour, and gets 403 ``needs_right``.
* What: the desktop's own answers, so the phone sees exactly what the PC's screens see. Chats, jobs, journals and
  media come from the desktop's own GET routes, called in process with this PC's token, but ONLY through the
  fixed list of paths below (ids checked, parameters from an allowlist, size capped). The device never names a
  path, so this is not a proxy. Journals keep the desktop's sealing (reasoning is withheld as it is on the PC).
* Audit: each device reading an area writes one ``device_read`` receipt per hour (device, kind, area, ``via``),
  not one per item, so the PC can show "From your devices" without a flood.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify, request

from agent_friday.tv_gateway.pc_common import ID_PATTERN, allowed, refuse as _refuse

_log = logging.getLogger(__name__)

RIGHT = "pc_read"
MAX_JSON = 4 * 1024 * 1024
MAX_PREVIEW = 3 * 1024 * 1024
AUDIT_EVERY_S = 3600.0
_MEDIA_ARGS = ("view", "q", "kind", "type", "project", "status", "tag", "source", "from", "to", "limit",
               "offset", "sort", "collection")
_AUDITED: dict = {}
_LOCK = threading.Lock()

# Fields of a workspace the phone needs to draw its entry; the rest of the registry stays on the PC.
_WS_FIELDS = ("id", "label", "group", "icon", "blurb", "description", "order", "kind", "voice_names")


def _guard(area: str) -> Optional[Response]:
    dev = getattr(g, "device", None)
    if not allowed(dev):
        return _refuse(403, "this device cannot read the PC", code="pc_read_not_for_this_device")
    from agent_friday.services import device_pairing
    if not device_pairing.has_right(dev, RIGHT):
        from agent_friday.tv_gateway import pairing
        pairing.offer_right(dev["id"], RIGHT)
        return _refuse(403, "this device has not been allowed to read the PC; the PC is asking on its screen",
                       code="needs_right", needs_right=RIGHT)
    _audit(dev, area)
    return None


def _audit(dev: dict, area: str) -> None:
    key = "%s:%s" % (dev.get("id"), area)
    now = time.time()
    with _LOCK:
        if now - _AUDITED.get(key, 0.0) < AUDIT_EVERY_S:
            return
        _AUDITED[key] = now
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import tailnet
    device_pairing._receipt("device_read", dev, area=area, right=RIGHT, via=tailnet.via())


def _desk(path: str, params: Optional[dict] = None) -> Tuple[int, bytes, str]:
    """One GET to this PC's own app, in process, with its own token. ``path`` is always built here."""
    from agent_friday import core
    r = core.app.test_client().get(path, query_string=params or {},
                                   headers={"X-Friday-Token": core._current_api_token()})
    return r.status_code, r.get_data(), r.headers.get("Content-Type", "application/json")


def _json_from_desk(path: str, params: Optional[dict] = None, area: str = "") -> Response:
    try:
        status, body, ctype = _desk(path, params)
    except Exception as e:
        _log.warning("pc_read %s failed: %s", area, type(e).__name__)
        return _refuse(503, "the PC could not answer that right now", code="pc_unavailable")
    if len(body) > MAX_JSON:
        return _refuse(413, "that is too big to send to a phone; ask for a smaller page", code="too_big")
    if status >= 500:
        return _refuse(503, "the PC could not answer that right now", code="pc_unavailable")
    resp = Response(body, status=status, mimetype=ctype.split(";")[0] or "application/json")
    resp.headers["X-Friday-As-Of"] = str(int(time.time()))
    return resp


def _int(name: str, default: int, hi: int) -> int:
    try:
        return max(0, min(hi, int(request.args.get(name, default) or default)))
    except (TypeError, ValueError):
        return default


def register(app: Flask) -> None:
    # ── 1. the chat list ────────────────────────────────────────────────────
    @app.get("/tv/v1/chats")
    def pc_chats():
        denied = _guard("chats")
        if denied is not None:
            return denied
        params = {"archived": "1"} if request.args.get("archived") in ("1", "true", "yes") else {}
        return _json_from_desk("/api/conversations", params, "chats")

    @app.get("/tv/v1/chats/<cid>")
    def pc_chat(cid):
        denied = _guard("chats")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(cid):
            return _refuse(400, "not a chat id", code="bad_id")
        return _json_from_desk("/api/conversations/%s" % cid, None, "chat")

    @app.get("/tv/v1/chats/<cid>/messages")
    def pc_chat_messages(cid):
        denied = _guard("chats")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(cid):
            return _refuse(400, "not a chat id", code="bad_id")
        return _json_from_desk("/api/conversations/%s/messages" % cid, {"limit": _int("limit", 200, 500) or 200},
                               "chat_messages")

    # ── 2. workspaces ───────────────────────────────────────────────────────
    @app.get("/tv/v1/workspaces")
    def pc_workspaces():
        denied = _guard("workspaces")
        if denied is not None:
            return denied
        from agent_friday.services import workspace_registry as reg
        try:
            reg_all = reg.registry()
            held = {w["id"] for w in reg.all_workspaces() if reg.is_held(w)}
            shown = [_ws_entry(w) for w in reg.all_workspaces() if w["id"] not in held]
            return jsonify({"ok": True, "as_of": int(time.time()), "groups": reg_all.get("groups") or [],
                            "workspaces": shown, "count": len(shown)})
        except Exception as e:
            _log.warning("pc_read workspaces failed: %s", type(e).__name__)
            return _refuse(503, "the PC could not list its workspaces right now", code="pc_unavailable")

    @app.get("/tv/v1/workspaces/<ws>")
    def pc_workspace(ws):
        denied = _guard("workspaces")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(ws):
            return _refuse(400, "not a workspace id", code="bad_id")
        from agent_friday.services import workspace_registry as reg
        entry = reg.get(ws)
        if entry is None or reg.is_held(entry):
            return _refuse(404, "this PC has no workspace called %r" % ws, code="unknown_workspace",
                           known=[w["id"] for w in reg.workspaces()])
        out = {"ok": True, "as_of": int(time.time()), "workspace": _ws_entry(entry)}
        try:
            from agent_friday.services import laya_manifests as lm
            out["verbs"] = [{"id": v.get("id"), "label": v.get("label") or v.get("name"),
                             "kind": v.get("kind") or v.get("risk")} for v in lm.verbs(ws) if isinstance(v, dict)]
            out["actions_here"] = False                  # a device cannot run a verb until pc_act exists
        except Exception:
            out["verbs"] = []
        return jsonify(out)

    # ── 3. jobs and their journals ──────────────────────────────────────────
    @app.get("/tv/v1/jobs")
    def pc_jobs():
        denied = _guard("jobs")
        if denied is not None:
            return denied
        return _json_from_desk("/api/tasks", None, "jobs")

    @app.get("/tv/v1/jobs/<jid>")
    def pc_job(jid):
        denied = _guard("jobs")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(jid):
            return _refuse(400, "not a job id", code="bad_id")
        return _json_from_desk("/api/tasks/%s" % jid, None, "job")

    @app.get("/tv/v1/jobs/<jid>/journal")
    def pc_job_journal(jid):
        denied = _guard("jobs")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(jid):
            return _refuse(400, "not a job id", code="bad_id")
        params = {"since": _int("since", 0, 2 ** 31)}
        if _int("limit", 0, 1000):
            params["limit"] = _int("limit", 0, 1000)
        return _json_from_desk("/api/tasks/%s/journal" % jid, params, "journal")

    # ── 4. media ────────────────────────────────────────────────────────────
    @app.get("/tv/v1/media")
    def pc_media():
        denied = _guard("media")
        if denied is not None:
            return denied
        params = {k: request.args[k][:200] for k in _MEDIA_ARGS if request.args.get(k)}
        if "limit" in params:
            params["limit"] = _int("limit", 100, 500) or 100
        return _json_from_desk("/api/media", params, "media")

    @app.get("/tv/v1/media/status")
    def pc_media_status():
        denied = _guard("media")
        if denied is not None:
            return denied
        return _json_from_desk("/api/media/status", None, "media_status")

    @app.get("/tv/v1/media/<mid>")
    def pc_media_card(mid):
        denied = _guard("media")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(mid):
            return _refuse(400, "not a media id", code="bad_id")
        return _json_from_desk("/api/media/%s" % mid, None, "media_card")

    @app.get("/tv/v1/media/<mid>/preview")
    def pc_media_preview(mid):
        denied = _guard("media")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(mid):
            return _refuse(400, "not a media id", code="bad_id")
        try:
            status, body, ctype = _desk("/api/media/%s/preview" % mid)
        except Exception as e:
            _log.warning("pc_read preview failed: %s", type(e).__name__)
            return _refuse(503, "the PC could not make that preview right now", code="pc_unavailable")
        if status != 200 or not ctype.lower().startswith("image/"):
            return _refuse(404 if status == 404 else 503, "no preview for that item", code="no_preview")
        if len(body) > MAX_PREVIEW:
            return _refuse(413, "that preview is too big for a phone", code="too_big")
        return Response(body, mimetype=ctype.split(";")[0])


def _ws_entry(w: dict) -> dict:
    out = {k: w[k] for k in _WS_FIELDS if k in w}
    out["boundary"] = (w.get("boundary") or {}).get("kind", "native")
    return out
