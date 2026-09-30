"""The same question to the same model is answered once.

Laya is deterministic for a given checkpoint and state, and the gate asks it
the same thing again and again: the Grants screen classifies every connector
read tool each time it opens, about thirty questions in a burst. Scoring each
of them afresh is what emptied the two scoring slots and sent most of a burst
to the keyword scan alone ("laya is still busy") in the week-one decision log.

A remembered answer is Laya's own answer, so the union's add-only property is
untouched; it is simply not recomputed. It belongs to the model instance that
produced it, and an error or a keyword-only fallback is never remembered.

No model is loaded here; the agent is faked at the `predict` boundary.
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_friday.services import approvals, decisions, dissent_gate, laya_backend

READ = "mcp_github_list_commits {}"
OTHER = "mcp_github_search_code {}"


class _Agent:
    def __init__(self, choice="hard", seconds=0.0, fail_first=0):
        self.choice = choice
        self.seconds = seconds
        self.fail_first = fail_first
        self.calls = []

    def predict(self, text, question):
        self.calls.append(text)
        if self.seconds:
            time.sleep(self.seconds)
        if self.fail_first:
            self.fail_first -= 1
            raise RuntimeError("fixture failure")
        return {"answers": {"severity": {"choice": self.choice, "confidence": 0.8,
                                         "probabilities": {}}}}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: {}, raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", None)
    monkeypatch.setattr(laya_backend, "_loading", False)
    monkeypatch.setattr(laya_backend, "_SCORE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    laya_backend.clear_answer_cache()
    yield
    laya_backend._agent = None
    laya_backend.clear_answer_cache()


def test_the_same_state_is_scored_once(monkeypatch):
    agent = _Agent("hard")
    monkeypatch.setattr(laya_backend, "_agent", agent)
    first = laya_backend.union_backend("policy_class", READ)
    second = laya_backend.union_backend("policy_class", READ)
    assert agent.calls == [READ]
    assert first[0] == second[0]
    assert second[2]["union"] == "or" and second[2]["laya"] == "hard"
    assert second[2].get("cached") is True
    assert not first[2].get("cached")


def test_a_different_state_is_scored_afresh(monkeypatch):
    agent = _Agent("soft")
    monkeypatch.setattr(laya_backend, "_agent", agent)
    laya_backend.union_backend("policy_class", READ)
    laya_backend.union_backend("policy_class", OTHER)
    assert agent.calls == [READ, OTHER]


def test_a_remembered_answer_needs_no_scoring_slot(monkeypatch):
    """A full scorer used to send even a repeated question to keyword-only."""
    agent = _Agent("hard")
    monkeypatch.setattr(laya_backend, "_agent", agent)
    laya_backend.union_backend("policy_class", READ)
    held = [laya_backend._reserve_scoring(pilot=False) for _ in range(laya_backend._MAX_SCORING)]
    try:
        assert all(held)
        _ans, _conf, detail = laya_backend.union_backend("policy_class", READ)
    finally:
        for release in held:
            release()
    assert detail["union"] == "or" and detail["laya"] == "hard"
    assert agent.calls == [READ]


def test_answers_belong_to_the_model_that_gave_them(monkeypatch):
    first = _Agent("hard")
    monkeypatch.setattr(laya_backend, "_agent", first)
    laya_backend.union_backend("policy_class", READ)
    second = _Agent("soft")
    monkeypatch.setattr(laya_backend, "_agent", second)
    _ans, _conf, detail = laya_backend.union_backend("policy_class", READ)
    assert second.calls == [READ]
    assert detail["laya"] == "soft"


def test_an_error_is_not_remembered(monkeypatch):
    agent = _Agent("hard", fail_first=1)
    monkeypatch.setattr(laya_backend, "_agent", agent)
    _a, _c, failed = laya_backend.union_backend("policy_class", READ)
    assert failed["union"] == "keyword-only"
    _a, _c, answered = laya_backend.union_backend("policy_class", READ)
    assert answered["union"] == "or" and answered["laya"] == "hard"
    assert len(agent.calls) == 2


def test_a_late_answer_serves_the_next_ask(monkeypatch):
    """The first ask timed out to keyword-only; the model still finished."""
    agent = _Agent("hard", seconds=0.6)
    monkeypatch.setattr(laya_backend, "_agent", agent)
    _a, _c, late = laya_backend.union_backend("policy_class", READ)
    assert late["union"] == "keyword-only"
    deadline = time.monotonic() + 5
    while laya_backend.answer_cache_stats()["size"] == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    _a, _c, served = laya_backend.union_backend("policy_class", READ)
    assert served["union"] == "or" and served.get("cached") is True
    assert len(agent.calls) == 1


def test_the_memory_is_bounded(monkeypatch):
    agent = _Agent("soft")
    monkeypatch.setattr(laya_backend, "_agent", agent)
    monkeypatch.setattr(laya_backend, "_ANSWER_CACHE_MAX", 3)
    for i in range(5):
        laya_backend.union_backend("policy_class", "mcp_x_get_%d {}" % i)
    assert laya_backend.answer_cache_stats()["size"] == 3
    laya_backend.union_backend("policy_class", "mcp_x_get_0 {}")
    assert agent.calls.count("mcp_x_get_0 {}") == 2


def test_status_reports_the_memory(monkeypatch):
    agent = _Agent("soft")
    monkeypatch.setattr(laya_backend, "_agent", agent)
    laya_backend.union_backend("policy_class", READ)
    laya_backend.union_backend("policy_class", READ)
    cache = laya_backend.status()["answer_cache"]
    assert cache["size"] == 1 and cache["hits"] == 1 and cache["misses"] == 1
