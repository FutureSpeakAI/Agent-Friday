"""Friday's workspaces as the page shows them: one list, read on both sides.

static/workspace_registry.js is the single source of each workspace's id,
name, icon, accent token, one-line description and the other words people use
for it. The page loads that file; this module parses the same bytes (the
strict JSON between its BEGIN and END markers), so the navigate tools, voice
and Friday's own prompts name a workspace exactly as the dock does.

A name here is also what people say out loud, so the file keeps each label
distinct and each alias unique; tests hold that.

A workspace marked `held` names a switch in settings.held_features. While
that switch is off, the workspace is not one Friday can name or reach: it is
missing from workspaces(), ids(), labels(), aliases(), resolve(), tool_list()
and spoken_list(). get() and all_workspaces() still see it, so its own code
and data keep their names.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

_BEGIN, _END = "/*BEGIN JSON*/", "/*END JSON*/"
_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "data": None}


def registry_path() -> Path | None:
    """The file the server serves as /static/workspace_registry.js (it serves
    static/ from its working directory), or the one in this checkout."""
    for p in (Path.cwd() / "static" / "workspace_registry.js",
              Path(__file__).resolve().parents[3] / "static" / "workspace_registry.js"):
        if p.is_file():
            return p
    return None


def parse(text: str) -> dict:
    """The registry from the file's text."""
    start, end = text.index(_BEGIN) + len(_BEGIN), text.index(_END)
    data = json.loads(text[start:end])
    data.setdefault("groups", [])
    for w in data.get("workspaces") or []:
        w.setdefault("aliases", [])
        w.setdefault("core", False)
        w.setdefault("tab", True)
    return data


def registry() -> dict:
    """The parsed registry, re-read when the file changes."""
    p = registry_path()
    if p is None:
        return {"groups": [], "workspaces": []}
    key = (str(p), p.stat().st_mtime_ns)
    with _LOCK:
        if _CACHE["key"] != key:
            _CACHE.update(key=key, data=parse(p.read_text(encoding="utf-8")))
        return _CACHE["data"]


def all_workspaces() -> list[dict]:
    """Every workspace in the file, held or not."""
    return list(registry().get("workspaces") or [])


def is_held(ws: dict | str | None) -> bool:
    """True while the workspace's held switch is off."""
    w = get(ws) if isinstance(ws, str) else ws
    feature = (w or {}).get("held")
    if not feature:
        return False
    from agent_friday.services import held_features
    return not held_features.enabled(feature)


def workspaces() -> list[dict]:
    """The workspaces Friday can offer now: every one but those held."""
    return [w for w in all_workspaces() if not is_held(w)]


def ids() -> list[str]:
    return [w["id"] for w in workspaces()]


def get(ws_id: str) -> dict | None:
    for w in all_workspaces():
        if w["id"] == ws_id:
            return w
    return None


def label(ws_id: str) -> str:
    """The workspace's name, as the dock shows it and as Friday says it."""
    w = get(ws_id)
    return w["label"] if w else str(ws_id or "").replace("_", " ").title()


def labels(include_held: bool = False) -> dict[str, str]:
    ws = all_workspaces() if include_held else workspaces()
    return {w["id"]: w["label"] for w in ws}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def aliases(include_held: bool = False) -> dict[str, str]:
    """Every word that names a workspace -> its id: the id, the label, and
    the listed aliases. A caller that keeps the table across a change of the
    held switch builds it with include_held=True and asks is_held() per hit."""
    out: dict[str, str] = {}
    for w in (all_workspaces() if include_held else workspaces()):
        for word in [w["id"], w["label"]] + list(w.get("aliases") or []):
            out.setdefault(_norm(word), w["id"])
    return out


def resolve(name: str) -> str | None:
    """The workspace a word names, or None. Exact words only: a spoken name
    that almost matches two workspaces must not quietly pick one."""
    n = _norm(name)
    if not n:
        return None
    table = aliases()
    if n in table:
        return table[n]
    for prefix in ("the ", "my "):
        if n.startswith(prefix) and n[len(prefix):] in table:
            return table[n[len(prefix):]]
    for suffix in (" workspace", " window", " tab", " app"):
        if n.endswith(suffix) and n[: -len(suffix)] in table:
            return table[n[: -len(suffix)]]
    return None


def tool_list() -> str:
    """The workspaces as a tool description names them: each id, with its
    name beside it where the two differ ("contacts (People)")."""
    return ", ".join(w["id"] if w["label"].lower() == w["id"] else "%s (%s)" % (w["id"], w["label"])
                     for w in workspaces())


def spoken_list() -> str:
    """The workspaces as a speakable list: 'News, Messages, ... and Settings'."""
    names = [w["label"] for w in workspaces()]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)
