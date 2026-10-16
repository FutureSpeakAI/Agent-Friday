"""The Models card shows ONE truth for which model is answering.

Field case: the System panel said Cloud only while the local status panel
showed bonsai2:27b serving on 127.0.0.1:8090, and a workspace chat was in fact
answered locally. The card's "Now answering" row was a prediction from the
routing settings, and nothing recorded what really answered.

Pinned: the chat routes record the model that generated the persisted reply
(the same model the reply's badge names); the soup payload reconciles that
fact with the prediction; a Cloud-only mode answered locally is named with
its reason; the page renders the fact, not the prediction, when there is one.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import agent_friday.routes.chat as chat_mod
from agent_friday.routes import intelligence as intel
from agent_friday.services import attribution

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "runtime_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def stores(monkeypatch):
    from agent_friday.services import model_store as ms
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "serving", lambda: {"bonsai2:27b": "http://127.0.0.1:8090"})
    monkeypatch.setattr(ms, "all_models", lambda: {})
    monkeypatch.setattr(ms, "available", lambda: {})
    monkeypatch.setattr(ms, "missing", lambda: {})
    monkeypatch.setattr(intel, "_weights_rows", lambda a, b: [])


def _soup():
    settings = {"model_routing": {"mode": "cloud_only", "local_model": "bonsai2:27b",
                                  "default_cloud_model": "claude-haiku-4-5"}}
    routing = {"reasoning": {"model": "claude-haiku-4-5", "provider": "anthropic"}}
    return intel._model_soup(settings, routing, {"models": {}, "ledger": {}}, {},
                             [{"name": "anthropic", "key_present": True}], {})


def _at():
    from agent_friday.services import answer_truth
    return answer_truth


def test_a_local_reply_under_cloud_only_is_what_the_card_says_answers(client, home, stores, monkeypatch):
    """End to end: a chat answered on this machine while the mode is Cloud only."""
    def dispatch(messages, **kwargs):
        attribution.record_generation("bonsai2:27b", provider="llama-cpp-local", seat="local")
        return "answered here", []
    monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
    client.post("/api/chat", json={"message": "hello", "workspace": "calendar"})
    soup = _soup()
    answering = (soup.get("answer") or {}).get("answering") or {}
    assert answering.get("model") == "bonsai2:27b", (
        "the card predicted %r from the settings; the reply came from bonsai2:27b"
        % soup["now"].get("model"))
    assert answering.get("where") == "local"


def test_cloud_only_answered_locally_is_one_named_truth(home, stores):
    _at().record("bonsai2:27b", seat="local", provider="llama-cpp-local",
                        workspace="calendar", why="the model chosen for this chat (bonsai2:27b)")
    soup = _soup()
    ans = soup["answer"]
    assert ans["answering"]["model"] == "bonsai2:27b"
    assert ans["answering"]["where"] == "local" and ans["answering"]["basis"] == "answered"
    assert ans["disagrees"] is True
    assert "Cloud only" in ans["note"] and "bonsai2:27b" in ans["note"]
    assert ans["next"]["where"] == "cloud", "the prediction is kept, labelled as the next turn"


def test_with_no_reply_on_record_the_row_says_it_is_a_prediction(home, stores):
    ans = _soup()["answer"]
    assert ans["answering"]["basis"] == "predicted" and ans["next"] is None


def test_agreeing_fact_and_prediction_show_once(home, stores):
    _at().record("claude-haiku-4-5", seat="cloud", provider="anthropic")
    ans = _soup()["answer"]
    assert ans["disagrees"] is False and ans["next"] is None


def test_the_chat_route_records_the_model_that_answered(client, home, monkeypatch):
    def dispatch(messages, **kwargs):
        attribution.record_generation("bonsai2:27b", provider="llama-cpp-local", seat="local")
        return "answered here", []
    monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
    client.post("/api/chat", json={"message": "hello", "workspace": "calendar"})
    row = _at().read()
    assert row and row["model"] == "bonsai2:27b" and row["seat"] == "local"
    assert row["workspace"] == "calendar"
    assert "hello" not in str(row) and "answered here" not in str(row), "no message text is kept"
    assert abs(row["at"] - time.time()) < 60


def test_the_card_renders_the_fact_not_the_prediction():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "soup.answer" in html and "'Answering you'" in html
    assert "'Next message, by your settings'" in html
