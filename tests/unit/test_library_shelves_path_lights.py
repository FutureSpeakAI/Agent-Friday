"""Search lights the path: paced, weighted by confidence, cyan only, still when idle.

The pacer and the controller are pure and run on a fake clock; the component's
core runs on the headless page, so the lights counted are the ones it really
adds to the scene, and the colours checked are the ones it really gives them.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "unit" / "library_shelves_harness.js"
node = shutil.which("node")

PRELUDE = r"""
const { makePage } = require(HARNESS);
const out = {};
const nodes = [
  { id: 'f:1', kind: 'folder', title: 'Contracts', documents: 6, parent: null },
  { id: 'f:2', kind: 'folder', title: 'Notes', documents: 6, parent: null }]
  .concat([1, 2, 3, 4, 5, 6].map(k => ({ id: 'd:' + k, kind: 'document', title: 'Doc ' + k, ext: 'pdf', pages: 3, parent: 'f:1' })))
  .concat([7, 8, 9, 10, 11, 12].map(k => ({ id: 'd:' + k, kind: 'document', title: 'Doc ' + k, ext: 'pdf', pages: 3, parent: 'f:2' })));
const routes = {
  '/api/library/tree?node=d%3A5&depth=3': { status: 'ok', nodes: [1, 2, 3].map(k => ({ id: 's:' + (20 + k), kind: 'section', title: 'Heading ' + k, level: 1, page_from: k, page_to: k, parent: 'd:5', doc: 5 })) },
  '/api/library/section/22': { section: { heading: 'Heading 2', passages: [{ id: 301, text: 'First passage.', block: 1, page: 2 }] } }
};
const mk = opts => makePage(Object.assign({ fetch: u => { const r = routes[u]; return r ? Promise.resolve({ json: () => Promise.resolve(r) }) : Promise.reject(new Error('404')); } }, opts));
const tick = () => new Promise(r => setTimeout(r, 0));
const hexOf = c => (Math.round(c.r * 255) << 16) | (Math.round(c.g * 255) << 8) | Math.round(c.b * 255);
"""


def _run(body):
    src = ("const HARNESS = " + json.dumps(str(HARNESS)) + ";\n" + PRELUDE + "(async () => {\n" + body
           + "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _max_in_window(times, window=1000):
    return max((sum(1 for u in times if t <= u < t + window) for t in times), default=0)


@pytest.mark.skipif(not node, reason="node is not installed")
def test_twelve_decisions_in_fifty_ms_are_paced_to_three_lights_a_second():
    out = _run(r"""
const pg = mk(); pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const I = pg.window.LibraryShelves3D.__internals;
const emitted = [];
const pl = I.createPathLights({ now: () => pg.clock.t, schedule: (f, ms) => pg.ctx.setTimeout(f, ms), cancel: id => pg.ctx.clearTimeout(id),
  emit: ev => emitted.push([ev.type, ev.key, Math.round(pg.clock.t - 1000)]) });
for (let k = 0; k < 12; k++) { pl.onDecision({ node_id: 'd:' + (k + 1), p: 0.9 - k * 0.05, title: 'Doc ' + k }); pg.advance(4, 1); }
out.pendingAfterBurst = pl.pending();
pg.advance(8000, 5);
out.times = emitted.filter(e => e[0] === 'light').map(e => e[2]);
out.keys = emitted.filter(e => e[0] === 'light').map(e => e[1]);
out.pendingAtEnd = pl.pending();
// an empty wait: no decisions, ten seconds, no light and no armed timer
const quiet = [];
const idle = I.createPathLights({ now: () => pg.clock.t, schedule: (f, ms) => pg.ctx.setTimeout(f, ms), cancel: id => pg.ctx.clearTimeout(id), emit: ev => quiet.push(ev) });
pg.advance(10000, 50);
out.idle = { emitted: quiet.length, timers: pg.clock.timers.length, pending: idle.pending() };
""")
    times = out["times"]
    assert len(times) == 12 and out["keys"] == ["d:%d" % k for k in range(1, 13)]
    assert times[0] <= 5, "the first light is not held back"
    assert _max_in_window(times) <= 3, times
    assert all(b - a >= 333 for a, b in zip(times, times[1:])), times
    assert out["pendingAfterBurst"] >= 8 and out["pendingAtEnd"] == 0
    assert out["idle"] == {"emitted": 0, "timers": 0, "pending": 0}


@pytest.mark.skipif(not node, reason="node is not installed")
def test_confidence_is_a_line_weight_and_a_word_never_a_hue():
    out = _run(r"""
const pg = mk(); pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const I = pg.window.LibraryShelves3D.__internals;
out.c = [0.95, 0.8, 0.79, 0.5, 0.49, 0.1].map(p => I.confidence(p));
// the scene: the same twelve decisions, each at a different confidence, through the real component core
const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), { props: () => ({}), setLive() {}, setHover() {}, setCap() {}, focusBox() {} });
const eng = pg.window.__libraryShelves3D;
core.setNodes(nodes); pg.advance(100);
const ps = [0.95, 0.6, 0.2];
ps.forEach((p, k) => pg.window.dispatchEvent({ type: 'friday-library-decision', detail: { level: 1, kind: 'document', node_id: 'd:' + (k + 1), doc_id: k + 1, title: 'Doc', p } }));
pg.advance(2500, 16);
const cyl = eng.overlay.children.filter(o => o.scale.x < 0.2 && o.scale.y > 1);
out.radii = cyl.map(o => +o.scale.x.toFixed(3)).sort((a, b) => b - a);
out.colours = eng.overlay.children.map(o => hexOf(o.material.color));
out.lit = core.litCount();
""")
    assert out["c"][0] == {"word": "sure", "radius": 0.07}
    assert [c["word"] for c in out["c"]] == ["sure", "sure", "fairly sure", "fairly sure", "a guess", "a guess"]
    assert out["radii"] == [0.07, 0.04, 0.02], out
    assert out["lit"] == 3
    cyan, amber = 0x00D4FF, 0xF59E0B
    assert out["colours"] and set(out["colours"]) == {cyan}, out["colours"]
    assert amber not in out["colours"]


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_component_adds_at_most_three_visible_lights_a_second_and_dims_parked_routes():
    out = _run(r"""
const pg = mk(); pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const I = pg.window.LibraryShelves3D.__internals;
const live = [];
const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), { props: () => ({}), setLive: t => live.push(t), setHover() {}, setCap() {}, focusBox() {} });
const eng = pg.window.__libraryShelves3D;
core.setNodes(nodes); pg.advance(100);
const t0 = pg.clock.t, seen = [];
let last = 0;
const ev = (type, detail) => pg.window.dispatchEvent({ type, detail });
const poll = () => { const n = core.litCount(); while (last < n) { seen.push(pg.clock.t - t0); last++; } };
for (let k = 1; k <= 12; k++) { ev('friday-library-decision', { level: 1, kind: 'document', node_id: 'd:' + k, doc_id: k, title: 'Doc ' + k, p: 0.9 }); poll(); for (let m = 0; m < 4; m++) { pg.advance(1, 1); poll(); } }
for (let t = 0; t < 6000; t++) { pg.advance(1, 1); poll(); }
out.seen = seen;
// evidence arrives: one settle; the lights off the best path go faint
ev('friday-library-evidence', { evidence: [{ label: '1', doc_id: 5, block_id: 301, page: 2, section_id: 22, score: 0.9, sure: 'sure' }] });
pg.advance(400, 16);
for (let k = 0; k < 10; k++) { await tick(); pg.advance(100, 16); }
out.opacities = eng.overlay.children.filter(o => o.material && o.material.blending === 2).map(o => +o.material.opacity.toFixed(2));
out.live = live;
""")
    seen = out["seen"]
    assert len(seen) == 12 and _max_in_window(seen) <= 3, seen
    assert out["live"] == ["Found 1 passage in Doc 5, section Heading 2."], out["live"]
    # the route to the best passage stays lit; the eleven parked lights are faint
    assert out["opacities"].count(0.5) == 1 and out["opacities"].count(0.14) == 11, out["opacities"]


@pytest.mark.skipif(not node, reason="node is not installed")
def test_dazzle_off_keeps_lines_and_drops_glow_and_reduced_motion_lights_at_once():
    out = _run(r"""
const settings = off => ({ '/api/settings': { settings: { studio_dazzle: off ? 'off' : 'full' } } });
async function lit(opts, off) {
  const all = Object.assign({}, routes, settings(off));
  const pg = makePage(Object.assign({ fetch: u => { const r = all[u]; return r ? Promise.resolve({ json: () => Promise.resolve(r) }) : Promise.reject(new Error('404')); } }, opts));
  pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
  const I = pg.window.LibraryShelves3D.__internals;
  const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), { props: () => ({}), setLive() {}, setHover() {}, setCap() {}, focusBox() {} });
  const eng = pg.window.__libraryShelves3D;
  core.setNodes(nodes); pg.advance(100); await tick();
  pg.window.dispatchEvent({ type: 'friday-library-decision', detail: { node_id: 'd:3', doc_id: 3, p: 0.9 } });
  const first = eng.overlay.children.map(o => [o.material.blending === 2 ? 'glow' : 'line', +o.material.opacity.toFixed(2)]);
  pg.advance(600, 16);
  const later = eng.overlay.children.map(o => [o.material.blending === 2 ? 'glow' : 'line', +o.material.opacity.toFixed(2)]);
  return { dz: eng.dazzle(), first, later };
}
out.off = await lit({}, true);
out.full = await lit({}, false);
out.reduced = await lit({ reduced: true }, false);
""")
    assert out["off"]["dz"] == 0
    assert [k for k, _ in out["off"]["later"]] == ["line"], out["off"]      # no glow: only the line
    assert out["off"]["later"][0][1] == 0.9
    assert sorted(k for k, _ in out["full"]["later"]) == ["glow", "line"]
    assert out["full"]["first"][0][1] == 0 and out["full"]["later"][0][1] > 0   # ramped in, not switched on
    assert [o for o in out["reduced"]["first"] if o[0] == "glow"] == [["glow", 0.5]]   # reduced motion: at once, no ramp
