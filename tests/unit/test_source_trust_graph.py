"""Unit tests for source_trust_graph — pure reputation-scoring logic.

Tests cover:
  * _extract_domain:  URL → bare domain canonicalization
  * _seed_for:        initial reputation seeding (high / low / neutral)
  * _composite:       weighted mean of six dimensions, clamped to [0, 1]
  * _recompute:       seed-anchored decayed recompute; score moves in correct direction
  * Full observe→score lifecycle using a temp dir for isolation
  * Edge cases: empty graph, unknown domain, malformed input, obs cap, user actions
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import source_trust_graph as stg
from source_trust_graph import (
    DIMENSIONS,
    SourceTrustGraph,
    _extract_domain,
    _seed_for,
    _MAX_OBSERVATIONS,
)


# ── helpers ────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory(prefix="stg_test_") as d:
        yield Path(d)


@pytest.fixture
def graph(tmpdir):
    """A fresh SourceTrustGraph pointed at an isolated temp dir."""
    return SourceTrustGraph(friday_dir=tmpdir)


# ── _extract_domain ────────────────────────────────────────────────────────────

class TestExtractDomain:
    def test_plain_domain(self):
        assert _extract_domain("example.com") == "example.com"

    def test_http_url(self):
        assert _extract_domain("http://example.com/some/path") == "example.com"

    def test_https_url(self):
        assert _extract_domain("https://example.com/article?q=1") == "example.com"

    def test_strips_www(self):
        assert _extract_domain("https://www.example.com/page") == "example.com"

    def test_subdomain_kept(self):
        # Subdomains other than "www" are preserved
        result = _extract_domain("https://news.bbc.co.uk/world/article")
        assert result == "news.bbc.co.uk"

    def test_www_only_strips_www(self):
        assert _extract_domain("www.reuters.com") == "reuters.com"

    def test_path_stripped(self):
        assert _extract_domain("https://fake-news.test/deep/path/article.html") == "fake-news.test"

    def test_query_string_stripped(self):
        assert _extract_domain("https://example.com?utm_source=foo") == "example.com"

    def test_fragment_stripped(self):
        assert _extract_domain("https://example.com#section") == "example.com"

    def test_empty_string(self):
        assert _extract_domain("") == ""

    def test_none(self):
        assert _extract_domain(None) == ""

    def test_whitespace(self):
        assert _extract_domain("   ") == ""

    def test_lowercases(self):
        assert _extract_domain("HTTPS://Example.COM/Path") == "example.com"

    def test_no_scheme(self):
        assert _extract_domain("example.com") == "example.com"

    def test_trailing_dot_stripped(self):
        # DNS trailing dot should not appear in stored keys
        result = _extract_domain("example.com.")
        assert result == "example.com"


# ── _seed_for ─────────────────────────────────────────────────────────────────

class TestSeedFor:
    def test_high_trust_domain_above_threshold(self):
        seed = _seed_for("reuters.com")
        score = SourceTrustGraph._composite(seed)
        assert score >= 0.7, f"Expected green seed for reuters.com, got {score}"

    def test_low_trust_domain_below_threshold(self):
        seed = _seed_for("infowars.com")
        score = SourceTrustGraph._composite(seed)
        assert score < 0.4, f"Expected red seed for infowars.com, got {score}"

    def test_unknown_domain_neutral(self):
        seed = _seed_for("totally-unknown-domain.test")
        score = SourceTrustGraph._composite(seed)
        assert 0.4 <= score <= 0.65, f"Expected neutral seed, got {score}"

    def test_all_dimensions_present(self):
        seed = _seed_for("example.com")
        for dim in DIMENSIONS:
            assert dim in seed, f"Missing dimension '{dim}' in seed"

    def test_prediction_accuracy_always_neutral(self):
        # prediction_accuracy is a placeholder — should always be 0.5
        for domain in ("reuters.com", "infowars.com", "unknown.test"):
            seed = _seed_for(domain)
            assert seed["prediction_accuracy"] == 0.5, (
                f"prediction_accuracy should be 0.5 for {domain}, got {seed['prediction_accuracy']}"
            )

    def test_seeds_in_unit_range(self):
        for domain in ("reuters.com", "infowars.com", "example.com"):
            seed = _seed_for(domain)
            for dim, val in seed.items():
                assert 0.0 <= val <= 1.0, f"{domain}/{dim} out of [0,1]: {val}"

    def test_high_and_low_differ(self):
        high = _seed_for("apnews.com")
        low = _seed_for("naturalnews.com")
        assert SourceTrustGraph._composite(high) > SourceTrustGraph._composite(low)


# ── _composite ────────────────────────────────────────────────────────────────

class TestComposite:
    def test_all_ones_returns_one(self):
        scores = {d: 1.0 for d in DIMENSIONS}
        assert SourceTrustGraph._composite(scores) == pytest.approx(1.0, abs=1e-4)

    def test_all_zeros_returns_zero(self):
        scores = {d: 0.0 for d in DIMENSIONS}
        assert SourceTrustGraph._composite(scores) == pytest.approx(0.0, abs=1e-4)

    def test_all_halves_returns_half(self):
        scores = {d: 0.5 for d in DIMENSIONS}
        result = SourceTrustGraph._composite(scores)
        assert result == pytest.approx(0.5, abs=1e-4)

    def test_result_in_unit_range(self):
        import random
        rng = random.Random(42)
        for _ in range(20):
            scores = {d: rng.random() for d in DIMENSIONS}
            c = SourceTrustGraph._composite(scores)
            assert 0.0 <= c <= 1.0, f"composite out of range: {c}"

    def test_weights_sum_to_one(self):
        """Sanity-check that the module's declared weights sum to 1.0."""
        total = sum(stg._WEIGHTS[d] for d in DIMENSIONS)
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_missing_dimension_defaults_to_half(self):
        # Missing dimension should default to 0.5 per the get() fallback
        scores = {d: 1.0 for d in DIMENSIONS}
        del scores["factual_accuracy"]
        c = SourceTrustGraph._composite(scores)
        # factual_accuracy weight 0.35 at 0.5 instead of 1.0 → lower composite
        assert c < 1.0


# ── _recompute ────────────────────────────────────────────────────────────────

class TestRecompute:
    def _make_rec(self, domain="example.com"):
        seed = _seed_for(domain)
        return {
            "domain": domain,
            "name": domain,
            "scores": dict(seed),
            "observations": [],
            "trust_score": SourceTrustGraph._composite(seed),
            "article_count": 0,
            "first_seen": "2026-01-01",
            "last_updated": "2026-01-01",
            "user_actions": {},
        }

    def test_no_observations_returns_seed(self):
        g = SourceTrustGraph.__new__(SourceTrustGraph)
        rec = self._make_rec("unknown.test")
        g._recompute(rec)
        # With no observations, the result should equal the seed composite
        seed_score = SourceTrustGraph._composite(_seed_for("unknown.test"))
        assert rec["trust_score"] == pytest.approx(seed_score, abs=0.02)

    def test_positive_signal_raises_score(self):
        """Repeated high-signal observations should push factual_accuracy up."""
        g = SourceTrustGraph.__new__(SourceTrustGraph)
        rec = self._make_rec("unknown.test")
        before = rec["scores"]["factual_accuracy"]
        for _ in range(15):
            rec["observations"].append({
                "date": "2026-06-09",
                "type": "claim_verified",
                "dimension": "factual_accuracy",
                "signal": 1.0,
                "detail": "",
                "counter_sources": [],
                "signed_by": "local",
            })
        g._recompute(rec)
        assert rec["scores"]["factual_accuracy"] > before

    def test_negative_signal_lowers_score(self):
        """Repeated low-signal observations should push factual_accuracy down."""
        g = SourceTrustGraph.__new__(SourceTrustGraph)
        rec = self._make_rec("unknown.test")
        before = rec["scores"]["factual_accuracy"]
        for _ in range(15):
            rec["observations"].append({
                "date": "2026-06-09",
                "type": "minority_claim",
                "dimension": "factual_accuracy",
                "signal": 0.0,
                "detail": "",
                "counter_sources": [],
                "signed_by": "local",
            })
        g._recompute(rec)
        assert rec["scores"]["factual_accuracy"] < before

    def test_scores_stay_in_unit_range(self):
        g = SourceTrustGraph.__new__(SourceTrustGraph)
        rec = self._make_rec("example.com")
        for _ in range(50):
            rec["observations"].append({
                "date": "2026-06-09",
                "type": "test",
                "dimension": "factual_accuracy",
                "signal": 0.0,
                "detail": "",
                "counter_sources": [],
                "signed_by": "local",
            })
        g._recompute(rec)
        for dim in DIMENSIONS:
            v = rec["scores"][dim]
            assert 0.0 <= v <= 1.0, f"Dimension {dim} out of range: {v}"
        assert 0.0 <= rec["trust_score"] <= 1.0

    def test_trust_score_updated(self):
        g = SourceTrustGraph.__new__(SourceTrustGraph)
        rec = self._make_rec("unknown.test")
        original = rec["trust_score"]
        for _ in range(20):
            rec["observations"].append({
                "date": "2026-06-09",
                "type": "claim_verified",
                "dimension": "factual_accuracy",
                "signal": 1.0,
                "detail": "",
                "counter_sources": [],
                "signed_by": "local",
            })
        g._recompute(rec)
        assert rec["trust_score"] != original


# ── Full observe→score lifecycle ──────────────────────────────────────────────

class TestObserveLifecycle:
    def test_observe_creates_new_source(self, graph):
        rec = graph.observe(
            "fake-news.test", "claim_disputed", "factual_accuracy", 0.2,
        )
        assert rec is not None
        assert rec["domain"] == "fake-news.test"

    def test_observe_persists_to_disk(self, graph, tmpdir):
        graph.observe("example.com", "claim_verified", "factual_accuracy", 0.9)
        trust_file = tmpdir / "source_trust.json"
        assert trust_file.exists()
        data = json.loads(trust_file.read_text(encoding="utf-8"))
        assert "example.com" in data["sources"]

    def test_positive_observations_raise_composite(self, graph):
        before = graph.score_for("example.com")
        for _ in range(20):
            graph.observe("example.com", "claim_verified", "factual_accuracy", 1.0)
        after = graph.score_for("example.com")
        assert after > before

    def test_negative_observations_lower_composite(self, graph):
        before = graph.score_for("fake-news.test")
        for _ in range(20):
            graph.observe("fake-news.test", "minority_claim", "factual_accuracy", 0.0)
        after = graph.score_for("fake-news.test")
        assert after < before

    def test_score_for_unknown_returns_neutral(self, graph):
        score = graph.score_for("never-seen.test")
        assert 0.4 <= score <= 0.65

    def test_observe_rejects_unknown_dimension(self, graph):
        result = graph.observe("example.com", "weird_type", "nonexistent_dim", 0.5)
        assert result is None

    def test_observe_rejects_bad_signal(self, graph):
        result = graph.observe("example.com", "test", "factual_accuracy", "not_a_number")
        assert result is None

    def test_observe_clamps_signal_above_one(self, graph):
        rec = graph.observe("example.com", "claim_verified", "factual_accuracy", 999.0)
        # Should succeed but clamp signal to 1.0
        assert rec is not None
        last_obs = rec["observations"][-1]
        assert last_obs["signal"] <= 1.0

    def test_observe_clamps_signal_below_zero(self, graph):
        rec = graph.observe("example.com", "claim_disputed", "factual_accuracy", -999.0)
        assert rec is not None
        last_obs = rec["observations"][-1]
        assert last_obs["signal"] >= 0.0

    def test_observe_with_url_input(self, graph):
        rec = graph.observe(
            "https://www.example.com/article?x=1",
            "claim_verified", "factual_accuracy", 0.9
        )
        assert rec is not None
        assert rec["domain"] == "example.com"

    def test_observe_empty_domain_returns_none(self, graph):
        result = graph.observe("", "claim_verified", "factual_accuracy", 0.9)
        assert result is None

    def test_observations_capped_at_max(self, graph):
        for i in range(_MAX_OBSERVATIONS + 50):
            graph._append(
                graph._get_or_create(graph._load(), "cap-test.example"),
                "test", "factual_accuracy", 0.5, detail=f"obs {i}",
            )
        data = graph._load()
        # Use a fresh rec to trigger cap via observe()
        for i in range(_MAX_OBSERVATIONS + 50):
            graph.observe("captest2.example", "test", "factual_accuracy", 0.5)
        rec = graph.get("captest2.example")
        assert len(rec["observations"]) <= _MAX_OBSERVATIONS

    def test_get_returns_none_for_unknown(self, graph):
        assert graph.get("totally-unknown.test") is None

    def test_score_for_high_seed(self, graph):
        # reuters.com should have a high seed even before any observations
        score = graph.score_for("reuters.com")
        assert score >= 0.7

    def test_score_for_low_seed(self, graph):
        score = graph.score_for("infowars.com")
        assert score < 0.4

    def test_dimensions_for_unknown_returns_seed(self, graph):
        dims = graph.dimensions_for("never-seen.test")
        assert set(dims.keys()) == set(DIMENSIONS)

    def test_all_sources_empty_on_fresh_graph(self, graph):
        assert graph.all_sources() == []

    def test_all_sources_after_observe(self, graph):
        graph.observe("example.com", "claim_verified", "factual_accuracy", 0.8)
        sources = graph.all_sources()
        assert len(sources) == 1
        assert sources[0]["domain"] == "example.com"

    def test_record_article_seen_increments_count(self, graph):
        graph.record_article_seen("example.com")
        graph.record_article_seen("example.com")
        rec = graph.get("example.com")
        assert rec["article_count"] == 2

    def test_leaderboard_sorted_by_trust_score(self, graph):
        for _ in range(10):
            graph.observe("good-source.test", "claim_verified", "factual_accuracy", 0.95)
        for _ in range(10):
            graph.observe("bad-source.test", "minority_claim", "factual_accuracy", 0.05)
        lb = graph.leaderboard()
        scores = [r["trust_score"] for r in lb]
        assert scores == sorted(scores, reverse=True)


# ── User actions ──────────────────────────────────────────────────────────────

class TestUserActions:
    def test_ban_sets_flag(self, graph):
        graph.record_user_action("example.com", "ban")
        rec = graph.get("example.com")
        assert rec["user_actions"]["banned"] is True

    def test_unban_clears_flag(self, graph):
        graph.record_user_action("example.com", "ban")
        graph.record_user_action("example.com", "unban")
        rec = graph.get("example.com")
        assert rec["user_actions"]["banned"] is False

    def test_boost_sets_flag(self, graph):
        graph.record_user_action("example.com", "boost")
        rec = graph.get("example.com")
        assert rec["user_actions"]["boosted"] is True

    def test_click_increments_counter(self, graph):
        graph.record_user_action("example.com", "click")
        graph.record_user_action("example.com", "click")
        rec = graph.get("example.com")
        assert rec["user_actions"]["clicks"] == 2

    def test_read_later_increments_counter(self, graph):
        graph.record_user_action("example.com", "read_later")
        rec = graph.get("example.com")
        assert rec["user_actions"]["read_laters"] == 1


# ── analyze_fetch heuristics ──────────────────────────────────────────────────

class TestAnalyzeFetch:
    def _make_article(self, source, title="", snippet="", sentiment=None, url="", category=""):
        return {
            "source": source, "title": title, "snippet": snippet,
            "sentiment": sentiment, "url": url, "category": category,
        }

    def test_empty_pool_returns_zero_counts(self, graph):
        summary = graph.analyze_fetch([], [])
        assert all(isinstance(v, int) for v in summary.values())
        assert summary["articles"] == 0

    def test_correction_detection(self, graph):
        pool = [self._make_article(
            "example.com", title="Correction: earlier article had errors"
        )]
        summary = graph.analyze_fetch(pool, [])
        assert summary["corrections"] >= 1

    def test_attribution_detection(self, graph):
        pool = [self._make_article(
            "example.com",
            snippet="According to official records, the company filed a 10-K yesterday."
        )]
        summary = graph.analyze_fetch(pool, [])
        assert summary["attribution"] >= 1

    def test_opinion_labeled_detection(self, graph):
        pool = [self._make_article(
            "example.com",
            url="/opinion/why-things-matter",
            title="Why this matters"
        )]
        summary = graph.analyze_fetch(pool, [])
        assert summary["opinion"] >= 1

    def test_articles_counter_matches_pool_size(self, graph):
        pool = [
            self._make_article("a.test", snippet="x"),
            self._make_article("b.test", snippet="y"),
        ]
        summary = graph.analyze_fetch(pool, [])
        assert summary["articles"] == 2

    def test_cluster_primary_boost(self, graph):
        arts = [
            self._make_article("a.test", snippet="court filing shows defendant guilty"),
            self._make_article("b.test", snippet="court filing shows defendant guilty"),
        ]
        cluster = {"articles": arts, "source_count": 2, "headline": "Court filing"}
        summary = graph.analyze_fetch([], [cluster])
        assert summary["primary_boosts"] >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
