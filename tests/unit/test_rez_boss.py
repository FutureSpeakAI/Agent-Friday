"""Giga Earth's boss on the page (avatar-visual-genome.md §15), run under node
with the vendored three.js from both scene files: every form of the set track
builds and draws finite geometry, a tile blasted at one form stays blasted at
every later one, the pattern is the same for one install and different for
another, the robot is only seen from the Unveiled form, the rings only from
the Rings form, a 2x2 block never straddles two counter-rotating sections,
and nothing turns under reduced motion.
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
BLOCK = re.compile(r"// <rez-boss>\n(.*?)// </rez-boss>", re.S)

HARNESS = r"""
const THREE = require(THREE_PATH);
const glowTexture = null;
let seed = 1;
const FridayGenome = { rand: () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; } };
BLOCK
const base = new THREE.Color(0x1e54c7), accent = new THREE.Color(0x5fa8ff);
const out = { forms: [] };
const goneAt = s => { seed = SEED; const g = new THREE.Group(); FridayRez.build(g, s);
  FridayRez.animate(1 / 60, 0.02, 0, base, accent, false);
  const m = new THREE.Matrix4(), hidden = [];
  const tiles = g.children[0].children.find(c => c.isInstancedMesh);
  for (let i = 0; i < FridayRez.N; i++) { tiles.getMatrixAt(i, m); if (Math.abs(m.determinant()) < 1e-9) hidden.push(i); }
  return { g, tiles, hidden }; };
for (let s = 0; s < FridayRez.FORMS.length; s++) {
  const { g, tiles, hidden } = goneAt(s);
  const root = g.children[0];
  const robot = root.children.find(c => c.isGroup && c.children.some(k => k.isMesh && k.geometry.type === 'IcosahedronGeometry'));
  const rings = root.children.filter(c => c.isGroup && c.children.some(k => k.geometry && k.geometry.type === 'TorusGeometry'));
  const finite = Array.from(tiles.instanceMatrix.array).every(Number.isFinite)
    && Array.from(tiles.instanceColor.array).every(Number.isFinite);
  const pts = FridayRez.points();
  // a block never straddles two layers (two sections, or a section and an arm)
  const blocksOk = FridayRez.blocks().every(b => {
    const secs = new Set(b.units.map(k => FridayRez.layers().findIndex(l => l.includes(k))));
    return secs.size === 1 && b.units.length >= 2;
  });
  const each = FridayRez.layers().flat().sort((a, b) => a - b);
  const layersOk = each.length === FridayRez.units() && each.every((k, j) => k === j);
  out.forms.push({ name: FridayRez.FORMS[s].name, hidden: hidden.length, units: FridayRez.units(),
    points: pts.length, finite, blocksOk, layersOk, blocks: FridayRez.blocks().length, layers: FridayRez.layers().length,
    robotSeen: robot.children.some(c => c.isMesh && c.visible) && robot.scale.x > 0.01,
    ringsSeen: rings.length === 2 && rings.every(r => r.visible) });
}
// monotone: every tile gone at form s is gone at s+1 (hidden, or in the arms)
const ballAt = s => { seed = SEED; const g = new THREE.Group(); FridayRez.build(g, s);
  FridayRez.animate(1 / 60, 0, 0, base, accent, true);
  const tiles = g.children[0].children.find(c => c.isInstancedMesh), m = new THREE.Matrix4(), p = new THREE.Vector3(), on = [];
  for (let i = 0; i < FridayRez.N; i++) { tiles.getMatrixAt(i, m); p.setFromMatrixPosition(m);
    if (Math.abs(p.length() - 7) < 1e-3 && Math.abs(m.determinant()) > 1e-9) on.push(i); }
  return on; };
out.onBall = []; for (let s = 0; s < FridayRez.FORMS.length; s++) out.onBall.push(ballAt(s));
out.monotone = out.onBall.every((on, s) => s === 0 || on.every(i => out.onBall[s - 1].includes(i)));
out.pattern = out.onBall[3].join(',');
// reduced motion: nothing turns
seed = SEED; { const g = new THREE.Group(); FridayRez.build(g, 5);
  const tiles = g.children[0].children.find(c => c.isInstancedMesh);
  FridayRez.animate(1 / 60, 0.5, 0, base, accent, true); const a = Array.from(tiles.instanceMatrix.array);
  for (let i = 0; i < 60; i++) FridayRez.animate(1 / 60, 0.5, 0, base, accent, true);
  out.reducedStill = Array.from(tiles.instanceMatrix.array).every((v, i) => Math.abs(v - a[i]) < 1e-9);
  for (let i = 0; i < 60; i++) FridayRez.animate(1 / 60, 0.5, 0, base, accent, false);
  out.movesOtherwise = Array.from(tiles.instanceMatrix.array).some((v, i) => Math.abs(v - a[i]) > 1e-4); }
console.log(JSON.stringify(out));
"""


def _run(path, seed):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <rez-boss> block"
    src = (HARNESS.replace("BLOCK", m.group(1)).replace("THREE_PATH", json.dumps(str(THREE)))
           .replace("SEED", str(seed)))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_every_form_builds_and_the_track_only_takes_tiles_away(path):
    o = _run(path, 7)
    names = [f["name"] for f in o["forms"]]
    assert names == ["Sealed", "Cracked", "Lock-on", "Unveiled", "Arms", "Rings", "Final form"]
    for f in o["forms"]:
        assert f["finite"] and f["blocksOk"] and f["layersOk"] and f["blocks"] > 0
        assert f["points"] == f["units"] == 200 - f["hidden"]
    # the ball's five sections; from the Arms form, each arm is a layer too
    assert [f["layers"] for f in o["forms"][:4]] == [5, 5, 5, 5]
    assert all(f["layers"] > 5 for f in o["forms"][4:])
    hidden = [f["hidden"] for f in o["forms"]]
    assert hidden[0] == 0 and hidden[1] > 0 and hidden[1] < hidden[2] < hidden[3]
    assert hidden[4:] == [0, 0, 0]                    # from the Arms form, blasted tiles swirl
    on = [len(x) for x in o["onBall"]]
    assert on == sorted(on, reverse=True) and on[0] == 200 and on[-1] <= 25
    assert o["monotone"] is True
    assert [f["robotSeen"] for f in o["forms"]] == [False, False, False, True, True, True, True]
    assert [f["ringsSeen"] for f in o["forms"]] == [False] * 5 + [True, True]
    assert o["reducedStill"] is True and o["movesOtherwise"] is True


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_blast_pattern_is_per_install():
    a, a2, b = _run(SCENES[0], 7), _run(SCENES[0], 7), _run(SCENES[0], 99)
    assert a["pattern"] == a2["pattern"] and a["pattern"] != b["pattern"]
