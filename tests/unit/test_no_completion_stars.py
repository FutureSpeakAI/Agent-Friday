"""No completion stars on the avatar stage.

Stephen, 2026-11-13: "I also still see completion stars on the Friday desktop (PC) holographic UI.
I want those nixed. No more completion stars, please."

FridayOrbSky was a DOM layer over the scene that drew one four-point sparkle for every task finished
today, polled from /api/tasks. It sat over the stage in every avatar mode (it is not part of the
canvas, so hologram and face tracking carried it too). It is gone, with its pointer hit-testing in the
orb hands, its placement helper and its poll. A finished task is still in Activity and the tasks
surface; nothing about it is drawn on the avatar.

Guarded, on both pages (index.html is served, ui_parts is its mirror):

* the layer, its element classes and its hooks no longer exist, and nothing else in the scene
  creates an element whose class names a star or a sparkle;
* loading the orb blocks defines no sky, and the status normaliser that decides what "finished"
  means for the orbs and Activity still works (a task reporting "complete" is finished);
* the orb hands read the pointer without a star branch.
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
global.window = { innerWidth: 1280, innerHeight: 800, addEventListener() {} };
global.document = { body: { appendChild() {} }, createElement: () => { throw new Error('the orb blocks must not build DOM at load'); } };
BLOCKS
const out = {};
out.sky = typeof FridayOrbSky;
out.complete = FridayOrbLife.statusOf({ status: 'complete' });
out.completed = FridayOrbLife.statusOf({ status: 'Completed' });
out.failed = FridayOrbLife.statusOf('failed');
out.finished = FridayOrbLife.isFinished({ status: 'complete' });
out.starAt = typeof FridayOrbLife.starAt;
console.log(JSON.stringify(out));
"""


def _text(scene):
    return scene.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_the_completion_star_layer_and_its_hooks_are_gone(scene):
    t = _text(scene)
    for gone in ("FridayOrbSky", "friday-orb-sky", "friday-star", "<orb-sky>", "</orb-sky>", "orbSky"):
        assert gone not in t, "%s still has %r: the completion-star layer must not exist" % (scene.name, gone)
    assert not re.search(r"\bfunction starAt\(id, w, h\)", t) and "receipt, starAt, keepOut" not in t


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_nothing_in_the_scene_builds_an_element_named_for_a_star_or_a_sparkle(scene):
    t = _text(scene)
    named = re.findall(r"""className\s*=\s*['"][^'"]*(?:star|sparkle|twinkle)[^'"]*['"]""", t, re.I)
    named += re.findall(r"""classList\.add\(\s*['"][^'"]*(?:star|sparkle|twinkle)[^'"]*['"]""", t, re.I)
    assert named == [], "an element is created with a star or sparkle class: %s" % named


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_the_orb_hands_have_no_star_branch(scene):
    hands = re.search(r"// <orb-hands>\n(.*?)// </orb-hands>", _text(scene), re.S).group(1)
    assert "sky" not in hands.lower() and "star" not in hands.replace("start", "").replace("Start", "").lower()


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.name)
def test_loading_the_orb_blocks_defines_no_sky_and_the_status_normaliser_still_works(scene):
    t = _text(scene)
    blocks = []
    for b in ("orb-life", "orb-hands"):
        m = re.search(r"// <" + b + r">\n(.*?)// </" + b + r">", t, re.S)
        assert m, "%s: no <%s>" % (scene.name, b)
        blocks.append(m.group(1))
    # <orb-hands> needs the page's globals only when it runs; loading it is enough to prove it has no sky.
    src = HARNESS.replace("BLOCKS", "\n".join(blocks[:1]))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["sky"] == "undefined" and out["starAt"] == "undefined"
    assert out["complete"] == "completed" and out["completed"] == "completed" and out["failed"] == "failed"
    assert out["finished"] is True
