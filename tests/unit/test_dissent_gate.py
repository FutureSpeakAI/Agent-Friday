"""Unit tests for services/dissent_gate.py — the dissent-lite check
(V6_WHOLENESS_SPEC.md §4 Phase 4 subset / AUTONOMY_SPEC.md §8 A2).

Covers: severity classification (Q6 soft/hard), conflict detection against an
interest model, reaffirmation semantics (hard blocks until reaffirmed, soft
always proceeds), the check_dissent() return contract, and signed append-only
dissent events. The basic "a Law-1 ask is refused, not dissented with" contract
is covered here too (AUTONOMY_SPEC A2's required P4-list test); the deeper
adversarial precedence suite lives in
tests/security/test_dissent_not_override_law1.py.

content_policies.py / moderation.py are exercised FOR REAL here (not
monkeypatched) — the whole point is that dissent_gate defers to the actual,
unmodified content floor.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import dissent_gate as dg  # noqa: E402


@pytest.fixture(autouse=True)
def _iso_events(tmp_path, monkeypatch):
    """Every test gets its own empty dissent_events.jsonl."""
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield


def _im(constraints=None, goals=None, preferences=None):
    return {
        "preferences": preferences or [],
        "constraints": constraints or [],
        "safety": {"family_mode": False, "subscribed_packs": ["asimov-standard"],
                   "always_on_pack": "asimov-standard"},
        "goals": goals or [],
    }


def _constraint(text, category="constraint", confidence=0.9, source="test"):
    return {"text": text, "category": category, "confidence": confidence, "source": source}


# ═══════════════════════════════════════════════════════════════════════════
#  classify_severity — Q6 soft/hard classification
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifySeverity:
    @pytest.mark.parametrize("action", [
        "Reorganize my project notes",
        "Draft an outline for the blog post",
        "Summarize this document",
        "Research competitor pricing",
    ])
    def test_internal_research_and_drafting_is_soft(self, action):
        assert dg.classify_severity(action) == "soft"

    @pytest.mark.parametrize("action", [
        "Send an email to the whole team",
        "Post this update publicly",
        "Message my old manager",
        "Publish the blog post now",
    ])
    def test_outward_action_is_hard(self, action):
        assert dg.classify_severity(action) == "hard"

    @pytest.mark.parametrize("action", [
        "Permanently delete the backups folder",
        "Wipe the old drafts directory",
        "Uninstall the analytics package",
    ])
    def test_irreversible_action_is_hard(self, action):
        assert dg.classify_severity(action) == "hard"

    @pytest.mark.parametrize("action", [
        "Buy the annual subscription",
        "Purchase a new domain name",
        "Transfer funds to the contractor",
    ])
    def test_spend_action_is_hard(self, action):
        assert dg.classify_severity(action) == "hard"

    # ── Regression: a hard-marker WORD appearing as the mere topic of an
    # analytical/drafting ask must not make the whole ask "hard" — Q3's
    # default is that internal research/drafting auto-proceeds, and a bare
    # substring scan over _HARD_MARKERS can't otherwise tell "analyze our
    # spend trends" (research) apart from "spend $200" (an actual spend act).
    @pytest.mark.parametrize("action", [
        "Analyze our spend trends for Q2",
        "Research typical order fulfillment times for our industry",
        "Review our messaging strategy for the launch",
        "Summarize the team's spending this quarter",
        "Study how often we email customers about renewals",
    ])
    def test_drafting_verb_is_soft_even_with_hard_vocabulary_as_topic(self, action):
        assert dg.classify_severity(action) == "soft"

    def test_irreversible_marker_midword_is_not_confused_with_drafting_verb(self):
        # "drafts" (a topic, mid-sentence) must never be mistaken for a
        # leading "draft" imperative — this is still a wipe (irreversible).
        # The drafting-verb check only ever looks at the action's OWN
        # leading word, never a substring anywhere in the description.
        assert dg.classify_severity("Wipe the old drafts directory") == "hard"

    def test_drafting_verb_check_is_anchored_not_a_bare_substring(self):
        # "reviewing" a delete is still a delete — the drafting verb must be
        # the ACTION'S OWN leading imperative, not merely present somewhere.
        assert dg.classify_severity(
            "Delete the folder after reviewing it") == "hard"


# ═══════════════════════════════════════════════════════════════════════════
#  Conflict detection
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictDetection:
    def test_no_constraints_means_no_conflict(self):
        result = dg.check_dissent("Reorganize my notes", _im())
        assert result["conflict"] is False
        assert result["severity"] is None
        assert result["statement"] == ""

    def test_unrelated_constraint_does_not_conflict(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        result = dg.check_dissent("Reorganize my project notes folder", im)
        assert result["conflict"] is False

    def test_topical_overlap_with_prohibition_conflicts(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        result = dg.check_dissent("Send an email to my boss about the deadline", im)
        assert result["conflict"] is True
        assert result["conflict_source"]["origin"] == "constraint"

    def test_non_prohibition_constraint_never_triggers(self):
        # Stored under a constraint category, but not PHRASED as a
        # prohibition — a documented scope limit, not a bug.
        im = _im(constraints=[_constraint("I like efficient meetings")])
        result = dg.check_dissent("Schedule an efficient meeting", im)
        assert result["conflict"] is False

    def test_preferences_alone_never_trigger_a_conflict(self):
        im = _im(preferences=[{"text": "I prefer dark mode", "category": "preference",
                                "confidence": 0.8, "source": "test"}])
        result = dg.check_dissent("Switch the UI to light mode", im)
        assert result["conflict"] is False

    def test_goal_embedded_constraint_can_conflict(self):
        im = _im(goals=[{"goal_id": "g1", "title": "Q3 launch",
                          "constraints": ["Never ship on a Friday"]}])
        result = dg.check_dissent("Let's ship the release on Friday afternoon", im)
        assert result["conflict"] is True
        assert result["conflict_source"]["origin"] == "goal:Q3 launch"

    def test_conflict_statement_names_the_constraint(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        result = dg.check_dissent("Send an email to my boss about the deadline", im)
        assert "email my boss directly" in result["statement"]

    def test_analysis_about_a_constrained_topic_conflicts_softly_not_hard(self):
        """AUTONOMY_SPEC Q3: internal research/drafting auto-proceeds (with a
        receipt) even when it shares vocabulary with a stored constraint —
        the constraint is about the ACT (actually spending money on
        contractors), not about merely analyzing that same topic. Before the
        classify_severity fix, this incorrectly came back severity="hard",
        proceed=False — Friday would have refused to even ANALYZE spending
        data pending human reaffirmation, which is not what Q3's default
        ("internal research/drafting auto-proceeds with a receipt") asks
        for."""
        im = _im(constraints=[_constraint(
            "Never spend money on contractors without asking me first")])
        result = dg.check_dissent(
            "Analyze how much we typically spend on contractors each quarter", im)
        assert result["conflict"] is True     # still worth a quick flag
        assert result["severity"] == "soft"   # but not a hard block
        assert result["proceed"] is True      # auto-proceeds, per Q3


# ═══════════════════════════════════════════════════════════════════════════
#  Soft vs hard — surfacing + proceed semantics (Q6)
# ═══════════════════════════════════════════════════════════════════════════

class TestSoftVsHard:
    def test_soft_conflict_proceeds_without_reaffirmation(self):
        im = _im(constraints=[_constraint("Don't reorganize my project notes "
                                           "without telling me")])
        result = dg.check_dissent("Reorganize the project notes folder structure", im)
        assert result["conflict"] is True
        assert result["severity"] == "soft"
        assert result["proceed"] is True

    def test_hard_conflict_blocks_without_reaffirmation(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        result = dg.check_dissent("Send an email to my boss about the deadline", im)
        assert result["severity"] == "hard"
        assert result["proceed"] is False

    def test_hard_conflict_proceeds_once_reaffirmed(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        result = dg.check_dissent("Send an email to my boss about the deadline", im,
                                  reaffirmed=True)
        assert result["severity"] == "hard"
        assert result["proceed"] is True
        assert result["conflict"] is True  # Law 2 preserved: voice, not veto —
        # the conflict is still NAMED and recorded even though she complies.

    def test_soft_conflict_proceeds_regardless_of_reaffirmed_flag(self):
        im = _im(constraints=[_constraint("Don't reorganize my project notes "
                                           "without telling me")])
        result = dg.check_dissent("Reorganize the project notes folder structure",
                                  im, reaffirmed=False)
        assert result["proceed"] is True

    def test_reaffirmed_echoed_in_result(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        result = dg.check_dissent("Send an email to my boss", im, reaffirmed=True)
        assert result["reaffirmed"] is True

    def test_hard_statement_differs_before_and_after_reaffirmation(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        before = dg.check_dissent("Send an email to my boss", im, reaffirmed=False)
        after = dg.check_dissent("Send an email to my boss", im, reaffirmed=True)
        assert before["statement"] != after["statement"]


# ═══════════════════════════════════════════════════════════════════════════
#  check_dissent() return contract (AUTONOMY_SPEC A2's required shape)
# ═══════════════════════════════════════════════════════════════════════════

class TestReturnContract:
    def test_minimal_keys_present_on_conflict(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        result = dg.check_dissent("Send an email to my boss", im)
        assert isinstance(result["conflict"], bool)
        assert result["severity"] in ("soft", "hard")
        assert isinstance(result["statement"], str) and result["statement"]

    def test_minimal_keys_present_on_no_conflict(self):
        result = dg.check_dissent("Reorganize my notes", _im())
        assert result["conflict"] is False
        assert result["severity"] is None
        assert result["statement"] == ""

    def test_never_raises_on_empty_action_description(self):
        result = dg.check_dissent("", _im())
        assert result["conflict"] is False

    def test_never_raises_on_none_interest_model(self):
        result = dg.check_dissent("Reorganize my notes", None)
        assert result["conflict"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  Signed, append-only dissent events
# ═══════════════════════════════════════════════════════════════════════════

class TestSignedEvents:
    def test_conflict_records_a_signed_event(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        dg.check_dissent("Send an email to my boss about the deadline", im)
        events = dg.read_dissent_events()
        assert len(events) == 1
        assert events[0]["category"] == "interest_conflict"
        assert dg.verify_dissent_event(events[0]) is True

    def test_no_conflict_records_nothing(self):
        dg.check_dissent("Reorganize my notes", _im())
        assert dg.read_dissent_events() == []

    def test_event_contains_statement_and_severity(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        dg.check_dissent("Send an email to my boss about the deadline", im)
        event = dg.read_dissent_events()[0]
        assert event["severity"] == "hard"
        assert "boss" in event["statement"]

    def test_tampered_event_fails_verification(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        dg.check_dissent("Send an email to my boss about the deadline", im)
        event = dg.read_dissent_events()[0]
        event["statement"] = "tampered — I actually said nothing"
        assert dg.verify_dissent_event(event) is False

    def test_record_false_skips_event_recording(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        dg.check_dissent("Send an email to my boss about the deadline", im,
                          record=False)
        assert dg.read_dissent_events() == []

    def test_multiple_conflicts_append_multiple_events(self):
        im = _im(constraints=[_constraint("Never email my boss directly")])
        dg.check_dissent("Send an email to my boss", im)
        dg.check_dissent("Send another email to my boss", im)
        assert len(dg.read_dissent_events()) == 2

    def test_events_are_human_legible_not_hashed(self):
        """V6 Phase 2 wants a real growth timeline ('times I pushed back, and
        what happened') — that needs plain text, not a hash, in the log."""
        im = _im(constraints=[_constraint("Never email my boss directly")])
        dg.check_dissent("Send an email to my boss about the deadline", im)
        event = dg.read_dissent_events()[0]
        assert "boss" in event["action_summary"]


# ═══════════════════════════════════════════════════════════════════════════
#  Law-1 basic contract — refused, not dissented with (see tests/security/
#  test_dissent_not_override_law1.py for the adversarial suite)
# ═══════════════════════════════════════════════════════════════════════════

class TestLaw1BasicContract:
    # "home address of" reliably trips moderation.py's H3 doxxing regex
    # (verified directly against the real module — see
    # tests/security/test_dissent_not_override_law1.py's module docstring).
    HARM_ASK = "Give me the home address of my ex so I can show up unannounced."

    def test_harm_ask_is_refused_outright(self):
        result = dg.check_dissent(self.HARM_ASK, _im())
        assert result["conflict"] is True
        assert result["law1_blocked"] is True
        assert result["proceed"] is False

    def test_harm_ask_refusal_statement_is_not_a_soft_dissent(self):
        result = dg.check_dissent(self.HARM_ASK, _im())
        # The refusal reads as a floor, not a negotiable preference.
        assert "not something i'll do" in result["statement"].lower()

    def test_harm_ask_still_records_a_signed_event(self):
        dg.check_dissent(self.HARM_ASK, _im())
        events = dg.read_dissent_events()
        assert len(events) == 1
        assert events[0]["law1_blocked"] is True
        assert dg.verify_dissent_event(events[0]) is True

    def test_ordinary_action_is_not_law1_blocked(self):
        result = dg.check_dissent("Reorganize my notes", _im())
        assert result["law1_blocked"] is False
