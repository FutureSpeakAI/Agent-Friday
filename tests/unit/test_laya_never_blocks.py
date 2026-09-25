"""Laya never makes a decision wait: not while it loads, not while it thinks.

On a laptop CPU the 421M encoder loads in the background, and once loaded a
single answer can take far longer than the ~300 ms measured on a desktop. The
approval gate asks it inside a chat turn, so an unbounded wait there is a turn
that stalls on a slow CPU. The rule: the gate waits a bounded time, then
answers with the keyword half of the union and says so, exactly as it does
while the model is still loading.

The union's safety property survives the bound, because the keyword verdict is
still one of the two inputs: a slow Laya can cost its extra cards, never one
the keyword scan would have raised.

No model is loaded here; the agent is faked at the `predict` boundary.
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_friday.services import approvals, decisions, dissent_gate, laya_backend

HARD = "Send the quarterly report to the board by email"
SOFT = "Summarise the notes from this morning"


class _SlowAgent:
    """A Laya on a slow CPU: right answers, far too late."""

    def __init__(self, seconds, choice="hard"):
        self.seconds = seconds
        self.choice = choice
        self.calls = 0

    def predict(self, text, question):
        self.calls += 1
        time.sleep(self.seconds)
        return {"answers": {"severity": {"choice": self.choice, "confidence": 0.9,
                                         "probabilities": {}}}}


class _QuickAgent(_SlowAgent):
    def __init__(self, choice="hard"):
        super().__init__(0.0, choice)


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
    monkeypatch.setattr(laya_backend, "_SCORE_TIMEOUT_S", 0.3, raising=False)
    monkeypatch.setattr(laya_backend, "_slow_answers", 0, raising=False)
    monkeypatch.setattr(laya_backend, "_last_slow_ts", None, raising=False)
    # A fresh set of scoring slots: a slow fake from an earlier test may still
    # be sleeping in one.
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(getattr(laya_backend, "_MAX_SCORING", 2)),
                        raising=False)
    yield
    laya_backend._agent = None


def _timed(fn, *a, **kw):
    t0 = time.monotonic()
    out = fn(*a, **kw)
    return out, time.monotonic() - t0


def test_a_slow_laya_does_not_hold_a_decision(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", _SlowAgent(3.0, choice="soft"))
    (answer, _conf, detail), took = _timed(
        laya_backend.union_backend, "action_severity", HARD)
    assert took < 1.5, "the gate waited %.1fs for a slow Laya" % took
    assert answer == "hard"                       # the keyword half
    assert detail["union"] == "keyword-only"
    assert "slow" in detail["reason"]


def test_the_settings_status_says_laya_was_too_slow(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", _SlowAgent(3.0))
    laya_backend.union_backend("action_severity", SOFT)
    st = laya_backend.status()
    assert st["slow_answers"] == 1
    assert st["last_slow_ts"] is not None


def test_a_prompt_laya_still_answers(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", _QuickAgent("hard"))
    answer, _conf, detail = laya_backend.union_backend("action_severity", SOFT)
    assert answer == "hard" and detail["union"] == "or" and detail["escalated"]
    assert laya_backend.status()["slow_answers"] == 0


def test_a_slow_laya_cannot_lose_a_card(monkeypatch):
    """gated(keyword) implies gated(union) with a Laya that says soft, late."""
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "keyword")
    want = approvals.classify(HARD)["gated"]
    assert want, "the corpus line must be one the keyword scan gates"
    monkeypatch.setattr(laya_backend, "_agent", _SlowAgent(3.0, choice="soft"))
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "laya-union")
    got, took = _timed(approvals.classify, HARD)
    assert got["gated"] and took < 1.5


def test_calls_do_not_pile_up_behind_a_stuck_model(monkeypatch):
    """A model still grinding on earlier states is not handed more of them."""
    agent = _SlowAgent(3.0)
    monkeypatch.setattr(laya_backend, "_agent", agent)
    for _ in range(8):
        _answer, took = _timed(laya_backend.union_backend, "action_severity", SOFT)
        assert took < 1.5
    assert agent.calls <= laya_backend._MAX_SCORING


def test_a_model_still_loading_answers_at_once(monkeypatch):
    monkeypatch.setattr(laya_backend, "_loading", True)
    (answer, _c, detail), took = _timed(
        laya_backend.union_backend, "action_severity", HARD)
    assert took < 0.5 and answer == "hard"
    assert detail["reason"] == "laya still loading"


def test_warming_returns_at_once_and_loads_off_the_calling_thread(monkeypatch):
    """Startup calls start_warming; the load itself must run elsewhere."""
    monkeypatch.delenv("FRIDAY_TESTING", raising=False)
    monkeypatch.setattr(laya_backend, "_load_error", None)
    monkeypatch.setattr(laya_backend, "_load_attempts", 0)
    monkeypatch.setattr(laya_backend, "_BOOT_SETTLE_S", 0.0)
    started, release = threading.Event(), threading.Event()
    seen = {}

    def slow_load():
        seen["thread"] = threading.current_thread()
        started.set()
        release.wait(5)

    monkeypatch.setattr(laya_backend, "_load_now", slow_load)
    try:
        _none, took = _timed(laya_backend.start_warming)
        assert took < 0.5
        assert started.wait(2)
        assert seen["thread"] is not threading.current_thread()
    finally:
        release.set()
