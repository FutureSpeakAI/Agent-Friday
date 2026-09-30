"""Brand fidelity: a workspace is the same thing in a desktop window and in its
own tab, and both read the brand system (docs/brand/fidelity-audit.md, the
first pass of P-BRAND-FIDELITY).

  * One workspace frame: its tools (chat about it, talk about it, its
    history), the mark that Friday customized it, and its body with that
    customization, drawn by a window's title bar and a tab's top bar alike.
  * Every Friday page wears the rocket and names the product in full.
  * A window's text has the tab's ink, and a control takes the page's face.
  * A selected tab is .btn.active; deny magenta marks a refusal or a stop;
    the approvals list in the bar draws the one approval card.
  * A workspace's passing notice is Friday's one toast.
  * Everything in the top bar is a Ctrl+K away.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import shutil
import socketserver
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday import brand

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
HEADS = ("index.html", "ui_parts/head.html")
ROCKET = '<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">'
NL = chr(10)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _fn(text: str, name: str) -> str:
    """The source of the top-level function `name`."""
    m = re.search(r"\nfunction %s\(" % name, text)
    assert m, name
    return text[m.start():text.index(NL + "}" + NL, m.start() + 1) + 2]


def _rule(css: str, selector: str) -> str:
    """The body of the first rule for exactly `selector`."""
    m = re.search(r"(?m)^\s*%s\s*\{([^}]*)\}" % re.escape(selector), css)
    assert m, selector
    return m.group(1)


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


# ── one workspace frame ──────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", PAGES)
def test_a_window_and_a_tab_draw_one_workspace_frame(rel):
    s = _read(rel)
    for name in ("WsTools", "WsCustomizedMark", "WsCustomBody"):
        assert len(re.findall(r"\nfunction %s\(" % name, s)) == 1, (rel, name)
    fwin = _fn(s, "FWin")
    for piece in ("WsTools", "WsCustomizedMark", "WsCustomBody"):
        assert piece in fwin, (rel, "the window draws " + piece)
    assert "custCss" not in fwin and "fridayOpenWorkspaceChat" not in fwin, rel + ": the window keeps a copy"
    bar = _fn(s, "ShellTabName")
    assert "WsTools" in bar and "WsCustomizedMark" in bar, rel + ": the tab's bar carries the workspace's tools"
    tab = _fn(s, "StandaloneShell")
    assert "WsCustomBody" in tab and "WorkspaceHistory" in tab, rel + ": the tab is customized and has its history"


@pytest.mark.parametrize("rel", PAGES)
def test_the_tab_keeps_the_rocket(rel):
    tab = _fn(_read(rel), "StandaloneShell")
    assert "rel~=" not in tab and "/assets/icons/" not in tab, rel + ": a tab swaps the rocket for its own icon"


# ── ink and face ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", HEADS)
def test_a_window_has_the_tab_ink_and_a_control_the_page_face(rel):
    s = _read(rel)
    assert "color: var(--fr-text)" in _rule(s, ".fwin"), rel
    assert re.search(r"button, input, select, textarea \{ font-family: inherit; \}", s), rel
    assert "box-sizing: border-box" in _rule(s, ".input"), rel + ": a full-width field overflows by its padding"


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/styles_and_scene.html"))
def test_a_tab_has_the_same_ink(rel):
    assert "color: var(--fr-text)" in _rule(_read(rel), ".ws-tab"), rel


# ── buttons ──────────────────────────────────────────────────────────────────

#: A stop, a removal or a refusal: what deny magenta is for (BRAND.md).
KEEP = re.compile(r"Remove|Delete|Clear|Cancel|Kill|Stop|Disconnect|Forget|Revoke|Reject|Deny|u2715|✕|u23F9|⏹")


def _label_compiled(s: str, at: int) -> str:
    depth, i = 0, s.index("{", at - 40)
    while True:
        depth += {"{": 1, "}": -1}.get(s[i], 0)
        if depth == 0:
            break
        i += 1
    return s[i + 1:i + 240].split("/*#__PURE__*/")[0].split("));")[0]


def _label_jsx(t: str, at: int) -> str:
    gt = t.index(">", at)
    while t[gt - 1] == "=":
        gt = t.index(">", gt + 1)
    return t[gt + 1:gt + 120].split(NL)[0]


@pytest.mark.parametrize("rel", PAGES + ("static/friday3d_records.js", "static/studio_files3d.js"))
def test_a_selected_segment_is_active_not_the_others_magenta(rel):
    left = re.findall(r"\(\s*([^()]*?)\s*\?\s*''\s*:\s*' ?btn-magenta'\)", _read(rel))
    # Only the publishing kill switch keeps it: magenta while pressing it would stop everything.
    assert all(c.strip() == "paused" for c in left), (rel, left)


@pytest.mark.parametrize("rel", PAGES)
def test_deny_magenta_marks_a_refusal_or_a_stop(rel):
    s = _read(rel)
    if rel == "index.html":
        needle, label = 'className: "btn btn-magenta"', _label_compiled
    else:
        needle, label = 'className="btn btn-magenta"', _label_jsx
    labels = [label(s, m.start()) for m in re.finditer(re.escape(needle), s)]
    assert labels, rel
    assert not [x for x in labels if not KEEP.search(x)], (rel, [x for x in labels if not KEEP.search(x)])


@pytest.mark.parametrize("rel", PAGES)
def test_the_bar_approvals_draw_the_one_approval_card(rel):
    body = _fn(_read(rel), "StandaloneApprovals")
    assert "ApprovalCardBody" in body and "Deny" not in body, rel


@pytest.mark.parametrize("rel", HEADS)
def test_a_reject_is_deny_magenta_and_a_delete_is_error_red(rel):
    s = _read(rel)
    assert "var(--fr-deny)" in _rule(s, ".kw-deny") and "var(--fr-deny)" in _rule(s, ".sc-btn.deny"), rel
    assert "var(--fr-error)" in _rule(s, ".kw-danger"), rel


@pytest.mark.parametrize("rel", PAGES)
def test_every_reject_uses_the_deny_style(rel):
    s = _read(rel)
    assert re.search(r"kw-btn kw-deny',\s*onClick: \(\) => rejectPending", s), rel
    assert re.search(r"sc-btn deny\"[\s\S]{0,60}'Reject: '", s), rel


# ── notices ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", PAGES)
def test_a_workspace_notice_is_the_one_toast(rel):
    s = _read(rel)
    assert len(re.findall(r"\nfunction useFridayToast\(", s)) == 1, rel
    assert not re.search(r"\[\s*toast\s*,\s*setToast\s*\]\s*=\s*useState", s), rel + ": a workspace draws its own notice"
    assert len(re.findall(r"const setToast\s*=\s*useFridayToast\(\)", s)) >= 18, rel
    assert not re.search(r"(?<![\w.])toast\s*&&", s), rel + ": an inline notice is still drawn"


def test_a_failure_notice_reads_as_one():
    m = re.search(r"const FRIDAY_TOAST_FAILED = (/.*?/i);", _read("index.html"))
    assert m
    cases = ["Copied", "Saved", "Logged as sent", "Copy failed", "Log failed", "Could not save",
             "Couldn’t reach the site", "Error: 500", "Unable to parse that time"]
    want = [False, False, False, True, True, True, True, True, True]
    script = ("const re=%s;const t=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "console.log(JSON.stringify(t.map(x=>re.test(x))))" % m.group(1))
    out = subprocess.run([_node(), "-e", script], input=json.dumps(cases), capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert json.loads(out.stdout) == want, out.stderr


# ── the bar ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", PAGES)
def test_everything_in_the_bar_is_a_ctrl_k_away(rel):
    s = _read(rel)
    for cid in ("chat", "quick-draft", "notifications", "fs-chat"):
        assert re.search(r"id: ?'%s'" % cid, s), (rel, cid)
    assert re.search(r"commands[:=]\s*\{?layoutCommands\.concat\([^)]*\bbarCommands\)", s), rel
    for fn in ("toggleChat", "toggleQuickDraft", "toggleNotifs"):
        assert re.search(r"onClick[:=]\s*\{?%s\b" % fn, s), (rel, fn + " is the bar button's own handler")


def test_the_model_selector_names_a_vendor_model_as_its_maker_does():
    fn = _fn(_read("index.html"), "prettyModel")
    cases = [["claude-sonnet-5-5", None], ["claude-opus-4-8", "claude-opus-4-8"],
             ["claude-haiku-4-5-20251001", None], ["gemini-3.8-live", None], ["gpt-5.1", None],
             ["hf.co/org/Ternary-Bonsai-2-27B-GGUF:Q2_K", "Ternary Bonsai 2 27B (PTQ1_0, 1.75 bit)"]]
    want = ["Claude Sonnet 5.5", "Claude Opus 4.8", "Claude Haiku 4.5", "Gemini 3.8 Live", "GPT 5.1",
            "Ternary Bonsai 2 27B (PTQ1_0, 1.75 bit)"]
    script = fn + (";const t=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                   "console.log(JSON.stringify(t.map(([i,l])=>prettyModel(i,l))))")
    out = subprocess.run([_node(), "-e", script], input=json.dumps(cases), capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert json.loads(out.stdout) == want, out.stderr


@pytest.mark.parametrize("rel", PAGES)
def test_knowledge_says_it_is_reading_rather_than_zero_pages(rel):
    assert re.search(r"struct \? totalFiles \+ ' pages", _read(rel)), rel


# ── every Friday page ────────────────────────────────────────────────────────

def test_the_sign_in_page_names_the_product_and_wears_the_rocket():
    from agent_friday import core
    page = core.LOGIN_HTML
    assert "<title>Sign in · %s</title>" % brand.PRODUCT_NAME in page
    assert "<h1>%s</h1>" % brand.PRODUCT_NAME.upper() in page and brand.MAKER in page
    assert ROCKET in page and "{{ product" not in page and "{{ maker" not in page
    assert "{{ error }}" in page, "the banner slot is still filled per request"


def test_the_draft_export_names_the_product():
    from agent_friday.services import misc_engine
    page = misc_engine._build_draft_html("We shipped it.", "linkedin_post", "a launch note")
    assert "· Draft · %s</title>" % brand.PRODUCT_NAME in page and ROCKET in page
    assert "<h1>%s DRAFT</h1>" % brand.PRODUCT_NAME.upper() in page
    assert brand.MADE_WITH in page and "Generated by FRIDAY" not in page


def test_the_research_page_carries_the_mark(tmp_path):
    from agent_friday.services.research import deliver
    c = SimpleNamespace(plan=None, question="What changed this week?", dir=tmp_path, styled_path=None,
                        save=lambda: None, log=lambda *_: None)
    out = deliver.style(c, "# Findings" + NL + NL + "One thing changed.")
    assert out, "the page was written"
    assert brand.MADE_WITH in Path(out).read_text(encoding="utf-8")


def test_friday_live_and_the_widget_name_the_product_and_wear_the_rocket():
    live = _read("static/live/friday_live.html")
    assert "<title>Live · %s</title>" % brand.PRODUCT_NAME in live and ROCKET in live
    assert "<h1>%s · LIVE</h1>" % brand.PRODUCT_NAME.upper() in live
    for rel in ("static/live/friday_live_manifest.json", "static/widget/manifest.json"):
        m = json.loads(_read(rel))
        assert m["name"].startswith(brand.PRODUCT_NAME) and m["short_name"] == brand.PRODUCT_NAME, rel
        assert all(i["src"].startswith("/static/android-chrome-") for i in m["icons"]), rel + ": the rocket"


def test_the_creation_page_wears_the_rocket():
    assert ROCKET in _read("src/agent_friday/routes/creations.py")


# ── in a real browser ────────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    """The tree as the server serves it: /w/<id> is index.html with the marker."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        m = re.match(r"^/w/([a-z0-9_-]+)", self.path)
        if m:
            page = _read("index.html").replace(
                "<head>", '<head><script>window.__FRIDAY_STANDALONE__="%s";'
                'document.documentElement.classList.add("ws-standalone");</script>' % m.group(1), 1)
            body = page.encode("utf-8")
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
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(ROOT)))
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
            page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
            page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                        body=json.dumps({"status": "ok"})))
            yield page, base
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _json(body):
    return lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def test_a_tab_carries_the_workspace_tools_its_customization_and_the_rocket(browser_page):
    page, base = browser_page
    cust = _json({"customizations": {"news": {"note": "Pinned by Friday",
                                              "actions": [{"label": "Do the thing", "prompt": "do it"}]}}})
    hist = _json({"entries": [], "current_keys": []})
    page.route("**/api/workspace/customizations", cust)
    page.route("**/api/workspace/news/history", hist)
    try:
        page.goto(base + "/w/news", wait_until="domcontentloaded")
        page.wait_for_selector('[data-testid="ws-tab-tools"] button', timeout=60000)
        tools = page.locator('[data-testid="ws-tab-tools"] button')
        assert [tools.nth(i).get_attribute("aria-label") for i in range(tools.count())] == [
            "Workspace chat for News", "Start voice for News", "Version history for News"]
        page.wait_for_selector('.ws-tab .ws-custom-root[data-ws="news"] >> text=Do the thing', timeout=15000)
        assert page.locator('[data-testid="ws-tab-name"] [title^="Friday has customized"]').count() == 1
        tools.nth(2).click()
        page.wait_for_selector(".ws-tab-hist >> text=History", timeout=10000)
        assert tools.nth(2).get_attribute("aria-pressed") == "true"
        icons = page.evaluate("Array.from(document.querySelectorAll('link[rel~=\"icon\"]'))"
                              ".map(l => l.getAttribute('href'))")
        assert icons and all(h.startswith("/static/") for h in icons), icons
    finally:
        page.unroute("**/api/workspace/customizations", cust)
        page.unroute("**/api/workspace/news/history", hist)


def test_a_window_has_the_tab_ink_and_its_controls_the_page_face(browser_page):
    page, base = browser_page
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector('.dock-btn[data-ws="news"]', timeout=60000)
    page.click('.dock-btn[data-ws="news"]')
    page.wait_for_selector('.fwin .ws-custom-root[data-ws="news"]', timeout=15000)
    got = page.evaluate("""() => {
      const root = document.querySelector('.fwin .ws-custom-root[data-ws="news"]');
      const span = document.createElement('span'); span.textContent = 'x';
      const btn = document.createElement('button'); btn.textContent = 'y';
      root.append(span, btn);
      const r = {ink: getComputedStyle(span).color, face: getComputedStyle(btn).fontFamily};
      span.remove(); btn.remove();
      return r;
    }""")
    assert got["ink"] == "rgba(255, 255, 255, 0.86)", got
    assert got["face"].split(",")[0].strip().strip("'\"") == "Inter", got


def test_a_selected_tab_is_active_and_the_others_plain(browser_page):
    page, base = browser_page
    page.goto(base + "/index.html?workspace=health", wait_until="domcontentloaded")
    page.wait_for_selector('.fwin .ws-custom-root[data-ws="health"] button[aria-pressed]', timeout=60000)
    tabs = page.evaluate("""() => Array.from(document.querySelectorAll(
        '.fwin .ws-custom-root[data-ws="health"] button[aria-pressed]')).slice(0, 5)
        .map(b => [b.textContent, b.className, b.getAttribute('aria-pressed')])""")
    assert tabs[0] == ["overview", "btn active", "true"], tabs
    assert all(c == "btn" and p == "false" for _t, c, p in tabs[1:]), tabs
