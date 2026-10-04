"""The process orbs' own logic (avatar-visual-genome.md §16), run under node
from both scene files: plain rules, no Three.js and no page.

- Every orb has a kind (research, code, media, mail, scheduled, system or a
  plain helper), read from fields the server sets in code, never from a
  model's words; the server's own `kind` wins when it sends one.
- Every orb has a speakable colour, one of the brand's five decoration hues,
  the least used first, so "the teal one" names one orb; colour words, kind
  words, order words and "it" resolve to orbs, and an ambiguous phrase says
  so instead of guessing.
- Hands-on control is a pure state machine: a tap opens, a still hold pauses,
  a fast throw away from Friday cancels, a drop on Friday asks for its status,
  a drop at the screen's edge pops it out, anything else lets it drift back.
- A cancel waits out its undo window before it is sent, and an undo inside
  the window means it is never sent.
- An orb's orbit closes in on Friday only with real progress; unknown
  progress never moves it. A finished orb spirals into the core.
- A receipt carries what the server knows (what it did, model, time, cost)
  and a failed one names a fix; sources only when the server sends them.
- The day's stars sit in the upper band, one fixed place per task.
- Orbs keep out of the centre UI (the prompt row and the dock).
- Sparks are rate-limited well inside the flash limit.
- No identity colour is a status hue, and cloud work is a paler tint of the
  same hue.
- A kind's form parameters follow the genome's step: the same step gives the
  same form, a new step a new one.
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
BLOCK = re.compile(r"// <orb-life>\n(.*?)// </orb-life>", re.S)

HARNESS = r"""
BLOCK
const L = FridayOrbLife, out = {};

// kinds
out.kinds = [
  { id: 'agent-1a2b', name: 'Agent', research_commission_id: 'rc1' },
  { id: 'sched-9', name: 'Scheduler' },
  { id: 'image-77', name: 'Image', category: 'creative' },
  { id: 'p1', name: 'Podcast' },
  { id: 'p2', name: 'Creating a poster' },
  { id: 'p3', name: 'Mail', category: 'communication' },
  { id: 'p4', name: 'Pulling Model' },
  { id: 'p5', name: 'Self-Improvement' },
  { id: 'agent-3c', name: 'Agent' },
  { id: 'agent-4d', name: 'Agent', kind: 'code' },
  { id: 'agent-5e', name: 'Research the moon', },
].map(L.kindOf);

// names: hues spread, least used first
const orbs = [];
const add = (id, kind, started) => { const o = { id, kind, started }; o.hue = L.pickHue(orbs); orbs.push(o); return o; };
add('a', 'research', 1); add('b', 'general', 2); add('c', 'media', 3); add('d', 'general', 4); add('e', 'mail', 5); add('f', 'code', 6);
out.hues = orbs.map(o => o.hue);
out.words = orbs.map(o => L.hueWord(o.hue));
out.speak = L.speakable(orbs[0]);
const R = (text, focus) => L.resolve(text, orbs, focus || null);
out.r_green = R("what's the green one doing?");
out.r_research = R('cancel the research one');
out.r_helper = R('pause the helper');
out.r_it = R('pop it out', 'c');
out.r_all = R('cancel all of them');
out.r_newest = R('what is the newest one doing');
out.r_none = R('what time is it');
out.r_teal_media = R('the teal media one');

// gestures
const ctx = { coreX: 640, coreY: 360, coreR: 120, width: 1280, height: 800 };
const G = L.gestures();
const run = (steps) => { const ev = []; for (const s of steps) { const r = s[0] === 'down' ? G.down(s[1], s[2], s[3], s[4])
  : s[0] === 'move' ? G.move(s[1], s[2], s[3]) : s[0] === 'tick' ? G.tick(s[1]) : G.up(s[1], s[2], s[3], ctx); if (r) ev.push(r.op); } return ev; };
out.g_tap = run([['down', 'a', 1000, 200, 0], ['up', 1002, 201, 120]]);
out.g_hold = run([['down', 'a', 1000, 200, 0], ['tick', 300], ['tick', 650], ['up', 1001, 200, 900]]);
out.g_fling = run([['down', 'a', 1000, 200, 0], ['move', 1030, 190, 40], ['move', 1100, 170, 80], ['up', 1180, 150, 120]]);
out.g_fling_in = run([['down', 'a', 1000, 200, 0], ['move', 950, 220, 40], ['move', 860, 250, 80], ['up', 760, 290, 120]]);
out.g_status = run([['down', 'a', 1000, 200, 0], ['move', 900, 260, 200], ['move', 700, 340, 600], ['move', 660, 355, 900], ['up', 655, 356, 1000]]);
out.g_popout = run([['down', 'a', 1000, 200, 0], ['move', 1100, 160, 300], ['move', 1240, 120, 700], ['move', 1262, 118, 900], ['up', 1263, 118, 1000]]);
out.g_drop = run([['down', 'a', 1000, 200, 0], ['move', 950, 230, 300], ['move', 900, 250, 700], ['up', 899, 250, 1000]]);
out.g_drag_moves = run([['down', 'a', 1000, 200, 0], ['move', 1030, 220, 40]]); G.cancel();
out.g_hold_after_drag = run([['down', 'a', 1000, 200, 0], ['move', 1060, 240, 50], ['tick', 900], ['up', 1061, 240, 1000]]);

// undo
const U = L.undoQueue(5000); let sent = 0;
U.schedule('a', () => sent++, 0); U.tick(4000); const before = sent; U.undo('a'); U.tick(6000);
out.undo_kept = { before, after: sent };
U.schedule('b', () => sent++, 10000); U.tick(14999); const mid = sent; U.tick(15000); U.tick(20000);
out.undo_sent = { mid, after: sent };

// progress and orbit
out.prog = [L.progressOf({}), L.progressOf({ progress: 0.5 }), L.progressOf({ step_n: 3, step_total: 4 }), L.progressOf({ progress: 50 }),
            L.progressOf({ progress: null, step_n: 2 })];
out.radius = [L.orbitRadius(null, 11, 6), L.orbitRadius(0, 11, 6), L.orbitRadius(0.5, 11, 6), L.orbitRadius(1, 11, 6)];
const sp = []; for (let t = 0; t <= L.SPIRAL_S + 0.2; t += 0.1) sp.push(L.spiralAt(t, 11, 0));
out.spiral = { mono: sp.every((s, i) => i === 0 || s.r <= sp[i - 1].r + 1e-9), end: sp[sp.length - 1].r, done: sp[sp.length - 1].done, startDone: sp[0].done };

// receipts
out.rc_ok = L.receipt({ task_id: 't1', name: 'Research the moon', status: 'completed', result: 'Found the four phases.', started: 100, ended: 160 },
                      { model: 'model-x', cost_usd: 0.0123 });
out.rc_fail = L.receipt({ task_id: 't2', name: 'Write a report', status: 'failed', resumable: true, step_n: 3 }, null);
out.rc_fail2 = L.receipt({ task_id: 't3', name: 'Fetch', status: 'error' }, {});
out.rc_src = L.receipt({ task_id: 't4', status: 'completed' }, { sources: [{ title: 'A' }] });

// stars
const W = 1280, H = 800, st = ['t1', 't2', 't3', 't1'].map(id => L.starAt(id, W, H));
out.stars = { same: st[0].x === st[3].x && st[0].y === st[3].y, distinct: st[0].x !== st[1].x || st[0].y !== st[1].y,
              upper: st.every(s => s.y >= 40 && s.y <= H * 0.32 && s.x > 0 && s.x < W) };

// keep-out
out.keep = { prompt: L.inKeepOut(640, 650, W, H), dock: L.inKeepOut(100, 760, W, H), topbar: L.inKeepOut(640, 10, W, H),
             side: L.inKeepOut(150, 300, W, H), lift: L.liftFor(640, 650, W, H), noLift: L.liftFor(150, 300, W, H),
             below: L.liftFor(640, 880, W, H) };
out.keep.lifted = !L.inKeepOut(640, 650 - out.keep.lift, W, H);

// sparks: at most one per orb per 0.4 s and three a second in all
const S = L.sparkGate(); let ok = 0, okOne = 0;
for (let t = 0; t < 1.0; t += 0.02) { for (const id of ['a', 'b', 'c', 'd', 'e']) if (S.allow(id, t)) ok++; }
const S2 = L.sparkGate(); for (let t = 0; t < 1.0; t += 0.02) if (S2.allow('a', t)) okOne++;
out.sparks = { all: ok, one: okOne };

// colours
out.status_hex = [0x00ff80, 0xf59e0b, 0xff0080, 0xef4444, 0x7a8699];
out.hues_hex = L.HUES.map(h => h.hex);
const lum = c => ((c >> 16) & 255) + ((c >> 8) & 255) + (c & 255);
out.tint = L.HUES.map(h => [lum(L.tint(h.hex, true)), lum(L.tint(h.hex, false))]);

// approvals linked to a task
out.appr = [L.approvalTask({ task_id: 'x' }), L.approvalTask({ subject_type: 'task', subject_id: 'y' }),
            L.approvalTask({ payload: { task_id: 'z' } }), L.approvalTask({ subject_type: 'taint', subject_id: 'q' })];

// frames to orbs
out.frame = [L.orbForFrame({ turn: 'tr2' }, [{ id: 'a', trace_id: 'tr1' }, { id: 'b', trace_id: 'tr2' }]),
             L.orbForFrame({ turn: 'nope' }, [{ id: 'a', trace_id: 'tr1' }]), L.orbForFrame({}, [{ id: 'a' }])];

// form parameters follow the genome's step
const f1 = L.formParams('media', 'sha256:aaa'), f2 = L.formParams('media', 'sha256:aaa'), f3 = L.formParams('media', 'sha256:bbb');
out.forms = { same: JSON.stringify(f1) === JSON.stringify(f2), differ: L.KIND_IDS.some(k => JSON.stringify(L.formParams(k, 'sha256:aaa')) !== JSON.stringify(L.formParams(k, 'sha256:bbb'))),
              all: L.KIND_IDS.every(k => L.formParams(k, 'x') && typeof L.formParams(k, 'x') === 'object') };
console.log(JSON.stringify(out));
"""


def _run(path):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <orb-life> block"
    src = HARNESS.replace("BLOCK", m.group(1))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def o(request):
    if not node:
        pytest.skip("node is not installed")
    return _run(request.param)


def test_every_orb_has_a_kind_from_fields_the_server_sets(o):
    assert o["kinds"] == ["research", "schedule", "media", "media", "media", "mail", "system", "code",
                          "general", "code", "general"]


def test_every_orb_has_a_speakable_colour_and_names_resolve(o):
    assert o["hues"][:5] == [0, 1, 2, 3, 4] and o["hues"][5] == 0          # least used first
    assert o["words"][:5] == ["teal", "pink", "blue", "sand", "violet"]
    assert o["speak"] == "the teal research helper"
    assert o["r_green"] == {"ids": ["a", "f"], "ambiguous": True}            # two teal orbs: say so
    assert o["r_research"] == {"ids": ["a"], "ambiguous": False}
    assert o["r_helper"] == {"ids": ["b", "d"], "ambiguous": True}
    assert o["r_it"] == {"ids": ["c"], "ambiguous": False}
    assert o["r_all"] == {"ids": ["a", "b", "c", "d", "e", "f"], "ambiguous": False}
    assert o["r_newest"] == {"ids": ["f"], "ambiguous": False}
    assert o["r_none"] == {"ids": [], "ambiguous": False}
    assert o["r_teal_media"] == {"ids": [], "ambiguous": False}              # no orb is both


def test_hands_on_control_is_a_plain_state_machine(o):
    assert o["g_tap"] == ["open"]
    assert o["g_hold"] == ["pause"]                       # once, while held; the release adds nothing
    assert o["g_fling"][-1] == "cancel"
    assert "cancel" not in o["g_fling_in"]                # thrown toward Friday is not a cancel
    assert o["g_status"][-1] == "status"
    assert o["g_popout"][-1] == "popout"
    assert o["g_drop"][-1] == "drop"
    assert o["g_drag_moves"] == ["drag"]
    assert "pause" not in o["g_hold_after_drag"]          # a drag that stops is not a hold


def test_a_cancel_waits_out_its_undo_window(o):
    assert o["undo_kept"] == {"before": 0, "after": 0}    # undone: never sent
    assert o["undo_sent"]["mid"] == 0 and o["undo_sent"]["after"] == 1   # sent once, at the end


def test_orbits_close_in_only_with_real_progress(o):
    assert o["prog"] == [None, 0.5, 0.75, 0.5, None]
    assert o["radius"] == [11, 11, 8.5, 6]
    assert o["spiral"]["mono"] is True and o["spiral"]["end"] == 0
    assert o["spiral"]["done"] is True and o["spiral"]["startDone"] is False


def test_a_receipt_says_what_the_server_knows_and_a_failure_names_a_fix(o):
    ok = o["rc_ok"]
    assert ok["did"] == "Found the four phases." and ok["model"] == "model-x"
    assert ok["duration_s"] == 60 and ok["cost_usd"] == 0.0123
    assert ok["failed"] is False and ok["fix"] is None and ok["sources"] is None
    assert o["rc_fail"]["failed"] is True and o["rc_fail"]["fix"]["action"] == "resume"
    assert o["rc_fail2"]["fix"]["action"] == "rerun"
    assert o["rc_src"]["sources"] == [{"title": "A"}]


def test_the_days_stars_sit_in_the_upper_band(o):
    assert o["stars"] == {"same": True, "distinct": True, "upper": True}


def test_orbs_keep_out_of_the_centre_ui(o):
    k = o["keep"]
    assert k["prompt"] is True and k["dock"] is True and k["topbar"] is True and k["side"] is False
    assert k["lift"] > 0 and k["noLift"] == 0 and k["lifted"] is True
    assert k["below"] > 0                 # out of sight below, its label on the dock: lifted too


def test_sparks_stay_well_inside_the_flash_limit(o):
    assert o["sparks"]["one"] <= 3 and o["sparks"]["all"] <= 3


def test_no_identity_colour_is_a_status_hue_and_cloud_is_a_paler_tint(o):
    for h in o["hues_hex"]:
        for s in o["status_hex"]:
            dr, dg, db = ((h >> 16) & 255) - ((s >> 16) & 255), ((h >> 8) & 255) - ((s >> 8) & 255), (h & 255) - (s & 255)
            assert (dr * dr + dg * dg + db * db) ** 0.5 > 60, (hex(h), hex(s))
    for cloud, local in o["tint"]:
        assert cloud > local


def test_an_approval_finds_its_task_only_from_a_real_link(o):
    assert o["appr"] == ["x", "y", "z", None]


def test_a_helpers_frame_finds_its_orb_by_trace(o):
    assert o["frame"] == ["b", None, None]


def test_a_kinds_form_follows_the_genomes_step(o):
    assert o["forms"] == {"same": True, "differ": True, "all": True}
