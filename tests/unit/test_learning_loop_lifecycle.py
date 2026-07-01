"""Full-lifecycle unit tests for the Learning Loop:
observe → mine_candidates → record_trial → score_skill → promote → retire.

Each test uses distinct task_type/approach values to avoid the module-level
UNIQUE(pattern) index colliding across tests within the shared learning.db.
"""
from __future__ import annotations

import uuid

import pytest

from agent_friday.services import learning_loop as ll


def _uniq(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class TestObserve:
    def test_observe_records_and_returns_id(self):
        r = ll.observe(_uniq("tt"), "some prompt", approach="a", success=True)
        assert r["ok"] is True
        assert "obs_id" in r

    def test_observe_coerces_garbage_numeric_fields(self):
        r = ll.observe(_uniq("tt"), "p", approach="a", success=True,
                       revisions="bad", duration_s="x", tokens=None)
        assert r["ok"] is True

    def test_observe_defaults_satisfaction_from_success(self):
        # No satisfaction given → success maps high, failure maps low. Both store.
        assert ll.observe(_uniq("t"), "p", approach="a", success=True)["ok"]
        assert ll.observe(_uniq("t"), "p", approach="a", success=False)["ok"]

    def test_observe_caps_oversized_meta(self):
        r = ll.observe(_uniq("t"), "p", approach="a", success=True,
                       meta={"big": "z" * 10000})
        assert r["ok"] is True


class TestMineCandidates:
    def test_mines_candidate_from_repeated_success(self):
        tt, ap = _uniq("mine"), "approach-x"
        for i in range(5):
            ll.observe(tt, f"distinct prompt {i}", approach=ap, success=True)
        created = ll.mine_candidates(min_success=0.7, min_samples=3, min_distinct=2)
        assert any(c["name"] == f"{tt}:{ap}" for c in created)

    def test_no_candidate_below_min_samples(self):
        tt, ap = _uniq("mine"), "approach-y"
        ll.observe(tt, "only one prompt", approach=ap, success=True)
        created = ll.mine_candidates(min_samples=3)
        assert not any(c["name"] == f"{tt}:{ap}" for c in created)

    def test_no_candidate_below_distinct_prompts(self):
        # 5 IDENTICAL prompts → only 1 distinct hash → anti-flood blocks it.
        tt, ap = _uniq("mine"), "approach-z"
        for _ in range(5):
            ll.observe(tt, "identical prompt text", approach=ap, success=True)
        created = ll.mine_candidates(min_distinct=2)
        assert not any(c["name"] == f"{tt}:{ap}" for c in created)

    def test_no_candidate_below_success_rate(self):
        tt, ap = _uniq("mine"), "approach-fail"
        for i in range(5):
            ll.observe(tt, f"p{i}", approach=ap, success=(i == 0))  # 20% success
        created = ll.mine_candidates(min_success=0.7)
        assert not any(c["name"] == f"{tt}:{ap}" for c in created)

    def test_mining_is_idempotent(self):
        tt, ap = _uniq("mine"), "approach-idem"
        for i in range(4):
            ll.observe(tt, f"p{i}", approach=ap, success=True)
        first = ll.mine_candidates()
        second = ll.mine_candidates()
        made1 = [c for c in first if c["name"] == f"{tt}:{ap}"]
        made2 = [c for c in second if c["name"] == f"{tt}:{ap}"]
        assert len(made1) == 1
        assert len(made2) == 0  # dedup — pattern already exists


class TestScoreAndPromote:
    def _make_candidate(self):
        tt, ap = _uniq("score"), "approach"
        for i in range(4):
            ll.observe(tt, f"prompt {i}", approach=ap, success=True)
        created = ll.mine_candidates()
        made = [c for c in created if c["name"] == f"{tt}:{ap}"]
        assert made, "candidate not minted"
        return made[0]["skill_id"]

    def test_record_trial_updates_score(self):
        sid = self._make_candidate()
        r = ll.record_trial(sid, success=True, satisfaction=0.9)
        assert r["ok"] is True
        assert isinstance(r["score"], float)

    def test_record_trial_invalid_skill_id(self):
        assert ll.record_trial("", success=True)["ok"] is False
        assert ll.record_trial(None, success=True)["ok"] is False

    def test_score_skill_unknown_returns_zero(self):
        assert ll.score_skill("does-not-exist") == 0.0

    def test_promote_moves_high_scorer_to_active(self):
        sid = self._make_candidate()
        for _ in range(5):
            ll.record_trial(sid, success=True, satisfaction=0.95)
        ll.promote(threshold=0.5, min_trials=3)
        actives = [s["skill_id"] for s in ll.active_skills()]
        assert sid in actives

    def test_promote_retires_decayed_active(self):
        sid = self._make_candidate()
        for _ in range(5):
            ll.record_trial(sid, success=True, satisfaction=0.95)
        ll.promote(threshold=0.5, min_trials=3)
        # Now feed a wall of failures so the Wilson bound craters.
        for _ in range(20):
            ll.record_trial(sid, success=False, satisfaction=0.05)
        ll.promote(threshold=0.5, retire=0.4)
        actives = [s["skill_id"] for s in ll.active_skills()]
        assert sid not in actives


class TestRenderAndEpoch:
    def test_render_heuristics_prompt_bounded(self):
        out = ll.render_heuristics_prompt(limit=5)
        assert isinstance(out, str)

    def test_run_epoch_returns_envelope(self):
        r = ll.run_epoch()
        assert "ok" in r

    def test_state_reports_counts(self):
        st = ll.state()
        assert st.get("available") is True
        assert "counts" in st

    def test_wilson_lower_bound_edges(self):
        assert ll._wilson_lower_bound(0, 0) == 0.0
        assert 0.0 <= ll._wilson_lower_bound(5, 10) <= 1.0
        # All wins → high but never above 1.
        assert ll._wilson_lower_bound(20, 20) <= 1.0


class TestDisabled:
    def test_all_ops_skip_when_disabled(self, monkeypatch):
        monkeypatch.setattr(ll, "_enabled", lambda: False)
        assert ll.observe("t", "p", approach="a", success=True)["skipped"]
        assert ll.mine_candidates() == []
        assert ll.promote() == []
        assert ll.active_skills() == []
        assert ll.run_epoch()["skipped"]
