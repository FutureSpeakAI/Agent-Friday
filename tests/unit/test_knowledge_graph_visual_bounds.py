"""Graph framing must remain bounded across archive size and pane shape."""
from pathlib import Path
import os
import subprocess

import pytest

ROOT = Path(os.environ.get("FRIDAY_GRAPH_TEST_SOURCE_ROOT") or Path(__file__).resolve().parents[2])


@pytest.mark.parametrize("source", ["index.html", "ui_parts/app.html"])
def test_visual_bounds_and_controls(source, tmp_path):
    text = (ROOT / source).read_text(encoding="utf-8")
    begin = text.index("function kgOverviewPositions(")
    end = text.index("function KnowledgeGraphWS(", begin)
    helpers = text[begin:end]
    check = r"""
const assert = require('node:assert/strict');
const entities=Array.from({length:2000},(_,i)=>({id:'node-'+i,community:i<1800?'main':'other',degree:i,x:i===0?1e8:0,y:0,z:0}));
const original=JSON.stringify(entities), overview=kgOverviewPositions(entities), shuffled=kgOverviewPositions(entities.slice().reverse());
assert.equal(JSON.stringify(entities),original,'Display normalization never mutates saved evidence');
assert.equal(overview.size,entities.length);
for(const e of entities){assert.deepEqual(overview.get(e.id),shuffled.get(e.id));assert(Math.hypot(...overview.get(e.id))<240)}
assert.equal(new Set([...overview.values()].map(p=>p.join(','))).size,entities.length);
const pairs=[0,1,1,2,2,3,0,3];
assert.deepEqual(kgVisibleEdgePairs(pairs,0,[0,2,4,6]),[0,1,6,7],'Focus excludes unrelated geometry, not black paint');
assert.deepEqual(kgVisibleEdgePairs(pairs,null,[2,6]),[2,3,6,7],'Star eligibility is preserved');
assert.deepEqual(kgVisibleEdgePairs(pairs,9,[0,2,4,6]),[]);
const many=Array.from({length:20000},(_,i)=>i), eligible=Array.from({length:10000},(_,i)=>i*2);
const sampled=kgVisibleEdgePairs(many,null,eligible);
assert.equal(sampled.length,20000);assert.equal(new Set(sampled).size,20000);
assert.deepEqual(sampled,kgVisibleEdgePairs(many,null,eligible));
const points = [[-120,-80,-100],[120,80,100],[0,0,0]];
for (const aspect of [0.35,0.8,1.5,3]) {
  const frame = kgSceneFrame(points,aspect);
  const angle = Math.min(58*Math.PI/360,Math.atan(Math.tan(58*Math.PI/360)*aspect));
  assert(frame.distance*Math.sin(angle)>frame.radius, 'Every pane fits with padding');
  assert(frame.center.every(Number.isFinite));
}
assert(kgSceneFrame(points,0.35).distance>kgSceneFrame(points,2).distance);
assert(kgSceneFrame([],1).distance>0);
assert(kgSceneFrame([[NaN,0,0]],1).center.every(Number.isFinite));
for(const count of [1,20,2000,10000]) {
  const coords=Array.from({length:count},(_,i)=>kgSectionPoint(i,count,0,1));
  for(let axis=0;axis<3;axis++) {
    const values=coords.map(p=>p[axis]);
    assert(Math.max(...values)-Math.min(...values)<=100.001, 'No count-proportional tower');
  }
  assert(coords.every(p=>p.every(Number.isFinite)));
  assert.equal(new Set(coords.map(p=>p.join(','))).size,count);
}
"""
    path = tmp_path / "graph-bounds.cjs"
    path.write_text(helpers + check, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Fit graph to view" in text
