"""One top bar everywhere (docs/design/active/unified-shell.md §4).

  * The start screen's top bar is one definition in App, and so are the chat
    tray, notifications, settings, Quick Draft and the command palette; the
    desktop and a workspace tab draw those same elements.
  * A workspace tab shows the bar with the model selector and the chat button,
    and the chat button opens the tray in that tab, docked beside the
    workspace rather than over it.
  * The scene menu, orbs and camera stay desktop-only; the tab's context slot
    names its workspace and links back to it on the desktop.
  * In a tab, opening another workspace opens that workspace's own page.

The browser half loads the real index.html from this tree (the /w/<id> page is
the same file with the server's one-line marker), with /api answered here.
"""
from __future__ import annotations

import functools
import http.server
import json
import pathlib
import re
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _app_body(text: str) -> str:
    at = text.index("function App() {") if "function App() {" in text else text.index("function App(){")
    return text[at:]


def _tab_branch(body: str) -> str:
    a = re.search(r"  if ?\(STANDALONE_WS ?&& ?STANDALONE_WS ?!== ?'settings'\) ?\{", body)
    assert a, "the tab branch of App"
    return body[a.start():body.index("\n  }\n", a.start())]


SHELL = ("shellTopBar", "shellChatPanel", "shellNotifs", "shellSettings", "shellQuickDraft",
         "shellPalette", "shellKillSwitch", "shellTaskModal")


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/app.html"))
def test_the_shell_is_defined_once_and_drawn_by_both_branches(rel):
    text = _read(rel)
    assert text.count("top-bar ${topBarHidden") == 1, rel + ": one top bar definition"
    assert text.count("chat-panel ${chatOpen") == 1, rel + ": one chat tray"
    body = _app_body(text)
    tab = _tab_branch(body)
    main = body[body.index(tab) + len(tab):]
    for name in SHELL:
        assert re.search(r"const %s ?=" % name, body), (rel, name)
        assert name in tab, rel + ": the tab does not draw " + name
        assert name in main, rel + ": the desktop does not draw " + name
    bar = body[body.index("const shellTopBar"):body.index("const shellKillSwitch")]
    # index.html has the QuickSwitch component; the mirror still has its older
    # inline model menu (noted in its header). Either way it is in the one bar.
    assert "QuickSwitch" in bar or "modelMenuRef" in bar, "the model selector is in the shared bar"
    assert "setChatOpen" in bar, "the chat button is in the shared bar"
    assert "StandaloneApprovals" in bar, "what is waiting shows in the bar"


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/app.html", "ui_parts/styles_and_scene.html"))
def test_the_tab_has_no_header_of_its_own(rel):
    assert "ws-tab-head" not in _read(rel)


def test_desktop_only_controls_stay_on_the_desktop():
    bar = _app_body(_read("index.html"))
    bar = bar[bar.index("const shellTopBar"):bar.index("const shellKillSwitch")]
    assert "shellTab ? /*#__PURE__*/React.createElement(ShellTabName" in bar, "the scene menu"
    assert "!shellTab && orbiting.length > 0" in bar, "the orbs"
    assert re.search(r"!shellTab && /\*#__PURE__\*/React\.createElement\(\"button\", \{\s+onClick: \(\) => \{\s+if \(cameraOn\)", bar), "the camera"


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/app.html"))
def test_a_tab_opens_other_workspaces_as_their_own_pages(rel):
    body = re.sub(r"\s+", "", _app_body(_read(rel)))
    assert "consttoggle=id=>{if(tabGo(id))return;" in body
    assert "constopenWs=rawId=>{if(tabGo(rawId))return;" in body
    nav = _read(rel)
    nav = nav[nav.index("function fridayNavigate("):]
    nav = nav[:nav.index("\n}\n")]
    assert "__FRIDAY_STANDALONE__" in nav and "'/w/'" in nav


# ── in a real browser ─────────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    """The tree as the server serves it: /w/<id> is index.html with the marker."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        m = re.match(r"^/w/([a-z0-9_-]+)", self.path)
        if m:
            html = (REPO / "index.html").read_text(encoding="utf-8").replace(
                "<head>", '<head><script>window.__FRIDAY_STANDALONE__="%s";'
                'document.documentElement.classList.add("ws-standalone");</script>' % m.group(1), 1)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()


@pytest.fixture(scope="module")
def browser_page():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            # The server's side: quiet, empty answers; no command stream.
            page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
            page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                        body=json.dumps({"status": "ok"})))
            yield page, base
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_workspace_tab_wears_the_desktop_top_bar(browser_page):
    page, base = browser_page
    page.goto(base + "/w/news", wait_until="domcontentloaded")
    page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    bar = page.locator('[data-testid="friday-top-bar"]')
    assert bar.count() == 1
    text = bar.inner_text()
    assert "AGENT FRIDAY™" in text and "FutureSpeak.AI" in text
    assert page.locator('[data-testid="ws-tab-name"]').inner_text().startswith("News")
    assert page.locator('[data-testid="ws-tab-back"]').get_attribute("href") == "/?workspace=news"
    assert bar.locator('[aria-label="Open chat with Friday"]').count() == 1
    assert bar.locator('[aria-label="Open settings"]').count() == 1
    assert page.locator('.dock-btn, .fwin').count() == 0, "no desktop in a tab"
    assert page.title() == "News · Agent Friday™"


def test_the_chat_button_docks_the_tray_beside_the_workspace(browser_page):
    page, base = browser_page
    page.goto(base + "/w/news", wait_until="domcontentloaded")
    page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    page.click('[data-testid="friday-top-bar"] [aria-label="Open chat with Friday"]')
    page.wait_for_selector(".chat-panel.open", timeout=10000)
    page.wait_for_timeout(500)
    got = page.evaluate("""() => {
      const tray = document.querySelector('.chat-panel.open').getBoundingClientRect();
      const body = document.querySelector('.ws-tab-body').getBoundingClientRect();
      const bar = document.querySelector('[data-testid="friday-top-bar"]').getBoundingClientRect();
      return {trayLeft: tray.left, trayTop: tray.top, bodyRight: body.right, bodyTop: body.top, barBottom: bar.bottom};
    }""")
    assert got["bodyRight"] <= got["trayLeft"] + 1, ("the tray covers the workspace", got)
    assert got["bodyTop"] >= got["barBottom"] - 1, ("the workspace runs under the bar", got)
    assert got["trayTop"] >= got["barBottom"] - 1, got


def test_the_desktop_keeps_its_scene_menu_and_one_bar(browser_page):
    page, base = browser_page
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector(".dock-btn", timeout=60000)
    bar = page.locator('[data-testid="friday-top-bar"]')
    assert bar.count() == 1
    assert bar.locator('[aria-label="Scene selection"]').count() == 1
    assert bar.locator('[data-testid="ws-tab-name"]').count() == 0
