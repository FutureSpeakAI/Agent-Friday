"""Each kind of helper has its own abstract form (avatar-visual-genome.md
§16.2), built under node with the vendored three.js from both scene files.

- Every kind builds a real form: lines or points, every coordinate finite,
  the whole form inside the orb's sphere.
- The kinds look different from each other: no two share a form.
- A kind's form follows the genome's step: different steps give different
  variations, the same step the same one.
- Colour and opacity can be set, and disposing frees every geometry and
  material once.
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
LIFE = re.compile(r"// <orb-life>\n(.*?)// </orb-life>", re.S)
FORMS = re.compile(r"// <orb-forms>\n(.*?)// </orb-forms>", re.S)

HARNESS = r"""
const THREE = require(THREE_PATH);
LIFE
FORMS
const F = FridayOrbForms, L = FridayOrbLife, out = { kinds: {} };
const sig = g => { const v = new THREE.Vector3(), a = []; g.updateMatrixWorld(true);
  g.traverse(c => { const p = c.geometry && c.geometry.attributes.position; if (!p) return;
    for (let i = 0; i < p.count; i += Math.max(1, Math.floor(p.count / 24))) { v.fromBufferAttribute(p, i).applyMatrix4(c.matrixWorld); a.push(v.x.toFixed(3), v.y.toFixed(3), v.z.toFixed(3)); } });
  return a.join(','); };
const stats = g => { const v = new THREE.Vector3(); let n = 0, r = 0, finite = true, lines = 0, points = 0; g.updateMatrixWorld(true);
  g.traverse(c => { if (c.isLine) lines++; if (c.isPoints) points++; const p = c.geometry && c.geometry.attributes.position; if (!p) return;
    for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i).applyMatrix4(c.matrixWorld); n++;
      if (![v.x, v.y, v.z].every(Number.isFinite)) finite = false; r = Math.max(r, v.length()); } });
  return { n, r, finite, lines, points }; };
for (const k of L.KIND_IDS) {
  const seeds = ['sha256:s1', 'sha256:s2', 'sha256:s3', 'sha256:s4', 'sha256:s5', 'sha256:s6'];
  const sigs = new Set(seeds.map(s => sig(F.build(k, L.formParams(k, s), 0x2dd4bf))));
  const g = F.build(k, L.formParams(k, 'sha256:s1'), 0x2dd4bf), again = F.build(k, L.formParams(k, 'sha256:s1'), 0x2dd4bf);
  const st = stats(g);
  F.setColor(g, 0xf472b6); let colourOk = true; g.traverse(c => { if (c.material && c.material.color.getHex() !== 0xf472b6) colourOk = false; });
  F.setOpacity(g, 0.5); F.setOpacity(g, 0.5); let opOk = true; g.traverse(c => { if (c.material && Math.abs(c.material.opacity - c.material.userData.base * 0.5) > 1e-9) opOk = false; });
  let disposed = 0; const seenG = new Set(), seenM = new Set();
  g.traverse(c => { if (c.geometry && !seenG.has(c.geometry)) { seenG.add(c.geometry); c.geometry.addEventListener('dispose', () => disposed++); }
                    if (c.material && !seenM.has(c.material)) { seenM.add(c.material); c.material.addEventListener('dispose', () => disposed++); } });
  F.dispose(g);
  out.kinds[k] = { ...st, kind: g.userData.kind, variations: sigs.size, same: sig(again) === sig(F.build(k, L.formParams(k, 'sha256:s1'), 0x2dd4bf)),
                   colourOk, opOk, disposedAll: disposed === seenG.size + seenM.size, sig: sig(again) };
}
out.R = F.R;
out.unknown = F.build('nonsense', null, 0x2dd4bf).userData.kind;
console.log(JSON.stringify(out));
"""


def _run(path):
    text = path.read_text(encoding="utf-8")
    life, forms = LIFE.search(text), FORMS.search(text)
    assert life and forms, f"{path.name}: missing <orb-life> or <orb-forms>"
    src = (HARNESS.replace("THREE_PATH", json.dumps(str(THREE)))
           .replace("LIFE", life.group(1)).replace("FORMS", forms.group(1)))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def o(request):
    if not node:
        pytest.skip("node is not installed")
    return _run(request.param)


def test_every_kind_builds_a_real_form_inside_the_orb(o):
    assert set(o["kinds"]) == {"research", "code", "media", "mail", "schedule", "system", "general"}
    for k, s in o["kinds"].items():
        assert s["kind"] == k
        assert s["n"] >= 12 and s["finite"] is True, k
        assert s["lines"] + s["points"] >= 1, k
        assert o["R"] * 0.85 <= s["r"] <= o["R"] * 1.001, (k, s["r"])
    assert o["kinds"]["research"]["points"] == 1                 # a point sphere
    for k, s in o["kinds"].items():
        assert s["lines"] + s["points"] == 1, k                   # one draw per form
    assert o["unknown"] == "general"


def test_the_kinds_look_different_from_each_other(o):
    sigs = [s["sig"] for s in o["kinds"].values()]
    assert len(set(sigs)) == len(sigs)


def test_a_kinds_form_follows_the_genomes_step(o):
    for k, s in o["kinds"].items():
        assert s["same"] is True, k
        assert s["variations"] >= 2, k


def test_colour_opacity_and_disposal(o):
    for k, s in o["kinds"].items():
        assert s["colourOk"] is True and s["opOk"] is True and s["disposedAll"] is True, k
