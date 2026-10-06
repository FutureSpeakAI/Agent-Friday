"""Process polling keeps completion deadlines and honors explicit dismissal."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts/styles_and_scene.html"]
PAGES = [ROOT / "index.html", ROOT / "ui_parts/app.html"]
NODE = shutil.which("node")


def _block(path):
    match = re.search(r"// <orb-process-sync>\n(.*?)// </orb-process-sync>", path.read_text(encoding="utf-8"), re.S)
    assert match
    life = re.search(r"// <orb-life>\n(.*?)// </orb-life>", path.read_text(encoding="utf-8"), re.S)
    assert life
    return life.group(1) + "\n" + match.group(1)


def _run(source):
    if not NODE:
        pytest.skip("node is not installed")
    r = subprocess.run([NODE, "-"], input=source, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_poll_retains_finished_orbs_but_removes_hidden_rows(path):
    source = r"""
const orbs = new Map(), removed = [], updates = [];
global.window = {
  fridayGetOrbs: () => [...orbs.values()],
  fridayAddOrb: p => orbs.set(p.id, {...p}),
  fridayUpdateOrb: (id, p) => { updates.push({id, ...p}); if(orbs.has(id)) Object.assign(orbs.get(id), p); },
  fridayRemoveOrb: id => { removed.push(id); orbs.delete(id); },
};
BLOCK
const row = {id:'p',name:'Friday',label:'Read a report',model:'model-a',status:'running',task_id:'task-a',orb_visible:true};
window.fridaySyncOrbs([row]);
const out = { metadata: {label:orbs.get('p').label,model:orbs.get('p').model,name:orbs.get('p').name,task_id:orbs.get('p').task_id,pid:orbs.get('p').pid} };
window.fridaySyncOrbs([{...row,status:'completed',ended:123}]);
out.finished = {kept:orbs.has('p'),ended:orbs.get('p').ended,removed:removed.length};
window.fridaySyncOrbs([]); window.fridaySyncOrbs([]);
out.missing = {kept:orbs.has('p'),ended:orbs.get('p').ended,removed:removed.length};
window.fridaySyncOrbs([{...row,status:'completed',ended:123,orb_visible:false}]);
out.expired = !orbs.has('p');
window.fridaySyncOrbs([{...row,dismissed:true}]);
out.dismissed = !orbs.has('p');
window.fridaySyncOrbs([{...row,id:'short',status:'running'}]);
window.fridaySyncOrbs([]); const afterOne = updates.length;
window.fridaySyncOrbs([]);
out.earlyRemoval = {kept:orbs.has('short'),status:orbs.get('short').status,once:updates.length===afterOne};
for (const status of ['error','failed','timeout','cancelled','interrupted']) {
    window.fridaySyncOrbs([{...row,id:'ended',status,ended:123}]);
    window.fridaySyncOrbs([]);
    if (!out.terminalStatuses) out.terminalStatuses = [];
    out.terminalStatuses.push(orbs.get('ended').status);
}
console.log(JSON.stringify(out));
""".replace("BLOCK", _block(path))
    out = _run(source)
    assert out["metadata"] == {"label": "Read a report", "model": "model-a", "name": "Friday", "task_id": "task-a", "pid": "p"}
    assert out["finished"] == {"kept": True, "ended": 123, "removed": 0}
    assert out["missing"] == out["finished"]
    assert out["expired"] is True and out["dismissed"] is True
    assert out["earlyRemoval"] == {"kept": True, "status": "completed", "once": True}
    assert out["terminalStatuses"] == ["error", "failed", "timeout", "cancelled", "interrupted"]


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_clear_includes_scene_only_completions_and_preserves_pending_work(path):
    text = path.read_text(encoding="utf-8")
    start = re.search(r"const clearOrbs\s*=\s*\(\)\s*=>\s*\{", text)
    assert start
    end = text.index("\n  };", start.start()) + len("\n  };")
    clear = text[start.start():end]
    source = r"""
const removed = [], requests = [];
const orbiting = [{id:'done',status:'completed'},{id:'failed',status:'error'},
                  {id:'live',status:'running'},{id:'queued',status:'queued'},
                  {id:'waiting',status:'awaiting_approval'}];
let after;
global.window = {fridayGetOrbs:()=>[{id:'local-done',status:'completed'},{id:'local-live',status:'running'}],fridayRemoveOrb:id=>removed.push(id)};
BLOCK
const setClearingOrbs = ()=>{}, setFailedProcs = ()=>{}, setOrbiting = fn => {after=fn(orbiting);};
const apiFetch = (url, args) => {requests.push({url,body:JSON.parse(args.body)});return Promise.resolve({json:()=>Promise.resolve({ids:[],orphan_ids:[]})});};
CLEAR
clearOrbs();
console.log(JSON.stringify({removed,kept:after.map(p=>p.id),requests}));
""".replace("BLOCK", _block(ROOT / "index.html")).replace("CLEAR", clear)
    out = _run(source)
    assert set(out["removed"]) == {"done", "failed", "local-done"}
    assert set(out["kept"]) == {"live", "queued", "waiting"}
    assert out["requests"] == [{"url": "/api/orbs/clear", "body": {"scope": "finished"}}]
