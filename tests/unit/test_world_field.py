"""The world's dots telegraph Friday's state (avatar-visual-genome.md §17),
run under node from both scene files: the state logic, no GPU.

- Only her frames (agent "friday") move the field; a helper's, another
  agent's or an unlabelled frame never does.
- The cloud web fires only on her sealed cloud egress ("egress", "sent",
  route "cloud"): never on a local route, a helper's egress or anything else,
  so it can stand as a privacy signal.
- The web spools out, holds, and pulls back when the answer comes (her next
  route, tool start, round, verify or error), or after a cap if nothing comes.
- Thinking (her rounds) is a current toward her that fades when the rounds
  stop; her private local work makes the nearest dots glow while it lasts,
  and a local answer is a brief glow.
- Offline, the field goes still, gently; back online, it moves again.
- The web fades in; its brightness never jumps.
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
BLOCK = re.compile(r"// <world-field>\n(.*?)        const FridayField = ", re.S)

HARNESS = r"""
BLOCK
const F = FridayFieldState, out = {};
const P = (state, phase, extra) => Object.assign({ type: 'presence', state, phase, agent: 'friday' }, extra || {});
const run = sec => { let r; for (let i = 0; i < Math.round(sec * 60); i++) r = F.step(1 / 60); return r; };

// only hers, and only a sealed cloud egress fires the web
F.reset();
F.frame(Object.assign(P('egress', 'sent', { route: 'cloud' }), { agent: 'helper-abc' }));
F.frame(Object.assign(P('egress', 'sent', { route: 'cloud' }), { agent: undefined }));
F.frame(P('egress', 'sent', { route: 'local' }));
F.frame(P('route', 'step', { route: 'cloud' }));
F.frame(P('egress', 'start', { route: 'cloud' }));
out.not_fired = { fired: F.fired(), web: run(0.5).web };
F.frame(P('egress', 'sent', { route: 'cloud' }));
out.fired = F.fired();

// spool, hold, pull back on the answer
let r = run(0.1); const early = r.web.front;
r = run(2.4); out.hold = { phase: r.web.phase, front: r.web.front, intensity: r.web.intensity, early: +early.toFixed(3) };
F.frame(P('route', 'step', { route: 'cloud' }));
r = run(0.5); out.retracting = { phase: r.web.phase, lessThanFull: r.web.front < 1 };
r = run(1.2); out.gone = r.web;

// the answer can also be her next tool call or round; a helper's frame is not her answer
F.reset(); F.frame(P('egress', 'sent', { route: 'cloud' })); run(1);
F.frame(Object.assign(P('tool', 'start'), { agent: 'helper-x' })); out.helper_not_answer = run(0.1).web.phase;
F.frame(P('tool', 'start')); out.tool_answer = run(0.1).web.phase;
// no answer: it pulls back after the cap
F.reset(); F.frame(P('egress', 'sent', { route: 'cloud' }));
r = run(10); out.capHold = r.web && r.web.phase;
r = run(9); out.capGone = r.web;

// the web fades in: intensity never jumps
F.reset(); F.frame(P('egress', 'sent', { route: 'cloud' }));
let maxStep = 0, last = 0;
for (let i = 0; i < 300; i++) { const w = F.step(1 / 60).web; const v = w ? w.intensity : 0; maxStep = Math.max(maxStep, Math.abs(v - last)); last = v; }
out.fadeStep = +maxStep.toFixed(4);

// thinking: a current toward her, fading when the rounds stop
F.reset(); F.frame(P('round', 'step', { n: 1 }));
out.thinking = { during: run(1.5).current, after: run(5).current };
F.reset(); F.frame(Object.assign(P('round', 'step', { n: 1 }), { agent: 'helper-y' }));
out.helperRound = run(1.5).current;

// local work: the nearest dots glow while it lasts; a local answer, briefly
F.reset(); F.frame(P('handoff', 'start'));
out.local = { during: run(1.5).glow };
F.frame(P('handoff', 'end')); out.local.after = run(1.5).glow;
F.reset(); F.frame(P('route', 'step', { route: 'local' }));
out.localAnswer = { soon: run(1).glow, later: run(3).glow };

// offline: still, gently; back online: moving
F.reset(); F.setOffline(true);
const first = F.step(1 / 60).speed; r = run(3);
out.offline = { first: +first.toFixed(3), speed: r.speed, still: r.still };
F.setOffline(false); out.online = run(3).speed;
console.log(JSON.stringify(out));
"""


def _run(path):
    m = BLOCK.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no <world-field> block"
    src = HARNESS.replace("BLOCK", m.group(1))
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module", params=SCENES, ids=lambda p: p.name)
def o(request):
    if not node:
        pytest.skip("node is not installed")
    return _run(request.param)


def test_only_her_sealed_cloud_egress_fires_the_web(o):
    assert o["not_fired"] == {"fired": 0, "web": None}
    assert o["fired"] == 1


def test_the_web_spools_holds_and_pulls_back_on_the_answer(o):
    h = o["hold"]
    assert 0 < h["early"] < 0.1
    assert h["phase"] == "hold" and h["front"] == 1 and h["intensity"] == 1
    assert o["retracting"] == {"phase": "retract", "lessThanFull": True}
    assert o["gone"] is None


def test_the_answer_is_hers_or_the_cap(o):
    assert o["helper_not_answer"] == "spool" and o["tool_answer"] == "retract"
    assert o["capHold"] == "hold" and o["capGone"] is None


def test_the_web_fades_in(o):
    assert o["fadeStep"] <= 0.05


def test_thinking_is_a_current_toward_her_that_fades(o):
    assert o["thinking"]["during"] > 0.9 and o["thinking"]["after"] == 0
    assert o["helperRound"] == 0


def test_local_work_glows_while_it_lasts(o):
    assert o["local"]["during"] == 1 and o["local"]["after"] == 0
    assert o["localAnswer"]["soon"] > 0.5 and o["localAnswer"]["later"] == 0


def test_offline_goes_still_gently_and_comes_back(o):
    assert o["offline"]["first"] > 0.95                      # no jump
    assert o["offline"]["speed"] == 0 and o["offline"]["still"] is True
    assert o["online"] == 1
