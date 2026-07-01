"""Unit tests for the Asimov-governed defederation protocol — assessment CRUD,
consensus thresholds, evidence/enum enforcement (anti-weaponization), spam
penalty, and heuristic pattern detectors.

Uses unique agent pubkeys per test to avoid consensus bleed across the shared
defederation.db.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest

from agent_friday.services import defederation as dfd

dfd._ensure_schema()


def _pk(name):
    return f"pk-{name}-{uuid.uuid4().hex[:8]}"


def _evidence(n=1):
    return [{"content_hash": f"h{i}", "timestamp": dfd._now(),
             "violation_type": "spam"} for i in range(n)]


class TestAssessmentValidation:
    def test_evidence_required(self):
        assert dfd.create_assessment(_pk("a"), [], "H1", 0.5, "MONITOR", "r") is None

    def test_invalid_harm_category_rejected(self):
        # "bad politics" is structurally impossible — not in the fixed enum.
        assert dfd.create_assessment(
            _pk("a"), _evidence(), "bad_politics", 0.5, "MONITOR", "r") is None

    def test_invalid_recommendation_rejected(self):
        assert dfd.create_assessment(
            _pk("a"), _evidence(), "H1", 0.5, "NUKE", "r") is None

    def test_valid_assessment_created(self):
        a = dfd.create_assessment(_pk("a"), _evidence(), "H2", 0.7, "RESTRICT", "reason")
        assert a is not None
        assert a["harm_category"] == "H2"
        assert a["evidence"]

    def test_severity_clamped(self):
        a = dfd.create_assessment(_pk("a"), _evidence(), "H1", 5.0, "MONITOR", "r")
        assert 0.0 <= a["severity_score"] <= 1.0


class TestAssessmentCRUD:
    def test_get_assessment(self):
        a = dfd.create_assessment(_pk("get"), _evidence(), "H1", 0.5, "MONITOR", "r")
        got = dfd.get_assessment(a["id"])
        assert got["id"] == a["id"]

    def test_get_missing_assessment_none(self):
        assert dfd.get_assessment("does-not-exist") is None

    def test_assessments_for_agent(self):
        agent = _pk("target")
        dfd.create_assessment(agent, _evidence(), "H1", 0.5, "MONITOR", "r",
                              assessor_pubkey=_pk("assessor"))
        lst = dfd.get_assessments_for(agent)
        assert len(lst) >= 1

    def test_withdraw_by_assessor(self):
        assessor = _pk("assessor")
        a = dfd.create_assessment(_pk("t"), _evidence(), "H1", 0.5, "MONITOR", "r",
                                  assessor_pubkey=assessor)
        w = dfd.withdraw_assessment(a["id"], assessor)
        assert w is not None
        assert w.get("withdrawn_at")

    def test_withdraw_by_other_denied(self):
        a = dfd.create_assessment(_pk("t"), _evidence(), "H1", 0.5, "MONITOR", "r",
                                  assessor_pubkey=_pk("owner"))
        assert dfd.withdraw_assessment(a["id"], "some-other-key") is None


class TestConsensus:
    def test_clean_when_no_assessments(self):
        c = dfd.compute_consensus(_pk("clean"))
        assert c["status"] == "CLEAN"

    def test_single_assessment_yields_monitor(self):
        agent = _pk("mon")
        dfd.create_assessment(agent, _evidence(), "H2", 0.6, "MONITOR", "r",
                              assessor_pubkey=_pk("a1"))
        assert dfd.get_consensus(agent)["status"] in ("MONITOR", "CLEAN")

    def test_defederate_requires_time_span_and_assessors(self, monkeypatch):
        agent = _pk("defed")
        # Three DEFEDERATE assessments from distinct assessors, spanning >24h.
        base = datetime.now(timezone.utc) - timedelta(hours=48)
        times = [base, base + timedelta(hours=30), base + timedelta(hours=47)]
        for i, t in enumerate(times):
            iso = t.strftime("%Y-%m-%dT%H:%M:%SZ")
            monkeypatch.setattr(dfd, "_now", lambda _iso=iso: _iso)
            dfd.create_assessment(agent, _evidence(), "coordinated_harassment",
                                  0.9, "DEFEDERATE", "r",
                                  assessor_pubkey=_pk(f"assessor{i}"))
        monkeypatch.undo()
        c = dfd.compute_consensus(agent)
        assert c["status"] == "DEFEDERATE"
        assert dfd.is_defederated(agent) is True

    def test_insufficient_assessors_no_defederate(self):
        agent = _pk("single-defed")
        # A single DEFEDERATE vote can never reach the >=3 assessor floor.
        dfd.create_assessment(agent, _evidence(), "deceptive_content", 0.95,
                              "DEFEDERATE", "r", assessor_pubkey=_pk("solo"))
        assert dfd.is_defederated(agent) is False

    def test_is_defederated_empty_key(self):
        assert dfd.is_defederated("") is False


class TestSpamPenalty:
    def test_spam_counter_increments(self):
        assessor = _pk("spammer")
        for _ in range(15):
            dfd.create_assessment(_pk("victim"), _evidence(), "H1", 0.5,
                                  "MONITOR", "r", assessor_pubkey=assessor)
        # Over the 30d threshold → effective weight reduced below the base trust.
        w = dfd._get_assessor_weight(assessor, peer_trust_score=1.0)
        assert w < 1.0


class TestPatternDetectors:
    def test_harassment_pattern_needs_multiple(self):
        agent = _pk("harass")
        for i in range(3):
            dfd.create_assessment(agent, _evidence(), "coordinated_harassment",
                                  0.7, "RESTRICT", "r", assessor_pubkey=_pk(f"a{i}"))
        r = dfd.detect_harassment_pattern(agent)
        assert r["pattern"] == "coordinated_harassment"
        assert r["score"] > 0

    def test_harassment_below_threshold(self):
        r = dfd.detect_harassment_pattern(_pk("no-harass"))
        assert r["pattern"] == "none"

    def test_epistemic_manipulation_diversity(self):
        agent = _pk("epi")
        for i in range(4):
            dfd.create_assessment(agent, _evidence(), "epistemic_manipulation",
                                  0.8, "RESTRICT", "r", assessor_pubkey=_pk(f"e{i}"))
        r = dfd.detect_epistemic_manipulation(agent)
        assert r["unique_assessors"] >= 2

    def test_sockpuppet_cluster_needs_two(self):
        assert dfd.detect_sockpuppet_cluster([_pk("only-one")])["pattern"] == "none"

    def test_radicalization_needs_three(self):
        assert dfd.detect_radicalization_pattern(_pk("no-rad"))["pattern"] == "none"
