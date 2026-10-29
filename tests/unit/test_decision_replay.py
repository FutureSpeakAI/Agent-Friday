"""DM6, layer 2: the nightly replay grades every decision-head receipt.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.4. The
replay uses only what receipts logged (no model), never lets a head grade
itself (gold comes from owner corrections, confirmed choices and answer keys,
each with a reference), judges miss and wrong on each head's own action, and
writes counts and receipt ids to one report per night. On the base branch
there is no services/decision_replay.py and the fixture fails with that.
"""
from __future__ import annotations

import importlib
import json
import re
import time
from pathlib import Path

import pytest


@pytest.fixture
def dr(tmp_path, monkeypatch):
    try:
        mod = importlib.import_module("agent_friday.services.decision_replay")
    except ImportError as e:
        pytest.fail("services/decision_replay does not exist (%s)" % e)
    from agent_friday.services import decision_heads, laya, off_record
    monkeypatch.setattr(mod, "_friday_dir", lambda: tmp_path)
    monkeypatch.setattr(laya, "log_path", lambda: tmp_path / "runtime" / "laya2" / "ask.jsonl")
    monkeypatch.setattr(off_record, "active", lambda: False)
    decision_heads._reset_caches_for_tests()
    yield mod
    decision_heads._reset_caches_for_tests()


NOW = time.mktime(time.strptime("2026-10-29T13:00:00", "%Y-%m-%dT%H:%M:%S"))
TH = {"act": 0.55, "abstain": 0.30}


def _receipt(rid, head, value, p, *, at="2026-10-29T11:00:00", layer="t1", latency=12.0, sha=None):
    return {"receipt_id": rid, "at": at, "latency_ms": latency, "text_sha": sha or rid,
            "versions": {"heads": {head: "v1-test"}},
            "questions": {head: {"preset": head, "layer": layer, "value": value, "p": p, "why": "x"}}}


def _write_receipts(tmp_path, recs):
    p = tmp_path / "runtime" / "laya2" / "ask.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    # As laya writes them: json.dumps(rec, sort_keys=True)
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs), encoding="utf-8")


def _row(rid, value, p, layer="t1"):
    return {"receipt_id": rid, "head": "h", "value": value, "p": p, "layer": layer}


# -- each head decides as its receipt means ------------------------------------------

@pytest.mark.parametrize("head,value,p,state,label", [
    ("triage.mail", "career", 0.8, "act", "career"),
    ("triage.mail", "career", 0.4, "hold", "career"),
    ("triage.mail", "career", 0.2, "abstain", "other"),
    ("triage.mail", "other", 0.9, "abstain", "other"),
    ("triage.notify", "fyi", 0.9, "hold", "fyi"),                 # fyi is not the notify head's action
    ("triage.notify", "needs_you", 0.9, "act", "needs_you"),
    ("evidence.sufficient", "search_more", 0.1, "act", "search_more"),   # its add-only action
    ("evidence.sufficient", "unsure", 0.5, "abstain", "unsure"),
    ("evidence.supports", "does_not_address", 0.2, "act", "does_not_address"),
    ("evidence.holds", False, 0.1, "hold", False),
    ("evidence.holds", True, 0.995, "act", True),
    ("loop.no_progress", "stalled", None, "act", "stalled"),
    ("loop.no_progress", "progressing", None, "hold", "progressing"),
    ("triage.mail", None, None, "degraded", None),
])
def test_each_head_is_decided_from_what_its_receipt_means(dr, head, value, p, state, label):
    from agent_friday.services import decision_heads
    spec = decision_heads.shipped(head)
    th = TH if head.startswith("triage") else spec["thresholds"]
    layer = "degraded" if value is None else "t1"
    d = dr.decide({"value": value, "p": p, "layer": layer}, head, th, spec["labels"] if "labels" in spec else None)
    assert (d["state"], d["label"]) == (state, label)


def test_miss_and_wrong_are_judged_on_the_heads_action(dr):
    gold = {("a", "h"): "needs_you", ("b", "h"): "fyi", ("c", "h"): "needs_you", ("d", "h"): "fyi"}
    rows = [_row("a", "needs_you", 0.9),       # acted, right
            _row("b", "needs_you", 0.9),       # acted, wrong
            _row("c", "fyi", 0.9),             # held fyi, but it needed the owner: a miss
            _row("d", "fyi", 0.9)]             # held fyi, rightly: no miss
    r = dr.replay_head(rows, "triage.notify", {"act": 0.7, "abstain": 0.55}, ["needs_you", "fyi", "other"], gold)
    assert r["verdict"] == {"pass": 2, "uncertain": 0, "fail": 2}
    assert (r["wrong"], r["miss"]) == (1, 1)
    assert r["take_rate"] == 0.5 and r["fail_ids"] == ["b", "c"]


def test_the_loop_head_is_graded_by_its_label_and_only_a_due_stop_is_a_miss(dr):
    gold = {("a", "h"): "stalled", ("b", "h"): "progressing", ("c", "h"): "stalled"}
    rows = [_row("a", "stalled", None), _row("b", "progressing", None), _row("c", "progressing", None)]
    r = dr.replay_head(rows, "loop.no_progress", {"act": 0.5, "abstain": 0.2}, ["progressing", "stalled", "unsure"], gold)
    assert r["verdict"] == {"pass": 2, "uncertain": 0, "fail": 1} and (r["wrong"], r["miss"]) == (0, 1)


def test_a_correct_no_from_a_yes_no_head_is_not_a_miss(dr):
    gold = {("a", "h"): False, ("b", "h"): True}
    rows = [_row("a", False, 0.1), _row("b", False, 0.1)]
    r = dr.replay_head(rows, "evidence.holds", {"act": 0.99, "abstain": 0.32}, None, gold)
    assert (r["verdict"]["pass"], r["miss"]) == (1, 1)


def test_ece_uses_the_probability_each_head_logs(dr):
    # evidence.sufficient logs P(holds): ECE asks how often gold was "sufficient" at that p.
    gold = {("a", "h"): "sufficient", ("b", "h"): "search_more"}
    r = dr.replay_head([_row("a", "unsure", 0.5), _row("b", "unsure", 0.5)], "evidence.sufficient",
                       {"act": 0.99, "abstain": 0.32}, None, gold)
    assert r["ece"] == 0.0 and r["n_gold_scored"] == 2


def test_a_candidate_that_loosens_abstain_on_a_triage_head_is_refused(dr):
    assert dr._candidate_refusal("triage.mail", {"abstain": 0.3}, {"act": 0.5, "abstain": 0.1})
    assert dr._candidate_refusal("triage.mail", {"abstain": 0.3}, {"act": 0.8, "abstain": 0.4}) is None
    assert dr._candidate_refusal("loop.no_progress", {}, {"act": 0.4})


# -- receipts, gold and the report ------------------------------------------------

def test_only_head_questions_inside_the_window_are_read_once_per_text(dr, tmp_path):
    _write_receipts(tmp_path, [
        _receipt("r1", "triage.mail", "career", 0.8, sha="s1"),
        _receipt("r1b", "triage.mail", "career", 0.9, sha="s1", at="2026-10-29T11:30:00"),   # the same text again
        _receipt("r2", "triage.mail", "career", 0.8, at="2026-10-27T01:00:00"),           # too old
        {"receipt_id": "r3", "at": "2026-10-29T11:00:00", "questions": {"route": {"value": "x", "p": 0.9}}},
    ])
    got = dr.rows(NOW - 24 * 3600, NOW)
    assert [(r["receipt_id"], r["head"]) for r in got] == [("r1b", "triage.mail")]


def test_gold_needs_an_allowed_source_a_reference_and_a_real_label(dr, monkeypatch):
    from agent_friday.services import off_record
    assert dr.record_outcome("r1", "triage.mail", "career", "owner_correction", "card-1")
    assert not dr.record_outcome("r1", "triage.mail", "career", "owner_correction", "")      # no reference
    assert not dr.record_outcome("r1", "triage.mail", "career", "head", "x")                  # never the head
    assert not dr.record_outcome("r1", "triage.mail", "the owner's own words", "answer_key", "t1")
    assert not dr.record_outcome("r1", "evidence.holds", "yes", "answer_key", "t1")          # a yes/no head: a bool
    monkeypatch.setattr(off_record, "active", lambda: True)
    assert not dr.record_outcome("r2", "triage.mail", "career", "answer_key", "t1")          # off the record
    assert dr._gold() == {("r1", "triage.mail"): "career"}


def test_no_head_and_not_laya_ever_writes_gold():
    root = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services"
    for f in [root / "laya.py", root / "decision_heads.py"] + sorted(root.glob("*_heads.py")):
        assert "record_outcome" not in f.read_text(encoding="utf-8"), f.name


def test_the_report_holds_counts_and_ids_never_words(dr, tmp_path):
    _write_receipts(tmp_path, [_receipt("r%d" % i, "triage.mail", "career", 0.8) for i in range(4)])
    dr.record_outcome("r0", "triage.mail", "finance", "owner_correction", "card-9")
    rep = dr.report(now=NOW)
    h = rep["heads"]["triage.mail"]
    assert h["n"] == 4 and h["by_set"]["shipped"]["take_rate"] == 1.0
    assert h["by_set"]["shipped"]["verdict"] == {"pass": 0, "uncertain": 3, "fail": 1}
    blob = json.dumps(rep)
    assert not re.search(r"career|finance|card-9", blob)


def test_a_candidate_is_replayed_beside_what_ships(dr, tmp_path):
    _write_receipts(tmp_path, [_receipt("r%d" % i, "triage.mail", "career", 0.6) for i in range(4)])
    rep = dr.report(now=NOW, candidates={"triage.mail": {"act": 0.9, "abstain": 0.3}})
    by = rep["heads"]["triage.mail"]["by_set"]
    assert by["shipped"]["take_rate"] == 1.0 and by["candidate"]["take_rate"] == 0.0


def _night_file(dr, night, n, rate, until):
    d = dr.bench_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.json" % night)).write_text(json.dumps({"night": night, "until": until, "heads": {
        "triage.mail": {"n": n, "by_set": {"shipped": {"take_rate": rate}}}}}), encoding="utf-8")


def test_drift_is_flagged_and_a_rerun_is_not_counted_twice(dr, tmp_path):
    for i, night in enumerate(("2026-10-26", "2026-10-27", "2026-10-28")):
        _night_file(dr, night, 10, 0.1, NOW - (3 - i) * 86400)
    (dr.bench_dir() / "2026-10-28-2.json").write_text(json.dumps({"night": "2026-10-28", "heads": {
        "triage.mail": {"n": 100, "by_set": {"shipped": {"take_rate": 0.9}}}}}), encoding="utf-8")
    _write_receipts(tmp_path, [_receipt("r%d" % i, "triage.mail", "career", 0.8) for i in range(5)])
    h = dr.report(now=NOW)["heads"]["triage.mail"]
    assert h["drift"]["flag"] is True and h["drift"]["decisions_in_week"] == 35


def test_the_replay_starts_where_the_last_report_stopped(dr, tmp_path):
    _night_file(dr, "2026-10-27", 1, 0.0, NOW - 2 * 86400)          # last night was skipped
    _write_receipts(tmp_path, [_receipt("old", "triage.mail", "career", 0.8, at="2026-10-28T09:00:00"),
                               _receipt("new", "triage.mail", "career", 0.8)])
    rep = dr.report(now=NOW)
    assert rep["rows"] == 2 and rep["shortfall_s"] == 0


def test_one_report_per_night_and_the_owner_comes_first(dr, monkeypatch):
    first = dr.run_nightly(now=NOW)
    assert first["written"] == "%s.json" % dr.night_key(NOW)
    assert dr.run_nightly(now=NOW)["skipped"] == "already replayed tonight"
    assert dr.run_nightly(now=NOW + 86400, should_stop=lambda: True)["skipped"] == "the owner is busy"
    calls = iter([False, True])
    assert dr.run_nightly(now=NOW + 86400, should_stop=lambda: next(calls, True))["skipped"] \
        == "the owner started a turn"


def test_off_the_record_the_night_replays_nothing(dr, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda: True)
    assert dr.run_nightly() == {"skipped": "off the record"}
    assert not dr.bench_dir().exists()


def test_the_practice_trials_job_runs_the_replay_and_counts_its_time(dr, monkeypatch):
    from agent_friday.services import lesson_records, r0_nightly, trial_job, trials
    monkeypatch.setattr(lesson_records, "raise_retirement_cards", lambda *a, **k: [])
    seen, spent = [], []
    monkeypatch.setattr(dr, "run_nightly", lambda **k: seen.append(k) or {"written": "x.json"})
    monkeypatch.setattr(trials, "on_ac_power", lambda: True)
    monkeypatch.setattr(trials, "night_seconds_used", lambda now=None: 0.0)
    monkeypatch.setattr(trials, "_spend_night", lambda s: spent.append(s))
    monkeypatch.setattr(trials, "weekly_digest", lambda *a, **k: None)
    monkeypatch.setattr(trials, "candidates", lambda status: [])
    monkeypatch.setattr(r0_nightly, "ensure_scheduled", lambda: None)
    monkeypatch.setattr(r0_nightly, "due", lambda: False)
    out = trial_job.run_pending_trials.__wrapped__()
    assert len(seen) == 1 and callable(seen[0].get("should_stop")) and len(spent) == 1
    assert out["decision_replay"]["written"] == "x.json" and out["skipped"] == "nothing to test"
    seen.clear()
    monkeypatch.setattr(trials, "on_ac_power", lambda: False)
    out = trial_job.run_pending_trials.__wrapped__()
    assert seen == [] and "decision_replay" not in out
