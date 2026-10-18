"""The tested-change card shows the owner what they decide on, in full.

Renders static/friday_trial_card.js in Node with a minimal React stand-in that
expands function components into a plain tree, then checks the tree: the text
in use now and the new text side by side, the evidence, the recommendation as
a label, the place to undo, and exactly two buttons, "Keep current" and "Use
new", neither pre-selected (no autofocus, same style). Also pins that both
pages load the card and route `instruction_trial` to it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CARD_JS = ROOT / "static" / "friday_trial_card.js"

HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
function expand(node) {
  if (node === null || node === undefined || node === false || node === true) return null;
  if (Array.isArray(node)) return node.map(expand);
  if (typeof node !== 'object') return String(node);
  if (typeof node.type === 'function') return expand(node.type(Object.assign({}, node.props, { children: node.children })));
  return { type: node.type, props: node.props || {}, children: (node.children || []).map(expand) };
}
global.window = {};
global.React = {
  createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
  useState: (v) => [v, () => {}], useEffect: () => {}, useCallback: (f) => f,
};
eval(src);
const a = JSON.parse(process.argv[2]);
const tree = expand(React.createElement(window.FridayTrialCard, { a, busy: false, onDecide: () => {} }));
const texts = [], buttons = [];
(function walk(n) {
  if (n === null) return;
  if (typeof n === 'string') { texts.push(n); return; }
  if (Array.isArray(n)) { n.forEach(walk); return; }
  if (n.type === 'button') buttons.push({ label: n.children.join(''), props: Object.keys(n.props), style: JSON.stringify(n.props.style || {}), testid: n.props['data-testid'] || '' });
  n.children.forEach(walk);
})(tree);
console.log(JSON.stringify({ text: texts.join('\n'), buttons }));
"""


def _render(card: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", HARNESS, str(CARD_JS), json.dumps(card)], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


CARD = {
    "approval_id": "appr_x1", "kind": "instruction_trial", "title": "Tested change: Say what you are about to do",
    "description": "Friday tested a change.\nThe new way did better on 5, worse on 0.",
    "payload": {
        "trial_id": "trial_1", "recommended": "candidate", "label": "the new way did better",
        "rule": "trial-rule-1", "per_task": {"mail-02": {"a_pass": 0, "b_pass": 2, "n": 2, "favours": "b"}},
        "missing": {"a": 0, "b": 1}, "new_safety_failures": [],
        "changes": [{"what": "instruction", "id": "50_say_before_acting", "title": "Say what you are about to do",
                     "before": "OLD TEXT in use now", "after": "NEW TEXT it would become",
                     "diff": "--- current\n+++ new\n-OLD TEXT in use now\n+NEW TEXT it would become\n"}],
        "undo": "Settings > Privacy & Data > instructions: compare with the shipped text and undo",
        "buttons": ["Keep current", "Use new"], "preselected": None,
    },
}


def test_the_card_shows_both_texts_the_evidence_and_where_to_undo():
    r = _render(CARD)
    for want in ("OLD TEXT in use now", "NEW TEXT it would become", "Recommended: use the new way",
                 "mail-02", "Missing runs", "To undo later: Settings > Privacy & Data > instructions"):
        assert want in r["text"], want


def test_the_card_has_two_equal_buttons_and_nothing_preselected():
    r = _render(CARD)
    decide = [b for b in r["buttons"] if b["testid"] in ("trial-keep", "trial-use-new")]
    assert [b["label"] for b in decide] == ["Keep current", "Use new"]
    assert decide[0]["style"] == decide[1]["style"]
    assert not any("autoFocus" in b["props"] for b in r["buttons"])


def test_an_untested_change_says_so():
    card = json.loads(json.dumps(CARD))
    card["payload"].update(untested=True, recommended="current", per_task={})
    r = _render(card)
    assert "untested" in r["text"] and "Recommended: keep current" in r["text"]


def test_both_pages_route_the_card_to_it():
    for page in ("index.html", "ui_parts/app.html"):
        text = (ROOT / page).read_text(encoding="utf-8")
        assert "a.kind === 'instruction_trial' && window.FridayTrialCard" in text, page
    for page in ("index.html", "ui_parts/styles_and_scene.html"):
        assert "/static/friday_trial_card.js" in (ROOT / page).read_text(encoding="utf-8"), page


def test_the_card_payload_carries_both_texts(tmp_path, monkeypatch):
    """services/trials puts the full before and after texts on the card."""
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "friday"))
    from agent_friday.services import instruction_files as inf, trials
    inf._reset_caches_for_tests()
    sid = "50_say_before_acting"
    new = inf.shipped_text(sid).replace("Before your FIRST tool call", "NEWLINE. Before your FIRST tool call")
    cand = {"candidate": {"instructions": {sid: new}}, "current": {}}
    ch = trials.card_changes(cand)
    assert ch[0]["before"] == inf.shipped_text(sid) and ch[0]["after"] == new
    assert "+" in ch[0]["diff"] and "NEWLINE" in ch[0]["diff"]
