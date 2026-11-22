"""Chats run both ways through ONE store on this PC: a chat started on the owner's phone, tablet or watch is a real
PC conversation, in the desktop's own list, and every PC chat can be opened and continued from those devices.

* ``POST /tv/v1/chats {title?, project?}`` starts a chat through the desktop's own create route (the same one its
  "+ New chat" calls), optionally inside a project, and returns its id. A device never holds a chat of its own:
  the id it continues is always one this PC made. Titled "New chat" unless the device sends a title, so the
  desktop's first-message titling names it like any other chat.
* Who: an owner-bound phone or tablet holding ``pc_read`` (the same door as reading the list; a watch reaches it
  through the phone). The body is data: at most ``MAX_BODY`` bytes, a fixed shape, a project id checked against
  ID_PATTERN and against the desktop's own project list before it is used.

Held by tests/unit/test_tv_chats_both_ways.py.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify, request

from agent_friday.tv_gateway import pc_read
from agent_friday.tv_gateway.pc_common import ID_PATTERN, refuse as _refuse

_log = logging.getLogger(__name__)

MAX_BODY = 4 * 1024
TITLE_MAX = 120
NEW_TITLE = "New chat"


def _desk_post(path: str, body: dict) -> Tuple[int, dict]:
    """One POST to this PC's own app, in process, with its own token. ``path`` is always built here."""
    from agent_friday import core
    r = core.app.test_client().post(path, json=body, headers={"X-Friday-Token": core._current_api_token()})
    data = r.get_json(silent=True)
    return r.status_code, data if isinstance(data, dict) else {}


def _body() -> Tuple[Optional[dict], Optional[Response]]:
    raw = request.stream.read(MAX_BODY + 1)          # never trusts Content-Length
    if len(raw) > MAX_BODY:
        return None, _refuse(413, "that is too much to start a chat with", code="too_big")
    if not raw.strip():
        return {}, None
    try:
        data = json.loads(raw)
    except ValueError:
        return None, _refuse(400, "that is not a chat to start", code="bad_request")
    if not isinstance(data, dict):
        return None, _refuse(400, "that is not a chat to start", code="bad_request")
    return data, None


def start_chat(title: str = "", project: Optional[str] = None) -> Tuple[int, dict]:
    """Create one PC chat through the desktop's own route. (status, answer) as the device should see it."""
    body = {"title": " ".join(str(title or "").split())[:TITLE_MAX] or NEW_TITLE}
    if project:
        body["project"] = project
    try:
        status, data = _desk_post("/api/conversations", body)
    except Exception as e:
        _log.warning("pc_chats start failed: %s", type(e).__name__)
        return 503, {"ok": False, "error": "the PC could not start a chat right now", "code": "pc_unavailable"}
    conv = data.get("conversation") if isinstance(data.get("conversation"), dict) else None
    if status >= 300 or not conv or not conv.get("id"):
        return 503, {"ok": False, "error": "the PC could not start a chat right now", "code": "pc_unavailable"}
    return 201, {"ok": True, "conversation_id": conv["id"], "conversation": conv}


def _project_exists(pid: str) -> Optional[bool]:
    """True/False from the desktop's own project route; None when it could not say."""
    try:
        status, _, _ = pc_read._desk("/api/projects/%s" % pid)
    except Exception:
        return None
    if status == 404:
        return False
    return True if status == 200 else None


def register(app: Flask) -> None:
    @app.post("/tv/v1/chats")
    def pc_chat_start():
        denied = pc_read._guard("chats")
        if denied is not None:
            return denied
        data, bad = _body()
        if bad is not None:
            return bad
        title = data.get("title")
        project = data.get("project")
        if title is not None and not isinstance(title, str):
            return _refuse(400, "a chat title is words", code="bad_request")
        if project is not None:
            if not isinstance(project, str) or not ID_PATTERN.match(project):
                return _refuse(400, "not a project id", code="bad_id")
            known = _project_exists(project)
            if known is None:
                return _refuse(503, "the PC could not check that project right now", code="pc_unavailable")
            if not known:
                return _refuse(404, "this PC has no such project", code="unknown_project")
        status, answer = start_chat(title or "", project)
        if status == 201:
            from agent_friday.services import device_pairing
            dev = getattr(g, "device", None) or {}
            device_pairing._receipt("device_chat_started", dev, conversation_id=answer["conversation_id"],
                                    in_project=bool(project))
            from agent_friday.tv_gateway import owner_turn
            owner_turn.announce_changed(answer["conversation_id"])     # the desktop's list shows it at once
        resp = jsonify(answer)
        resp.status_code = status
        return resp
