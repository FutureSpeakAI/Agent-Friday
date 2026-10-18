"""SkillOpt's scorer on practice tasks, and the card instead of a silent promotion.

harness2 adoption HA4 (spec §11): the evaluator runs one practice episode per
case with the version's text as the skill; a case that cannot be graded RAISES
(so run_epoch counts it missing); a recommendation becomes a screen-only trial
card and only the owner's click makes it the champion; an auto-research edit
becomes a queued trial, not a version.
"""
from __future__ import annotations

import pytest

KEY = b"g" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    friday = tmp_path / "friday"
    friday.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(friday))
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", friday / "approvals.json")
    approvals._write_store([])
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    import agent_friday.skillopt_engine as sopt
    eng = sopt.SkillOptEngine(root=tmp_path / "skillopt")
    monkeypatch.setattr(sopt, "_engine_singleton", eng)
    return eng


def _fake_run(plan):
    seen = []

    def run_one(*, task, arm, repeat, overlay, out_dir):
        content = list(overlay["files"].values())[0]
        seen.append((task["id"], content))
        return {"grade": plan(task["id"], content), "wall_seconds": 12.0}
    run_one.seen = seen
    return run_one


def test_the_evaluator_runs_the_version_as_the_skill_and_scores_it(home):
    from agent_friday.services import skillopt_evaluator as sev
    run_one = _fake_run(lambda tid, c: {"status": "graded", "pass": "v2" in c, "score": 0.5})
    ev = sev.make_evaluator(run_one)
    m = ev("demo", "# v2", {"task": {"id": "t1"}})
    assert m == {"accuracy": 1.0, "completeness": 0.5, "latency_ms": 12000.0, "cost_usd": 0.0,
                 "safety_fail": False}
    assert run_one.seen == [("t1", "# v2")]


def test_a_missing_grade_raises_so_the_case_is_missing(home):
    from agent_friday.services import skillopt_evaluator as sev
    ev = sev.make_evaluator(_fake_run(lambda tid, c: {"status": "missing", "reason": "seat busy"}))
    with pytest.raises(sev.EvalMissing):
        ev("demo", "# v2", {"task": {"id": "t1"}})


def test_a_recommendation_is_a_card_and_only_a_click_changes_the_champion(home):
    from agent_friday.services import approvals, skillopt_evaluator as sev
    eng = home
    base = eng.register_skill("demo", content="# demo v1\n\nbaseline")
    cand = eng.register_version("demo", content="# demo v2\n\nbetter", parent_version=base.version_id)
    eng.evaluator = sev.make_evaluator(_fake_run(
        lambda tid, c: {"status": "graded", "pass": "v2" in c, "score": 1.0 if "v2" in c else 0.2}))
    eng.recommender = sev.recommend
    batch = [{"task": {"id": f"t{i}", "domain": "skill", "prompt": "x"}} for i in range(6)]
    epoch = eng.run_epoch("demo", cand.version_id, batch)
    assert epoch.decision == "recommended", epoch.reason
    assert eng.storage("demo").best_version().version_id == base.version_id
    cards = [c for c in approvals.list_approvals(status="pending") if c["kind"] == "instruction_trial"]
    assert len(cards) == 1
    approvals.decide(cards[0]["approval_id"], "approve", decided_by="owner")
    assert eng.storage("demo").best_version().version_id == base.version_id
    approvals.decide(cards[0]["approval_id"], "approve", decided_by="owner:ui")
    assert eng.storage("demo").best_version().version_id == cand.version_id


def test_an_auto_research_edit_is_queued_for_a_trial(home):
    from agent_friday.services import skillopt_evaluator as sev, trials
    eng = home
    base = eng.register_skill("demo", content="# demo v1")
    cid = sev.queue_finding("demo", "# demo v1\n\nCheck the date first.", base.version_id, "date check", None)
    cand = trials.load(cid)
    assert cand["status"] == "queued" and cand["origin"] == "skillopt_finding"
    assert [v.version_id for v in eng.storage("demo").list_versions()] == [base.version_id]
