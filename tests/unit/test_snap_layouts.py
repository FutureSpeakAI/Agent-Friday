"""Snap layouts for the chat tray, workspace windows and chat windows
(docs/design/active/unified-shell.md §11.2 to §11.4).

  * One pure function, fridaySnapBox, gives a slot's exact box: halves, thirds
    and two thirds of the snap area, which is the window less the docked tray.
    Slots that meet share an edge to the pixel; tested in node at several
    window sizes with the tray on either side.
  * A window snaps from the layout menu on its maximise button, from Ctrl+Alt
    and the arrows, and by dragging toward an edge (a preview shows first,
    holding steps the size); beside the chat, the window fills the rest
    exactly and the two resize together.
  * The layout is remembered per workspace and per tab, and survives a reload.
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
node = shutil.which("node")


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8-sig")


def _block(text, name):
    m = re.search(r"/\* %s:begin \*/\n(.*?)/\* %s:end \*/" % (name, name), text, re.S)
    assert m, name
    return m.group(1)


def test_both_pages_carry_the_same_snap_block():
    assert _block(_read(PAGES[0]), "fridaySnap") == _block(_read(PAGES[1]), "fridaySnap")


@pytest.mark.parametrize("rel", PAGES)
def test_the_windows_and_the_tray_use_it(rel):
    text = _read(rel)
    fwin = text[text.index("function FWin("):]
    fwin = fwin[:fwin.index("\n}\n")]
    assert "useFridaySnap(id" in fwin and "SnapPreview" in fwin and "SnapMenu" in fwin, rel
    assert "fridaySnapMagnet(fridayDesktopArea()" in fwin, rel
    assert re.search(r"--fr-chat-dock-left'", text) and "fridayLayoutOf(layoutTab" in text, rel
    assert 'data-testid": "chat-layout"' in text or 'data-testid="chat-layout"' in text, rel


def test_a_chat_window_snaps_too():
    text = _read("index.html")
    win = text[text.index("function ConversationWindow("):]
    win = win[:win.index("\n}\n")]
    assert "useFridaySnap('chat:' + convId" in win and "SnapMenu" in win and "SnapPreview" in win


@pytest.mark.parametrize("rel", PAGES)
def test_a_snap_chord_is_taken_before_the_scene_sees_it(rel):
    """The scene steps its structure on an arrow, listening on the document,
    which hears a key before a listener on the window does. The snap chord is
    taken in the capture phase and stopped there."""
    text = _read(rel)
    i = text.index("fridaySnapKey(e)", text.index("/* fridaySnap:end */"))
    effect = text[i:i + 1200]
    assert "stopPropagation()" in effect, rel
    assert re.search(r"addEventListener\('keydown',\s*onKey,\s*true\)", effect), rel
    assert re.search(r"removeEventListener\('keydown',\s*onKey,\s*true\)", effect), rel


# ── the geometry, in node ────────────────────────────────────────────────────

TILINGS = [["left_half", "right_half"], ["left_third", "right_two_thirds"],
           ["left_two_thirds", "right_third"], ["left_third", "middle_third", "right_third"]]


@pytest.fixture(scope="module")
def geometry(tmp_path_factory):
    if not node:
        pytest.skip("node is not on PATH")
    script = _block(_read("index.html"), "fridaySnap") + r"""
const out = {tilings: [], target: [], magnet: [], keys: [], steps: [], tray: [], layouts: []};
for (const vw of [1600, 1366, 1013, 777]) {
  for (const tray of [null, {side: 'right', w: Math.round(vw / 3)}, {side: 'left', w: Math.round(vw / 2)}]) {
    const a = fridaySnapArea(vw, 1000, 36, 912, tray);
    for (const t of %s) out.tilings.push({vw, tray, area: a, boxes: t.map(s => fridaySnapBox(a, s)), full: fridaySnapBox(a, 'full')});
  }
}
const a = fridaySnapArea(1600, 1000, 36, 1000, null);
for (const [x, y, held] of [[5, 500, 0], [5, 500, 700], [5, 500, 1400], [5, 500, 2100], [1595, 500, 0], [1595, 500, 750], [800, 40, 0], [800, 500, 0]])
  out.target.push(fridaySnapTarget(a, x, y, held));
for (const b of [{x: 7, y: 300, w: 600, h: 400}, {x: 993, y: 45, w: 600, h: 400}, {x: 200, y: 300, w: 600, h: 400}])
  out.magnet.push(fridaySnapMagnet(a, b));
for (const e of [{ctrlKey: true, altKey: true, key: 'ArrowLeft'}, {ctrlKey: true, altKey: true, key: 'ArrowUp'},
                 {ctrlKey: true, key: 'ArrowLeft'}, {ctrlKey: true, altKey: true, shiftKey: true, key: 'ArrowLeft'}])
  out.keys.push(fridaySnapKey(e));
for (const [cur, dir] of [[null, 'left'], ['left_half', 'left'], ['left_two_thirds', 'left'], ['left_third', 'left'],
                          ['left_half', 'right'], ['right_third', 'up'], ['full', 'down']])
  out.steps.push(fridaySnapStep(cur, dir));
for (const [frac, side, dir] of [[1 / 3, 'right', 'right'], [1 / 2, 'right', 'right'], [2 / 3, 'right', 'right'], [0.4, 'right', 'left']])
  out.tray.push(fridayTrayStep(frac, side, dir));
for (const v of ['fullscreen_chat', {window: 'left_half'}, {window: 'full', chat: {side: 'left', frac: 0.5}},
                 {window: 'nowhere'}, {chat: {side: 'up', frac: 3}}, null, 'something'])
  out.layouts.push(fridayLayoutOf(v));
console.log(JSON.stringify(out));
""" % json.dumps(TILINGS)
    p = tmp_path_factory.mktemp("snap") / "geometry.js"
    p.write_text(script, encoding="utf-8")
    cp = subprocess.run([node, str(p)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    return json.loads(cp.stdout)


def test_slots_tile_the_area_exactly_with_the_tray_on_either_side(geometry):
    assert len(geometry["tilings"]) == 4 * 3 * len(TILINGS)
    for t in geometry["tilings"]:
        a, boxes = t["area"], t["boxes"]
        assert boxes[0]["x"] == a["x"], t
        assert boxes[-1]["x"] + boxes[-1]["w"] == a["x"] + a["w"], t
        for left, right in zip(boxes, boxes[1:]):
            assert left["x"] + left["w"] == right["x"], ("a gap or an overlap", t)
        assert all(b["y"] == 36 and b["h"] == 912 - 36 for b in boxes), t
        assert t["full"] == a
        tray = t["tray"]
        if tray and tray["side"] == "right":
            assert a["x"] == 0 and a["w"] == t["vw"] - tray["w"], t
        if tray and tray["side"] == "left":
            assert a["x"] == tray["w"] and a["w"] == t["vw"] - tray["w"], t


def test_halves_and_thirds_are_what_they_say(geometry):
    t = next(x for x in geometry["tilings"] if x["vw"] == 1600 and not x["tray"] and len(x["boxes"]) == 3)
    assert [b["w"] for b in t["boxes"]] == [533, 534, 533]
    t = next(x for x in geometry["tilings"] if x["vw"] == 1600 and not x["tray"] and x["boxes"][0]["w"] == 800)
    assert [b["x"] for b in t["boxes"]] == [0, 800]


def test_dragging_toward_an_edge_and_holding_steps_the_size(geometry):
    assert geometry["target"] == ["left_half", "left_two_thirds", "left_third", "left_half",
                                  "right_half", "right_two_thirds", "full", None]


def test_a_free_windows_edges_are_magnetic(geometry):
    assert geometry["magnet"] == [{"x": 0, "y": 300, "w": 600, "h": 400},
                                  {"x": 1000, "y": 36, "w": 600, "h": 400},
                                  {"x": 200, "y": 300, "w": 600, "h": 400}]


def test_the_keys_and_their_steps(geometry):
    assert geometry["keys"] == ["left", "up", None, None]
    assert geometry["steps"] == ["left_half", "left_two_thirds", "left_third", "left_half",
                                 "right_half", "full", None]
    third, half, two = 1 / 3, 1 / 2, 2 / 3
    assert [round(t["frac"], 4) for t in geometry["tray"]] == [round(half, 4), round(two, 4), round(third, 4), round(third, 4)]
    assert [t["side"] for t in geometry["tray"]] == ["right", "right", "right", "left"]


def test_a_remembered_layout_is_read_carefully(geometry):
    assert geometry["layouts"] == [{"window": "full", "chat": {}}, {"window": "left_half", "chat": None},
                                   {"window": "full", "chat": {"side": "left", "frac": 0.5}},
                                   None, {"window": "full", "chat": {}}, None, None]


# ── the tools ────────────────────────────────────────────────────────────────

@pytest.fixture
def pages():
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def _answer(q, reply):
    seen = []

    def page():
        msg = q.get(timeout=5)
        seen.append(msg)
        desktop_bus.ack(msg["id"], reply)

    t = threading.Thread(target=page, daemon=True)
    t.start()
    return seen, t


def test_put_chat_on_the_right_third(pages, monkeypatch):
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "set_chat_tray")
    assert spec[2]["size"][0] == "string" and "right third" in spec[1]
    tool = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "set_chat_tray")
    assert tool["input_schema"]["properties"]["size"]["enum"] == ["third", "half", "two_thirds"]
    monkeypatch.setattr(agent, "CHAT_TRAY_ACK_S", 5.0)
    q = desktop_bus.subscribe("page-1", "chat")
    seen, t = _answer(q, {"applied": True, "shown": True, "side": "right", "size": "third"})
    out = agent._tool_set_chat_tray({"side": "right", "size": "third"})
    t.join(5)
    assert seen[0]["side"] == "right" and seen[0]["size"] == "third"
    assert out == "CHAT_OK — the chat is open on the right, a third of the screen."
    assert agent._tool_set_chat_tray({"size": "most"}).startswith("CHAT_FAIL")


def test_put_a_workspace_on_the_left_two_thirds(pages):
    from agent_friday.core import _load_settings, _save_settings
    spec = next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == "set_workspace_layout")
    assert spec[2]["position"][0] == "string" and spec[3] == ["fullscreen_chat"]
    out = agent._tool_set_workspace_layout({"workspace": "news", "fullscreen_chat": False,
                                            "position": "left two thirds"})
    assert out.startswith("LAYOUT_SAVED:news") and "the left two thirds" in out, out
    assert _load_settings()["workspace_layouts"]["news"] == {"window": "left_two_thirds"}
    assert agent._tool_set_workspace_layout({"workspace": "news", "fullscreen_chat": False,
                                             "position": "top"}).startswith("LAYOUT_FAIL")
    _save_settings({"workspace_layouts": {}})


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


class _Site:
    """A page of this tree, its settings kept here across reloads."""

    def __init__(self, browser, settings=None, local=None):
        b, self.base = browser
        self.settings, self.saved, self.errors = dict(settings or {}), [], []
        self.ctx = b.new_context(viewport={"width": 1600, "height": 1000})
        if local:
            self.ctx.add_init_script("(() => { const s = %s; for (const k in s) localStorage.setItem(k, s[k]); })()"
                                     % json.dumps(local))
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
        self.page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                         body=json.dumps({"status": "ok"})))
        self.page.route("**/api/settings", self._settings)

    def _settings(self, route):
        if route.request.method == "POST":
            body = (route.request.post_data_json or {}).get("settings") or {}
            self.saved.append(body)
            self.settings.update(body)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"status": "ok", "settings": self.settings}))

    def desktop(self):
        self.page.goto(self.base + "/index.html", wait_until="domcontentloaded")
        self.page.wait_for_selector('.dock-btn[data-ws="news"]', timeout=60000)
        self.page.wait_for_timeout(500)
        return self

    def open_news(self):
        self.page.click('.dock-btn[data-ws="news"]')
        self.page.wait_for_selector('.fwin', timeout=15000)
        self.page.wait_for_timeout(700)
        return self

    def boxes(self):
        return self.page.evaluate("""() => {
          const r = s => { const e = document.querySelector(s); return e ? e.getBoundingClientRect().toJSON() : null; };
          const w = document.querySelector('.fwin');
          const area = fridayDesktopArea();
          return {win: r('.fwin'), snap: w && w.dataset.snap || null, tray: r('.chat-panel.open'),
                  trayLeft: !!document.querySelector('.chat-panel.open.left'), area, vw: innerWidth,
                  menu: !!document.querySelector('[data-testid="snap-menu"]'),
                  preview: (document.querySelector('[data-testid="snap-preview"]') || {dataset: {}}).dataset.slot || null};
        }""")

    def menu(self, selector='[data-fwin-max="news"]'):
        self.page.hover(selector)
        self.page.wait_for_selector('[data-testid="snap-menu"]', timeout=5000)
        return self

    def close(self):
        assert not self.errors, self.errors
        self.ctx.close()


@pytest.fixture
def site(browser):
    made = []

    def make(**kw):
        s = _Site(browser, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def _exact(box, want):
    assert box is not None
    for k in ("x", "y", "width", "height"):
        assert abs(box[k] - want[k]) < 0.6, (k, box, want)


def test_the_menu_snaps_a_window_to_an_exact_half(site):
    s = site().desktop().open_news().menu()
    s.page.click('[data-testid="snap-menu"] [data-snap="left_half"]')
    s.page.wait_for_timeout(500)
    g = s.boxes()
    a = g["area"]
    assert g["snap"] == "left_half" and not g["menu"], g
    _exact(g["win"], {"x": 0, "y": a["y"], "width": round(a["w"] / 2), "height": a["h"]})
    assert {"workspace_layouts": {"news": {"window": "left_half"}}} in s.saved, s.saved


def test_beside_the_chat_the_window_fills_the_rest_exactly(site):
    s = site().desktop().open_news().menu()
    s.page.click('[data-testid="snap-menu"] [aria-label="This window in two thirds, the chat beside it"]')
    s.page.wait_for_selector(".chat-panel.open", timeout=5000)
    s.page.wait_for_timeout(900)
    g = s.boxes()
    assert g["snap"] == "full", g
    assert round(g["tray"]["width"]) == round(1600 / 3), g
    assert g["win"]["x"] == 0 and abs(g["win"]["x"] + g["win"]["width"] - g["tray"]["x"]) < 0.6, ("no gutter", g)
    saved = s.saved[-1]["workspace_layouts"]["news"]
    assert saved["window"] == "full" and saved["chat"]["side"] == "right" and abs(saved["chat"]["frac"] - 0.333) < 0.002
    # the two resize together: the tray's edge moves the window's
    h = s.page.locator(".chat-resize-w").bounding_box()
    x, y = h["x"] + h["width"] / 2, h["y"] + h["height"] / 2
    s.page.mouse.move(x, y)
    s.page.mouse.down()
    for step in range(1, 7):
        s.page.mouse.move(x - step * 20, y)
    s.page.mouse.up()
    s.page.wait_for_timeout(700)
    g2 = s.boxes()
    assert g2["tray"]["width"] > g["tray"]["width"] + 100, (g, g2)
    assert abs(g2["win"]["x"] + g2["win"]["width"] - g2["tray"]["x"]) < 0.6, ("they moved together", g2)


def test_the_keys_step_a_window_through_the_slots(site):
    s = site().desktop().open_news()
    s.page.click(".fwin .fwin-title")
    seen = []
    for key in ("Control+Alt+ArrowRight", "Control+Alt+ArrowRight", "Control+Alt+ArrowRight",
                "Control+Alt+ArrowUp", "Control+Alt+ArrowDown"):
        s.page.keyboard.press(key)
        s.page.wait_for_timeout(300)
        seen.append(s.boxes()["snap"])
    assert seen == ["right_half", "right_two_thirds", "right_third", "full", None]


_SPY_ON_THE_SCENE = """() => { window.__steps = [];
  window.fridayVibe.nextStructure = () => window.__steps.push('next');
  window.fridayVibe.prevStructure = () => window.__steps.push('prev'); }"""


def test_the_snap_keys_leave_the_scene_alone(site):
    """A plain arrow is the scene's own key (its next structure); a snap chord
    belongs to the window or the tray and never reaches the scene."""
    s = site().desktop().open_news()
    s.page.evaluate(_SPY_ON_THE_SCENE)
    s.page.click(".fwin .fwin-title")
    s.page.keyboard.press("Control+Alt+ArrowRight")
    s.page.wait_for_timeout(300)
    assert s.boxes()["snap"] == "right_half"
    assert s.page.evaluate("window.__steps") == []
    s.page.keyboard.press("ArrowRight")
    s.page.wait_for_timeout(150)
    assert s.page.evaluate("window.__steps") == ["next"], "the scene still has its own key"
    # in a tab the chord moves the tray, and the scene there is left alone too
    s.page.goto(s.base + "/w/news", wait_until="domcontentloaded")
    s.page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    s.page.wait_for_timeout(500)
    s.page.evaluate(_SPY_ON_THE_SCENE)
    s.page.keyboard.press("Control+Alt+ArrowLeft")
    s.page.wait_for_selector(".chat-panel.open.left", timeout=5000)
    assert s.page.evaluate("window.__steps") == []


def test_dragging_to_an_edge_previews_holds_and_snaps(site):
    s = site().desktop().open_news()
    bar = s.page.locator(".fwin .fwin-title").bounding_box()
    s.page.mouse.move(bar["x"] + 40, bar["y"] + bar["height"] / 2)
    s.page.mouse.down()
    for x in (300, 150, 60, 4):
        s.page.mouse.move(x, 420)
    s.page.wait_for_timeout(150)
    assert s.boxes()["preview"] == "left_half"
    s.page.wait_for_timeout(800)
    assert s.boxes()["preview"] == "left_two_thirds"
    s.page.mouse.up()
    s.page.wait_for_timeout(400)
    g = s.boxes()
    assert g["snap"] == "left_two_thirds" and g["preview"] is None, g
    _exact(g["win"], {"x": 0, "y": g["area"]["y"], "width": round(g["area"]["w"] * 2 / 3), "height": g["area"]["h"]})
    # dragged away, it is free again at its own size
    bar = s.page.locator(".fwin .fwin-title").bounding_box()
    s.page.mouse.move(bar["x"] + 40, bar["y"] + bar["height"] / 2)
    s.page.mouse.down()
    for x in (300, 500, 700):
        s.page.mouse.move(x, 400)
    s.page.mouse.up()
    s.page.wait_for_timeout(400)
    assert s.boxes()["snap"] is None


def test_a_layout_survives_a_reload(site):
    s = site().desktop().open_news().menu()
    s.page.click('[data-testid="snap-menu"] [data-snap="right_third"]')
    s.page.wait_for_timeout(400)
    s.page.reload(wait_until="domcontentloaded")
    s.page.wait_for_selector('.dock-btn[data-ws="news"]', timeout=60000)
    s.page.wait_for_timeout(500)
    s.open_news()
    g = s.boxes()
    assert g["snap"] == "right_third", g
    a = g["area"]
    _exact(g["win"], {"x": a["x"] + round(a["w"] * 2 / 3), "y": a["y"],
                      "width": a["w"] - round(a["w"] * 2 / 3), "height": a["h"]})


def test_a_tab_keeps_its_own_layout(site):
    s = site(settings={"workspace_layouts": {"tab:news": {"window": "full", "chat": {"side": "left", "frac": 0.5}},
                                             "news": {"window": "left_third"}}})
    s.page.goto(s.base + "/w/news", wait_until="domcontentloaded")
    s.page.wait_for_selector(".chat-panel.open.left", timeout=60000)
    s.page.wait_for_timeout(700)
    got = s.page.evaluate("""() => ({tray: document.querySelector('.chat-panel.open').getBoundingClientRect().toJSON(),
      body: document.querySelector('.ws-tab-body').getBoundingClientRect().toJSON(),
      pad: getComputedStyle(document.querySelector('.ws-tab')).paddingLeft})""")
    assert got["tray"]["x"] == 0 and round(got["tray"]["width"]) == 800, got
    assert got["pad"] == "800px" and abs(got["body"]["x"] - 800) < 0.6, got


def test_the_trays_menu_puts_it_on_the_left_half(site):
    s = site()
    s.page.goto(s.base + "/w/news", wait_until="domcontentloaded")
    s.page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    s.page.click('[data-testid="friday-top-bar"] [aria-label="Open chat with Friday"]')
    s.page.wait_for_selector(".chat-panel.open", timeout=10000)
    s.page.wait_for_timeout(500)
    s.menu('[data-testid="chat-layout"]')
    s.page.click('[data-testid="snap-menu"] [aria-label="Chat on the left, a half"]')
    s.page.wait_for_timeout(700)
    g = s.boxes()
    assert g["trayLeft"] and g["tray"]["x"] == 0 and round(g["tray"]["width"]) == 800, g
    assert s.saved[-1]["workspace_layouts"]["tab:news"]["chat"] == {"side": "left", "frac": 0.5}


def test_a_chat_window_snaps_by_its_menu_and_the_keys(site):
    s = site().desktop()
    s.page.evaluate("window.fridayOpenChatWindow('c-test', 'A test thread')")
    s.page.wait_for_selector('[data-chat-window]', timeout=10000)
    s.page.wait_for_timeout(400)
    s.menu('[data-testid="chat-win-snap"]')
    s.page.click('[data-testid="snap-menu"] [data-snap="right_half"]')
    s.page.wait_for_timeout(400)
    box = s.page.evaluate("""() => { const w = document.querySelector('[data-chat-window]');
      return {snap: w.dataset.snap, r: w.getBoundingClientRect().toJSON(), a: fridayDesktopArea()}; }""")
    assert box["snap"] == "right_half", box
    _exact(box["r"], {"x": box["a"]["x"] + round(box["a"]["w"] / 2), "y": box["a"]["y"],
                      "width": box["a"]["w"] - round(box["a"]["w"] / 2), "height": box["a"]["h"]})
    s.page.keyboard.press("Control+Alt+ArrowLeft")
    s.page.wait_for_timeout(300)
    assert s.page.evaluate("document.querySelector('[data-chat-window]').dataset.snap") == "left_half"
