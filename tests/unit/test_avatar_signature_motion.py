"""Avatar articulation follows real state and preserves each form's anatomy.

The scene's actual motion code runs with the vendored Three.js under Node.
No camera, model, browser or application home is opened by these tests.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
THREE = ROOT / "static" / "vendor" / "three-r128.min.js"
NODE = shutil.which("node")
BLOCK = re.compile(r"// <avatar-signature-motion>\n(.*?)// </avatar-signature-motion>", re.S)
FORMS = ["CUBES", "DOME", "GRID", "MANDELBROT", "ASTROLABE", "TESSERACT", "NETWORK", "MOBIUS", "CABLES"]


def _node(script):
    if not NODE:
        pytest.skip("node is not installed")
    result = subprocess.run([NODE, "-"], input=script, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr[-2500:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def motion(request):
    source = request.param.read_text(encoding="utf-8")
    block = BLOCK.search(source)
    assert block, "the scene has no avatar articulation engine"
    harness = r"""
const F = FridayAvatarMotion, forms = FORMS, out = {};
const run = (seconds, input, hz = 60) => {
  let state;
  for (let i = 0; i < Math.round(seconds * hz); i++) state = F.step(1 / hz, input);
  return Object.assign({}, state);
};
const pose = () => Object.fromEntries(forms.map(id => [id, Object.assign({}, F.sample(id, 0.7, 0.3, {}))]));
F.reset(); run(4, { mood: 'IDLE', voice: 0.9, speaking: false });
out.noSpeech = pose();
F.reset(); out.voice = run(4, { mood: 'SPEAKING', voice: 0.8, speaking: true }); out.speaking = pose();
F.reset(); out.process = run(4, { mood: 'EXECUTING', voice: 0, speaking: false }); out.processing = pose();
F.reset(); out.create = run(4, { mood: 'CREATING' }); out.creating = pose();
out.spatial = Object.fromEntries(forms.map(id => [id, [F.sample(id, 0.1, 0.2, {}), F.sample(id, 0.9, 0.8, {})]]));
run(12, { mood: 'IDLE' }); out.settled = pose();
F.reset(); run(4, { mood: 'EXECUTING', speaking: true, voice: 1 });
const before = F.step(0, {}).phase;
out.reducedState = run(3, { mood: 'EXECUTING', speaking: true, voice: 1, reduced: true });
out.reduced = pose(); out.reducedClock = out.reducedState.phase - before;
out.rates = [30, 60, 120].map(hz => { F.reset(); run(4, { mood: 'CREATING', speaking: true, voice: 0.8 }, hz); return pose(); });
F.reset(); let max = 0;
for (let frame = 0; frame < 600; frame++) {
  F.step(1 / 60, { mood: frame % 80 < 40 ? 'EXECUTING' : 'CREATING', voice: 1, speaking: true });
  forms.forEach(id => Object.values(F.sample(id, 0.7, 0.3, {})).forEach(v => { max = Math.max(max, Math.abs(v)); }));
}
out.max = max;
console.log(JSON.stringify(out));
""".replace("FORMS", json.dumps(FORMS))
    return _node(block.group(1) + harness)


def _size(pose):
    return sum(abs(v) for v in pose.values())


def test_processing_has_its_own_response_without_pretending_to_speak(motion):
    assert motion["process"]["voice"] == 0
    assert motion["process"]["processing"] > 0.99
    assert motion["process"]["creating"] == 0
    for name in FORMS:
        assert _size(motion["processing"][name]) > 0.002, name
        assert motion["processing"][name] != motion["speaking"][name], name


def test_every_legacy_form_has_a_distinct_speech_signature(motion):
    fingerprints = set()
    for name in FORMS:
        pose = motion["speaking"][name]
        assert _size(pose) > 0.002, name
        fingerprints.add(tuple(round(pose[k], 6) for k in sorted(pose)))
    assert len(fingerprints) == len(FORMS), "different anatomies must not share one scale pulse"


def test_creating_articulates_parts_instead_of_only_scaling_the_whole_form(motion):
    assert motion["create"]["creating"] > 0.99
    for name in FORMS:
        poses = [motion["creating"][name], *motion["spatial"][name]]
        assert max(_size(p) for p in poses) > 0.002, name
        if name != "TESSERACT":  # its two independent 4D rotations move each vertex differently
            assert motion["spatial"][name][0] != motion["spatial"][name][1], name


def test_unplayed_audio_and_idle_do_not_fabricate_activity(motion):
    assert all(_size(p) == 0 for p in motion["noSpeech"].values())
    assert all(_size(p) < 1e-5 for p in motion["settled"].values())


def test_added_motion_is_bounded_and_still_under_reduced_motion(motion):
    assert 0.1 < motion["max"] < 1.5
    assert motion["reducedClock"] == 0
    assert all(_size(p) == 0 for p in motion["reduced"].values())


def test_articulation_does_not_speed_up_with_the_display_refresh_rate(motion):
    for name in FORMS:
        for channel in ("lift", "twist", "scale"):
            values = [r[name][channel] for r in motion["rates"]]
            assert max(values) - min(values) < 1e-10, (name, channel, values)


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_faint_energy_flares_cannot_cut_depth_holes_through_avatars(path):
    source = path.read_text(encoding="utf-8")
    creator = source.split("function createEnergyFlare() {", 1)[1].split("function createMaterial(", 1)[0]
    script = "const THREE = require(%s); const scene = new THREE.Scene(), energyLines = [];\n" % json.dumps(str(THREE))
    script += "function createEnergyFlare() {" + creator
    script += """
createEnergyFlare();
const line = energyLines[0], m = line.material;
console.log(JSON.stringify({ count: scene.children.length, points: line.geometry.attributes.position.count,
  depthWrite: m.depthWrite, depthTest: m.depthTest, transparent: m.transparent,
  additive: m.blending === THREE.AdditiveBlending, opacity: m.opacity }));
"""
    material = _node(script)
    assert material["count"] == 1 and material["points"] == 51
    assert material["transparent"] and material["additive"] and material["opacity"] > 0
    assert material["depthTest"], "the flare must still respect opaque geometry in front of it"
    assert not material["depthWrite"], "a faint decorative line must not erase the transparent avatar behind it"


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_scientific_forms_receive_processing_even_when_no_voice_is_playing(path):
    source = path.read_text(encoding="utf-8")
    block = BLOCK.search(source)
    assert block
    declaration = re.search(r"const herState = \{.*?\};", source, re.S)
    assert declaration
    script = block.group(1) + """
let avatarState;
for (let i = 0; i < 240; i++) avatarState = FridayAvatarMotion.step(1 / 60, { mood: 'EXECUTING', voice: 0 });
const holoAmplitude = 0, currentMood = 'EXECUTING', moodLerpValues = { baseColor: 0, accentColor: 0 };
const SceneMotion = { reduced: () => false };
""" + declaration.group(0) + "\nconsole.log(JSON.stringify(herState));"
    state = _node(script)
    assert state["voice"] == 0
    assert state["thinking"] > 0.99, "tool execution must reach each scientific form's own processing animator"
    assert state["creating"] == 0
