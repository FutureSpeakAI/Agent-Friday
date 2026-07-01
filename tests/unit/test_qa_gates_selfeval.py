"""Unit tests for qa_gates — config clamping, score parsing tolerance,
text evaluation with a stubbed model, and the generate→evaluate→improve/flag
gate loop. The model seam (_generate_text) is stubbed so no LLM is called.
"""
from __future__ import annotations

import pytest

from agent_friday.services import qa_gates as qa


class TestConfig:
    def test_defaults_present(self):
        cfg = qa.qa_config()
        assert "enabled" in cfg and "threshold" in cfg

    def test_threshold_clamped(self, monkeypatch):
        import agent_friday.core as core
        monkeypatch.setattr(core, "_load_settings",
                            lambda: {"qa_gates": {"threshold": 5.0, "max_retries": 99}})
        cfg = qa.qa_config()
        assert 0.0 <= cfg["threshold"] <= 1.0
        assert 0 <= cfg["max_retries"] <= 3


class TestScoreParsing:
    def test_parse_json_verdict(self):
        v = qa._parse_score('{"score": 0.8, "critique": "good", "suggestions": ""}')
        assert v["score"] == 0.8
        assert v["critique"] == "good"

    def test_parse_score_colon_form(self):
        v = qa._parse_score("score: 0.65 overall")
        assert v["score"] == pytest.approx(0.65)

    def test_parse_score_out_of_ten(self):
        v = qa._parse_score("score = 8/10")
        assert v["score"] == pytest.approx(0.8)

    def test_parse_bare_number(self):
        v = qa._parse_score("0.42 is my rating")
        assert v["score"] == pytest.approx(0.42)

    def test_norm_score_rescales(self):
        assert qa._norm_score(8) == pytest.approx(0.8)     # 0-10
        assert qa._norm_score(80) == pytest.approx(0.8)    # 0-100
        assert qa._norm_score(0.5) == 0.5
        assert qa._norm_score("bad") is None

    def test_unparseable_returns_none_score(self):
        assert qa._parse_score("no number at all")["score"] is None


class TestEvaluateText:
    def test_empty_content_skipped(self):
        r = qa.evaluate_text("", "intent")
        assert r["status"] == "skipped"
        assert r["passed"] is True

    def test_passing_score(self, monkeypatch):
        import agent_friday.services.model_router as mr
        monkeypatch.setattr(mr, "_generate_text",
                            lambda *a, **k: '{"score": 0.9, "critique": "great"}')
        r = qa.evaluate_text("some content", "make it great")
        assert r["status"] == "ok"
        assert r["passed"] is True

    def test_failing_score(self, monkeypatch):
        import agent_friday.services.model_router as mr
        monkeypatch.setattr(mr, "_generate_text",
                            lambda *a, **k: '{"score": 0.2, "critique": "weak", "suggestions": "redo"}')
        r = qa.evaluate_text("bad content", "be excellent")
        assert r["passed"] is False

    def test_evaluator_unavailable_is_skip(self, monkeypatch):
        import agent_friday.services.model_router as mr
        monkeypatch.setattr(mr, "_generate_text",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
        r = qa.evaluate_text("content", "intent")
        assert r["status"] == "skipped"
        assert r["passed"] is True  # a missing key never blocks delivery


class TestGateLoop:
    def test_gate_disabled_runs_once_ungated(self, monkeypatch):
        monkeypatch.setattr(qa, "qa_config", lambda: {**qa._QA_DEFAULTS, "enabled": False})
        calls = []
        out = qa.gate_text(lambda hint: calls.append(hint) or "content", "intent")
        assert out["gated"] is False
        assert len(calls) == 1

    def test_gate_passes_on_good_first_try(self, monkeypatch):
        monkeypatch.setattr(qa, "qa_config",
                            lambda: {**qa._QA_DEFAULTS, "enabled": True,
                                     "threshold": 0.5, "max_retries": 1, "mode": "improve"})
        monkeypatch.setattr(qa, "evaluate_text",
                            lambda c, i, **k: {"passed": True, "score": 0.9})
        out = qa.gate_text(lambda hint: "great content", "intent")
        assert out["passed"] is True
        assert out["attempts"] == 1

    def test_gate_improves_then_flags(self, monkeypatch):
        monkeypatch.setattr(qa, "qa_config",
                            lambda: {**qa._QA_DEFAULTS, "enabled": True,
                                     "threshold": 0.9, "max_retries": 1, "mode": "flag"})
        # Always below threshold → exhausts retries → flagged.
        monkeypatch.setattr(qa, "evaluate_text",
                            lambda c, i, **k: {"passed": False, "score": 0.3,
                                               "critique": "weak", "suggestions": "fix"})
        seen = []
        out = qa.gate_text(lambda hint: seen.append(hint) or "content", "intent")
        assert out["passed"] is False
        assert out["action"] == "flagged"
