"""A connector "read" Laya could not check waits for the owner.

`action_gate.classify` lets an `mcp_*` tool through as internal only when its
name leads with a read verb AND the union gate agrees. Its rule, in its own
words: either one saying otherwise makes it outward, "and so does the union
gate being unable to answer".

That held when Laya was not loaded (`_laya_down`). It did not hold when Laya
was loaded but busy or too slow: the union then answers with its keyword half,
the keyword scan calls a read verb internal, and the read went ahead on one
opinion. The decision log recorded 230 such "still busy" answers in a week.

Only ADDS cards, never removes one: an answered union is unchanged, and so is
a gate whose owner did not select Laya.
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approvals, decisions, dissent_gate, laya_backend

READ = "mcp_github_list_commits"


class _Agent:
    def __init__(self, choice):
        self.choice = choice

    def predict(self, text, question):
        return {"answers": {"severity": {"choice": self.choice, "confidence": 0.8,
                                         "probabilities": {}}}}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    monkeypatch.delenv("FRIDAY_DECISION_BACKEND", raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_loading", False)
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    laya_backend.clear_answer_cache()
    yield
    laya_backend._agent = None
    laya_backend.clear_answer_cache()


def _select(monkeypatch, backend):
    settings = {"decision_backend": backend, "decision_shadow": ""}
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: settings, raising=False)


def _hold_every_slot():
    return [laya_backend._reserve_scoring(pilot=False)
            for _ in range(laya_backend._MAX_SCORING)]


def test_a_read_laya_was_too_busy_to_check_is_held(monkeypatch):
    _select(monkeypatch, "laya-union")
    monkeypatch.setattr(laya_backend, "_agent", _Agent("soft"))
    held = _hold_every_slot()
    try:
        assert all(held)
        klass, why = action_gate.classify(READ, {})
    finally:
        for release in held:
            release()
    assert klass == action_gate.OUTWARD
    assert "Laya" in why


def test_a_read_laya_checked_and_cleared_still_runs(monkeypatch):
    _select(monkeypatch, "laya-union")
    monkeypatch.setattr(laya_backend, "_agent", _Agent("soft"))
    assert action_gate.classify(READ, {})[0] == action_gate.INTERNAL


def test_a_read_laya_flagged_is_held(monkeypatch):
    _select(monkeypatch, "laya-union")
    monkeypatch.setattr(laya_backend, "_agent", _Agent("hard"))
    assert action_gate.classify(READ, {})[0] == action_gate.OUTWARD


def test_without_laya_selected_nothing_changes(monkeypatch):
    _select(monkeypatch, "keyword")
    monkeypatch.setattr(laya_backend, "_agent", None)
    assert action_gate.classify(READ, {})[0] == action_gate.INTERNAL


def test_classify_says_whether_the_second_opinion_answered(monkeypatch):
    _select(monkeypatch, "laya-union")
    monkeypatch.setattr(laya_backend, "_agent", _Agent("soft"))
    assert approvals.classify("mcp_github_list_issues {}")["second_opinion"] == "answered"
    held = _hold_every_slot()
    try:
        missing = approvals.classify("mcp_github_get_issue {}")
    finally:
        for release in held:
            release()
    assert missing["second_opinion"] == "missing"
    _select(monkeypatch, "keyword")
    assert approvals.classify("mcp_github_get_issue {}")["second_opinion"] is None
