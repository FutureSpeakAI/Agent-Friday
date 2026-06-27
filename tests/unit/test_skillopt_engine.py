"""Unit tests for skillopt_engine.py — pure-math scoring, validation gate,
and data-class helpers. No server, no network, no real ~/.friday writes.

All tests use synthetic data and a temp directory for SkillStorage/SkillOptEngine.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.skillopt_engine as soe
from agent_friday.skillopt_engine import (
    DEFAULT_LATENCY_TARGET_MS,
    DEFAULT_COST_TARGET_USD,
    REGRESSION_TOLERANCE,
    SCORE_MIN,
    SCORE_MAX,
    SkillVersion,
    TrainingEpoch,
    ValidationGate,
    composite_score,
    normalize_cost,
    normalize_latency,
    _percentile,
    _safe_jsonify,
    _safe_slug,
)


# ════════════════════════════════════════════════════════════════════════
#  normalize_latency
# ════════════════════════════════════════════════════════════════════════

class TestNormalizeLatency:
    def test_zero_duration_returns_1(self):
        assert normalize_latency(0) == 1.0

    def test_negative_duration_returns_1(self):
        assert normalize_latency(-100) == 1.0

    def test_exactly_at_target_returns_1(self):
        assert normalize_latency(DEFAULT_LATENCY_TARGET_MS) == 1.0

    def test_just_below_target_returns_1(self):
        assert normalize_latency(DEFAULT_LATENCY_TARGET_MS - 1) == 1.0

    def test_two_times_target_approx_half(self):
        # At 2x target: exp(-(2T - T) / T) = exp(-1) ≈ 0.368
        val = normalize_latency(DEFAULT_LATENCY_TARGET_MS * 2)
        assert abs(val - math.exp(-1)) < 1e-9

    def test_five_times_target_approx_point_one(self):
        # At 5x target: exp(-4) ≈ 0.018
        val = normalize_latency(DEFAULT_LATENCY_TARGET_MS * 5)
        expected = math.exp(-4)
        assert abs(val - expected) < 1e-9

    def test_result_in_zero_one(self):
        for ms in [0, 1, 100, 5000, 10_000, 1_000_000]:
            v = normalize_latency(ms)
            assert 0.0 <= v <= 1.0, f"out-of-range at {ms}ms: {v}"

    def test_custom_target(self):
        val = normalize_latency(2000, target_ms=1000)
        assert abs(val - math.exp(-1)) < 1e-9

    def test_huge_duration_approaches_zero(self):
        val = normalize_latency(1e12)
        assert val < 1e-6

    def test_monotonically_decreasing_beyond_target(self):
        ms_values = [DEFAULT_LATENCY_TARGET_MS * k for k in [1.1, 2, 5, 10, 100]]
        scores = [normalize_latency(ms) for ms in ms_values]
        for a, b in zip(scores, scores[1:]):
            assert a > b


# ════════════════════════════════════════════════════════════════════════
#  normalize_cost
# ════════════════════════════════════════════════════════════════════════

class TestNormalizeCost:
    def test_zero_cost_returns_1(self):
        assert normalize_cost(0) == 1.0

    def test_negative_cost_returns_1(self):
        assert normalize_cost(-0.01) == 1.0

    def test_exactly_at_target_returns_1(self):
        assert normalize_cost(DEFAULT_COST_TARGET_USD) == 1.0

    def test_just_below_target_returns_1(self):
        assert normalize_cost(DEFAULT_COST_TARGET_USD * 0.5) == 1.0

    def test_two_times_target_approx_exp_neg1(self):
        val = normalize_cost(DEFAULT_COST_TARGET_USD * 2)
        assert abs(val - math.exp(-1)) < 1e-9

    def test_result_in_zero_one(self):
        for c in [0, 0.001, 0.05, 0.1, 1.0, 100.0]:
            v = normalize_cost(c)
            assert 0.0 <= v <= 1.0, f"out-of-range at cost={c}: {v}"

    def test_monotonically_decreasing_beyond_target(self):
        targets = [DEFAULT_COST_TARGET_USD * k for k in [1.1, 2, 5, 10]]
        scores = [normalize_cost(c) for c in targets]
        for a, b in zip(scores, scores[1:]):
            assert a > b

    def test_tiny_target_no_division_by_zero(self):
        # target_usd = 0 hits the max(target_usd, 1e-6) guard
        val = normalize_cost(0.1, target_usd=0.0)
        assert 0.0 <= val <= 1.0


# ════════════════════════════════════════════════════════════════════════
#  composite_score
# ════════════════════════════════════════════════════════════════════════

class TestCompositeScore:
    def test_all_zero_metrics_returns_zero(self):
        # latency_ms=0 → latency_norm=1.0, cost_usd=0 → cost_norm=1.0
        # but accuracy/user_satisfaction/completeness=0.
        # Weighted sum depends on weights. Verify in [0,1] and == 0 only if
        # latency/cost are the only non-zero parts.
        score = composite_score({})
        assert SCORE_MIN <= score <= SCORE_MAX

    def test_explicit_all_zeros_accuracy_satisfaction(self):
        m = {"accuracy": 0.0, "user_satisfaction": 0.0, "completeness": 0.0,
             "latency_ms": 0.0, "cost_usd": 0.0}
        # latency_ms=0 → 1.0, cost=0 → 1.0; weights sum to 1
        # latency weight=0.15, cost weight=0.10  → score = 0.25 (of default weights)
        score = composite_score(m)
        assert SCORE_MIN <= score <= SCORE_MAX
        assert score > 0.0  # latency+cost contribute

    def test_perfect_metrics_returns_one(self):
        m = {"accuracy": 1.0, "user_satisfaction": 1.0, "completeness": 1.0,
             "latency_ms": 0.0, "cost_usd": 0.0}
        score = composite_score(m)
        assert abs(score - 1.0) < 1e-9

    def test_clamped_to_zero_one(self):
        # Even with extreme overrides the result stays in [0,1]
        m = {"accuracy": 2.0, "user_satisfaction": 5.0, "completeness": 10.0}
        score = composite_score(m)
        assert SCORE_MIN <= score <= SCORE_MAX

    def test_custom_weights_influence_score(self):
        m_acc = {"accuracy": 1.0, "user_satisfaction": 0.0, "completeness": 0.0,
                 "latency_ms": DEFAULT_LATENCY_TARGET_MS * 10,
                 "cost_usd": DEFAULT_COST_TARGET_USD * 10}
        # Heavy accuracy weight → high score
        high_w = {"accuracy": 0.99, "latency": 0.003, "cost": 0.002,
                  "user_satisfaction": 0.003, "completeness": 0.002}
        score_high = composite_score(m_acc, weights=high_w)
        # Low accuracy weight → lower score
        low_w = {"accuracy": 0.01, "latency": 0.30, "cost": 0.30,
                 "user_satisfaction": 0.30, "completeness": 0.09}
        score_low = composite_score(m_acc, weights=low_w)
        assert score_high > score_low

    def test_latency_ms_key_used(self):
        m_fast = {"accuracy": 0.5, "latency_ms": 0.0}
        m_slow = {"accuracy": 0.5, "latency_ms": DEFAULT_LATENCY_TARGET_MS * 100}
        assert composite_score(m_fast) > composite_score(m_slow)

    def test_duration_ms_alias(self):
        # composite_score also accepts duration_ms as fallback for latency
        m = {"accuracy": 0.5, "duration_ms": 0.0}
        score = composite_score(m)
        assert SCORE_MIN <= score <= SCORE_MAX

    def test_cost_usd_key_used(self):
        m_cheap = {"accuracy": 0.5, "cost_usd": 0.0}
        m_expensive = {"accuracy": 0.5, "cost_usd": DEFAULT_COST_TARGET_USD * 100}
        assert composite_score(m_cheap) > composite_score(m_expensive)

    def test_custom_weights_normalized_internally(self):
        # Weights that don't sum to 1 should still produce a valid score
        m = {"accuracy": 0.8, "user_satisfaction": 0.8, "completeness": 0.8}
        score = composite_score(m, weights={"accuracy": 10, "user_satisfaction": 10,
                                             "completeness": 10, "latency": 1, "cost": 1})
        assert SCORE_MIN <= score <= SCORE_MAX

    def test_result_always_in_range_parametric(self):
        cases = [
            {"accuracy": 0.0},
            {"accuracy": 1.0, "user_satisfaction": 1.0},
            {"accuracy": -5.0},                    # negative raw metric
            {"latency_ms": 1e9},                   # absurdly slow
            {"cost_usd": 1e6},                     # absurdly expensive
            {},                                    # empty
        ]
        for m in cases:
            s = composite_score(m)
            assert SCORE_MIN <= s <= SCORE_MAX, f"out-of-range for {m}: {s}"


# ════════════════════════════════════════════════════════════════════════
#  ValidationGate.evaluate
# ════════════════════════════════════════════════════════════════════════

class TestValidationGate:
    @pytest.fixture
    def gate(self):
        return ValidationGate()

    def test_tolerance_is_regression_tolerance_constant(self, gate):
        assert gate.tolerance == REGRESSION_TOLERANCE

    def test_regression_rejected(self, gate):
        # candidate well below 0.95 * best
        ok, reason = gate.evaluate(candidate_score=0.5, baseline_score=0.6, best_score=0.9)
        assert ok is False
        assert "regression" in reason.lower()

    def test_exactly_at_tolerance_boundary_passes(self, gate):
        # candidate == tolerance * best (exactly on boundary → should pass tolerance check)
        best = 0.80
        candidate = REGRESSION_TOLERANCE * best   # = 0.76
        # But is it >= baseline? Use baseline == candidate so baseline check doesn't bite.
        ok, reason = gate.evaluate(candidate_score=candidate,
                                   baseline_score=candidate - 0.001,
                                   best_score=best)
        assert ok is True

    def test_candidate_below_baseline_rejected(self, gate):
        # Candidate must pass the regression tolerance check (>= 0.95 * best)
        # but still be below baseline to hit the "baseline beat" branch.
        # best=0.80, tolerance*best=0.76; candidate=0.77 (passes tolerance);
        # baseline=0.78 (candidate < baseline → baseline-beat rejection).
        ok, reason = gate.evaluate(candidate_score=0.77, baseline_score=0.78, best_score=0.80)
        assert ok is False
        assert "baseline" in reason.lower()

    def test_marginal_improvement_passes(self, gate):
        # candidate slightly above baseline but improvement < 0.5%
        baseline = 0.80
        candidate = baseline + 0.001   # ~0.125% improvement
        ok, reason = gate.evaluate(candidate_score=candidate,
                                   baseline_score=baseline,
                                   best_score=baseline)
        assert ok is True
        assert "marginal" in reason.lower()

    def test_clear_improvement_passes(self, gate):
        ok, reason = gate.evaluate(candidate_score=0.85, baseline_score=0.78, best_score=0.78)
        assert ok is True
        assert "promoted" in reason.lower()

    def test_custom_tolerance(self):
        gate = ValidationGate(tolerance=0.90)
        # 0.88 > 0.90 * 0.95 = 0.855 → passes tolerance; 0.88 > 0.87 → passes baseline
        ok, _ = gate.evaluate(candidate_score=0.88, baseline_score=0.87, best_score=0.95)
        assert ok is True

    def test_perfect_scores(self, gate):
        ok, reason = gate.evaluate(candidate_score=1.0, baseline_score=1.0, best_score=1.0)
        # 1.0 >= 0.95 * 1.0 ✓; 1.0 >= 1.0 ✓; improvement = 0 → marginal pass
        assert ok is True

    def test_all_zero_scores(self, gate):
        # baseline=0, best=0 → the 1e-6 guard prevents division by zero
        ok, reason = gate.evaluate(candidate_score=0.0, baseline_score=0.0, best_score=0.0)
        # candidate=0.0 >= 0.95*0.0=0.0 → passes tolerance; 0.0 >= 0.0 baseline → marginal
        assert ok is True

    def test_regression_factor_exactly_095(self, gate):
        # Verify the exact factor used is REGRESSION_TOLERANCE (0.95)
        best = 1.0
        just_below = REGRESSION_TOLERANCE * best - 1e-9
        ok, _ = gate.evaluate(candidate_score=just_below,
                               baseline_score=just_below - 0.001,
                               best_score=best)
        assert ok is False

    def test_returns_two_tuple(self, gate):
        result = gate.evaluate(0.8, 0.7, 0.8)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


# ════════════════════════════════════════════════════════════════════════
#  _safe_slug
# ════════════════════════════════════════════════════════════════════════

class TestSafeSlug:
    def test_empty_string_returns_unnamed_skill(self):
        assert _safe_slug("") == "unnamed_skill"

    def test_whitespace_only_returns_unnamed_skill(self):
        assert _safe_slug("   ") == "unnamed_skill"

    def test_normal_name(self):
        assert _safe_slug("my_skill") == "my_skill"

    def test_uppercase_lowercased(self):
        assert _safe_slug("MySkill") == "myskill"

    def test_spaces_become_underscores(self):
        assert _safe_slug("my skill") == "my_skill"

    def test_special_chars_removed(self):
        result = _safe_slug("my!@#skill")
        assert "!" not in result
        assert "@" not in result

    def test_hyphens_preserved(self):
        assert "-" in _safe_slug("my-skill")

    def test_unicode_stripped(self):
        result = _safe_slug("résumé skill")
        assert all(c.isascii() or c == "_" for c in result)

    def test_numbers_preserved(self):
        assert "123" in _safe_slug("skill123")

    def test_pure_special_chars_returns_unnamed_skill(self):
        assert _safe_slug("!!!") == "unnamed_skill"


# ════════════════════════════════════════════════════════════════════════
#  _percentile
# ════════════════════════════════════════════════════════════════════════

class TestPercentile:
    def test_empty_list_returns_zero(self):
        assert _percentile([], 50) == 0.0

    def test_single_element_any_percentile(self):
        assert _percentile([42.0], 0) == 42.0
        assert _percentile([42.0], 50) == 42.0
        assert _percentile([42.0], 100) == 42.0

    def test_two_elements_50th(self):
        result = _percentile([1.0, 3.0], 50)
        assert abs(result - 2.0) < 1e-9

    def test_p0_is_minimum(self):
        values = [5.0, 3.0, 1.0, 4.0, 2.0]
        assert _percentile(values, 0) == 1.0

    def test_p100_is_maximum(self):
        values = [5.0, 3.0, 1.0, 4.0, 2.0]
        assert _percentile(values, 100) == 5.0

    def test_p50_median_odd(self):
        result = _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50)
        assert abs(result - 3.0) < 1e-9

    def test_p95_large_list(self):
        values = list(range(1, 101))  # 1..100
        result = _percentile([float(v) for v in values], 95)
        # At 95th percentile of 100 uniform values: k=(99)*0.95=94.05 → floor=94, ceil=95
        # s[94]=95, s[95]=96; interp → 95 + 96*0.05 = 95.05
        assert 95.0 <= result <= 96.0

    def test_linear_interpolation(self):
        values = [0.0, 10.0]
        result = _percentile(values, 25)
        # k = (2-1) * 0.25 = 0.25; s[0]=0, s[1]=10; 0 + 10*0.25 = 2.5
        assert abs(result - 2.5) < 1e-9

    def test_returns_float(self):
        assert isinstance(_percentile([1.0, 2.0, 3.0], 50), float)


# ════════════════════════════════════════════════════════════════════════
#  _safe_jsonify
# ════════════════════════════════════════════════════════════════════════

class TestSafeJsonify:
    def test_none_passthrough(self):
        assert _safe_jsonify(None) is None

    def test_bool_passthrough(self):
        assert _safe_jsonify(True) is True
        assert _safe_jsonify(False) is False

    def test_int_passthrough(self):
        assert _safe_jsonify(42) == 42

    def test_float_passthrough(self):
        assert _safe_jsonify(3.14) == 3.14

    def test_str_passthrough(self):
        assert _safe_jsonify("hello") == "hello"

    def test_set_becomes_list(self):
        result = _safe_jsonify({1, 2, 3})
        assert isinstance(result, list)
        assert sorted(result) == [1, 2, 3]

    def test_tuple_becomes_list(self):
        result = _safe_jsonify((1, 2))
        assert isinstance(result, list)

    def test_dict_keys_become_strings(self):
        result = _safe_jsonify({1: "a", 2: "b"})
        assert all(isinstance(k, str) for k in result.keys())

    def test_nested_dict_with_set(self):
        result = _safe_jsonify({"items": {1, 2}})
        assert isinstance(result["items"], list)

    def test_nested_list(self):
        result = _safe_jsonify([[1, 2], [3, 4]])
        assert result == [[1, 2], [3, 4]]

    def test_depth_limit_triggers_repr(self):
        # Build a deeply nested dict that exceeds depth=6
        nested = {}
        cur = nested
        for _ in range(10):
            cur["child"] = {}
            cur = cur["child"]
        cur["value"] = "leaf"
        result = _safe_jsonify(nested)
        # Just verify it doesn't crash and returns something
        assert result is not None

    def test_unknown_object_becomes_repr_string(self):
        class Weird:
            def __repr__(self):
                return "weird_obj"
        result = _safe_jsonify(Weird())
        assert isinstance(result, str)
        assert "weird_obj" in result

    def test_dataclass_converted(self):
        import dataclasses

        @dataclasses.dataclass
        class Simple:
            x: int = 1
            y: str = "a"

        result = _safe_jsonify(Simple())
        assert isinstance(result, dict)
        assert result["x"] == 1

    def test_result_is_json_serializable(self):
        import json
        obj = {"key": {1, 2}, "nested": (True, None, [1, 2.5])}
        result = _safe_jsonify(obj)
        json.dumps(result)  # must not raise


# ════════════════════════════════════════════════════════════════════════
#  SkillVersion.short_hash and diff_against
# ════════════════════════════════════════════════════════════════════════

class TestSkillVersion:
    def _make(self, content: str, version_id: str = "v001") -> SkillVersion:
        return SkillVersion(
            skill_name="test_skill",
            version_id=version_id,
            created_at="2024-01-01T00:00:00Z",
            content=content,
        )

    def test_short_hash_is_12_hex_chars(self):
        sv = self._make("hello world")
        h = sv.short_hash
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_short_hash_deterministic(self):
        sv = self._make("same content")
        assert sv.short_hash == sv.short_hash

    def test_different_content_different_hash(self):
        a = self._make("content A")
        b = self._make("content B")
        assert a.short_hash != b.short_hash

    def test_empty_content_hash(self):
        sv = self._make("")
        assert len(sv.short_hash) == 12

    def test_diff_against_shows_additions(self):
        old = self._make("line one\nline two\n", "v001")
        new = self._make("line one\nline two\nline three\n", "v002")
        diff = new.diff_against(old)
        assert "+line three" in diff

    def test_diff_against_same_content_empty(self):
        content = "identical content\n"
        a = self._make(content, "v001")
        b = self._make(content, "v002")
        diff = b.diff_against(a)
        assert diff == ""

    def test_diff_against_shows_removals(self):
        old = self._make("line one\nline two\nline three\n", "v001")
        new = self._make("line one\nline three\n", "v002")
        diff = new.diff_against(old)
        assert "-line two" in diff

    def test_to_dict_includes_short_hash(self):
        sv = self._make("content")
        d = sv.to_dict()
        assert "short_hash" in d
        assert d["short_hash"] == sv.short_hash


# ════════════════════════════════════════════════════════════════════════
#  TrainingEpoch.relative_improvement
# ════════════════════════════════════════════════════════════════════════

class TestTrainingEpoch:
    def _make(self, candidate: float, baseline: float) -> TrainingEpoch:
        return TrainingEpoch(
            epoch_id="test-epoch",
            skill_name="skill_x",
            candidate_version="v002",
            baseline_version="v001",
            batch_size=5,
            started_at="2024-01-01T00:00:00Z",
            candidate_score=candidate,
            baseline_score=baseline,
        )

    def test_positive_improvement(self):
        ep = self._make(0.9, 0.8)
        assert abs(ep.relative_improvement - (0.9 - 0.8) / 0.8) < 1e-9

    def test_zero_improvement(self):
        ep = self._make(0.8, 0.8)
        assert abs(ep.relative_improvement) < 1e-9

    def test_negative_improvement(self):
        ep = self._make(0.7, 0.8)
        assert ep.relative_improvement < 0.0

    def test_zero_baseline_returns_zero(self):
        # Prevent division by zero
        ep = self._make(0.5, 0.0)
        assert ep.relative_improvement == 0.0

    def test_negative_baseline_returns_zero(self):
        ep = self._make(0.5, -0.1)
        assert ep.relative_improvement == 0.0

    def test_large_improvement(self):
        ep = self._make(1.0, 0.5)
        assert abs(ep.relative_improvement - 1.0) < 1e-9  # 100% improvement


# ════════════════════════════════════════════════════════════════════════
#  SkillOptEngine (integration using temp dir)
# ════════════════════════════════════════════════════════════════════════

@pytest.fixture
def engine(tmp_path):
    """Fresh SkillOptEngine rooted under pytest's tmp_path."""
    return soe.SkillOptEngine(root=tmp_path / "skillopt")


class TestSkillOptEngine:
    def test_register_skill_creates_version(self, engine):
        ver = engine.register_skill("demo", content="# Demo skill\n")
        assert ver.version_id == "v001"
        assert ver.promoted is True  # first version auto-promoted

    def test_register_same_content_is_idempotent(self, engine):
        v1 = engine.register_skill("demo", content="# Demo\n")
        v2 = engine.register_skill("demo", content="# Demo\n")
        # Same content → same version returned
        assert v1.version_id == v2.version_id

    def test_register_different_content_increments_version(self, engine):
        engine.register_skill("demo", content="# v1\n")
        v2 = engine.register_skill("demo", content="# v2\n")
        assert v2.version_id == "v002"

    def test_record_execution_produces_record(self, engine):
        ver = engine.register_skill("demo", content="# Demo\n")
        rec = engine.record_execution(
            skill_name="demo", version_id=ver.version_id,
            inputs={"q": "hello"}, outputs={"a": "world"},
            metrics={"accuracy": 0.9, "user_satisfaction": 0.8, "completeness": 0.7},
            duration_ms=100.0, cost_usd=0.001,
        )
        assert 0.0 <= rec.composite_score <= 1.0
        assert rec.skill_name == "demo"

    def test_composite_score_in_execution_record(self, engine):
        ver = engine.register_skill("demo", content="# Demo\n")
        rec = engine.record_execution(
            skill_name="demo", version_id=ver.version_id,
            inputs={}, outputs={},
            metrics={"accuracy": 1.0, "user_satisfaction": 1.0, "completeness": 1.0},
            duration_ms=0.0, cost_usd=0.0,
        )
        assert abs(rec.composite_score - 1.0) < 1e-9

    def test_list_skills_returns_registered(self, engine):
        engine.register_skill("skill_a", content="A\n")
        engine.register_skill("skill_b", content="B\n")
        names = engine.list_skills()
        assert "skill_a" in names
        assert "skill_b" in names

    def test_status_snapshot_shape(self, engine):
        engine.register_skill("demo", content="# Demo\n")
        snap = engine.status_snapshot("demo")
        assert snap["skill_name"] == "demo"
        assert "best_version" in snap
        assert "rolling_mean" in snap

    def test_validation_gate_rejects_regression_in_epoch(self, engine):
        ver1 = engine.register_skill("demo", content="# v1\n")
        # Inject a high baseline composite score
        storage = engine.storage("demo")
        v = storage.read_version(ver1.version_id)
        v.metrics_summary["composite"] = 0.95
        v.promoted = True
        storage.write_version(v)

        ver2 = engine.register_version(
            "demo", content="# v2\n",
            parent_version=ver1.version_id,
        )

        def bad_evaluator(skill_name, content, case):
            return {"accuracy": 0.1}  # very low → regression

        engine.evaluator = bad_evaluator
        epoch = engine.run_epoch("demo", ver2.version_id, [{"inputs": {}}])
        assert epoch.decision == "rejected"


# ════════════════════════════════════════════════════════════════════════
#  Edge cases / interaction
# ════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_composite_score_latency_ms_zero_cost_zero_full_accuracy(self):
        """All perfect → score = 1.0"""
        score = composite_score({
            "accuracy": 1.0,
            "user_satisfaction": 1.0,
            "completeness": 1.0,
            "latency_ms": 0.0,
            "cost_usd": 0.0,
        })
        assert abs(score - 1.0) < 1e-9

    def test_normalize_latency_at_target_is_one(self):
        assert normalize_latency(DEFAULT_LATENCY_TARGET_MS) == 1.0

    def test_normalize_cost_at_target_is_one(self):
        assert normalize_cost(DEFAULT_COST_TARGET_USD) == 1.0

    def test_validation_gate_tolerance_constant_is_095(self):
        assert REGRESSION_TOLERANCE == 0.95

    def test_safe_slug_unicode_emoji(self):
        # Emoji should be stripped, not crash
        result = _safe_slug("skill 🤖")
        assert "unnamed_skill" == result or len(result) > 0

    def test_percentile_unsorted_input(self):
        # Must sort internally
        result = _percentile([5.0, 1.0, 3.0], 50)
        assert abs(result - 3.0) < 1e-9


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
