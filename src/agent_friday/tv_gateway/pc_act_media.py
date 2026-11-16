"""Changing media on the PC from the owner's phone or tablet (wider brief step 4, writes; ``pc_act``).

Who: an owner-bound phone or tablet that holds ``pc_act`` (the same guard as the job routes). What: five actions, each
one fixed desktop call, so the PC applies the checks of its own button:
* change a card by hand: only ``title``, ``tags``, ``favorite`` and ``project``, each type-checked (publishing goes
  through its own route and the desktop refuses it here);
* ``turn-into`` a kind (a lower-case word; the desktop knows which kinds exist) which starts a job;
* ``publish``: the desktop raises its approval card on the PC and nothing leaves the PC until it is pressed there;
  ``unpublish``;
* ``delete``: a file moves to Friday's recoverable trash; a post card is refused (the desktop deletes a post in the
  content store, which cannot be undone) and the phone is told to do that on the PC;
* ``restore`` an entry of that trash (the trash list is a ``pc_read`` read).
Answers have every host path left out. Each action writes a ``device_act`` receipt. Each rule is held by a test in
tests/unit/test_tv_pc_act_media.py.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify, request

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pc_act, pc_read, tailnet
from agent_friday.tv_gateway.pc_common import ID_PATTERN, hide_host_paths, refuse

_log = logging.getLogger(__name__)

TURN_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_TITLE, MAX_TAGS, MAX_TAG, MAX_PROJECT = 200, 20, 40, 100


def _desk_send(method: str, path: str, body: dict) -> Tuple[int, dict]:
    from agent_friday import core
    token = {"X-Friday-Token": core._current_api_token()}
    reply = core.app.test_client().open(path, method=method, json=body, headers=token)
    answer = reply.get_json(silent=True)
    return reply.status_code, answer if isinstance(answer, dict) else {}


def _is_tag(tag) -> bool:
    return isinstance(tag, str) and 0 < len(tag) <= MAX_TAG


def _checked_patch(body: dict) -> Optional[dict]:
    """The fields of a hand edit a device may send, each of the right type; None when there is nothing to change."""
    out = {}
    title, tags, favorite, project = body.get("title"), body.get("tags"), body.get("favorite"), body.get("project")
    if isinstance(title, str) and 0 < len(title.strip()) <= MAX_TITLE:
        out["title"] = title.strip()
    if isinstance(tags, list) and len(tags) <= MAX_TAGS and all(_is_tag(tag) for tag in tags):
        out["tags"] = tags
    if isinstance(favorite, bool):
        out["favorite"] = favorite
    if isinstance(project, str) and 0 < len(project) <= MAX_PROJECT:
        out["project"] = project
    return out or None


def _act(action: str, media_id: str, method: str, path: str, body: Optional[dict] = None) -> Response:
    denied = pc_act._guard()
    if denied is not None:
        return denied
    if not ID_PATTERN.match(media_id):
        return refuse(400, "not a media id", code="bad_id")
    try:
        status, answer = _desk_send(method, path, body or {})
    except Exception as e:
        _log.warning("pc_act media %s failed: %s", action, type(e).__name__)
        status, answer = 503, {}
    device_pairing._receipt("device_act", g.device, right=pc_act.RIGHT, action=action, media=media_id, outcome=status,
                            via=tailnet.via())
    if status >= 500:
        return refuse(503, "the PC could not do that right now", code="pc_unavailable")
    shaped = jsonify(hide_host_paths(answer))
    shaped.status_code = status
    return shaped


def _is_post_card(media_id: str) -> Optional[bool]:
    """True for a post card, False for anything else, None when the desktop does not know the card."""
    import json
    status, body, _ = pc_read._desk("/api/media/%s" % media_id)
    if status == 404:
        return None
    try:
        return (json.loads(body).get("card") or {}).get("source_kind") == "post"
    except ValueError:
        return True


def register(app: Flask) -> None:
    @app.patch("/tv/v1/media/<mid>")
    def pc_media_patch(mid):
        changes = _checked_patch(request.get_json(silent=True) or {})
        if changes is None:
            denied = pc_act._guard()
            return denied if denied is not None else refuse(
                400, "send title, tags, favorite or project", code="bad_request")
        return _act("media_patch", mid, "PATCH", "/api/media/%s" % mid, changes)

    @app.post("/tv/v1/media/<mid>/turn-into")
    def pc_media_turn_into(mid):
        kind = (request.get_json(silent=True) or {}).get("kind")
        if not (isinstance(kind, str) and TURN_KIND.match(kind)):
            denied = pc_act._guard()
            return denied if denied is not None else refuse(400, "send {kind}", code="bad_request")
        return _act("media_turn_into", mid, "POST", "/api/media/%s/turn-into" % mid, {"kind": kind})

    @app.post("/tv/v1/media/<mid>/publish")
    def pc_media_publish(mid):
        return _act("media_publish", mid, "POST", "/api/media/%s/publish" % mid)

    @app.post("/tv/v1/media/<mid>/unpublish")
    def pc_media_unpublish(mid):
        return _act("media_unpublish", mid, "POST", "/api/media/%s/unpublish" % mid)

    @app.delete("/tv/v1/media/<mid>")
    def pc_media_delete(mid):
        denied = pc_act._guard()
        if denied is not None:
            return denied
        if not ID_PATTERN.match(mid):
            return refuse(400, "not a media id", code="bad_id")
        try:
            is_post = _is_post_card(mid)
        except Exception as e:
            _log.warning("pc_act media delete check failed: %s", type(e).__name__)
            return refuse(503, "the PC could not do that right now", code="pc_unavailable")
        if is_post is None:
            return refuse(404, "this PC has no media card %r" % mid, code="unknown_media")
        if is_post:
            return refuse(409, "a post is deleted on the PC, where it cannot be undone", code="delete_on_pc")
        return _act("media_delete", mid, "DELETE", "/api/media/%s" % mid)

    @app.get("/tv/v1/media/trash")
    def pc_media_trash():
        denied = pc_read._guard("media")
        if denied is not None:
            return denied
        return pc_read._json_from_desk("/api/media/trash", None, "media_trash", shape=hide_host_paths)

    @app.post("/tv/v1/media/trash/<entry>/restore")
    def pc_media_restore(entry):
        return _act("media_restore", entry, "POST", "/api/media/trash/%s/restore" % entry)
