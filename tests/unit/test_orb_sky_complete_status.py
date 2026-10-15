"""FridayOrbSky's success stars render for the status the server really sends.

Tasks report "complete" (services/agent.py, item_actions, scheduler,
misc_engine), but the sky only accepted "completed|done", so a finished
helper never got its star (field install report, Part 1 #10 / Part 2 #12).
The page now normalises a task's status in one place,
FridayOrbLife.statusOf, and every orb reader goes through it.

Driven under node against the page's own <orb-life> and <orb-sky> blocks,
from both scene files. Red on origin/main: the "complete" task leaves no star.
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
const mkEl = () => ({ style: {}, className: '', isConnected: true, children: [],
  appendChild(c) { this.children.push(c); return c; }, remove() { this.isConnected = false; } });
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
  { task_id: 'still-running', status: 'running' },
]);
out.stars = FridayOrbSky.list().map(s => ({ id: s.id, failed: s.failed }));
out.finished_complete = FridayOrbLife.isFinished({ status: 'complete' });
out.status_complete = FridayOrbLife.statusOf({ status: 'complete' });
out.status_failed = FridayOrbLife.statusOf('failed');
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
    assert stars.get("broke") is True
    assert "still-running" not in stars
    assert out["finished_complete"] is True
    assert out["status_complete"] == "completed"
    assert out["status_failed"] == "failed"


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_orb_readers_compare_through_the_one_normaliser(scene):
    text = scene.read_text(encoding="utf-8")
    assert "if (o.status === 'completed') receipt(o, s);" not in text
    assert "if (upd.status === 'completed') {" not in text
    assert "FridayOrbLife.statusOf(t)" in text
