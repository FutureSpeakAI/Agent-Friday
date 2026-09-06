"""Gauntlet finding F74 (2026-09-04, seam 13/SkillOpt investigation,
externally sourced): skill_capture.capture()'s success signal never
reached SkillOpt's composite score, so a real chat turn Friday considered
a total failure and a real chat turn she considered a full success scored
IDENTICALLY.

Two coupled claims from an outside review Stephen commissioned (it read
the public v5.10.0 repo, both reproduced there by executing the scoring
functions -- re-verified here against current code before trusting them):

  (a) capture() called record_skill_run(..., metrics={"quality": score,
      "success": score}, ...), but skillopt_engine.composite_score() only
      ever reads "accuracy", "user_satisfaction", "completeness",
      "latency_ms"/"duration_ms", and "cost_usd" -- "quality" and
      "success" are simply never looked at. Reproduced directly: with
      default weights, 1000ms latency, $0 cost, metrics={"quality": 1.0,
      "success": 1.0} and metrics={"quality": 0.0, "success": 0.0} both
      produced composite_score == 0.25. Identical.

  (b) _success_score() itself is a reply-shape heuristic, not a task
      completion check: no error + a stripped reply >= 8 chars + no
      _DENY_PREFIXES match => (used to be 1.0, "success"), regardless of
      what the reply actually claims happened. The reviewer's own
      example, "Done. I sent the email and booked your flight.", scored
      1.0 with zero evidence either action occurred.

Fix (src/agent_friday/skill_capture.py, capture()): route `score` into
the single metrics key it can honestly speak to -- `accuracy` -- instead
of two keys composite_score() ignores. Deliberately NOT copied into
user_satisfaction/completeness too: _success_score() has no information
about either of those dimensions, and claiming it does would fabricate
false precision. This makes the metric-mismatch fix a net improvement
rather than "count a bad signal for more": production's only other
composite_score producer, src/agent_friday/ui/liquid_ui.py's
_record_usage_to_skillopt(), already uses the correct accuracy/
user_satisfaction/completeness keys against three independently-derived
signals -- skill_capture.py was simply not following the convention its
own sibling caller uses correctly.

CORRECTION (F75, Stephen's direct ruling, 2026-09-05): claim (b) itself
is now partially fixed too -- not with the real task-verification design
work Stephen explicitly deferred (a genuine product decision about what
"success" should mean per skill/task type, specified as follow-up work
in skill_capture.py itself, not decided here), but with the "minimum
honest change" he did rule on: _success_score() no longer returns
SUCCESS_SCORE (1.0) for a merely-plausible reply. It returns
UNVERIFIED_SCORE (0.5) instead -- a third state for "no confirmed
failure, but also no confirmed success," since the absence of a bad
signal was never the presence of a good one. SUCCESS_SCORE remains fully
wired through every consumer but is not reachable by anything in this
file today. See TestSuccessScoreNowReturnsUnverifiedNotSuccess below,
which replaces the old TestSuccessScoreStillMeasuresReplyShapeOnly.
"""
from __future__ import annotations

import types

import pytest

import agent_friday.skill_capture as skill_capture
import agent_friday.skillopt_engine as skillopt_engine
from agent_friday.skillopt_engine import SkillOptEngine


@pytest.fixture
def isolated_engine(tmp_path, monkeypatch):
    """Redirect the SkillOpt singleton so this test never touches the
    real ~/.friday/skillopt, and capture()'s record_skill_run() call
    lands somewhere we can read back."""
    engine = SkillOptEngine(root=tmp_path / "skillopt")
    monkeypatch.setattr(skillopt_engine, "_engine_singleton", engine)
    return engine


@pytest.fixture
def matched_skill(monkeypatch):
    fake_skill = types.SimpleNamespace(name="demo_matched_skill")
    monkeypatch.setattr(
        "agent_friday.skill_registry.match_skills",
        lambda message, limit=3: [fake_skill],
    )
    return fake_skill


class TestCaptureSuccessSignalReachesScore:
    def test_successful_and_failed_turns_now_score_differently(
        self, isolated_engine, matched_skill, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(skill_capture, "FRIDAY_DIR", tmp_path / "friday_home")
        monkeypatch.setattr(skill_capture, "TRAJ_FILE",
                            tmp_path / "friday_home" / "trajectories.jsonl")

        skill_capture.capture(
            message="please send the report",
            reply="Done. I sent the report to the team.",
            duration_ms=1000.0,
            error=None,
        )
        skill_capture.capture(
            message="please send the report",
            reply="",
            duration_ms=1000.0,
            error="tool crashed",
        )

        execs = isolated_engine.storage("demo_matched_skill").read_executions()
        assert len(execs) == 2, (
            f"expected both capture() calls to record an execution against "
            f"the matched skill, got {len(execs)}"
        )
        success_score = execs[0].composite_score
        failure_score = execs[1].composite_score

        assert success_score != failure_score, (
            f"a successful-looking reply and an errored reply produced the "
            f"SAME composite_score ({success_score}) -- this is F74's core "
            f"claim: the success signal never reached the score at all"
        )
        assert success_score > failure_score

    def test_the_recorded_metrics_key_is_accuracy_not_the_dead_keys(
        self, isolated_engine, matched_skill, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(skill_capture, "FRIDAY_DIR", tmp_path / "friday_home")
        monkeypatch.setattr(skill_capture, "TRAJ_FILE",
                            tmp_path / "friday_home" / "trajectories.jsonl")

        skill_capture.capture(
            message="do the thing", reply="Done, all set.",
            duration_ms=1000.0, error=None,
        )

        execs = isolated_engine.storage("demo_matched_skill").read_executions()
        assert len(execs) == 1
        assert execs[0].metrics.get("accuracy") == skill_capture.UNVERIFIED_SCORE, (
            f"expected the (now honestly-labeled) unverified score to be "
            f"recorded under the 'accuracy' key composite_score() actually "
            f"reads, got metrics={execs[0].metrics!r}"
        )


class TestSuccessScoreNowReturnsUnverifiedNotSuccess:
    """F75 (Stephen's direct ruling, 2026-09-05): replaces the old
    TestSuccessScoreStillMeasuresReplyShapeOnly, which pinned the BUG
    (a plausible reply scoring a confirmed SUCCESS_SCORE with zero
    evidence) as accepted, current behavior. That bug is fixed -- a
    plausible reply now scores UNVERIFIED_SCORE, a distinct third value
    that is neither a confirmed failure nor a confirmed success. What is
    NOT fixed, deliberately, per the same ruling: real completion
    verification (so SUCCESS_SCORE could ever actually be reached) is
    real design work, not decided here -- see skill_capture.py's own
    follow-up note. Kept as its own class so that future, deliberate work
    has an obvious test to update rather than silently breaking this one."""

    def test_a_plausible_reply_is_unverified_not_a_confirmed_success(self):
        reviewer_example = "Done. I sent the email and booked your flight."
        score = skill_capture._success_score(reviewer_example, None)
        assert score == skill_capture.UNVERIFIED_SCORE, (
            f"a plausible-looking reply with zero evidence either claimed "
            f"action occurred scored {score!r}, not UNVERIFIED_SCORE -- "
            f"this is F75's exact false-positive if it's SUCCESS_SCORE "
            f"again, or a new, undocumented value if it's neither"
        )
        assert score != skill_capture.SUCCESS_SCORE, (
            "SUCCESS_SCORE must not be reachable without real completion "
            "evidence, which this heuristic still does not check"
        )

    def test_a_confirmed_failure_still_scores_failure_not_unverified(self):
        """Non-regression: F75's fix must not soften genuine failure
        detection into 'merely unverified' -- an error, a too-short
        reply, and a deny-prefix match are real negative evidence and
        must stay FAILURE_SCORE."""
        assert skill_capture._success_score("Done, all set.", "boom") == \
            skill_capture.FAILURE_SCORE
        assert skill_capture._success_score("Sure.", None) == \
            skill_capture.FAILURE_SCORE
        assert skill_capture._success_score(
            "[GOVERNANCE DENY] blocked", None) == skill_capture.FAILURE_SCORE

    def test_genuine_success_and_plausible_fiction_are_still_indistinguishable(self):
        """The deeper problem F75 explicitly did NOT fix tonight: a
        genuine success and a fabricated claim of the same action still
        score identically -- as UNVERIFIED_SCORE now, not a false
        SUCCESS_SCORE, but still indistinguishable from each other. Real
        completion verification (the follow-up work specified in
        skill_capture.py) is what would tell these apart; guessing at it
        here was explicitly ruled out."""
        genuine = "Done. I sent the email."
        fabricated = "Done. I sent the email."  # identical text, no real send
        assert (
            skill_capture._success_score(genuine, None)
            == skill_capture._success_score(fabricated, None)
            == skill_capture.UNVERIFIED_SCORE
        )
