"""The gate's explanation describes the mode actually in force.

After the shipped default became shadow, /api/decisions/gate_status told the
owner "An action is held for your sign-off when either one says it should
be" while only the keyword scan was deciding: the union's sentence, in a mode
where Laya changes nothing. A status line that describes a different gate
from the one running is the kind of claim this endpoint exists to prevent.

One test per mode, with Laya ready and not ready.
"""
from __future__ import annotations

import pytest

from agent_friday.services import laya_backend

UNION_SENTENCE = "when either one says it should be"


def _gate(monkeypatch, backend, shadow, *, ready=True, loading=False, error=None):
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", backend)
    if shadow:
        monkeypatch.setenv("FRIDAY_DECISION_SHADOW", shadow)
    else:
        monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    settings = {"decision_backend": backend, "decision_shadow": shadow or ""}
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: dict(settings), raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", object() if ready else None)
    monkeypatch.setattr(laya_backend, "_loading", loading)
    monkeypatch.setattr(laya_backend, "_load_error", error)
    monkeypatch.setattr(laya_backend, "_slow_answers", 0)
    monkeypatch.setattr(laya_backend, "_missed", dict.fromkeys(laya_backend.MISS_REASONS, 0))
    monkeypatch.setattr(laya_backend, "_last_missed_ts", None)
    monkeypatch.setattr(laya_backend, "start_warming", lambda *a, **k: None)


def _status(client):
    return client.get("/api/decisions/gate_status").get_json()


def test_off(client, monkeypatch):
    _gate(monkeypatch, "keyword", None, ready=False)
    b = _status(client)
    assert b["mode"] == "off" and b["effective_backend"] == "keyword"
    assert b["explain"] == "The keyword scan alone is deciding."
    assert b["degraded"] is False


def test_shadow_ready(client, monkeypatch):
    _gate(monkeypatch, "keyword", "laya")
    b = _status(client)
    assert b["mode"] == "shadow" and b["effective_backend"] == "keyword"
    assert b["explain"].startswith("The keyword scan decides.")
    assert "changes no decision" in b["explain"]
    assert UNION_SENTENCE not in b["explain"]
    assert b["degraded"] is False


def test_shadow_loading(client, monkeypatch):
    _gate(monkeypatch, "keyword", "laya", ready=False, loading=True)
    b = _status(client)
    assert b["effective_backend"] == "keyword"
    assert b["explain"].startswith("The keyword scan decides.")
    assert "loading" in b["explain"] and "scored when it is back" in b["explain"]
    assert UNION_SENTENCE not in b["explain"]
    assert b["degraded"] is True


def test_union_ready(client, monkeypatch):
    _gate(monkeypatch, "laya-union", None)
    b = _status(client)
    assert b["mode"] == "on" and b["effective_backend"] == "laya-union"
    assert UNION_SENTENCE in b["explain"]
    assert b["degraded"] is False


def test_union_loading(client, monkeypatch):
    _gate(monkeypatch, "laya-union", None, ready=False, loading=True)
    b = _status(client)
    assert b["effective_backend"] == "keyword"
    assert "still loading" in b["explain"] and "keyword scan alone" in b["explain"]
    assert b["degraded"] is True


def test_union_not_answering(client, monkeypatch):
    _gate(monkeypatch, "laya-union", None, ready=False, error="fixture failure")
    b = _status(client)
    assert b["effective_backend"] == "keyword"
    assert "not answering" in b["explain"]
    assert b["degraded"] is True


def test_laya_alone(client, monkeypatch):
    _gate(monkeypatch, "laya", None)
    b = _status(client)
    assert b["effective_backend"] == "laya"
    assert "Laya alone decides" in b["explain"]
    assert UNION_SENTENCE not in b["explain"]
