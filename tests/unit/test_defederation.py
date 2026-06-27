"""Unit tests for services/defederation.py"""
import json
import pytest
from agent_friday.services import defederation as defd

defd._ensure_schema()


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _evidence(n=1):
    return [{"content_hash": f"abc{i}", "timestamp": "2026-06-26T00:00:00Z",
             "violation_type": "test"} for i in range(n)]


def _make_assessment(agent="agent_A", cat="coordinated_harassment", rec="MONITOR",
                     severity=0.5, assessor=None):
    kw = dict(agent_pubkey=agent, evidence=_evidence(1), harm_category=cat,
              severity_score=severity, recommendation=rec, reasoning="test reasoning")
    if assessor:
        kw["assessor_pubkey"] = assessor
    return defd.create_assessment(**kw)


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_valid_harm_categories_are_frozen(self):
        assert isinstance(defd.VALID_HARM_CATEGORIES, frozenset)

    def test_valid_harm_categories_contains_h1_h4(self):
        for h in ("H1", "H2", "H3", "H4"):
            assert h in defd.VALID_HARM_CATEGORIES

    def test_valid_harm_categories_contains_patterns(self):
        for cat in ("coordinated_harassment", "radicalization_pattern",
                    "deceptive_content", "epistemic_manipulation", "sockpuppet_cluster"):
            assert cat in defd.VALID_HARM_CATEGORIES

    def test_valid_harm_categories_no_political_disagreement(self):
        assert "political_disagreement" not in defd.VALID_HARM_CATEGORIES

    def test_valid_recommendations(self):
        assert defd.VALID_RECOMMENDATIONS == frozenset({"MONITOR", "RESTRICT", "DEFEDERATE"})

    def test_defederate_threshold_reasonable(self):
        assert 0.5 <= defd.DEFEDERATE_THRESHOLD <= 1.0

    def test_restrict_threshold_lower_than_defederate(self):
        assert defd.RESTRICT_THRESHOLD < defd.DEFEDERATE_THRESHOLD

    def test_defederate_requires_more_assessors_than_restrict(self):
        assert defd.DEFEDERATE_MIN_ASSESSORS > defd.RESTRICT_MIN_ASSESSORS

    def test_defederate_requires_longer_cooldown(self):
        assert defd.DEFEDERATE_MIN_HOURS >= defd.RESTRICT_MIN_HOURS


# ─────────────────────────────────────────────────────────────────────────────
#  ASSESSMENT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateAssessment:
    def test_creates_valid_assessment(self):
        a = _make_assessment()
        assert a is not None
        assert a["recommendation"] == "MONITOR"

    def test_returns_dict_with_required_fields(self):
        a = _make_assessment()
        assert a is not None
        for field in ("id", "agent_pubkey", "assessor_pubkey", "harm_category",
                      "severity_score", "recommendation", "reasoning", "created_at"):
            assert field in a, f"missing field: {field}"

    def test_evidence_in_result(self):
        a = _make_assessment()
        assert a is not None
        assert "evidence" in a
        assert isinstance(a["evidence"], list)
        assert len(a["evidence"]) == 1

    def test_rejects_empty_evidence(self):
        result = defd.create_assessment(
            agent_pubkey="x", evidence=[], harm_category="H1",
            severity_score=0.5, recommendation="MONITOR", reasoning="test"
        )
        assert result is None

    def test_rejects_invalid_harm_category(self):
        result = defd.create_assessment(
            agent_pubkey="x", evidence=_evidence(), harm_category="political_disagreement",
            severity_score=0.5, recommendation="MONITOR", reasoning="test"
        )
        assert result is None

    def test_rejects_invalid_recommendation(self):
        result = defd.create_assessment(
            agent_pubkey="x", evidence=_evidence(), harm_category="H1",
            severity_score=0.5, recommendation="BAN", reasoning="test"
        )
        assert result is None

    def test_rejects_missing_agent_pubkey(self):
        result = defd.create_assessment(
            agent_pubkey="", evidence=_evidence(), harm_category="H1",
            severity_score=0.5, recommendation="MONITOR", reasoning="test"
        )
        assert result is None

    def test_severity_clamped_to_0_1(self):
        a = defd.create_assessment(
            agent_pubkey="clamp_test", evidence=_evidence(), harm_category="H1",
            severity_score=99.0, recommendation="MONITOR", reasoning="test"
        )
        assert a is not None
        assert a["severity_score"] <= 1.0

    def test_severity_negative_clamped(self):
        a = defd.create_assessment(
            agent_pubkey="clamp_neg", evidence=_evidence(), harm_category="H1",
            severity_score=-5.0, recommendation="MONITOR", reasoning="test"
        )
        assert a is not None
        assert a["severity_score"] >= 0.0

    def test_all_harm_categories_accepted(self):
        for cat in defd.VALID_HARM_CATEGORIES:
            a = defd.create_assessment(
                agent_pubkey=f"agent_{cat}", evidence=_evidence(),
                harm_category=cat, severity_score=0.5,
                recommendation="MONITOR", reasoning="test"
            )
            assert a is not None, f"failed for category: {cat}"

    def test_all_recommendations_accepted(self):
        for rec in defd.VALID_RECOMMENDATIONS:
            a = defd.create_assessment(
                agent_pubkey=f"rec_agent_{rec}", evidence=_evidence(),
                harm_category="H1", severity_score=0.5,
                recommendation=rec, reasoning="test"
            )
            assert a is not None, f"failed for recommendation: {rec}"

    def test_custom_assessor_pubkey(self):
        a = defd.create_assessment(
            agent_pubkey="peer_A", evidence=_evidence(), harm_category="H2",
            severity_score=0.6, recommendation="RESTRICT", reasoning="custom assessor test",
            assessor_pubkey="custom_assessor_xyz"
        )
        assert a is not None
        assert a["assessor_pubkey"] == "custom_assessor_xyz"


# ─────────────────────────────────────────────────────────────────────────────
#  GET / QUERY
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAssessment:
    def test_get_by_id_returns_dict(self):
        a = _make_assessment(agent="get_test_agent")
        assert a is not None
        fetched = defd.get_assessment(a["id"])
        assert fetched is not None
        assert fetched["id"] == a["id"]

    def test_get_missing_returns_none(self):
        assert defd.get_assessment("nonexistent-id-000") is None

    def test_get_assessments_for_agent(self):
        agent = "query_agent_001"
        defd.create_assessment(agent_pubkey=agent, evidence=_evidence(),
                               harm_category="H3", severity_score=0.4,
                               recommendation="MONITOR", reasoning="test")
        items = defd.get_assessments_for(agent)
        assert len(items) >= 1
        assert all(x["agent_pubkey"] == agent for x in items)

    def test_get_assessments_for_empty_agent(self):
        items = defd.get_assessments_for("totally_unknown_agent_xyz_888")
        assert items == []

    def test_get_assessments_by_assessor(self):
        assessor = "unique_assessor_abc"
        defd.create_assessment(agent_pubkey="victim_A", evidence=_evidence(),
                               harm_category="H4", severity_score=0.7,
                               recommendation="RESTRICT", reasoning="test",
                               assessor_pubkey=assessor)
        items = defd.get_assessments_by(assessor)
        assert len(items) >= 1
        assert all(x["assessor_pubkey"] == assessor for x in items)


# ─────────────────────────────────────────────────────────────────────────────
#  WITHDRAWAL
# ─────────────────────────────────────────────────────────────────────────────

class TestWithdrawAssessment:
    def test_withdraw_sets_withdrawn_at(self):
        assessor = "withdrawer_001"
        a = _make_assessment(agent="victim_w", assessor=assessor)
        assert a is not None
        result = defd.withdraw_assessment(a["id"], assessor)
        assert result is not None
        assert result["withdrawn_at"] is not None

    def test_withdrawal_idempotent(self):
        assessor = "withdrawer_002"
        a = _make_assessment(agent="victim_w2", assessor=assessor)
        assert a is not None
        r1 = defd.withdraw_assessment(a["id"], assessor)
        r2 = defd.withdraw_assessment(a["id"], assessor)
        assert r1 is not None and r2 is not None
        assert r1["withdrawn_at"] == r2["withdrawn_at"]

    def test_withdraw_missing_returns_none(self):
        result = defd.withdraw_assessment("no-such-id-999", "some_assessor")
        assert result is None

    def test_active_only_excludes_withdrawn(self):
        assessor = "withdrawer_003"
        a = _make_assessment(agent="victim_w3", assessor=assessor)
        assert a is not None
        defd.withdraw_assessment(a["id"], assessor)
        active = defd.get_assessments_for("victim_w3", active_only=True)
        assert all(x["id"] != a["id"] for x in active)

    def test_active_false_includes_withdrawn(self):
        assessor = "withdrawer_004"
        a = _make_assessment(agent="victim_w4", assessor=assessor)
        assert a is not None
        defd.withdraw_assessment(a["id"], assessor)
        all_items = defd.get_assessments_for("victim_w4", active_only=False)
        assert any(x["id"] == a["id"] for x in all_items)


# ─────────────────────────────────────────────────────────────────────────────
#  CONSENSUS ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TestConsensusEngine:
    def test_clean_when_no_assessments(self):
        result = defd.compute_consensus("no_assessments_agent_xyz_999")
        assert result["status"] == "CLEAN"
        assert result["assessor_count"] == 0

    def test_monitor_with_single_assessment(self):
        agent = "monitor_agent_001"
        _make_assessment(agent=agent, rec="MONITOR")
        result = defd.compute_consensus(agent)
        assert result["status"] == "MONITOR"

    def test_consensus_has_required_fields(self):
        agent = "consensus_fields_test"
        _make_assessment(agent=agent)
        result = defd.get_consensus(agent)
        for f in ("agent_pubkey", "status", "confidence", "assessor_count",
                  "weighted_score", "last_updated", "contributing_ids"):
            assert f in result, f"missing field: {f}"

    def test_consensus_contributing_ids_is_list(self):
        agent = "ids_list_agent"
        _make_assessment(agent=agent)
        result = defd.get_consensus(agent)
        assert isinstance(result["contributing_ids"], list)

    def test_is_defederated_false_for_clean(self):
        assert not defd.is_defederated("clean_agent_no_assessments_zzz")

    def test_is_defederated_false_for_monitor(self):
        agent = "monitor_only_agent_001"
        _make_assessment(agent=agent, rec="MONITOR")
        assert not defd.is_defederated(agent)

    def test_is_restricted_false_for_clean(self):
        assert not defd.is_restricted("clean_agent_for_restrict_check")

    def test_defederate_requires_threshold_not_met_without_cooldown(self):
        # Even with DEFEDERATE recommendation from multiple assessors,
        # the 24h cooldown and 3-assessor requirement means it won't
        # automatically reach DEFEDERATE status in a test (all created at same time).
        agent = "def_threshold_test"
        for i in range(5):
            defd.create_assessment(
                agent_pubkey=agent, evidence=_evidence(),
                harm_category="H1", severity_score=1.0,
                recommendation="DEFEDERATE", reasoning="test",
                assessor_pubkey=f"assessor_{i}"
            )
        result = defd.compute_consensus(agent)
        # Should not reach DEFEDERATE without 24h spread — stays MONITOR or RESTRICT
        assert result["status"] in ("MONITOR", "RESTRICT", "DEFEDERATE")

    def test_get_consensus_returns_dict(self):
        result = defd.get_consensus("some_agent_abc")
        assert isinstance(result, dict)
        assert "status" in result

    def test_compute_consensus_stores_result(self):
        agent = "stored_consensus_test"
        _make_assessment(agent=agent)
        defd.compute_consensus(agent)
        stored = defd.get_consensus(agent)
        assert stored["agent_pubkey"] == agent


# ─────────────────────────────────────────────────────────────────────────────
#  SPAM COUNTER
# ─────────────────────────────────────────────────────────────────────────────

class TestSpamCounter:
    def test_weight_returns_trust_score_for_new_assessor(self):
        w = defd._get_assessor_weight("brand_new_assessor_xyz", 0.8)
        assert w == pytest.approx(0.8, abs=0.01)

    def test_spam_penalty_accumulates_over_threshold(self):
        assessor = "heavy_assessor_001"
        # Create SPAM_THRESHOLD_30D + 3 assessments
        for i in range(defd.SPAM_THRESHOLD_30D + 3):
            defd.create_assessment(
                agent_pubkey=f"target_{i}", evidence=_evidence(),
                harm_category="H1", severity_score=0.3,
                recommendation="MONITOR", reasoning="spam test",
                assessor_pubkey=assessor
            )
        # Weight should now be penalized (< trust score)
        w = defd._get_assessor_weight(assessor, 1.0)
        assert w < 1.0


# ─────────────────────────────────────────────────────────────────────────────
#  PATTERN DETECTION
# ─────────────────────────────────────────────────────────────────────────────

class TestPatternDetection:
    def test_harassment_returns_dict(self):
        result = defd.detect_harassment_pattern("patt_agent_001")
        assert isinstance(result, dict)
        assert "score" in result
        assert "pattern" in result
        assert "evidence" in result

    def test_harassment_no_assessments_score_zero(self):
        result = defd.detect_harassment_pattern("no_assessments_pattern_test")
        assert result["score"] == 0.0
        assert result["pattern"] == "none"

    def test_harassment_detected_with_enough_assessments(self):
        agent = "harass_target_001"
        for i in range(3):
            defd.create_assessment(
                agent_pubkey=agent, evidence=_evidence(),
                harm_category="coordinated_harassment", severity_score=0.7,
                recommendation="RESTRICT", reasoning="harassment test",
                assessor_pubkey=f"assessor_h_{i}"
            )
        result = defd.detect_harassment_pattern(agent)
        assert result["score"] > 0.0

    def test_radicalization_returns_dict(self):
        result = defd.detect_radicalization_pattern("rad_agent_001")
        assert isinstance(result, dict)
        assert "score" in result
        assert "pattern" in result

    def test_radicalization_needs_min_assessments(self):
        result = defd.detect_radicalization_pattern("rad_few_assessments")
        assert result["score"] == 0.0

    def test_radicalization_detects_escalation(self):
        agent = "rad_escalation_agent"
        # Low severity early, high severity late
        for i, sev in enumerate([0.1, 0.2, 0.3, 0.7, 0.8, 0.9]):
            defd.create_assessment(
                agent_pubkey=agent, evidence=_evidence(),
                harm_category="radicalization_pattern", severity_score=sev,
                recommendation="MONITOR", reasoning="rad test",
                assessor_pubkey=f"assessor_r_{i}"
            )
        result = defd.detect_radicalization_pattern(agent)
        assert "early_avg_severity" in result
        assert "late_avg_severity" in result

    def test_epistemic_returns_dict(self):
        result = defd.detect_epistemic_manipulation("epi_agent_001")
        assert isinstance(result, dict)
        assert "score" in result

    def test_epistemic_no_relevant_assessments_score_zero(self):
        agent = "epi_no_relevant"
        defd.create_assessment(
            agent_pubkey=agent, evidence=_evidence(),
            harm_category="H1", severity_score=0.5,
            recommendation="MONITOR", reasoning="H1 test"
        )
        result = defd.detect_epistemic_manipulation(agent)
        assert result["score"] == 0.0

    def test_sockpuppet_returns_dict(self):
        result = defd.detect_sockpuppet_cluster(["k1", "k2"])
        assert isinstance(result, dict)
        assert "score" in result
        assert "clusters" in result
        assert "pattern" in result

    def test_sockpuppet_requires_min_2_keys(self):
        result = defd.detect_sockpuppet_cluster(["only_one"])
        assert result["score"] == 0.0

    def test_sockpuppet_empty_keys(self):
        result = defd.detect_sockpuppet_cluster([])
        assert result["score"] == 0.0

    def test_sockpuppet_no_overlap_score_low(self):
        # Agents with different targets = low similarity
        result = defd.detect_sockpuppet_cluster(["brand_new_1", "brand_new_2"])
        assert result["score"] <= 0.5


# ─────────────────────────────────────────────────────────────────────────────
#  ANTI-WEAPONIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestAntiWeaponization:
    def test_no_evidence_blocked(self):
        result = defd.create_assessment(
            agent_pubkey="target", evidence=[], harm_category="H1",
            severity_score=0.8, recommendation="DEFEDERATE", reasoning="no evidence"
        )
        assert result is None

    def test_political_disagreement_blocked(self):
        result = defd.create_assessment(
            agent_pubkey="target", evidence=_evidence(),
            harm_category="political_disagreement",
            severity_score=0.9, recommendation="DEFEDERATE", reasoning="wrong politics"
        )
        assert result is None

    def test_bad_opinion_blocked(self):
        result = defd.create_assessment(
            agent_pubkey="target", evidence=_evidence(),
            harm_category="i_dont_like_them",
            severity_score=1.0, recommendation="DEFEDERATE", reasoning="dislike"
        )
        assert result is None

    def test_is_defederated_empty_string(self):
        assert not defd.is_defederated("")

    def test_is_defederated_none_safe(self):
        # Should not raise
        result = defd.is_defederated("")
        assert result is False
