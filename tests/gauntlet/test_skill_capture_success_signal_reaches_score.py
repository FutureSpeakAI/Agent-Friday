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
      _DENY_PREFIXES match => 1.0, regardless of what the reply actually
      claims happened. The reviewer's own example, "Done. I sent the
      email and booked your flight.", scores 1.0 with zero evidence
      either action occurred (see
      test_success_score_believes_unverified_claims below -- this half
      is NOT fixed here, see F74's fix_note in the ledger for why).

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

What is deliberately NOT fixed here: _success_score()'s underlying
weakness (claim b). Real task-verification would need to be aware of
what a given skill is even supposed to accomplish (a skill needing zero
tool calls to succeed is not equivalent to one that claims to have sent
an email with no corresponding tool_trace entry) -- that is a genuine
product decision about what "success" should mean per skill, not a
mechanical patch, and guessing at it risks a worse, false-confidence
signal instead of an honestly-absent one. Flagged for Stephen, not
decided here.
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
        assert execs[0].metrics.get("accuracy") == 1.0, (
            f"expected the success score to be recorded under the 'accuracy' "
            f"key composite_score() actually reads, got metrics="
            f"{execs[0].metrics!r}"
        )


class TestSuccessScoreStillMeasuresReplyShapeOnly:
    """Documents, does not fix, the second (deeper) half of F74 -- claim
    (b) from the external review. Kept as its own class so a future,
    deliberate improvement to _success_score() has an obvious test to
    update rather than silently breaking this one."""

    def test_success_score_believes_unverified_claims(self):
        reviewer_example = "Done. I sent the email and booked your flight."
        assert skill_capture._success_score(reviewer_example, None) == 1.0, (
            "if this is no longer 1.0, _success_score() has gained some "
            "form of task verification -- F74's claim (b) may be resolved "
            "and this test (and the finding's disposition) should be "
            "updated to match, not just this assertion"
        )

    def test_success_score_cannot_tell_true_success_from_plausible_fiction(self):
        genuine = "Done. I sent the email."
        fabricated = "Done. I sent the email."  # identical text, no real send
        assert (
            skill_capture._success_score(genuine, None)
            == skill_capture._success_score(fabricated, None)
        ), "these must currently be indistinguishable -- that's the defect"
