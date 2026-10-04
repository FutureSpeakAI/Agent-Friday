"""Shelves behaviours that must survive a data refresh, a growing library, a held
key and a closing view: the selection, the home frame, the section load, glow
and dazzle, disposal, keyboard camera and the label textures.

Each test runs the real engine and component on the headless page (node, fake
clock); none needs a browser.
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
const mkNodes = (docs) => [
  { id: 'f:1', kind: 'folder', title: 'Contracts', documents: docs, parent: null },
  { id: 'f:2', kind: 'folder', title: 'Notes', documents: docs, parent: null }]
  .concat(Array.from({ length: docs }, (_, k) => ({ id: 'd:' + (k + 1), kind: 'document', title: 'Doc ' + (k + 1), ext: 'pdf', pages: 3, parent: k % 2 ? 'f:2' : 'f:1' })));
const nodes = mkNodes(12);
const treeOf = key => {
  const n = Number(key.slice(2));
  if (n === 9) return { status: 'ok', nodes: Array.from({ length: 80 }, (_, k) => ({ id: 's:' + (900 + k), kind: 'section', title: 'Part ' + k, level: 1, page_from: k + 1, page_to: k + 1, parent: 'd:9', doc: 9 })) };
  return { status: 'ok', nodes: [1, 2, 3].map(k => ({ id: 's:' + (n * 100 + k), kind: 'section', title: 'Heading ' + k, level: 1, page_from: k, page_to: k, parent: key, doc: n })) };
};
const answer = (u, extra) => {
  let m = /^\/api\/library\/tree\?node=(d%3A\d+)&depth=3$/.exec(u);
  if (m) return treeOf(decodeURIComponent(m[1]));
  m = /^\/api\/library\/section\/(\d+)$/.exec(u);
  if (m) return { section: { heading: 'Section ' + m[1], passages: [{ id: Number(m[1]) * 10, text: 'A passage.', block: Number(m[1]) * 10, page: 1 }] } };
  return (extra && extra[u]) || null;
};
const okFetch = extra => u => { const r = answer(u, extra); return r ? Promise.resolve({ json: () => Promise.resolve(r) }) : Promise.reject(new Error('404 ' + u)); };
const tick = async () => { for (let k = 0; k < 30; k++) await new Promise(r => setTimeout(r, 0)); };
const UI = (live) => ({ props: () => ({}), setLive: t => { if (live) live.push(t); }, setHover() {}, setCap() {}, focusBox() {} });
function session(opts, nds) {
  const pg = makePage(Object.assign({ fetch: okFetch() }, opts));
  pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
  const I = pg.window.LibraryShelves3D.__internals;
  const live = [];
  const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), UI(live));
  const eng = pg.window.__libraryShelves3D;
  core.setNodes(nds || nodes); pg.advance(2500, 16);
  return { pg, core, eng, I, live };
}
// the engine's own record of which card is selected: x = 2 in the card state
const selectedIndex = pg => {
  const meshes = pg.rec.instanced.filter(m => m.geometry && m.geometry.attributes && m.geometry.attributes.aState);
  const a = meshes[meshes.length - 1].geometry.attributes.aState.array;
  for (let i = 0; i < a.length / 4; i++) if (a[i * 4] === 2) return i;
  return -1;
};
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
def test_the_selection_survives_a_nodes_refresh_and_opening_a_document():
    out = _run(r"""
const s = await session();
s.core.select('d:3');
out.before = [s.core._state.idx.get('d:3'), selectedIndex(s.pg)];
s.core.setNodes(mkNodes(14));                      // a refresh while indexing: the data is rebuilt
out.afterRefresh = [s.core._state.idx.get('d:3'), selectedIndex(s.pg)];
s.core.activate('d:5'); await tick(); s.pg.advance(300, 16);
out.afterOpen = [s.core._state.idx.get('d:5'), selectedIndex(s.pg)];
""")
    for k in ("before", "afterRefresh", "afterOpen"):
        want, got = out[k]
        assert want >= 0 and got == want, (k, out)


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_home_camera_refits_when_the_library_grows_until_the_camera_is_moved():
    out = _run(r"""
const s = await session();
const reframes = []; const orig = s.eng.reframe;
s.eng.reframe = soft => { reframes.push(!!soft); return orig(soft); };
const r0 = s.eng.camState().r;
s.core.setNodes(mkNodes(120)); s.pg.advance(3000, 16);
out.grewR = [r0, s.eng.camState().r];
out.homeCalls = reframes.length;
// once the camera has been moved by a flight, growth leaves it where the reader put it
s.core.focusPassage({ doc: 5, section: 502 }); await tick(); s.pg.advance(2000, 16);
reframes.length = 0;
const before = JSON.stringify(s.eng.camState());
s.core.setNodes(mkNodes(200)); s.pg.advance(3000, 16);
out.movedCalls = reframes.length;
out.movedSame = before === JSON.stringify(s.eng.camState());
""")
    r0, r1 = out["grewR"]
    assert out["homeCalls"] >= 1 and r1 > r0 * 1.2, out
    assert out["movedCalls"] == 0 and out["movedSame"] is True, out


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_section_load_is_cached_per_document_and_the_passage_is_framed_after_it():
    out = _run(r"""
const hits = [];
const s = await session({ fetch: u => { hits.push(u); return okFetch()(u); } });
hits.length = 0;
// two requests for the same document while its sections are still loading
const p1 = s.core.focusPassage({ doc: 5, section: 502 });
const p2 = s.core.focusPassage({ doc: 5, section: 502 });
const r2 = await p2; await p1;
out.second = { sec: r2 && r2.sec && r2.sec.id, fallback: r2 && r2.fallback };
out.treeFetches = hits.filter(u => u.indexOf('/tree?') > 0).length;
// a section beyond the 60-section cap is still the one framed; a section that does not exist falls back, and says so
const far = await s.core.focusPassage({ doc: 9, section: 979 });
out.far = { sec: far.sec && far.sec.id, fallback: far.fallback, n: s.core._state.sections['d:9'].length };
const none = await s.core.focusPassage({ doc: 9, section: 99999 });
out.none = { sec: none.sec && none.sec.id, fallback: none.fallback };
out.hasFar = s.core._state.sections['d:9'].some(x => x.id === 's:979');
""")
    assert out["second"] == {"sec": "s:502", "fallback": False}, out
    assert out["treeFetches"] == 1, out
    assert out["far"]["sec"] == "s:979" and out["far"]["fallback"] is False and out["far"]["n"] == 60, out
    assert out["none"]["fallback"] is True and out["none"]["sec"] is not None, out


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_view_that_is_disposed_ignores_late_answers():
    out = _run(r"""
const pending = [];
const fetch = u => new Promise(res => pending.push(() => res({ json: () => Promise.resolve(u.indexOf('/api/settings') === 0 ? { settings: { studio_dazzle: 'off' } } : answer(u)) })));
const s = await session({ fetch });
const calls = { fly: 0, dazzle: 0, data: 0, live: 0 };
s.core.activate('d:5');                            // the sections are being fetched
s.core.dispose();
const e = s.eng;
e.flyTo = () => { calls.fly++; }; e.setDazzle = () => { calls.dazzle++; }; e.setData = () => { calls.data++; };
s.live.length = 0;
pending.splice(0).forEach(f => f()); await tick();
pending.splice(0).forEach(f => f()); await tick();
out.calls = calls; out.live = s.live.length;
""")
    assert out["calls"] == {"fly": 0, "dazzle": 0, "data": 0, "live": 0} and out["live"] == 0, out


@pytest.mark.skipif(not node, reason="node is not installed")
def test_dazzle_off_frees_the_glow_and_turning_it_back_on_restores_it():
    out = _run(r"""
const s = await session();
const mats = [], orig = s.eng.glowSprite;
s.eng.glowSprite = c => { const sp = orig(c); const m = sp.material, d = m.dispose; m.disposed = 0; m.dispose = function () { m.disposed++; return d && d.call(m); }; mats.push(m); return sp; };
const glow = () => s.eng.overlay.children.filter(o => o.material && o.material.blending === 2).map(o => +o.material.opacity.toFixed(2));
const ev = (type, detail) => s.pg.window.dispatchEvent({ type, detail });
ev('friday-library-decision', { node_id: 'd:3', doc_id: 3, p: 0.9 }); s.pg.advance(600, 16);
out.on = glow();
ev('friday-dazzle', 'off'); s.pg.advance(100, 16);
out.off = glow(); out.disposed = mats.map(m => m.disposed);
ev('friday-dazzle', 'full'); s.pg.advance(600, 16);
out.back = glow();
ev('friday-dazzle', 'subtle'); s.pg.advance(600, 16);
out.subtle = glow();
""")
    assert out["on"] == [0.5] and out["off"] == [], out
    assert out["disposed"] == [1], out
    assert out["back"] == [0.5], out
    assert len(out["subtle"]) == 1 and 0 < out["subtle"][0] < 0.5, out


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_held_arrow_key_opens_a_document_at_most_three_times_a_second():
    out = _run(r"""
const s = await session();
const times = [], orig = s.eng.setData;
s.eng.setData = function () { times.push(s.pg.clock.t); return orig.apply(this, arguments); };
const evidence = [1, 2, 3, 4, 5, 6].map(k => ({ label: String(k), doc_id: k, block_id: k * 10 + 1, page: 1, section_id: k * 100 + 1, score: 1 - k / 10 }));
s.pg.window.dispatchEvent({ type: 'friday-library-evidence', detail: { evidence } });
s.pg.advance(50, 16); await tick();
times.length = 0;
const t0 = s.pg.clock.t;
for (let k = 0; k < 22; k++) { s.core.key({ key: 'ArrowDown' }); s.pg.advance(25, 5); await tick(); }   // a held key: 22 repeats in 550 ms
s.pg.advance(1500, 16); await tick();
out.times = times.map(t => t - t0);
out.idx = s.core._state.eviIdx;
out.open = s.core._state.openDoc;
out.evi = s.core._state.evi.map(e => e.doc_id);
""")
    assert out["times"] and _max_in_window(out["times"]) <= 3, out["times"]
    assert out["open"] == "d:%d" % out["evi"][out["idx"]], out    # the key's last stop is the document that ends up open


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_spatial_arrow_pans_the_camera_to_a_card_that_is_off_screen_and_leaves_visible_ones_alone():
    out = _run(r"""
async function arrow(offscreen) {
  const pg = makePage({ fetch: okFetch() });
  pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
  // a projection that keeps the layout's order but puts every card off screen (or well inside it)
  pg.window.THREE.Vector3.prototype.project = function () { this.x = this.x * 0.01 + (offscreen ? 2 : 0); this.y = this.y * 0.01; this.z = 0; return this; };
  const I = pg.window.LibraryShelves3D.__internals;
  const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), UI());
  const eng = pg.window.__libraryShelves3D;
  core.setNodes(nodes); pg.advance(2500, 16);
  core.select('d:1');
  const before = eng.camState().t;
  core.key({ key: 'ArrowRight' });
  const sel = core._state.sel, i = core._state.idx.get(sel);
  pg.advance(3000, 16);
  const pos = eng.itemPos(i), after = eng.camState().t;
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  return { moved: dist(before, after), toItem: dist(after, pos), changed: sel !== 'd:1', home: core._state.home };
}
out.off = await arrow(true);
out.on = await arrow(false);
""")
    assert out["off"]["changed"] and out["off"]["moved"] > 0.5 and out["off"]["toItem"] < 0.5, out
    assert out["off"]["home"] is False
    assert out["on"]["changed"] and out["on"]["moved"] < 1e-6 and out["on"]["home"] is True, out


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_nodes_refresh_does_not_redraw_label_textures():
    out = _run(r"""
const s = await session();
s.core.activate('d:5'); await tick(); s.pg.advance(600, 16);   // the fan's labels are on screen too
let made = 0; const mk = s.pg.document.createElement;
s.pg.document.createElement = tag => { if (tag === 'canvas') made++; return mk(tag); };
for (let k = 0; k < 3; k++) { s.core.setNodes(mkNodes(12).map(n => Object.assign({}, n))); s.pg.advance(100, 16); }
out.made = made;
""")
    assert out["made"] == 0, out
