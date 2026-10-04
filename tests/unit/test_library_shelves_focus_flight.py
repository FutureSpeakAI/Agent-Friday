"""Citation focus: one flight folder -> document -> section -> passage, at most
1300 ms, skippable by any key or click, a plain cut under reduced motion, with
the cited block outlined 2px in cyan and reading text never under 12px.

Runs the real engine and component on the headless page; the clock is fake, so
"frames" are counted, not timed by the wall.
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
  { id: 'f:1', kind: 'folder', title: 'Contracts', documents: 2, parent: null },
  { id: 'd:5', kind: 'document', title: 'Lease', ext: 'pdf', pages: 20, parent: 'f:1' },
  { id: 'd:6', kind: 'document', title: 'NDA', ext: 'pdf', pages: 4, parent: 'f:1' }];
const routes = {
  '/api/library/tree?node=d%3A5&depth=3': { status: 'ok', nodes: [1, 2, 3].map(k => ({ id: 's:' + (20 + k), kind: 'section', title: 'Heading ' + k, level: 1, page_from: k, page_to: k, parent: 'd:5', doc: 5 })) },
  '/api/library/section/22': { section: { heading: 'Heading 2', passages: [
    { id: 300, text: 'An earlier paragraph.', block: 0, page: 2 },
    { id: 301, text: 'The cited paragraph says the tenant shall pay rent monthly.', block: 1, page: 2 }] } }
};
const tick = async () => { for (let k = 0; k < 30; k++) await new Promise(r => setTimeout(r, 0)); };
async function session(reduced) {
  const pg = makePage({ reduced, fetch: u => { const r = routes[u]; return r ? Promise.resolve({ json: () => Promise.resolve(r) }) : Promise.reject(new Error('404')); } });
  pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
  const I = pg.window.LibraryShelves3D.__internals;
  const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), { props: () => ({}), setLive() {}, setHover() {}, setCap() {}, focusBox() {} });
  const eng = pg.window.__libraryShelves3D;
  const flights = [], orig = eng.flyTo;
  eng.flyTo = (p, ms) => { flights.push([p.length, ms]); return orig(p, ms); };
  core.setNodes(nodes); pg.advance(300);
  return { pg, core, eng, flights, I };
}
const reading = eng => eng.overlay.children.find(o => o.geometry && o.material && o.material.map && o.material.map.image);
"""


def _run(body):
    src = ("const HARNESS = " + json.dumps(str(HARNESS)) + ";\n" + PRELUDE + "(async () => {\n" + body
           + "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


FOCUS = "{ doc: 5, block: 301, page: 2, section: 22 }"


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_focus_flight_is_one_tween_of_at_most_1300_ms_through_four_stops():
    out = _run(r"""
const s = await session(false);
const before = JSON.stringify(s.eng.camState());
s.core.focusPassage(""" + FOCUS + r""");
await tick();
const t0 = s.pg.clock.t, cams = [JSON.stringify(s.eng.camState())];
out.flyingAtStart = s.eng.isFlying();
while (s.eng.isFlying() && s.pg.clock.t - t0 < 4000) { s.pg.advance(16, 16); cams.push(JSON.stringify(s.eng.camState())); }
out.ms = s.pg.clock.t - t0;
out.distinct = new Set(cams).size;
out.moved = cams[cams.length - 1] !== before;
out.flights = s.flights;
// the pure flight: however far the path, never longer than 1300
const P = s.pg.window.__files3dInternals;
const from = { t: [0, 0, 0], theta: 0, phi: 1.5, r: 100 };
const far = [{ t: [900, 0, 0], theta: 9, phi: 1.5, r: 5 }];
out.pureLong = P.makeFlight(from, far, 99999, false).dur;
out.pureDefault = P.makeFlight(from, far, undefined, false).dur;
const f = P.makeFlight(from, [{ t: [10, 0, 0], theta: 1, phi: 1.5, r: 50 }, { t: [20, 0, 0], theta: 2, phi: 1.5, r: 20 }], 1000, false);
out.pureMid = f.sample(500).cam.t[0];
out.pureEnd = f.sample(1000).done;
""")
    assert out["flyingAtStart"] is True
    assert out["flights"] == [[4, 1300]], out["flights"]            # folder, document, section, passage: one flight
    assert 0 < out["ms"] <= 1300 + 16, out["ms"]
    assert out["distinct"] > 20 and out["moved"] is True, out
    assert out["pureLong"] <= 1300 and out["pureDefault"] <= 1300
    assert 9 < out["pureMid"] < 11 and out["pureEnd"] is True      # halfway through the time is halfway down the path


@pytest.mark.skipif(not node, reason="node is not installed")
def test_reduced_motion_cuts_to_the_passage_and_outlines_it_at_once():
    out = _run(r"""
const s = await session(true);
const before = JSON.stringify(s.eng.camState());
s.core.focusPassage(""" + FOCUS + r""");
await tick();                                    // the data arrives; the clock has not moved a frame
out.flying = s.eng.isFlying();
const cams = [JSON.stringify(s.eng.camState())];
for (let k = 0; k < 10; k++) { s.pg.advance(16, 16); cams.push(JSON.stringify(s.eng.camState())); }
out.distinct = new Set(cams).size;
out.moved = cams[0] !== before;
const mesh = reading(s.eng);
const calls = mesh.material.map.image._ctx.calls;
out.stroke = calls.filter(c => c[0] === 'strokeRect').length;
out.lineWidth = calls.filter(c => c[0] === 'set:lineWidth').map(c => c[1]).pop();
out.strokeStyle = calls.filter(c => c[0] === 'set:strokeStyle').map(c => c[1]).pop();
out.fonts = Array.from(new Set(calls.filter(c => c[0] === 'set:font').map(c => c[1])));
out.flights = s.flights;
""")
    assert out["flying"] is False
    assert out["distinct"] == 1 and out["moved"] is True, out        # no tween frames: one camera, already there
    assert out["stroke"] == 1                                         # the cited block is outlined with no wait
    assert out["strokeStyle"] == "#00d4ff"                            # --fr-cyan, read from the token
    assert abs(out["lineWidth"] * 14 / 16 - 2) < 1e-6                 # 2px on screen at the reading scale


@pytest.mark.skipif(not node, reason="node is not installed")
def test_any_key_or_click_skips_the_flight():
    out = _run(r"""
const s = await session(false);
s.core.focusPassage(""" + FOCUS + r""");
await tick();
s.pg.advance(200, 16);
out.midFlight = s.eng.isFlying();
const mid = JSON.stringify(s.eng.camState());
s.core.key({ key: 'Shift' });                    // any key
out.afterKey = s.eng.isFlying();
const end = s.eng.camState();
out.jumped = JSON.stringify(end) !== mid;
// a second flight, skipped by a click
s.core.focusPassage({ doc: 5, block: 300, page: 2, section: 22 });
await tick();
s.pg.advance(100, 16);
out.second = s.eng.isFlying();
s.core.skip();                                   // the pointer-down on the stage
out.afterClick = s.eng.isFlying();
""")
    assert out["midFlight"] is True and out["afterKey"] is False and out["jumped"] is True
    assert out["afterClick"] is False


@pytest.mark.skipif(not node, reason="node is not installed")
def test_reading_text_is_never_under_12px_on_screen_and_the_measure_is_72ch():
    out = _run(r"""
const pg = makePage(); pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const I = pg.window.LibraryShelves3D.__internals;
const sizes = [];
[340, 500, 700, 1080, 1600].forEach(h => {
  const r = I.readingScale({ fovDeg: 52, planeW: 8, canvasW: 800, fontPx: I.READ.font, viewH: h, target: I.READ.target });
  sizes.push(+r.onScreen.toFixed(3));
});
out.sizes = sizes;
out.font = I.READ.font; out.lineH = I.READ.lineH; out.measure = I.READ.measure;
// 72ch on the canvas: the wrap width is 72 zero-widths, so a long line breaks near 72 characters
const lines = I.wrapToWidth('word '.repeat(60), s => s.length * 10, 72 * 10);
out.longest = Math.max.apply(null, lines.map(l => l.length));
// the type drawn on the plane: Inter for reading, JetBrains Mono for the page number
const ctx = pg.makeEl('canvas').getContext('2d');
const pages = I.layoutPassages([{ text: 'Rent is due monthly.', page: 12 }], s => s.length * 8, 72 * 8, 20);
const geom = I.readingGeometry(ctx, pages[0], { heading: 'Heading' });
I.paintReadingPage(ctx, pages[0], geom, { heading: 'Heading', ground: 'g', text: 't', dim: 'd', cyan: 'c' });
out.fonts = Array.from(new Set(ctx.calls.filter(c => c[0] === 'set:font').map(c => c[1])));
out.label = pages[0][0].label;
""")
    assert all(s >= 12 for s in out["sizes"]), out["sizes"]
    assert out["font"] == 16 and out["lineH"] == 1.55 and out["measure"] == 72
    assert 60 <= out["longest"] <= 73, out["longest"]
    assert out["label"] == "p. 12"
    assert any("Inter" in f for f in out["fonts"]) and any("JetBrains Mono" in f for f in out["fonts"])
    assert not any("Orbitron" in f for f in out["fonts"])
    for f in out["fonts"]:
        px = int(f.split("px")[0].split()[-1])
        assert px >= 16, f             # at the 14px reading scale, 16 canvas px is >= 12 on screen
