"""DM6: the old-versus-new trial for a decision head's thresholds.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.3
(subject kind decision_head_threshold). Same scorer, new numbers: arm A is the
thresholds in force and arm B the candidate, both replayed from the scores the
receipts logged (no model, no GPU). B is recommended only if wrong acts on gold
rows do not rise AND misses fall by at least one AND no regression task newly
fails. Otherwise keep current; ties go to current. The module reads and
reports; it can set nothing. On the base branch there is no
services/decision_head_trial.py and the fixture fails with that.
"""
from __future__ import annotations

import importlib
import re
import time
from pathlib import Path

import pytest

HEAD = "evidence.holds"          # yes/no head: acts (says True) at p >= act
TH = {"act": 0.70, "abstain": 0.30}


@pytest.fixture
def dt():
    try:
        return importlib.import_module("agent_friday.services.decision_head_trial")
    except ImportError as e:
        pytest.fail("services/decision_head_trial does not exist (%s)" % e)


def _row(rid, p):
    return {"receipt_id": rid, "head": HEAD, "value": None, "p": p, "layer": "t1", "latency_ms": 10.0}


def _gold(**by_rid):
    return {(rid, HEAD): g for rid, g in by_rid.items()}


def _verdict(dt, rows, gold, candidate, regression=None, current=TH):
    return dt.verdict(HEAD, current=current, candidate=candidate, rows=rows, labels=None,
                      gold=gold, regression=regression if regression is not None else {})


BASE_ROWS = [_row("r1", 0.60), _row("r2", 0.90), _row("r3", 0.20)]
BASE_GOLD = _gold(r1=True, r2=True, r3=False)


def test_a_candidate_that_removes_a_miss_without_a_new_wrong_act_is_recommended(dt):
    v = _verdict(dt, BASE_ROWS, BASE_GOLD, {"act": 0.55})
    assert v["recommend"] == "candidate"
    assert v["arms"]["current"]["miss"] == 1 and v["arms"]["candidate"]["miss"] == 0
    assert v["arms"]["candidate"]["wrong"] <= v["arms"]["current"]["wrong"]
    assert v["subject"] == {"kind": "decision_head_threshold", "id": HEAD}
    assert v["rule"].startswith("decision-head-trial-rule-")


def test_a_new_wrong_act_keeps_current_even_when_a_miss_goes_away(dt):
    rows = BASE_ROWS + [_row("r4", 0.60)]
    v = _verdict(dt, rows, {**BASE_GOLD, **_gold(r4=False)}, {"act": 0.55})
    assert v["arms"]["candidate"]["wrong"] == 1 and v["arms"]["candidate"]["miss"] == 0
    assert v["recommend"] == "current"


def test_a_tie_goes_to_current(dt):
    v = _verdict(dt, BASE_ROWS, BASE_GOLD, {"act": 0.65})       # r1 (0.60) still under the bar
    assert v["arms"]["candidate"]["miss"] == v["arms"]["current"]["miss"] == 1
    assert v["arms"]["candidate"]["wrong"] == v["arms"]["current"]["wrong"]
    assert v["recommend"] == "current"


def test_fewer_wrong_acts_alone_is_not_enough(dt):
    """The rule asks misses to fall by at least one; a candidate that only
    stops a wrong act (raising the bar) leaves the miss count where it was."""
    rows = [_row("a", 0.80), _row("b", 0.90)]
    v = _verdict(dt, rows, _gold(a=False, b=True), {"act": 0.85})
    assert v["arms"]["current"]["wrong"] == 1 and v["arms"]["candidate"]["wrong"] == 0
    assert v["recommend"] == "current"


def test_rows_with_no_gold_never_decide_a_trial(dt):
    v = _verdict(dt, BASE_ROWS, {}, {"act": 0.55})
    assert v["recommend"] == "current"
    assert v["arms"]["candidate"]["miss"] == 0 and v["arms"]["current"]["miss"] == 0


def test_a_candidate_may_only_carry_the_three_numbers(dt):
    for bad in ({"mode": "act"}, {"direction": "stop_only"}, {"act": 0.5, "scorer": "other"}):
        v = _verdict(dt, BASE_ROWS, BASE_GOLD, bad)
        assert v["recommend"] == "current", bad
        assert v.get("refused"), bad


def test_a_lower_abstain_on_a_triage_head_cannot_be_replayed_and_is_refused(dt):
    row = {"receipt_id": "m1", "head": "triage.mail", "value": "career", "p": 0.4, "layer": "t1", "latency_ms": 5.0}
    v = dt.verdict("triage.mail", current={"act": 0.6, "abstain": 0.35}, candidate={"abstain": 0.2},
                   rows=[row], labels=["career", "other"], gold={("m1", "triage.mail"): "career"},
                   regression={})
    assert v["recommend"] == "current" and "abstain" in v["refused"]


def test_without_a_regression_set_the_answer_is_keep_current(dt):
    v = dt.verdict(HEAD, current=TH, candidate={"act": 0.55}, rows=BASE_ROWS, labels=None,
                   gold=BASE_GOLD, regression=None)
    assert v["recommend"] == "current"
    assert "regression" in v["reason"]


def test_a_regression_task_that_newly_fails_blocks_the_candidate(dt):
    reg = {"t_ok": [_row("t1", 0.90)],
           "t_breaks": [_row("t2", 0.60)]}                      # at 0.55 this acts; gold says it should not
    gold = {**BASE_GOLD, **_gold(t1=True, t2=False)}
    v = _verdict(dt, BASE_ROWS, gold, {"act": 0.55}, regression=reg)
    assert v["arms"]["candidate"]["miss"] < v["arms"]["current"]["miss"]
    assert v["regression"]["new_failures"] == ["t_breaks"]
    assert v["recommend"] == "current"


def test_a_clean_regression_set_lets_the_candidate_through(dt):
    reg = {"t_ok": [_row("t1", 0.90)]}
    v = _verdict(dt, BASE_ROWS, {**BASE_GOLD, **_gold(t1=True)}, {"act": 0.55}, regression=reg)
    assert v["regression"]["new_failures"] == []
    assert v["recommend"] == "candidate"


def test_the_verdict_holds_counts_and_receipt_ids_never_text(dt):
    v = _verdict(dt, BASE_ROWS + [_row("r4", 0.60)], {**BASE_GOLD, **_gold(r4=False)}, {"act": 0.55})
    assert v["arms"]["candidate"]["fail_ids"] == ["r4"]          # a receipt id, nothing else
    assert not {"text", "message", "subject_text", "body"} & set(v)
    assert all(isinstance(i, str) for i in v["arms"]["candidate"]["fail_ids"])


# ── run(): the window and the gold come from the real stores ───────────────

NOW = time.mktime(time.strptime("2026-10-29T13:00:00", "%Y-%m-%dT%H:%M:%S"))


def _receipt(rid, p, at):
    return {"receipt_id": rid, "at": at, "latency_ms": 12.0, "text_sha": rid,
            "versions": {"heads": {"evidence.sufficient": "v1-test"}},
            "questions": {"evidence.sufficient": {"preset": "evidence.sufficient", "layer": "t1",
                                                  "value": "x", "p": p, "why": "x"}}}


@pytest.fixture
def stores(dt, tmp_path, monkeypatch):
    import json
    from agent_friday.services import decision_heads, decision_replay as dr, laya, off_record
    monkeypatch.setattr(dr, "_friday_dir", lambda: tmp_path)
    monkeypatch.setattr(laya, "log_path", lambda: tmp_path / "runtime" / "laya2" / "ask.jsonl")
    monkeypatch.setattr(off_record, "active", lambda: False)
    decision_heads._reset_caches_for_tests()
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"thresholds": dict(TH), "source": "shipped"} if hid == HEAD else None)
    monkeypatch.setattr(decision_heads, "shipped",
                        lambda hid: {"labels": [], "thresholds": dict(TH)} if hid == HEAD else None)

    def put(recs, golds):
        p = tmp_path / "runtime" / "laya2" / "ask.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs), encoding="utf-8")
        for rid, g in golds.items():
            assert dr.record_gold(rid, HEAD, g, "answer_key", "key-" + rid)
    yield put
    decision_heads._reset_caches_for_tests()


def test_run_reads_the_last_28_days_only_and_only_gold_rows_count(dt, stores):
    inside = "2026-10-25T11:00:00"
    outside = "2026-09-20T11:00:00"                              # 39 days back
    stores([_receipt("r1", 0.60, inside), _receipt("r2", 0.90, inside), _receipt("r3", 0.20, inside),
            _receipt("old", 0.60, outside)],                      # a wrong act at the candidate, but too old
           {"r1": True, "r2": True, "r3": False, "old": False})
    v = dt.run(HEAD, {"act": 0.55}, now=NOW, regression={})
    assert v["window_days"] == 28
    assert v["arms"]["current"]["n"] == 3
    assert v["recommend"] == "candidate"


def test_the_module_can_set_nothing_and_never_asks_to_(dt):
    src = Path(dt.__file__).read_text("utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for banned in (r"\brecord_gold\(", r"\._write\(", r"\bpropose\(", r"\bcard_valid\(", r"\bcard_payload\(",
                   r"\bsign\(", r"_append_history", r"\bsave\("):
        assert not re.search(banned, code), banned
