"""What an orb shows (avatar-visual-genome.md §16.2, §16.3), run under node
with the vendored three.js, the page's own ProcessOrbManager and a small
stand-in for the DOM, from both scene files.

- An orb wears its kind's form in its identity hue; the plain sphere is gone.
- A label is built from text, never parsed as markup.
- Real progress closes the orbit on Friday; unknown progress never moves it.
- A finished orb spirals into Friday before the layer lets it go.
- A failed orb stays, dim and marked, until it is looked at; opening it is
  looking at it.
- An approval card linked to a helper's task turns its orb amber and says
  it needs you.
- An orb that drifts over the prompt row or the dock is lifted clear of it.
- Cloud work is a paler tint with an outer ring; local work is not.
- A tool call is a spark, rate-limited, and none at all under reduced
  motion.
- A helper's own helper is its moon, close by.
- The swarm orb is left as the orb layer draws it, and is not a hand target.
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
BLOCKS = ["orb-life", "orb-forms", "orb-sky", "orb-hands", "orb-scene"]
MANAGER = re.compile(r"(        class ProcessOrbManager \{.*?\n        \}\n)", re.S)

HARNESS = r"""
const THREE = require(THREE_PATH);
let clock = 0, reduced = false;
Object.defineProperty(global, 'performance', { value: { now: () => clock * 1000 }, configurable: true });
const mkEl = () => {
  const el = { style: {}, dataset: {}, children: [], className: '', _text: '', _html: '', isConnected: true, offsetWidth: 80, offsetHeight: 18,
    appendChild(c) { this.children.push(c); c.parent = this; return c; }, remove() { this.isConnected = false; },
    addEventListener() {}, querySelector(sel) { const cls = sel.replace(/^\./, ''); const find = n => { for (const c of n.children) { if ((' ' + c.className + ' ').includes(' ' + cls + ' ')) return c; const r = find(c); if (r) return r; } return null; }; return find(this); },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1280, height: 800 }) };
  Object.defineProperty(el, 'textContent', { get() { return this._text + this.children.map(c => c.textContent).join(''); },
    set(v) { this._text = String(v); this.children = []; this._html = ''; } });
  Object.defineProperty(el, 'innerHTML', { get() { return this._html; }, set(v) { this._html = String(v); this.children = []; this._text = ''; } });
  return el;
};
global.window = { innerWidth: 1280, innerHeight: 800, addEventListener() {}, dispatchEvent() { return true; }, open: () => null };
global.innerWidth = 1280; global.innerHeight = 800;
global.document = { body: mkEl(), documentElement: {}, createElement: mkEl };
global.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
global.requestAnimationFrame = () => 1; global.setInterval = () => 1; global.clearInterval = () => {};
global.setTimeout = () => 0; global.clearTimeout = () => {};
global.fetch = () => Promise.resolve({ ok: false, status: 404, json: async () => null });
global.getComputedStyle = () => ({ backgroundColor: 'rgba(0, 0, 0, 0)', backgroundImage: 'none' });
global.glowTexture = null; global.holoAmplitude = 0;
global.SceneMotion = { reduced: () => reduced };
global.FridayGenome = { current: () => ({ step: { content_hash: 'sha256:test' } }) };
const navs = []; global.fridayNavigate = t => navs.push(t);
let approvalFn = null; window.fridayApprovalFeed = { subscribe: fn => { approvalFn = fn; fn([]); } };
BLOCKS
MANAGER
const camera = new THREE.PerspectiveCamera(60, 1280 / 800, 0.1, 200);
camera.position.set(0, 5, 18); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
const renderer = { domElement: mkEl() };
const mgr = new ProcessOrbManager(new THREE.Scene(), camera, renderer);
FridayOrbScene.install(mgr, camera);
const run = sec => { for (let i = 0; i < Math.round(sec * 60); i++) { clock += 1 / 60; mgr.update(1 / 60, clock); } };
const out = {};
const S = id => FridayOrbScene._state(id);

// the form and the label
mgr.addOrb({ id: 'helper-a', label: '<img src=x onerror=alert(1)>', category: 'default', icon: '✦', task_id: 'ta' });
const oa = mgr.orbs.get('helper-a');
out.form = { sphereHidden: oa.sphere.visible === false, hasForm: oa.group.children.includes(S('helper-a').form), kind: S('helper-a').kind };
out.label = { html: oa.labelEl.innerHTML, text: oa.labelEl.textContent, word: oa.labelEl.textContent.includes('teal') };

// progress: real closes in, unknown does not
mgr.addOrb({ id: 'helper-b', label: 'B', category: 'default', task_id: 'tb' });
mgr.addOrb({ id: 'helper-c', label: 'C', category: 'default', task_id: 'tc' });
const r0b = mgr.orbs.get('helper-b').oRadius, r0c = mgr.orbs.get('helper-c').oRadius;
mgr.updateOrb('helper-b', { progress: 0.5 });
run(8);
out.progress = { known: +(r0b - mgr.orbs.get('helper-b').oRadius).toFixed(2), unknown: +(r0c - mgr.orbs.get('helper-c').oRadius).toFixed(3),
                 expect: +((r0b - 6.5) * 0.5).toFixed(2) };

// done: the spiral before the layer lets go
mgr.updateOrb('helper-b', { status: 'completed' }); mgr.removeOrb('helper-b');
const r1 = Math.hypot(mgr.orbs.get('helper-b').group.position.x, mgr.orbs.get('helper-b').group.position.z);
run(0.8);
const mid = mgr.orbs.get('helper-b');
out.spiral = { notDyingYet: mid && !mid.dying, closer: mid && Math.hypot(mid.group.position.x, mid.group.position.z) < r1 };
run(1.0); out.spiral.dyingAfter = !mgr.orbs.get('helper-b') || mgr.orbs.get('helper-b').dying;
run(1.0); out.spiral.goneAfter = !mgr.orbs.get('helper-b');

// failed: stays until looked at
mgr.updateOrb('helper-c', { status: 'error' }); mgr.removeOrb('helper-c'); run(5);
const oc = mgr.orbs.get('helper-c'), sc = S('helper-c');
out.failed = { stays: !!oc && !oc.dying, failRing: !!sc && sc.failRing.visible, said: !!oc && oc.labelEl.textContent.includes('Failed'),
               listed: FridayOrbHands.run({ op: 'list' }).helpers.some(h => h.line.includes('failed')) };
FridayOrbHands.run({ op: 'open', target: 'the blue one' });
run(1);
out.failed.lookedAt = !mgr.orbs.get('helper-c') || mgr.orbs.get('helper-c').dying;
out.failed.nav = navs.slice(-1)[0];

// an approval for a helper's task
approvalFn([{ approval_id: 'ap1', subject_type: 'task', subject_id: 'ta' }]);
run(0.2);
out.approval = { glow: oa.glow.material.color.getHex() === 0xf59e0b, form: (() => { let c = null; S('helper-a').form.traverse(x => { if (x.material && c === null) c = x.material.color.getHex(); }); return c === 0xf59e0b; })(),
                 said: oa.labelEl.textContent.includes('needs your OK') };
approvalFn([]); run(0.1);
out.approval.cleared = oa.glow.material.color.getHex() !== 0xf59e0b;

// the keep-out: an orb near the prompt row is lifted clear
mgr.addOrb({ id: 'helper-d', label: 'D', category: 'default' });
const od = mgr.orbs.get('helper-d'); od.oAngle = Math.PI / 2; od.oSpeed = 0; od.oHeight = -3; od.group.position.set(0, -3, od.oRadius);
run(0.1);
const sd0 = S('helper-d').sy;
run(12);
out.keep = { startedIn: FridayOrbLife.liftFor(S('helper-d').sx, sd0, 1280, 800) > 0,
             endsClear: FridayOrbLife.liftFor(S('helper-d').sx, S('helper-d').sy, 1280, 800) === 0 };

// cloud work: a paler tint and the ring
FridayOrbScene._tasks.set('ta', { task_id: 'ta', seat_is_local: false });
mgr.updateOrb('helper-a', { label: 'A' });
out.cloud = { ring: S('helper-a').cloudRing.visible, localRing: S('helper-d').cloudRing.visible };

// sparks: rate-limited, none under reduced motion
let sparks = 0;
for (let i = 0; i < 30; i++) { FridayOrbScene.frame({ type: 'presence', state: 'tool', phase: 'start', agent: 'helper-a' }); sparks = Math.max(sparks, S('helper-a').sparks.length); clock += 0.02; }
run(1);
reduced = true; FridayOrbScene.frame({ type: 'presence', state: 'tool', phase: 'start', agent: 'helper-d' }); out.sparks = { max: sparks, reduced: S('helper-d').sparks.length };
reduced = false;
FridayOrbScene.frame({ type: 'presence', state: 'tool', phase: 'start', agent: 'helper-zz' });   // no such orb: nothing

// moons: a helper's own helper stays close to it
FridayOrbScene._tasks.set('tp', { task_id: 'tp', trace_id: 'tr-p' });
FridayOrbScene._tasks.set('tk', { task_id: 'tk', trace_id: 'tr-k', parent_trace_id: 'tr-p' });
mgr.addOrb({ id: 'helper-p', label: 'P', category: 'default', task_id: 'tp' });
mgr.addOrb({ id: 'helper-k', label: 'K', category: 'default', task_id: 'tk' });
run(3);
out.moon = +mgr.orbs.get('helper-k').group.position.distanceTo(mgr.orbs.get('helper-p').group.position).toFixed(2);

// a row that arrives in parts: the kind follows what is known now
mgr.addOrb({ id: 'image-late', label: 'L', category: 'default' });
const k0 = S('image-late') && S('image-late').kind;
mgr.addOrb({ id: 'image-late', label: 'L', category: 'default', research_commission_id: 'rc9' });
out.late = { before: k0, after: S('image-late').kind, oneForm: mgr.orbs.get('image-late').group.children.filter(c => c.isGroup && c.userData.kind).length };
mgr.updateOrb('image-late', { status: 'completed' }); mgr.removeOrb('image-late'); run(3);

// labels never sit on each other: two orbs at the same place on screen
mgr.addOrb({ id: 'twin-1', label: 'Twin one', category: 'default' }); mgr.addOrb({ id: 'twin-2', label: 'Twin two', category: 'default' });
run(0.5);
for (const id of ['twin-1', 'twin-2']) { const o = mgr.orbs.get(id); o.labelEl.style.display = 'block'; FridayOrbScene._state(id).sx = 500; FridayOrbScene._state(id).sy = 300; }
const lab = FridayOrbScene._labels().filter(l => l.id.startsWith('twin'));
out.labels = { two: lab.length, apart: lab.length === 2 && Math.abs(lab[0].y - lab[1].y) >= 15 };
['twin-1', 'twin-2'].forEach(id => { mgr.updateOrb(id, { status: 'completed' }); mgr.removeOrb(id); }); run(3);

// spread, not bunched: orbs added in a row keep apart, at one speed
const spreadIds = ['s1', 's2', 's3', 's4'];   // with the four still here: the layer's cap of eight
spreadIds.forEach(id => mgr.addOrb({ id, label: id, category: 'default' }));
const tier = Object.fromEntries(spreadIds.map(id => [id, mgr.orbs.get(id).oHeight]));   // the tier it was given
run(5);                                                                              // (the keep-out may lift one later)
const ang = spreadIds.map(id => mgr.orbs.get(id).oAngle);
let minGap = Infinity;
for (let i = 0; i < ang.length; i++) for (let j = i + 1; j < ang.length; j++) {
  let d = Math.abs(ang[i] - ang[j]) % (2 * Math.PI); d = Math.min(d, 2 * Math.PI - d);
  if (Math.abs(tier[spreadIds[i]] - tier[spreadIds[j]]) < 0.5) minGap = Math.min(minGap, d); }
out.spread = { minGapDeg: Math.round(minGap * 180 / Math.PI), oneSpeed: new Set(spreadIds.map(id => mgr.orbs.get(id).oSpeed)).size === 1 };
spreadIds.forEach(id => { mgr.updateOrb(id, { status: 'completed' }); mgr.removeOrb(id); }); run(3);

// the swarm orb
mgr.addOrb({ id: 'helper-swarm', label: '+3 helpers', category: 'default', count: 3 });
out.swarm = { dressed: !!S('helper-swarm'), target: FridayOrbHands.run({ op: 'list' }).helpers.length };
console.log(JSON.stringify(out));
"""


def _run(path):
    text = path.read_text(encoding="utf-8")
    blocks = []
    for b in BLOCKS:
        m = re.search(r"// <" + b + r">\n(.*?)// </" + b + r">", text, re.S)
        assert m, f"{path.name}: no <{b}>"
        blocks.append(m.group(1))
    mgr = MANAGER.search(text)
    assert mgr, f"{path.name}: no ProcessOrbManager"
    src = (HARNESS.replace("THREE_PATH", json.dumps(str(THREE))).replace("BLOCKS", "\n".join(blocks))
           .replace("MANAGER", mgr.group(1)))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def o(request):
    if not node:
        pytest.skip("node is not installed")
    return _run(request.param)


def test_an_orb_wears_its_kinds_form(o):
    assert o["form"] == {"sphereHidden": True, "hasForm": True, "kind": "general"}


def test_a_label_is_text_never_markup(o):
    assert "<img" not in o["label"]["html"] and "<img" in o["label"]["text"] and o["label"]["word"] is True


def test_real_progress_closes_the_orbit_and_unknown_does_not(o):
    p = o["progress"]
    assert p["unknown"] == 0
    assert abs(p["known"] - p["expect"]) < 0.25


def test_a_finished_orb_spirals_home_first(o):
    assert o["spiral"] == {"notDyingYet": True, "closer": True, "dyingAfter": True, "goneAfter": True}


def test_a_failure_stays_until_it_is_looked_at(o):
    f = o["failed"]
    assert f["stays"] is True and f["failRing"] is True and f["said"] is True and f["listed"] is True
    assert f["lookedAt"] is True and f["nav"] == {"workspace": "system", "tab": "task", "task": "tc"}


def test_an_approval_turns_its_orb_amber_and_says_so(o):
    assert o["approval"] == {"glow": True, "form": True, "said": True, "cleared": True}


def test_an_orb_is_lifted_clear_of_the_centre_ui(o):
    assert o["keep"] == {"startedIn": True, "endsClear": True}


def test_cloud_work_wears_the_ring(o):
    assert o["cloud"] == {"ring": True, "localRing": False}


def test_sparks_are_limited_and_absent_under_reduced_motion(o):
    assert 1 <= o["sparks"]["max"] <= 3 and o["sparks"]["reduced"] == 0


def test_a_helpers_own_helper_is_its_moon(o):
    assert o["moon"] < 1.5


def test_a_kind_follows_what_is_known_now(o):
    assert o["late"] == {"before": "media", "after": "research", "oneForm": 1}


def test_orbs_spread_out_and_keep_their_spacing(o):
    assert o["spread"]["oneSpeed"] is True
    assert o["spread"]["minGapDeg"] >= 30


def test_labels_never_sit_on_each_other(o):
    assert o["labels"] == {"two": 2, "apart": True}


def test_the_swarm_orb_is_left_alone(o):
    assert o["swarm"]["dressed"] is False
    assert o["swarm"]["target"] == 4     # helper-a, helper-d, helper-p, helper-k


PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_fridays_mood_never_follows_her_helpers(path):
    # Her core's colour is hers: a helper task running, or any orb on the
    # scene, must not turn her to EXECUTING (§16: an orb never drives her form).
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if "sysMood" in ln and "EXECUTING" in ln]
    assert lines, f"{path.name}: the mood decision is gone"
    for ln in lines:
        assert "taskRunning" not in ln and "fridayGetOrbs" not in ln, ln.strip()
