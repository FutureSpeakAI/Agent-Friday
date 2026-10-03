"""The keyboard shortcuts sheet (docs/design/active/unified-shell.md §12).

  * One list, FRIDAY_SHORTCUTS, in a block both pages carry. The sheet shows it
    as it applies where it opens (the desktop, or a workspace's own tab), with
    her name and the dictation key as Settings has them.
  * Every chord the page's key functions accept is on the list, and every key
    listener the page holds across the app is accounted for: its keys are on
    the list, or it is named here with the reason it is not a shortcut.
  * "?" opens it outside a text field and not over a workspace's own "?";
    Esc closes it; the palette lists it.
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

REPO = pathlib.Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
node = shutil.which("node")


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8-sig")


def _block(text, name):
    m = re.search(r"/\* %s:begin \*/\n(.*?)/\* %s:end \*/" % (name, name), text, re.S)
    assert m, name
    return m.group(1)


def test_both_pages_carry_the_same_list():
    assert _block(_read(PAGES[0]), "fridayShortcuts") == _block(_read(PAGES[1]), "fridayShortcuts")


@pytest.mark.parametrize("rel", PAGES)
def test_both_branches_of_the_app_show_it_and_the_palette_lists_it(rel):
    text = _read(rel)
    assert "function ShortcutsSheet(" in text, rel
    assert "fridayShortcutsKey(e)" in text, rel
    assert re.search(r"barCommands,\s*keysCommands\)", text), rel
    assert re.search(r"label:\s*'Keyboard shortcuts'", text), rel
    if rel == "index.html":
        assert text.count("shellPalette, shellShortcuts, shellQuickPost") == 2
    else:
        assert "{shellPalette}{shellShortcuts}{shellQuickPost}" in text
        assert "{shellPalette}\n    {shellShortcuts}" in text


# ── the list and the key functions, in node ─────────────────────────────────

_PROBE = r"""
const NAMES = {ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', ' ': 'Space', Escape: 'Esc'};
const CODES = {'/': 'Slash', '?': 'Slash', ' ': 'Space', ',': 'Comma', '.': 'Period', ';': 'Semicolon',
               '-': 'Minus', '=': 'Equal', '[': 'BracketLeft', ']': 'BracketRight'};
// "?" is Shift and the slash key: listed as "?" when nothing else is held.
const label = e => {
  if (e.key === '?' && !(e.ctrlKey || e.metaKey || e.altKey)) return '?';
  const k = NAMES[e.key] || (e.key.length === 1 ? e.key.toUpperCase() : e.key);
  return [(e.ctrlKey || e.metaKey) && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', k].filter(Boolean).join('+');
};
const keys = 'abcdefghijklmnopqrstuvwxyz0123456789/?,.;-=[] '.split('').concat(
  ['Enter', 'Escape', 'Tab', 'Backspace', 'Delete', 'Home', 'End', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'],
  Array.from({length: 12}, (_, i) => 'F' + (i + 1)));
const accepted = new Set();
for (const key of keys) for (let m = 0; m < 16; m++) {
  if (key === '?' && !(m & 4)) continue;
  const e = {key: m & 4 && key.length === 1 && /[a-z]/.test(key) ? key.toUpperCase() : key,
             code: CODES[key] || (/^[a-z]$/.test(key) ? 'Key' + key.toUpperCase() : /^\d$/.test(key) ? 'Digit' + key : key),
             ctrlKey: !!(m & 1), altKey: !!(m & 2), shiftKey: !!(m & 4), metaKey: !!(m & 8),
             defaultPrevented: false, target: {tagName: 'BODY'}};
  if (fridayLandingKey(e) || fridayTrayKey(e) || fridaySnapKey(e) || fridayShortcutsKey(e)) accepted.add(label(e));
}
const listed = new Set(FRIDAY_SHORTCUTS.flatMap(s => s.keys));
const typed = (key, mods, target, prevented) => fridayShortcutsKey(Object.assign(
  {key, defaultPrevented: !!prevented, target: target || {tagName: 'BODY'}}, mods || {}));
console.log(JSON.stringify({
  accepted: [...accepted].sort(), listed: [...listed],
  desktop: fridayShortcutRows('desktop', 'Juno', {on: true, hotkey: 'alt+t'}),
  tab: fridayShortcutRows('tab', '', {on: false, hotkey: 'ctrl+alt+d'}),
  names: ['AGENT FRIDAY', 'Friday', '', '  ', 'JUNO-B', 'juno'].map(fridayNameInText),
  labels: ['alt+t', 'ctrl+shift+f12', 'f9'].map(fridayKeyLabel),
  groups: [['Ctrl+Alt+←', 'Ctrl+Alt+→'], ['←', '→'], ['Ctrl+K'], ['Ctrl+click'], ['?'],
           ['Ctrl+Alt+↑', 'Shift+Enter']].map(fridayChordGroups),
  scene: [{key: 'ArrowLeft'}, {key: 'ArrowRight', shiftKey: true}, {key: 'ArrowRight', ctrlKey: true},
          {key: 'ArrowUp'}, {key: 'ArrowDown'}, {key: 'Right'}, null].map(e => fridaySceneKey(e)),
  question: {
    plain: typed('?', {shiftKey: true}), ctrl: typed('?', {ctrlKey: true}), alt: typed('?', {altKey: true}),
    meta: typed('?', {metaKey: true}), slash: typed('/'), taken: typed('?', {}, null, true),
    input: typed('?', {}, {tagName: 'INPUT'}), area: typed('?', {}, {tagName: 'TEXTAREA'}),
    select: typed('?', {}, {tagName: 'SELECT'}), editable: typed('?', {}, {tagName: 'DIV', isContentEditable: true})
  }
}));
"""


@pytest.fixture(scope="module")
def probe():
    if not node:
        pytest.skip("node is not on PATH")
    text = _read("index.html")
    script = "".join(_block(text, n) for n in ("fridayLandingJudge", "fridayTrayKeys", "fridaySnap",
                                               "fridayShortcuts")) + _PROBE
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_every_chord_the_key_functions_take_is_on_the_list(probe):
    missing = [c for c in probe["accepted"] if c not in probe["listed"]]
    assert not missing, "keys the page takes that the shortcuts list does not show: %s" % missing


def test_the_list_claims_no_chord_those_functions_do_not_take(probe):
    theirs = ["Ctrl+/", "Ctrl+Shift+Space", "Ctrl+Alt+C", "Ctrl+Alt+←", "Ctrl+Alt+→", "Ctrl+Alt+↑",
              "Ctrl+Alt+↓", "?"]
    assert sorted(theirs) == sorted(probe["accepted"])
    assert all(c in probe["listed"] for c in theirs)


def test_the_rows_are_for_where_it_opens(probe):
    desk, tab = probe["desktop"], probe["tab"]
    assert {r["group"] for r in desk} >= {"Anywhere", "Windows and the chat tray", "On the desktop",
                                          "Anywhere in Windows"}
    assert "On the desktop" not in {r["group"] for r in tab}, "a tab has no dock, scene or widget"
    snap = lambda rows: next(r["what"] for r in rows if r["keys"] == ["Ctrl+Alt+←", "Ctrl+Alt+→"])
    assert snap(desk).startswith("Snap the window in front")
    assert snap(tab).startswith("Put the chat tray on that side")
    assert "Type to Juno: the chat field" in [r["what"] for r in desk][1]
    assert any("Interrupt Friday mid-sentence" in r["what"] for r in tab), "no name: Friday"
    assert not any("{name}" in r["what"] for r in desk + tab)


def test_the_dictation_key_is_the_one_settings_has(probe):
    on = probe["desktop"][-1]
    off = probe["tab"][-1]
    assert on["group"] == off["group"] == "Anywhere in Windows"
    assert on["keys"] == ["Alt+T"] and on["what"].startswith("Hold, speak and let go")
    assert off["keys"] == ["Ctrl+Alt+D"] and "which is off" in off["what"]
    assert probe["labels"] == ["Alt+T", "Ctrl+Shift+F12", "F9"]


def test_alternatives_that_share_their_modifiers_are_written_once(probe):
    assert probe["groups"] == [
        [{"mods": ["Ctrl", "Alt"], "keys": ["←", "→"]}],
        [{"mods": [], "keys": ["←", "→"]}],
        [{"mods": ["Ctrl"], "keys": ["K"]}],
        [{"mods": ["Ctrl"], "keys": ["click"]}],
        [{"mods": [], "keys": ["?"]}],
        [{"mods": ["Ctrl", "Alt"], "keys": ["↑"]}, {"mods": ["Shift"], "keys": ["Enter"]}],
    ]


def test_the_shell_keeps_every_arrow_the_scene_would_take(probe):
    """The scene steps on ← or → whatever is held with it, so while a workspace
    is open the shell keeps all of them (audit K3); up and down were never its."""
    assert probe["scene"] == [True, True, True, False, False, False, False]


def test_her_name_reads_as_a_name_in_a_sentence(probe):
    assert probe["names"] == ["Agent Friday", "Friday", "Friday", "Friday", "Juno-B", "juno"]


def test_question_mark_opens_it_only_where_it_should(probe):
    q = probe["question"]
    assert q["plain"] is True
    assert not any(q[k] for k in ("ctrl", "alt", "meta", "slash", "taken", "input", "area", "select",
                                  "editable"))


# ── every key listener the page holds, accounted for ────────────────────────

#: What a page-wide key listener looks for, and the keys on the list it is.
#: None: it is not a shortcut, for the reason given.
CENSUS = [
    (r"fridaySnapKey\(e\)", ["Ctrl+Alt+←", "Ctrl+Alt+→", "Ctrl+Alt+↑", "Ctrl+Alt+↓"]),
    (r"fridayTrayKey\(e\)", ["Ctrl+Alt+C"]),
    (r"fridayLandingKey\(e\)", ["Ctrl+/", "Ctrl+Shift+Space"]),
    (r"fridayShortcutsKey\(e\)", ["?"]),
    (r"e\.key\.toLowerCase\(\) === 'k'", ["Ctrl+K"]),
    (r"\(e\.key === 'F' \|\| e\.key === 'f'\)", ["Ctrl+Shift+F"]),
    (r"\(e\.key === 'Q' \|\| e\.key === 'q'\)", ["Ctrl+Shift+Q"]),
    (r"e\.key === 'F9'", ["F9"]),
    (r"nextStructure\(\)", ["←", "→"]),
    (r"e\.key !== 'Escape'", ["Esc"]),         # interrupting her while she speaks
    (r"e\.key === 'Escape'", ["Esc"]),         # closing what is open
    (r"\.friday-cite\[data-kw-page\]", None),  # Enter or Space on a focused citation: its own button keys
    (r"\.friday-remote-img\[data-remote-src\]", None),  # Enter or Space on a focused remote-image placeholder: its own button keys
    (r"fridayLandingInput\(", None),           # the landing judge noting that a key was pressed
    (r"fridaySceneKey\(e\)", None),            # keeps the scene's arrows from it while a workspace is open
]


def _listeners(text):
    pat = (r"(?:window|document|document\.documentElement)\.addEventListener\('keydown',\s*"
           r"(function\s*\(e\)\s*\{|[\w$]+)")
    for m in re.finditer(pat, text):
        h = m.group(1)
        if h.startswith("function"):
            body = text[m.end():m.end() + 600]
        else:
            at = max(text.rfind("const %s = " % h, 0, m.start()), text.rfind("function %s(" % h, 0, m.start()))
            assert at >= 0, (h, text.count("\n", 0, m.start()) + 1)
            body = text[at:m.start()]
        yield text.count("\n", 0, m.start()) + 1, h, body


def test_every_key_listener_in_the_page_is_on_the_list_or_named_here():
    text = _read("index.html")
    listed = set(re.findall(r"'([^']+)'", " ".join(re.findall(r"keys: \[([^\]]*)\]",
                                                              _block(text, "fridayShortcuts")))))
    seen, unknown = 0, []
    for line, h, body in _listeners(text):
        seen += 1
        hits = [keys for pat, keys in CENSUS if re.search(pat, body)]
        if not hits:
            unknown.append("line %d (%s)" % (line, h))
        for keys in hits:
            for k in keys or []:
                assert k in listed, "line %d answers %s, which the list does not show" % (line, k)
    assert not unknown, ("a key listener across the page that the shortcuts list does not account "
                         "for: put its keys in FRIDAY_SHORTCUTS, or name it in CENSUS with the reason: %s"
                         % unknown)
    assert seen >= 19


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
    b, base = browser
    made = []

    def make(path="/index.html", settings=None):
        ctx = b.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
        page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                    body=json.dumps({"status": "ok"})))
        page.route("**/api/settings", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"status": "ok", "settings": dict(settings or {})})))
        page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_selector('[data-testid="friday-top-bar"]', timeout=60000)
        page.wait_for_timeout(800)
        made.append((ctx, errors))
        return page

    yield make
    for ctx, errors in made:
        assert not errors, errors
        ctx.close()


def _sheet(page):
    return page.evaluate(r"""() => { const s = document.querySelector('[data-testid="shortcuts-sheet"]');
      if (!s) return null;
      // a row's keys sit on one line and inside their column, whatever the markup
      const rows = Array.from(s.querySelectorAll('.keys-row'));
      const keysOf = r => r.querySelector('.keys-chords');
      const broken = rows.filter(r => keysOf(r).getBoundingClientRect().height > 30).map(r => keysOf(r).innerText);
      const overlap = rows.filter(r => keysOf(r).scrollWidth > keysOf(r).clientWidth + 1).map(r => keysOf(r).innerText);
      return {text: s.innerText, groups: Array.from(s.querySelectorAll('h3')).map(h => h.textContent),
              dialog: s.getAttribute('role'), label: document.getElementById(s.getAttribute('aria-labelledby')).textContent,
              focused: s.contains(document.activeElement), broken, overlap,
              chords: rows.map(r => keysOf(r).innerText.replace(/\s+/g, ''))}; }""")


def test_question_mark_shows_the_keys_and_esc_closes_them(open_page):
    page = open_page(settings={"agent_name": "Juno"})
    page.keyboard.press("?")
    page.wait_for_selector('[data-testid="shortcuts-sheet"]', timeout=5000)
    s = _sheet(page)
    assert s["dialog"] == "dialog" and s["label"] == "Keyboard shortcuts" and s["focused"], s
    assert s["groups"] == ["Anywhere", "Windows and the chat tray", "In a chat box", "On the desktop",
                           "Safety", "Anywhere in Windows"], s["groups"]
    assert "Type to Juno" in s["text"] and "Snap the window in front" in s["text"], s["text"]
    assert not s["broken"] and not s["overlap"], ("a chord broke or ran into its words", s)
    assert "Ctrl+Alt+←/→" in s["chords"] and "Ctrl+Shift+Space" in s["chords"], s["chords"]
    page.keyboard.press("Escape")
    page.wait_for_selector('[data-testid="shortcuts-sheet"]', state="detached", timeout=5000)


def test_a_text_field_keeps_its_question_mark_and_the_palette_opens_the_list(open_page):
    page = open_page()
    page.keyboard.press("Control+k")
    page.wait_for_selector(".cmd-palette-input", timeout=5000)
    page.keyboard.type("?")
    page.wait_for_timeout(200)
    assert _sheet(page) is None
    assert page.input_value(".cmd-palette-input") == "?"
    page.fill(".cmd-palette-input", "keyboard")
    page.click('.cmd-palette-item:has-text("Keyboard shortcuts")')
    page.wait_for_selector('[data-testid="shortcuts-sheet"]', timeout=5000)
    assert page.query_selector(".cmd-palette") is None


def test_in_a_tab_it_lists_what_the_keys_do_there(open_page):
    page = open_page("/w/news", settings={"push_to_transcribe": False})
    page.keyboard.press("?")
    page.wait_for_selector('[data-testid="shortcuts-sheet"]', timeout=5000)
    s = _sheet(page)
    assert "On the desktop" not in s["groups"], s["groups"]
    assert "Put the chat tray on that side" in s["text"] and "Snap the window" not in s["text"], s["text"]
    assert "Push-to-transcribe, which is off" in s["text"], s["text"]
    assert not s["broken"] and not s["overlap"], ("a chord broke or ran into its words", s)
    page.click(".keys-sheet-overlay", position={"x": 12, "y": 900})
    page.wait_for_selector('[data-testid="shortcuts-sheet"]', state="detached", timeout=5000)
