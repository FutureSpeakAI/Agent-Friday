"""GET /tv/v1/inherit: the desktop's half of "pair a fresh device -> zero re-entry" (R3, G4 skeleton).

A device that has paired and opened a session asks once and receives its member's whole setup:
profile, settings, model defaults, chats, arcade (favourites, saves) and creations, plus the shared
household space. Which member that is was decided by the owner when the device was bound
(``household.bind_device``); the device never names one, so a tablet signed in as Janet cannot ask
for Stephen's data (a ``member`` query is refused outright, 400).

Keys: nothing is sent. The reply says ``keys: {"mode": "proxy", "copied": []}``: the device reaches
the PC's keys through the PC. The optional, consented end-to-end encrypted copy is a later slice.

``register(app)`` adds the route to the gateway app. The session gate in ``app.py`` already covers
every path that is not open, so ``g.device`` is set here; it is checked again anyway.
"""
from __future__ import annotations

from flask import Flask, Response, g, jsonify, request

from agent_friday.tv_gateway import household

PATH = "/tv/v1/inherit"
_NAMING_ARGS = ("member", "member_id", "principal", "user")


def _refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp


def register(app: Flask) -> None:
    @app.get(PATH)
    def inherit_bundle():
        dev = getattr(g, "device", None)
        if not dev:
            return _refuse(401, "device session required")
        if any(a in request.args for a in _NAMING_ARGS):
            return _refuse(400, "a device does not choose its member; the PC knows who it was signed in as")
        member_id = household.member_for_device(dev["id"])
        bundle = household.bundle_for(member_id) if member_id else None
        if bundle is None:
            return _refuse(409, "this device has not been signed in as a household member yet",
                           needs_member=True)
        return jsonify({"ok": True, **bundle, "keys": {"mode": "proxy", "copied": []}})
