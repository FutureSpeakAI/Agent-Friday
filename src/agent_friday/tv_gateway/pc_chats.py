"""Chats run both ways through ONE store on this PC: a chat started on the owner's phone, tablet or watch is a real
PC conversation, in the desktop's own list, and every PC chat can be opened and continued from those devices.

* ``POST /tv/v1/chats {project?}`` starts a chat through the desktop's own create route (the same one its
  "+ New chat" calls), optionally inside a project, and returns its id. A device never holds a chat of its own:
  the id it continues is always one this PC made. Always titled "New chat", so the desktop's first-message titling
  names it from what was said, like any other chat; a device never writes words into the desktop's sidebar.
* Who: the same as an owner turn (an owner-bound phone or tablet, pc_common.allowed), because starting an empty chat
  is what the owner's first turn already does (owner_turn.bind_seat); it is a write, so it is NOT behind ``pc_read``,
  whose card promises the device cannot change anything. Starting one INSIDE a project also needs ``pc_read``: whether
  a project id exists is project knowledge. The body is data: at most ``MAX_BODY`` bytes, a fixed shape, a project id
  checked against ID_PATTERN and against the desktop's own project list before it is used. The answer is the new id
  only. At most ``STARTS_PER_WINDOW`` starts per device in ``WINDOW_S`` seconds, so no device floods the sidebar.

* ``GET /tv/v1/projects`` and ``GET /tv/v1/projects/<id>``: the desktop's projects as its sidebar shows them, a project's
  chats (rows like /tv/v1/chats) and its files by name and size. Read from the desktop's own /api/projects routes; no
  device keeps a project list of its own. Same door (owner phone or tablet with ``pc_read``).

Held by tests/unit/test_tv_chats_both_ways.py and tests/unit/test_tv_projects.py.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from flask import Flask, Response, g, jsonify, request

from agent_friday.tv_gateway import pc_read
from agent_friday.tv_gateway.pc_act import _desk_post
from agent_friday.tv_gateway.pc_common import ID_PATTERN, allowed, refuse as _refuse

_log = logging.getLogger(__name__)

MAX_BODY = 4 * 1024
NEW_TITLE = "New chat"
STARTS_PER_WINDOW = 10
WINDOW_S = 600.0
_starts: Dict[str, List[float]] = {}
_starts_lock = threading.Lock()


def _may_start(device_id: str) -> Optional[float]:
    """Hold one start slot for this device, or None when it already made STARTS_PER_WINDOW in WINDOW_S. A start
    that fails gives its slot back (_give_back); only chats made count (test_a_device_cannot_flood_the_desktops_list,
    test_a_failed_start_does_not_use_up_the_devices_starts)."""
    now = time.monotonic()
    with _starts_lock:
        recent = [t for t in _starts.get(device_id, []) if now - t < WINDOW_S]
        if len(recent) >= STARTS_PER_WINDOW:
            _starts[device_id] = recent
            return None
        _starts[device_id] = recent + [now]
        return now


def _give_back(device_id: str, slot: float) -> None:
    with _starts_lock:
        held = _starts.get(device_id, [])
        if slot in held:
            held.remove(slot)


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


def start_chat(project: Optional[str] = None) -> Tuple[int, dict]:
    """Create one PC chat through the desktop's own route. (status, answer) as the device should see it."""
    body = {"title": NEW_TITLE}
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
    return 201, {"ok": True, "conversation_id": conv["id"]}


def _project_exists(pid: str) -> Optional[bool]:
    """True/False from the desktop's own project route; None when it could not say."""
    try:
        status, _, _ = pc_read._desk("/api/projects/%s" % pid)
    except Exception:
        return None
    if status == 404:
        return False
    return True if status == 200 else None


PROJECT_FIELDS = ("id", "name", "type", "color", "conversations", "files", "created_at", "updated_at", "archived")
INSTRUCTIONS_MAX = 2000
FILE_FIELDS = ("name", "bytes", "added_at")


def _project_row(p: dict) -> dict:
    """A project as the desktop sidebar summarises it, without its seat or codebases (the PC's business)."""
    return {k: p[k] for k in PROJECT_FIELDS if k in p}


def _desk_json(path: str, area: str) -> Tuple[Optional[dict], Optional[Response]]:
    try:
        status, body, _ = pc_read._desk(path)
    except Exception as e:
        _log.warning("pc_chats %s failed: %s", area, type(e).__name__)
        return None, _refuse(503, "the PC could not answer that right now", code="pc_unavailable")
    if status == 404:
        return None, _refuse(404, "this PC has no such project", code="unknown_project")
    try:
        data = json.loads(body) if status == 200 else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, _refuse(503, "the PC could not answer that right now", code="pc_unavailable")
    return data, None


def register(app: Flask) -> None:
    @app.get("/tv/v1/projects")
    def pc_projects():
        """The desktop's projects, as its sidebar lists them (GET /api/projects). Held by test_tv_projects.py."""
        denied = pc_read._guard("projects")
        if denied is not None:
            return denied
        data, bad = _desk_json("/api/projects", "projects")
        if bad is not None:
            return bad
        rows = [_project_row(p) for p in data.get("projects") or [] if isinstance(p, dict) and p.get("id")]
        return pc_read._page_out({"ok": True, "projects": rows[:pc_read.CHATS_PAGE_MAX], "total": len(rows)})

    @app.get("/tv/v1/projects/<pid>")
    def pc_project(pid):
        """One project: the desktop's own record, its chats (rows like /tv/v1/chats, newest first) and its files by name and
        size. Files are listed, never their host paths. Held by test_tv_projects.py."""
        denied = pc_read._guard("projects")
        if denied is not None:
            return denied
        if not ID_PATTERN.match(pid):
            return _refuse(400, "not a project id", code="bad_id")
        data, bad = _desk_json("/api/projects/%s" % pid, "project")
        if bad is not None:
            return bad
        proj = data.get("project") if isinstance(data.get("project"), dict) else {}
        files, bad = _desk_json("/api/projects/%s/files" % pid, "project files")
        if bad is not None:
            return bad                     # unknown files are a refusal, never an empty list that reads as "none"
        names = {pid: proj.get("name")}
        chats = [pc_read._chat_row(c, names) for c in (data.get("conversations") or [])[:pc_read.CHATS_PAGE_MAX]
                 if isinstance(c, dict) and c.get("id")]
        out = {"ok": True, "project": dict(_project_row(proj),
                                           instructions=str(proj.get("instructions") or "")[:INSTRUCTIONS_MAX]),
               "conversations": chats,
               "files": [{k: f[k] for k in FILE_FIELDS if k in f}
                         for f in ((files or {}).get("files") or []) if isinstance(f, dict) and f.get("name")]}
        return pc_read._page_out(pc_read.hide_host_paths(out))

    @app.post("/tv/v1/chats")
    def pc_chat_start():
        """Held by test_starting_a_chat_is_an_owner_turns_power_not_a_readers."""
        if not allowed(getattr(g, "device", None)):
            return _refuse(403, "this device cannot start chats on the PC", code="not_for_this_device")
        data, bad = _body()
        if bad is not None:
            return bad
        if set(data) - {"project"}:
            return _refuse(400, "a new chat takes a project and nothing else", code="bad_request")
        project = data.get("project")
        if project is not None:
            if not isinstance(project, str) or not ID_PATTERN.match(project):
                return _refuse(400, "not a project id", code="bad_id")
            denied = pc_read._guard("projects")
            if denied is not None:
                return denied
            known = _project_exists(project)
            if known is None:
                return _refuse(503, "the PC could not check that project right now", code="pc_unavailable")
            if not known:
                return _refuse(404, "this PC has no such project", code="unknown_project")
        dev = getattr(g, "device", None) or {}
        dev_id = str(dev.get("id") or "")
        slot = _may_start(dev_id)
        if slot is None:
            return _refuse(429, "that is a lot of new chats; try again in a few minutes", code="too_many")
        status, answer = start_chat(project)
        if status != 201:
            _give_back(dev_id, slot)
        if status == 201:
            from agent_friday.services import device_pairing
            device_pairing._receipt("device_chat_started", dev, conversation_id=answer["conversation_id"],
                                    in_project=bool(project))
            from agent_friday.tv_gateway import owner_turn
            owner_turn.announce_changed(answer["conversation_id"])     # the desktop's list shows it at once
        resp = jsonify(answer)
        resp.status_code = status
        return resp
