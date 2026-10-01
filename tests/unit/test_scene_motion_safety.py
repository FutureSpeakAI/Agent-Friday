"""The holographic scene's motion safety, run under node, in both UI files.

A structure change used to add +4.0 bloom in one frame, which doubled the
whole scene's brightness at once, and the 3D scene never read
prefers-reduced-motion. `SceneMotion` (between the <scene-motion> markers in
index.html and its mirror ui_parts/styles_and_scene.html) now owns those
rules; these tests run that block for real and check the frame loop uses it.
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

BLOCK_RE = re.compile(r"// <scene-motion>\n(.*?)// </scene-motion>", re.S)

HARNESS = r"""
let listener = null;
const mq = { matches: REDUCED, addEventListener(_, f) { listener = f; } };
globalThis.window = { matchMedia: () => mq };
BLOCK
const out = {};
const dt = 1 / 60;
out.reduced = SceneMotion.reduced();
out.started = SceneMotion.startFlash(10);
const env = [];
for (let i = 0; i < 240; i++) env.push(SceneMotion.tick(dt));
out.first_frame = env[0];
out.peak = Math.max(...env);
out.frames_to_peak = env.indexOf(out.peak) + 1;
out.after_4s = env[env.length - 1];
out.bloom_at_peak = SceneMotion.flashBloom(out.peak);
out.again_too_soon = SceneMotion.startFlash(10.5);
out.again_later = SceneMotion.startFlash(12);
out.scale_in = SceneMotion.transitionScale(0.5, true);
out.scale_out = SceneMotion.transitionScale(0.5, false);
out.speech = SceneMotion.speechGain();
out.timelapse = SceneMotion.timeLapseAllowed();
// the OS setting flips while the page is open
if (listener) listener({ matches: !REDUCED });
out.flipped = SceneMotion.reduced();
out.flipped_start = SceneMotion.startFlash(20);
out.flipped_tick = SceneMotion.tick(dt);
console.log(JSON.stringify(out));
"""


def _block(path):
    m = BLOCK_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <scene-motion> block"
    assert len(BLOCK_RE.findall(path.read_text(encoding="utf-8"))) == 1, \
        f"{path.name}: more than one <scene-motion> block"
    return m.group(1)


def _run(path, reduced):
    src = HARNESS.replace("BLOCK", _block(path)).replace("REDUCED", "true" if reduced else "false")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_change_flash_rises_gently_and_is_capped(path):
    o = _run(path, reduced=False)
    assert o["reduced"] is False and o["started"] is True
    # no single-frame jump: the first frame is a small part of the peak
    assert o["first_frame"] <= 0.1
    assert o["frames_to_peak"] >= 20            # about 0.4 s at 60 fps
    assert o["peak"] == 1
    assert o["bloom_at_peak"] <= 1.0            # was 4.0
    assert o["after_4s"] < o["peak"]            # and it decays
    # switching again at once does not flash again
    assert o["again_too_soon"] is False and o["again_later"] is True
    # the ordinary transition is unchanged
    assert o["scale_in"] == pytest.approx(1.1) and o["scale_out"] == pytest.approx(1.25)
    assert o["speech"] == 1 and o["timelapse"] is True
    # turning reduced motion on mid-session stops the flash at once
    assert o["flipped"] is True and o["flipped_start"] is False and o["flipped_tick"] == 0


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_reduced_motion_means_no_flash_no_zoom_no_time_lapse(path):
    o = _run(path, reduced=True)
    assert o["reduced"] is True
    assert o["started"] is False and o["peak"] == 0
    assert o["scale_in"] == 1 and o["scale_out"] == 1
    assert o["speech"] == 0.5
    assert o["timelapse"] is False
    assert o["flipped"] is False and o["flipped_start"] is True


# The frame loop must actually go through SceneMotion. Each anchor is checked
# to occur exactly once, so a second copy elsewhere cannot satisfy it silently.
WIRING = [
    # A structure change starts its glow through SceneMotion (setEvolution).
    "SceneMotion.startFlash(performance.now() / 1000);",
    "metamorphosisFlash = SceneMotion.tick(delta);",
    "bloomPass.strength = Math.max(0.7, currentBloom) + SceneMotion.flashBloom(metamorphosisFlash);",
    "g.scale.setScalar(SceneMotion.transitionScale(ease, true)); }",
    "g.scale.setScalar(SceneMotion.transitionScale(ease, false)); }",
    "holoTargetAmplitude = (window._fridayHoloAmplitude || 0) * SceneMotion.speechGain()",
    "* (SceneMotion.reduced() ? 1 : FridayGenome.speechAmplitude());",
    "const _motionMood = (_speaking && !SceneMotion.reduced()) ? tMood : MOODS.IDLE;",
    "if (!timeLapseActive && !SceneMotion.timeLapseAllowed()) return false;",
    "if (timeLapseActive && SceneMotion.timeLapseAllowed()) {",
]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_frame_loop_uses_scene_motion(path):
    text = path.read_text(encoding="utf-8")
    for anchor in WIRING:
        assert text.count(anchor) == 1, f"{path.name}: {anchor!r} x{text.count(anchor)}"
    assert "metamorphosisFlash * 4.0" not in text
    assert "metamorphosisFlash = 1.0" not in text
