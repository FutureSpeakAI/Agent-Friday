"""Search results are announced through a polite live region the component renders.

"Found 3 passages in <document>, section <heading>." is built by one function
and spoken once, when the evidence's best passage has been flown to.
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
  '/api/library/section/22': { section: { heading: 'Heading 2', passages: [{ id: 301, text: 'Rent is due monthly.', block: 1, page: 2 }] } }
};
const tick = async () => { for (let k = 0; k < 30; k++) await new Promise(r => setTimeout(r, 0)); };
"""


def _run(body):
    src = ("const HARNESS = " + json.dumps(str(HARNESS)) + ";\n" + PRELUDE + "(async () => {\n" + body
           + "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_announcement_text():
    out = _run(r"""
const pg = makePage(); pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const A = pg.window.LibraryShelves3D.__internals.announceText;
out.three = A({ count: 3, doc: 'Lease', heading: 'Heading 2' });
out.one = A({ count: 1, doc: 'Lease', heading: 'Heading 2' });
out.noHeading = A({ count: 2, doc: 'Lease' });
out.noDoc = A({ count: 4 });
out.none = A({ count: 0 });
""")
    assert out["three"] == "Found 3 passages in Lease, section Heading 2."
    assert out["one"] == "Found 1 passage in Lease, section Heading 2."
    assert out["noHeading"] == "Found 2 passages in Lease."
    assert out["noDoc"] == "Found 4 passages."
    assert out["none"] == "Nothing in your Library matched."


@pytest.mark.skipif(not node, reason="node is not installed")
def test_the_component_renders_a_polite_live_region():
    out = _run(r"""
const pg = makePage(); pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const C = pg.window.LibraryShelves3D;
const tree = C({ nodes: nodes, status: null, onOpen() {} });
const found = [];
const walk = el => { if (!el || typeof el !== 'object') return; if (el.p && el.p.role === 'status') found.push(el.p); (el.c || []).forEach(walk); };
walk(tree);
out.regions = found.map(p => [p['aria-live'], p['aria-atomic']]);
out.rootRole = tree.p.role; out.focusable = tree.p.tabIndex;
""")
    assert out["regions"] == [["polite", "true"]]
    assert out["focusable"] == 0              # keys are handled on the canvas's own frame


@pytest.mark.skipif(not node, reason="node is not installed")
def test_a_search_result_is_announced_once_after_the_best_passage_is_framed():
    out = _run(r"""
const pg = makePage({ fetch: u => { const r = routes[u]; return r ? Promise.resolve({ json: () => Promise.resolve(r) }) : Promise.reject(new Error('404')); } });
pg.load('static/studio_files3d.js'); pg.load('static/library_shelves.js');
const I = pg.window.LibraryShelves3D.__internals;
const spoken = [];
const core = I.createCore(pg.window.Friday3D, pg.window.THREE, pg.makeEl('div'), { props: () => ({}), setLive: t => spoken.push(t), setHover() {}, setCap() {}, focusBox() {} });
core.setNodes(nodes); pg.advance(200);
pg.window.dispatchEvent({ type: 'friday-library-evidence', detail: { evidence: [
  { label: '1', doc_id: 5, block_id: 301, page: 2, section_id: 22, score: 0.9, sure: 'sure' },
  { label: '2', doc_id: 6, block_id: 401, page: 1, section_id: 40, score: 0.4, sure: 'a guess' },
  { label: '3', doc_id: 5, block_id: 302, page: 3, section_id: 23, score: 0.3, sure: 'a guess' }] } });
out.immediately = spoken.slice();
await tick(); pg.advance(1500, 16); await tick();
out.spoken = spoken.filter(t => /^Found/.test(t));
""")
    assert out["immediately"] == []
    assert out["spoken"] == ["Found 3 passages in Lease, section Heading 2."]
