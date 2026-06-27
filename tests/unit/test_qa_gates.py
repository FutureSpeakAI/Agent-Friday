"""Unit tests for Self-Evaluation Gates (services/qa_gates.py).

The text evaluator routes through services.model_router._generate_text, which we
stub so no model is called. Covers score parsing, pass/fail thresholds, graceful
skip on evaluator failure, and the generate→evaluate→improve gate loop.
"""
import pytest

from agent_friday.services import qa_gates
from agent_friday.services import model_router as model_router


# ── score parsing ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ('{"score": 0.85, "critique": "good"}', 0.85),
    ("score: 0.4", 0.4),
    ("score = 8/10", 0.8),
    ("0.55 and some prose", 0.55),
    ("90 out of 100 overall", 0.9),   # leading 90 → /100
])
def test_parse_score_variants(raw, expected):
    assert qa_gates._parse_score(raw)["score"] == pytest.approx(expected, abs=0.01)


def test_parse_score_unparseable_is_none():
    assert qa_gates._parse_score("no number here")["score"] is None


# ── evaluate_text ──────────────────────────────────────────────────────────
def _stub_gen(monkeypatch, reply):
    monkeypatch.setattr(model_router, "_generate_text",
                        lambda *a, **k: reply, raising=True)


def test_evaluate_text_pass(monkeypatch):
    _stub_gen(monkeypatch, '{"score": 0.9, "critique": "on point", "suggestions": ""}')
    v = qa_gates.evaluate_text("some content", "make it sharp")
    assert v["status"] == "ok"
    assert v["passed"] is True
    assert v["score"] == 0.9


def test_evaluate_text_fail_below_threshold(monkeypatch):
    _stub_gen(monkeypatch, '{"score": 0.3, "critique": "off brief", "suggestions": "tighten"}')
    v = qa_gates.evaluate_text("weak content", "make it sharp")
    assert v["passed"] is False
    assert v["suggestions"] == "tighten"


def test_evaluate_text_skips_when_evaluator_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no provider")
    monkeypatch.setattr(model_router, "_generate_text", _boom, raising=True)
    v = qa_gates.evaluate_text("content", "intent")
    assert v["status"] == "skipped"
    assert v["passed"] is True   # skip is treated as a pass


def test_evaluate_text_empty_content_passes():
    v = qa_gates.evaluate_text("", "intent")
    assert v["status"] == "skipped" and v["passed"] is True


# ── gate_text loop ─────────────────────────────────────────────────────────
def test_gate_disabled_runs_once_ungated(monkeypatch):
    monkeypatch.setattr(qa_gates, "qa_config",
                        lambda: {"enabled": False, "threshold": 0.7,
                                 "max_retries": 1, "mode": "improve"})
    calls = []
    out = qa_gates.gate_text(lambda hint: calls.append(hint) or "draft", "intent")
    assert out["gated"] is False
    assert out["content"] == "draft"
    assert len(calls) == 1


def test_gate_improves_then_passes(monkeypatch):
    monkeypatch.setattr(qa_gates, "qa_config",
                        lambda: {"enabled": True, "threshold": 0.7,
                                 "max_retries": 2, "mode": "improve"})
    # Drive evaluate_text by attempt count: first fails, second passes.
    state = {"n": 0}

    def fake_eval(content, intent, **k):
        state["n"] += 1
        passed = state["n"] >= 2
        return {"passed": passed, "score": 0.9 if passed else 0.2,
                "critique": "x", "suggestions": "y"}
    monkeypatch.setattr(qa_gates, "evaluate_text", fake_eval)

    hints = []
    out = qa_gates.gate_text(lambda hint: hints.append(hint) or f"draft{state['n']}",
                             "intent")
    assert out["passed"] is True
    assert out["attempts"] == 2
    assert hints[1] != ""   # the retry carried a critique hint


def test_gate_flag_mode_does_not_retry(monkeypatch):
    monkeypatch.setattr(qa_gates, "qa_config",
                        lambda: {"enabled": True, "threshold": 0.7,
                                 "max_retries": 3, "mode": "flag"})
    monkeypatch.setattr(qa_gates, "evaluate_text",
                        lambda *a, **k: {"passed": False, "score": 0.1,
                                         "critique": "bad", "suggestions": "fix"})
    calls = []
    out = qa_gates.gate_text(lambda hint: calls.append(hint) or "draft", "intent")
    assert out["action"] == "flagged"
    assert out["attempts"] == 1   # flag mode generates once, no silent retries
    assert out["passed"] is False
