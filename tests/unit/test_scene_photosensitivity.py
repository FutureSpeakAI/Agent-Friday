"""The holographic scene never flashes (WCAG 2.3.1). The rendered-frame test
is tests/app/specs/photosensitivity.spec.ts; these are the fast guards on the
code that makes it so, in both scene files:

- the brightness governor is the composer's last pass;
- the scene draws from sound (the microphone's bands and Friday's voice)
  only through the slow envelope;
- an energy line surges, never jumps to white;
- the ocean's camera glides, never snaps back;
- a structure change never pops a structure: run under node, the page's
  own setEvolution keeps every structure's opacity continuous when a change
  is asked for mid-crossfade, asked for again, or reversed.
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


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_the_governor_is_the_last_pass_and_sound_is_enveloped(path):
    t = path.read_text(encoding="utf-8")
    assert "composer.addPass(holoPass);" in t
    after = t.split("composer.addPass(holoPass);", 1)[1][:400]
    assert "composer.addPass(BrightnessGovernor.create());" in after
    assert t.count("// <brightness-governor>") == 1 and "MAX_L_PER_S = 25" in t
    assert "for (const k in audioEnv) audioData[k] = audioEnv[k] = soundEnvelope(audioEnv[k], audioData[k], delta);" in t
    assert "holoAmplitude = soundEnvelope(holoAmplitude, holoTargetAmplitude, delta);" in t
    assert "line.userData.intensity = 1.0" not in t
    assert "(elapsed*2) % 10" not in t


def _fn(text, name):
    m = re.search(r"\n        function %s\(.*?\n        }\n" % name, text, re.S)
    assert m, name
    return m.group(0)


HARNESS = r"""
const EVOLUTION_PATH = [{ id: 'A', name: 'a' }, { id: 'B', name: 'b' }, { id: 'C', name: 'c' }];
let currentStructure = 'A', targetStructure = 'A', transitionProgress = 1.0, currentEvolutionIdx = 0;
let leavingFrom = 1.0, leavingStructures = [];
const structures = { A: { visible: true }, B: { visible: false }, C: { visible: false } };
const SceneMotion = { startFlash() {} };
const document = { getElementById() { return null; } };
FUNCS
const snap = () => ['A', 'B', 'C'].map(k => +shownOpacity(k).toFixed(4));
const out = {};
setEvolution(1); out.start = snap();                         // B starts arriving from nothing
transitionProgress = 0.5; out.midBefore = snap();
setEvolution(2); out.midAfter = snap();                      // C asked for mid-crossfade
transitionProgress = 0.3; out.again = snap();
setEvolution(2); out.againAfter = snap();                    // asked for again
out.progressKept = transitionProgress;
setEvolution(1); out.backAfter = snap();                     // and back to B
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_a_structure_change_never_pops_a_structure(path):
    t = path.read_text(encoding="utf-8")
    funcs = "".join(_fn(t, n) for n in ("smoothstep", "shownOpacity", "setEvolution"))
    r = subprocess.run([node, "-"], input=HARNESS.replace("FUNCS", funcs), capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["start"] == [1, 0, 0]
    assert o["midAfter"] == o["midBefore"]            # nothing jumps when C is asked for
    assert o["againAfter"] == o["again"] and o["progressKept"] == 0.3
    before_back = o["again"]
    assert abs(o["backAfter"][1] - before_back[1]) < 1e-3   # B continues from what it showed
