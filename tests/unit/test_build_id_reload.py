"""Stale tabs: the server names its build, and an open tab offers a reload
when the build behind it changes (field install report, Part 2 #9; Part 1 #10:
"Open tabs keep the old JS after restarts").

On origin/main the server reported only `version`, which cannot tell two
builds of the same beta apart, the served page recorded nothing about which
build it came from, and nothing in the page compared the two, so a tab kept
the old JavaScript after an update until someone happened to reload it.

Server side: through the real routes (`/` and `/api/health`) on a bare Flask
app. Page side: source checks over both copies of the page, plus the watch
module driven under node (skipped when node is absent).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
WATCH = ROOT / "static" / "js" / "friday_build_watch.js"
INDEX = ROOT / "index.html"
APP = ROOT / "ui_parts" / "app.html"
HEAD = ROOT / "ui_parts" / "head.html"

_BUILD_RE = re.compile(r'window\.__FRIDAY_BUILD_ID__=("[^"]*"|null);')


def _reset_build_cache():
    from agent_friday.services import app_version
    reset = getattr(app_version, "_reset_build_id_for_tests", None)
    if reset:
        reset()


@pytest.fixture
def ui_tree(tmp_path, monkeypatch):
    """A synthetic app directory: an index page and one script, served from cwd
    exactly as server.py serves the real tree."""
    (tmp_path / "index.html").write_text(
        "<!DOCTYPE html><html><head><title>t</title></head><body></body></html>",
        encoding="utf-8")
    (tmp_path / "static" / "js").mkdir(parents=True)
    (tmp_path / "static" / "js" / "a.js").write_text("var a = 1;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _reset_build_cache()
    yield tmp_path
    _reset_build_cache()


def _app():
    from agent_friday.routes import core_routes as cr
    app = Flask(__name__)
    app.register_blueprint(cr.core_bp)
    return app


def _served_build_id(client):
    body = client.get("/").get_data(as_text=True)
    m = _BUILD_RE.search(body)
    assert m, "the served page does not record which build it came from"
    return json.loads(m.group(1))


def _health_build_id(monkeypatch):
    """build_id from /api/health's fast path (the payload a cold tab gets)."""
    from agent_friday.routes import core_routes as cr
    cr._reset_health_cache_for_tests()
    release = threading.Event()
    monkeypatch.setattr(cr, "_HEALTH_WAIT_S", 0.2, raising=False)
    monkeypatch.setattr(cr, "_health_payload", lambda: (release.wait(30.0), {"status": "ok"})[1])
    try:
        body = _app().test_client().get("/api/health").get_json()
    finally:
        release.set()
        cr._reset_health_cache_for_tests()
    return body


def test_the_served_page_and_health_name_the_same_build(ui_tree, monkeypatch):
    client = _app().test_client()
    page_id = _served_build_id(client)
    body = _health_build_id(monkeypatch)
    assert "build_id" in body, "/api/health does not report a build id"
    assert isinstance(page_id, str) and page_id
    assert body["build_id"] == page_id
    from agent_friday.services import app_version
    version = app_version.running_version() or "unknown"
    assert page_id.startswith(version + "+"), page_id


def test_the_full_health_payload_carries_the_build_id():
    """The fallback is not the only shape: the full payload names it too."""
    src = (ROOT / "src" / "agent_friday" / "routes" / "core_routes.py").read_text(encoding="utf-8-sig")
    full = src[src.index("def _health_payload"):]
    full = full[:full.index("\n@core_bp.route", 1) if "\n@core_bp.route" in full else len(full)]
    assert '"build_id": _build_id()' in full


def test_a_changed_ui_file_changes_the_build_id_after_a_restart(ui_tree):
    client = _app().test_client()
    before = _served_build_id(client)
    (ui_tree / "static" / "js" / "a.js").write_text("var a = 2;\n", encoding="utf-8")
    # Same process: computed once, so the id holds until the server restarts.
    assert _served_build_id(client) == before
    _reset_build_cache()  # a restart is a new process
    after = _served_build_id(client)
    assert after != before
    assert after.split("+")[0] == before.split("+")[0], "same version, different build"


def test_an_unchanged_tree_keeps_its_build_id_across_a_restart(ui_tree):
    client = _app().test_client()
    before = _served_build_id(client)
    _reset_build_cache()
    assert _served_build_id(client) == before, "a restart alone must not prompt a reload"


# ── The page ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", [INDEX, HEAD], ids=["index.html", "head.html"])
def test_both_page_copies_load_the_build_watch(page):
    assert '<script src="/static/js/friday_build_watch.js" defer></script>' in page.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", [INDEX, APP], ids=["index.html", "app.html"])
def test_the_top_bar_health_ping_feeds_the_build_watch(page):
    src = page.read_text(encoding="utf-8")
    assert re.search(r"window\.fridayBuildWatch\.check\(\s*d\s*&&\s*d\.build_id\s*\)", src), page.name


def test_the_watch_compares_against_the_id_the_page_loaded_with():
    src = WATCH.read_text(encoding="utf-8")
    assert "__FRIDAY_BUILD_ID__" in src
    assert "id === state.loaded" in src
    assert "build_id" in src
    # Calm and accessible: polite live region, reduced motion respected.
    assert "aria-live', 'polite'" in src
    assert "prefers-reduced-motion" in src
    # Never auto-reloads: location.reload is only reachable from the button.
    assert src.count("location.reload") == 1
    assert re.search(r"reload\.onclick = function \(\) \{ try \{ root\.location\.reload\(\)", src)


node = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
function El(tag) {
  this.tagName = tag; this.children = []; this.attrs = {}; this.style = {};
  this.parentNode = null; this.textContent = ''; this.id = '';
}
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.appendChild = function (c) { c.parentNode = this; this.children.push(c); return c; };
El.prototype.removeChild = function (c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; };
const all = [];
const body = new El('body');
global.window = {
  __FRIDAY_BUILD_ID__: process.argv[3],
  FRIDAY_BRAND: { product: 'Agent Friday' },
  location: { reloads: 0, reload() { this.reloads++; } },
  matchMedia: () => ({ matches: true }),
  addEventListener() {}, setInterval() {}, fetch: undefined,
};
function walk(n, f) { f(n); n.children.forEach(c => walk(c, f)); }
global.window.document = {
  body,
  visibilityState: 'visible',
  addEventListener() {},
  createElement: t => new El(t),
  getElementById: id => { let hit = null; walk(body, n => { if (n.id === id) hit = n; }); return hit; },
  querySelectorAll: () => [],
};
const run = new Function('window', fs.readFileSync(process.argv[2], 'utf8'));
run(global.window);
const w = global.window.fridayBuildWatch;
const notice = () => global.window.document.getElementById('friday-build-notice');
const out = {};
out.installed = !!w;
out.same = w.check(process.argv[3]);
out.same_notice = !!notice();
out.unknown = w.check(null);
out.unknown_notice = !!notice();
out.changed = w.check('9.9.9+abcdef012345');
const n = notice();
out.changed_notice = !!n;
out.role = n && n.attrs.role;
out.live = n && n.attrs['aria-live'];
out.text = n && n.children[0].textContent;
const buttons = n ? n.children.filter(c => c.tagName === 'button').map(b => b.textContent) : [];
out.buttons = buttons;
out.auto_reloads = global.window.location.reloads;
const later = n.children.find(c => c.textContent === 'Later');
later.onclick();
out.after_later = !!notice();
w.check('9.9.9+abcdef012345');
out.same_new_id_again = !!notice();
const reload = (() => { w.check('9.9.9+fedcba543210'); return notice().children.find(c => c.textContent === 'Reload'); })();
reload.onclick();
out.reloads_after_click = global.window.location.reloads;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_watch_offers_a_reload_only_when_the_build_changes(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run([node, str(harness), str(WATCH), "1.1.0b1+000000000000"],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["installed"]
    assert out["same"] is False and out["same_notice"] is False
    assert out["unknown"] is False and out["unknown_notice"] is False, "unknown must never read as updated"
    assert out["changed"] is True and out["changed_notice"] is True
    assert out["role"] == "status" and out["live"] == "polite"
    assert "was updated" in out["text"] and "reload" in out["text"].lower()
    assert out["buttons"] == ["Reload", "Later"]
    assert out["auto_reloads"] == 0, "the notice must never reload by itself"
    assert out["after_later"] is False
    assert out["same_new_id_again"] is False, "a dismissed update does not nag again"
    assert out["reloads_after_click"] == 1
