"""The page side of Friday's look (avatar-visual-genome.md §3.4, §6.2, §6.4),
run under node from both scene files: the empty genome draws v1, only the
cool identity moods are recoloured (status moods never), the seeded dropout
is stable per install, and a look that draws more than 10% slower is undone.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
APPS = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]
node = shutil.which("node")
BLOCK = re.compile(r"// <avatar-genome>\n(.*?)// </avatar-genome>", re.S)

HARNESS = r"""
globalThis.window = globalThis;
globalThis.document = { hidden: false, addEventListener() {} };
globalThis.performance = { now: () => Date.now() };
const posts = [];
globalThis.fetch = (url, opt) => { if (opt && opt.method === 'POST') posts.push(url); return Promise.resolve({ ok: false }); };
globalThis.console.warn = () => {};
const MOODS = {
  IDLE: { baseColor: 0x1e54c7, accentColor: 0x5fa8ff, bloomStrength: 1.3, grain: 0.03 },
  CURIOUS: { baseColor: 0x00b3b3, accentColor: 0x00ffff, bloomStrength: 1.0, grain: 0.04 },
  SPEAKING: { baseColor: 0x00ff80, accentColor: 0x00d4ff, bloomStrength: 1.4, grain: 0.05 },
  EXECUTING: { baseColor: 0xffaa00, accentColor: 0xff3300, bloomStrength: 1.2, grain: 0.04 },
};
const structures = {}, scene = { remove() {}, add() {} };
const coreCubes = [], cathedralRings = [], astrolabeRings = [], shannonNodes = [];
let targetStructure = 'CUBES', currentStructure = 'CUBES', builds = 0;
function buildAllStructures() { builds++; }
function setGroupOpacity() {}
const FridayGestureScene = { resetCache() {} };
BLOCK
const G = FridayGenome, out = {};
out.v1_spacing = G.EX('CUBES', 'spacing', 1.6);
out.v1_rings = G.EX('ASTROLABE', 'rings', 8);
const view = (offsetColour, spacing, sigil, hash) => ({
  v1: false, step: { content_hash: hash },
  expression: { CUBES: { spacing, sparsity: 0.1 }, ASTROLABE: { rings: 7 } },
  palette: { bloom: 1.05, grain: 0.9, moods: { IDLE: { base: offsetColour, accent: '#a19aff' },
                                               CURIOUS: { base: '#0095b3', accent: '#00d4ff' } } },
  sigil });
G._receive(view('#3448e0', 1.8, { arms: 5, tilt: 3, accent_slot: 1, phase: 0.2 }, 'sha256:a'), false);
out.spacing = G.EX('CUBES', 'spacing', 1.6);
out.rings = G.EX('ASTROLABE', 'rings', 8);
out.idle = MOODS.IDLE.baseColor.toString(16);
out.speaking = MOODS.SPEAKING.baseColor.toString(16);
out.executing = MOODS.EXECUTING.baseColor.toString(16);
out.speaking_bloom = MOODS.SPEAKING.bloomStrength;
out.builds = builds;
// the seeded dropout: the same install draws the same cubes
const draw = () => { const r = []; for (let i = 0; i < 27; i++) r.push(G.rand('CUBES') < 0.15 ? 0 : 1); return r.join(''); };
G._rebuild(); const d1 = draw(); G._rebuild(); const d2 = draw();
out.same_install = d1 === d2;
G._receive(view('#3448e0', 1.8, { arms: 7, tilt: -9, accent_slot: 4, phase: 0.8 }, 'sha256:b'), false);
G._rebuild(); out.other_install = draw() !== d1;
// the frame budget: a look more than 10% slower is undone
for (let i = 0; i < 600; i++) G.sample(1 / 60, true);
G._receive(view('#4a3fe0', 1.9, { arms: 7, tilt: -9, accent_slot: 4, phase: 0.8 }, 'sha256:heavy'), true);
for (let i = 0; i < 600; i++) G.sample(1 / 45, true);      // 22 ms vs 16.7 ms
out.heavy_undone = posts.filter(u => /\/api\/avatar\/undo/.test(u)).length;
out.after_undo_hash = G.current().step.content_hash;
out.held_message = G.heldMessage();
// within budget: kept
posts.length = 0;
G._receive(view('#4a3fe0', 1.9, { arms: 7, tilt: -9, accent_slot: 4, phase: 0.8 }, 'sha256:light'), true);
for (let i = 0; i < 600; i++) G.sample(1 / 58, true);      // 17.2 ms, within 10%
out.light_kept = posts.length === 0 && G.current().step.content_hash === 'sha256:light';
// frames while gesturing or hidden are not counted
out.not_calm_ignored = (() => { const before = posts.length; G.sample(1, false); return posts.length === before; })();
console.log(JSON.stringify(out));
"""


def _run(path):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <avatar-genome> block"
    r = subprocess.run([node, "-"], input=HARNESS.replace("BLOCK", m.group(1)), capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_the_page_draws_the_genome_and_v1_without_one(path):
    o = _run(path)
    assert o["v1_spacing"] == 1.6 and o["v1_rings"] == 8          # no genome: v1 literals
    assert o["spacing"] == 1.8 and o["rings"] == 7
    assert o["idle"] == "3448e0"                                    # identity mood recoloured
    assert o["speaking"] == "ff80" and o["executing"] == "ffaa00"   # status moods never
    assert abs(o["speaking_bloom"] - 1.4 * 1.05) < 1e-9             # luminance applies to all
    assert o["same_install"] is True and o["other_install"] is True


@pytest.mark.skipif(not node, reason="node is not installed")
@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_a_look_that_draws_slower_is_undone_and_one_that_does_not_is_kept(path):
    o = _run(path)
    assert o["heavy_undone"] == 1
    assert o["after_undo_hash"] == "sha256:b"
    assert "too heavy" in o["held_message"]
    assert o["light_kept"] is True
    assert o["not_calm_ignored"] is True


WIRING_SCENE = [
    "const gridSize = 3; const spacing = FridayGenome.EX('CUBES', 'spacing', 1.6);",
    "if (FridayGenome.rand('CUBES') < FridayGenome.EX('CUBES', 'sparsity', 0.15)) continue;",
    "for(let i=1; i<=FridayGenome.EX('ASTROLABE', 'rings', 8); i++) {",
    "const nTubes = FridayGenome.EX('CABLES', 'tubes', 80);",
    "FridayHopf.build(gNone, { lines: FridayGenome.EX('NONE', 'lines', 100) });",
    "FridayWormhole.build(gWorm, { rings: FridayGenome.EX('WORMHOLE', 'rings', 20) });",
    "FridayBlackHole.build(gHole, { dust: FridayGenome.EX('BLACKHOLE', 'dust', 320), lowCost: !!window.__fridayLowCost,",
    "FridayDyson.build(gIco, { shells: FridayGenome.EX('ICOSAHEDRON', 'shells', 3),",
    "coarse: FridayGenome.EX('ICOSAHEDRON', 'detail_delta', 0) < 0, rand: () => FridayGenome.rand('ICOSAHEDRON') });",
    "FridayDirac.animate(delta, Object.assign({ wave: FridayGenome.EX('QUANTUM', 'wave', 10) }, herState));",
    "const nPillars = FridayGenome.EX('DOME', 'pillars', 8);",
    "edenLady = FridayRez.build(gEden, FridayGenome.EX('EDEN', 'stage', 0));",
    "for(let i=0; i<FridayGenome.EX('NETWORK', 'nodes', 120); i++) {",
    "FridayGenome.start();",
    "FridayGenome.sample(dt, FridayGestures.settled());",
    "window.FridayGenome = FridayGenome;",
]


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_the_builders_read_the_genome(path):
    t = path.read_text(encoding="utf-8")
    for anchor in WIRING_SCENE:
        assert t.count(anchor) == 1, f"{path.name}: {anchor!r} x{t.count(anchor)}"


@pytest.mark.parametrize("path", APPS, ids=lambda p: p.name)
def test_the_scene_menu_has_the_evolution_controls(path):
    t = path.read_text(encoding="utf-8")
    assert t.count("function AvatarEvolutionSection()") == 1
    assert "Rotate structures automatically" in t and "Reset to auto (evolution)" not in t
    assert ("React.createElement(AvatarEvolutionSection, null)" in t) or ("<AvatarEvolutionSection/>" in t)
    for url in ("/api/avatar/status", "/api/avatar/history", "/api/avatar/evolve-now",
                "/api/avatar/undo", "/api/avatar/rollback", "/api/avatar/reset",
                "/api/avatar/settings", "/api/avatar/use-local"):
        assert url in t, url
