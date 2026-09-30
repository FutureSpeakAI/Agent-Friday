"""The lattice's processing-state gestures, run under node from both UI files
(avatar-visual-genome.md §13). The rule under test: every gesture comes from
a real presence frame, the same event always makes the same move, nothing
moves without an event, reduced motion is brightness only, and brightness
never jumps."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
node = shutil.which("node")
BLOCK = re.compile(r"// <presence-gestures>\n(.*?)// </presence-gestures>", re.S)

HARNESS = r"""
BLOCK
// A full 3x3x3 lattice, camera on +z, up +y.
const S = 1.6, units = [], at = {};
for (let x = 0; x < 3; x++) for (let y = 0; y < 3; y++) for (let z = 0; z < 3; z++) {
  at[[x, y, z]] = units.length; units.push([(x - 1) * S, (y - 1) * S, (z - 1) * S]);
}
const blocks = [];
for (let a = 0; a < 3; a++) for (const side of [0, 2]) {
  const o = [0, 1, 2].filter(k => k !== a);
  for (const s0 of [0, 1]) for (const s1 of [0, 1]) {
    const us = [], cs = [];
    for (const d0 of [0, 1]) for (const d1 of [0, 1]) {
      const g = [0, 0, 0]; g[a] = side; g[o[0]] = s0 + d0; g[o[1]] = s1 + d1;
      us.push(at[g]); cs.push(g.map(v => (v - 1) * S));
    }
    const axis = [0, 0, 0]; axis[a] = side === 2 ? 1 : -1;
    blocks.push({ units: us, axis, centre: [0, 1, 2].map(k => cs.reduce((s, c) => s + c[k], 0) / 4) });
  }
}
const layers = [[], [], []]; units.forEach((u, i) => layers[Math.round(u[0] / S) + 1].push(i));
const top = []; units.forEach((u, i) => { if (u[1] > 1) top.push(i); });
const an = { units, layers, blocks, top, core: [0, 0, 0], toward: [0, 0, 1], up: [0, 1, 0],
             across: [1, 0, 0], extent: S * 1.5, spacing: S };
const G = FridayGestures, dt = 1 / 60;
const run = (sec) => { let r; for (let i = 0; i < Math.round(sec / dt); i++) r = G.step(dt, an); return r; };
const moving = r => Object.keys(r.units).filter(k => {
  const u = r.units[k]; return u.d.some(v => Math.abs(v) > 1e-6) || u.r.some(v => Math.abs(v) > 1e-6) || Math.abs(u.s - 1) > 1e-6;
}).map(Number).sort((a, b) => a - b);
const P = (state, phase, extra) => Object.assign({ type: 'presence', state, phase }, extra || {});
const out = {};

// 1. nothing happens without an event
G.reset(); G.setReduced(REDUCED);
let quiet = true;
for (let i = 0; i < 600; i++) { const r = G.step(dt, an); if (Object.keys(r.units).length || r.thread || r.scan) quiet = false; }
out.quiet = quiet;

// 2. one twist per tool call, held until it returns
G.reset(); G.setReduced(REDUCED);
['c1', 'c2', 'c3'].forEach(ref => G.frame(P('tool', 'start', { ref })));
let r = run(0.6);
out.twist_units = moving(r).length;
out.twist_blocks = G._twists().filter(t => t.block !== null).length;
out.twist_rot = Object.values(r.units).filter(u => u.r.some(v => Math.abs(v - Math.PI / 2) < 1e-6 || Math.abs(v + Math.PI / 2) < 1e-6)).length;
out.twist_bright = Object.values(r.units).filter(u => u.b > 1.001).length;
out.twist_status = r.status;
['c1', 'c2', 'c3'].forEach(ref => G.frame(P('tool', 'end', { ref, ok: true })));
r = run(0.6);
out.twist_after = moving(r).length + G._twists().length;
// more than four at once queue, and start as slots free
G.reset(); G.setReduced(REDUCED);
['a', 'b', 'c', 'd', 'e'].forEach(ref => G.frame(P('tool', 'start', { ref })));
run(0.1);
out.queue_running = G._twists().length; out.queue_waiting = G._queue().length;
G.frame(P('tool', 'end', { ref: 'a' })); run(0.5);
out.queue_after = G._queue().length;
out.queue_blocks_disjoint = (() => { const seen = new Set(); for (const t of G._twists()) { if (t.block === null) continue;
  for (const u of an.blocks[t.block].units) { if (seen.has(u)) return false; seen.add(u); } } return true; })();

// 3. one layer per round
G.reset(); G.setReduced(REDUCED);
G.frame(P('round', 'step', { n: 2 })); r = run(0.7);
out.round2 = Object.keys(r.units).map(Number).sort((a, b) => a - b);
out.layer1 = layers[1].slice().sort((a, b) => a - b);
G.reset(); G.setReduced(REDUCED);
G.frame(P('round', 'step', { n: 4 })); r = run(0.7);
out.round4_in_layer0 = Object.keys(r.units).map(Number).every(i => layers[0].includes(i));
out.round_status = r.status;

// 4. local stays inside; only a sealed cloud send opens the vent
G.reset(); G.setReduced(REDUCED);
G.frame(P('route', 'step', { route: 'local' })); G.frame(P('round', 'step', { n: 1 }));
let maxThread = 0; for (let i = 0; i < 120; i++) maxThread = Math.max(maxThread, G.step(dt, an).thread);
out.local_thread = maxThread;
G.frame(P('egress', 'sent', { route: 'cloud' }));
maxThread = 0; let topMoved = false;
for (let i = 0; i < 120; i++) { const q = G.step(dt, an); maxThread = Math.max(maxThread, q.thread);
  if (top.some(t => q.units[t] && q.units[t].d[1] > 0.1)) topMoved = true; }
out.cloud_thread = maxThread; out.cloud_top_moved = topMoved;

// 5. approvals: one cube each, stepped forward, the only approval hue
G.reset(); G.setReduced(REDUCED);
G.setApprovals(2); r = run(1.0);
const hued = Object.keys(r.units).filter(k => r.units[k].hue === 'approval').map(Number);
out.approval_hued = hued.length;
out.approval_forward = hued.filter(k => r.units[k].d[2] > 0.5).length;
out.approval_facing = hued.every(k => units[k][2] > 1);
out.approval_status = r.status;
G.setApprovals(0); r = run(1.0);
out.approval_after = Object.values(r.units).filter(u => u.hue).length + moving(r).length;
G.reset(); G.setReduced(REDUCED);
['x', 'y'].forEach(ref => G.frame(P('tool', 'start', { ref }))); G.frame(P('round', 'step', { n: 1 }));
r = run(0.5);
out.hue_without_approval = Object.values(r.units).filter(u => u.hue).length;

// 6. verification sweeps once and settles what it passes
G.reset(); G.setReduced(REDUCED);
G.frame(P('verify', 'once', { ok: true }));
let sawScan = false, settled = 0;
for (let i = 0; i < 100; i++) { const q = G.step(dt, an); if (q.scan) sawScan = true;
  settled += Object.values(q.units).filter(u => u.s < 0.999 || u.b > 1.001).length; }
out.scan = sawScan; out.settled = settled > 0;
out.scan_after = run(0.5).scan;

// 7. listening ripples the facing side with the voice, and only then
G.reset(); G.setReduced(REDUCED);
G.setMic(0.8); let face = new Set();
for (let i = 0; i < 60; i++) { const q = G.step(dt, an); Object.keys(q.units).forEach(k => face.add(Number(k))); }
out.listen_units_face = [...face].every(k => units[k][2] > 1) && face.size > 0;
G.setMic(0); r = run(1.0);
out.listen_after = moving(r).length;

// 8. reduced motion: no unit ever moves, turns or scales
G.reset(); G.setReduced(REDUCED);
['p', 'q'].forEach(ref => G.frame(P('tool', 'start', { ref })));
G.frame(P('round', 'step', { n: 1 })); G.frame(P('egress', 'sent', { route: 'cloud' }));
G.frame(P('verify', 'once', { ok: false })); G.setApprovals(1); G.setMic(0.6);
let anyMove = false, anyLight = false, maxStep = 0, minB = 9, maxB = 0; const prev = {};
for (let i = 0; i < 180; i++) {
  const q = G.step(dt, an);
  if (moving(q).length) anyMove = true;
  for (const k of Object.keys(q.units)) { const b = q.units[k].b;
    if (b !== 1) anyLight = true; minB = Math.min(minB, b); maxB = Math.max(maxB, b);
    maxStep = Math.max(maxStep, Math.abs(b - (prev[k] === undefined ? 1 : prev[k]))); prev[k] = b; }
}
out.burst_moves = anyMove; out.burst_lights = anyLight;
out.burst_max_step = maxStep; out.burst_min_b = minB; out.burst_max_b = maxB;

// 9. the same events make the same moves
const trace = () => { G.reset(); G.setReduced(REDUCED); G.frame(P('tool', 'start', { ref: 'z' }));
  G.frame(P('round', 'step', { n: 3 })); return JSON.stringify(run(0.8)); };
out.deterministic = trace() === trace();
console.log(JSON.stringify(out));
"""


def _run(path, reduced):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <presence-gestures> block"
    src = HARNESS.replace("BLOCK", m.group(1)).replace("REDUCED", "true" if reduced else "false")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_each_gesture_comes_from_its_event_and_only_from_it(path):
    o = _run(path, reduced=False)
    assert o["quiet"] is True                              # no event, no motion
    # one twist per tool call, held, then home
    assert o["twist_blocks"] == 3 and o["twist_units"] >= 6
    assert o["twist_rot"] >= 6
    assert o["twist_status"].startswith("Using a tool")
    assert o["twist_after"] == 0
    assert o["queue_running"] == 4 and o["queue_waiting"] == 1
    assert o["queue_after"] == 0 and o["queue_blocks_disjoint"] is True
    # one layer per round, the same number the status line prints
    assert o["round2"] == o["layer1"]
    assert o["round4_in_layer0"] is True
    assert o["round_status"] == "Thinking, round 4"
    # local stays inside; the vent opens only on a sealed cloud send
    assert o["local_thread"] == 0
    assert o["cloud_thread"] > 0.9 and o["cloud_top_moved"] is True
    # approvals: facing cubes step forward in the approval hue, and only they
    assert o["approval_hued"] == 2 and o["approval_forward"] == 2
    assert o["approval_facing"] is True
    assert o["approval_status"] == "Waiting for your OK (2)"
    assert o["approval_after"] == 0
    assert o["hue_without_approval"] == 0
    # verification sweeps once and settles what it passes
    assert o["scan"] is True and o["settled"] is True and o["scan_after"] is None
    # listening moves only the facing side, only while there is a voice
    assert o["listen_units_face"] is True and o["listen_after"] == 0
    assert o["deterministic"] is True


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_reduced_motion_is_brightness_only_and_nothing_flashes(path):
    o = _run(path, reduced=True)
    assert o["quiet"] is True
    assert o["burst_moves"] is False and o["burst_lights"] is True
    assert o["twist_bright"] >= 6
    assert o["burst_max_step"] <= 0.08 + 1e-9
    assert 0.6 - 1e-9 <= o["burst_min_b"] and o["burst_max_b"] <= 1.5 + 1e-9
    assert o["approval_hued"] == 2 and o["approval_forward"] == 0


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_brightness_never_jumps_even_under_a_burst(path):
    o = _run(path, reduced=False)
    assert o["burst_max_step"] <= 0.08 + 1e-9
    assert 0.6 - 1e-9 <= o["burst_min_b"] and o["burst_max_b"] <= 1.5 + 1e-9


WIRING_SCENE = [
    "FridayGestureScene.begin();",
    "FridayGestureScene.end(delta);",
    "const mesh = new THREE.Mesh(boxGeo, boxMat.clone());",
    "mesh.add(new THREE.LineSegments(edgeGeo, edgeMat.clone()));",
    "speed: 0.5+Math.random()*0.5, ix: x, iy: y, iz: z };",
    "feed.onPresence(f => FridayGestures.frame(f));",
]


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_the_scene_is_wired_to_the_gestures(path):
    t = path.read_text(encoding="utf-8")
    for anchor in WIRING_SCENE:
        assert t.count(anchor) == 1, f"{path.name}: {anchor!r} x{t.count(anchor)}"


@pytest.mark.parametrize("path", [ROOT / "index.html", ROOT / "ui_parts" / "app.html"],
                         ids=lambda p: p.name)
def test_the_feed_hands_presence_frames_to_the_scene(path):
    t = path.read_text(encoding="utf-8")
    assert t.count("const presenceListeners = new Set();") == 1
    assert t.count("d.type === 'presence'") == 1
    assert t.count("onPresence(fn)") == 1
    # the mic meter publishes its level for the listening ripple
    assert t.count("window._fridayMicLevel = v.micPeakRecent || 0") + t.count(
        "window._fridayMicLevel=v.micPeakRecent||0") == 1
