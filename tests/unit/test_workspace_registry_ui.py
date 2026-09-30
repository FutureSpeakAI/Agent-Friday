"""One registry names every workspace, and the page keeps the contracts the
server's organize and navigate tools rely on.

  * The page loads static/workspace_registry.js before the app, and the dock,
    window titles, tab headers and the command palette read it.
  * Every workspace has an icon, a distinct name that is easy to say, aliases
    that point at one workspace each, and a brand accent (never a status hue).
  * An icon's straight lines paint: no horizontal or vertical stroke uses a
    gradient or glow in box units, which paint nothing on a zero-height box.
  * A window can fill the desktop; a tab Friday opened for one item confirms
    what it shows under the id the server put in its address.
  * Messages takes a Gmail search and a maximized conversation; News opens one
    story on its own; a batch card lists its items; System lists Friday's
    changes, each with its Undo.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.services import item_actions, workspace_registry

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
HEAD = (ROOT / "ui_parts" / "head.html").read_text(encoding="utf-8")
MAIL = (ROOT / "static" / "friday_mail.js").read_text(encoding="utf-8")
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


@pytest.mark.parametrize("ui", sorted(UI))
def test_a_window_can_fill_the_desktop(ui):
    s = UI[ui]
    assert "friday:fwin-max" in s and "__fridayMaxFor" in s
    assert s.count("data-fwin-max") >= 1
    assert re.search(r"a\.max", s), "the action bus does not pass max to the window"


@pytest.mark.parametrize("ui", sorted(UI))
def test_a_tab_confirms_under_the_id_the_server_gave_it(ui):
    s = UI[ui]
    m = re.search(r"/\^tab-\[0-9\]\+-\[0-9a-f\]\{6\}\$/", s)
    assert m, "the tab's report id pattern is missing"
    rid = "tab-%d-%s" % (1759200000, "a1b2c3")
    assert re.fullmatch(r"tab-[0-9]+-[0-9a-f]{6}", rid)
    import inspect
    from agent_friday.services import desktop_targets
    assert '"tab-%d-%s" % (int(time.time()), secrets.token_hex(3))' in inspect.getsource(desktop_targets)
    assert "post('/api/desktop/ack'" in s and "q0.get('nav')" in s


def test_messages_takes_a_search_and_a_maximized_conversation():
    assert "'q', 'max'" in MAIL
    assert "selectFor.current = q" in MAIL and "setSel(new Set((data.messages || []).map(m => m.id)))" in MAIL
    assert "q: query || ''" in MAIL
    assert ".fm.fm-max.fm-reading .fm-listwrap" in MAIL


@pytest.mark.parametrize("ui", sorted(UI))
def test_news_opens_one_story_on_its_own(ui):
    s = UI[ui]
    assert "function NewsArticleView" in s
    assert re.search(r"keys:\s*\[\s*'tab',\s*'article',\s*'url',\s*'title',\s*'source'\s*\]", s)


@pytest.mark.parametrize("ui", sorted(UI))
def test_a_batch_card_lists_its_items(ui):
    s = UI[ui]
    assert "handler === 'item_batch'" in s or "handler==='item_batch'" in s
    assert item_actions.HANDLER == "item_batch"


@pytest.mark.parametrize("ui", sorted(UI))
def test_system_lists_fridays_changes_with_undo(ui):
    s = UI[ui]
    assert "function FridayChangesCard" in s
    assert "/api/actions/receipts?limit=15" in s and "'/undo'" in s


def test_the_receipts_routes_the_page_calls_exist():
    src = (ROOT / "src" / "agent_friday" / "routes" / "actions.py").read_text(encoding="utf-8")
    assert '"/api/actions/receipts", methods=["GET"]' in src
    assert '"/api/actions/receipts/<rid>/undo", methods=["POST"]' in src
