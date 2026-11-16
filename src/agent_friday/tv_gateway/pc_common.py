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


HOST_PATH_KEYS = frozenset({"path", "abs_path", "source_path", "folder", "dir", "directory", "root"})
_HOST_PATH_VALUE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|mnt|var|tmp|opt|etc|root)/)")


_HOST_PATH_IN_TEXT = re.compile(
    r"(?:file:///?)?"
    r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\|/(?:Users|home|mnt|var|tmp|opt|etc|root|Volumes|srv)/)"
    r"[^\s'\"<>)]*")
PATH_REPLACEMENT = "[a path on the PC]"


def _clean_text(text: str):
    """None for text that is only a host path (it is left out); otherwise the text with any host path inside it
    replaced. Guarded by test_a_path_inside_a_sentence_is_replaced."""
    if _HOST_PATH_VALUE.match(text):
        return None
    return _HOST_PATH_IN_TEXT.sub(PATH_REPLACEMENT, text)


def hide_host_paths(value):
    """A copy of a desktop answer with every host file path left out: any key that names one, text that is an
    absolute Windows, UNC or Unix path, and a path written inside a sentence (replaced). A phone never needs where
    a file lives on the PC. Guarded by test_no_host_path_reaches_the_phone in tests/unit/test_tv_pc_media.py."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in HOST_PATH_KEYS:
                continue
            cleaned = hide_host_paths(item)
            if cleaned is not None or item is None:
                out[key] = cleaned
        return out
    if isinstance(value, list):
        return [cleaned for cleaned in (hide_host_paths(item) for item in value) if cleaned is not None]
    if isinstance(value, str):
        return _clean_text(value)
    return value


def refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp
