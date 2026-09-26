"""What the desktop tells Friday it can open must be what it actually opens.

Each workspace declares, beside its deep-link handler, the keys that handler
reads and the sections it can show (fridayDeclareNav). navigate_to resolves
the user's words against that declaration, so a handler that starts reading a
new key, or a section list that names a tab the workspace does not have,
would send Friday to something that is not there. This reads the served page
and its mirror and holds the declarations to the handlers beside them.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
APP = REPO / "ui_parts" / "app.html"
MAIL = REPO / "static" / "friday_mail.js"


def _block(src, start):
    """The text of the {...} block whose opening brace is at or after `start`,
    skipping strings, template literals and comments."""
    i = src.index("{", start)
    depth, j, n = 0, i, len(src)
    while j < n:
        c = src[j]
        if c in "'\"`":
            q, j = c, j + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
        elif src.startswith("//", j):
            j = src.index("\n", j)
        elif src.startswith("/*", j):
            j = src.index("*/", j) + 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError("unbalanced block at %d" % start)


def _declarations(src):
    out = {}
    for m in re.finditer(r"fridayDeclareNav\(\s*'([a-z]+)'\s*,", src):
        body = _block(src, m.end())
        keys = re.search(r"keys:\s*\[([^\]]*)\]", body)
        out[m.group(1)] = {
            "keys": set(re.findall(r"'([A-Za-z_]+)'", keys.group(1))) if keys else set(),
            "body": body}
    return out


def _handler_keys(src, ws):
    m = re.search(r"useNavTarget\(\s*'%s'\s*,\s*\(?t\)?\s*=>\s*\{" % ws, src)
    if not m:
        return None
    return set(re.findall(r"\bt\.([A-Za-z_]+)", _block(src, m.start())))


def _component(src, name):
    m = re.search(r"^function %s\(" % name, src, re.M)
    assert m, "%s is gone" % name
    return _block(src, m.end())


INDEX_SRC = INDEX.read_text(encoding="utf-8")
APP_SRC = APP.read_text(encoding="utf-8")
DECL = _declarations(INDEX_SRC)


def test_the_workspaces_with_deep_links_all_declare_themselves():
    assert set(DECL) >= {"knowledge", "studio", "system", "code", "news", "calendar",
                         "draft", "contacts", "content", "settings"}
    assert "fridayDeclareNav" in INDEX_SRC and "fridayNavManifest" in INDEX_SRC


@pytest.mark.parametrize("ws", sorted(DECL))
def test_a_handler_reads_only_declared_keys(ws):
    used = _handler_keys(INDEX_SRC, ws)
    if used is None:
        pytest.skip("%s has no useNavTarget handler of its own" % ws)
    used.discard("workspace")
    assert used <= DECL[ws]["keys"], (
        "%s's deep-link handler reads %s, which its declaration does not list"
        % (ws, sorted(used - DECL[ws]["keys"])))


def test_messages_declares_what_its_deep_link_reads():
    src = MAIL.read_text(encoding="utf-8")
    decl = _declarations(src.replace("(window.__fridayNavDecls = window.__fridayNavDecls || []).push(['messages',",
                                     "fridayDeclareNav('messages',"))
    assert "messages" in decl, "friday_mail.js no longer declares Messages"
    m = re.search(r"if \(!t \|\| t\.workspace !== 'messages'\) return;", src)
    assert m, "the Messages deep-link handler moved"
    body = src[m.start():src.index("};", m.start())]
    used = set(re.findall(r"\bt\.([A-Za-z_]+)", body)) - {"workspace"}
    assert used <= decl["messages"]["keys"], sorted(used - decl["messages"]["keys"])


@pytest.mark.parametrize("ws,component,ids", [
    ("news", "NewsWS", ["frontpage", "feed", "readlater", "notes", "briefings", "weekly",
                        "editorial", "trust"]),
    ("studio", "StudioWS", ["generate", "music", "timeline", "production", "gallery",
                            "projects", "files"]),
    ("system", "SystemWS", ["self-improvement", "approvals"]),
])
def test_declared_sections_are_ones_the_workspace_has(ws, component, ids):
    body = _component(INDEX_SRC, component)
    const = {"news": "NEWS_TABS", "studio": "STUDIO_VIEWS"}.get(ws)
    if const:
        start = INDEX_SRC.index("const %s = [" % const)
        declared = re.findall(r"id:\s*'([a-z-]+)'", INDEX_SRC[start:INDEX_SRC.index("];", start)])
    else:
        declared = re.findall(r"id:\s*'([a-z-]+)'", DECL[ws]["body"])
    assert sorted(declared) == sorted(ids), (ws, declared)
    for sid in ids:
        assert ("'%s'" % sid) in body, "%s declares a section %r that %s never shows" % (
            ws, sid, component)


@pytest.mark.parametrize("ws", sorted(DECL))
def test_the_mirror_declares_the_same_keys(ws):
    mirror = _declarations(APP_SRC)
    assert ws in mirror, "ui_parts/app.html does not declare %s" % ws
    assert mirror[ws]["keys"] == DECL[ws]["keys"], ws


@pytest.mark.parametrize("name", ["index.html", "app.html"])
def test_both_copies_carry_the_desktop_channel(name):
    src = INDEX_SRC if name == "index.html" else APP_SRC
    for needle in ("/api/desktop/events", "/api/desktop/state", "/api/desktop/ack",
                   "FRIDAY_TAB_REPORTERS", "fridayNavManifest()", "data-st-section"):
        assert needle in src, "%s is missing %s" % (name, needle)
