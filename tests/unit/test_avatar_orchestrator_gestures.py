"""Friday's avatar is a window into her own state and nobody else's
(avatar-visual-genome.md §13), run under node from both scene files.

- Orchestrator only: frames labelled for any other agent (a helper, another
  model, a background job) never move the scene, however many arrive. Her
  helpers show only as her own state, one calm held motion, the same for
  one helper or ten; the status line counts them.
- Every real event Friday emits has its gesture, each played three times:
  a memory search draws one unit per source toward the core; a reflex snaps
  a unit; a saved memory sinks one; private local work frosts the outer
  units and one floats out when the summary is sent; a tool that worked
  locks home. The user's own data moves it too: typing sends a data-in wave
  across the facing side, and talking over her makes it yield.
- Evolution sets the style, never the meaning (§13.7): at every gesture
  gene's extreme, each gesture moves the same units, the same number of
  times, the same way; only its timing and curve change.
- Under reduced motion, every new gesture is brightness only, and brightness
  never steps more than 0.08 a frame, even under a flood.
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
BLOCK = re.compile(r"// <presence-gestures>\n(.*?)// </presence-gestures>", re.S)

HARNESS = r"""
BLOCK
const S = 1.6, units = [], at = {};
for (let x = 0; x < 3; x++) for (let y = 0; y < 3; y++) for (let z = 0; z < 3; z++) {
  at[[x, y, z]] = units.length; units.push([(x - 1) * S, (y - 1) * S, (z - 1) * S]);
}
const blocks = [];
for (let a = 0; a < 3; a++) for (const side of [0, 2]) {
  const o = [0, 1, 2].filter(k => k !== a);
  for (const s0 of [0, 1]) for (const s1 of [0, 1]) {
    const us = [], cs = [];
    for (const d0 of [0, 1]) for (const d1 of [0, 1]) {
      const g = [0, 0, 0]; g[a] = side; g[o[0]] = s0 + d0; g[o[1]] = s1 + d1;
      us.push(at[g]); cs.push(g.map(v => (v - 1) * S));
    }
    const axis = [0, 0, 0]; axis[a] = side === 2 ? 1 : -1;
    blocks.push({ units: us, axis, centre: [0, 1, 2].map(k => cs.reduce((s, c) => s + c[k], 0) / 4) });
  }
}
const layers = [[], [], []]; units.forEach((u, i) => layers[Math.round(u[0] / S) + 1].push(i));
const top = []; units.forEach((u, i) => { if (u[1] > 1) top.push(i); });
const an = { units, layers, blocks, top, core: [0, 0, 0], toward: [0, 0, 1], up: [0, 1, 0],
             across: [1, 0, 0], extent: S * 1.5, spacing: S };
const G = FridayGestures, dt = 1 / 60;
const P = (state, phase, extra) => Object.assign({ type: 'presence', agent: 'friday', state, phase }, extra || {});
const mag = v => Math.hypot(v[0], v[1], v[2]);
const peaks = (series, thr) => { let n = 0, up = false;
  for (const v of series) { if (!up && v > thr) { n++; up = true; } else if (up && v < thr * 0.3) up = false; } return n; };
// Run sec seconds; return the per-frame records.
const record = (sec, each) => { const rs = []; for (let i = 0; i < Math.round(sec / dt); i++) { const q = G.step(dt, an); rs.push(q); if (each) each(q, i); } return rs; };
const moved = q => Object.keys(q.units).filter(k => { const u = q.units[k];
  return mag(u.d) > 1e-6 || mag(u.r) > 1e-6 || Math.abs(u.s - 1) > 1e-6; }).map(Number).sort((a, b) => a - b);
const out = {};
let maxStep = 0, lastB = {};
// A fresh engine, and a fresh watch on brightness (steps are per session).
const fresh = (reduced) => { G.reset(); G.setReduced(!!reduced); lastB = {}; };
const watchB = q => { for (const k of Object.keys(q.units)) { const b = q.units[k].b;
  maxStep = Math.max(maxStep, Math.abs(b - (lastB[k] === undefined ? 1 : lastB[k]))); lastB[k] = b; } };

// ── 1. a flood of other agents' frames moves nothing ──────────────────────
fresh(REDUCED);
// An unlabelled frame (undefined) is not hers either: only agent === 'friday' is.
const OTHERS = ['helper:1', 'helper-0a1b2c3d4e5f', 'salon:host', 'laya', 'needle', 'background:sched-1',
                'model:gemini', undefined, 'FRIDAY', ''];
let k = 0;
for (let i = 0; i < 300; i++) {
  const a = OTHERS[i % OTHERS.length];
  const kinds = [P('tool', 'start', { ref: 'x' + i, agent: a }), P('round', 'step', { n: 1 + (i % 5), agent: a }),
    P('egress', 'sent', { route: 'cloud', agent: a }), P('verify', 'once', { ok: true, agent: a }),
    P('error', 'once', { agent: a }), P('retrieval', 'once', { n: 5, agent: a }), P('memory_saved', 'once', { agent: a }),
    P('tool', 'end', { ref: 'x' + (i - 1), ok: false, agent: a }), P('handoff', 'start', { agent: a })];
  G.frame(kinds[i % kinds.length]);
}
let floodMoved = new Set(), floodStatus = new Set();
record(3, q => { moved(q).forEach(i => floodMoved.add(i)); floodStatus.add(q.status); watchB(q); });
out.flood_moved = floodMoved.size; out.flood_status = [...floodStatus];

// ── 2. ten helpers look like one: one calm held state ─────────────────────
const helperLook = (n) => { fresh(REDUCED);
  for (let i = 0; i < n; i++) G.frame(P('subagent', 'start', { ref: 'h' + i, agent: 'friday' }));
  // and every helper's own work, labelled as theirs
  for (let i = 0; i < 200; i++) G.frame(P(['tool', 'round', 'egress'][i % 3], ['start', 'step', 'sent'][i % 3],
                                          { ref: 't' + i, n: 1 + (i % 3), route: 'cloud', agent: 'helper:' + (i % n) }));
  let maxD = 0, maxB = 0, unitsSet = new Set(), status = '';
  record(4, q => { for (const k2 of Object.keys(q.units)) { const u = q.units[k2]; maxD = Math.max(maxD, mag(u.d)); maxB = Math.max(maxB, Math.abs(u.b - 1)); unitsSet.add(+k2); }
                   status = q.status; watchB(q); });
  const layerOk = [...unitsSet].every(i => layers[1].includes(i));
  for (let i = 0; i < n; i++) G.frame(P('subagent', 'end', { ref: 'h' + i, agent: 'friday' }));
  const after = moved(record(2).pop()).length;
  return { maxD: +maxD.toFixed(4), maxB: +maxB.toFixed(4), units: unitsSet.size, layerOk, status, after };
};
out.helpers1 = helperLook(1); out.helpers10 = helperLook(10);
// her own work while helpers run still shows, and the status counts the helpers
fresh(REDUCED);
for (let i = 0; i < 3; i++) G.frame(P('subagent', 'start', { ref: 'h' + i, agent: 'friday' }));
G.frame(P('round', 'step', { n: 1, agent: 'friday' }));
let sawWave = false; record(0.7, q => { if (layers[0].some(i => q.units[i] && (mag(q.units[i].d) > 0.1 || q.units[i].b > 1.05))) sawWave = true; });
out.own_with_helpers = sawWave;
out.own_with_helpers_status = G.status();
record(5); out.waiting_status = G.status();
out.flood_max_step = maxStep;

// ── 3. each real event has its gesture, three times ───────────────────────
const series = (setup, sec, metric) => { fresh(REDUCED); setup(); const s = []; let st = ''; const touched = new Set();
  record(sec, q => { s.push(metric(q)); if (!st && q.status) st = q.status; moved(q).forEach(i => touched.add(i));
    Object.keys(q.units).forEach(i => { if (Math.abs(q.units[i].b - 1) > 1e-3) touched.add(+i); }); watchB(q); });
  return { s, st, touched: touched.size, rest: moved(record(1.5).pop()).length }; };
const toCore = q => Math.max(0, ...Object.keys(q.units).map(i => { const u = q.units[i], p = units[i];
  return REDUCED ? Math.max(0, u.b - 1) : Math.max(0, -(u.d[0] * p[0] + u.d[1] * p[1] + u.d[2] * p[2]) / (mag(p) || 1)); }));
const dimOrIn = q => Math.max(0, ...Object.values(q.units).map(u => REDUCED ? Math.max(0, 1 - u.b) : Math.max(0, 1 - u.s)));
const anyRot = q => Math.max(0, ...Object.values(q.units).map(u => REDUCED ? Math.max(0, u.b - 1) : Math.abs(u.r[2])));
const outward = q => Math.max(0, ...Object.keys(q.units).map(i => { const u = q.units[i], p = units[i];
  return REDUCED ? Math.max(0, u.b - 1) : Math.max(0, (u.d[0] * p[0] + u.d[1] * p[1] + u.d[2] * p[2]) / (mag(p) || 1)); }));
const backward = q => Math.max(0, ...Object.values(q.units).map(u => REDUCED ? Math.max(0, 1 - u.b) : Math.max(0, -u.d[2])));
let r = series(() => G.frame(P('retrieval', 'once', { n: 4 })), 3.6, toCore);
out.draw = { peaks: peaks(r.s, REDUCED ? 0.1 : 0.3), units: r.touched, status: r.st, rest: r.rest };
r = series(() => G.frame(P('reflex', 'once')), 1.4, anyRot);
out.snap = { peaks: peaks(r.s, REDUCED ? 0.1 : 1.0), units: r.touched, rest: r.rest };
r = series(() => G.frame(P('memory_saved', 'once')), 4.0, q => REDUCED ? Math.max(0, ...Object.values(q.units).map(u => 1 - u.b)) : toCore(q));
out.sink = { peaks: peaks(r.s, REDUCED ? 0.02 : 0.4), units: r.touched, status: r.st, rest: r.rest };
r = series(() => G.frame(P('handoff', 'start')), 1.5, q => Math.max(0, ...Object.values(q.units).map(u => 1 - u.b)));
out.frost = { dims: r.s[r.s.length - 1] > 0.1, status: r.st };
G.frame(P('handoff', 'sent')); const fl = []; let flStatus = '', flUnit = null, flMax = 0;
record(4.0, q => { fl.push(outward(q)); if (!flStatus && q.status) flStatus = q.status;
  Object.keys(q.units).forEach(i => { const m = REDUCED ? q.units[i].b - 1 : mag(q.units[i].d); if (m > flMax) { flMax = m; flUnit = +i; } }); });
// the unit that floats is one the user can see: on the facing side, at the top
out.float = { peaks: peaks(fl, REDUCED ? 0.1 : 0.4), status: flStatus,
              facing: flUnit !== null && units[flUnit][2] > 1, top: flUnit !== null && units[flUnit][1] > 1 };
// a handoff that ends without a send (no local model, a card raised) thaws:
// nothing floats out, and the outer units come back
r = series(() => { G.frame(P('handoff', 'start')); record(1.5); G.frame(P('handoff', 'end')); }, 2.0, outward);
const thawed = record(0.1).pop();
out.thaw = { floated: Math.max(0, ...r.s) > 0.05, rest: r.rest, status: G.status(),
             dim: Object.values(thawed.units).some(u => u.b < 0.99 || Math.abs(u.s - 1) > 1e-3) };
// (measured once the twist is home, when the lock begins)
r = series(() => { G.frame(P('tool', 'start', { ref: 'w1' })); record(2.2); G.frame(P('tool', 'end', { ref: 'w1', ok: true })); record(0.3); }, 1.3,
           q => REDUCED ? Math.max(0, ...Object.values(q.units).map(u => u.b - 1)) : dimOrIn(q));
out.lock = { peaks: peaks(r.s, REDUCED ? 0.05 : 0.03), rest: r.rest };
r = series(() => G.yielded(), 1.8, backward);
out.yield = { peaks: peaks(r.s, REDUCED ? 0.1 : 0.25), rest: r.rest };
fresh(REDUCED); const ty = [];
for (let i = 0; i < 90; i++) { if (i % 6 === 0 && i < 60) G.typed(); const q = G.step(dt, an); ty.push(moved(q).length + Object.values(q.units).filter(u => u.b > 1.01).length); watchB(q); }
out.typing_moves = Math.max(...ty.slice(0, 60)) > 0;
out.typing_rest = moved(record(1.5).pop()).length;

// ── 4. evolution: style changes, meaning does not ─────────────────────────
const meaning = (style) => { const res = {};
  const go = (setup, sec) => { G.reset(); G.setReduced(false); G.setStyle(style); setup(); const touched = new Set(); let t = 0, end = 0;
    record(sec, q => { t += dt; const m = moved(q); m.forEach(i => touched.add(i)); if (m.length) end = t; }); return { touched: [...touched].sort((a, b) => a - b), end }; };
  const wv = []; G.reset(); G.setStyle(style); G.frame(P('round', 'step', { n: 2 }));
  record(6, q => wv.push(layers[1].reduce((s, i) => s + (q.units[i] ? mag(q.units[i].d) : 0), 0)));
  res.wave_peaks = peaks(wv, 0.5);
  res.wave_units = go(() => G.frame(P('round', 'step', { n: 2 })), 6).touched;
  const vt = []; let up = true; G.reset(); G.setStyle(style); G.frame(P('egress', 'sent', { route: 'cloud' }));
  record(7, q => { vt.push(q.thread); top.forEach(i => { if (q.units[i] && q.units[i].d[1] < -1e-6) up = false; }); });
  res.vent_peaks = peaks(vt, 0.5); res.vent_up = up;
  const tw = []; G.reset(); G.setStyle(style); G.frame(P('tool', 'start', { ref: 's1' }));
  record(3, q => tw.push(Math.max(0, ...Object.values(q.units).map(u => Math.abs(u.r[2])))));
  res.twist_peaks = peaks(tw, 1.2); res.twist_held = tw[tw.length - 1] > 1.5;
  const ap = []; G.reset(); G.setStyle(style); G.setApprovals(1);
  record(4, q => ap.push(Math.max(0, ...Object.values(q.units).filter(u => u.hue === 'approval').map(u => u.d[2]))));
  res.approval_peaks = peaks(ap, 0.6); res.approval_forward = ap[ap.length - 1] > 0.6;
  res.draw_units = go(() => G.frame(P('retrieval', 'once', { n: 3 })), 4).touched.length;
  res.wave_end = go(() => G.frame(P('round', 'step', { n: 2 })), 6).end;
  return res; };
out.style_default = meaning({});
out.style = [{ tempo: 0.85, ease: 'snap', trail: 0.5 }, { tempo: 1.15, ease: 'glide', trail: 0 }, { tempo: 1.0, ease: 'spring', trail: 0.25 }].map(meaning);
// trail: glow lingers longer with a longer trail
const glowAfter = (trail) => { G.reset(); G.setStyle({ trail }); G.frame(P('reflex', 'once')); let last = 0, t = 0;
  record(3, q => { t += dt; if (Object.values(q.units).some(u => u.g > 0.01)) last = t; }); return last; };
out.glow_trail0 = glowAfter(0); out.glow_trail5 = glowAfter(0.5);
console.log(JSON.stringify(out));
"""


def _run(path, reduced):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <presence-gestures> block"
    src = HARNESS.replace("BLOCK", m.group(1)).replace("REDUCED", "true" if reduced else "false")
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def runs(request):
    if not node:
        pytest.skip("node is not installed")
    return {"full": _run(request.param, False), "reduced": _run(request.param, True)}


def test_a_flood_of_other_agents_frames_moves_nothing(runs):
    for o in runs.values():
        assert o["flood_moved"] == 0
        assert o["flood_status"] == [""]
        assert o["flood_max_step"] <= 0.08 + 1e-9


def test_ten_helpers_look_like_one_calm_state_of_her_own(runs):
    o = runs["full"]
    one, ten = o["helpers1"], o["helpers10"]
    assert one["units"] > 0 and one["layerOk"] is True          # one layer turns
    assert ten["maxD"] == one["maxD"] and ten["units"] == one["units"]   # ten look like one
    assert ten["maxD"] <= 0.15                                   # and it is calm
    assert one["status"] == "Waiting on 1 helper" and ten["status"] == "Waiting on 10 helpers"
    assert one["after"] == 0 and ten["after"] == 0               # gone when they finish
    assert o["own_with_helpers"] is True                         # her own round still shows
    assert o["own_with_helpers_status"].startswith("Thinking, round 1")
    assert o["waiting_status"] == "Waiting on 3 helpers"
    red = runs["reduced"]["helpers10"]
    assert red["maxD"] == 0 and red["maxB"] > 0                  # reduced: brightness only


def test_each_real_event_has_its_gesture_three_times(runs):
    o = runs["full"]
    assert o["draw"]["peaks"] == 3 and o["draw"]["units"] == 4 and o["draw"]["rest"] == 0
    assert o["draw"]["status"] == "Found 4 sources"
    assert o["snap"]["peaks"] == 3 and o["snap"]["units"] == 1 and o["snap"]["rest"] == 0
    assert o["sink"]["peaks"] == 3 and o["sink"]["units"] == 1 and o["sink"]["rest"] == 0
    assert o["sink"]["status"] == "Saved to memory"
    assert o["frost"]["dims"] is True and o["frost"]["status"] == "Working privately on this computer"
    assert o["float"]["peaks"] == 3 and o["float"]["status"] == "Sent a scrubbed summary"
    assert o["float"]["facing"] is True and o["float"]["top"] is True
    assert o["thaw"] == {"floated": False, "rest": 0, "status": "", "dim": False}
    assert o["lock"]["peaks"] == 3 and o["lock"]["rest"] == 0
    assert o["yield"]["peaks"] == 3 and o["yield"]["rest"] == 0
    assert o["typing_moves"] is True and o["typing_rest"] == 0


def test_reduced_motion_new_gestures_are_brightness_only(runs):
    o = runs["reduced"]
    for g in ("draw", "snap", "sink", "float", "yield"):
        assert o[g]["peaks"] == 3, g
    assert o["lock"]["peaks"] == 3
    assert o["thaw"]["floated"] is False and o["thaw"]["dim"] is False
    assert o["flood_max_step"] <= 0.08 + 1e-9


def test_evolution_changes_the_style_never_the_meaning(runs):
    o = runs["full"]
    base = o["style_default"]
    assert base["wave_peaks"] == 3 and base["vent_peaks"] == 3 and base["twist_peaks"] == 3
    for s in o["style"]:
        for k in ("wave_peaks", "vent_peaks", "vent_up", "twist_peaks", "twist_held",
                  "approval_peaks", "approval_forward", "draw_units", "wave_units"):
            assert s[k] == base[k], k
    slow, fast = o["style"][0], o["style"][1]                   # tempo 0.85 and 1.15
    assert slow["wave_end"] > base["wave_end"] > fast["wave_end"]
    assert o["glow_trail5"] > o["glow_trail0"]                   # a longer trail lingers


# The page feeds the avatar: evolution's style, its speech amplitude (never
# past the reduced-motion cap), and the user's own typing and talking over
# her. Each anchor occurs exactly once, so a second copy cannot satisfy it.
SCENE_WIRING = [
    "FridayGestures.setStyle(FridayGenome.style());",
    "window.fridayAvatar = { typed: () => FridayGestures.typed(), yielded: () => FridayGestures.yielded() };",
    "* (SceneMotion.reduced() ? 1 : FridayGenome.speechAmplitude());",
]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.name)
def test_the_scene_takes_its_style_from_evolution(path):
    text = path.read_text(encoding="utf-8")
    for anchor in SCENE_WIRING:
        assert text.count(anchor) == 1, f"{path.name}: {anchor!r} x{text.count(anchor)}"


def _assert_playback_yield_wiring(text):
    # Interrupt, Escape and Crew quiet yield; stopping the voice session does not.
    callers = re.findall(r"flushPlaybackRef\.current\?\.\('([^']*)'", text)
    yields = sorted(c for c in callers if re.search(r"interrupted|barge", c, re.I))
    assert yields == ["Crew quiet / barge", "Escape barge-in", "WS interrupted message"], callers
    flush = text[re.search(r"flushPlaybackRef\.current\s*=\s*\(?caller\)?\s*=>", text).end():][:4000]
    assert re.search(r"/interrupted\|barge/i\.test\(caller\s*\|\|\s*''\)\)\s*\{\s*try\s*\{\s*"
                     r"window\.fridayAvatar\s*&&\s*window\.fridayAvatar\.yielded\(\)", flush)


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_typing_and_being_talked_over_reach_the_avatar(path):
    text = path.read_text(encoding="utf-8")
    _assert_playback_yield_wiring(text)
    # Typing to Friday: every change in the chat input.
    assert len(re.findall(r"onChange\(e\.target\.value\);.{0,200}?window\.fridayAvatar\.typed\(\)", text, re.S)) == 1


@pytest.mark.parametrize("caller", ["Crew quiet / barge", "Escape barge-in", "WS interrupted message"])
def test_yield_guard_rejects_each_disconnected_interrupt_path(caller):
    text = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    changed = text.replace("flushPlaybackRef.current?.('" + caller + "'", "disconnectedPlayback?.('" + caller + "'")
    assert changed != text
    with pytest.raises(AssertionError):
        _assert_playback_yield_wiring(changed)


def test_yield_guard_rejects_missing_avatar_signal():
    text = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    changed = text.replace("window.fridayAvatar.yielded()", "void 0")
    assert changed != text
    with pytest.raises(AssertionError):
        _assert_playback_yield_wiring(changed)
