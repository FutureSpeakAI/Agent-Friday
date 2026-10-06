"""Snap layouts for the chat tray, workspace windows and chat windows
(docs/design/active/unified-shell.md §11.2 to §11.4).

  * One pure function, fridaySnapBox, gives a slot's exact box: halves, thirds
    and two thirds of the snap area, which is the window less the docked tray.
    Slots that meet share an edge to the pixel; tested in node at several
    window sizes with the tray on either side.
  * A window snaps from the layout menu on its maximise button, from Ctrl+Alt
    and the arrows, and by dragging toward an edge (a preview shows first,
    holding steps the size); beside the chat, the window fills the available
    work region exactly and the two resize together. The spatial desktop
    reserves a separate avatar stage; classic mode retains its original area.
  * The layout is remembered per workspace and per tab, and survives a reload.
"""
from __future__ import annotations

import datetime as _dt
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
    assert "useFridaySnap(id" in fwin and "SnapPreview" in fwin and "WsTools" in fwin, rel
    tools = text[text.index("function WsTools("):]
    tools = tools[:tools.index("\n}\n")]
    assert "SnapMenu" in tools and "arrange.onPick" in tools and "arrange.onToggle" in tools, rel
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

    def __init__(self, browser, settings=None, local=None, classic=False):
        b, self.base = browser
        self.classic = classic
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
        self.page.route("**/api/setup/status", lambda r: r.fulfill(status=200, content_type="application/json",
                                                                    body=json.dumps({"initialized": True})))
        self.page.route("**/api/settings", self._settings)

    def _settings(self, route):
        if route.request.method == "POST":
            body = (route.request.post_data_json or {}).get("settings") or {}
            self.saved.append(body)
            self.settings.update(body)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"status": "ok", "settings": self.settings}))

    def desktop(self):
        self.page.goto(self.base + "/index.html" + ("?experience=classic" if self.classic else ""),
                       wait_until="domcontentloaded")
        self.page.wait_for_selector('.dock-btn[data-ws="news"]', timeout=60000)
        if not self.classic:
            self.page.wait_for_selector('.fx-shell', timeout=15000)
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
          const cs = getComputedStyle(document.documentElement);
          const right = parseFloat(cs.getPropertyValue('--fr-chat-dock')) || 0;
          const left = parseFloat(cs.getPropertyValue('--fr-chat-dock-left')) || 0;
          const dock = r('.dock:not(.hidden)');
          const rawArea = fridaySnapArea(innerWidth, innerHeight,
            parseFloat(cs.getPropertyValue('--fr-topbar-h')) || 36,
            dock && dock.height ? Math.round(dock.top) : innerHeight,
            left ? {side:'left',w:left} : right ? {side:'right',w:right} : null);
          return {win: r('.fwin'), snap: w && w.dataset.snap || null, tray: r('.chat-panel.open'),
                  trayLeft: !!document.querySelector('.chat-panel.open.left'), rail: r('.fx-rail'), area, rawArea,
                  topbar: r('[data-testid="friday-top-bar"]'),
                  arrangement: document.body.dataset.fridayHoloArrangement, vw: innerWidth,
                  spatial: window.FridayHolographicWorkspace && window.FridayHolographicWorkspace.state.spatial,
                  menu: !!document.querySelector('[data-testid="snap-menu"]'),
                  preview: (document.querySelector('[data-testid="snap-preview"]') || {dataset: {}}).dataset.slot || null};
        }""")

    def menu(self, selector='[data-fwin-max="news"]'):
        if selector.startswith('[data-fwin-max='):
            self.page.click(selector)
        else:
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


def _assert_desktop_edge(g, classic=False):
    assert g["rail"] is None, g
    if classic:
        assert g["area"] == g["rawArea"], g
        assert g["spatial"]["stage"] is None, g
    else:
        assert g["topbar"] and g["topbar"]["height"] > 0, g
        base = g["rawArea"]
        stage, area = g["spatial"]["stageFrame"], g["area"]
        avatar, caption = g["spatial"]["stage"], g["spatial"]["caption"]
        # The reserved scene frame includes a separate, readable state caption.
        assert avatar["x"] == stage["x"] and avatar["w"] == stage["w"], g
        assert avatar["y"] == stage["y"] and avatar["h"] > 0, g
        assert avatar["y"] + avatar["h"] <= caption["y"], g
        assert caption["x"] >= stage["x"] and caption["x"] + caption["w"] <= stage["x"] + stage["w"], g
        assert caption["y"] + caption["h"] <= stage["y"] + stage["h"], g
        gutter = 8 if g["vw"] < 760 else 16
        assert stage and stage["w"] > 0 and stage["h"] > 0, g
        assert stage["y"] == base["y"] + 12, g
        if g["spatial"]["layout"] == "compact":
            assert stage["x"] == area["x"] == gutter, g
            assert stage["w"] == area["w"] == g["vw"] - 2 * gutter, g
            assert area["y"] == stage["y"] + stage["h"] + 16, g
            assert stage["h"] + area["h"] + 16 == base["h"] - 24, g
        else:
            assert area["y"] == stage["y"], g
            assert area["h"] == stage["h"] == base["h"] - 24, g
            assert area["w"] >= 440, g
            assert area["w"] + stage["w"] + 16 == base["w"] - 2 * gutter, g
            if g["tray"] and not g["trayLeft"]:
                assert stage["x"] == base["x"] + gutter, g
                assert area["x"] == stage["x"] + stage["w"] + 16, g
            else:
                assert area["x"] == base["x"] + gutter, g
                assert stage["x"] == area["x"] + area["w"] + 16, g


@pytest.mark.parametrize("classic", [False, True], ids=["spatial", "classic"])
def test_the_menu_snaps_a_window_to_an_exact_half(site, classic):
    s = site(classic=classic).desktop().open_news().menu()
    s.page.click('[data-testid="snap-menu"] [data-snap="left_half"]')
    s.page.wait_for_timeout(500)
    g = s.boxes()
    a = g["area"]
    _assert_desktop_edge(g, classic)
    assert g["snap"] == "left_half" and not g["menu"], g
    _exact(g["win"], {"x": a["x"], "y": a["y"], "width": round(a["w"] / 2), "height": a["h"]})
    assert {"workspace_layouts": {"news": {"window": "left_half"}}} in s.saved, s.saved


@pytest.mark.parametrize("classic", [False, True], ids=["spatial", "classic"])
def test_beside_the_chat_the_window_fills_the_rest_exactly(site, classic):
    s = site(classic=classic).desktop().open_news().menu()
    s.page.click('[data-testid="snap-menu"] [aria-label="This window in two thirds, the chat beside it"]')
    s.page.wait_for_selector(".chat-panel.open", timeout=5000)
    s.page.wait_for_timeout(900)
    g = s.boxes()
    _assert_desktop_edge(g, classic)
    assert g["snap"] == "full", g
    assert round(g["tray"]["width"]) == round(1600 / 3), g
    assert g["win"]["x"] == g["area"]["x"], g
    _exact(g["win"], {"x": g["area"]["x"], "y": g["area"]["y"], "width": g["area"]["w"], "height": g["area"]["h"]})
    assert g["win"]["x"] + g["win"]["width"] <= g["tray"]["x"] + 0.6, ("chat and workspace never overlap", g)
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
    _assert_desktop_edge(g2, classic)
    assert g2["win"]["x"] == g2["area"]["x"], g2
    assert g2["tray"]["width"] > g["tray"]["width"] + 100, (g, g2)
    _exact(g2["win"], {"x": g2["area"]["x"], "y": g2["area"]["y"], "width": g2["area"]["w"], "height": g2["area"]["h"]})
    assert g2["win"]["x"] + g2["win"]["width"] < g["win"]["x"] + g["win"]["width"], ("window follows the enlarged chat", g, g2)


def test_left_docked_chat_keeps_top_navigation_and_window_separate(site):
    s = site().desktop().open_news().menu()
    s.page.click('[data-testid="snap-menu"] [data-snap="full"]')
    s.page.locator('.top-bar button[aria-label^="Open chat with"]').click()
    s.page.wait_for_selector(".chat-panel.open", timeout=10000)
    s.page.wait_for_timeout(500)
    s.menu('[data-testid="chat-layout"]')
    s.page.click('[data-testid="snap-menu"] [aria-label="Chat on the left, a half"]')
    s.page.wait_for_timeout(900)
    g = s.boxes()
    _assert_desktop_edge(g)
    assert g["trayLeft"] and g["tray"]["x"] == 0 and round(g["tray"]["width"]) == 800, g
    assert g["win"]["x"] == g["tray"]["right"] + 16, ("window starts after chat and desktop gutter", g)
    assert g["snap"] == "full", g
    a = g["area"]
    _exact(g["win"], {"x": a["x"], "y": a["y"], "width": a["w"], "height": a["h"]})


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
    """A plain arrow is the scene's own key (its next structure) while no
    workspace is open; a snap chord belongs to the window or the tray and never
    reaches the scene."""
    s = site().desktop()
    s.page.evaluate(_SPY_ON_THE_SCENE)
    s.page.keyboard.press("ArrowRight")
    s.page.wait_for_timeout(150)
    assert s.page.evaluate("window.__steps") == ["next"], "with no workspace open the scene has its own key"
    s.open_news()
    s.page.click(".fwin .fwin-title")
    s.page.keyboard.press("Control+Alt+ArrowRight")
    s.page.wait_for_timeout(300)
    assert s.boxes()["snap"] == "right_half"
    assert s.page.evaluate("window.__steps") == ["next"]
    # in a tab the chord moves the tray, and the scene there is left alone too
    s.page.goto(s.base + "/w/news", wait_until="domcontentloaded")
    s.page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    s.page.wait_for_timeout(500)
    s.page.evaluate(_SPY_ON_THE_SCENE)
    s.page.keyboard.press("Control+Alt+ArrowLeft")
    s.page.wait_for_selector(".chat-panel.open.left", timeout=5000)
    assert s.page.evaluate("window.__steps") == []


def test_while_a_workspace_is_open_the_arrows_stay_out_of_the_scene(site):
    """Audit K3. With no workspace open a bare arrow steps the scene, as it
    always has. While one is open the shell keeps the arrows: the workspace
    still hears them, the scene behind it does not. Closing it gives them back."""
    s = site().desktop()
    s.page.evaluate(_SPY_ON_THE_SCENE)
    s.page.keyboard.press("ArrowRight")
    s.page.wait_for_timeout(150)
    assert s.page.evaluate("window.__steps") == ["next"]
    s.open_news()
    s.page.evaluate("""() => { window.__heard = [];
      const w = document.querySelector('.fwin'); w.tabIndex = -1; w.focus();
      w.addEventListener('keydown', e => window.__heard.push(e.key)); }""")
    for key in ("ArrowRight", "ArrowLeft", "Shift+ArrowRight"):
        s.page.keyboard.press(key)
    s.page.wait_for_timeout(150)
    assert s.page.evaluate("window.__steps") == ["next"], "the scene stepped behind an open workspace"
    assert s.page.evaluate("window.__heard") == ["ArrowRight", "ArrowLeft", "Shift", "ArrowRight"], (
        "the workspace must still hear its keys")
    s.page.click('.dock-btn[data-ws="news"]')
    s.page.wait_for_selector(".fwin", state="detached", timeout=5000)
    s.page.keyboard.press("ArrowLeft")
    s.page.wait_for_timeout(150)
    assert s.page.evaluate("window.__steps") == ["next", "prev"], "closed, the scene has its keys back"


def test_in_a_workspace_tab_the_arrows_stay_out_of_the_scene(site):
    s = site()
    s.page.goto(s.base + "/w/news", wait_until="domcontentloaded")
    s.page.wait_for_selector('[data-standalone="news"]', timeout=60000)
    s.page.wait_for_timeout(500)
    s.page.evaluate(_SPY_ON_THE_SCENE)
    s.page.keyboard.press("ArrowRight")
    s.page.keyboard.press("ArrowLeft")
    s.page.wait_for_timeout(150)
    assert s.page.evaluate("window.__steps") == []


@pytest.mark.parametrize("classic", [False, True], ids=["spatial", "classic"])
def test_dragging_to_an_edge_previews_holds_and_snaps(site, classic):
    s = site(classic=classic).desktop().open_news()
    edge = s.boxes()["area"]["x"]
    bar = s.page.locator(".fwin .fwin-title").bounding_box()
    # The edge-hold dwell steps on a clock the test moves, so each size is
    # reached at its own time however slow the machine (FRIDAY_SNAP_HOLD_MS).
    s.page.clock.install()
    s.page.clock.pause_at(_dt.datetime.now() + _dt.timedelta(seconds=1))   # time stands still between steps
    s.page.mouse.move(bar["x"] + 40, bar["y"] + bar["height"] / 2)
    s.page.mouse.down()
    for x in (edge + 300, edge + 150, edge + 60, edge + 4):
        s.page.mouse.move(x, 420)
    s.page.clock.run_for(150)
    s.page.wait_for_timeout(50)
    assert s.boxes()["preview"] == "left_half"
    s.page.clock.run_for(800)
    s.page.wait_for_timeout(50)
    assert s.boxes()["preview"] == "left_two_thirds"
    s.page.mouse.up()
    s.page.clock.run_for(400)
    s.page.wait_for_timeout(50)
    g = s.boxes()
    _assert_desktop_edge(g, classic)
    assert g["snap"] == "left_two_thirds" and g["preview"] is None, g
    _exact(g["win"], {"x": g["area"]["x"], "y": g["area"]["y"], "width": round(g["area"]["w"] * 2 / 3), "height": g["area"]["h"]})
    # dragged away, it is free again at its own size
    bar = s.page.locator(".fwin .fwin-title").bounding_box()
    s.page.mouse.move(bar["x"] + 40, bar["y"] + bar["height"] / 2)
    s.page.mouse.down()
    for x in (300, 500, 700):
        s.page.mouse.move(x, 400)
    s.page.mouse.up()
    s.page.clock.run_for(400)
    s.page.wait_for_timeout(50)
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
