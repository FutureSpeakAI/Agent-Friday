"""The Einstein-Rosen Bridge (WORMHOLE) and Hawking Radiation (BLACKHOLE),
avatar-visual-genome.md §19, run under node with the vendored three.js, lifted
verbatim from both scene files.

The wormhole (`// <wormhole>`, FridayWormhole):
- the grid lies on Flamm's paraboloid, z = 2 sqrt(rs (r - rs)), both funnels;
  the rings gene sets its rings;
- the far side shows her local work and her cloud sends from the world
  field's state and from nothing else (no state, no web, whatever she does);
- speaking ripples it, thinking tightens the throat and runs light through,
  listening widens the mouth; reduced motion stills the ripples;
- the throat steadies over the weeks, eased, never in a jump;
- the mouth bends the world's dots (a lens the size of the throat).

The black hole (`// <black-hole>`, FridayBlackHole):
- its shader traces Schwarzschild light (x'' = -3/2 h^2 x / r^5), the photon
  ring at the critical impact parameter 3 sqrt(3) / 2, a disk from r = 3 to
  10 with Doppler beaming and gravitational redshift;
- its emission runs from deep ember through pink and white to blue-white,
  never through amber, and its only red is dark;
- Hawking pairs pop in softly, the one falling in fades, its partner leaves
  as a mote that becomes a dot of the world; idle sparse, thinking and
  speaking more, none under reduced motion;
- listening spirals the dust in toward the horizon;
- over the weeks it radiates hotter and its shadow shrinks;
- the world's dots, and its own dust behind it, bend round the shadow.
"""
import colorsys
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
WORM = re.compile(r"// <wormhole>\n(.*?)// </wormhole>", re.S)
HOLE = re.compile(r"// <black-hole>\n(.*?)// </black-hole>", re.S)

HARNESS = r"""
const THREE = require(THREE_PATH);
let stepNo = 0;
const FridayGenome = { current: () => ({ step: stepNo ? { step: stepNo } : null }) };
const lens = []; const FridayField = { setLens: (p, r) => lens.push([p ? p.toArray() : null, r]) };
WORM
HOLE
const base = new THREE.Color(0x1e54c7), accent = new THREE.Color(0x5fa8ff), amber = new THREE.Color(0xf59e0b);
const S = o => Object.assign({ voice: 0, thinking: false, listening: false, base, accent, reduced: false }, o || {});
const run = (mod, st, secs) => { for (let f = 0; f < 60 * secs; f++) mod.animate(1 / 60, st); };
const draws = g => { let n = 0; g.traverse(o => { if (o.isMesh || o.isPoints || o.isLine) n++; }); return n; };
const out = { worm: {}, hole: {} };
// ── wormhole ──
{ const W = out.worm, g = new THREE.Group(); FridayWormhole.build(g, { rings: 18 });
  const P = FridayWormhole._parts(), geo = P.grid.geometry, pos = geo.attributes.position.array, aR = geo.attributes.aR.array, aS = geo.attributes.aSide.array;
  W.draws = draws(g);
  let worst = 0; for (let i = 0; i < aR.length; i++) { const z = pos[i * 3 + 2], want = aS[i] * FridayWormhole.flamm(aR[i]) * FridayWormhole.RS;
    worst = Math.max(worst, Math.abs(z - want)); const rr = Math.hypot(pos[i * 3], pos[i * 3 + 1]); worst = Math.max(worst, Math.abs(rr - aR[i] * FridayWormhole.RS)); }
  W.flammErr = worst; W.minR = Math.min(...aR);
  const ringR = new Set(); for (let i = 0; i < aR.length; i += 2) if (aS[i] > 0 && aR[i] === aR[i + 1]) ringR.add(aR[i].toFixed(5));
  W.nearRings = ringR.size;
  // the far side: nothing without the field's word
  run(FridayWormhole, S({ thinking: true, voice: 0.8, listening: true }), 3);
  W.noField = { local: FridayWormhole._local(), web: FridayWormhole._web() };
  run(FridayWormhole, S({ field: { glow: 1, web: null } }), 3); W.localWork = FridayWormhole._local();
  run(FridayWormhole, S({ field: { glow: 0, web: { phase: 'hold', front: 1, intensity: 1 } } }), 3); W.cloud = FridayWormhole._web();
  run(FridayWormhole, S({ field: { glow: 0, web: null } }), 4); W.after = { local: FridayWormhole._local(), web: FridayWormhole._web() };
  // states
  const gu = P.grid.material.uniforms, su = P.stream.material.uniforms;
  run(FridayWormhole, S({ voice: 0.8 }), 2); W.ripple = gu.uRipple.value;
  run(FridayWormhole, S({ thinking: true }), 3); W.tight = gu.uTight.value; W.stream = su.uOn.value;
  run(FridayWormhole, S({ listening: true }), 3); W.widen = gu.uWiden.value;
  const ph = gu.uPhase.value; run(FridayWormhole, S({ reduced: true, voice: 0.9 }), 2); W.reduced = { phase: gu.uPhase.value - ph, ripple: gu.uRipple.value };
  // the weeks: steadier, eased
  stepNo = 0; FridayWormhole.build(new THREE.Group(), {}); W.stable0 = FridayWormhole._stable();
  stepNo = 8; let prev = FridayWormhole._stable(), jump = 0; for (let f = 0; f < 60 * 30; f++) { FridayWormhole.animate(1 / 60, S()); const s = FridayWormhole._stable(); jump = Math.max(jump, s - prev); prev = s; }
  W.stable8 = prev; W.stableJump = jump; stepNo = 0;
  lens.length = 0; FridayWormhole.animate(1 / 60, S()); W.lens = lens.slice(-1)[0];
  const pts = FridayWormhole.points(), L = FridayWormhole.layers(), all = L.flat().sort((a, b) => a - b);
  W.view = { units: FridayWormhole.units(), pts: pts.length, finite: pts.every(p => p.every(Number.isFinite)), cover: all.length === 24 && all.every((k, j) => k === j) };
}
// ── black hole ──
{ const H = out.hole, g = new THREE.Group(); const cam = new THREE.PerspectiveCamera(60, 1.6, 0.1, 1000); cam.position.set(0, 2.3, 21); cam.lookAt(0, 0, 0); cam.updateMatrixWorld();
  g.add(cam); stepNo = 0; FridayBlackHole.build(g, { dust: 300 });
  H.draws = draws(g); H.fs = FridayBlackHole._mat().fragmentShader; H.rs0 = FridayBlackHole._rs(); H.heat0 = FridayBlackHole._heat();
  const toward = [0, 2.3 / 21, 1];
  const SP = () => FridayBlackHole._sparks().geometry.attributes.aA.array;
  // pairs: rates by state, soft pop-in
  // The sparks' light as a whole: a pair's light moving from its partner to
  // the mote it becomes is not a flash; a pair popping in at once would be.
  const rate = (st, secs) => { FridayBlackHole.build(new THREE.Group(), { dust: 300 }); let maxRise = 0, prev = null;
    for (let f = 0; f < 60 * secs; f++) { FridayBlackHole.animate(1 / 60, Object.assign({ toward, camera: cam }, st));
      const total = Array.from(SP()).reduce((s, v) => s + v, 0);
      if (prev !== null) maxRise = Math.max(maxRise, total - prev); prev = total; }
    return { live: FridayBlackHole._pairs(), motes: FridayBlackHole._motes(), maxRise }; };
  H.idle = rate(S(), 10); H.think = rate(S({ thinking: true }), 10); H.speak = rate(S({ voice: 0.9 }), 10); H.reduced = rate(S({ reduced: true, voice: 0.9, thinking: true }), 10);
  // listening: the dust spirals in
  FridayBlackHole.build(new THREE.Group(), { dust: 300 });
  const meanR = () => FridayBlackHole._grains().reduce((s, gr) => s + gr.r, 0) / FridayBlackHole._grains().length;
  run(FridayBlackHole, Object.assign({ toward }, S()), 2); H.rRest = meanR();
  run(FridayBlackHole, Object.assign({ toward }, S({ listening: true })), 4); H.rListen = meanR();
  H.dustN = FridayBlackHole._grains().length;
  // the weeks
  stepNo = 10; FridayBlackHole.build(new THREE.Group(), {}); H.heat10 = FridayBlackHole._heat(); H.rs10 = FridayBlackHole._rs(); stepNo = 0;
  // lens for the world and for its own dust
  FridayBlackHole.build(g, { dust: 300 }); lens.length = 0;
  FridayBlackHole.animate(1 / 60, Object.assign({ toward, camera: cam }, S())); H.lens = lens.slice(-1)[0];
  H.dustLens = FridayBlackHole._sparks().material.uniforms.uLensR.value;
  const pts = FridayBlackHole.points(), L = FridayBlackHole.layers(), all = L.flat().sort((a, b) => a - b);
  H.view = { units: FridayBlackHole.units(), pts: pts.length, finite: pts.every(p => p.every(Number.isFinite)), cover: all.length === 24 && all.every((k, j) => k === j) };
}
console.log(JSON.stringify(out));
"""


def _run(path):
    t = path.read_text(encoding="utf-8")
    w, h = WORM.search(t), HOLE.search(t)
    assert w and h, f"{path.name}: missing a block"
    src = (HARNESS.replace("WORM", w.group(1)).replace("HOLE", h.group(1))
           .replace("THREE_PATH", json.dumps(str(THREE))))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def run(request):
    if not node:
        pytest.skip("node is not installed")
    return _run(request.param)


def test_both_scene_files_carry_the_same_modules():
    for rx in (WORM, HOLE):
        a, b = (rx.search(p.read_text(encoding="utf-8")).group(1) for p in SCENES)
        assert a == b


# ── the wormhole ──

def test_the_grid_lies_on_flamms_paraboloid(run):
    w = run["worm"]
    assert w["draws"] == 4
    assert w["flammErr"] < 1e-4 and w["minR"] == pytest.approx(1.0)
    assert w["nearRings"] == 18, "the rings gene sets the near funnel's rings"


def test_the_far_side_shows_only_what_the_field_says(run):
    w = run["worm"]
    assert w["noField"]["local"] < 1e-6 and w["noField"]["web"] < 1e-6, "no field state, nothing on the far side"
    assert w["localWork"] > 0.95
    assert w["cloud"] > 0.95
    assert w["after"]["local"] < 0.05 and w["after"]["web"] < 0.05


def test_speaking_thinking_and_listening_move_the_throat(run):
    w = run["worm"]
    assert w["ripple"] > 0.5
    assert w["tight"] > 0.9 and w["stream"] > 0.9
    assert w["widen"] > 0.9
    assert w["reduced"]["phase"] == 0 and w["reduced"]["ripple"] < 0.05


def test_the_throat_steadies_over_the_weeks_without_a_jump(run):
    w = run["worm"]
    assert w["stable0"] == 0
    assert w["stable8"] > 0.99
    assert w["stableJump"] < 0.01


def test_the_mouth_bends_the_worlds_dots(run):
    w = run["worm"]
    assert w["lens"][1] == pytest.approx(1.1, rel=1e-6)


def test_the_wormhole_gestures_by_patch(run):
    v = run["worm"]["view"]
    assert v["units"] == v["pts"] == 24 and v["finite"] and v["cover"]


# ── the black hole ──

def test_the_shader_traces_schwarzschild_light(run):
    fs = run["hole"]["fs"]
    assert "-1.5 * h2 * x / pow(r, 5.0)" in fs, "the null geodesic"
    assert "if (r < 1.0) { captured = true; break; }" in fs, "the horizon at r = 1"
    assert "sqrt(h2) - 2.598" in fs, "the photon ring at b = 3 sqrt(3) / 2"
    assert "rr > 3.0 && rr < 10.0" in fs, "the disk from the innermost stable orbit"
    assert "float g = sqrt(1.0 - 1.0 / rr);" in fs, "gravitational redshift"
    assert "1.0 / (gam * (1.0 - beta * dot(vel, -normalize(vh))))" in fs, "Doppler"
    assert "1.0 - exp(" in fs, "a soft shoulder, never glare"
    assert run["hole"]["draws"] == 3


def _palette(fs):
    m = re.search(r"vec3 ember = vec3\(([^)]*)\), rose = vec3\(([^)]*)\), white = vec3\(([^)]*)\), blue = vec3\(([^)]*)\);", fs)
    assert m, "the emission palette"
    return [tuple(float(x) for x in g.split(",")) if "," in g else (float(g),) * 3 for g in m.groups()]


def test_its_light_is_never_amber_and_its_only_red_is_dark(run):
    for rgb in _palette(run["hole"]["fs"]):
        h, l, s = colorsys.rgb_to_hls(*rgb)
        deg = h * 360
        if s > 0.15:
            assert not (25 <= deg <= 55), "amber means 'needs you': %r" % (rgb,)
            if deg < 15 or deg > 345:
                assert l < 0.35, "a red as light as the alert red: %r" % (rgb,)


def test_pairs_pop_in_softly_and_come_with_her_state(run):
    h = run["hole"]
    for k in ("idle", "think", "speak"):
        # each new pair takes a quarter second to appear; at most a few start in one frame
        assert h[k]["maxRise"] <= 3 * 2 / (0.25 * 60) + 1e-6, "the sparks brightened faster than a quarter second"
    assert 0 < h["idle"]["live"] + h["idle"]["motes"] < h["think"]["live"] + h["think"]["motes"]
    assert h["speak"]["motes"] > h["idle"]["motes"]
    assert h["reduced"]["live"] == 0 and h["reduced"]["motes"] == 0


def test_listening_spirals_the_dust_in(run):
    h = run["hole"]
    assert h["dustN"] == 300
    assert h["rListen"] < h["rRest"] - 2


def test_it_radiates_hotter_and_smaller_over_the_weeks(run):
    h = run["hole"]
    assert h["heat10"] > h["heat0"] + 1.5
    assert h["rs10"] < h["rs0"]


def test_the_worlds_dots_and_its_own_dust_bend_round_the_shadow(run):
    h = run["hole"]
    assert h["lens"][1] == pytest.approx(2.6 * h["rs0"], rel=1e-6)
    assert h["dustLens"] > 0


def test_the_black_hole_gestures_by_patch_of_dust(run):
    v = run["hole"]["view"]
    assert v["units"] == v["pts"] == 24 and v["finite"] and v["cover"]
