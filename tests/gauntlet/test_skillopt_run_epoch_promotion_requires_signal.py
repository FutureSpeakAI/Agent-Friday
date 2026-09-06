"""Gauntlet finding F73 (2026-09-04, seam 13/SkillOpt investigation,
externally sourced): SkillOptEngine.run_epoch() could promote a candidate
skill version that was never actually evaluated.

The external review (Stephen commissioned an outside read of the public
v5.10.0 repo) inspected but did not execute this one, so per instruction
it was reproduced here before being trusted. All four reproductions it
asked for confirmed the claim against current code, before this fix:

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

Reachability note, for honest severity: as of this fix, nothing in the
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
            if case["i"] % 2 == 0:
                raise RuntimeError("intermittent failure")
            return {"accuracy": 0.9}

        engine.evaluator = half_broken_evaluator
        _, candidate = _make_candidate(engine)

        epoch = engine.run_epoch(
            "demo_skill", candidate.version_id,
            eval_batch=[{"i": i} for i in range(4)],
        )

        assert epoch.decision in ("promoted", "rejected"), (
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
                                 eval_batch=[{"inputs": {}}])

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

        epoch = engine.run_epoch("demo_skill", candidate.version_id,
                                 eval_batch=[{"inputs": {}}])

        assert epoch.decision == "promoted"
        best = storage.best_version()
        assert best.version_id == candidate.version_id
