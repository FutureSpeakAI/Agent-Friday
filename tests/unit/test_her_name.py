"""Every line of the interface that speaks of her reads her name as the owner
gave it; the product stays Agent Friday(TM) (audit Y1, docs/brand/BRAND.md).

  * fridayName() (block fridayHerName, both pages) reads her name through
    fridayNameInText; App keeps it current once settings come from the server,
    and the last name seen is kept in the browser so a reload does not flash the
    default. FRIDAY_BRAND.name is the product.
  * No string in the page, Mail or the 3D views names her "Friday" by hand. The
    ones that still say Friday are listed below with their reasons: a weekday,
    a value compared in code, her name's own default, a log, and the scene's and
    the avatar's own lines (the avatar session's code). A new one fails here.
"""
from __future__ import annotations

import collections
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
WORKSPACE_SCRIPTS = ("static/friday_mail.js", "static/friday3d_records.js")
node = shutil.which("node")
BS = chr(92)

#: A bare "Friday": not "Agent Friday", not part of a longer word.
BARE = re.compile(r"(?<![A-Za-z0-9_$\-])(?<!Agent )(?<!Agent\xa0)Friday(?![A-Za-z0-9_$])")

#: The strings that still say Friday, as written, and how many of each.
ALLOWED = {
    "index.html": {
        f" {BS}u2014 Friday is standing back": 1,                       # the scene's (avatar session)
        "Friday itself is unaffected — chat, voice and tools all still work. ": 1,  # the scene's
        # fridayNameInText's own fallback; the weekday in Workflows' day list;
        # the name field's default and its example
        "Friday": 4,
        "Every Friday at 4pm, pull together the reader tips from my inbox and sort them by beat": 1,
        f" writes one every Friday at 7 PM Central {BS}u2014 her one fully independent space.": 1,
        "this Friday, through another program": 1,                      # a value the server sends
        # the avatar's Evolution section (the avatar session's code)
        "Friday has a new look.": 1,
        "A frontier model (Friday picks)": 1,
        "No model: Friday’s own changes": 1,
        "Friday, no model": 1,
        "Who makes Friday’s looks": 1,
        "Back to how Friday first looked.": 1,
        "By voice: “Friday, evolve now”, “undo that look”, “go back to last month’s look”, “what changed?”": 1,
        "VOICE_DEBUG: mic MUTED (Friday speaking, worklet=": 1,         # a debug log
    },
    # the default where a page gives no name (a fixture, a test)
    "static/friday_mail.js": {"Friday": 1},
    "static/friday3d_records.js": {"Friday": 1},
}


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8-sig")


# ── the string and template pieces of compiled page code ─────────────────────

KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw",
            "case", "do", "else", "yield", "await"}
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
WORD = re.compile(r"[\w$]+")


def _literals(src):
    """[(offset, raw text)] of every string and template piece, skipping
    comments and regular-expression literals. For compiled code (no JSX)."""
    out, i, n, prev, stack = [], 0, len(src), "", []
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == BS else 1
            out.append((i + 1, src[i + 1:j]))
            i, prev = j + 1, "lit"
            continue
        if c == "`" or (c == "}" and stack and stack[-1] == "`"):
            if c == "}":
                stack.pop()
            j = start = i + 1
            while j < n:
                if src[j] == BS:
                    j += 2
                    continue
                if src[j] == "`":
                    out.append((start, src[start:j]))
                    i, prev = j + 1, "lit"
                    break
                if src.startswith("${", j):
                    out.append((start, src[start:j]))
                    stack.append("`")
                    i, prev = j + 2, "{"
                    break
                j += 1
            else:
                i = n
            continue
        if c == "/":
            if prev in REGEX_AFTER or prev in KEYWORDS or prev == "":
                j, in_class = i + 1, False
                while j < n and src[j] != "\n":
                    ch = src[j]
                    if ch == BS:
                        j += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    j += 1
                j += 1
                while j < n and (src[j].isalnum() or src[j] == "_"):
                    j += 1
                i, prev = j, "lit"
                continue
            i, prev = i + 1, "/"
            continue
        if c.isalnum() or c in "_$":
            m = WORD.match(src, i)
            if not m:
                i, prev = i + 1, "id"
                continue
            i, prev = m.end(), (m.group(0) if m.group(0) in KEYWORDS else "id")
            continue
        if c == "{":
            stack.append("{")
        elif c == "}" and stack:
            stack.pop()
        i, prev = i + 1, c
    return out


def _code(rel):
    text = _read(rel)
    if not rel.endswith(".html"):
        return [text]
    return [m.group(2) for m in re.finditer(r"<script\b([^>]*)>(.*?)</script\b[^>]*>", text, re.S | re.I)
            if "src=" not in m.group(1) and len(m.group(2).strip()) >= 40]


def test_the_scanner_reads_the_whole_page():
    """A scanner that lost its place would find nothing and pass."""
    found = [t for body in _code("index.html") for _, t in _literals(body)]
    assert len(found) > 20000, len(found)
    for known in ("Keyboard shortcuts", "Agent Friday", "/api/settings"):
        assert any(known in t for t in found), known


@pytest.mark.parametrize("rel", ("index.html",) + WORKSPACE_SCRIPTS)
def test_no_string_names_her_friday_by_hand(rel):
    found = collections.Counter(t for body in _code(rel) for _, t in _literals(body) if BARE.search(t))
    allowed = ALLOWED[rel]
    extra = ["%r (x%d)" % (t, n - allowed.get(t, 0)) for t, n in found.items() if n > allowed.get(t, 0)]
    assert not extra, (
        "%s names her Friday by hand; say fridayName() for her, FRIDAY_BRAND.name for the product, "
        "or add it to ALLOWED with the reason:\n  %s" % (rel, "\n  ".join(extra)))


# ── the helper and where it gets the name ───────────────────────────────────

def _block(text, name):
    m = re.search(r"/\* %s:begin \*/\n(.*?)/\* %s:end \*/" % (name, name), text, re.S)
    assert m, name
    return m.group(1)


def test_both_pages_carry_the_same_helper():
    assert _block(_read(PAGES[0]), "fridayHerName") == _block(_read(PAGES[1]), "fridayHerName")


@pytest.mark.parametrize("rel", PAGES)
def test_the_app_takes_her_name_only_from_loaded_settings(rel):
    text = _read(rel)
    app = text[text.index("function App("):]
    assert re.search(r"if \(agentSettings\.workspace_layouts\) fridaySetName\(agentSettings\.agent_name\);", app), rel


@pytest.mark.parametrize("rel", WORKSPACE_SCRIPTS)
def test_a_workspace_script_reads_her_name_from_the_page(rel):
    head = _read(rel)[:4000]
    assert "const fridayName = () => (typeof W.fridayName === 'function' ? W.fridayName() : 'Friday');" in head
    assert "const FRIDAY_BRAND = W.FRIDAY_BRAND || { name: 'Agent Friday™' };" in head


_PROBE = r"""
const store = {};
global.window = {};
global.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
eval(BLOCKS);
const seen = {fresh: fridayName()};
fridaySetName('Juno'); seen.set = fridayName(); seen.stored = store.friday_agent_name;
fridaySetName(''); seen.empty = fridayName();
fridaySetName('AGENT FRIDAY'); seen.default = fridayName();
window.__FRIDAY_AGENT_NAME = undefined; store.friday_agent_name = 'Juno';
eval(BLOCKS);
seen.reload = fridayName();
seen.global = typeof window.fridayName;
console.log(JSON.stringify(seen));
"""


def test_her_name_comes_from_settings_and_survives_a_reload():
    if not node:
        pytest.skip("node is not on PATH")
    text = _read("index.html")
    blocks = _block(text, "fridayShortcuts") + _block(text, "fridayHerName")
    script = "const BLOCKS = %s;\n%s" % (json.dumps(blocks), _PROBE)
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    assert seen == {"fresh": "Friday", "set": "Juno", "stored": "Juno", "empty": "Juno",
                    "default": "Agent Friday", "reload": "Juno", "global": "function"}, seen


# ── in a real browser ────────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_api.sync_playwright() as pw:
            try:
                b = pw.chromium.launch()
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            yield b, "http://127.0.0.1:%d" % httpd.server_address[1]
            b.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _desktop(page, base, settings):
    page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
    page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                body=json.dumps({"status": "ok"})))
    page.route("**/api/settings", lambda r: r.fulfill(status=200, content_type="application/json",
                                                     body=json.dumps({"status": "ok", "settings": settings})))
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector('[data-testid="friday-top-bar"]', timeout=60000)


_CHAT = '[data-testid="friday-top-bar"] button[aria-label^="Open chat with"]'


def test_the_page_speaks_of_her_by_her_name_and_the_product_keeps_its_own(browser):
    b, base = browser
    ctx = b.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _desktop(page, base, {"agent_name": "Juno", "workspace_layouts": {}})
        page.wait_for_function("document.querySelector(%s).getAttribute('aria-label') === 'Open chat with Juno'"
                               % json.dumps(_CHAT), timeout=10000)
        lockup = page.evaluate("window.FRIDAY_BRAND.lockup")
        assert lockup == "Agent Friday™ by FutureSpeak.AI™", lockup
        # a reload shows the name this browser last saw before settings arrive
        page.unroute("**/api/settings")
        page.route("**/api/settings", lambda r: None)                 # never answers
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(_CHAT, timeout=60000)
        assert page.get_attribute(_CHAT, "aria-label") == "Open chat with Juno"
        assert not errors, errors
    finally:
        ctx.close()


def test_her_default_name_reads_agent_friday_and_before_any_name_she_is_friday(browser):
    """A new owner's settings carry her default name, "AGENT FRIDAY", which reads
    "Agent Friday" in a sentence. Before settings have come, with no name kept in
    the browser, she is Friday."""
    b, base = browser
    # the second answer lacks what only the server's settings carry, so to the
    # page they have not come yet
    for settings, want in (({"agent_name": "AGENT FRIDAY", "workspace_layouts": {}}, "Agent Friday"),
                           ({"agent_name": "Juno"}, "Friday")):
        ctx = b.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        try:
            _desktop(page, base, settings)
            page.wait_for_function("document.querySelector(%s).getAttribute('aria-label') === %s"
                                   % (json.dumps(_CHAT), json.dumps("Open chat with " + want)), timeout=10000)
        finally:
            ctx.close()
