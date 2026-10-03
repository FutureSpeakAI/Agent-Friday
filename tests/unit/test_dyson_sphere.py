"""The Dyson Sphere (avatar-visual-genome.md §19), structure ICOSAHEDRON: a star
inside shells of hexagonal collector panels, still being built, with
prominences rising from the star between them.

The module (`// <dyson-sphere>`, FridayDyson) runs under node with the vendored
three.js, lifted verbatim from both scene files:
- it draws in five objects whatever the panel count (two instanced meshes, the
  star, its glow, one point cloud of prominences);
- the panels are shared by area, so the gene for shells changes the layout and
  never the cost, and a coarse build has fewer;
- it is about two fifths built at first and whole after about ten steps, inner
  shells first, and panels a step adds ease in;
- thinking turns the shells faster, never past the flicker cap; listening turns
  the panels toward the viewer; speaking brightens the star and lifts the
  loops, with her voice smoothed so syllables do not strobe it;
- now and then a loop lifts toward the panels and never through them;
- under reduced motion nothing turns and the voice moves nothing;
- the gesture engine's view (units, points, layers, blocks) is consistent.
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
node = shutil.which("node")
BLOCK = re.compile(r"// <dyson-sphere>\n(.*?)// </dyson-sphere>", re.S)

HARNESS = r"""
const THREE = require(THREE_PATH);
const glowTexture = null;
let stepNo = 0;
const FridayGenome = { current: () => ({ step: stepNo ? { step: stepNo } : null }) };
BLOCK
const base = new THREE.Color(0x1e54c7), accent = new THREE.Color(0x5fa8ff);
let seed = 7; const rand = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
const S = (o) => Object.assign({ voice: 0, thinking: false, listening: false, base, accent, reduced: false, toward: [0, 0.35, 1] }, o || {});
const out = {};
function fresh(shells, coarse, step) {
  stepNo = step || 0; seed = 7;
  const g = new THREE.Group(); FridayDyson.build(g, { shells, coarse, rand });
  return g;
}
const draws = g => { let n = 0; g.traverse(o => { if (o.isMesh || o.isPoints || o.isSprite || o.isLine) n++; }); return n; };
const P = () => FridayDyson._parts();
// cost and layout
out.layout = [2, 3, 4].map(k => { const g = fresh(k, false, 0); return { shells: FridayDyson.shells(), draws: draws(g),
  panels: P().body.count, units: FridayDyson.units() }; });
{ const g = fresh(3, true, 0); out.coarse = P().body.count; }
// how built: by steps; inner shells first
const builtAt = step => { fresh(3, false, step); for (let f = 0; f < 60 * 30; f++) FridayDyson.animate(1 / 60, S());
  const byShell = [0, 0, 0]; const tot = [0, 0, 0];
  const m = new THREE.Matrix4(), v = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  const n = P().body.count;
  for (let i = 0; i < n; i++) { P().body.getMatrixAt(i, m); m.decompose(v, q, s); const r = v.length();
    const k = FridayDyson._shellOf(i); tot[k]++; if (s.x > 0.05) byShell[k]++; }
  return { shown: FridayDyson.shown() / n, done: FridayDyson.done(), shells: byShell.map((b, k) => b / Math.max(1, tot[k])) }; };
out.built0 = builtAt(0); out.built5 = builtAt(5); out.built12 = builtAt(12);
// a step lands: new panels ease in, none jumps
{ fresh(3, false, 0); for (let f = 0; f < 60; f++) FridayDyson.animate(1 / 60, S());
  stepNo = 4; const m = new THREE.Matrix4(), v = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  const sizeOf = () => { const a = []; for (let i = 0; i < P().body.count; i++) { P().body.getMatrixAt(i, m); m.decompose(v, q, s); a.push(s.x); } return a; };
  let prev = sizeOf(), maxJump = 0, before = FridayDyson.shown();
  for (let f = 0; f < 60 * 12; f++) { FridayDyson.animate(1 / 60, S()); const now = sizeOf();
    now.forEach((x, i) => { maxJump = Math.max(maxJump, Math.abs(x - prev[i])); }); prev = now; }
  out.stepEase = { before, after: FridayDyson.shown(), maxJump }; }
// turning: thinking faster, capped; reduced: none
const turnRate = (st) => { fresh(3, false, 3); for (let f = 0; f < 120; f++) FridayDyson.animate(1 / 60, st);
  const m = new THREE.Matrix4(), a = new THREE.Vector3(), b = new THREE.Vector3(); P().body.getMatrixAt(0, m); a.setFromMatrixPosition(m);
  FridayDyson.animate(1 / 60, st); P().body.getMatrixAt(0, m); b.setFromMatrixPosition(m);
  return a.angleTo(b) * 60; };
out.turnRest = turnRate(S()); out.turnThink = turnRate(S({ thinking: true })); out.turnReduced = turnRate(S({ reduced: true, thinking: true }));
// listening: the panels face the viewer more
const facing = (st) => { fresh(3, false, 6); for (let f = 0; f < 60 * 4; f++) FridayDyson.animate(1 / 60, st);
  const m = new THREE.Matrix4(), v = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3(), z = new THREE.Vector3();
  const tw = new THREE.Vector3(0, 0.35, 1).normalize(); let sum = 0, n = 0;
  for (let i = 0; i < P().body.count; i++) { P().body.getMatrixAt(i, m); m.decompose(v, q, s); if (s.x < 0.05) continue;
    z.set(0, 0, 1).applyQuaternion(q); sum += z.dot(tw); n++; }
  return sum / n; };
out.faceRest = facing(S()); out.faceListen = facing(S({ listening: true }));
// speaking: the star brightens, the loops rise; a 5 Hz voice does not strobe it
{ fresh(3, false, 0); for (let f = 0; f < 120; f++) FridayDyson.animate(1 / 60, S());
  const u = P().star.material.uniforms, pu = P().proms.material.uniforms;
  out.starRest = u.uBright.value; out.loopRest = pu.uB.value.reduce((a, v) => a + v.w, 0);
  for (let f = 0; f < 120; f++) FridayDyson.animate(1 / 60, S({ voice: 0.8 }));
  out.starSpeak = u.uBright.value; out.loopSpeak = pu.uB.value.reduce((a, v) => a + v.w, 0);
  const b = []; for (let f = 0; f < 180; f++) { FridayDyson.animate(1 / 60, S({ voice: (Math.floor(f / 6) % 2) ? 0.9 : 0.1 })); b.push(u.uBright.value); }
  const tail = b.slice(60); out.strobe = Math.max(...tail) - Math.min(...tail);
  for (let f = 0; f < 120; f++) FridayDyson.animate(1 / 60, S({ voice: 0.9, reduced: true }));
  out.starReducedSpeak = u.uBright.value; }
// eruptions: one loop at a time lifts toward the panels, never through them
{ fresh(3, false, 0); const pu = P().proms.material.uniforms; let maxH = 0, seen = new Set(), maxStep = 0, prev = null;
  for (let f = 0; f < 60 * 90; f++) { FridayDyson.animate(1 / 60, S());
    const hs = pu.uB.value.map(v => v.w); maxH = Math.max(maxH, ...hs);
    if (prev) maxStep = Math.max(maxStep, ...hs.map((h, k) => Math.abs(h - prev[k])) ); prev = hs;
    const e = FridayDyson._erupt(); if (e) seen.add(e.k); }
  out.erupt = { maxTop: FridayDyson.STAR_R * (1 + maxH), inner: FridayDyson.R_IN, loops: seen.size, maxStepPerS: maxStep * 60 }; }
// the gesture engine's view
{ fresh(4, false, 3); FridayDyson.animate(1 / 60, S());
  const n = FridayDyson.units(), pts = FridayDyson.points(), L = FridayDyson.layers(), B = FridayDyson.blocks();
  const all = L.flat().sort((a, b) => a - b);
  out.view = { n, pts: pts.length, finite: pts.every(p => p.every(Number.isFinite)), standing: pts.every(p => Math.hypot(...p) > 2.5),
    layersCover: all.length === n && all.every((k, j) => k === j), blocksOk: B.every(b => b.units.length >= 1 && b.units.length <= 4
      && new Set(b.units.map(k => L.findIndex(l => l.includes(k)))).size === 1) };
  const m = new THREE.Matrix4(), v = new THREE.Vector3(); P().body.getMatrixAt(0, m);
  FridayDyson.applyUnit(0, { d: [0, 0, 0.6], r: [0, 0, 0], s: 1.2, b: 1, g: 0.5, hue: 'approval' }, accent, new THREE.Color(0xf59e0b));
  out.applied = true; }
console.log(JSON.stringify(out));
"""


def _run(path):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <dyson-sphere> block"
    src = HARNESS.replace("BLOCK", m.group(1)).replace("THREE_PATH", json.dumps(str(THREE)))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def dyson(request):
    if not node:
        pytest.skip("node is not installed")
    return _run(request.param)


def test_both_scene_files_carry_the_same_module():
    a, b = (BLOCK.search(p.read_text(encoding="utf-8")).group(1) for p in SCENES)
    assert a == b


def test_five_draws_whatever_the_panels_and_shells_share_them(dyson):
    for k, row in zip([2, 3, 4], dyson["layout"]):
        assert row["shells"] == k
        assert row["draws"] == 5, row
        assert 400 <= row["panels"] <= 440, row
    assert 300 <= dyson["coarse"] <= 330


def test_it_is_still_being_built_and_steps_add_panels_inner_shells_first(dyson):
    b0, b5, b12 = dyson["built0"], dyson["built5"], dyson["built12"]
    assert 0.35 <= b0["shown"] <= 0.6, b0
    assert b0["shells"][0] > b0["shells"][1] > b0["shells"][2], "the inner shells fill first"
    assert b5["shown"] > b0["shown"] + 0.15
    assert b12["shown"] == pytest.approx(1.0)


def test_panels_a_step_adds_ease_in(dyson):
    e = dyson["stepEase"]
    assert e["after"] > e["before"]
    assert e["maxJump"] < 0.04, "a panel appeared in one frame"


def test_thinking_turns_the_shells_faster_never_past_the_cap(dyson):
    assert dyson["turnThink"] > 1.5 * dyson["turnRest"] > 0
    assert dyson["turnThink"] <= 0.3 + 1e-6
    assert dyson["turnReduced"] == 0


def test_listening_turns_the_panels_toward_the_viewer(dyson):
    assert dyson["faceListen"] > dyson["faceRest"] + 0.2


def test_speaking_brightens_the_star_and_lifts_the_loops_without_strobing(dyson):
    assert dyson["starSpeak"] > dyson["starRest"] + 0.1
    assert dyson["loopSpeak"] > 1.15 * dyson["loopRest"]
    # Syllables at five a second: the star's light moves by under a tenth.
    assert dyson["strobe"] < 0.1, dyson["strobe"]
    assert dyson["starReducedSpeak"] == pytest.approx(dyson["starRest"], abs=0.01)


def test_a_loop_now_and_then_lifts_toward_the_panels_never_through_them(dyson):
    e = dyson["erupt"]
    assert e["loops"] >= 2, "over a minute and a half, more than one loop lifted"
    assert 0.85 * e["inner"] < e["maxTop"] < 0.97 * e["inner"], "it reaches toward the inner shell and stops short"
    assert e["maxStepPerS"] < 1.0, "it rises slowly"


def test_the_gesture_engine_sees_standing_panels_in_shells_and_blocks(dyson):
    v = dyson["view"]
    assert v["n"] == v["pts"] > 50
    assert v["finite"] and v["standing"]
    assert v["layersCover"] and v["blocksOk"]
    assert dyson["applied"]
