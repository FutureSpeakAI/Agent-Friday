"""One-action collapse of the chat tray (docs/design/active/unified-shell.md §11.1).

  * One control hides the tray fully, whatever width the owner gave it: it
    moves past its edge by its own width and is invisible once there. Before,
    a closed tray parked at right: -340px, so a tray wider than 340px stayed
    partly on screen until its edge was dragged.
  * A slim pill on that edge brings it back; Ctrl+Alt+C and the palette hide
    and show it; by voice and in chat, set_chat_tray. Dragging still works.
  * The workspace takes the full width; reduced motion hides it at once; a
    tray the owner hid stays hidden when a layout is applied on its own.
"""
from __future__ import annotations

import functools
import http.server
import json
import pathlib
import re
import shutil
import socketserver
import subprocess
import threading

import pytest

from agent_friday.services import agent, desktop_bus
from agent_friday.services import voice_engine as ve

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
HEADS = ("index.html", "ui_parts/head.html")
node = shutil.which("node")


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8-sig")


def _block(text, name):
    m = re.search(r"/\* %s:begin \*/\n(.*?)/\* %s:end \*/" % (name, name), text, re.S)
    assert m, name
    return m.group(1)


# ── the page ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", HEADS)
def test_a_hidden_tray_moves_by_its_own_width(rel):
    css = _read(rel)
    base = re.search(r"\n        \.chat-panel \{([^}]*)\}", css).group(1)
    assert "right: -340px" not in base and "right: 0" in base, rel
    assert "transform: translateX(calc(100% + 40px))" in base and "visibility: hidden" in base, rel
    assert re.search(r"\.chat-panel\.open \{ transform: none; visibility: visible;", css), rel
    assert re.search(r"\.chat-panel\.left \{[^}]*transform: translateX\(calc\(-100% - 40px\)\)", css), rel
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".chat-panel, .chat-panel.open, .chat-edge-pill { transition: none !important; }" in reduced[:300], rel
    assert re.search(r"\.chat-edge-pill \{[^}]*position: fixed", css), rel


@pytest.mark.parametrize("rel", PAGES)
def test_one_control_hides_it_and_a_pill_brings_it_back(rel):
    text = _read(rel)
    assert text.count('data-testid="chat-collapse"') + text.count('"data-testid": "chat-collapse"') == 1, rel
    assert "Hide chat (Ctrl+Alt+C)" in text and "Show chat (Ctrl+Alt+C)" in text, rel
    assert text.count("chat-edge-pill") >= 2, rel
    assert re.search(r"localStorage\.setItem\('friday_chat_hidden', ?'1'\)", text), rel
    assert re.search(r"const toggleChat ?= ?\(\) ?=> ?\{\s*showChat\(!chatOpen\)", text), rel
    assert re.search(r"label: ?chatOpen ?\? ?'Hide chat' ?: ?'Show chat'", text), rel
    assert re.search(r"!\(auto ?&& ?chatHiddenByOwner\(\)\)", text), rel


def test_both_pages_read_the_keys_the_same_way():
    assert _block(_read(PAGES[0]), "fridayTrayKeys") == _block(_read(PAGES[1]), "fridayTrayKeys")


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_key(tmp_path):
    cases = [
        ({"ctrlKey": True, "altKey": True, "key": "c"}, "toggle"),
        ({"ctrlKey": True, "altKey": True, "key": "C"}, "toggle"),
        ({"ctrlKey": True, "altKey": True, "shiftKey": True, "key": "C"}, None),
        ({"ctrlKey": True, "key": "c"}, None),
        ({"altKey": True, "key": "c"}, None),
        # AltGr is Ctrl+Alt on Windows: a Polish keyboard writes "ć" there
        ({"ctrlKey": True, "altKey": True, "key": "ć", "code": "KeyC"}, None),
        ({"ctrlKey": True, "altKey": True, "metaKey": True, "key": "c"}, None),
    ]
    script = tmp_path / "keys.js"
    script.write_text(_block(_read("index.html"), "fridayTrayKeys")
                      + "\nconsole.log(JSON.stringify(%s.map(fridayTrayKey)));\n"
                      % json.dumps([c for c, _ in cases]), encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout) == [want for _, want in cases]


# ── set_chat_tray ────────────────────────────────────────────────────────────

@pytest.fixture
def pages():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def test_it_is_a_voice_and_chat_tool_on_the_owners_own_screen():
    from agent_friday.governance import action_gate
    from agent_friday.services import taint
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "set_chat_tray")
    assert spec[3] == [] and spec[2]["visible"][0] == "boolean" and spec[2]["side"][0] == "string"
    assert ve._voice_tool_names().count("set_chat_tray") == 1
    assert "CHAT_OK" in spec[1] and "CHAT_NOT_APPLIED" in spec[1]
    assert agent.CLAUDE_TOOL_HANDLERS["set_chat_tray"] is agent._tool_set_chat_tray
    tool = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "set_chat_tray")
    assert tool["input_schema"]["properties"]["side"]["enum"] == ["left", "right"]
    assert agent.TOOL_RINGS["set_chat_tray"] == 1
    assert "set_chat_tray" in action_gate.INTERNAL_TOOLS
    assert taint.TOOL_ROLES["set_chat_tray"] == {}


def test_voice_routes_it_through_the_checkpoint(monkeypatch):
    called, governed = [], []
    monkeypatch.setattr(agent, "_tool_set_chat_tray", lambda inp: called.append(inp) or "CHAT_OK")

    def execute(t, a, handler=None, session_ctx=None):
        governed.append(t)
        return handler(a)

    monkeypatch.setattr(agent, "_execute_tool", execute)
    out = ve._voice_tool_run("set_chat_tray", {"visible": False}, lambda *a, **k: None, {"conversation_id": "c1"})
    assert out == "CHAT_OK" and called == [{"visible": False}] and governed == ["set_chat_tray"]


def test_it_says_what_the_page_in_front_did(pages, monkeypatch):
    monkeypatch.setattr(agent, "CHAT_TRAY_ACK_S", 5.0)
    q = desktop_bus.subscribe("page-1", "chat")
    out = {}

    def call():
        out["text"] = agent._tool_set_chat_tray({"visible": "false"})

    t = threading.Thread(target=call)
    t.start()
    msg = q.get(timeout=5)
    assert msg["type"] == "chat_tray" and msg["visible"] is False and "side" not in msg
    assert desktop_bus.ack(msg["id"], {"applied": True, "shown": False, "side": "right"})
    t.join(10)
    assert out["text"] == "CHAT_OK — the chat is hidden; the pill on the right edge brings it back."


def test_nothing_changes_without_a_page_and_a_wrong_ask_is_refused(pages):
    assert agent._tool_set_chat_tray({"visible": True}).startswith("CHAT_NOT_APPLIED")
    assert agent._tool_set_chat_tray({}).startswith("CHAT_FAIL")
    assert agent._tool_set_chat_tray({"side": "top"}).startswith("CHAT_FAIL")


# ── in a real browser ────────────────────────────────────────────────────────

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
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        with sync_api.sync_playwright() as pw:
            try:
                b = pw.chromium.launch()
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            yield b, base
            b.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def open_page(browser):
    """A fresh page (its own storage) with /api answered here."""
    b, base = browser
    made = []

    def make(local=None, settings=None, reduced=False):
        ctx = b.new_context(viewport={"width": 1600, "height": 1000},
                            reduced_motion="reduce" if reduced else "no-preference")
        if local:
            ctx.add_init_script("(() => { const s = %s; for (const k in s) localStorage.setItem(k, s[k]); })()"
                                % json.dumps(local))
        page = ctx.new_page()
        page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
        page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                    body=json.dumps({"status": "ok"})))
        if settings is not None:
            page.route("**/api/settings", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"status": "ok", "settings": settings})))
        made.append(ctx)
        return page, base

    yield make
    for ctx in made:
        ctx.close()


GEOMETRY = """() => {
  const p = document.querySelector('.chat-panel'), r = p.getBoundingClientRect();
  const tab = document.querySelector('.ws-tab'), body = document.querySelector('.ws-tab-body');
  const pill = document.querySelector('[data-testid="chat-edge-pill"]');
  return {open: p.classList.contains('open'), vw: innerWidth,
          onScreen: Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)),
          visibility: getComputedStyle(p).visibility, duration: getComputedStyle(p).transitionDuration,
          dock: getComputedStyle(document.documentElement).getPropertyValue('--fr-chat-dock').trim(),
          bodyRight: body ? body.getBoundingClientRect().right : null, trayLeft: r.left,
          tabPad: tab ? getComputedStyle(tab).paddingRight : null,
          pill: pill ? pill.getBoundingClientRect().toJSON() : null};
}"""


def _open_tray(page):
    page.click('[data-testid="friday-top-bar"] [aria-label="Open chat with Friday"]')
    page.wait_for_selector(".chat-panel.open", timeout=10000)
    page.wait_for_timeout(600)


def test_one_click_hides_the_tray_fully_and_the_workspace_takes_the_width(open_page):
    page, base = open_page()
    page.goto(base + "/w/news", wait_until="domcontentloaded")
    page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    _open_tray(page)
    before = page.evaluate(GEOMETRY)
    assert before["open"] and before["onScreen"] > 250 and before["tabPad"] != "0px", before
    assert before["bodyRight"] <= before["trayLeft"] + 1, before
    page.click('[data-testid="chat-collapse"]')
    page.wait_for_timeout(700)
    g = page.evaluate(GEOMETRY)
    assert not g["open"] and g["onScreen"] == 0 and g["visibility"] == "hidden", g
    assert g["dock"] == "0px" and g["tabPad"] == "0px" and g["bodyRight"] == g["vw"], g
    assert g["pill"] and g["pill"]["right"] == g["vw"] and g["pill"]["width"] <= 22, g
    page.click('[data-testid="chat-edge-pill"]')
    page.wait_for_selector(".chat-panel.open", timeout=5000)
    assert page.locator('[data-testid="chat-edge-pill"]').count() == 0


def test_the_key_hides_and_shows_it(open_page):
    page, base = open_page()
    page.goto(base + "/w/news", wait_until="domcontentloaded")
    page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    _open_tray(page)
    page.keyboard.press("Control+Alt+c")
    page.wait_for_timeout(700)
    assert page.evaluate(GEOMETRY)["onScreen"] == 0
    page.keyboard.press("Control+Alt+c")
    page.wait_for_selector(".chat-panel.open", timeout=5000)


def test_a_wide_tray_leaves_nothing_on_screen(open_page):
    page, base = open_page(local={"friday_chat_w": "900"})
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector(".dock-btn", timeout=60000)
    _open_tray(page)
    assert page.evaluate(GEOMETRY)["onScreen"] >= 880
    page.click('[data-testid="chat-collapse"]')
    page.wait_for_timeout(700)
    g = page.evaluate(GEOMETRY)
    assert g["onScreen"] == 0 and g["visibility"] == "hidden", g


def test_reduced_motion_hides_it_at_once(open_page):
    page, base = open_page(reduced=True)
    page.goto(base + "/w/news", wait_until="domcontentloaded")
    page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    _open_tray(page)
    page.click('[data-testid="chat-collapse"]')
    g = page.evaluate(GEOMETRY)
    assert g["duration"].split(",")[0].strip() == "0s" and g["visibility"] == "hidden", g


def test_dragging_the_edge_past_its_narrowest_still_hides_it(open_page):
    page, base = open_page(local={"friday_chat_w": "420"})
    page.goto(base + "/w/news", wait_until="domcontentloaded")
    page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    _open_tray(page)
    h = page.locator(".chat-resize-w").bounding_box()
    x, y = h["x"] + h["width"] / 2, h["y"] + h["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(x + step * 40, y)
    page.mouse.up()
    page.wait_for_timeout(700)
    assert page.evaluate(GEOMETRY)["onScreen"] == 0
    assert page.evaluate("localStorage.getItem('friday_chat_w')") == "420", "its width is kept"


def test_a_tray_the_owner_hid_stays_hidden_when_a_layout_applies_itself(open_page):
    page, base = open_page(local={"friday_chat_hidden": "1"},
                           settings={"workspace_layouts": {"news": "fullscreen_chat"}})
    page.goto(base + "/w/news", wait_until="domcontentloaded")
    page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    page.wait_for_timeout(1500)
    g = page.evaluate(GEOMETRY)
    assert not g["open"] and g["tabPad"] == "0px" and g["pill"], g
