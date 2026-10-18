"""The holographic scene gives way on a machine that cannot draw it quickly.

With no GPU every frame of the scene is drawn in software, and a scene that asks
for the next frame as soon as the last one lands takes the whole machine: on the
Windows CI runner (software WebGL) the desktop page's own controls answered in
seconds, and connect_services.spec.ts ran out of its 180 s budget on one run in
three. `ScenePacer` (between the <scene-pacer> markers in index.html and its
mirror ui_parts/styles_and_scene.html) spaces the draws after a slow frame so
the scene takes about a third of the time, and changes nothing on a machine
that keeps up. These tests run that block under node and check the frame loop
goes through it.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
node = shutil.which("node")

BLOCK_RE = re.compile(r"// <scene-pacer>\n(.*?)// </scene-pacer>", re.S)

# A frame loop driven by a fake clock: a drawn frame costs DRAW ms before the
# next callback, a frame that waits costs IDLE ms (one display refresh).
HARNESS = r"""
let onVisibility = null;
globalThis.document = { addEventListener(type, f) { if (type === 'visibilitychange') onVisibility = f; } };
BLOCK
function run(drawMs, idleMs, totalMs, t0) {
  let t = t0, drawn = 0, drawingMs = 0, frames = 0, longestGap = 0, lastDraw = null;
  while (t < t0 + totalMs) {
    frames++;
    if (ScenePacer.draw(t)) {
      if (lastDraw !== null) longestGap = Math.max(longestGap, t - lastDraw);
      lastDraw = t; drawn++; drawingMs += drawMs; t += drawMs;
    } else t += idleMs;
  }
  return { drawn, frames, share: drawingMs / (t - t0), longestGap, end: t };
}
const out = {};
out.fast = run(16.7, 16.7, 10000, 0);
ScenePacer.reset();
out.slow = run(300, 16.7, 30000, 0);
ScenePacer.reset();
out.very_slow = run(3000, 16.7, 60000, 0);
// The machine recovers (the GPU comes back): every frame is drawn again.
const after = run(16.7, 16.7, 10000, out.very_slow.end);
out.recovered_tail = after;
// A hidden tab: frames stop, then resume a minute later. That gap is not a
// slow frame, so the first frame back is drawn.
ScenePacer.reset();
run(16.7, 16.7, 1000, 0);
if (onVisibility) onVisibility();
out.after_hidden = ScenePacer.draw(61000);
out.has_listener = !!onVisibility;
out.consts = { slow: ScenePacer.SLOW_MS, share: ScenePacer.SHARE, max: ScenePacer.MAX_WAIT_MS };
console.log(JSON.stringify(out));
"""


def _block(path):
    text = path.read_text(encoding="utf-8")
    found = BLOCK_RE.findall(text)
    assert found, f"{path.name}: no <scene-pacer> block"
    assert len(found) == 1, f"{path.name}: more than one <scene-pacer> block"
    return found[0]


def _run(path):
    src = HARNESS.replace("BLOCK", _block(path))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_a_machine_that_keeps_up_draws_every_frame(path):
    o = _run(path)
    assert o["fast"]["drawn"] == o["fast"]["frames"]


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_a_slow_machine_keeps_most_of_its_time(path):
    o = _run(path)
    # 300 ms frames: before, the scene drew back to back (share 1.0).
    assert 0.25 <= o["slow"]["share"] <= 0.40, o["slow"]
    assert o["slow"]["drawn"] >= 20                      # it still moves
    # 3 s frames: the wait is capped, so a new picture still comes.
    assert o["very_slow"]["share"] <= 0.65, o["very_slow"]
    assert o["very_slow"]["longestGap"] <= 3000 + 2000 + 20


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_recovery_and_a_hidden_tab_are_not_slow_frames(path):
    o = _run(path)
    tail = o["recovered_tail"]
    # after at most one capped wait, every frame is drawn again
    assert tail["frames"] - tail["drawn"] <= 2000 / 16.7 + 1
    assert o["has_listener"] is True
    assert o["after_hidden"] is True
    assert o["consts"] == {"slow": 66, "share": pytest.approx(1 / 3), "max": 2000}


# The frame loop must actually go through ScenePacer.
WIRING = [
    "if (!ScenePacer.draw(performance.now())) { /* this frame waits */ }",
    "else if (window.__fridayLowCost) renderer.render(scene, camera);",
    "} else ScenePacer.reset();",
]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_frame_loop_draws_through_the_pacer(path):
    text = path.read_text(encoding="utf-8")
    for anchor in WIRING:
        assert text.count(anchor) == 1, f"{path.name}: {anchor!r} x{text.count(anchor)}"


def test_held_scenes_do_not_count_as_slow_frames():
    text = (ROOT / "index.html").read_text(encoding="utf-8")
    assert text.count("if (sceneContextLost) { ScenePacer.reset(); return; }") == 1
    assert text.count("if (callHold) { ScenePacer.reset(); return; }") == 1
