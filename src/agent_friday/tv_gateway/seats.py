"""GET and POST /tv/v1/seats: list the seats (models) a paired device may use, and set the current one.

Stephen, 2026-11-13: "I want model switching on phone and tablet and PC as well. Fast and easy."
The desktop's top bar already has the switcher (the "Model selection" pill and its Quick Switch
list, the same one this endpoint mirrors); this is the paired devices' half of it.

* An OWNER device (a phone, tablet or watch the PC bound to the owner) lists every seat the owner
  can use right now, the one in use marked, and sets the current one. The list is the pill's own
  (the catalogue's orchestrator seats that are available) and the set is the pill's own save
  (POST /api/settings, run in process with this PC's session token), so the seat gate, the
  local-only guard and every other check on a pick apply unchanged.
* Anyone else (the TV, the family member, an unbound device) sees only the seats the owner
  allowed that member (household ``model_defaults.allowed_seats``, nothing until the owner sets it),
  and cannot switch: the model a family turn uses is the owner's call.
* A device never names a member; a ``member`` field or query is refused (400), as inherit does.
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, Response, g, jsonify, request

from agent_friday.tv_gateway import household, owner_turn

_log = logging.getLogger(__name__)
PATH = "/tv/v1/seats"
_NAMING = ("member", "member_id", "principal", "user")


def _refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp


def _catalogue() -> list:
    """The orchestrator seats usable right now: what the top bar's pill lists."""
    from agent_friday.services.model_catalog import build_catalog
    rows = (build_catalog(include_engines=False).get("roles") or {}).get("orchestrator") or []
    return [r for r in rows if isinstance(r, dict) and r.get("id") and r.get("available") is not False]


def _current() -> Optional[str]:
    from agent_friday.core import _load_settings
    return str((_load_settings() or {}).get("orchestrator_model") or "") or None


def _public(row: dict, current: Optional[str]) -> dict:
    return {"id": row["id"], "label": row.get("label") or row["id"], "short": row.get("short") or row.get("label") or row["id"],
            "provider": row.get("provider") or "", "provider_label": row.get("provider_label") or "",
            "local": bool(row.get("local")), "current": row["id"] == current}


def _allowed_for(dev: dict) -> list:
    """The seat ids the owner allowed this device's member; empty until the owner sets them."""
    member = household.member_for_device(str(dev.get("id")))
    bundle = household.bundle_for(member) if member else None
    allowed = ((bundle or {}).get("model_defaults") or {}).get("allowed_seats")
    return [str(a) for a in allowed] if isinstance(allowed, list) else []


def _save(model_id: str) -> tuple:
    """The pill's own save, in process: (ok, reason)."""
    from agent_friday import core
    r = core.app.test_client().post(
        "/api/settings", json={"settings": {"orchestrator_model": model_id}},
        headers={"X-Friday-Token": core._current_api_token()})
    try:
        d = r.get_json(silent=True) or {}
    except Exception:
        d = {}
    if r.status_code != 200 or d.get("status") == "error":
        return False, str(d.get("message") or d.get("error") or "the seat was not accepted")[:200]
    return True, ""


def register(app: Flask) -> None:
    @app.get(PATH)
    def list_seats():
        dev = getattr(g, "device", None)
        if not dev:
            return _refuse(401, "device session required")
        if any(a in request.args for a in _NAMING):
            return _refuse(400, "a device does not choose its member; the PC knows who it was signed in as")
        try:
            rows, current = _catalogue(), _current()
        except Exception as e:
            _log.info("seats not readable: %s", type(e).__name__)
            return _refuse(503, "this computer can't list its models right now")
        owner = owner_turn.is_owner_device(dev)
        if not owner:
            allowed = set(_allowed_for(dev))
            rows = [r for r in rows if r["id"] in allowed]
        return jsonify({"ok": True, "can_switch": owner, "current": current if owner else None,
                        "seats": [_public(r, current if owner else None) for r in rows]})

    @app.post(PATH)
    def set_seat():
        dev = getattr(g, "device", None)
        if not dev:
            return _refuse(401, "device session required")
        body = request.get_json(silent=True) or {}
        if any(a in body for a in _NAMING) or any(a in request.args for a in _NAMING):
            return _refuse(400, "a device does not choose its member; the PC knows who it was signed in as")
        if not owner_turn.is_owner_device(dev):
            return _refuse(403, "only the owner's own phone or tablet switches the model")
        wanted = str(body.get("model") or "").strip()
        if not wanted:
            return _refuse(400, "no model named")
        try:
            known = {r["id"] for r in _catalogue()}
        except Exception:
            return _refuse(503, "this computer can't list its models right now")
        if wanted not in known:
            return _refuse(404, "that is not a seat this computer can use right now")
        ok, why = _save(wanted)
        if not ok:
            return _refuse(409, why)
        from agent_friday.services import device_pairing
        device_pairing._receipt("device_seat", dev, model=wanted)
        return jsonify({"ok": True, "current": wanted})
