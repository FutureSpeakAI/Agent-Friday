"""Friday's honesty rules are fixed text; no score steers the prompt.

Red on main 7c49be86:
  * the EPISTEMIC STATE block carried the keyword-scored average and told
    the model to "increase pushback" when it fell (metric-chasing);
  * a behavioural-monitor event was written as a synthetic low-scoring
    turn, dragging the pushback average the sycophancy check reads;
  * the weekly editorial was regenerated "for stronger pushback" whenever
    an essay scored the neutral 0.5, which is every essay with no teach or
    do phrase.
"""
from __future__ import annotations

import pytest

from agent_friday import epistemic_engine as ee
from agent_friday.services import model_router as mr


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "HISTORY_PATH", tmp_path / "h.jsonl")
    monkeypatch.setattr(ee, "SCORES_PATH", tmp_path / "s.json")
    monkeypatch.setattr(ee, "GOVERNANCE_EVENTS_PATH", tmp_path / "g.jsonl")
    monkeypatch.setattr(ee, "FRIDAY_DIR", tmp_path)
    return ee.EpistemicEngine()


class TestFixedPolicy:
    def test_the_prompt_text_is_identical_across_scores_and_carries_none(self, engine, monkeypatch):
        texts = []
        for overall in (0.1, 0.9):
            monkeypatch.setattr(engine, "get_scores", lambda o=overall: {
                "overall": o, "dimensions": {"information_gain": o, "pushback_rate": o,
                                              "socratic_ratio": o, "independence_fostering": o}})
            texts.append(engine.get_prompt_injection())
        assert texts[0] == texts[1]
        for t in texts:
            assert "Increase pushback" not in t and "epistemic score" not in t.lower()
            assert "0.1" not in t and "0.9" not in t
            assert "I was wrong" in t and "new evidence" in t

    def test_the_built_prompt_has_the_honesty_block_and_no_epistemic_state(self, monkeypatch):
        seen = []

        class _E:
            def get_prompt_injection(self):
                return "SHOULD NOT BE READ"

            def get_scores(self):
                return {"overall": 0.1, "dimensions": {}}
        monkeypatch.setattr(ee, "get_epistemic_engine", lambda: _E())
        p1, _ = mr._build_context_prompt("hello", workspace="chat", provider="local")
        assert "== HONESTY ==" in p1 and ee.HONESTY_POLICY in p1
        assert "== EPISTEMIC STATE ==" not in p1 and "SHOULD NOT BE READ" not in p1
        assert "Independence score:" not in p1
        seen.append(p1.split("== HONESTY ==")[1].split("\n==")[0])
        p2, _ = mr._build_context_prompt("hello", workspace="chat", provider="local")
        assert p2.split("== HONESTY ==")[1].split("\n==")[0] == seen[0]

    def test_the_policy_is_registered_as_trusted_text(self):
        from agent_friday.services import egress_gate as eg
        mr._honesty_rules_text()
        assert ee.HONESTY_POLICY.strip() in eg._TRUSTED_TEXTS


class TestGovernanceEventIsNotATurn:
    def test_a_monitor_event_leaves_pushback_rate_unchanged(self, engine):
        engine.score_turn("Should I invest everything?", "Actually, I disagree: that is too concentrated.")
        before = engine.get_scores()["dimensions"]["pushback_rate"]
        turns_before = engine.get_scores()["total_turns_scored"]
        engine.register_governance_event(0.9, "an agent loop tripped the risk threshold")
        after = engine.get_scores()
        assert after["dimensions"]["pushback_rate"] == before
        assert after["total_turns_scored"] == turns_before
        assert (engine.governance_events() or [])[-1]["severity"] == 0.9

    def test_the_monitor_still_calls_it(self):
        from agent_friday.governance import behavioral_monitor as bm
        src = open(bm.__file__, encoding="utf-8").read()
        assert "register_governance_event" in src


class TestEditorialNotRewrittenForNeutral:
    def test_a_neutral_essay_is_composed_once(self, monkeypatch, tmp_path):
        from agent_friday.services import news_engine as ne
        calls = []
        monkeypatch.setattr(ne, "_generate_text",
                            lambda messages, **k: calls.append(k) or "An essay with no teach or do phrases.")
        monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda **k: "sys", raising=False)
        monkeypatch.setattr(ne, "_predict_route_provider", lambda **k: "local", raising=False)
        monkeypatch.setattr(ne, "_gated_vault_control", lambda: None, raising=False)
        monkeypatch.setattr(ne, "_gather_editorial_pool",
                            lambda days=7: [{"source": "a.test", "title": "Story", "snippet": "x"}])
        monkeypatch.setattr(ne, "_load_banned_sources", lambda: [], raising=False)
        monkeypatch.setattr(ne, "EDITORIALS_DIR", tmp_path)
        monkeypatch.setattr(ne, "_editorial_independence_score", lambda text: 0.5)
        monkeypatch.setattr(ne, "_notify_weekly_editorial", lambda *a, **k: None, raising=False)
        out = ne._generate_weekly_editorial()
        assert len(calls) == 1, "a neutral essay was regenerated"
        assert out.get("regenerated") is False and out.get("independence_score") == 0.5
