"""Every chat reply says who answered it, including the ones no model wrote.

A paired phone or tablet shows the serving model on each reply (the gateway reads
`served_by.answered_by.model`). A turn settled by a rule, a refusal or a held offer used to carry no
`served_by` at all, which the client cannot tell from "not recorded". They now say so: no model, and
which kind of turn it was. `answered_by` stays absent, so no model id is ever read out of them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.routes import chat as chat_routes


def _json(resp):
    return resp.get_json()


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(chat_routes, "_persist_turn", lambda *a, **k: None)
    monkeypatch.setattr(chat_routes, "_save_chat_history", lambda *a, **k: None)


def test_the_helper_says_no_model_and_never_names_one():
    sb = chat_routes._served_by_no_model("navigate")
    assert sb["no_model"] is True and sb["kind"] == "navigate"
    assert sb["model"] is None and sb["provider"] == "none" and sb["answered"] is False
    assert "answered_by" not in sb


def test_an_instant_reply_says_a_rule_answered_it(client, quiet):
    out = {"reply": "Opened it.", "tool_trace": [], "actions": [], "outcome": "ok"}
    with client.application.test_request_context("/api/chat", method="POST"):
        data = _json(chat_routes._instant_response("open the second one", out, "c-who"))
    assert data["instant"] is True
    assert data["served_by"]["no_model"] is True and data["served_by"]["kind"] == "rule"
    assert "answered_by" not in data["served_by"]


def test_a_held_local_turn_says_nothing_answered_it(client, quiet):
    with client.application.test_request_context("/api/chat", method="POST"):
        data = _json(chat_routes._offer_turn_response(
            "hello", "The local model could not finish.", {"choices": []},
            conversation_id="c-who", model="bonsai2:27b"))
    assert data["served_by"]["answered"] is False
    assert data["served_by"]["model"] == "bonsai2:27b"  # the one that did NOT answer, as before


def test_every_deterministic_return_in_chat_carries_served_by():
    """The turns that never reach a model: each such return in chat() names its kind."""
    src = Path(chat_routes.__file__).read_text(encoding="utf-8")
    kinds = set(re.findall(r'_served_by_no_model\("([a-z_]+)"\)', src))
    assert kinds >= {"rule", "navigate", "open_path", "demo", "seat_missing",
                     "vault_refused", "cloud_only_no_key", "error"}, kinds
