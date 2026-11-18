"""Three small read-only answers the phone's Settings and avatar need from the PC (handoff:
HANDOFF-burndown-phone-settings-api.md sections 3 and 4; the shape picker's ``GET /tv/v1/evolution``). Same door as
every other owner read: an owner-bound phone or tablet that holds ``pc_read`` (tv_gateway/pc_read); nothing here writes.

* ``GET /tv/v1/evolution``: the PC's chosen avatar shape, from the desktop's own ``/api/evolution``, cut down to
  ``day``, ``structure``, ``structure_index``, ``calendar_index`` and ``preferred_scene_index`` (whole numbers and one
  short name). The desktop's GET stores the first-launch date the first time it is ever asked, and nothing else.
* ``GET /tv/v1/providers``: which model providers the PC has switched on: ``id``, ``label``, ``configured``,
  ``local``. Names and a yes or no only: no key, no last-4, no URL, no header, no environment variable name.
* ``GET /tv/v1/connectors``: ``id``, ``label``, ``kind`` (``mcp`` or ``connector``) and ``connected`` for every
  connector the desktop lists. Nothing about its fields, setup, secrets, tools or detail text crosses.
  ``device_control`` is false: the per-device on/off switch of the handoff is not built, so no row claims a device
  setting it does not have.

A desktop failure or an answer that is not plainly that shape is a 503, never a guess. Each rule is held by a test in
tests/unit/test_tv_pc_settings_read.py.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from flask import Flask, Response, jsonify

from agent_friday.tv_gateway import pc_read

_log = logging.getLogger(__name__)

MAX_ROWS = 200
_PLAIN = re.compile(r"^[^\x00-\x1f\x7f]{1,80}\Z")      # \Z, never $: a trailing newline slips past $
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,63}\Z")


def _plain(value) -> Optional[str]:
    """A short one-line text with no control characters and no host path, else None."""
    if not isinstance(value, str) or not _PLAIN.match(value):
        return None
    if _looks_like_a_host_path(value):
        return None
    return value


def _looks_like_a_host_path(text: str) -> bool:
    """True when the text looks like a file path or URL on this PC (a label must never carry one)."""
    return bool(re.search(r"[A-Za-z]:[\\/]|\\\\|(?:^|\s)/(?:Users|home|mnt|var|tmp|opt|etc|root|srv|data)/"
                          r"|(?:^|\s)~[\\/]|[A-Za-z]+://|@", text))


def _whole(value, low: int, high: int) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _scene_count() -> int:
    """How many shapes the PC has (the desktop bounds its own pin to this); 15 when it cannot be read."""
    try:
        from agent_friday.routes.insights import SCENE_NAMES
        return len(SCENE_NAMES)
    except Exception:
        return 15


def shape_evolution(data) -> Optional[dict]:
    """The avatar shape answer, or None when the desktop's answer is not plainly one. An index at or past the number of
    shapes the PC has would crash the phone's lookup, so it is refused here too."""
    if not isinstance(data, dict):
        return None
    top = _scene_count() - 1
    index = _whole(data.get("structure_index"), 0, top)
    calendar = _whole(data.get("calendar_index"), 0, top)
    day = _whole(data.get("day"), 1, 1_000_000)
    if index is None or calendar is None or day is None:
        return None
    pinned = data.get("preferred_scene_index")
    pinned = None if pinned is None else _whole(pinned, 0, top)
    structure = _plain(data.get("structure"))
    if structure is None:
        return None
    return {"ok": True, "day": day, "structure": structure, "structure_index": index, "calendar_index": calendar,
            "preferred_scene_index": pinned}


def shape_connectors(data) -> Optional[dict]:
    """The connector rows the phone may see, or None when the desktop's answer is not plainly a list of them."""
    rows = data.get("connectors") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return None
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue                                       # one odd row never hides the others
        key, name = row.get("key"), _plain(row.get("name"))
        if not (isinstance(key, str) and _ID.match(key)) or name is None or not isinstance(row.get("connected"), bool):
            continue
        out.append({"id": key, "label": name, "kind": "mcp" if row.get("kind") == "mcp" else "connector",
                    "connected": row["connected"]})
    return {"ok": True, "device_control": False, "connectors": out}


def providers_answer() -> Optional[dict]:
    """The enabled model providers by name, with whether each has what it needs and whether it runs on this PC."""
    from agent_friday.routing import provider_descriptors as pd
    from agent_friday.services import credential_store
    from agent_friday.services.provider_registry import get_provider_registry
    out = []
    for prov in get_provider_registry().list_providers():
        if not isinstance(prov, dict) or prov.get("enabled") is False:
            continue
        name = prov.get("name")
        label = _plain(prov.get("label")) or (name if isinstance(name, str) and _ID.match(name) else None)
        if not (isinstance(name, str) and _ID.match(name)) or label is None:
            continue
        auth = prov.get("auth") if isinstance(prov.get("auth"), dict) else {}
        if auth.get("type", "env_var") == "env_var":
            # Whether a key exists, never a use of it: provider_key_status only tests that the stored file decrypts
            # (it does not stamp the key as used the way a read does), and the environment is asked without keeping
            # what it returns.
            configured = credential_store.provider_key_status(name) == "connected" or bool(pd._env_key(prov))
        else:
            configured = True      # no key to hold (a local server); sign-in state is not read here
        out.append({"id": name, "label": label, "configured": configured,
                    "local": pd.classification_of(prov) == pd.CLASSIFICATION_LOCAL})
    return {"ok": True, "providers": out[:MAX_ROWS]}


def _from_desk(path: str, shaper, area: str) -> Response:
    try:
        status, body, _ = pc_read._desk(path)
        shaped = shaper(json.loads(body)) if status == 200 else None
    except Exception as error:
        _log.warning("pc_read %s failed: %s", area, type(error).__name__)
        shaped = None
    if shaped is None:
        return pc_read._refuse(503, "the PC could not answer that right now", code="pc_unavailable")
    return jsonify(shaped)


def register(app: Flask) -> None:
    @app.get("/tv/v1/evolution")
    def pc_evolution():
        denied = pc_read._guard("evolution")
        if denied is not None:
            return denied
        return _from_desk("/api/evolution", shape_evolution, "evolution")

    @app.get("/tv/v1/connectors")
    def pc_connectors():
        denied = pc_read._guard("connectors")
        if denied is not None:
            return denied
        return _from_desk("/api/connectors", shape_connectors, "connectors")

    @app.get("/tv/v1/providers")
    def pc_providers():
        denied = pc_read._guard("providers")
        if denied is not None:
            return denied
        try:
            answer = providers_answer()
        except Exception as error:
            _log.warning("pc_read providers failed: %s", type(error).__name__)
            answer = None
        if answer is None:
            return pc_read._refuse(503, "the PC could not answer that right now", code="pc_unavailable")
        return jsonify(answer)
