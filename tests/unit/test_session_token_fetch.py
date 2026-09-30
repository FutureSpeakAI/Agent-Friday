"""The page attaches the session token to every same-origin state change.

The server refuses a browser POST/PUT/PATCH/DELETE or WebSocket upgrade that
lacks the session token (services/origin_gate.py), so the page must send it on
every such fetch, not only the ones routed through apiFetch. This runs the
installSessionTokenFetch block from index.html under node against a stubbed
fetch. The mirror must carry the identical block.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
START = "(function installSessionTokenFetch()"
END = "})();\n"
node = shutil.which("node")


def _block(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    a = text.index(START)
    assert text.count(START) == 1
    return text[a:text.index(END, a) + len(END)]


def test_mirror_carries_the_same_block():
    assert _block("index.html") == _block("ui_parts/app.html")


def test_the_block_installs_before_the_panel_cache():
    text = (ROOT / "index.html").read_text(encoding="utf-8")
    assert text.index(START) < text.index("(function installPanelCache()")


HARNESS = r"""
const calls = [];
globalThis.window = {
  __FRIDAY_API_TOKEN: 'tok-123',
  location: {href: 'http://127.0.0.1:3000/', origin: 'http://127.0.0.1:3000'},
  fetch: (input, init) => {
    const h = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
    calls.push({url: String(input instanceof Request ? input.url : input),
                token: h.get('X-Friday-Token'), extra: h.get('X-Extra')});
    return Promise.resolve(new Response('{}'));
  },
};
BLOCK
(async () => {
  const f = window.fetch;
  await f('/api/a', {method: 'POST'});
  await f('/api/b', {method: 'delete', headers: {'X-Extra': 'kept'}});
  await f('/api/c');
  await f('/api/d', {method: 'PUT', headers: {'X-Friday-Token': 'own'}});
  await f('https://evil.example/x', {method: 'POST'});
  await f(new Request('http://127.0.0.1:3000/api/e', {method: 'PATCH', headers: {'X-Extra': 'r'}}));
  window.__FRIDAY_API_TOKEN = '';
  await f('/api/f', {method: 'POST'});
  console.log(JSON.stringify(calls));
})();
"""


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_state_changes_carry_the_token_and_nothing_else_does(tmp_path):
    script = tmp_path / "h.js"
    script.write_text(HARNESS.replace("BLOCK", _block("index.html")), encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    a, b, c, d, foreign, req, notok = json.loads(cp.stdout.strip().splitlines()[-1])
    assert a["token"] == "tok-123"
    assert b["token"] == "tok-123" and b["extra"] == "kept"
    assert c["token"] is None                      # reads are untouched
    assert d["token"] == "own"                     # an explicit token is not replaced
    assert foreign["token"] is None                # the token never leaves this origin
    assert req["token"] == "tok-123" and req["extra"] == "r"
    assert notok["token"] is None
