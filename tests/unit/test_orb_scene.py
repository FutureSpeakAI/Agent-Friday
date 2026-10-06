"""What an orb shows (avatar-visual-genome.md §16.2, §16.3), run under node
with the vendored three.js, the page's own ProcessOrbManager and a small
stand-in for the DOM, from both scene files.

- An orb wears its kind's form in its identity hue; the plain sphere is gone.
- A label is built from text, never parsed as markup.
- Real progress closes the orbit on Friday; unknown progress never moves it.
- A finished orb drifts without spinning until five minutes pass or Clear.
- A failed orb stays dim and marked until its deadline or dismissal; opening
  its fix acknowledges it.
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
Date.now = () => 1000000 + Math.round(clock * 1000);
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
mgr.addOrb({ id: 'helper-a', label: '<img src=x onerror=alert(1)>', category: 'default', icon: '✦', task_id: 'ta', model: 'provider/model-a' });
const oa = mgr.orbs.get('helper-a');
out.form = { sphereHidden: oa.sphere.visible === false, hasForm: oa.group.children.includes(S('helper-a').form), kind: S('helper-a').kind };
out.label = { html: oa.labelEl.innerHTML, text: oa.labelEl.textContent, word: oa.labelEl.textContent.includes('teal'), runner: oa.labelEl.textContent.includes('model-a') };

// progress: real closes in, unknown does not
mgr.addOrb({ id: 'helper-b', label: 'B', category: 'default', task_id: 'tb' });
mgr.addOrb({ id: 'helper-c', label: 'C', category: 'default', task_id: 'tc' });
const r0b = mgr.orbs.get('helper-b').oRadius, r0c = mgr.orbs.get('helper-c').oRadius;
mgr.updateOrb('helper-b', { progress: 0.5 });
run(8);
out.progress = { known: +(r0b - mgr.orbs.get('helper-b').oRadius).toFixed(2), unknown: +(r0c - mgr.orbs.get('helper-c').oRadius).toFixed(3),
                 expect: +((r0b - 6.5) * 0.5).toFixed(2) };

// Completion keeps the radius, scale and slow orbital drift, but stops self-spin.
mgr.updateOrb('helper-b', { status: 'completed', progress: 1 });
const finished = mgr.orbs.get('helper-b'), finishRadius = finished.oRadius,
      finishSpin = S('helper-b').form.rotation.y, finishAngle = finished.oAngle;
run(2);
const mid = mgr.orbs.get('helper-b');
out.completed = { stays: !!mid && !mid.dying,
    radius: !!mid && Math.abs(mid.oRadius - finishRadius) < 1e-9,
    scale: !!mid && Math.abs(mid.group.scale.x - 1) < 1e-9,
    spin: !!mid && S('helper-b').form.rotation.y === finishSpin,
    drift: !!mid && Math.abs(mid.oAngle - finishAngle - 0.24) < 1e-9 };
clock += 297; mgr.update(0, clock);
mgr.updateOrb('helper-b', { status: 'completed', progress: 1 });
out.completed.beforeDeadline = !!mgr.orbs.get('helper-b');
clock += 1.001; mgr.update(0, clock);
out.completed.expired = !mgr.orbs.get('helper-b');

// Clear is immediate for both completed and failed orbs, without an exit spin.
out.cleared = [];
for (const status of ['completed', 'error']) {
    mgr.addOrb({ id: 'clear-' + status, label: 'Cleared', category: 'default' });
    mgr.updateOrb('clear-' + status, { status });
    const o = mgr.orbs.get('clear-' + status);
    mgr.removeOrb(o.id);
    out.cleared.push(!mgr.orbs.has(o.id) && !o.labelEl.isConnected && !mgr.scene.children.includes(o.group));
    mgr._cleanup(o.id); // isolate the next assertion even against the old exit animation
}

// failed: stays until looked at
mgr.updateOrb('helper-c', { status: 'error' }); run(5);
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

// Friday's outline follows the visible structure: a big one widens it, a small one narrows it
global.structures = {};
const big = new THREE.Group(); big.add(new THREE.Mesh(new THREE.SphereGeometry(7, 8, 6), new THREE.MeshBasicMaterial()));
const small = new THREE.Group(); small.add(new THREE.Mesh(new THREE.BoxGeometry(3, 3, 3), new THREE.MeshBasicMaterial())); small.visible = false;
structures.BIG = big; structures.SMALL = small;
run(2.2); const rBig = FridayOrbScene._coreR();
big.visible = false; small.visible = true; run(2.2); const rSmall = FridayOrbScene._coreR();
out.core = { big: +rBig.toFixed(2), small: +rSmall.toFixed(2) };
big.visible = true; small.visible = false; run(2.2);

// behind Friday: the label fades and the hand cannot take it; in front, both come back
mgr.addOrb({ id: 'back-1', label: 'Back', category: 'default' });
const ob = mgr.orbs.get('back-1'); ob.oSpeed = 0; ob.oAngle = -Math.PI / 2; ob.oHeight = 0; ob.oRadius = 10;
ob.group.position.set(0, 0, -10);
run(0.2);
const behindNow = { behind: FridayOrbScene._state('back-1').behind, faded: ob.labelEl.style.opacity === '0',
                    target: FridayOrbHands.run({ op: 'list' }).helpers.some(h => h.name.includes(FridayOrbLife.hueWord(FridayOrbScene._state('back-1').hue)) && h.line.includes('Back')) };
ob.oAngle = Math.PI / 2; ob.group.position.set(0, 0, 10); run(0.2);
out.behind = { ...behindNow, frontBack: FridayOrbScene._state('back-1').behind, frontShown: ob.labelEl.style.opacity === '' };
mgr.updateOrb('back-1', { status: 'completed' }); mgr.removeOrb('back-1'); run(3);

// labels never sit on each other: two orbs at the same place on screen
mgr.addOrb({ id: 'twin-1', label: 'Twin one', category: 'default' }); mgr.addOrb({ id: 'twin-2', label: 'Twin two', category: 'default' });
// Both occupy the same visible world position: screen coordinates alone do
// not make an orb visible when its real orbit is behind Friday.
for (const id of ['twin-1', 'twin-2']) {
    const o = mgr.orbs.get(id);
    o.oSpeed = 0; o.oAngle = Math.PI / 2; o.oHeight = 0; o.oRadius = S(id).baseR = 10;
    o.group.position.set(0, 0, 10);
}
run(0.5);
const lab = FridayOrbScene._labels().filter(l => l.id.startsWith('twin'));
out.labels = { two: lab.length, apart: lab.length === 2 && Math.abs(lab[0].y - lab[1].y) >= 15,
    front: ['twin-1', 'twin-2'].every(id => !S(id).behind),
    overlapping: Math.hypot(S('twin-1').sx - S('twin-2').sx, S('twin-1').sy - S('twin-2').sy) < 1e-9 };
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

// The swarm is excluded even when every ordinary helper is in view.
const ordinary = ['helper-a', 'helper-d', 'helper-p', 'helper-k'];
ordinary.forEach((id, i) => {
    const o = mgr.orbs.get(id);
    o.oSpeed = 0; o.oAngle = Math.PI / 2 + (i - 1.5) * 0.2;
    o.oHeight = 0; o.oRadius = S(id).baseR = 10;
    o.group.position.set(Math.cos(o.oAngle) * 10, 0, Math.sin(o.oAngle) * 10);
});
run(0.1);
const normalTargets = FridayOrbHands.run({ op: 'list' }).helpers.length;
mgr.addOrb({ id: 'helper-swarm', label: '+3 helpers', category: 'default', count: 3 });
out.swarm = { dressed: !!S('helper-swarm'), target: FridayOrbHands.run({ op: 'list' }).helpers.length,
    normalTargets, front: ordinary.every(id => !S(id).behind) };
// A reload uses the server end time, not a fresh five-minute timer.
for (const id of [...mgr.orbs.keys()]) mgr._cleanup(id);
mgr.addOrb({ id: 'restored', label: 'Restored', category: 'default', name: 'Local helper' });
mgr.updateOrb('restored', { status: 'completed', ended: Date.now() / 1000 - 299 });
out.restored = { runner: mgr.orbs.get('restored').labelEl.textContent.includes('Local helper') };
clock += 1.001; mgr.update(0, clock);
out.restored.expired = !mgr.orbs.has('restored');
mgr._cleanup('restored');

// An in-flight response cannot resurrect a cleared ID; a new run can reuse it.
mgr.addOrb({ id: 'late', label: 'Late', category: 'default' });
mgr.updateOrb('late', { status: 'completed' }); mgr.removeOrb('late');
mgr.addOrb({ id: 'late', label: 'Late', category: 'default', started: Date.now() / 1000 - 20 });
out.latePoll = { hidden: !mgr.orbs.has('late') };
mgr.addOrb({ id: 'late', label: 'New run', category: 'default', started: Date.now() / 1000 + 1 });
out.latePoll.newRun = !!mgr.orbs.get('late') && mgr.orbs.get('late').label === 'New run';
// Every terminal status expires and stops spinning while its status remains intact.
mgr._cleanup('late');
out.outcomes = [];
for (const status of ['completed', 'error', 'failed', 'timeout', 'cancelled', 'interrupted']) {
    const id = 'outcome-' + status;
    mgr.addOrb({ id, label: status, category: 'default' });
    mgr.updateOrb(id, { status, ended: Date.now() / 1000 - 299 });
    const spin = S(id).form.rotation.y;
    run(0.1);
    const o = mgr.orbs.get(id);
    const state = { status, retained: !!o && o.status === status, still: S(id).form.rotation.y === spin };
    clock += 1; mgr.update(0, clock);
    state.expired = !mgr.orbs.has(id);
    out.outcomes.push(state);
    mgr._cleanup(id);
}
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
    assert "<img" not in o["label"]["html"] and "<img" in o["label"]["text"] and o["label"]["word"] is False
    assert o["label"]["runner"] is True


def test_real_progress_closes_the_orbit_and_unknown_does_not(o):
    p = o["progress"]
    assert p["unknown"] == 0
    assert abs(p["known"] - p["expect"]) < 0.25


def test_completion_keeps_a_calm_orbit_for_five_minutes_without_spinning(o):
    assert o["completed"] == {"stays": True, "radius": True, "scale": True,
                              "spin": True, "drift": True,
                              "beforeDeadline": True, "expired": True}


def test_clear_removes_completed_and_failed_orbs_without_an_exit_animation(o):
    assert o["cleared"] == [True, True]


def test_reloading_keeps_the_original_deadline_and_shows_the_runner(o):
    assert o["restored"] == {"runner": True, "expired": True}


def test_a_late_poll_cannot_recreate_a_cleared_orb_but_a_new_run_can(o):
    assert o["latePoll"] == {"hidden": True, "newRun": True}


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


def test_fridays_outline_follows_the_visible_structure(o):
    assert 6.9 <= o["core"]["big"] <= 7.1          # the sphere of radius 7
    assert 2.5 <= o["core"]["small"] <= 2.7        # the cube's corner, 2.6 out


def test_an_orb_behind_friday_fades_and_cannot_be_taken(o):
    assert o["behind"] == {"behind": True, "faded": True, "target": False, "frontBack": False, "frontShown": True}


def test_labels_never_sit_on_each_other(o):
    assert o["labels"] == {"two": 2, "apart": True, "front": True, "overlapping": True}


def test_the_swarm_orb_is_left_alone(o):
    assert o["swarm"] == {"dressed": False, "target": 4, "normalTargets": 4, "front": True}


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


def test_every_terminal_outcome_stops_spinning_and_expires_without_changing_status(o):
    assert o["outcomes"] == [
        {"status": status, "retained": True, "still": True, "expired": True}
        for status in ["completed", "error", "failed", "timeout", "cancelled", "interrupted"]
    ]
