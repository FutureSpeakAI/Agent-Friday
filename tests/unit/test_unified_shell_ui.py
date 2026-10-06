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


@pytest.mark.parametrize("style", ["simple", "classic"])
@pytest.mark.parametrize("size", [(1600, 1000), (390, 844), (568, 320)])
def test_standalone_chat_respects_the_header_at_every_size(browser_page, style, size):
    page, base = browser_page
    try:
        page.set_viewport_size({"width": size[0], "height": size[1]})
        page.goto(base + "/w/news?experience=" + style, wait_until="domcontentloaded")
        page.wait_for_selector('[data-standalone="news"]', timeout=60000)
        assert page.locator("body").get_attribute("data-friday-display-style") == style
        chat = page.get_by_role("button", name="Open chat with Friday", exact=True)
        if not chat.is_visible():
            page.get_by_role("button", name="More Friday controls", exact=True).click()
        chat.click()
        page.wait_for_selector(".chat-panel.open", timeout=10000)
        assert not page.locator(".chat-panel.open .chat-resize-h").is_visible()
        page.wait_for_function("""() => {
          const bar = document.querySelector('.top-bar').getBoundingClientRect();
          const tray = document.querySelector('.chat-panel.open').getBoundingClientRect();
          return tray.top >= bar.bottom - 1 && tray.bottom <= innerHeight + 1 && tray.height > 0;
        }""")
    finally:
        page.set_viewport_size({"width": 1600, "height": 1000})


def test_the_desktop_keeps_its_scene_menu_and_one_bar(browser_page):
    page, base = browser_page
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector(".dock-btn", timeout=60000)
    bar = page.locator('[data-testid="friday-top-bar"]')
    assert bar.count() == 1
    assert bar.locator('[aria-label="Scene selection"]').count() == 1
    assert bar.locator('[data-testid="ws-tab-name"]').count() == 0


# ── fullscreen with chat (§5) ────────────────────────────────────────────────

@pytest.mark.parametrize("rel", ("index.html", "ui_parts/app.html"))
def test_both_pages_carry_the_layout(rel):
    body = _app_body(_read(rel))
    bar = body[body.index("const shellTopBar"):body.index("const shellKillSwitch")]
    assert "fs-chat-toggle" in bar, rel + ": the button is in the shared bar"
    assert re.search(r"id: ?'fs-chat'", body), rel + ": the palette's action"
    assert re.search(r"e\.shiftKey ?&& ?\(e\.key ?=== ?'F'", body), rel + ": the keystroke"
    assert re.search(r"addEventListener\('friday:layout', ?on\)", body), rel + ": Friday's change"
    assert re.search(r"saveAgentSettings\(\{ ?workspace_layouts: ?next ?\}\)", body), rel + ": remembered"


class _Api:
    """/api/settings and /api/desktop/ack as the page sees them, recorded."""

    def __init__(self, page, settings=None):
        self.page, self.settings, self.saved, self.acks = page, dict(settings or {}), [], []

    def _settings(self, route):
        if route.request.method == "POST":
            body = (route.request.post_data_json or {}).get("settings") or {}
            self.saved.append(body)
            self.settings.update(body)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"status": "ok", "settings": self.settings}))

    def _ack(self, route):
        self.acks.append(route.request.post_data_json or {})
        route.fulfill(status=200, content_type="application/json", body='{"status":"ok"}')

    def __enter__(self):
        self.page.route("**/api/settings", self._settings)
        self.page.route("**/api/desktop/ack", self._ack)
        return self

    def __exit__(self, *a):
        self.page.unroute("**/api/settings", self._settings)
        self.page.unroute("**/api/desktop/ack", self._ack)


def _boxes(page):
    return page.evaluate("""() => {
      const r = s => { const e = document.querySelector(s); return e ? e.getBoundingClientRect() : null; };
      const w = r('.fwin.maxed'), tray = r('.chat-panel.open');
      return {maxed: !!w, trayOpen: !!tray, winRight: w && w.right, trayLeft: tray && tray.left,
              dockHidden: !!document.querySelector('.dock.hidden'),
              body: document.body.classList.contains('fr-fs-chat')};
    }""")


def test_the_keystroke_lays_the_front_workspace_out_and_back(browser_page):
    page, base = browser_page
    with _Api(page) as api:
        page.goto(base + "/index.html", wait_until="domcontentloaded")
        page.wait_for_selector('.dock-btn[data-ws="news"]', timeout=60000)
        page.click('.dock-btn[data-ws="news"]')
        page.wait_for_selector(".fwin", timeout=15000)
        page.wait_for_timeout(600)
        page.keyboard.press("Control+Shift+F")
        page.wait_for_selector(".chat-panel.open", timeout=10000)
        page.wait_for_timeout(700)
        on = _boxes(page)
        assert on["maxed"] and on["trayOpen"] and on["dockHidden"] and on["body"], on
        assert on["winRight"] <= on["trayLeft"] + 1, ("the tray covers the workspace", on)
        assert {"workspace_layouts": {"news": "fullscreen_chat"}} in api.saved, api.saved
        assert page.locator('[data-testid="fs-chat-toggle"]').get_attribute("aria-pressed") == "true"
        page.keyboard.press("Control+Shift+F")
        page.wait_for_timeout(700)
        off = _boxes(page)
        assert not off["maxed"] and not off["trayOpen"] and not off["dockHidden"] and not off["body"], off
        assert api.saved[-1] == {"workspace_layouts": {}}, api.saved


def test_the_palette_offers_it(browser_page):
    page, base = browser_page
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector(".dock-btn", timeout=60000)
    page.keyboard.press("Control+k")
    page.keyboard.type("full")
    page.wait_for_timeout(400)
    assert "Fullscreen with chat" in page.locator(".cmd-palette").inner_text()
    page.keyboard.press("Escape")


def test_a_tab_remembers_it_and_applies_fridays_change(browser_page):
    page, base = browser_page
    with _Api(page, {"workspace_layouts": {"news": "fullscreen_chat"}}) as api:
        page.goto(base + "/w/news", wait_until="domcontentloaded")
        page.wait_for_selector(".chat-panel.open", timeout=30000)
        page.wait_for_timeout(600)
        pad = page.evaluate("getComputedStyle(document.querySelector('.ws-tab')).paddingRight")
        assert pad not in ("0px", ""), "the tray is docked beside the workspace"
        # Friday turns it off (set_workspace_layout), and the tab says it applied it.
        page.evaluate("""() => window.dispatchEvent(new CustomEvent('friday:layout',
            {detail: {type: 'layout', id: 'layout-1-abc123', workspace: 'news', fullscreen_chat: false}}))""")
        page.wait_for_timeout(600)
        assert page.locator(".chat-panel.open").count() == 0
        assert {"id": "layout-1-abc123", "result": {"applied": True, "workspace": "news", "page": "tab"}} in api.acks
        # One for another workspace is not this tab's to apply.
        page.evaluate("""() => window.dispatchEvent(new CustomEvent('friday:layout',
            {detail: {type: 'layout', id: 'layout-2-abc123', workspace: 'calendar', fullscreen_chat: true}}))""")
        page.wait_for_timeout(400)
        assert not any(a.get("id") == "layout-2-abc123" for a in api.acks)
