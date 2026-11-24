"""One live line per workspace for the owner's phone list (UI-CRITIC Q6; contract draft 3, section 4 addition).

    GET /tv/v1/workspaces?live=1   each row may carry live: {text, as_of}

The line is written here, on the PC, from the rows the workspace's own section already serves to the phone
(tv_gateway/ws_content.py): the same adapter, the same device block, the same sensitivity, behind the same read right
the list route checks. Nothing here reads anything the phone could not already open. A row holding a URL never
becomes a line (the broker's rule); the text is one plain line of at most TEXT_MAX characters. A workspace with
nothing to say, or whose section can't be read, has no line. Each line is kept TTL_S seconds with the time it was
true, so a list read does not re-read every workspace. tests/unit/test_tv_ws_summary.py holds the rules.
"""
from __future__ import annotations

import logging
import threading
import time
import unicodedata
from datetime import datetime, timezone
from typing import Callable, Optional

_log = logging.getLogger(__name__)

TEXT_MAX = 80
TTL_S = 60
_clock_real = time.time
_clock = time.time

_kept: dict[str, tuple[float, Optional[dict]]] = {}
_lock = threading.Lock()


def _plain(text) -> str:
    """One line: no control or format characters (no bidi overrides), single spaces, cut with an ellipsis."""
    s = "".join(c if not unicodedata.category(c).startswith("C") else " " for c in str(text or ""))
    s = " ".join(s.split())
    return s if len(s) <= TEXT_MAX else s[:TEXT_MAX - 1].rstrip() + "…"


def _newest_title(rows: list[dict]) -> Optional[str]:
    return rows[0].get("title") if rows else None


def _next_event(rows: list[dict]) -> Optional[str]:
    now = datetime.fromtimestamp(_clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nxt = next((r for r in rows if (r.get("when") or "") >= now), None)
    if nxt is None:
        return None
    return " · ".join(p for p in (nxt.get("title"), nxt.get("line")) if p)


def _count(noun: str) -> Callable[[list[dict]], Optional[str]]:
    return lambda rows: ("%d %s%s" % (len(rows), noun, "" if len(rows) == 1 else "s")) if rows else None


# workspace -> (the section it speaks from, how its rows become one line). A workspace not here has no line.
SAYS: dict[str, tuple[str, Callable[[list[dict]], Optional[str]]]] = {
    "news": ("latest", _newest_title),
    "calendar": ("agenda", _next_event),
    "knowledge": ("pages", _count("page")),
}


def _compute(ws: str, now: float) -> Optional[dict]:
    from agent_friday.services import a2ui_profile as prof
    from agent_friday.tv_gateway import ws_content
    said = SAYS.get(ws)
    served = ws_content._serving(ws) if said else None
    if served is None or said[0] not in served[0]:
        return None
    section, say = said
    try:
        rows = served[0][section].pages(served[1]["sensitivity"])
        raw = say(rows)
    except Exception as e:                                  # one workspace being down never takes the list down
        _log.info("workspace line for %s unreadable: %s", ws, type(e).__name__)
        return None
    if not raw or prof.has_url(str(raw)):
        return None
    text = _plain(raw)
    if not text:
        return None
    return {"text": text, "as_of": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def line(ws: str) -> Optional[dict]:
    """{text, as_of} for this workspace, or None. Kept TTL_S seconds; as_of is when it was read."""
    now = _clock()
    with _lock:
        hit = _kept.get(ws)
    if hit is not None and now - hit[0] < TTL_S:
        return hit[1]
    got = _compute(ws, now)
    with _lock:
        _kept[ws] = (now, got)
    return got


def forget() -> None:
    with _lock:
        _kept.clear()
