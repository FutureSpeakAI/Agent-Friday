"""A saved workflow's card offers "Its emails: save as drafts / send", rendered under node from the
served index.html (no browser). Synthetic data only."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _extract(src, start):
    if start not in src:
        pytest.fail("the page has no %s" % start)
    i = src.index(start)
    depth, j = 0, src.index("{", src.index(")", i))
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(start)


HARNESS = """
let calls = [];
const React = {createElement: (t, p, ...c) => ({t, p: p || {}, c: c.flat(Infinity).filter(x => x !== null && x !== false && x !== undefined)})};
const useState = v => [typeof v === 'function' ? v() : v, () => {}];
const apiFetch = (u, o) => { calls.push([u, o && o.method, o && JSON.parse(o.body || 'null')]); return Promise.resolve({json: () => ({status: 'ok'})}); };
const text = n => typeof n === 'string' ? n : (n && n.c || []).map(text).join('');
const all = (n, f, out = []) => { if (n && typeof n === 'object') { if (f(n)) out.push(n); (n.c || []).forEach(x => all(x, f, out)); } return out; };
"""


@pytest.mark.skipif(not NODE, reason="node is not on PATH")
def test_an_unscheduled_workflow_card_shows_its_email_choice_and_patches_the_workflow():
    body = _extract(SRC, "function WfEmailChoice(") + """
const kept = WfEmailChoice({w: {slug: 'weekly-status', schedule_id: null, email_send: true}});
const sel = all(kept, n => n.t === 'select')[0];
const opts = all(sel, n => n.t === 'option').map(o => [o.p.value, text(o)]);
sel.p.onChange({target: {value: 'draft'}});
const fresh = all(WfEmailChoice({w: {slug: 'news-digest'}}), n => n.t === 'select')[0].p.value;
const job = WfEmailChoice({w: {slug: 'weekly-status', schedule_id: 'sch_1', email_send: true}});
console.log(JSON.stringify({value: sel.p.value, label: text(kept), opts, calls, fresh, job}));
"""
    cp = subprocess.run([NODE, "-e", HARNESS + body], capture_output=True, text=True, encoding="utf-8",
                        timeout=60, cwd=str(ROOT))
    assert cp.returncode == 0, cp.stderr[-2000:]
    got = json.loads(cp.stdout.strip().splitlines()[-1])
    assert got["value"] == "send", "a workflow kept sending does not show send"
    assert "Its emails" in got["label"]
    assert [o[0] for o in got["opts"]] == ["draft", "send"]
    assert got["calls"] == [["/api/workflows/chains/weekly-status", "PATCH", {"email_send": False}]]
    assert got["fresh"] == "draft"
    assert got["job"] is None, "a scheduled workflow's card would show two email switches"


@pytest.mark.parametrize("path", ["index.html", "ui_parts/app.html"])
def test_the_workflow_card_places_the_choice_where_a_job_card_has_its_own(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    facts = _extract(src, "function WfScheduleFacts(")
    assert "!w.schedule_id && w.slug ? E(WfEmailChoice, { w: w }) : null" in facts
    assert "if (!s && !w.model && !w.slug) return null;" in facts
    assert _extract(src, "function WfEmailChoice(") == _extract(SRC, "function WfEmailChoice(")
