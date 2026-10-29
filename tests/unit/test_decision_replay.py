"""DM6, layer 2: the nightly replay grades every decision-head receipt of the day.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.4. The
replay uses only what receipts logged (no model), never lets a head grade
itself (gold comes from owner corrections, confirmed choices and answer keys),
and writes counts and receipt ids to an append-only report. On the base branch
there is no services/decision_replay.py and the fixture fails with that.
"""
from __future__ import annotations

import importlib
import json
import time

import pytest


@pytest.fixture
def dr(tmp_path, monkeypatch):
    try:
        mod = importlib.import_module("agent_friday.services.decision_replay")
    except ImportError as e:
        pytest.fail("services/decision_replay does not exist (%s)" % e)
    from agent_friday.services import decision_heads, laya
    monkeypatch.setattr(mod, "_friday_dir", lambda: tmp_path)
    monkeypatch.setattr(laya, "log_path", lambda: tmp_path / "runtime" / "laya2" / "ask.jsonl")
    decision_heads._reset_caches_for_tests()
    yield mod
    decision_heads._reset_caches_for_tests()


NOW = time.mktime(time.strptime("2026-10-29T03:00:00", "%Y-%m-%dT%H:%M:%S"))


def _receipt(rid, head, value, p, *, at="2026-10-29T01:00:00", layer="t1", latency=12.0):
    return {"receipt_id": rid, "at": at, "latency_ms": latency, "text_sha": "abc",
            "versions": {"heads": {head: "v1-test"}},
            "questions": {head: {"preset": head, "layer": layer, "value": value, "p": p, "why": "x"}}}


def _write_receipts(tmp_path, recs):
    p = tmp_path / "runtime" / "laya2" / "ask.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


# -- the pure parts -------------------------------------------------------------

@pytest.mark.parametrize("row,state", [
    ({"value": "career", "p": 0.8, "layer": "t1"}, "act"),
    ({"value": "career", "p": 0.4, "layer": "t1"}, "hold"),
    ({"value": "career", "p": 0.2, "layer": "t1"}, "abstain"),       # under the abstain threshold
    ({"value": "other", "p": 0.9, "layer": "t1"}, "abstain"),        # the abstain label
    ({"value": None, "p": None, "layer": "degraded"}, "abstain"),
])
def test_a_row_is_decided_under_the_thresholds_given(dr, row, state):
    th = {"act": 0.55, "abstain": 0.30}
    assert dr.decide(row, th, ["career", "finance", "other"])["state"] == state


def test_miss_wrong_pass_fail_and_uncertain_are_counted_from_gold_only(dr):
    rows = [{"receipt_id": "a", "head": "h", "value": "career", "p": 0.8, "layer": "t1"},    # act, right
            {"receipt_id": "b", "head": "h", "value": "career", "p": 0.8, "layer": "t1"},    # act, wrong
            {"receipt_id": "c", "head": "h", "value": "career", "p": 0.4, "layer": "t1"},    # hold, right: a miss
            {"receipt_id": "d", "head": "h", "value": "other", "p": 0.9, "layer": "t1"},     # abstain, gold career: a miss
            {"receipt_id": "e", "head": "h", "value": "career", "p": 0.8, "layer": "t1"}]    # no gold: uncertain
    gold = {("a", "h"): "career", ("b", "h"): "finance", ("c", "h"): "career", ("d", "h"): "career"}
    r = dr.replay_head(rows, {"act": 0.55, "abstain": 0.3}, ["career", "finance", "other"], gold)
    assert r["verdict"] == {"pass": 2, "uncertain": 2, "fail": 1}
    assert r["wrong"] == 1 and r["miss"] == 2
    assert r["take_rate"] == 0.6 and r["fail_ids"] == ["b"]


def test_ece_is_zero_when_confidence_matches_accuracy(dr):
    assert dr.ece([0.75] * 4, [True, True, True, False]) == 0.0
    assert dr.ece([], []) is None


# -- receipts, gold and the report ------------------------------------------------

def test_only_head_questions_inside_the_window_are_replayed(dr, tmp_path):
    _write_receipts(tmp_path, [
        _receipt("r1", "triage.mail", "career", 0.8),
        _receipt("r2", "triage.mail", "career", 0.8, at="2026-10-27T01:00:00"),        # too old
        {"receipt_id": "r3", "at": "2026-10-29T01:00:00", "questions": {"route": {"value": "x", "p": 0.9}}},
    ])
    got = dr.rows(now=NOW)
    assert [(r["receipt_id"], r["head"]) for r in got] == [("r1", "triage.mail")]


def test_gold_comes_only_from_the_owner_or_an_answer_key(dr, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda: False)
    assert dr.record_outcome("r1", "triage.mail", "career", "owner_correction")
    assert not dr.record_outcome("r1", "triage.mail", "career", "head")           # never the head itself
    assert not dr.record_outcome("r1", "triage.mail", "career", "brain_choice")   # nor an unconfirmed choice
    monkeypatch.setattr(off_record, "active", lambda: True)
    assert not dr.record_outcome("r2", "triage.mail", "career", "answer_key")     # off the record: nothing
    assert dr._gold() == {("r1", "triage.mail"): "career"}


def test_the_report_holds_counts_and_ids_never_words(dr, tmp_path, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda: False)
    _write_receipts(tmp_path, [_receipt("r%d" % i, "triage.mail", "career", 0.8) for i in range(4)])
    dr.record_outcome("r0", "triage.mail", "finance", "owner_correction")
    rep = dr.report(now=NOW)
    h = rep["heads"]["triage.mail"]
    assert h["n"] == 4 and h["by_set"]["shipped"]["take_rate"] == 1.0
    assert h["by_set"]["shipped"]["verdict"] == {"pass": 0, "uncertain": 3, "fail": 1}
    assert h["by_set"]["shipped"]["fail_ids"] == ["r0"]
    blob = json.dumps(rep)
    assert "career" not in blob and "finance" not in blob          # labels stay in the receipts


def test_a_candidate_is_replayed_beside_what_ships(dr, tmp_path):
    _write_receipts(tmp_path, [_receipt("r%d" % i, "triage.mail", "career", 0.6) for i in range(4)])
    rep = dr.report(now=NOW, candidates={"triage.mail": {"act": 0.9, "abstain": 0.3}})
    by = rep["heads"]["triage.mail"]["by_set"]
    assert by["shipped"]["take_rate"] == 1.0 and by["candidate"]["take_rate"] == 0.0


def test_drift_is_flagged_at_ten_points_with_enough_decisions(dr, tmp_path):
    d = dr.bench_dir()
    d.mkdir(parents=True)
    for day in ("2026-10-26", "2026-10-27", "2026-10-28"):
        (d / ("%s.json" % day)).write_text(json.dumps({"heads": {"triage.mail": {
            "n": 10, "by_set": {"shipped": {"take_rate": 0.1}}}}}), encoding="utf-8")
    _write_receipts(tmp_path, [_receipt("r%d" % i, "triage.mail", "career", 0.8) for i in range(5)])
    h = dr.report(now=NOW)["heads"]["triage.mail"]
    assert h["drift"]["flag"] is True and h["drift"]["decisions_in_week"] == 35


def test_reports_are_append_only_and_the_night_runs_once(dr, tmp_path, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda: False)
    a = dr.write({"date": "2026-10-29", "heads": {}})
    b = dr.write({"date": "2026-10-29", "heads": {}})
    assert (a.name, b.name) == ("2026-10-29.json", "2026-10-29-2.json")
    assert dr.run_nightly(now=NOW)["skipped"] == "already replayed today"


def test_off_the_record_the_night_replays_nothing(dr, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda: True)
    assert dr.run_nightly() == {"skipped": "off the record"}
    assert not dr.bench_dir().exists()


def test_the_practice_trials_job_runs_the_replay_and_counts_its_time(dr, monkeypatch):
    from agent_friday.services import lesson_records, r0_nightly, trial_job, trials
    monkeypatch.setattr(lesson_records, "raise_retirement_cards", lambda *a, **k: [])
    seen, spent = [], []
    monkeypatch.setattr(dr, "run_nightly", lambda **k: seen.append(1) or {"written": "x.json", "seconds": 2.5})
    monkeypatch.setattr(trials, "on_ac_power", lambda: True)
    monkeypatch.setattr(trials, "night_seconds_used", lambda now=None: 0.0)
    monkeypatch.setattr(trials, "_spend_night", lambda s: spent.append(s))
    monkeypatch.setattr(trials, "weekly_digest", lambda *a, **k: None)
    monkeypatch.setattr(trials, "candidates", lambda status: [])
    monkeypatch.setattr(r0_nightly, "ensure_scheduled", lambda: None)
    monkeypatch.setattr(r0_nightly, "due", lambda: False)
    out = trial_job.run_pending_trials.__wrapped__()
    assert seen == [1] and spent == [2.5] and out["decision_replay"]["written"] == "x.json"
    assert out["skipped"] == "nothing to test"                      # the trials themselves are unchanged
    seen.clear()
    monkeypatch.setattr(trials, "on_ac_power", lambda: False)
    out = trial_job.run_pending_trials.__wrapped__()
    assert seen == [] and "decision_replay" not in out
