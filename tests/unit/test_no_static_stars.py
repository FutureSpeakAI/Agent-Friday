"""The avatar home draws no resting star dots.

Stephen, 2026-11-13: "I see some stationary pixels on the tv home screen and on the desktop home
screen ... I don't want the static pixels on the Avatar home screens."

The dots are FridayField, the scene's world of stars (1,200 to 7,000 of them). Each star used to carry a
resting brightness (0.42 plus its phase), so the whole field was always on screen; when Friday is
offline, or reduced motion is on, the field stands still and reads as static pixels.

What the field is for is Friday's STATE, so that stays: a star lights only when something real happens.
The cloud-send constellation (the privacy signal: it fires only on her sealed cloud egress) lights the
stars that are its nodes, and the local-work glow still warms the stars nearest her. What goes is the
resting layer: with no cloud send and no local work, no star draws at all.

Run under node from both scene files (index.html is served, ui_parts is its mirror): the shader's
brightness expression is evaluated for real, no GPU.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
BRIGHT = re.compile(r"vBright\s*=\s*(.*?);\s*\n\s*vPhase", re.S)

HARNESS = r"""
const smoothstep = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const bright = (phase, uGlow, near, vPulse, zf) => EXPR;
const out = { rest: [], pulse: [], glow: [] };
for (const phase of [0, 0.25, 0.5, 0.75, 1]) for (const near of [0, 0.5, 1]) for (const zf of [0.15, 0.3, 0.5, 0.7, 0.85]) {
  out.rest.push(bright(phase, 0, near, 0, zf));
  out.pulse.push(bright(phase, 0, near, 1, zf));
  out.glow.push(bright(phase, 1, near, 0, zf) * (near > 0 ? 1 : 0));
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def lit(request):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = request.param.read_text(encoding="utf-8")
    m = BRIGHT.search(source)
    assert m, "the world-field vertex shader no longer sets vBright"
    expr = m.group(1).strip()
    run = subprocess.run([node, "-"], input=HARNESS.replace("EXPR", expr), text=True, capture_output=True,
                         encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout)


def test_a_star_with_nothing_happening_draws_nothing(lit):
    assert max(lit["rest"]) == 0, "a resting star is still lit: the field shows as static pixels"


def test_a_cloud_send_still_lights_its_stars(lit):
    assert min(lit["pulse"]) > 0, "the cloud-send constellation must still light the stars it runs through"


def test_local_work_still_glows_the_stars_nearest_her(lit):
    assert max(lit["glow"]) > 0
