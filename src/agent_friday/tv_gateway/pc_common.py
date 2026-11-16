"""What the PC doors for the owner's phone and tablet share: who counts, and the shape of a refusal."""
from __future__ import annotations

from typing import Optional

from flask import Response, jsonify

OWNER_KINDS = ("phone", "tablet")


def allowed(dev: Optional[dict]) -> bool:
    """An owner-bound phone or tablet. Never a watch, a TV, a family device or an unbound one."""
    from agent_friday.tv_gateway import owner_turn
    return bool(dev) and owner_turn.is_owner_device(dev) and str(dev.get("kind") or "").lower() in OWNER_KINDS


def refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp
