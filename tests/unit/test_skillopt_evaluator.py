"""SkillOpt's scorer on practice tasks, and the card instead of a silent promotion.

The evaluator runs one practice episode per
case with the version's text as the skill; a case that cannot be graded RAISES
(so run_epoch counts it missing); a recommendation becomes a screen-only trial
card and only the owner's click makes it the champion; an auto-research edit
becomes a queued trial, not a version.
"""
from __future__ import annotations

import pytest
from tests.desk_press_support import pressed      # "the click" is a verified press on this PC (services/desk_press)

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
    with pressed(): approvals.decide(cards[0]["approval_id"], "approve", decided_by="owner:ui")
    assert eng.storage("demo").best_version().version_id == cand.version_id


def test_an_auto_research_edit_is_queued_for_a_trial(home):
    from agent_friday.services import skillopt_evaluator as sev, trials
    eng = home
    base = eng.register_skill("demo", content="# demo v1")
    cid = sev.queue_finding("demo", "# demo v1\n\nCheck the date first.", base.version_id, "date check", None)
    cand = trials.load(cid)
    assert cand["status"] == "queued" and cand["origin"] == "skillopt_finding"
    assert [v.version_id for v in eng.storage("demo").list_versions()] == [base.version_id]


def test_every_episode_asks_the_night_gate_first(home):
    """Battery, tonight's cap used up or an interactive turn: the next case is
    missing and no episode starts."""
    from agent_friday.services import skillopt_evaluator as sev
    run_one = _fake_run(lambda tid, c: {"status": "graded", "pass": True, "score": 1.0})
    why = {"now": ""}
    spent = []
    ev = sev.make_evaluator(run_one, gate=lambda: why["now"], spend=spent.append)
    ev("demo", "# v2", {"task": {"id": "t1"}})
    why["now"] = "on battery"
    with pytest.raises(sev.EvalMissing):
        ev("demo", "# v2", {"task": {"id": "t2"}})
    assert [t for t, _ in run_one.seen] == ["t1"]
    assert spent == [12.0]


def test_the_night_gate_refuses_battery_and_a_used_cap(monkeypatch):
    from agent_friday.services import skillopt_evaluator as sev, trial_job, trials
    monkeypatch.setattr(trial_job, "_gate_blocks", lambda: "")
    monkeypatch.setattr(trials, "night_seconds_used", lambda: 0.0)
    monkeypatch.setattr(trials, "on_ac_power", lambda: False)
    assert sev.night_gate() == "on battery"
    monkeypatch.setattr(trials, "on_ac_power", lambda: True)
    assert sev.night_gate() == ""
    monkeypatch.setattr(trials, "night_seconds_used", lambda: float(trials.NIGHT_CAP_SECONDS))
    assert "used up" in sev.night_gate()


def test_the_nightly_skillopt_run_is_a_visible_job_and_skips_on_battery(monkeypatch):
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services"
           / "notifications.py").read_text(encoding="utf-8")
    head = src.split("def _skillopt_nightly():", 1)[0].rstrip().splitlines()[-1]
    assert head.startswith("@visible_job(") and "kind='skillopt'" in head
    from agent_friday.services import notifications, skillopt_evaluator as sev
    monkeypatch.setattr(sev, "night_gate", lambda: "on battery")
    monkeypatch.setattr(sev, "install", lambda **k: pytest.fail("must not wire the scorer on battery"))
    out = notifications._skillopt_nightly.__wrapped__() if hasattr(notifications._skillopt_nightly, "__wrapped__") \
        else notifications._skillopt_nightly()
    assert out["skipped"] == "on battery"


def test_a_closed_evaluator_runs_nothing(home):
    from agent_friday.services import skillopt_evaluator as sev
    run_one = _fake_run(lambda tid, c: {"status": "graded", "pass": True, "score": 1.0})
    ev = sev.make_evaluator(run_one)
    ev.close()
    ev.close()
    with pytest.raises(sev.EvalMissing):
        ev("demo", "# v2", {"task": {"id": "t1"}})
    assert run_one.seen == []
