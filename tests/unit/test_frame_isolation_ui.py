"""Frames Friday shows are sandboxed, and the broker is the only door back.

Structural checks over the two copies of the page (index.html is served,
ui_parts/app.html is its hand-maintained mirror) and behavioural checks of the
postMessage broker, run under node when it is installed.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BROKER = ROOT / "static" / "js" / "friday_frame_broker.js"
PAGES = {"index.html": ROOT / "index.html", "app.html": ROOT / "ui_parts" / "app.html"}
FORBIDDEN_TOKENS = ("allow-same-origin", "allow-top-navigation")


def _text(name):
    return PAGES[name].read_text(encoding="utf-8")


def _iframes(name):
    """The attribute/prop text of every iframe in a page copy."""
    s = _text(name)
    if name == "index.html":
        return [s[m.end():m.end() + 500].split("style:")[0]
                for m in re.finditer(r'createElement\("iframe", \{', s)]
    return [s[m.end():m.end() + 500].split("style=")[0]
            for m in re.finditer(r"<iframe\b", s)]


@pytest.mark.parametrize("name", list(PAGES))
def test_the_page_has_iframes_to_check(name):
    assert len(_iframes(name)) == 4, name


@pytest.mark.parametrize("name", list(PAGES))
def test_every_iframe_is_sandboxed_to_an_opaque_origin(name):
    for props in _iframes(name):
        m = re.search(r'sandbox[:=]\s*\{?"([^"]*)"', props)
        assert m, "an iframe with no sandbox: " + props.strip()[:120]
        for bad in FORBIDDEN_TOKENS:
            assert bad not in m.group(1), props.strip()[:120]


@pytest.mark.parametrize("name", list(PAGES))
def test_every_iframe_registers_with_the_broker(name):
    assert all("fridayAttachFrame" in p for p in _iframes(name))


@pytest.mark.parametrize("name", list(PAGES))
def test_chat_artifact_frame_has_a_sandbox(name):
    """The frame that renders an HTML document from a chat/research reply."""
    s = _text(name)
    i = s.index("fridayFrameScrollbars(htmlContent)")
    assert "sandbox" in s[i:i + 200]


def test_both_copies_carry_the_same_isolation_pieces():
    for marker in ("/api/session/token", "session_token_required",
                   "fridayRefreshSessionToken", "noopener"):
        counts = {n: _text(n).count(marker) for n in PAGES}
        assert all(c > 0 for c in counts.values()), (marker, counts)
    for path in (ROOT / "index.html", ROOT / "ui_parts" / "head.html"):
        assert "/static/js/friday_frame_broker.js" in path.read_text(encoding="utf-8"), path


# --- the broker's surface is closed and holds no authority ----------------

SENSITIVE = re.compile(r"vault|credential|secret|token|passw|passphrase|command|exec|shell|"
                       r"terminal|setting|key|api|fetch|run", re.I)


def _capability_names():
    src = BROKER.read_text(encoding="utf-8")
    body = src[src.index("var CAPABILITIES = {"):src.index("function requireGesture")]
    return re.findall(r"^    '([a-z.]+)':", body, re.M)


def test_no_capability_names_authority():
    names = _capability_names()
    assert names == ["ping", "theme.get", "frame.resize", "link.open", "clipboard.write"]
    assert not [n for n in names if SENSITIVE.search(n)]


def test_the_broker_cannot_reach_the_api_or_the_token():
    src = BROKER.read_text(encoding="utf-8")
    for needle in ("fetch(", "XMLHttpRequest", "WebSocket", "__FRIDAY_API_TOKEN",
                   "apiFetch", "X-Friday-Token", "eval(", "new Function", "localStorage"):
        assert needle not in src, needle


def test_the_contract_document_lists_every_capability():
    doc = (ROOT / "docs" / "development" / "frame-broker.md").read_text(encoding="utf-8")
    for name in _capability_names():
        assert "`" + name + "`" in doc, name
    for word in ("vault", "credentials", "runs a command"):
        assert word in doc


# --- behaviour (node) ------------------------------------------------------

NODE = shutil.which("node")

HARNESS = r"""
const broker = require(process.argv[1]);
const out = [];
let opened = [], copied = [], active = true, clock = 0;
const env = { now: () => clock, open: (...a) => opened.push(a), userActive: () => active,
              clipboardWrite: async (t) => { copied.push(t); } };
function frame(tokens, attrs) {
  const replies = [];
  const src = { postMessage: (m) => replies.push(m) };
  const style = {};
  return { replies, src, el: { sandbox: { contains: (t) => tokens.includes(t) },
    hasAttribute: (n) => n === 'sandbox' || (attrs || []).includes(n),
    contentWindow: src, isConnected: true, style } };
}
const b = broker.create(env);
const ok = frame(['allow-scripts'], ['data-friday-resizable']);
const same = frame(['allow-scripts', 'allow-same-origin']);
const stranger = frame(['allow-scripts']);
b.attach(ok.el); b.attach(same.el);
const req = (f, cap, args, origin) => b.onMessage({ source: f.src, origin: origin === undefined ? 'null' : origin,
  data: { protocol: 'friday-frame/1', id: cap, cap, args } });
const tick = () => new Promise((r) => setTimeout(r, 5));
(async () => {
  req(ok, 'ping'); req(ok, 'theme.get'); req(ok, 'frame.resize', { height: 300 });
  req(ok, 'frame.resize', { height: 99999 });
  req(ok, 'vault.read', { name: 'x' }); req(ok, 'run_command', { cmd: 'dir' });
  req(ok, 'constructor'); req(ok, '__proto__');
  active = false; req(ok, 'link.open', { url: 'https://example.com' });
  active = true; req(ok, 'link.open', { url: 'javascript:alert(1)' });
  req(ok, 'link.open', { url: 'https://example.com/a' });
  req(ok, 'clipboard.write', { text: 'hello' });
  req(same, 'ping'); req(stranger, 'ping'); req(ok, 'ping', null, 'https://evil.example');
  await tick();
  out.push({ ok: ok.replies, same: same.replies.length, stranger: stranger.replies.length,
             opened, copied, height: ok.el.style.height });
  const flood = frame(['allow-scripts']); b.attach(flood.el);
  for (let i = 0; i < 40; i++) req(flood, 'ping');
  await tick();
  out.push({ flood: flood.replies.filter((r) => !r.ok).length });
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def run():
    if not NODE:
        pytest.skip("node is not installed")
    p = subprocess.run([NODE, "-e", HARNESS, str(BROKER)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def _by_id(replies):
    return {r["id"]: r for r in replies}


def test_a_sandboxed_frame_gets_the_listed_capabilities(run):
    r = _by_id(run[0]["ok"])
    assert r["ping"]["ok"] and r["ping"]["result"]["capabilities"] == _capability_names()
    assert r["theme.get"]["result"]["primary"] == "#00d4ff"
    assert r["frame.resize"]["ok"] and run[0]["height"] == "300px"


def test_out_of_range_and_unlisted_requests_are_refused(run):
    r = run[0]["ok"]
    by_cap = {}
    for m in r:
        by_cap.setdefault(m["id"], []).append(m)
    assert sorted(m["ok"] for m in by_cap["frame.resize"]) == [False, True]
    for cap in ("vault.read", "run_command", "constructor", "__proto__"):
        (m,) = by_cap[cap]
        assert m["ok"] is False and "isn't something a frame can ask" in m["error"], cap


def test_links_and_copying_need_a_gesture_and_a_web_address(run):
    o = run[0]
    assert o["opened"] == [["https://example.com/a", "_blank", "noopener,noreferrer"]]
    assert o["copied"] == ["hello"]
    links = [m for m in o["ok"] if m["id"] == "link.open"]
    assert sorted(m["ok"] for m in links) == [False, False, True]


def test_a_frame_that_shares_the_origin_or_is_unknown_gets_no_reply(run):
    o = run[0]
    assert o["same"] == 0 and o["stranger"] == 0
    assert all(m.get("result", {}).get("protocol") == "friday-frame/1" or m["id"] != "ping"
               for m in o["ok"])
    assert len([m for m in o["ok"] if m["id"] == "ping"]) == 1   # the foreign-origin ping got nothing


def test_a_flood_is_cut_off(run):
    assert run[1]["flood"] >= 10
