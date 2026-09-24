"""Knowledge is one workspace: the wiki's pages and the graph built from them.

The joins the merged workspace depends on, checked in both UI files
(index.html is served; ui_parts/app.html is its hand-kept mirror):

  * a page node's id is the key the server gives that page
    (wiki_graph._page_key), so clicking a node opens its page and opening a
    page finds its node;
  * the old workspace id `wiki` resolves to Knowledge on its Pages view: in
    the navigation bus, and in a dock arrangement saved before the merge;
  * citation chips open Knowledge, one chip per citation;
  * the layout keeps its bottom bar in view: the root is a flex column whose
    middle row takes the slack, and every host gives the root a real height.

The JavaScript is run with node, taken from the files as they ship. Skipped,
not failed, where node is unavailable.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from agent_friday.services.knowledge_graph.wiki_graph import _page_key

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = {"index.html": ROOT / "index.html", "app.html": ROOT / "ui_parts" / "app.html"}
CSS_HOSTS = {"index.html": ROOT / "index.html", "head.html": ROOT / "ui_parts" / "head.html"}

node = shutil.which("node")
needs_node = pytest.mark.skipif(not node, reason="node is not on PATH")


def _text(name):
    return UI[name].read_text(encoding="utf-8")


def _top(text, head):
    """The top-level declaration starting with `head`: its own line when that
    line is complete, else through the first line that closes it at column 0."""
    a = text.index("\n" + head) + 1
    line = text[a:text.index("\n", a)]
    if line.count("{") == line.count("}") and line.count("[") == line.count("]") \
            and line.count("(") == line.count(")"):
        return line
    m = re.compile(r"\n[}\]]+[);]*\n").search(text, a)
    assert m, f"no end found for {head!r}"
    return text[a:m.end()]


def _dock_source(text):
    """DOCK_GROUPS and the registries derived from it (adjacent in both files)."""
    a = text.index("const DOCK_GROUPS")
    b = text.index("\n", text.index("const WS_GROUP_OF", a))
    return text[a:b]


def _run(tmp_path, js):
    f = tmp_path / "probe.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([node, str(f)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _nav_source(text):
    return "\n".join(_top(text, h) for h in (
        "const WS_ALIASES", "function fridayWorkspaceId", "function fridayResolveTarget"))


# ── node ↔ page ──────────────────────────────────────────────────────────

PATHS = [
    "brain/bootstrap.md",
    "Brain/Boot Strap.md",
    "people/ Dana Okafor .md",
    "top-level.md",
    "a/b/c.MD",
    "reference/_index.md",
    "notes/plain.txt",
    "entities\\org-example.md",
]


@needs_node
@pytest.mark.parametrize("ui", sorted(UI))
def test_a_page_node_id_is_the_key_the_server_gives_the_page(ui, tmp_path):
    text = _text(ui)
    src = _top(text, "function kwPageKey") + "\n" + _top(text, "function kwNodeIdForPath")
    got = _run(tmp_path, src + "\nconsole.log(JSON.stringify(%s.map(p => [kwPageKey(p), kwNodeIdForPath(p)])));"
               % json.dumps(PATHS))
    for path, (key, node_id) in zip(PATHS, got):
        assert key == _page_key(path), (ui, path)
        assert node_id == "page:" + _page_key(path), (ui, path)


# ── the old id ───────────────────────────────────────────────────────────

@needs_node
@pytest.mark.parametrize("ui", sorted(UI))
def test_the_old_wiki_id_opens_knowledge_on_its_pages_view(ui, tmp_path):
    src = _nav_source(_text(ui))
    got = _run(tmp_path, src + """
const t = {workspace: 'news', x: 1};
console.log(JSON.stringify({
  wiki: fridayWorkspaceId('wiki'),
  news: fridayWorkspaceId('news'),
  withPath: fridayResolveTarget({workspace: 'wiki', path: 'brain/bootstrap.md'}),
  keepsView: fridayResolveTarget({workspace: 'wiki', view: 'split'}).view,
  untouched: fridayResolveTarget(t) === t,
}));""")
    assert got["wiki"] == "knowledge"
    assert got["news"] == "news"
    assert got["withPath"] == {"workspace": "knowledge", "view": "pages", "path": "brain/bootstrap.md"}
    assert got["keepsView"] == "split"
    assert got["untouched"] is True


@pytest.mark.parametrize("ui", sorted(UI))
def test_one_knowledge_icon_and_no_wiki_icon(ui):
    block = _dock_source(_text(ui))
    ids = re.findall(r"\bid:\s*'([a-z0-9_-]+)'", block)
    assert "knowledge" in ids and "wiki" not in ids
    # Ctrl+K "wiki" still finds it.
    assert re.search(r"id:\s*'knowledge'[^}]*aka:\s*\[[^\]]*'wiki'", block, flags=re.S)


@pytest.mark.parametrize("ui", sorted(UI))
def test_the_knowledge_window_is_the_merged_workspace(ui):
    text = _text(ui)
    assert "function WikiWS" not in text
    assert "function KnowledgeWS" in text and "function KnowledgePages" in text
    if ui == "index.html":
        assert "knowledge: /*#__PURE__*/React.createElement(KnowledgeWS, null)" in text
        assert not re.search(r"\bwiki: /\*#__PURE__\*/React\.createElement\(", text)
    else:
        assert "knowledge:<KnowledgeWS/>" in text
        assert "wiki:<" not in text


DOCK_CASES = [
    # (saved dock_custom, show_all, knowledge visible, why)
    ({}, True, True, "nothing saved"),
    ({"order": ["wiki", "news", "knowledge"], "hidden": []}, True, True, "pre-merge, both shown"),
    ({"order": ["news", "wiki", "knowledge"], "hidden": ["knowledge"]}, True, True,
     "pre-merge: Knowledge hidden, Wiki kept; the merged icon holds the wiki"),
    ({"order": ["news", "wiki", "knowledge"], "hidden": ["wiki"]}, True, True,
     "pre-merge: Wiki hidden, Knowledge kept"),
    ({"hidden": ["wiki"]}, False, True, "pre-merge, hidden list only"),
    ({"order": ["news", "wiki", "knowledge"], "hidden": ["wiki", "knowledge"]}, True, False,
     "pre-merge: both hidden stays hidden"),
    ({"order": ["news", "knowledge"], "hidden": ["knowledge"]}, True, False,
     "post-merge: hidden as saved"),
]


@needs_node
@pytest.mark.parametrize("ui", sorted(UI))
def test_a_dock_saved_before_the_merge_keeps_the_wiki_reachable(ui, tmp_path):
    text = _text(ui)
    src = "\n".join([_dock_source(text), _nav_source(text), _top(text, "function dockArrangement")])
    got = _run(tmp_path, src + """
const cases = %s;
console.log(JSON.stringify(cases.map(([c, all]) => {
  const r = dockArrangement(c, all);
  return {visible: r.visible.includes('knowledge'), ids: r.ids, configured: r.configured};
})));""" % json.dumps([[c, all_] for c, all_, _, _ in DOCK_CASES]))
    for (custom, _, want, why), res in zip(DOCK_CASES, got):
        assert res["visible"] is want, (ui, why)
        assert "wiki" not in res["ids"], (ui, why)
        assert res["configured"] is bool(custom.get("order") or custom.get("hidden")), (ui, why)
    # The merged icon takes the earlier of the two places.
    assert got[1]["ids"][0] == "knowledge", ui


# ── citations ────────────────────────────────────────────────────────────

@needs_node
@pytest.mark.parametrize("ui", sorted(UI))
def test_citations_open_knowledge_one_chip_each(ui, tmp_path):
    src = _top(_text(ui), "function fridayCitationize")
    cases = {
        "wiki": "See [wiki:brain/bootstrap].",
        "memory": 'We spoke [memory:2026-07-08/"the plan"].',
        "conversation": "Earlier [conversation:2026-07-09].",
        "bare_date": "On [2026-08-01] you said.",
        "literal": "Copied 💬 2026-08-02 from history.",
        "md_link": "A link [2026-08-01](https://example.com).",
    }
    got = _run(tmp_path, src + """
const cases = %s;
const out = {};
for (const k in cases) {
  const html = fridayCitationize(cases[k]);
  out[k] = {
    chips: (html.match(/class="friday-cite/g) || []).length,
    page: (html.match(/data-kw-page="([^"]*)"/) || [])[1] || null,
    query: (html.match(/data-kw-query="([^"]*)"/) || [])[1] || null,
    link: html.indexOf('role="link"') >= 0,
  };
}
console.log(JSON.stringify(out));""" % json.dumps(cases, ensure_ascii=False))
    assert got["wiki"] == {"chips": 1, "page": "brain/bootstrap", "query": None, "link": True}
    for k, day in (("memory", "2026-07-08"), ("conversation", "2026-07-09"),
                   ("bare_date", "2026-08-01"), ("literal", "2026-08-02")):
        assert got[k] == {"chips": 1, "page": None, "query": day, "link": True}, (ui, k)
    assert got["md_link"]["chips"] == 0


# ── the bottom bar stays in view ─────────────────────────────────────────

LAYOUT_RULES = [
    r"\.kw-root\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*height:\s*100%;[^}]*min-height:\s*0",
    r"\.kw-body\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0",
    r"\.kw-bar\s*\{[^}]*flex-shrink:\s*0",
    r"\.kw-scene\s*>\s*canvas\s*\{[^}]*position:\s*absolute;[^}]*inset:\s*0",
    r"\.kw-root:fullscreen\s*\{[^}]*height:\s*100vh",
]


@pytest.mark.parametrize("host", sorted(CSS_HOSTS))
@pytest.mark.parametrize("rule", LAYOUT_RULES)
def test_the_layout_gives_the_bar_its_row_in_every_host(host, rule):
    css = CSS_HOSTS[host].read_text(encoding="utf-8")
    assert re.search(rule, css), f"{host}: missing {rule}"


# Knowledge's root is a .ws-fill: the shell gives such a root exactly its
# frame's height, in a standalone tab and in a desktop window.
FILL_RULES = [
    r"\.ws-tab-body\s*>\s*\.ws-fill[^{]*\{[^}]*flex:\s*1 1 auto;[^}]*min-height:\s*0",
    r"\.ws-custom-root\s*>\s*\.ws-fill[^{]*\{[^}]*flex:\s*1 1 auto;[^}]*min-height:\s*0",
    r"\.ws-custom-root:has\(>\s*\.ws-fill\)[^{]*\{[^}]*flex:\s*1 1 auto;[^}]*min-height:\s*0",
    r"\.fwin-body:has\(>\s*\.ws-custom-root\s*>\s*\.ws-fill\)[^{]*\{[^}]*flex-direction:\s*column",
]
FILL_HOSTS = {"index.html": ROOT / "index.html",
              "styles_and_scene.html": ROOT / "ui_parts" / "styles_and_scene.html"}


@pytest.mark.parametrize("host", sorted(FILL_HOSTS))
@pytest.mark.parametrize("rule", FILL_RULES)
def test_every_frame_fills_a_ws_fill_root(host, rule):
    assert re.search(rule, FILL_HOSTS[host].read_text(encoding="utf-8")), f"{host}: missing {rule}"


@pytest.mark.parametrize("ui", sorted(UI))
def test_the_knowledge_root_is_a_ws_fill(ui):
    assert "className: 'kw-root ws-fill'" in _text(ui), ui


@pytest.mark.parametrize("ui", sorted(UI))
def test_the_bar_is_rendered_unconditionally(ui):
    text = _text(ui)
    a = text.index("function KnowledgeWS(")
    body = text[a:text.index("\nfunction ", a + 10)]
    # The last child of .kw-root, with no condition in front of it, marked as
    # the workspace's footer (the standalone layout check measures the panes
    # against it).
    assert re.search(r"\)\)\), h\('div', \{\s*className: 'kw-bar',\s*'data-kw-bar': '1',\s*'data-ws-footer': '1'", body), ui


@pytest.mark.parametrize("ui", sorted(UI))
def test_knowledge_opens_at_a_two_pane_window_size(ui):
    text = _text(ui)
    m = re.search(r"const FWIN_FIRST_SIZE\s*=\s*\{\s*knowledge:\s*\{\s*w:\s*(\d+),\s*h:\s*(\d+),\s*v:\s*(\d+)", text)
    assert m, ui
    w, h, _ = map(int, m.groups())
    assert w >= 1000 and h >= 700


@pytest.mark.parametrize("ui", sorted(UI))
def test_icon_only_buttons_carry_an_aria_label(ui):
    """A button shown as a glyph alone (the section's galaxy button; Big Bang
    and Tour on a compact toolbar) is named for a screen reader: the rule
    tests/icon_labels.spec.ts checks in a browser, pinned here per file."""
    s = _text(ui)
    for label in ("'aria-label': 'Show the ' + s.name + ' section in the galaxy'",
                  "Big Bang: collapse everything to a point",
                  "'Stop the tour':'Presentation tour'" if ui == "app.html"
                  else "\"aria-label\": touring ? 'Stop the tour' : 'Presentation tour'"):
        assert label in s, "%s: missing %r" % (ui, label)
