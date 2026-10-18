"""Rows that arrive on a busy page still get their stage (static/workspace_stages.js).

The registrar looked again 400 ms after the LAST change to the page: every change restarted the timer. On
a page that keeps changing (a clock, a status line, a live list) it never looked, so rows that arrived then
never reached Friday's stage. open_only_stage.spec.ts failed this way once CI ran the desktop page at full
speed (the app-specs GL fix). Now it looks at most 400 ms after the FIRST change, however busy the page is.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
node = shutil.which("node")

HARNESS = r"""
globalThis.self = globalThis;
let looks = 0, rows = false, observer = null;
globalThis.document = {
  readyState: 'complete', body: {},
  querySelector(sel) { looks++; return rows && sel.indexOf('health:') >= 0 ? {} : null; },
};
globalThis.MutationObserver = class { constructor(cb) { observer = cb; } observe() {} };
const staged = [];
globalThis.fridayStage = { domList(id) { staged.push(id); return () => {}; } };
require(process.argv[process.argv.length - 1]);
const afterStart = looks;
// The page changes every 100 ms for 2 s; a health row arrives at 300 ms.
let t = 0;
const tick = setInterval(() => { t += 100; if (t === 300) rows = true; observer([]); }, 100);
setTimeout(() => {
  clearInterval(tick);
  console.log(JSON.stringify({ syncs: (looks - afterStart) / 12, staged }));
}, 2000);
"""


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_row_that_arrives_on_a_busy_page_gets_its_stage():
    r = subprocess.run([node, "-e", HARNESS, str(ROOT / "static" / "workspace_stages.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["syncs"] >= 3, "the registrar never looked while the page kept changing: %s" % out
    assert out["staged"] == ["health"]
