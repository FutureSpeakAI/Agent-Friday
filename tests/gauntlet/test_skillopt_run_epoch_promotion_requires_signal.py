"""Gauntlet finding F73 (seam 13/SkillOpt, externally sourced):
SkillOptEngine.run_epoch() could promote a candidate skill version that was
never actually evaluated.

An external review of the public v5.10.0 repo raised this without executing
it, so it is reproduced here before being trusted. All four reproductions
confirmed the claim against the code before the fix:

  1. Evaluator raises on EVERY case in the batch -> promoted.
  2. No evaluator configured, and neither candidate nor baseline has any
     prior execution history -> promoted.
  3. Empty eval_batch (zero cases) -> promoted.
  4. (mechanism) Any of the above collapses candidate_score and
     baseline_score to the exact same fallback value -- a tie -- and
     ValidationGate.evaluate()'s own tolerance rule always treats a tie
     as a "marginal pass". A real, working evaluator that finds a genuine
     regression is correctly rejected (see
     test_working_evaluator_with_genuine_regression_is_still_rejected
     below) -- the defect is specifically that "no signal" is
     indistinguishable from "confirmed a tie".

The review also noted `epoch.reason` records the evaluator's exception
message and is then unconditionally overwritten by the gate's own reason
string afterward, so the diagnostic is lost -- confirmed separately below.

Fix (src/agent_friday/skillopt_engine.py, run_epoch()): track whether any
case was genuinely evaluated (evaluator ran without raising, or -- in the
no-evaluator path -- both candidate and baseline actually had prior
accuracy history rather than a defaulted 0.0). An empty batch, or a batch
where zero cases produced real signal, now short-circuits to
decision="inconclusive" (a state the TrainingEpoch dataclass's own
`decision` docstring already listed -- "pending | promoted | rejected |
inconclusive" -- but that run_epoch() never actually produced) with a
reason naming why, and never reaches the gate or _promote() at all. A
partial failure (some cases evaluated, some didn't) still proceeds to the
gate as before, but the reason string now names the failure count and
the last error instead of silently dropping it.

Reachability note, for honest severity: nothing in the
live server wires an evaluator into the one production SkillOptEngine
singleton (get_engine() calls SkillOptEngine() with zero arguments), and
nothing in the currently-wired autoresearch/nightly pipeline
(skill_capture.run_nightly() -> maybe_autoresearch() ->
AutoResearchLoop.maybe_trigger()) ever calls run_epoch() at all --
AutoResearchLoop's own docstring documents run_epoch as pipeline step 5
("Hand candidates to TrainingEpoch -- validation gate decides") but
apply_finding() stops after registering the new candidate version,
never actually calling it. So this bug is real and confirmed by
execution, but not live/user-reachable today except via a direct Python
call (as a test, or a future caller that finally wires up the documented
step 5) -- which is exactly why fixing it now, before that wiring lands,
matters: the one production evaluator config (none at all) is precisely
the worst-case input this bug mishandled.
"""
from __future__ import annotations

import pytest

from agent_friday.skillopt_engine import SkillOptEngine


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


class TestRunEpochRefusesToPromoteOnNoSignal:
    def test_evaluator_raising_on_every_case_does_not_promote(self, engine):
        def exploding_evaluator(skill_name, content, case):
            raise RuntimeError("simulated evaluator crash")

        engine.evaluator = exploding_evaluator
        baseline, candidate = _make_candidate(engine)

        epoch = engine.run_epoch("demo_skill", candidate.version_id,
                                 eval_batch=[{"inputs": {}}] * 3)

        assert epoch.decision == "inconclusive", (
            f"a candidate whose evaluator raised on every single case was "
            f"promoted (decision={epoch.decision!r}) -- this is F73, an "
            f"evaluator that never produced one real data point should "
            f"never be able to promote a candidate"
        )
        best = engine.storage("demo_skill").best_version()
        assert best.version_id == baseline.version_id, (
            "the candidate got promoted (best_version changed) despite "
            "zero successful evaluations"
        )

    def test_evaluator_error_message_is_preserved_in_reason(self, engine):
        def exploding_evaluator(skill_name, content, case):
            raise RuntimeError("distinctive marker: model API timed out")

        engine.evaluator = exploding_evaluator
        _, candidate = _make_candidate(engine)

        epoch = engine.run_epoch("demo_skill", candidate.version_id,
                                 eval_batch=[{"inputs": {}}])

        assert "distinctive marker: model API timed out" in epoch.reason, (
            f"the evaluator's actual exception message was lost from "
            f"epoch.reason (got {epoch.reason!r}) -- this is the second "
            f"half of F73: the diagnostic must survive, not be silently "
            f"overwritten by the gate's own reason string"
        )

    def test_no_evaluator_and_no_history_does_not_promote(self, engine):
        # engine.evaluator is None by default -- matches the one production
        # instantiation site (get_engine() calls SkillOptEngine() with no args).
        assert engine.evaluator is None
        baseline, candidate = _make_candidate(engine)

        epoch = engine.run_epoch("demo_skill", candidate.version_id,
                                 eval_batch=[{"inputs": {}}])

        assert epoch.decision == "inconclusive", (
            f"no evaluator was configured and neither version has ever "
            f"been executed, yet decision={epoch.decision!r} -- there is "
            f"no data of any kind backing this promotion"
        )
        best = engine.storage("demo_skill").best_version()
        assert best.version_id == baseline.version_id

    def test_empty_eval_batch_does_not_promote(self, engine):
        _, candidate = _make_candidate(engine)

        epoch = engine.run_epoch("demo_skill", candidate.version_id, eval_batch=[])

        assert epoch.decision == "inconclusive", (
            f"an eval_batch of zero cases still produced "
            f"decision={epoch.decision!r} -- nothing was evaluated at all"
        )
        assert "empty" in epoch.reason.lower()

    def test_partial_evaluator_failure_reason_names_the_failures(self, engine):
        """Not every case failing is not forced inconclusive (some real
        signal exists) -- but the failure must not vanish from the reason.

        Fails deterministically by CASE (not by call count): run_epoch calls
        the evaluator twice per case (candidate, then baseline), so keying
        failure off a shared call counter would flip mid-case and make every
        case fail regardless of intent. Keying off the case's own index
        keeps both calls within one case on the same branch.
        """
        def half_broken_evaluator(skill_name, content, case):
            if case["i"] == 0:
                raise RuntimeError("intermittent failure")
            return {"accuracy": 0.9}

        engine.evaluator = half_broken_evaluator
        _, candidate = _make_candidate(engine)

        epoch = engine.run_epoch(
            "demo_skill", candidate.version_id,
            eval_batch=[{"i": i} for i in range(10)],
        )

        assert epoch.decision in ("recommended", "rejected"), (
            "a batch with SOME successful evaluations should still reach "
            "the gate, not be forced inconclusive"
        )
        assert "eval case(s) had evaluator errors" in epoch.reason, (
            f"partial evaluator failures were dropped from epoch.reason "
            f"entirely (got {epoch.reason!r})"
        )

    def test_working_evaluator_with_genuine_regression_is_still_rejected(self, engine):
        """Non-regression check: the fix must not weaken the gate's
        existing, correct behavior when given a real signal."""
        def honest_evaluator(skill_name, content, case):
            return {"accuracy": 0.9 if "baseline" in content else 0.1}

        engine.evaluator = honest_evaluator
        _, candidate = _make_candidate(engine)

        epoch = engine.run_epoch("demo_skill", candidate.version_id,
                                 eval_batch=[{"inputs": {}}] * 6)

        assert epoch.decision == "rejected"
        assert epoch.candidate_score < epoch.baseline_score

    def test_working_evaluator_with_genuine_improvement_is_still_promoted(self, engine):
        """Non-regression check: a real, clear improvement must still
        promote -- the fix only blocks promotion on NO signal, not on
        good signal."""
        def honest_evaluator(skill_name, content, case):
            return {"accuracy": 0.5 if "baseline" in content else 0.95}

        engine.evaluator = honest_evaluator
        baseline, candidate = _make_candidate(engine)
        storage = engine.storage("demo_skill")
        v = storage.read_version(baseline.version_id)
        v.metrics_summary["composite"] = 0.5
        storage.write_version(v)

        recommended = []
        engine.recommender = lambda name, cand, ep: recommended.append(cand.version_id)
        epoch = engine.run_epoch("demo_skill", candidate.version_id,
                                 eval_batch=[{"inputs": {}}] * 6)

        # A clear improvement is RECOMMENDED to the owner (harness2 HA-E28);
        # the champion changes only when the owner's card is approved.
        assert epoch.decision == "recommended"
        assert recommended == [candidate.version_id]
        best = storage.best_version()
        assert best.version_id == baseline.version_id, "promoted with no owner decision"


# ── harness2 adoption HA4 (HA-E27..E29), red first ──────────────────────────
#
# Before: a raising case scored 0.0 for BOTH arms and still counted in both
# means; one case was enough; a tie passed as "marginal"; a safety mistake was
# invisible; and a pass promoted at once with no owner decision.

from agent_friday.skillopt_engine import MIN_GRADED_CASES, composite_score, DEFAULT_WEIGHTS


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