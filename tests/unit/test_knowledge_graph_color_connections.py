"""The graph retains visible topology and distinct stable record colors."""
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize("source", ["index.html", "ui_parts/app.html"])
def test_color_and_connection_recovery(source, tmp_path):
    text = (ROOT / source).read_text(encoding="utf-8")
    helpers = text[text.index("function kgOverviewPositions("):text.index("function KnowledgeGraphWS(")]
    check = r"""
const assert=require('node:assert/strict');
// Uniform sampling used to hide some otherwise connected leaves entirely.
const star=[];for(let i=1;i<=2000;i++)star.push(0,i);
const eligible=Array.from({length:2000},(_,i)=>i*2);
const balanced=kgVisibleEdgePairs(star,null,eligible,1200);
assert.equal(balanced.length,star.length,'Every connected leaf keeps its link');
assert.equal(kgVisibleEdgePairs(star,0,eligible,600).length,star.length,'Focus shows every selected connection');
assert.equal(kgVisibleEdgePairs(star,null,eligible,1200,'all').length,star.length);
const pairs=[0,1,1,2,0,2,2,3,3,4,2,4,5,6];
const edges=Array.from({length:pairs.length/2},(_,i)=>2*i);
const result=kgVisibleEdgePairs(pairs,null,edges,1);
const neighbors=new Map();for(let i=0;i<result.length;i+=2){const a=pairs[result[i]],b=pairs[result[i+1]];if(!neighbors.has(a))neighbors.set(a,[]);if(!neighbors.has(b))neighbors.set(b,[]);neighbors.get(a).push(b);neighbors.get(b).push(a)}
const reached=new Set([0]),todo=[0];while(todo.length){for(const next of neighbors.get(todo.pop())||[])if(!reached.has(next)){reached.add(next);todo.push(next)}}
assert.deepEqual([...reached].sort(),[0,1,2,3,4],'Bridge edges preserve connected components');
assert.equal(new Set(result).size,result.length);
const kinds=['person','org','publication','social','project','photograph'];
const hues=kinds.map(k=>kgNodeHue({id:'page:entities/'+k+'-example',type:'page',section:'entities'},0));
assert(new Set(hues.map(h=>Math.round(h*10))).size>=5,'Distinct categories do not collapse to one cyan');
for(const k of kinds){const e={id:'page:entities/'+k+'-example',type:'page'};assert.equal(kgNodeHue(e,0),kgNodeHue({...e},9))}
assert(kgEdgeOpacity(4000,false,'balanced')>=0.12,'Normal overview links remain visible');
assert(kgEdgeOpacity(40000,false,'all')<kgEdgeOpacity(4000,false,'balanced'),'Full network adapts opacity to density');
"""
    script = tmp_path / "graph-colors.cjs"
    script.write_text(helpers + check, encoding="utf-8")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
