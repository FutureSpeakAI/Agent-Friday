"""SkillOpt's epoch gate after harness2 adoption HA4 (spec §11.2, HA-E27..E29).

Before: a case whose evaluator raised scored 0.0 for BOTH versions and still
counted in both means; one case was enough; a tie passed as a "marginal pass";
a safety mistake was invisible; and a pass promoted the candidate at once with
no owner decision. These run in CI (tests/unit); the same cases also extend
tests/gauntlet/test_skillopt_run_epoch_promotion_requires_signal.py.
"""
from __future__ import annotations

import pytest

from agent_friday.skillopt_engine import DEFAULT_WEIGHTS, MIN_GRADED_CASES, SkillOptEngine, composite_score


@pytest.fixture
def engine(tmp_path):
    return SkillOptEngine(root=tmp_path / "skillopt")


def _make_candidate(engine, skill_name="demo_skill"):
    baseline = engine.register_skill(skill_name, content="# demo v1\n\nbaseline")
    candidate = engine.register_version(
        skill_name=skill_name, content="# demo v2\n\nuntested candidate",
        parent_version=baseline.version_id, edit_summary="candidate edit",
        edit_source="auto-research",
    )
    return baseline, candidate


def test_a_clear_improvement_is_recommended_not_promoted(engine):
    def honest_evaluator(skill_name, content, case):
        return {"accuracy": 0.5 if "baseline" in content else 0.95}
    engine.evaluator = honest_evaluator
    baseline, candidate = _make_candidate(engine)
    recommended = []
    engine.recommender = lambda name, cand, ep: recommended.append(cand.version_id)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{"inputs": {}}] * 6)
    assert epoch.decision == "recommended"
    assert recommended == [candidate.version_id]
    assert engine.storage("demo_skill").best_version().version_id == baseline.version_id

# ── harness2 adoption HA4 (HA-E27..E29), red first ──────────────────────────
#
# Before: a raising case scored 0.0 for BOTH arms and still counted in both
# means; one case was enough; a tie passed as "marginal"; a safety mistake was
# invisible; and a pass promoted at once with no owner decision.



def test_a_raising_case_is_missing_not_zero(engine):
    def evaluator(skill_name, content, case):
        if case["i"] < 2:
            raise RuntimeError("seat busy")
        return {"accuracy": 1.0 if "untested" in content else 0.5}
    engine.evaluator = evaluator
    _, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{"i": i} for i in range(8)])
    assert epoch.candidate_score == pytest.approx(composite_score({"accuracy": 1.0}, DEFAULT_WEIGHTS))
    assert epoch.baseline_score == pytest.approx(composite_score({"accuracy": 0.5}, DEFAULT_WEIGHTS))
    assert "2/8" in epoch.reason


def test_fewer_than_six_graded_cases_is_inconclusive(engine):
    engine.evaluator = lambda n, content, case: {"accuracy": 1.0 if "untested" in content else 0.1}
    _, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{}] * (MIN_GRADED_CASES - 1))
    assert epoch.decision == "inconclusive"


def test_a_tie_keeps_the_baseline(engine):
    engine.evaluator = lambda n, content, case: {"accuracy": 0.7}
    baseline, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{}] * 8)
    assert epoch.decision == "rejected"
    assert engine.storage("demo_skill").best_version().version_id == baseline.version_id


def test_any_safety_mistake_rejects_whatever_the_scores(engine):
    def evaluator(n, content, case):
        if "untested" in content:
            return {"accuracy": 1.0, "safety_fail": case.get("i") == 3}
        return {"accuracy": 0.2}
    engine.evaluator = evaluator
    _, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{"i": i} for i in range(8)])
    assert epoch.decision == "rejected" and "safety" in epoch.reason


def test_paired_counts_decide_not_means(engine):
    """One huge win cannot outvote five small losses."""
    def evaluator(n, content, case):
        i = case["i"]
        if "untested" in content:
            return {"accuracy": 1.0 if i == 0 else 0.4}
        return {"accuracy": 0.0 if i == 0 else 0.5}
    engine.evaluator = evaluator
    _, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{"i": i} for i in range(6)])
    assert epoch.decision == "rejected"


def test_nothing_but_the_card_reaches_promote():
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "src" / "agent_friday"
    callers = []
    for p in src.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "_promote"
                            and p.name == "skillopt_engine.py"):
                        callers.append(f"{p.name}:{node.name}")
                    if isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "promote_from_card":
                        callers.append(f"{p.name}:{node.name}")
    assert sorted(set(callers)) == ["skillopt_engine.py:promote_from_card", "trials.py:adopt"], callers


def test_an_auto_research_edit_becomes_a_candidate_not_a_version(engine):
    from agent_friday.skillopt_engine import ResearchFinding
    baseline, _ = _make_candidate(engine)
    before = [v.version_id for v in engine.storage("demo_skill").list_versions()]
    sunk = []
    engine.candidate_sink = lambda name, content, parent, summary, finding: sunk.append((name, parent)) or "trial_x"
    f = ResearchFinding(finding_id="f1", skill_name="demo_skill", triggered_at="t", trigger_reason="score_drop",
                        rolling_mean=0.1, best_score=0.5, hypotheses=[],
                        proposed_edits=[{"op": "append", "content": "Check the date first.", "summary": "date"}])
    out = engine.research.apply_finding(f)
    assert out is None
    assert [v.version_id for v in engine.storage("demo_skill").list_versions()] == before
    assert sunk == [("demo_skill", baseline.version_id)]


def test_more_than_ten_percent_missing_is_inconclusive(engine):
    def evaluator(skill_name, content, case):
        if case["i"] < 3:
            raise RuntimeError("seat busy")
        return {"accuracy": 1.0 if "untested" in content else 0.2}
    engine.evaluator = evaluator
    _, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{"i": i} for i in range(20)])
    assert epoch.decision == "inconclusive"


def test_a_safety_mistake_rejects_even_when_the_baseline_made_one_too(engine):
    def evaluator(n, content, case):
        if "untested" in content:
            return {"accuracy": 1.0, "safety_fail": case.get("i") == 2}
        return {"accuracy": 0.2, "safety_fail": case.get("i") == 2}
    engine.evaluator = evaluator
    _, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{"i": i} for i in range(8)])
    assert epoch.decision == "rejected"

def test_a_safety_mistake_counts_even_when_the_baseline_half_is_missing(engine):
    def evaluator(n, content, case):
        if "untested" in content:
            return {"accuracy": 1.0, "safety_fail": case.get("i") == 0}
        if case.get("i") == 0:
            raise RuntimeError("seat busy")
        return {"accuracy": 0.2}
    engine.evaluator = evaluator
    _, candidate = _make_candidate(engine)
    epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[{"i": i} for i in range(12)])
    assert epoch.decision == "rejected" and "safety" in epoch.reason