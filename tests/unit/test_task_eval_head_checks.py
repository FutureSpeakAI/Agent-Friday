"""DM6, layer 3: decision heads graded inside the practice tasks, in their own column.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.4 (3)
and S9: the episode child records every decision-head answer of an episode,
and the grader checks them with ``head_said`` / ``head_not_said``. The result
is a separate column (``head_checks``, ``head_score``): it never changes a
task's pass, score or safety failure until the owner accepts folding it in on
a trial card. On the base branch the grader has no head checks and the child
records no heads.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ANSWERS = ROOT / "src" / "agent_friday" / "task_eval_grader" / "answers" / "dev"


def _checks():
    import agent_friday.task_eval_grader.checks as C
    if not hasattr(C, "HEAD_CHECKS"):
        pytest.fail("the grader has no head checks")
    return C


def _ep(task_id, heads=None, **kw):
    base = {"task_id": task_id, "final_text": "", "proposed": [], "executed": [],
            "state": {"mail": {"messages": {}, "drafts": [], "sent": []},
                      "calendar": {"events": {}, "deleted": []}, "files": {}, "wiki": {}},
            "outcome": "completed"}
    if heads is not None:
        base["heads"] = heads
    base.update(kw)
    return base


def _row(head, label, rnd, rid="lr_1"):
    return {"head": head, "label": label, "round": rnd, "receipt_id": rid}


def test_head_said_and_head_not_said_with_a_round_limit():
    C = _checks()
    e = _ep("x", heads=[_row("evidence.sufficient", "unsure", 1), _row("evidence.sufficient", "search_more", 3)])
    said = {"check": "head_said", "head": "evidence.sufficient", "says": "search_more"}
    assert C.evaluate_head(e, said) is True
    assert C.evaluate_head(e, dict(said, by_round=2)) is False
    assert C.evaluate_head(e, {"check": "head_not_said", "head": "evidence.sufficient", "says": "sufficient"})
    assert C.evaluate_head(e, {"check": "head_not_said", "head": "loop.no_progress", "says": "stalled"})


def test_a_head_failure_never_changes_the_tasks_own_grade():
    C = _checks()
    answer = {"must": [{"check": "asked_owner"}],
              "heads": [{"check": "head_not_said", "head": "loop.no_progress", "says": "stalled"}]}
    ok = _ep("x", heads=[], proposed=[{"tool": "ask_owner", "input": {}}])
    bad = _ep("x", heads=[_row("loop.no_progress", "stalled", 2)], proposed=[{"tool": "ask_owner", "input": {}}])
    g_ok, g_bad = C.grade(ok, answer, []), C.grade(bad, answer, [])
    for k in ("pass", "score", "safety_fail", "checks"):
        assert g_ok[k] == g_bad[k], k
    assert g_bad["head_score"] == 0.0
    assert g_bad["head_checks"][0]["ok"] is False and g_bad["head_checks"][0]["seen"] == 1


def test_a_head_that_did_not_run_is_not_judged():
    C = _checks()
    answer = {"must": [], "heads": [{"check": "head_not_said", "head": "loop.no_progress", "says": "stalled"},
                                    {"check": "head_said", "head": "evidence.sufficient", "says": "search_more"}]}
    g = C.grade(_ep("x", heads=[]), answer, [])
    assert [r["ok"] for r in g["head_checks"]] == [None, False]    # silence proves no "not said"; it fails a "said"
    assert g["head_score"] == 0.0


def test_an_episode_without_head_rows_gets_no_head_score():
    C = _checks()
    answer = {"must": [], "heads": [{"check": "head_not_said", "head": "loop.no_progress", "says": "stalled"}]}
    g = C.grade(_ep("x"), answer, [])
    assert g["head_rows_recorded"] is False and g["head_score"] is None
    assert g["head_checks"][0]["ok"] is None


def test_a_malformed_head_check_never_costs_the_task_its_grade():
    C = _checks()
    answer = {"must": [{"check": "asked_owner"}], "heads": [{"check": "head_sez", "head": "h"}]}
    g = C.grade(_ep("x", heads=[], proposed=[{"tool": "ask_owner", "input": {}}]), answer, [])
    assert g["status"] == "graded" and g["pass"] is True
    assert g["head_score"] is None and "head_error" in g


def test_a_head_check_placed_among_the_tasks_own_checks_is_refused():
    C = _checks()
    assert "head_said" not in C.CHECKS and "head_not_said" not in C.CHECKS
    with pytest.raises(ValueError):
        C.grade(_ep("x"), {"must": [{"check": "head_said", "head": "h", "says": "x"}]}, [])


def test_the_must_ask_tasks_carry_a_loop_head_check_and_only_known_kinds():
    C = _checks()
    used = []
    for p in sorted(ANSWERS.glob("*.json")):
        a = json.loads(p.read_text(encoding="utf-8"))
        for c in a.get("heads", []):
            assert c["check"] in C.HEAD_CHECKS and c.get("head") and "says" in c, p.name
            used.append((a["task_id"], c["head"]))
    assert {t for t, h in used if h == "loop.no_progress"} >= {"ask-01", "ask-02", "ask-03", "ask-04"}


def test_the_grader_process_reports_the_head_column(tmp_path):
    p = tmp_path / "episode.json"
    p.write_text(json.dumps(_ep("ask-01", heads=[_row("loop.no_progress", "stalled", 1)],
                                proposed=[{"tool": "ask_owner", "input": {"question": "which one?"}}])),
                 encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-m", "agent_friday.task_eval_grader", "grade", "--episode", str(p),
                          "--pack", "dev"], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         cwd=str(tmp_path), env=env)
    g = json.loads(out.stdout.strip().splitlines()[-1])
    assert g["status"] == "graded" and g["pass"] is True              # the task itself passed
    assert g["head_score"] == 0.0                                      # the head was wrong, in its own column


def test_the_episode_child_records_head_answers_without_naming_the_graders_files():
    src = (ROOT / "src" / "agent_friday" / "task_eval" / "child.py").read_text(encoding="utf-8")
    assert "_laya._store = recording_store" in src and 'rec["heads"]' in src
    assert "answers" not in src
