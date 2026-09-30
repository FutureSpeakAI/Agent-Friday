"""A shadow score that cannot run now is written down, and scored later.

Shadow mode is how Laya earns the gate: the keyword scan decides and Laya's
answer to the same question is logged beside it. The week-one review found
that most real actions never got a Laya answer at all. The shadow thread ran
the backend, the backend raised (not loaded, or both scoring slots busy), and
the row was simply never written. The evidence meant to decide promotion was
missing exactly where it was most needed, with nothing to say so.

Now:
  * a shadow that cannot score writes a row saying it was skipped and why;
  * the question is kept (bounded, and never off the record) and scored once
    Laya can, in a row marked `catch_up` with how late it was;
  * in the background a shadow scoring waits for a free slot rather than
    giving up, because nothing is waiting on it.
"""
from __future__ import annotations

import json
import threading

import pytest

from agent_friday.services import approvals, decisions, dissent_gate, laya_backend

HARD = "Send the quarterly report to the board by email"


class _Agent:
    def __init__(self, choice="hard"):
        self.choice = choice
        self.calls = 0

    def predict(self, text, question):
        self.calls += 1
        return {"answers": {"severity": {"choice": self.choice, "confidence": 0.8}}}


@pytest.fixture(autouse=True)
def _shadow(tmp_path, monkeypatch):
    log = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(decisions, "log_path", lambda: log)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "keyword")
    monkeypatch.setenv("FRIDAY_DECISION_SHADOW", "laya")
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", None)
    monkeypatch.setattr(laya_backend, "_loading", False)
    monkeypatch.setattr(laya_backend, "start_warming", lambda *a, **k: None)
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    # Synchronous shadows: the thread is not what is under test.
    monkeypatch.setattr(decisions, "_SHADOW_SYNC_FOR_TESTS", True, raising=False)
    decisions.clear_shadow_backlog()
    laya_backend.clear_answer_cache()
    yield log
    decisions.clear_shadow_backlog()
    laya_backend.clear_answer_cache()
    laya_backend._agent = None


def _rows(log):
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_an_unloaded_laya_leaves_a_skipped_row(_shadow):
    decisions.decide("action_severity", HARD)
    shadows = [r for r in _rows(_shadow) if r.get("shadow")]
    assert len(shadows) == 1
    assert shadows[0]["skipped"]
    assert "not loaded" in shadows[0]["skipped"]
    assert decisions.shadow_backlog_size() == 1


def test_the_skipped_question_is_scored_once_laya_can(_shadow, monkeypatch):
    decisions.decide("action_severity", HARD)
    agent = _Agent("hard")
    monkeypatch.setattr(laya_backend, "_agent", agent)
    assert decisions.drain_shadow_backlog() == 1
    caught = [r for r in _rows(_shadow) if r.get("catch_up")]
    assert len(caught) == 1
    row = caught[0]
    assert row["shadow"] is True and row["method"] == "laya"
    assert row["answer"] == "hard"
    assert row["lag_s"] >= 0
    assert "agreed" in row
    assert decisions.shadow_backlog_size() == 0


def test_a_busy_scorer_is_waited_for_in_the_background(_shadow, monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", _Agent("hard"))
    held = [laya_backend._reserve_scoring(pilot=False)
            for _ in range(laya_backend._MAX_SCORING)]
    threading.Timer(0.3, lambda: [r() for r in held]).start()
    decisions.decide("action_severity", HARD)
    shadows = [r for r in _rows(_shadow) if r.get("shadow")]
    assert len(shadows) == 1
    assert not shadows[0].get("skipped")
    assert shadows[0]["answer"] == "hard"


def test_nothing_is_kept_off_the_record(_shadow, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "skip", lambda *a, **k: True)
    decisions.decide("action_severity", HARD)
    assert decisions.shadow_backlog_size() == 0


def test_the_backlog_is_bounded(_shadow, monkeypatch):
    monkeypatch.setattr(decisions, "_SHADOW_BACKLOG_MAX", 3)
    for i in range(5):
        decisions.decide("action_severity", "%s %d" % (HARD, i))
    assert decisions.shadow_backlog_size() == 3
