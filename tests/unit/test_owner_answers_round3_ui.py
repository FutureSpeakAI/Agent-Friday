"""The round-3 controls on the page, rendered under node from the served index.html (no browser).

* A job card's "Follow my default" option names the default as it is now and PATCHes "default";
  a job's emails are "save as drafts" or "send" on the same card.
* Settings > Connections > Default account for drafts: Ask me plus each connected Gmail account.
* An email approval card's buttons say Send and Don't send.
* The weekly podcast's one-time offer: Switch, Keep, close.
* The top bar keeps Activity in sight while anything waits (static/friday_responsive_topbar.js):
  the packing rule at 1366 and 1280 px laptop widths, and the page publishing what waits.
Synthetic data only.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not on PATH")


def _extract(start):
    if start not in SRC:
        pytest.fail("the page has no %s" % start)
    i = SRC.index(start)
    depth, j = 0, SRC.index("{", SRC.index(")", i))
    for k in range(j, len(SRC)):
        if SRC[k] == "{":
            depth += 1
        elif SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i:k + 1]
    raise AssertionError(start)


HARNESS = """
let calls = [];
const STATE = [];
const React = {createElement: (t, p, ...c) => ({t, p: p || {}, c: c.flat(Infinity).filter(x => x !== null && x !== false && x !== undefined)})};
const useState = v => [STATE.length ? STATE.shift() : (typeof v === 'function' ? v() : v), () => {}];
const useEffect = () => {};
const apiFetch = (u, o) => { calls.push([u, o && JSON.parse(o.body || 'null')]); return Promise.resolve({json: () => ({status: 'ok'})}); };
const fetch = apiFetch;
const fridayName = () => 'Friday'; const PODCAST_C = {md: '12px', dim: 'grey', accent: 'cyan', accentSoft: 'c'};
const StRow = (p, ...c) => ({t: 'StRow', p, c: c.flat()});
const text = n => typeof n === 'string' ? n : (n && n.c || []).map(text).join('');
const all = (n, f, out = []) => { if (n && typeof n === 'object') { if (f(n)) out.push(n); (n.c || []).forEach(x => all(x, f, out)); if (n.t === 'StRow') (n.p.children || []).forEach(x => all(x, f, out)); } return out; };
"""


def _node(body):
    cp = subprocess.run([NODE, "-e", HARNESS + body], capture_output=True, text=True, encoding="utf-8",
                        timeout=60, cwd=str(ROOT))
    assert cp.returncode == 0, cp.stderr[-2000:]
    return json.loads(cp.stdout.strip().splitlines()[-1])


def test_a_job_card_offers_follow_my_default_naming_it_and_the_email_choice():
    got = _node(_extract("function WfJobChoices(") + """
const wf = WfJobChoices({w: {schedule_id: 'sch_1', missed: 'run_once', missed_own: false, missed_default: 'skip',
                             on_held: 'wait', on_held_own: true, held_default: 'retry', model_pinned: true, email_send: true}});
const sel = all(wf, n => n.t === 'select');
const opts = sel.map(s => all(s, n => n.t === 'option').map(o => [o.p.value, text(o)]));
sel[0].p.onChange({target: {value: 'default'}});
sel[2].p.onChange({target: {value: 'draft'}});
console.log(JSON.stringify({values: sel.map(s => s.p.value), opts, calls}));
""")
    assert got["values"] == ["default", "wait", "send"], got["values"]
    assert ["default", "follow my default (now: skip)"] in got["opts"][0]
    assert ["default", "follow my default (now: run once when it is back)"] in got["opts"][1]
    assert [o[0] for o in got["opts"][2]] == ["draft", "send"]
    assert got["calls"][0] == ["/api/schedules/sch_1", {"missed": "default"}]
    assert got["calls"][1] == ["/api/schedules/sch_1", {"email_send": False}]


def test_settings_lists_the_draft_accounts_with_ask_me_and_saves_the_choice():
    got = _node(_extract("function MailDefaultAccountRow(") + """
STATE.push([{id: 'acct_a', email: 'owner@example.test', label: 'Work'}, {id: 'acct_b', email: 'home@example.test', label: 'home@example.test'}]);
let saved = null;
const row = MailDefaultAccountRow({s: {mail_default_account: 'home@example.test'}, save: x => { saved = x; }});
const sel = all(row, n => n.t === 'select')[0];
const opts = all(sel, n => n.t === 'option').map(o => [o.p.value, text(o)]);
sel.p.onChange({target: {value: ''}});
console.log(JSON.stringify({label: row.p.label, key: row.p.settingKey, value: sel.p.value, opts, saved}));
""")
    assert got["label"] == "Default account for drafts" and got["key"] == "settings.mail_default_account"
    assert got["opts"] == [["", "Ask me"], ["acct_a", "Work (owner@example.test)"], ["acct_b", "home@example.test"]]
    assert got["value"] == "acct_b", "the saved address did not select its account"
    assert got["saved"] == {"mail_default_account": ""}


def test_an_email_card_says_send_and_dont_send():
    got = _node(_extract("function fridayApprovalWords(") + """
console.log(JSON.stringify({mail: fridayApprovalWords({payload: {handler: 'gmail_send'}}), other: fridayApprovalWords({kind: 'x', payload: {}})}));
""")
    assert got == {"mail": ["Send", "Don't send"], "other": ["Approve", "Deny"]}
    body = _extract("function ApprovalCardBody(")
    assert body.count("fridayApprovalWords(a)[0]") == 1 and body.count("fridayApprovalWords(a)[1]") == 1


def test_the_weekly_offer_has_switch_keep_and_close_and_posts_the_answer():
    got = _node(_extract("function WeeklyOneVoiceOffer(") + """
STATE.push('offered');
const card = WeeklyOneVoiceOffer();
const buttons = all(card, n => n.t === 'button');
buttons[0].p.onClick();
console.log(JSON.stringify({text: text(card), buttons: buttons.map(text), calls}));
""")
    assert "Weekly is two voices; want one voice?" in got["text"]
    assert got["buttons"] == ["Switch", "Keep", "×"]
    assert got["calls"][-1] == ["/api/podcasts/weekly-one-voice", {"choice": "switch"}]
    assert "h(WeeklyOneVoiceOffer, null)" in SRC


# ── the top bar ─────────────────────────────────────────────────────────────────

#: Widths of the desktop top bar's chips with an approval waiting (a long workspace name and model name).
#: A 1366 or 1280 px laptop at Windows' usual 125-150% scaling is about 1100 CSS px wide.
BAR = """
const items = [
  {id: 'brand', brand: true, width: 176, priority: 1000},
  {id: 'workspaces', width: 190, priority: 100},
  {id: 'style', width: 92, priority: 50},
  {id: 'projects', width: 78, priority: 75},
  {id: 'activity', width: 72, priority: 40, kind: 'activity'},
  {id: 'depth', width: 84, priority: 65},
  {id: 'model', width: 230, priority: 50},
  {id: 'approvals', width: 170, priority: 95},
  {id: 'local-ai', width: 160, priority: 99},
  {id: 'clock', width: 64, priority: 5},
  {id: 'bell', width: 34, priority: 80},
  {id: 'chat', width: 34, priority: 90},
  {id: 'settings', width: 34, priority: 35},
].map((x, order) => ({...x, order}));
"""


def test_activity_never_collapses_while_something_waits_at_laptop_widths():
    got = _node("""
const T = require('./static/friday_responsive_topbar.js');
""" + BAR + """
const out = {};
for (const w of [1366, 1280, 1100, 1024]) {
  const keepFacts = T.itemFacts ? T.itemFacts('Activity', true) : {};
  const waiting = items.map(x => x.id === 'activity' ? {...x, ...keepFacts} : x);
  const quiet = items.map(x => x.id === 'activity' ? {...x, ...(T.itemFacts ? T.itemFacts('Activity', false) : {})} : x);
  out[w] = {waiting: T.packTopbar(w, waiting, 40, 8), quiet: T.packTopbar(w, quiet, 40, 8)};
}
console.log(JSON.stringify(out));
""")
    for w in ("1366", "1280", "1100", "1024"):
        assert "activity" in got[w]["waiting"]["visible"], "Activity collapsed at %s px while something waits" % w
        assert got[w]["waiting"]["overflow"], "nothing else collapsed at %s px (the case is not exercised)" % w
    # Negative control: with nothing waiting the old priority order still applies, and it is the one that
    # tucked Activity away at a scaled laptop width.
    assert "activity" in got["1100"]["quiet"]["overflow"]


def test_the_waiting_store_reports_approvals_and_needs_you_and_the_page_publishes_both():
    got = _node("""
const T = require('./static/friday_responsive_topbar.js');
const s = T.waitingStore(); const seen = [];
s.subscribe(v => seen.push(v));
s.set({approvals: 1}); s.set({approvals: 1}); s.set({approvals: 0}); s.set({needs: 2}); s.set({needs: 0});
console.log(JSON.stringify({seen, now: s.get(), facts: [T.itemFacts('Activity', true), T.itemFacts('Projects', true)]}));
""")
    assert got["seen"] == [True, False, True, False] and got["now"] is False
    assert got["facts"][0] == {"priority": 40, "kind": "activity", "keep": True} and got["facts"][1] is None
    assert "window.fridayWaiting.set({ approvals: (items || []).length })" in SRC
    assert "window.fridayWaiting.set({ needs: (d.needs_you || 0) + (d.held_sends || 0) })" in SRC
    js = (ROOT / "static/friday_responsive_topbar.js").read_text(encoding="utf-8")
    assert "item.element.dataset.keep" in js and "metadata(node, groupIndex, index, waiting)" in js
