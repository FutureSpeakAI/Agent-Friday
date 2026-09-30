"""Every time the second opinion is missing, the gate says so, and why.

In union mode a question Laya does not answer is decided by the keyword scan
alone. That is a safe fallback and it must not be a silent one. The week-one
decision log had 257 such answers out of 624, and the Settings panel showed
"Both scanners serving" throughout, because `degraded` only meant "not loaded"
and a busy scorer was counted nowhere at all.

Each keyword-only answer is now counted by reason, and a miss in the last
fifteen minutes marks the gate degraded, in the same panel, in words.
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_friday.services import approvals, decisions, dissent_gate, laya_backend


class _Agent:
    def predict(self, text, question):
        return {"answers": {"severity": {"choice": "soft", "confidence": 0.9}}}


@pytest.fixture(autouse=True)
def _union(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "laya-union")
    monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", _Agent())
    monkeypatch.setattr(laya_backend, "_loading", False)
    monkeypatch.setattr(laya_backend, "_slow_answers", 0)
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    laya_backend.reset_missed()
    laya_backend.clear_answer_cache()
    yield
    laya_backend.reset_missed()
    laya_backend.clear_answer_cache()
    laya_backend._agent = None


def _busy_answer(state="mcp_github_get_issue {}"):
    held = [laya_backend._reserve_scoring(pilot=False)
            for _ in range(laya_backend._MAX_SCORING)]
    try:
        return laya_backend.union_backend("policy_class", state)
    finally:
        for release in held:
            if release:
                release()


def test_a_busy_scorer_is_counted_as_a_miss():
    _a, _c, detail = _busy_answer()
    assert detail["union"] == "keyword-only"
    st = laya_backend.status()
    assert st["missed"]["busy"] == 1
    assert st["last_missed_reason"] == "busy"
    assert st["last_missed_ts"] is not None


def test_an_unloaded_model_is_counted_as_a_miss(monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", None)
    monkeypatch.setattr(laya_backend, "start_warming", lambda *a, **k: None)
    laya_backend.union_backend("policy_class", "mcp_github_get_issue {}")
    assert laya_backend.status()["missed"]["not_loaded"] == 1


def test_an_answered_question_is_not_a_miss():
    laya_backend.union_backend("policy_class", "mcp_github_get_issue {}")
    assert sum(laya_backend.status()["missed"].values()) == 0


def test_a_recent_miss_marks_the_gate_degraded(client):
    _busy_answer()
    body = client.get("/api/decisions/gate_status").get_json()
    assert body["degraded"] is True
    assert body["laya"]["ready"] is True
    assert "could not answer 1 time" in body["explain"]
    assert "keyword scan alone" in body["explain"]


def test_an_old_miss_does_not(client):
    _busy_answer()
    # Set directly: the fixture's reset clears it, a monkeypatch undo would
    # restore the fresh timestamp after that reset and leak it onward.
    laya_backend._last_missed_ts = time.time() - 2 * 3600
    body = client.get("/api/decisions/gate_status").get_json()
    assert body["degraded"] is False
    assert body["explain"].startswith("Both scanners are serving.")
