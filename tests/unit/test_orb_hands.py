"""Hands-on control of the process orbs (avatar-visual-genome.md §16.1), run
under node from both scene files with a small stand-in for the page.

- Voice and the pointer share one path: a desktop action {type:"orb", op,
  target} resolves the phrase where the orbs are and returns what happened.
- A cancel waits out its undo window. Then a running helper is stopped at its
  next step, a task that is not running is cancelled outright, and a process
  with no task is cancelled as a process. An undo inside the window means
  nothing is ever sent.
- An ambiguous phrase never acts: it names the candidates instead.
- Pausing only happens where the server marks a row pausable; anywhere else
  it says at once that it is not possible yet.
- A pop-out asked for outside a click opens the helper's view as a window,
  since the browser blocks a new tab there.
- Opening a helper that needs the owner's OK opens the approval card it is
  waiting on, the same card; any other opens its thread.
- The pointer is read at the window and acts only over the bare scene: a
  press on a control is never taken for an orb, and a throw away from Friday
  over the bare scene schedules a cancel.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]
node = shutil.which("node")
LIFE = re.compile(r"// <orb-life>\n(.*?)// </orb-life>", re.S)
HANDS = re.compile(r"// <orb-hands>\n(.*?)// </orb-hands>", re.S)

HARNESS = r"""
let clock = 1000;
const listeners = {}, events = [], fetches = [], navs = [], opens = [];
const fakeEl = () => ({ style: {}, dataset: {}, children: [], appendChild(c) { this.children.push(c); }, querySelector() { return null; },
  remove() {}, addEventListener() {}, textContent: '', isConnected: true, className: '' });
global.window = { addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
                  dispatchEvent: e => { events.push([e.type, e.detail]); return true; }, open: (u, n) => { opens.push(u); return null; } };
global.innerWidth = 1280; global.innerHeight = 800;
global.document = { documentElement: {}, body: fakeEl(), createElement: fakeEl };
global.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
global.requestAnimationFrame = () => 1;
global.setInterval = () => 1; global.clearInterval = () => {}; global.setTimeout = () => 0; global.clearTimeout = () => {};
Object.defineProperty(global, 'performance', { value: { now: () => clock }, configurable: true });
let answer = () => 200;
global.fetch = (url, opts) => { const method = (opts && opts.method) || 'GET'; fetches.push([method, url]);
  const status = answer(method, url); return Promise.resolve({ status, ok: status < 300, json: async () => ({}) }); };
global.fridayNavigate = t => navs.push(t);
global.fridayWorkspaceTabUrl = (id, p) => '/w/' + id + '?' + new URLSearchParams(p);
global.getComputedStyle = el => el._cs || { backgroundColor: 'rgba(0, 0, 0, 0)', backgroundImage: 'none' };
LIFE
HANDS
const H = FridayOrbHands, out = {};
const orbs = [
  { id: 'agent-a', task_id: 'ta', kind: 'research', hue: 0, label: 'Reading sources', status: 'running', step_n: 2, step_total: 5, started: 1, sx: 1000, sy: 200, sr: 20 },
  { id: 'agent-b', task_id: 'tb', kind: 'general', hue: 1, label: 'Drafting', status: 'running', started: 2, sx: 300, sy: 220, sr: 20 },
  { id: 'image-c', kind: 'media', hue: 2, label: 'Rendering', status: 'running', started: 3, sx: 200, sy: 500, sr: 20 },
  { id: 'agent-d', task_id: 'td', kind: 'code', hue: 0, label: 'Tests', status: 'running', started: 4, pausable: true, sx: 1100, sy: 420, sr: 20 },
  { id: 'agent-e', task_id: 'te', kind: 'mail', hue: 3, label: 'Mail', status: 'running', approval_id: 'ap-9', started: 5, sx: 150, sy: 150, sr: 20 },
];
const marks = [];
H.attach({ orbs: () => orbs, core: () => ({ x: 640, y: 360, r: 120 }), drag() {}, release() {}, hover() {}, mark: (id, s) => marks.push([id, s]) });
const flush = () => new Promise(r => setImmediate(r));
(async () => {
  out.list = H.run({ op: 'list' });
  out.status_green = H.run({ op: 'status', target: "what's the green one doing?" });
  out.cancel_amb = H.run({ op: 'cancel', target: 'cancel the green one' });
  out.none = H.run({ op: 'cancel', target: 'cancel the purple one' });

  // cancel, waited out: stop-after-step; 409 (not running) falls back to DELETE
  answer = (m, u) => (u.endsWith('/stop-after-step') ? 409 : 200);
  out.cancel = H.run({ op: 'cancel', target: 'cancel the research one' });
  clock += 4900; H._undo.tick(clock); await flush(); out.before = fetches.slice();
  clock += 200; H._undo.tick(clock); await flush(); await flush(); out.after = fetches.slice();
  // cancel, then undo inside the window: nothing sent
  fetches.length = 0;
  H.run({ op: 'cancel', target: 'the pink helper' }); clock += 2000; H._undo.tick(clock);
  out.undo = H.run({ op: 'undo', target: 'it' }); clock += 9000; H._undo.tick(clock); await flush();
  out.undone_fetches = fetches.slice();
  // a process with no task
  H.run({ op: 'cancel', target: 'the media one' }); clock += 5100; H._undo.tick(clock); await flush();
  out.process_cancel = fetches.slice();
  // pause: not pausable, then pausable
  fetches.length = 0;
  out.pause_no = H.run({ op: 'pause', target: 'pause the pink one' }); await flush();
  out.pause_no_fetch = fetches.length;
  out.pause_yes = H.run({ op: 'pause', target: 'pause the code one' }); await flush();
  out.pause_yes_fetch = fetches.slice();
  // pop-out by voice: a window, not a tab
  out.pop = H.run({ op: 'popout', target: 'pop the research one out' }); out.navs = navs.slice(); out.opens = opens.slice();
  // open: the approval card for a waiting helper, the thread for any other
  events.length = 0;
  H.run({ op: 'open', target: 'the mail one' }); H.run({ op: 'open', target: 'the research one' });
  out.open = events.map(e => [e[0], e[1] && (e[1].approval_id || e[1].id)]);
  // the pointer: a press on a control is not an orb; a throw over the bare scene cancels
  fetches.length = 0; marks.length = 0;
  const backdrop = { closest: () => null, getBoundingClientRect: () => ({ width: 1280, height: 720 }) };
  const button = { closest: () => ({}), getBoundingClientRect: () => ({ width: 1280, height: 720 }) };
  const fire = (t, e) => (listeners[t] || []).forEach(f => f(Object.assign({ button: 0, preventDefault() {}, stopPropagation() {} }, e)));
  events.length = 0;
  fire('pointerdown', { target: button, clientX: 300, clientY: 220 });
  clock += 80; fire('pointerup', { target: button, clientX: 300, clientY: 220 });
  out.on_control = events.length + H._undo.keys().length;
  clock = 100000;
  fire('pointerdown', { target: backdrop, clientX: 300, clientY: 220 });
  clock += 40; fire('pointermove', { target: backdrop, clientX: 270, clientY: 215 });
  clock += 40; fire('pointermove', { target: backdrop, clientX: 200, clientY: 205 });
  clock += 40; fire('pointerup', { target: backdrop, clientX: 110, clientY: 190 });
  out.thrown = { pending: H._undo.keys(), marks: marks.slice() };
  // the same throw toward Friday is not a cancel
  H._undo.undo('agent-b'); clock = 200000;
  fire('pointerdown', { target: backdrop, clientX: 300, clientY: 220 });
  clock += 40; fire('pointermove', { target: backdrop, clientX: 340, clientY: 240 });
  clock += 40; fire('pointermove', { target: backdrop, clientX: 420, clientY: 280 });
  clock += 40; fire('pointerup', { target: backdrop, clientX: 520, clientY: 320 });
  out.toward = H._undo.keys();
  out.lines = [H.statusLine(orbs[0]), H.statusLine(orbs[4]), H.statusLine({ kind: 'general', hue: 2, status: 'failed', label: 'boom' }),
               H.statusLine({ kind: 'media', hue: 2, status: 'queued' })];
  console.log(JSON.stringify(out));
})();
"""


def _run(path):
    text = path.read_text(encoding="utf-8")
    life, hands = LIFE.search(text), HANDS.search(text)
    assert life and hands, f"{path.name}: missing <orb-life> or <orb-hands>"
    src = HARNESS.replace("LIFE", life.group(1)).replace("HANDS", hands.group(1))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def o(request):
    if not node:
        pytest.skip("node is not installed")
    return _run(request.param)


def test_voice_resolves_where_the_orbs_are(o):
    assert [h["name"] for h in o["list"]["helpers"]] == [
        "the teal research helper", "the pink helper", "the blue media helper", "the teal code helper", "the sand mail helper"]
    st = o["status_green"]
    assert st["ok"] is True and [r["id"] for r in st["results"]] == ["agent-a", "agent-d"]     # both teal ones answer
    assert o["cancel_amb"]["ok"] is False and o["cancel_amb"]["ambiguous"] is True
    assert o["cancel_amb"]["candidates"] == ["the teal research helper", "the teal code helper"]
    assert o["none"]["ok"] is False and o["none"]["reason"] == "no helper matches"


def test_a_cancel_waits_out_its_undo_window_then_stops_the_helper(o):
    assert o["cancel"]["ok"] is True and o["cancel"]["results"][0]["pending"] is True
    assert o["before"] == []
    assert o["after"] == [["POST", "/api/tasks/ta/stop-after-step"], ["DELETE", "/api/tasks/ta"]]


def test_an_undo_inside_the_window_sends_nothing(o):
    assert o["undo"]["ok"] is True
    assert o["undone_fetches"] == []


def test_a_process_without_a_task_is_cancelled_as_a_process(o):
    assert o["process_cancel"] == [["POST", "/api/processes/image-c/cancel"]]


def test_pausing_is_honest(o):
    assert o["pause_no"]["ok"] is False and "not possible yet" in o["pause_no"]["results"][0]["reason"]
    assert o["pause_no_fetch"] == 0
    assert o["pause_yes"]["ok"] is True and o["pause_yes_fetch"] == [["POST", "/api/tasks/td/pause"]]


def test_a_popout_by_voice_opens_a_window(o):
    assert o["pop"]["results"][0]["where"] == "window"
    assert o["navs"] == [{"workspace": "system", "tab": "task", "task": "ta"}]
    assert o["opens"] == []                      # no blocked-popup bar: it never tries


def test_opening_a_waiting_helper_opens_its_approval_card(o):
    assert o["open"] == [["friday:approval-focus", "ap-9"], ["fridayOrbClicked", "agent-a"]]


def test_the_pointer_acts_only_over_the_bare_scene(o):
    assert o["on_control"] == 0
    assert o["thrown"]["pending"] == ["agent-b"] and ["agent-b", "cancelling"] in o["thrown"]["marks"]
    assert o["toward"] == []


def test_status_lines_come_from_the_servers_fields(o):
    assert o["lines"] == [
        "The teal research helper is working on step 2 of 5: Reading sources",
        "The sand mail helper needs your OK.",
        "The blue helper failed: boom",
        "The blue media helper is waiting its turn.",
    ]
