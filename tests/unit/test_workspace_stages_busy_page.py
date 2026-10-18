"""Rows that arrive on a busy page still get their stage, and a busy page costs the registrar nothing
(static/workspace_stages.js).

The registrar looked again 400 ms after the LAST change to the page: every change restarted the timer. On
a page that keeps changing (a clock, a status line, a live list) it never looked, so rows that arrived then
never reached Friday's stage. open_only_stage.spec.ts failed this way once CI ran the desktop page at full
speed (the app-specs GL fix). Now it looks at most 400 ms after the first change that adds or removes a
marked row; a change that touches no marked row (a clock's text) does not make it look at all.
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
let scans = 0, rows = [], observer = null;
const row = { nodeType: 1, hasAttribute: a => a === 'data-fr-ref', getAttribute: () => 'health:med-0', querySelector: () => null };
const text = { nodeType: 3 };
globalThis.document = {
  readyState: 'complete', body: {},
  querySelector(sel) { scans++; return rows.length && sel.indexOf('health:') >= 0 ? row : null; },
  querySelectorAll() { scans++; return rows; },
};
globalThis.MutationObserver = class { constructor(cb) { observer = cb; } observe() {} };
const staged = [];
globalThis.fridayStage = { domList(id) { staged.push(id); return () => {}; } };
require(process.argv[process.argv.length - 1]);
const afterStart = scans;
let t = 0, stagedAt = null, scansBeforeRow = null;
// The page's text changes every 100 ms for 2 s; a health row arrives at 1000 ms.
const tick = setInterval(() => {
  t += 100;
  if (t === 1000) { scansBeforeRow = scans - afterStart; rows = [row]; observer([{ addedNodes: [row], removedNodes: [] }]); }
  else observer([{ addedNodes: [text], removedNodes: [text] }]);
  if (stagedAt === null && staged.length) stagedAt = t;
}, 100);
setTimeout(() => {
  clearInterval(tick);
  console.log(JSON.stringify({ staged, stagedAt, scansBeforeRow }));
}, 2000);
"""


def _run():
    r = subprocess.run([node, "-e", HARNESS, str(ROOT / "static" / "workspace_stages.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_row_that_arrives_on_a_busy_page_gets_its_stage_soon():
    out = _run()
    assert out["staged"] == ["health"], "the registrar never looked while the page kept changing: %s" % out
    assert out["stagedAt"] is not None and out["stagedAt"] <= 1000 + 600, out


@pytest.mark.skipif(not node, reason="node is not installed")
def test_changes_that_touch_no_marked_row_cost_no_scan():
    assert _run()["scansBeforeRow"] == 0
