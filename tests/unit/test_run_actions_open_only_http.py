"""An action from the server opens only http(s) URLs.

`fridayRunActions` runs the actions a chat or voice reply carries, and those
replies carry model output. `open_url` and `open_last_source` hand their URL
to window.open; a `javascript:` URL there would run script with the page's
origin. Both the served index.html and the ui_parts/app.html mirror are run
under node with a recording `window.open`, rather than asserted on as text.
"""
import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                               reason="node is not installed")

FILES = ["index.html", "ui_parts/app.html"]


def _function(src: str, name: str) -> str:
    """The text of top-level `function <name>(`, located by a unique anchor
    and closed by brace matching."""
    needle = "function %s(" % name
    assert src.count(needle) == 1, "%d definitions of %s" % (src.count(needle), name)
    start = src.index(needle)
    depth, i = 0, src.index("{", start)
    while True:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


HARNESS = """
'use strict';
const opened = [];
const window = { location: { href: 'http://127.0.0.1:3000/' },
                 open: (u) => { opened.push(u); } };
const fetch = () => Promise.resolve();
const fridayNavigate = () => {};
%s
%s
const cases = %s;
const out = {};
for (const [name, actions, last] of cases) {
  opened.length = 0;
  window._fridayLastVoiceSource = last;
  fridayRunActions(actions);
  out[name] = opened.slice();
}
console.log(JSON.stringify(out));
"""

CASES = [
    ["https", [{"type": "open_url", "url": "https://example.org/a"}], None],
    ["javascript", [{"type": "open_url", "url": "javascript:alert(document.cookie)"}], None],
    ["javascript_case", [{"type": "open_url", "url": " JaVaScRiPt:alert(1)"}], None],
    ["data", [{"type": "open_url", "url": "data:text/html,<script>1</script>"}], None],
    ["file", [{"type": "open_url", "url": "file:///C:/Windows/win.ini"}], None],
    ["last_source_js", [{"type": "open_last_source"}], "javascript:alert(1)"],
    ["last_source_http", [{"type": "open_last_source"}], "http://example.org/story"],
]


@pytest.mark.parametrize("path", FILES)
def test_run_actions_opens_http_and_refuses_script_urls(path, tmp_path):
    src = open(path, encoding="utf-8").read()
    script = HARNESS % (_function(src, "fridayHttpUrl"),
                        _function(src, "fridayRunActions"),
                        json.dumps(CASES))
    f = tmp_path / "harness.js"
    f.write_text(script, encoding="utf-8")
    run = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["https"] == ["https://example.org/a"]
    assert out["last_source_http"] == ["http://example.org/story"]
    for name in ("javascript", "javascript_case", "data", "file", "last_source_js"):
        assert out[name] == [], (name, out[name])
