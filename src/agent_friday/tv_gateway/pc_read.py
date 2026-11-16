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

import json
import logging
import re
import threading
import time
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify, request

_log = logging.getLogger(__name__)

RIGHT = "pc_read"
OWNER_KINDS = ("phone", "tablet")                      # never the watch, never the TV
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,95}$")      # never starts with a dot: no "..", no hidden names
MAX_JSON = 4 * 1024 * 1024
MAX_PREVIEW = 3 * 1024 * 1024
AUDIT_EVERY_S = 3600.0
_MEDIA_ARGS = ("view", "q", "kind", "type", "project", "status", "tag", "source", "from", "to", "limit",
               "offset", "sort", "collection")
_AUDITED: dict = {}
_LOCK = threading.Lock()

# Fields of a workspace the phone needs to draw its entry; the rest of the registry stays on the PC.
_WS_FIELDS = ("id", "label", "group", "icon", "blurb", "description", "order", "kind", "voice_names")


def _refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp


def allowed(dev: Optional[dict]) -> bool:
    """An owner-bound phone or tablet (the household's binding, never a claim in the request)."""
    from agent_friday.tv_gateway import owner_turn
    return bool(dev) and owner_turn.is_owner_device(dev) and str(dev.get("kind") or "").lower() in OWNER_KINDS


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


CHATS_PAGE, CHATS_PAGE_MAX = 30, 100
MESSAGES_PAGE, MESSAGES_PAGE_MAX = 200, 500


def _unavailable(area: str, e: Optional[Exception] = None) -> Response:
    if e is not None:
        _log.warning("pc_read %s failed: %s", area, type(e).__name__)
    return _refuse(503, "the PC could not answer that right now", code="pc_unavailable")


def _stamped(resp: Response) -> Response:
    resp.headers["X-Friday-As-Of"] = str(int(time.time()))
    return resp


def _json_from_desk(path: str, params: Optional[dict] = None, area: str = "") -> Response:
    try:
        status, body, ctype = _desk(path, params)
    except Exception as e:
        return _unavailable(area, e)
    if len(body) > MAX_JSON:
        return _refuse(413, "that is too big to send to a phone; ask for a smaller page", code="too_big")
    if status >= 500:
        return _unavailable(area)
    return _stamped(Response(body, status=status, mimetype=ctype.split(";")[0] or "application/json"))


def _desk_object(path: str, params: dict, area: str, key: str) -> Tuple[Optional[dict], Optional[Response]]:
    """The desktop's answer as a dict holding a list under ``key``, or the response to send instead (a 503 when
    the desktop failed, its own answer when it refused, e.g. a 404 for a chat that is not there)."""
    try:
        status, body, ctype = _desk(path, params)
        data = json.loads(body) if status < 500 else None
    except Exception as e:
        return None, _unavailable(area, e)
    if status >= 500:
        return None, _unavailable(area)
    if status >= 300 or not isinstance(data, dict) or not isinstance(data.get(key), list):
        return None, Response(body, status=status, mimetype=ctype.split(";")[0] or "application/json")
    return data, None


def _page_out(payload: dict) -> Response:
    resp = jsonify(payload)
    if resp.content_length is not None and resp.content_length > MAX_JSON:
        return _refuse(413, "that is too big to send to a phone; ask for a smaller page", code="too_big")
    return _stamped(resp)


def _float(name: str) -> Optional[float]:
    try:
        return float(request.args.get(name)) if request.args.get(name) not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(name: str, default: int, hi: int) -> int:
    try:
        return max(0, min(hi, int(request.args.get(name, default) or default)))
    except (TypeError, ValueError):
        return default


def register(app: Flask) -> None:
    # ── 1. the chat list ────────────────────────────────────────────────────
    @app.get("/tv/v1/chats")
    def pc_chats():
        """The chat list, paged. ``q`` searches every chat's text (the desktop's own search); otherwise the
        list is newest first, ``limit`` (default 30, at most 100) from ``offset``, only chats active after
        ``since`` (epoch seconds) when that is given. The desktop sends its whole list and its projects in one
        answer; the phone gets one page and a ``next_offset``, never the lot.
        Held by tests/unit/test_tv_pc_chats.py (paging, since, search, owner-only forms)."""
        denied = _guard("chats")
        if denied is not None:
            return denied
        q = (request.args.get("q") or "").strip()[:200]
        if q:
            return _json_from_desk("/api/conversations/search", {"q": q}, "chats")
        params = {"archived": "1"} if request.args.get("archived") in ("1", "true", "yes") else {}
        data, other = _desk_object("/api/conversations", params, "chats", "conversations")
        if other is not None:
            return other
        rows = [c for c in data["conversations"] if isinstance(c, dict)]
        since = _float("since")
        if since is not None:
            rows = [c for c in rows if (c.get("last_active_at") or 0) > since]
        limit, offset = _int("limit", CHATS_PAGE, CHATS_PAGE_MAX) or CHATS_PAGE, _int("offset", 0, 1_000_000)
        nxt = offset + limit if offset + limit < len(rows) else None
        return _page_out({"status": "ok", "conversations": rows[offset:offset + limit], "total": len(rows),
                          "offset": offset, "next_offset": nxt, "main_id": data.get("main_id")})

    @app.get("/tv/v1/chats/<cid>")
    def pc_chat(cid):
        denied = _guard("chats")
        if denied is not None:
            return denied
        if not _ID.match(cid):
            return _refuse(400, "not a chat id", code="bad_id")
        return _json_from_desk("/api/conversations/%s" % cid, None, "chat")

    @app.get("/tv/v1/chats/<cid>/messages")
    def pc_chat_messages(cid):
        """A chat's messages: the last ``limit`` (default 200, at most 500), or with ``after=<message id>`` only
        the ones written after that message, which is the cheap live update (the phone polls with the last id
        it holds). With a cursor, ``has_more`` says there were more than ``limit`` (read again with the last
        id). An ``after`` id the chat does not hold gets ``reset: true`` and the last page, so a deleted message
        never strands the phone. Held by the cursor tests in tests/unit/test_tv_pc_chats.py."""
        denied = _guard("chats")
        if denied is not None:
            return denied
        if not _ID.match(cid):
            return _refuse(400, "not a chat id", code="bad_id")
        after = (request.args.get("after") or "").strip()
        if after and not _ID.match(after):
            return _refuse(400, "not a message id", code="bad_id")
        limit = _int("limit", MESSAGES_PAGE, MESSAGES_PAGE_MAX) or MESSAGES_PAGE
        data, other = _desk_object("/api/conversations/%s/messages" % cid, {} if after else {"limit": limit},
                                   "chat_messages", "messages")
        if other is not None:
            return other
        msgs = [m for m in data["messages"] if isinstance(m, dict)]
        if not after:
            return _page_out({"status": "ok", "messages": msgs[-limit:]})
        ids = [m.get("id") for m in msgs]
        if after not in ids:
            return _page_out({"status": "ok", "messages": msgs[-limit:], "has_more": False, "reset": True})
        newer = msgs[ids.index(after) + 1:]
        return _page_out({"status": "ok", "messages": newer[:limit], "has_more": len(newer) > limit})

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
        if not _ID.match(ws):
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
        if not _ID.match(jid):
            return _refuse(400, "not a job id", code="bad_id")
        return _json_from_desk("/api/tasks/%s" % jid, None, "job")

    @app.get("/tv/v1/jobs/<jid>/journal")
    def pc_job_journal(jid):
        denied = _guard("jobs")
        if denied is not None:
            return denied
        if not _ID.match(jid):
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
        if not _ID.match(mid):
            return _refuse(400, "not a media id", code="bad_id")
        return _json_from_desk("/api/media/%s" % mid, None, "media_card")

    @app.get("/tv/v1/media/<mid>/preview")
    def pc_media_preview(mid):
        denied = _guard("media")
        if denied is not None:
            return denied
        if not _ID.match(mid):
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
