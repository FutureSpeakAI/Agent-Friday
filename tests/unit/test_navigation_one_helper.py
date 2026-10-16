"""fridayNavigate and fridayOpenWorkspace are one helper, not two.

They diverged (field install report, Part 2 #12): fridayOpenWorkspace (the
desktop's) opened the workspace window and then deep-linked it, while
fridayNavigate only deep-linked a workspace that was ALREADY open, so every
caller of fridayNavigate on a closed workspace (the orb sky's stars, the
Meetings pill, a task receipt, Knowledge's graph) silently did nothing.

Now fridayNavigate is canonical: the desktop registers its window opener
(window.__fridayOpenWsWindow) and fridayOpenWorkspace is a thin alias. Both
names keep working. Red on origin/main: fridayNavigate never opens the window.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]
node = shutil.which("node")

HARNESS = r"""
const WS_ALIASES = { wiki: { workspace: 'knowledge', view: 'pages' } };
const events = [];
global.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
global.window = { dispatchEvent: e => { events.push(e.detail); return true; }, location: { href: '/' } };
FUNCS
const opened = [];
const out = {};
// No desktop mounted (a workspace tab, the undocked chat): navigation is the event alone.
fridayNavigate({ workspace: 'system', tab: 'task', task: 't1' });
out.without_desktop = { opened: opened.slice(), events: events.length };
// The desktop registers its opener: a closed workspace now opens, then deep-links.
window.__fridayOpenWsWindow = id => opened.push(id);
fridayNavigate({ workspace: 'system', tab: 'task', task: 't2' });
fridayNavigate({ workspace: 'wiki', path: 'p.md' });
out.with_desktop = { opened: opened.slice(), last: events[events.length - 1] };
console.log(JSON.stringify(out));
"""


def _funcs(text):
    start = text.index("function fridayWorkspaceId(")
    end = text.index("function useNavTarget(")
    return text[start:end]


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_fridayNavigate_opens_a_closed_workspace_when_the_desktop_is_here(page):
    src = HARNESS.replace("FUNCS", _funcs(page.read_text(encoding="utf-8")))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["without_desktop"] == {"opened": [], "events": 1}
    assert out["with_desktop"]["opened"] == ["system", "knowledge"], "the window must open before the deep link"
    assert out["with_desktop"]["last"]["workspace"] == "knowledge"
    assert out["with_desktop"]["last"]["view"] == "pages"


def test_fridayOpenWorkspace_is_a_thin_alias_installed_by_the_desktop():
    text = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "window.__fridayOpenWsWindow = openWs;" in text
    assert "window.fridayOpenWorkspace = t => fridayNavigate(t);" in text
    # The old second implementation is gone.
    assert not re.search(r"window\.fridayOpenWorkspace = t => \{\s+if \(!t \|\| !t\.workspace\) return;", text)
    mirror = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    assert "window.__fridayOpenWsWindow=openWs;window.fridayOpenWorkspace=(t)=>fridayNavigate(t);" in mirror
