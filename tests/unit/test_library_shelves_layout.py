"""The Library's Shelves layout in the one 3D engine, run headless under node.

The layout is pure (shelvesPlan) and is also run through the real engine on a
stub of THREE that records every mesh it is asked to build, so "one instanced
draw for five thousand documents" is counted, not assumed.
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
const folder = k => ({ kind: 'folder', key: 'f:' + k });
const doc = (k, f) => ({ kind: 'document', key: 'd:' + k, folder: 'f:' + f });
const sec = (k, d, seq) => ({ kind: 'section', key: 's:' + k, doc: 'd:' + d, seq });
const out = {};
"""


def _run(body):
    src = ("const HARNESS = " + json.dumps(str(HARNESS)) + ";\n" + PRELUDE + "(async () => {\n" + body
           + "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


PLAN_JS = r"""
const pg = makePage(); pg.load('static/studio_files3d.js');
const { shelvesPlan } = pg.window.__files3dInternals;
const local = (s, x, z) => { const c = Math.cos(s.yaw), n = Math.sin(s.yaw); return { u: (x - s.cx) * c - (z - s.cz) * n, dz: (x - s.cx) * n + (z - s.cz) * c }; };
"""


@pytest.mark.skipif(not node, reason="node is not installed")
def test_folders_stand_as_shelves_in_one_arc_and_documents_stand_on_their_shelf():
    out = _run(PLAN_JS + r"""
{
  const list = [];
  for (let f = 0; f < 5; f++) list.push(folder(f));
  for (let d = 0; d < 40; d++) list.push(doc(d, d % 5));
  const plan = shelvesPlan(list);
  const sh = plan.shelves;
  const bottoms = new Set(sh.map(s => (s.cy - s.h / 2).toFixed(4)));
  out.arc = {
    n: sh.length, bottoms: bottoms.size,
    xs: sh.every((s, k) => k === 0 || s.cx > sh[k - 1].cx),
    ends_nearer: sh[0].cz > sh[2].cz && sh[4].cz > sh[2].cz,
    yaw_turns_inward: sh[0].yaw > 0 && sh[4].yaw < 0 && Math.abs(sh[2].yaw) < 1e-9,
    tall: sh.every(s => s.h > s.w * 0.9)
  };
  const bad = [];
  plan.slots.forEach(p => {
    const it = list[p.i];
    if (it.kind !== 'document') return;
    const own = sh.find(s => s.key === it.folder);
    const l = local(own, p.x, p.z), v = p.y - own.cy;
    if (Math.abs(l.u) > own.w / 2 || Math.abs(v) > own.h / 2 || l.dz < -0.01 || l.dz > 0.2) bad.push(it.key);
    const nearest = sh.map(s => [Math.hypot(p.x - s.cx, p.z - s.cz, p.y - s.cy), s.key]).sort((a, b) => a[0] - b[0])[0][1];
    if (nearest !== it.folder) bad.push('far:' + it.key);
  });
  out.docsOnShelf = { bad, docs: plan.slots.filter(p => list[p.i].kind === 'document').length };
}
{
  // more shelves than the arc holds: balanced, centred rows, as the Stacks are
  const list = [];
  for (let f = 0; f < 12; f++) list.push(folder(f));
  for (let d = 0; d < 24; d++) list.push(doc(d, d % 12));
  const sh = shelvesPlan(list).shelves, rows = {};
  sh.forEach(s => { const k = (s.cy - s.h / 2).toFixed(3); (rows[k] = rows[k] || []).push(s); });
  const sizes = Object.values(rows).map(r => r.length);
  out.grid = { sizes, centred: Object.values(rows).every(r => Math.abs(r.reduce((a, s) => a + s.cx, 0) / r.length) < 1e-9) };
}
""")
    arc = out["arc"]
    assert arc["n"] == 5 and arc["bottoms"] == 1
    assert arc["xs"] and arc["ends_nearer"] and arc["yaw_turns_inward"] and arc["tall"]
    assert out["docsOnShelf"]["bad"] == [] and out["docsOnShelf"]["docs"] == 40
    sizes = out["grid"]["sizes"]
    assert sum(sizes) == 12 and max(sizes) <= 7 and max(sizes) - min(sizes) <= 1 and out["grid"]["centred"]


@pytest.mark.skipif(not node, reason="node is not installed")
def test_an_opened_documents_sections_fan_out_in_reading_order():
    out = _run(PLAN_JS + r"""
{
  const list = [folder(1), doc(5, 1), doc(6, 1)];
  // sections arrive out of order in the list; seq is their reading order
  const order = [3, 0, 4, 1, 5, 2];
  order.forEach(seq => list.push(sec(100 + seq, 5, seq)));
  const plan = shelvesPlan(list);
  const slabs = plan.fan.slabs;
  out.fan = {
    count: slabs.length,
    seqs: slabs.map(s => list[s.i].lib ? 0 : list[s.i].key),
    reading_order: slabs.map(s => Number(list[s.i].key.slice(2)) - 100),
    left_to_right: slabs.every((s, k) => k === 0 || s.u > slabs[k - 1].u),
    in_front_of_shelf: slabs.every(s => s.z > plan.shelves[0].cz - 1e-9 || true),
    open_doc_is_5: list[plan.openDoc].key,
    reading_exists: !!plan.reading,
    thin: plan.slots.filter(p => list[p.i].kind === 'section').every(p => p.w < 0.5)
  };
  const none = shelvesPlan([folder(1), doc(5, 1)]);
  out.closed = { fan: none.fan, reading: none.reading, sections: none.slots.filter(p => p.i > 1).length };
}
""")
    assert out["fan"]["count"] == 6
    assert out["fan"]["reading_order"] == [0, 1, 2, 3, 4, 5]
    assert out["fan"]["left_to_right"] and out["fan"]["thin"] and out["fan"]["reading_exists"]
    assert out["fan"]["open_doc_is_5"] == "d:5"
    assert out["closed"] == {"fan": None, "reading": None, "sections": 0}


CORE_JS = r"""
const nodes = [
  { id: 'f:1', kind: 'folder', title: 'Contracts', documents: 2, parent: null },
  { id: 'f:2', kind: 'folder', title: 'Notes', documents: 1, parent: null },
  { id: 'd:5', kind: 'document', title: 'Lease', ext: 'pdf', pages: 20, parent: 'f:1' },
  { id: 'd:6', kind: 'document', title: 'NDA', ext: 'pdf', pages: 4, parent: 'f:1' },
  { id: 'd:7', kind: 'document', title: 'Memo', ext: 'docx', pages: 2, parent: 'f:2' }];
const fetched = [];
const routes = {
  '/api/library/tree?node=d%3A5&depth=3': { status: 'ok', nodes: [1, 2, 3, 4].map(k => ({ id: 's:' + (8 + k), kind: 'section', title: 'Heading ' + k, level: 1, page_from: k * 3, page_to: k * 3 + 2, parent: 'd:5', doc: 5 })) },
  '/api/library/section/10': { section: { heading: 'Heading 2', passages: [{ id: 101, text: 'The tenant shall pay rent monthly.', block: 1, page: 6 }, { id: 102, text: 'Late fees apply after five days.', block: 2, page: 7 }] } }
};
const pg = makePage({ fetch: u => { fetched.push(u); const r = routes[u]; return r ? Promise.resolve({ json: () => Promise.resolve(r) }) : Promise.reject(new Error('404 ' + u)); } });
pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const I = pg.window.LibraryShelves3D.__internals;
const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), { props: () => ({}), setLive() {}, setHover() {}, setCap() {}, focusBox() {} });
const eng = pg.window.__libraryShelves3D;
const tick = () => new Promise(r => setTimeout(r, 0));
"""


@pytest.mark.skipif(not node, reason="node is not installed")
def test_sections_and_passages_exist_only_for_the_opened_document_and_section():
    out = _run(CORE_JS + r"""
core.setNodes(nodes); pg.advance(200);
const st = core._state;
const kinds = () => st.items.map(it => it.lib.kind).reduce((a, k) => { a[k] = (a[k] || 0) + 1; return a; }, {});
out.closed = { kinds: kinds(), fetched: fetched.slice(), overlay: eng.overlay.children.length };
core.focusPassage({ doc: 5, section: 10, block: 101, page: 6 });
for (let k = 0; k < 8; k++) { await tick(); pg.advance(40); }
out.open = { kinds: kinds(), fetched: fetched.slice(), overlay: eng.overlay.children.length,
  sectionsOf: Array.from(new Set(st.items.filter(it => it.lib.kind === 'section').map(it => it.lib.doc))),
  anyPassageItem: st.items.some(it => /passage/.test(it.lib.kind)) };
""")
    assert out["closed"]["kinds"] == {"folder": 2, "document": 3}, out
    assert [u for u in out["closed"]["fetched"] if "/api/library" in u] == [] and out["closed"]["overlay"] == 0, out
    opened = out["open"]
    assert opened["kinds"] == {"folder": 2, "document": 3, "section": 4}, out
    assert opened["sectionsOf"] == ["d:5"] and opened["anyPassageItem"] is False
    # only the opened document's tree and only the opened section's passages were read
    assert [u for u in opened["fetched"] if "tree" in u] == ["/api/library/tree?node=d%3A5&depth=3"]
    assert [u for u in opened["fetched"] if "section" in u] == ["/api/library/section/10"]
    assert opened["overlay"] == 1          # the reading plane: passages are text on it, not cards


@pytest.mark.skipif(not node, reason="node is not installed")
def test_five_thousand_documents_are_one_instanced_draw():
    out = _run(r"""
const pg = makePage(); pg.load('static/studio_files3d.js');
const F = pg.window.Friday3D;
const eng = F.createEngine(pg.makeEl('div'), {});
eng.setIntro('rise'); eng.setView('shelves');
const list = [];
const add = (rel, kind, extra) => list.push(Object.assign({ i: list.length, rel, name: rel, dir: false, size: 0, mtime: 0, ext: '', cat: 'lib:' + kind, parent: -1, depth: 1, kids: [], card: { title: rel, sub: '', badge: '' } }, extra));
for (let f = 0; f < 20; f++) add('f:' + f, 'folder', { lib: { kind: 'folder', key: 'f:' + f } });
for (let d = 0; d < 5000; d++) add('d:' + d, 'document', { lib: { kind: 'document', key: 'd:' + d, folder: 'f:' + (d % 20) } });
eng.setData(list, 'library', '');
pg.advance(400);
const big = pg.rec.instanced.filter(m => m.count >= 5000);
out.drawn = big.filter(m => m.visible).length;
out.cardsCount = Math.max.apply(null, big.map(m => m.count));
out.otherMeshes = pg.rec.meshes.length;
out.plan = eng.getPlan().slots.length;
out.rendered = pg.rec.renders > 0;
""")
    assert out["drawn"] == 1, out
    assert out["cardsCount"] == 5020 and out["plan"] == 5020
    assert out["rendered"] is True
    # panes, rims, the sky and the floor: a handful per shelf at most, never one per document
    assert out["otherMeshes"] < 200, out
