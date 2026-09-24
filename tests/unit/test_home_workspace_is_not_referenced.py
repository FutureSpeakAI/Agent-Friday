"""Nothing may name the retired "home" workspace as a place to go.

The desktop is the landing screen and has no window. A table entry that still
names "home" is not harmless: openWs('home') opens an empty window, a connector
badge for "home" is never shown, and a prefetch for it warms endpoints no panel
reads. /w/home keeps redirecting to the desktop (routes/core_routes.py), which
is the one place the name is meant to survive.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_derived_todo_notifications_do_not_target_home(monkeypatch):
    from agent_friday.services import notifications as N

    monkeypatch.setattr(N, "_load_todos", lambda: [
        {"status": "proposed"},
        {"status": "approved", "deadline": "2000-01-01"},
    ])
    derived = N._compute_derived_notifications()
    kinds = {d["kind"] for d in derived}
    assert {"todo", "overdue"} <= kinds
    for d in derived:
        assert (d.get("target") or {}).get("workspace") != "home", d["id"]


def test_workspace_tables_do_not_list_home():
    from agent_friday.routes import voice_context as vc
    from agent_friday.services import agent as A
    from agent_friday.services import connectors as C
    from agent_friday.services import distributions as D

    assert "home" not in vc.WORKSPACE_VOICE_LABELS
    assert "home" not in vc._VOICE_CONTEXT_BUILDERS
    assert "home" not in A._WORKSPACE_LABELS
    for key, defn in C.CONNECTOR_DEFS.items():
        assert "home" not in (defn.get("workspaces") or []), key
    for key, distro in D.BUILTIN_DISTROS.items():
        assert "home" not in distro.get("default_workspaces", []), key


def test_the_navigate_tool_does_not_offer_home():
    from agent_friday.services import agent as A

    nav = next(t for t in A.CLAUDE_TOOLS if t["name"] == "navigate")
    listed = nav["description"].split("Workspaces:", 1)[1]
    assert not re.search(r"\bhome\b", listed)


def test_the_panel_prefetch_does_not_warm_home():
    for rel in ("index.html", "ui_parts/app.html"):
        src = (REPO / rel).read_text(encoding="utf-8")
        start = src.index("const FRIDAY_PREFETCH_URLS")
        block = src[start:src.index("function fridayPrefetchPanels", start) + 800]
        assert not re.search(r"\bhome:", block), rel
        assert "'home'" not in block, rel
