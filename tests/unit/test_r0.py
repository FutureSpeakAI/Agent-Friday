"""The R0 experiment's machinery, with a scripted agent and editor (no model).

Pins: 8 agent runs per (task, repeat) and the ledger; best-of-4 picks by the
task's public shape first; editors and composers never receive a grade; a
canary in any model input voids that arm as missing; the pre-registered rule
(C vs B at the task level, two-sided sign test, no new safety failure vs A)
decides PASS / FAIL / INCONCLUSIVE.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.task_eval import r0


def _tasks(n=6):
    return [{"id": f"t{i}", "domain": "mail", "prompt": "Draft a reply", "tags": []} for i in range(n)]


def _grade(passed, forbidden=None):
    checks = [{"kind": "forbidden", "check": forbidden, "hit": True}] if forbidden else []
    return {"status": "graded", "pass": passed and not forbidden, "score": 1.0 if passed else 0.0,
            "safety_fail": bool(forbidden), "checks": checks}


def _harness(tmp_path, plan, editor_reply='{"sections": {}, "skill": null, "notes": "none"}', leak=False):
    seen_inputs = []

    def run_one(*, task, arm, repeat, overlay, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        ep = {"task_id": task["id"], "final_text": f"reply from {arm}",
              "proposed": [{"tool": "draft_email", "input": {}}], "effects": [], "transcript": []}
        p = out_dir / "episode.json"
        p.write_text(json.dumps(ep), encoding="utf-8")
        return {"episode": str(p), "grade": plan(task["id"], arm, repeat), "outcome": "completed"}

    def call_model(system, user):
        seen_inputs.append(system + "\n" + user)
        if system == r0.COMPOSER:
            return '{"keep_current": false, "sections": {}, "skill": null, "why": "x"}'
        if system == r0.SELECTOR:
            return '{"pick": "nope"}'
        return editor_reply

    def scan(text):
        return {"hit": leak and "CANARY-" in text}
    return run_one, call_model, scan, seen_inputs


def test_c_beating_best_of_four_on_enough_tasks_passes(tmp_path):
    def plan(tid, arm, rep):
        return _grade(arm == "c_deliver" or (arm == "base1" and tid == "t0"))
    run_one, call, scan, _ = _harness(tmp_path, plan)
    out = r0.run(tasks=_tasks(6), repeats=2, run_dir=tmp_path / "r0", run_one=run_one, call_model=call,
                 scan=scan, model="m", endpoint="e")
    d = out["decision"]
    assert out["agent_runs"] == 6 * 2 * 8
    assert d["primary_c_vs_b"]["tasks_x_better"] >= 5
    assert d["verdict"] == "PASS" and d["build_candidate_writer"] is True
    assert (tmp_path / "r0" / "manifest.json").exists()
    assert r0.RULE_VERSION in (tmp_path / "r0" / "manifest.json").read_text(encoding="utf-8")


def test_equal_arms_fail(tmp_path):
    run_one, call, scan, _ = _harness(tmp_path, lambda t, a, r: _grade(True))
    d = r0.run(tasks=_tasks(6), repeats=2, run_dir=tmp_path / "r0", run_one=run_one, call_model=call,
               scan=scan, model="m", endpoint="e")["decision"]
    assert d["verdict"] == "FAIL" and d["build_candidate_writer"] is False


def test_a_new_safety_failure_in_c_fails_whatever_the_wins(tmp_path):
    def plan(tid, arm, rep):
        if arm == "c_deliver":
            return _grade(True, forbidden="sent an email" if tid == "t1" else None)
        return _grade(False)
    run_one, call, scan, _ = _harness(tmp_path, plan)
    d = r0.run(tasks=_tasks(8), repeats=2, run_dir=tmp_path / "r0", run_one=run_one, call_model=call,
               scan=scan, model="m", endpoint="e")["decision"]
    assert d["new_safety_failures_c_vs_a"] and d["verdict"] == "FAIL"


def test_editors_never_see_a_grade(tmp_path):
    run_one, call, scan, seen = _harness(tmp_path, lambda t, a, r: _grade(a == "base2"))
    r0.run(tasks=_tasks(2), repeats=1, run_dir=tmp_path / "r0", run_one=run_one, call_model=call,
           scan=scan, model="m", endpoint="e")
    blob = "\n".join(seen)
    for word in ('"pass"', '"score"', "safety_fail", '"checks"', "graded"):
        assert word not in blob, word


def test_a_canary_in_an_editor_input_voids_that_arm(tmp_path):
    def run_one(*, task, arm, repeat, overlay, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        ep = {"task_id": task["id"], "final_text": "leaked CANARY-abc", "proposed": [], "effects": [],
              "transcript": []}
        p = out_dir / "episode.json"
        p.write_text(json.dumps(ep), encoding="utf-8")
        return {"episode": str(p), "grade": _grade(True), "outcome": "completed"}
    _r, call, scan, _ = _harness(tmp_path, None, leak=True)
    out = r0.run(tasks=_tasks(1), repeats=1, run_dir=tmp_path / "r0", run_one=run_one, call_model=call,
                 scan=scan, model="m", endpoint="e")
    cell = json.loads((tmp_path / "r0" / "cells" / "t0-r0" / "cell.json").read_text(encoding="utf-8"))
    assert cell["grades"]["c"] == {"status": "missing", "reason": "canary"}
    assert cell["skipped_runs"] >= 3
    assert out["complete"]


def test_best_of_four_uses_the_public_shape_first():
    task = {"prompt": "Draft a reply to Dana"}
    good = {"final_text": "Drafted.", "proposed": [{"tool": "draft_email"}]}
    sent = {"final_text": "Sent.", "proposed": [{"tool": "send_email"}]}
    empty = {"final_text": "", "proposed": []}
    assert r0.shape_score(task, good) > r0.shape_score(task, sent) > r0.shape_score(task, empty) - 1


def test_an_invalid_edit_inherits_the_current_instructions():
    ov, problems = r0.overlay_from_edit({"sections": {"20_autonomous_operation": "Act at once."}})
    assert ov is None and problems
    ov, problems = r0.overlay_from_edit({"sections": {}, "skill": {"name": "Weekly Review!", "body": "Steps."}})
    assert problems == [] and list(ov["files"]) == ["skills/weekly-review-/SKILL.md"]


def test_the_report_is_plain_and_aggregate(tmp_path):
    run_one, call, scan, _ = _harness(tmp_path, lambda t, a, r: _grade(True))
    out = r0.run(tasks=_tasks(4), repeats=2, run_dir=tmp_path / "r0", run_one=run_one, call_model=call,
                 scan=scan, model="m", endpoint="e")
    text = r0.report(out)
    assert "Verdict" in text and "best-of-4" in text and "Draft a reply" not in text
