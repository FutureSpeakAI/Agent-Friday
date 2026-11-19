"""What the PC doors for the owner's phone and tablet share: who counts, and the shape of a refusal."""
from __future__ import annotations

import re
from typing import Optional

from flask import Response, jsonify

OWNER_KINDS = ("phone", "tablet")
# A chat, job or media id: never starts with a dot, so no "..", no hidden names.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,95}$")


def allowed(dev: Optional[dict]) -> bool:
    """An owner-bound phone or tablet. Never a watch, a TV, a family device or an unbound one.
    Guarded by test_only_an_owner_phone_or_tablet_may_register_a_key (test_tv_pc_approve_key.py)."""
    from agent_friday.tv_gateway import owner_turn
    return bool(dev) and owner_turn.is_owner_device(dev) and str(dev.get("kind") or "").lower() in OWNER_KINDS


def refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp
