"""What every workspace adapter shares (PW-G2): the three ways a read can fail, and one in-process GET to the desktop.

An adapter is a module with SECTIONS (the section ids it serves), FACETS ({facet name: function of a row}), and
pages(sensitivity), facets(rows), rev(rows), search(q, sensitivity), item(ref, sensitivity, offset, limit), each
raising Missing, Locked or Unavailable and nothing else the phone could read. tv_gateway/ws_content.py turns those
three into 404, 423 and 503. tests/unit/test_tv_ws_content.py holds the mapping.
"""
from __future__ import annotations

import json
from typing import Optional

from agent_friday.tv_gateway import pc_read


class Missing(Exception):
    """No such thing here."""


class Locked(Exception):
    """It is encrypted and the PC's vault is locked."""


class Unavailable(Exception):
    """The PC could not read it."""


def desk_json(path: str, params: Optional[dict] = None) -> dict:
    """One GET to this PC's own app, in process, answered as a JSON object."""
    status, body, _ = pc_read._desk(path, params)
    if status == 404:
        raise Missing(path)
    if status != 200:
        raise Unavailable(path)
    try:
        data = json.loads(body)
    except ValueError as e:
        raise Unavailable(path) from e
    if not isinstance(data, dict):
        raise Unavailable(path)
    return data
