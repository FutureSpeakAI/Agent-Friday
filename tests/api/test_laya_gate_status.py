"""The approval-gate status line in Settings says when Laya was too slow.

On a laptop CPU the second opinion can miss its time bound; the gate then
decides by the keyword scan alone. The existing status text is where the owner
learns that, in words, with a count.
"""
from __future__ import annotations

from agent_friday.services import laya_backend


def _serving(monkeypatch, slow: int):
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "laya-union")
    monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", object())
    monkeypatch.setattr(laya_backend, "_loading", False)
    monkeypatch.setattr(laya_backend, "_slow_answers", slow, raising=False)


def test_a_slow_laya_is_named_in_the_status_text(client, monkeypatch):
    _serving(monkeypatch, slow=3)
    body = client.get("/api/decisions/gate_status").get_json()
    assert body["laya"]["slow_answers"] == 3
    assert "too slow to answer 3 times" in body["explain"]
    assert "keyword scan alone" in body["explain"]


def test_a_prompt_laya_says_nothing_about_speed(client, monkeypatch):
    _serving(monkeypatch, slow=0)
    body = client.get("/api/decisions/gate_status").get_json()
    assert body["explain"].startswith("Both scanners are serving.")
    assert "too slow" not in body["explain"]
