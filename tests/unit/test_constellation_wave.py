"""A real cloud send reveals a travelling constellation of the scene's stars."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts/styles_and_scene.html"]

HARNESS = r"""
const THREE = require(process.argv[2]);
global.window = { __fridayLowCost: true };
Object.defineProperty(globalThis, 'navigator', { value: { hardwareConcurrency: 4, deviceMemory: 4 }, configurable: true });
global.document = { body: { classList: { contains: () => false } } };
let seed = 83;
Math.random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
BLOCK
const scene = new THREE.Scene(), F = FridayField, S = FridayFieldState;
F.build(scene); S.reset();
const send = () => S.frame({type:'presence',agent:'friday',state:'egress',phase:'sent',route:'cloud'});
const advance = sec => { for(let i=0; i<Math.round(sec*30);i++) F.update(1/30, {}); return F._web(); };
const out = { idle: advance(2) };
send(); out.first = advance(0.1); out.middle = advance(2.5);
const web = scene.children.find(o => o.userData.constellation) || scene.children.find(o => o.isLineSegments);
const stars = scene.children.find(o => o.userData.worldField);
out.depthWrites = [web.material.depthWrite, stars.material.depthWrite];
out.positionBuffer = web.geometry.attributes.position.count;
out.drawCount = web.geometry.drawRange.count;
out.middleDepth = Math.min(...Array.from(web.geometry.attributes.position.array.slice(0, out.drawCount * 3)).filter((v,i)=>i%3===2));
out.finite = Array.from(web.geometry.attributes.position.array).every(Number.isFinite);
const p = web.geometry.attributes.position.array;
out.anchored = false;
if (out.middle.ids) {
  // Every line segment starts or ends on the same shader-driven stars;
  // sample the endpoints of every subdivided edge.
  const current = out.middle.ids.map(i => F._posOf(i, [0,0,0]));
  out.anchored = true;
  for (let i=0; i<web.geometry.drawRange.count; i+=12) for(const j of [i, i+11]) {
    if (!current.some(v => Math.hypot(v[0]-p[j*3],v[1]-p[j*3+1],v[2]-p[j*3+2])<0.0001)) out.anchored=false;
  }
}
out.late = advance(2.8); out.afterWave = advance(4);
send(); out.second = advance(1.5);
S.frame({type:'presence',agent:'friday',state:'route',phase:'step',route:'cloud'});
out.afterAnswer = advance(2);
out.afterTail = advance(6);
send(); advance(0.3); send(); advance(0.3); send(); advance(0.3); send();
out.burst = advance(1);
S.reset(); F.dispose(); F.build(scene);
const before = F._posOf(0, [0,0,0]);
for(let i=0;i<90;i++) F.update(1/30,{reduced:true});
out.reducedDrift = Math.hypot(...F._posOf(0,[0,0,0]).map((v,i)=>v-before[i]));
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def wave(request):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = request.param.read_text(encoding="utf-8")
    block = re.search(r"// <world-field>\n(.*?)// </world-field>", source, re.S)
    assert block
    three = ROOT / "static/vendor/three-r128.min.js"
    run = subprocess.run([node, "-", str(three)], input=HARNESS.replace("BLOCK", block.group(1)),
                         text=True, capture_output=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout)


def test_constellation_is_dark_until_send_and_after_wave(wave):
    assert not wave["idle"]["visible"]
    assert wave["middle"]["visible"]
    assert not wave["afterWave"]["visible"], "links must fade behind the wave, even while awaiting a reply"
    assert wave["afterAnswer"]["visible"], "a fast reply must not abort an already-sent pulse"
    assert not wave["afterTail"]["visible"]


def test_wave_branches_across_depth_without_lighting_every_edge(wave):
    mid = wave["middle"]
    assert wave["middleDepth"] < -100, "the drawn constellation must reach the background starfield"
    assert mid.get("nodes", 0) >= 150
    assert mid.get("depth", 0) < -100, "the constellation must reach the background starfield"
    assert 0 < mid.get("lit", 0) < mid["edges"] * 0.8
    assert wave["late"]["visible"]
    assert len(set(mid["ids"])) == mid["nodes"]
    assert all(value > 0 for value in mid["routes"])


def test_edges_stay_attached_to_stars_and_new_sends_vary_routes(wave):
    assert wave["anchored"]
    assert wave["middle"]["ids"] != wave["second"]["ids"]
    assert wave["finite"]
    assert wave["drawCount"] <= wave["positionBuffer"]
    assert wave["depthWrites"] == [False, False]
    assert wave["burst"].get("waves", 99) <= 3


def test_reduced_motion_stops_starfield_travel(wave):
    assert wave["reducedDrift"] == pytest.approx(0)
