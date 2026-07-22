"""Unit tests for services/interest_model.py — the read-only "what the user
has signalled they want" assembly (V6_WHOLENESS_SPEC.md §4 Phase 4 subset /
AUTONOMY_SPEC.md §8 A2).

Isolates services/user_model.py's SQLite store per-test the same way
test_user_model.py does (monkeypatch FRIDAY_DIR/DB_PATH onto a tmp_path), so
these tests never touch the real ~/.friday even though the whole suite also
redirects HOME globally (tests/conftest.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import interest_model as im  # noqa: E402
from agent_friday.services import user_model as um  # noqa: E402


@pytest.fixture(autouse=True)
def _iso_user_model(tmp_path, monkeypatch):
    """Every test gets a fresh, empty user_model.db."""
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    yield


@pytest.fixture(autouse=True)
def _reset_goals_provider():
    """register_goals_provider is process-global state — reset it around
    every test so one test's fake provider can't leak into the next."""
    im.register_goals_provider(None)
    yield
    im.register_goals_provider(None)


# ═══════════════════════════════════════════════════════════════════════════
#  Preferences vs. constraints — same store, filtered by category
# ═══════════════════════════════════════════════════════════════════════════

class TestPreferencesAndConstraints:
    def test_preference_fact_appears_in_preferences_not_constraints(self):
        um.note_fact("preference", "I prefer dark mode", confidence=0.8)
        prefs = im.gather_preferences()
        cons = im.gather_constraints()
        assert any(p["text"] == "I prefer dark mode" for p in prefs)
        assert not any(c["text"] == "I prefer dark mode" for c in cons)

    def test_constraint_fact_appears_in_constraints_not_preferences(self):
        um.note_fact("constraint", "Never email my boss directly", confidence=0.9)
        cons = im.gather_constraints()
        prefs = im.gather_preferences()
        assert any(c["text"] == "Never email my boss directly" for c in cons)
        assert not any(p["text"] == "Never email my boss directly" for p in prefs)

    def test_boundary_and_restriction_categories_also_count_as_constraints(self):
        um.note_fact("boundary", "Don't share my location", confidence=0.8)
        um.note_fact("restriction", "Never post about my family", confidence=0.8)
        cons = {c["text"] for c in im.gather_constraints()}
        assert "Don't share my location" in cons
        assert "Never post about my family" in cons

    def test_record_constraint_is_a_thin_user_model_wrapper(self):
        r = im.record_constraint("Never buy anything over $50 without asking")
        assert r.get("ok") is True
        cons = {c["text"] for c in im.gather_constraints()}
        assert "Never buy anything over $50 without asking" in cons

    def test_record_constraint_rejects_empty_text(self):
        r = im.record_constraint("   ")
        assert r.get("ok") is False

    def test_max_items_limits_preferences(self):
        for i in range(5):
            um.note_fact("preference", f"pref number {i}", confidence=0.5)
        assert len(im.gather_preferences(max_items=2)) == 2

    def test_max_items_limits_constraints(self):
        for i in range(5):
            um.note_fact("constraint", f"never do thing {i}", confidence=0.5)
        assert len(im.gather_constraints(max_items=3)) == 3

    def test_empty_store_returns_empty_lists(self):
        assert im.gather_preferences() == []
        assert im.gather_constraints() == []

    def test_degrades_to_empty_list_when_user_model_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db is on fire")
        # gather_preferences() still reads via profile(); gather_constraints()
        # reads via facts_by_categories() directly (see below) — both call
        # paths need to be broken for this to actually exercise both
        # functions' degradation, not just vacuously pass because the store
        # happens to be empty.
        monkeypatch.setattr(um, "profile", _boom)
        monkeypatch.setattr(um, "facts_by_categories", _boom)
        assert im.gather_preferences() == []
        assert im.gather_constraints() == []

    def test_count_constraint_facts_degrades_to_zero_when_user_model_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db is on fire")
        monkeypatch.setattr(um, "count_facts_by_categories", _boom)
        assert im.count_constraint_facts() == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Regression: constraints must survive high UNRELATED fact volume (the
#  reported defect — gather_constraints() used to filter user_model.profile()
#  ['facts'], a GLOBAL top-50 across ALL categories combined, so a single
#  constraint silently vanished once 50+ ordinary preference facts (a
#  realistic outcome of note_fact's own confidence-reinforcement over weeks
#  of use) outranked it. Fixed by querying constraint categories directly.
# ═══════════════════════════════════════════════════════════════════════════

class TestConstraintsSurviveUnrelatedFactVolume:
    def test_constraint_not_pushed_out_by_55_ordinary_preferences(self):
        um.note_fact("constraint", "Never share my location with anyone",
                      confidence=0.7)
        for i in range(55):
            um.note_fact("preference", f"ordinary preference number {i}",
                         confidence=0.85)
        cons = {c["text"] for c in im.gather_constraints()}
        assert "Never share my location with anyone" in cons

    def test_profile_facts_view_would_have_dropped_it_precondition(self):
        """Sanity precondition proving the OLD bug was real: the shared,
        globally-capped profile()['facts'] view (what gather_constraints()
        used to filter) really does drop the constraint once enough
        higher/equal-confidence unrelated facts pile up — establishing that
        the fix in gather_constraints() is doing real work, not papering
        over a scenario that could never happen."""
        um.note_fact("constraint", "Never share my location with anyone",
                      confidence=0.7)
        for i in range(55):
            um.note_fact("preference", f"ordinary preference number {i}",
                         confidence=0.85)
        profile_facts = um.profile()["facts"]
        assert not any(f["text"] == "Never share my location with anyone"
                       for f in profile_facts)

    def test_constraints_truncated_count_zero_when_nothing_truncated(self):
        um.note_fact("constraint", "Never contact my ex", confidence=0.9)
        model = im.assemble_interest_model()
        assert model["constraints_truncated_count"] == 0

    def test_constraints_truncated_count_reflects_real_truncation(self):
        for i in range(5):
            um.note_fact("constraint", f"never do thing {i}", confidence=0.5)
        model = im.assemble_interest_model(max_constraints=2)
        assert len(model["constraints"]) == 2
        assert model["constraints_truncated_count"] == 3

    def test_count_constraint_facts_matches_total_stored(self):
        for i in range(4):
            um.note_fact("constraint", f"never do thing {i}", confidence=0.5)
        assert im.count_constraint_facts() == 4


# ═══════════════════════════════════════════════════════════════════════════
#  Safety posture — informational only, content_policies stays authoritative
# ═══════════════════════════════════════════════════════════════════════════

class TestSafetyPosture:
    def test_shape(self):
        posture = im.gather_safety_posture()
        assert isinstance(posture, dict)
        assert isinstance(posture["family_mode"], bool)
        assert isinstance(posture["subscribed_packs"], list)

    def test_asimov_standard_always_present(self):
        # asimov-standard is the always-on pack in content_policies.py
        # regardless of whether its DB has been initialized yet in this
        # process — see content_policies.get_subscribed_packs()'s fallback.
        posture = im.gather_safety_posture()
        assert "asimov-standard" in posture["subscribed_packs"]
        assert posture["always_on_pack"] == "asimov-standard"

    def test_family_mode_reflects_moderation_policy(self, monkeypatch):
        from agent_friday.services import moderation

        monkeypatch.setattr(moderation, "get_policy",
                             lambda: {"family_mode": True})
        posture = im.gather_safety_posture()
        assert posture["family_mode"] is True

    def test_degrades_gracefully_when_moderation_raises(self, monkeypatch):
        from agent_friday.services import moderation

        def _boom():
            raise RuntimeError("policy file corrupt")
        monkeypatch.setattr(moderation, "get_policy", _boom)
        posture = im.gather_safety_posture()
        assert posture["family_mode"] is False  # safe default, no crash


# ═══════════════════════════════════════════════════════════════════════════
#  Goals — deferred to Phase A3; defensive, swappable provider
# ═══════════════════════════════════════════════════════════════════════════

class TestGoalsProvider:
    def test_default_provider_returns_empty_list(self):
        assert im.get_active_goals() == []

    def test_register_and_use_custom_provider(self):
        im.register_goals_provider(lambda: [{"goal_id": "g1", "title": "demo"}])
        assert im.get_active_goals() == [{"goal_id": "g1", "title": "demo"}]

    def test_register_none_restores_default(self):
        im.register_goals_provider(lambda: [{"goal_id": "g1"}])
        im.register_goals_provider(None)
        assert im.get_active_goals() == []

    def test_broken_provider_degrades_to_empty_list(self):
        def _boom():
            raise RuntimeError("goals store unavailable")
        im.register_goals_provider(_boom)
        assert im.get_active_goals() == []

    def test_provider_returning_none_degrades_to_empty_list(self):
        im.register_goals_provider(lambda: None)
        assert im.get_active_goals() == []


# ═══════════════════════════════════════════════════════════════════════════
#  assemble_interest_model — the one function dissent_gate.py calls
# ═══════════════════════════════════════════════════════════════════════════

class TestAssembleInterestModel:
    def test_has_all_five_keys(self):
        # constraints_truncated_count was added so a max_constraints (or
        # store-level) truncation is a visible, countable fact rather than
        # a silent drop — see TestConstraintsSurviveUnrelatedFactVolume.
        model = im.assemble_interest_model()
        assert set(model) == {"preferences", "constraints",
                               "constraints_truncated_count", "safety", "goals"}

    def test_no_wallclock_field(self):
        model = im.assemble_interest_model()
        assert "timestamp" not in model
        assert "generated_at" not in model

    def test_deterministic_when_store_unchanged(self):
        um.note_fact("preference", "I prefer terse answers", confidence=0.7)
        um.note_fact("constraint", "Never contact my ex", confidence=0.9)
        assert im.assemble_interest_model() == im.assemble_interest_model()

    def test_includes_constraint_written_via_record_constraint(self):
        im.record_constraint("Always ask before posting anything publicly")
        model = im.assemble_interest_model()
        assert any(c["text"] == "Always ask before posting anything publicly"
                   for c in model["constraints"])

    def test_includes_goals_from_registered_provider(self):
        im.register_goals_provider(lambda: [{"goal_id": "g42", "title": "ship it"}])
        model = im.assemble_interest_model()
        assert model["goals"] == [{"goal_id": "g42", "title": "ship it"}]

    def test_distinct_from_a_single_turn_instruction(self):
        """The interest model has no notion of "the current message" at all —
        it is assembled purely from durable stores, which is the whole point
        (AUTONOMY_SPEC: "Distinct from the instruction in the current turn")."""
        import inspect
        sig = inspect.signature(im.assemble_interest_model)
        assert "instruction" not in sig.parameters
        assert "message" not in sig.parameters
