"""One registry names every workspace, and the page reads it.

  * The page loads static/workspace_registry.js before the app, and the dock,
    window titles, tab headers and the command palette read it.
  * Every workspace has an icon, a distinct name that is easy to say, aliases
    that point at one workspace each, and a brand accent (never a status hue).
  * An icon's straight lines paint: no horizontal or vertical stroke uses a
    gradient or glow in box units, which paint nothing on a zero-height box.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.services import workspace_registry

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
HEAD = (ROOT / "ui_parts" / "head.html").read_text(encoding="utf-8")
REG = workspace_registry.parse((ROOT / "static" / "workspace_registry.js").read_text(encoding="utf-8"))
UI = {"index.html": INDEX, "app.html": APP}

STATUS_HUES = {"green", "amber", "red", "yellow", "orange"}


def test_the_page_loads_the_registry_before_the_app():
    tag = '<script src="/static/workspace_registry.js"></script>'
    assert tag in HEAD
    at = INDEX.index(tag)
    assert at < INDEX.index("const DOCK_GROUPS = fridayDockGroups(window.FRIDAY_WORKSPACE_REGISTRY);")
    assert "const DOCK_GROUPS=fridayDockGroups(window.FRIDAY_WORKSPACE_REGISTRY);" in APP


def test_every_workspace_has_its_icon():
    for w in REG["workspaces"]:
        assert (ROOT / "assets" / "icons" / (w["icon"] + ".svg")).is_file(), w["id"]


def test_names_are_distinct_and_every_alias_names_one_workspace():
    labels = [w["label"].lower() for w in REG["workspaces"]]
    assert len(labels) == len(set(labels))
    for a in labels:
        for b in labels:
            assert a == b or not b.startswith(a), "%r is the start of %r: easy to mishear" % (a, b)
    owner = {}
    for w in REG["workspaces"]:
        for word in [w["id"], w["label"]] + w["aliases"]:
            k = word.strip().lower()
            assert owner.setdefault(k, w["id"]) == w["id"], "%r names %s and %s" % (k, owner[k], w["id"])


def test_accents_are_brand_accents():
    for w in REG["workspaces"]:
        assert w["accent"] not in STATUS_HUES, w["id"]
    assert sum(1 for w in REG["workspaces"] if w["accent"] == "cyan") > len(REG["workspaces"]) // 2


def test_every_workspace_says_what_it_is_for():
    for w in REG["workspaces"]:
        assert 10 < len(w["blurb"]) <= 90, w["id"]


def _straight(el):
    if el.startswith("<line"):
        n = {k: float(v) for k, v in re.findall(r'\b(x1|y1|x2|y2)="([-\d.]+)"', el)}
        return len(n) == 4 and (n["x1"] == n["x2"] or n["y1"] == n["y2"])
    d = re.search(r'\bd="([^"]+)"', el)
    cmds = re.findall(r"[MLHVmlhv][^MLHVmlhvCcSsQqTtAaZz]*", d.group(1)) if d else []
    return len(cmds) == 2 and cmds[1][0] in "HhVv"


@pytest.mark.parametrize("icon", sorted({w["icon"] for w in REG["workspaces"]}))
def test_an_icons_straight_lines_paint(icon):
    s = (ROOT / "assets" / "icons" / (icon + ".svg")).read_text(encoding="utf-8")
    box = {m.group(1) for m in re.finditer(r'<(?:linearGradient|radialGradient|filter)\b[^>]*\bid="([^"]+)"[^>]*>', s)
           if "userSpaceOnUse" not in m.group(0)}
    for m in re.finditer(r"<(?:line|path)\b[^>]*/>", s):
        el = m.group(0)
        if not _straight(el):
            continue
        for ref in re.findall(r'url\(#([^)]+)\)', el):
            assert ref not in box, "%s: a straight line uses %s, in box units" % (icon, ref)
