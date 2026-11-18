"""The Crew workspace's roster, read for a phone (PW-G2, adapter 7).

Reads the desktop's own /api/crew/agents (and /api/crew/agents/<id>) in process, GET only. An agent reaches the phone as
a row (name, role, whether it is active, which model it reasons with) and an item (those, its personality text, its
limits, and how many tools, skills, projects and folders it may use). The folders' paths, the agent's own memory notes
and the voice settings never leave the PC. tests/unit/test_tv_ws_crew.py holds the rules.
"""
from __future__ import annotations

import re
from typing import Optional

from agent_friday.tv_gateway import ws_blocks
from agent_friday.tv_gateway.ws_adapter import Missing, Unavailable, desk_json, digest as _digest

PREFIX = "agent:"
KIND = "agent"
FACETS = {"status": lambda row: row["state"] or ""}
REF = re.compile(r"^agent:(?P<id>crew-[a-f0-9]{16})$")


def _row(agent: dict, sensitivity: str) -> Optional[dict]:
    ident = str(agent.get("id") or "")
    if not REF.match(PREFIX + ident):
        return None
    model = " ".join(part for part in (ws_blocks.line(agent.get("provider"), 40), ws_blocks.line(agent.get("model"), 60)) if part)
    return {"ref": PREFIX + ident, "kind": KIND, "title": ws_blocks.line(agent.get("name"), 80) or "(unnamed)",
            "line": " · ".join(part for part in (ws_blocks.line(agent.get("role"), 100), model) if part),
            "state": ws_blocks.line(agent.get("status"), 20) or None, "when": _when(agent.get("updated_at")), "thumb": None,
            "lives_on": "pc", "sensitivity": sensitivity, "taint": None}


def _when(stamp) -> Optional[str]:
    from datetime import datetime, timezone
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not 0 < stamp < 10**11:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _agents() -> list[dict]:
    raw = desk_json("/api/crew/agents").get("agents")
    if not isinstance(raw, list):
        raise Unavailable("crew")
    return [a for a in raw if isinstance(a, dict)]


def pages(sensitivity: str) -> list[dict]:
    """The agents that are not retired, by name."""
    rows = [r for r in (_row(a, sensitivity) for a in _agents()) if r]
    rows.sort(key=lambda r: (r["title"].lower(), r["ref"]))
    return rows


def facets(rows: list[dict]) -> list[dict]:
    counts: dict = {}
    for r in rows:
        name = FACETS["status"](r)
        counts[name] = counts.get(name, 0) + 1
    return [{"name": "status", "values": [{"v": k, "n": n} for k, n in sorted(counts.items())]}]


def rev(rows: list[dict]) -> str:
    return "g_%d_%s" % (len(rows), _digest(*[(r["ref"], r["when"], r["title"], r["state"]) for r in rows]))


def search(q: str, sensitivity: str) -> list[dict]:
    needle = q.strip().lower()
    return [dict(r, snippet=None) for r in pages(sensitivity) if needle in r["title"].lower() or needle in r["line"].lower()]


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    m = REF.match(ref or "")
    if not m:
        raise Missing(ref)
    agent = desk_json("/api/crew/agents/" + m.group("id")).get("agent")
    row = _row(agent, sensitivity) if isinstance(agent, dict) else None
    if row is None:
        raise Missing(ref)
    meta = [{"k": "Status", "v": row["state"] or "unknown"}, {"k": "Model", "v": row["line"].split(" · ")[-1]},
            {"k": "Step limit", "v": str(agent.get("max_steps"))}, {"k": "Time limit", "v": "%s s" % agent.get("time_budget_s")}]
    for label, key in (("Tools", "allowed_tools"), ("Skills", "skills"), ("Projects", "project_ids"), ("Folders", "grants")):
        values = agent.get(key)
        meta.append({"k": label, "v": "%d" % len(values) if isinstance(values, list) else "0"})
    parsed = ws_blocks.parse("\n\n".join(t for t in (str(agent.get("role") or ""), str(agent.get("persona") or "")) if t))
    return {"rev": "a_" + _digest(ref, agent.get("revision"), row["title"]), "ref": ref, "kind": KIND, "title": row["title"],
            "lives_on": "pc", "sensitivity": sensitivity, "taint": None, "meta": meta,
            "blocks": parsed.blocks[offset:offset + limit], "relations": [], "sources": [],
            "next": str(offset + limit) if offset + limit < len(parsed.blocks) else None, "truncated": False}
