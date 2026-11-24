"""The FutureSpeak (Sites) workspace's projects, read for a phone (PW-G2, adapter 9; A2UI slice 2).

Reads the desktop's own /api/futurespeak/projects (and /api/futurespeak/project/<name>) in process, GET only. A project
reaches the phone as a row (its name, how its deploy stands, its branch and how many changes are uncommitted, when it
last changed) and an item (those again, as facts). Its folder path, its site and Replit addresses, its description and
its commit messages never leave the PC: the phone learns how a project stands, not what is in it. Pipeline, revenue and
legal are not read here at all. A name that looks like an address (a project named after its site) is left out, as
the broker leaves out such rows. If the desktop's project list is unreadable as a file it answers with its default
list (and seeds the file), so the phone sees that list, not a 503; a failed or malformed answer is a 503.
tests/unit/test_tv_ws_futurespeak.py holds the rules.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from agent_friday.services.a2ui_profile import has_url
from agent_friday.tv_gateway import ws_blocks
from agent_friday.tv_gateway.ws_adapter import Missing, Unavailable, desk_json, digest as _digest

PREFIX = "project:"
KIND = "project"
#: The desktop's deploy states, and the words the phone says for them. Any other state is no state.
STATE_WORDS = {"green": "live", "yellow": "changes waiting", "red": "failing", "remote": "not on this PC"}
FACETS = {"deploy": lambda row: row["state"] or ""}
#: A plain project name: a letter or digit first, no space last, spaces only inside; 80 characters at most.
REF = re.compile(r"project:(?P<name>[A-Za-z0-9](?:[A-Za-z0-9 ._\-]{0,78}[A-Za-z0-9._\-])?)")


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _when(stamp) -> Optional[str]:
    """An ISO time with its offset, as UTC with a Z; anything else is no time."""
    if not isinstance(stamp, str) or len(stamp) > 40:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(project: dict, sensitivity: str) -> Optional[dict]:
    name = str(project.get("name") or "")
    if not REF.fullmatch(PREFIX + name) or has_url(name):
        return None
    status = project.get("status") if isinstance(project.get("status"), dict) else {}
    state = status.get("deploy_status") if status.get("deploy_status") in STATE_WORDS else None
    uncommitted = _count(status.get("uncommitted"))
    parts = (STATE_WORDS.get(state, ""), ws_blocks.line(status.get("branch"), 80),
             "%d uncommitted" % uncommitted if uncommitted else "")
    return {"ref": PREFIX + name, "kind": KIND, "title": name, "line": " · ".join(p for p in parts if p),
            "state": state, "when": _when(status.get("last_commit_date")), "thumb": None, "lives_on": "pc",
            "sensitivity": sensitivity, "taint": None}


def _projects() -> list[dict]:
    raw = desk_json("/api/futurespeak/projects").get("projects")
    if not isinstance(raw, list):
        raise Unavailable("futurespeak")
    return [p for p in raw if isinstance(p, dict)]


def pages(sensitivity: str) -> list[dict]:
    """His projects, by name."""
    rows = [r for r in (_row(p, sensitivity) for p in _projects()) if r]
    rows.sort(key=lambda r: (r["title"].lower(), r["ref"]))
    return rows


def facets(rows: list[dict]) -> list[dict]:
    counts: dict = {}
    for r in rows:
        name = FACETS["deploy"](r)
        counts[name] = counts.get(name, 0) + 1
    return [{"name": "deploy", "values": [{"v": k, "n": n} for k, n in sorted(counts.items())]}]


def rev(rows: list[dict]) -> str:
    return "p_%d_%s" % (len(rows), _digest(*[(r["ref"], r["line"], r["state"], r["when"]) for r in rows]))


def search(q: str, sensitivity: str) -> list[dict]:
    needle = q.strip().lower()
    return [dict(r, snippet=None) for r in pages(sensitivity)
            if needle in r["title"].lower() or needle in r["line"].lower()]


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    m = REF.fullmatch(ref or "")
    if not m:
        raise Missing(ref)
    project = desk_json("/api/futurespeak/project/" + quote(m.group("name"), safe="")).get("project")
    row = _row(project, sensitivity) if isinstance(project, dict) else None
    if row is None or row["title"] != m.group("name"):
        raise Missing(ref)
    status = project.get("status") if isinstance(project.get("status"), dict) else {}
    meta = [{"k": "Deploy", "v": STATE_WORDS.get(row["state"], "unknown")}]
    branch = ws_blocks.line(status.get("branch"), 80)
    if branch:
        meta.append({"k": "Branch", "v": branch})
    meta.append({"k": "Uncommitted", "v": str(_count(status.get("uncommitted")))})
    changed = ws_blocks.line(status.get("last_commit_rel"), 40)
    if changed:
        meta.append({"k": "Last change", "v": changed})
    return {"rev": "p_" + _digest(ref, row["line"], row["state"], row["when"], changed), "ref": ref, "kind": KIND,
            "title": row["title"], "lives_on": "pc", "sensitivity": sensitivity, "taint": None, "meta": meta,
            "blocks": [], "relations": [], "sources": [], "next": None, "truncated": False}
