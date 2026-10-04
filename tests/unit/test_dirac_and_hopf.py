"""Dirac Probability (QUANTUM) and Transcendence (NONE), avatar-visual-genome.md
§19, run under node with the vendored three.js, lifted verbatim from both scene
files.

Dirac (`// <dirac-cloud>`, FridayDirac): an electron's probability cloud.
- One draw. Normal blending and capped colours, so it cannot pile up into the
  glare the old additive sphere had.
- The orbitals are real: a p orbital is dark in its nodal plane and bright
  along its axis, d_z2 is dark on its nodal cones, d_xy on its two planes.
- Thinking walks the orbitals one into the next; at rest it settles back.
- Speaking runs interference ripples outward, slowly (well under three a
  second at any point); listening collapses the cloud and sharpens it.
- Reduced motion: no ripples, no walk.
- The gesture engine's view: 24 clusters; a gesture lasts one frame.

Transcendence (`// <hopf-fibration>`, FridayHopf): the Hopf fibration.
- One draw, two fibres a line gene, normal blending (no pile-up glare).
- Every fibre projects to a true circle, and two fibres link exactly once
  (the Gauss linking number), however far it has turned through the fourth
  dimension.
- No fibre reaches the projection's pole (nothing runs off to infinity).
- Thinking turns it faster and wider; listening pulls it taut (no turn);
  speaking makes light flow along the fibres, slowly; reduced motion stills it.
- The gesture engine's view: 25 sectors in five tori.
"""
import json
import math
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
THREE = ROOT / "static" / "vendor" / "three-r128.min.js"
node = shutil.which("node")
DIRAC = re.compile(r"// <dirac-cloud>\n(.*?)// </dirac-cloud>", re.S)
HOPF = re.compile(r"// <hopf-fibration>\n(.*?)// </hopf-fibration>", re.S)

HARNESS = r"""
const THREE = require(THREE_PATH);
DIRAC
HOPF
const base = new THREE.Color(0x1e54c7), accent = new THREE.Color(0x5fa8ff), amber = new THREE.Color(0xf59e0b);
let seed = 11; const rand = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
const S = o => Object.assign({ voice: 0, thinking: false, listening: false, base, accent, reduced: false, wave: 10 }, o || {});
const draws = g => { let n = 0; g.traverse(o => { if (o.isMesh || o.isPoints || o.isLine) n++; }); return n; };
const out = { dirac: {}, hopf: {} };
// ── Dirac ──
{ const D = out.dirac, g = new THREE.Group(); FridayDirac.build(g, { count: 6000, rand });
  const pts = g.children[0], mat = FridayDirac._mat(), u = mat.uniforms;
  D.draws = draws(g); D.n = FridayDirac._n(); D.normalBlend = mat.blending === THREE.NormalBlending; D.depthWrite = mat.depthWrite;
  D.capColour = /min\(vC, vec3\(0\.8\)\)/.test(mat.fragmentShader); D.capAlpha = /clamp\([^;]*0\.85\)/.test(mat.vertexShader);
  const dir = pts.geometry.attributes.aDir.array, W = pts.geometry.attributes.aW.array, R = pts.geometry.attributes.aR.array;
  // Brightness-weighted share of points in a region, for one orbital.
  const share = (o, inRegion) => { let a = 0, t = 0; for (let i = 0; i < D.n; i++) { const w = W[i * 4 + o]; t += w;
      if (inRegion(dir[i * 3], dir[i * 3 + 1], dir[i * 3 + 2])) a += w; } return a / t; };
  const area = inRegion => { let a = 0; for (let i = 0; i < 20000; i++) { const z = 2 * rand() - 1, p = 2 * Math.PI * rand(), s = Math.sqrt(1 - z * z);
      if (inRegion(s * Math.cos(p), z, s * Math.sin(p))) a++; } return a / 20000; };
  const pPlane = (x, y, z) => Math.abs(y) < 0.08, pAxis = (x, y, z) => Math.abs(y) > 0.85;
  const dCone = (x, y, z) => Math.abs(3 * y * y - 1) < 0.12, dxyPlanes = (x, y, z) => Math.abs(x) < 0.06 || Math.abs(z) < 0.06;
  D.p = { plane: share(1, pPlane) / area(pPlane), axis: share(1, pAxis) / area(pAxis) };
  D.dz2 = { cones: share(2, dCone) / area(dCone) }; D.dxy = { planes: share(3, dxyPlanes) / area(dxyPlanes) };
  D.s = { plane: share(0, pPlane) / area(pPlane) };
  let maxR = 0; for (let i = 0; i < D.n * 4; i++) maxR = Math.max(maxR, R[i]); D.maxR = maxR;
  // rest, then thinking walks the orbitals
  for (let f = 0; f < 300; f++) FridayDirac.animate(1 / 60, S());
  D.rest = FridayDirac._mix();
  const seen = new Set(); for (let f = 0; f < 60 * 20; f++) { FridayDirac.animate(1 / 60, S({ thinking: true }));
    const m = FridayDirac._mix(); const k = m.indexOf(Math.max(...m)); if (m[k] > 0.85) seen.add(k); }
  D.walked = seen.size;
  for (let f = 0; f < 60 * 6; f++) FridayDirac.animate(1 / 60, S());
  D.settled = FridayDirac._mix();
  // speaking: ripples, slowly
  const ph0 = u.uPhase.value; for (let f = 0; f < 120; f++) FridayDirac.animate(1 / 60, S({ voice: 0.7 }));
  D.ripple = u.uRipple.value; D.phaseRate = (u.uPhase.value - ph0) / 2;
  // listening
  for (let f = 0; f < 240; f++) FridayDirac.animate(1 / 60, S({ listening: true })); D.collapse = u.uCollapse.value;
  // reduced
  for (let f = 0; f < 240; f++) FridayDirac.animate(1 / 60, S({ reduced: true, voice: 0.9, thinking: true }));
  const ph1 = u.uPhase.value, mx1 = FridayDirac._mix(); for (let f = 0; f < 120; f++) FridayDirac.animate(1 / 60, S({ reduced: true, voice: 0.9, thinking: true }));
  D.reduced = { ripple: u.uRipple.value, phaseMoved: u.uPhase.value - ph1, mixMoved: Math.max(...FridayDirac._mix().map((v, i) => Math.abs(v - mx1[i]))) };
  // the gesture engine's view
  const P = FridayDirac.points(), L = FridayDirac.layers(), B = FridayDirac.blocks(), all = L.flat().sort((a, b) => a - b);
  D.view = { units: FridayDirac.units(), pts: P.length, finite: P.every(p => p.every(Number.isFinite)),
    layersCover: all.length === 24 && all.every((k, j) => k === j), blocksOk: B.every(b => b.units.length >= 1 && b.units.length <= 4) };
  FridayDirac.applyUnit(3, { d: [0, 1, 0], r: [0, 0, 0], s: 1.3, b: 1, g: 0.8, hue: 'approval' }, accent, amber);
  D.applied = [u.uD.value[3].y, u.uD.value[3].w, u.uG.value[3].x > 0];
  FridayDirac.animate(1 / 60, S()); D.cleared = [u.uD.value[3].y, u.uD.value[3].w, u.uG.value[3].w];
}
// ── Hopf ──
{ const H = out.hopf, g = new THREE.Group(); FridayHopf.build(g, { lines: 100 });
  const mat = FridayHopf._mat(), u = mat.uniforms;
  H.draws = draws(g); H.fibres = FridayHopf._fibres(); H.isSegments = g.children[0].children[0].isLineSegments; H.normalBlend = mat.blending === THREE.NormalBlending;
  // Circles and links, at rest and turned as far as thinking turns it.
  const fibre = (k, j, a, n) => { const out = [], q = [0, 0, 0, 0], per = Math.round(H.fibres / FridayHopf.TORI);
    const eta = 0.2 + (0.60 - 0.2) * k / (FridayHopf.TORI - 1), delta = 2 * Math.PI * j / per + k * 0.37;
    for (let i = 0; i < n; i++) { FridayHopf._at(eta, delta, 2 * Math.PI * i / n, a, q); out.push([q[0], q[1], q[2], q[3]]); } return out; };
  const circleErr = c4 => { // coplanar, and every point the same distance from the circle's centre
    const c = c4.map(p => p.slice(0, 3));
    const m = [0, 1, 2].map(a => c.reduce((s, p) => s + p[a], 0) / c.length);
    const a = c[0].map((v, i) => v - m[i]), b = c[Math.floor(c.length / 4)].map((v, i) => v - m[i]);
    const n = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]], nl = Math.hypot(...n);
    let plane = 0; const ds = [];
    for (const p of c) { const d = [p[0] - m[0], p[1] - m[1], p[2] - m[2]]; plane = Math.max(plane, Math.abs((d[0] * n[0] + d[1] * n[1] + d[2] * n[2]) / nl)); }
    // the centre: the circumcentre of three well-spread points
    const P = [c[0], c[Math.floor(c.length / 3)], c[Math.floor(2 * c.length / 3)]];
    const A = P[1].map((v, i) => v - P[0][i]), B = P[2].map((v, i) => v - P[0][i]);
    const AxB = [A[1] * B[2] - A[2] * B[1], A[2] * B[0] - A[0] * B[2], A[0] * B[1] - A[1] * B[0]];
    const a2 = A.reduce((s, v) => s + v * v, 0), b2 = B.reduce((s, v) => s + v * v, 0), d2 = AxB.reduce((s, v) => s + v * v, 0);
    const t1 = [a2 * B[0] - b2 * A[0], a2 * B[1] - b2 * A[1], a2 * B[2] - b2 * A[2]];
    const cc = [ (t1[1] * AxB[2] - t1[2] * AxB[1]) / (2 * d2) + P[0][0], (t1[2] * AxB[0] - t1[0] * AxB[2]) / (2 * d2) + P[0][1], (t1[0] * AxB[1] - t1[1] * AxB[0]) / (2 * d2) + P[0][2] ];
    const rs = c.map(p => Math.hypot(p[0] - cc[0], p[1] - cc[1], p[2] - cc[2]));
    return { plane, spread: (Math.max(...rs) - Math.min(...rs)) / Math.max(...rs) }; };
  const link = (c1, c2) => { // Gauss linking integral, discretised
    let s = 0; const n1 = c1.length, n2 = c2.length;
    for (let i = 0; i < n1; i++) { const a = c1[i], a2 = c1[(i + 1) % n1], da = [a2[0] - a[0], a2[1] - a[1], a2[2] - a[2]], ma = [(a[0] + a2[0]) / 2, (a[1] + a2[1]) / 2, (a[2] + a2[2]) / 2];
      for (let j = 0; j < n2; j++) { const b = c2[j], b2 = c2[(j + 1) % n2], db = [b2[0] - b[0], b2[1] - b[1], b2[2] - b[2]], mb = [(b[0] + b2[0]) / 2, (b[1] + b2[1]) / 2, (b[2] + b2[2]) / 2];
        const r = [ma[0] - mb[0], ma[1] - mb[1], ma[2] - mb[2]], rl = Math.hypot(...r);
        const cr = [da[1] * db[2] - da[2] * db[1], da[2] * db[0] - da[0] * db[2], da[0] * db[1] - da[1] * db[0]];
        s += (r[0] * cr[0] + r[1] * cr[1] + r[2] * cr[2]) / (rl * rl * rl); } }
    return s / (4 * Math.PI); };
  H.circles = []; H.links = []; H.maxW = 0;
  for (const a of [0, FridayHopf.A_THINK, -FridayHopf.A_THINK]) {
    for (const [k, j] of [[0, 0], [2, 7], [4, 13]]) H.circles.push(circleErr(fibre(k, j, a, 240)));
    H.links.push(link(fibre(0, 0, a, 300), fibre(4, 11, a, 300)), link(fibre(2, 3, a, 300), fibre(2, 20, a, 300)));
    for (let k = 0; k < 5; k++) for (let j = 0; j < 50; j += 3) for (const p of fibre(k, j, a, 90)) H.maxW = Math.max(H.maxW, p[3]);
  }
  // states
  const sw = (st, secs) => { let mn = 1e9, mx = -1e9; for (let f = 0; f < 60 * secs; f++) { FridayHopf.animate(1 / 60, st); mn = Math.min(mn, u.uA.value); mx = Math.max(mx, u.uA.value); } return mx - mn; };
  H.swingRest = sw(S(), 30); H.swingThink = sw(S({ thinking: true }), 30); H.swingTaut = (sw(S({ listening: true }), 4), sw(S({ listening: true }), 10));
  H.taut = u.uTaut.value;
  const f0 = u.uFlow.value; for (let f = 0; f < 120; f++) FridayHopf.animate(1 / 60, S({ voice: 0.9 }));
  H.flowAmp = u.uFlowAmp.value; H.flowRate = (u.uFlow.value - f0) / 2;
  const a1 = u.uA.value, fl1 = u.uFlow.value; for (let f = 0; f < 120; f++) FridayHopf.animate(1 / 60, S({ reduced: true, voice: 0.9, thinking: true }));
  H.reduced = { turn: Math.abs(u.uA.value - a1) < 1e-9 || true, flowMoved: u.uFlow.value - fl1, flowAmp: u.uFlowAmp.value };
  const a2 = u.uA.value; for (let f = 0; f < 60; f++) FridayHopf.animate(1 / 60, S({ reduced: true })); H.reduced.turn = Math.abs(u.uA.value - a2);
  const P = FridayHopf.points(), L = FridayHopf.layers(), all = L.flat().sort((a, b) => a - b);
  H.view = { units: FridayHopf.units(), pts: P.length, finite: P.every(p => p.every(Number.isFinite)), layers: L.length,
    layersCover: all.length === 25 && all.every((k, j) => k === j), blocksOk: FridayHopf.blocks().every(b => b.units.length >= 1 && b.units.length <= 4) };
}
console.log(JSON.stringify(out));
"""


def _run(path):
    t = path.read_text(encoding="utf-8")
    d, h = DIRAC.search(t), HOPF.search(t)
    assert d and h, f"{path.name}: missing a block"
    src = (HARNESS.replace("DIRAC", d.group(1)).replace("HOPF", h.group(1))
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
    for rx in (DIRAC, HOPF):
        a, b = (rx.search(p.read_text(encoding="utf-8")).group(1) for p in SCENES)
        assert a == b


# ── Dirac ──

def test_the_cloud_is_one_draw_and_cannot_pile_up_into_glare(run):
    d = run["dirac"]
    assert d["draws"] == 1 and d["n"] == 6000
    assert d["normalBlend"] and not d["depthWrite"]
    assert d["capColour"] and d["capAlpha"]
    assert d["maxR"] < 9


def test_the_orbitals_have_their_nodes_and_lobes(run):
    d = run["dirac"]
    # density relative to an even spread over the same directions
    assert d["p"]["plane"] < 0.05, "a p orbital is dark in its nodal plane"
    assert d["p"]["axis"] > 2.0, "and bright along its axis"
    assert d["dz2"]["cones"] < 0.05, "d_z2 is dark on its nodal cones"
    assert d["dxy"]["planes"] < 0.05, "d_xy is dark on its nodal planes"
    assert 0.7 < d["s"]["plane"] < 1.3, "s is round"


def test_thinking_walks_the_orbitals_and_rest_settles_back(run):
    d = run["dirac"]
    assert d["walked"] == 4
    assert max(abs(a - b) for a, b in zip(d["settled"], d["rest"])) < 0.05


def test_speaking_ripples_slowly_and_listening_collapses(run):
    d = run["dirac"]
    assert d["ripple"] > 0.4
    assert 0 < d["phaseRate"] <= 2 * math.pi, "a ring passes any point less than once a second"
    assert d["collapse"] > 0.9


def test_reduced_motion_stills_the_cloud(run):
    r = run["dirac"]["reduced"]
    assert r["ripple"] < 0.02 and r["phaseMoved"] == 0 and r["mixMoved"] < 1e-3


def test_the_cloud_gestures_by_cluster_for_one_frame(run):
    d = run["dirac"]
    v = d["view"]
    assert v["units"] == v["pts"] == 24 and v["finite"] and v["layersCover"] and v["blocksOk"]
    assert d["applied"] == [1, 1.3, True]
    assert d["cleared"] == [0, 1, 1]


# ── Hopf ──

def test_the_fibration_is_one_draw_two_fibres_a_line_and_cannot_glare(run):
    h = run["hopf"]
    assert h["draws"] == 1 and h["isSegments"] and h["normalBlend"]
    assert h["fibres"] == 200


def test_every_fibre_is_a_circle_and_any_two_link_once(run):
    h = run["hopf"]
    for c in h["circles"]:
        assert c["plane"] < 1e-6 and c["spread"] < 1e-6, c
    for lk in h["links"]:
        assert abs(abs(lk) - 1) < 0.02, h["links"]


def test_nothing_runs_off_to_infinity(run):
    assert run["hopf"]["maxW"] <= 0.75


def test_thinking_turns_it_wider_listening_pulls_it_taut_speaking_flows(run):
    h = run["hopf"]
    assert h["swingThink"] > 1.4 * h["swingRest"] > 0
    assert h["swingTaut"] < 0.01 and h["taut"] > 0.95
    assert h["flowAmp"] > 0.5
    assert 0 < h["flowRate"] <= 2 * math.pi * 0.8, "light along a fibre passes a point under once a second"


def test_reduced_motion_stills_the_fibration(run):
    r = run["hopf"]["reduced"]
    assert r["turn"] < 1e-9 and r["flowMoved"] == 0 and r["flowAmp"] < 0.05


def test_the_fibration_gestures_by_sector(run):
    v = run["hopf"]["view"]
    assert v["units"] == v["pts"] == 25 and v["finite"] and v["layers"] == 5 and v["layersCover"] and v["blocksOk"]
