"""The desktop sky shows success stars only, for the status the server sends.

Two owner-facing rules, driven under node against the page's own <orb-life>
and <orb-sky> blocks in both scene files:

* Tasks report "complete" (services/agent.py, item_actions, scheduler), but
  the sky only accepted "completed|done", so a finished helper never got its
  star. FridayOrbLife.statusOf normalises a status once and every reader uses
  it.
* A failed, errored, timed-out or cancelled task draws nothing in the sky: no
  hollow red ring. Failures stay in Activity. Process orbs keep their own
  failure ring; that is a different object and is not touched here.

Red on the old page: the "complete" task leaves no star, and the failed task
leaves a ring.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
node = shutil.which("node")

HARNESS = r"""
const made = [];
const mkEl = () => { const e = { style: {}, className: '', isConnected: true, children: [],
  appendChild(c) { this.children.push(c); return c; }, remove() { this.isConnected = false; } }; made.push(e); return e; };
global.window = { innerWidth: 1280, innerHeight: 800, addEventListener() {} };
global.document = { body: mkEl(), createElement: mkEl };
global.requestAnimationFrame = f => { f(); return 1; };
global.fetch = () => Promise.resolve({ ok: false });
BLOCKS
const now = Date.now() / 1000;
const out = {};
FridayOrbSky.sync([
  { task_id: 'done-complete', status: 'complete', ended: now },
  { task_id: 'done-completed', status: 'completed', ended: now },
  { task_id: 'done-upper', status: 'Complete', ended: now },
  { task_id: 'broke', status: 'failed', ended: now },
  { task_id: 'errored', status: 'error', ended: now },
  { task_id: 'slow', status: 'timeout', ended: now },
  { task_id: 'stopped', status: 'cancelled', ended: now },
  { task_id: 'still-running', status: 'running' },
]);
out.stars = FridayOrbSky.list().map(s => ({ id: s.id, failed: s.failed }));
const css = made.map(e => String(e.style.cssText || '') + ' ' + String(e.className || ''));
out.ringish = css.filter(c => /border-radius:50%|#ef4444|\bfailed\b/.test(c)).length;
out.star_els = css.filter(c => /friday-star/.test(c)).length;
out.finished_complete = FridayOrbLife.isFinished({ status: 'complete' });
const norm = typeof FridayOrbLife.statusOf === 'function' ? FridayOrbLife.statusOf : () => null;
out.status_complete = norm({ status: 'complete' });
out.status_failed = norm('failed');
console.log(JSON.stringify(out));
"""


def _run(path):
    text = path.read_text(encoding="utf-8")
    blocks = []
    for b in ("orb-life", "orb-sky"):
        m = re.search(r"// <" + b + r">\n(.*?)// </" + b + r">", text, re.S)
        assert m, f"{path.name}: no <{b}>"
        blocks.append(m.group(1))
    src = HARNESS.replace("BLOCKS", "\n".join(blocks))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_a_complete_task_earns_its_success_star(scene):
    out = _run(scene)
    stars = {s["id"]: s["failed"] for s in out["stars"]}
    assert stars.get("done-complete") is False, "a task reporting 'complete' must draw a success star"
    assert stars.get("done-completed") is False
    assert stars.get("done-upper") is False
    assert "still-running" not in stars
    assert out["finished_complete"] is True
    assert out["status_complete"] == "completed"
    assert out["status_failed"] == "failed"


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_a_failed_task_draws_no_ring_in_the_sky(scene):
    out = _run(scene)
    ids = [s["id"] for s in out["stars"]]
    for gone in ("broke", "errored", "slow", "stopped"):
        assert gone not in ids, f"a '{gone}' task must not appear in the sky"
    assert all(s["failed"] is False for s in out["stars"])
    assert out["ringish"] == 0, "no hollow red ring element may be created"
    assert out["star_els"] == 3


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_the_sky_has_no_failure_ring_style_and_readers_share_one_normaliser(scene):
    text = scene.read_text(encoding="utf-8")
    sky = re.search(r"// <orb-sky>\n(.*?)// </orb-sky>", text, re.S).group(1)
    assert "#ef4444" not in sky and "border-radius:50%" not in sky
    assert "if (o.status === 'completed') receipt(o, s);" not in text
    assert "if (upd.status === 'completed') {" not in text
    assert "FridayOrbLife.statusOf(t)" in text
