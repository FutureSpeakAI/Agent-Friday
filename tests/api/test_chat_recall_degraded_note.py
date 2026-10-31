"""A turn whose long-term memory could not be read says so in its context (NS-32.5-1).

/api/chat: the conversation goes on, and the turn's context tail carries the RECALL DEGRADED
note naming what could not be read, so the model does not present anything as remembered from
it. Modelled on tests/api/test_chat_household_note.py.
"""
from __future__ import annotations

import copy

import pytest

from agent_friday import core
from agent_friday.routes import chat
from agent_friday.routing.model_router import ModelRouter
from agent_friday.services import taint
from agent_friday.services.prompt_cache import VOLATILE_MARKER


def _fake_context(message, *a, **k):
    return ("== PERSONA ==\nI am Friday.\n" + VOLATILE_MARKER + "\nNow: 12:00\n", [])


@pytest.fixture
def wired(monkeypatch, patch_app):
    try:
        from agent_friday.services import recall_health
    except ImportError:
        pytest.fail("a memory store that cannot be read looks empty: services/recall_health does not exist")
    recall_health._reset()
    settings = copy.deepcopy(core.DEFAULT_SETTINGS)
    settings["model_routing"].update({"mode": "local_preferred", "vault_local_only": False})
    patch_app("_load_settings", lambda: settings)
    patch_app("_build_session_continuity_block", lambda *a, **k: "")
    patch_app("_build_emotional_tone_block", lambda *a, **k: "")
    patch_app("_build_context_prompt", _fake_context)
    patch_app("_index_chat_turn", lambda *a, **k: None)
    monkeypatch.setattr("agent_friday.services.laya_personal.is_minor", lambda *a, **k: False)
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": "local", "model": "bonsai2:27b", "provider_name": "bonsai2-local",
        "is_local": True, "vault_allowed": True, "vault_access": True,
        "scrub_pii": False, "refuse": False, "warning": None,
    })
    taint.reset()
    yield recall_health
    taint.reset()
    recall_health._reset()


def _send(client, monkeypatch, message):
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy(messages))
        return "ok", []
    monkeypatch.setattr(chat, "_call_ollama", dispatch)
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert seen, "the local seat was not dispatched to"
    return seen[-1][-1]["content"]


def test_a_store_that_could_not_be_read_this_turn_is_named(client, monkeypatch, patch_app, wired):
    def broken_recall(message, session_id, *a, **k):
        wired.note_failure("past_conversations", OSError(5, "I/O error"))
        return ""
    patch_app("_build_memory_context_block", broken_recall)
    last = _send(client, monkeypatch, "what did we decide about the depot?")
    assert "RECALL DEGRADED" in last and "past conversations could not be read" in last


def test_a_turn_with_memory_working_carries_no_note(client, monkeypatch, patch_app, wired):
    patch_app("_build_memory_context_block", lambda *a, **k: "")
    last = _send(client, monkeypatch, "what did we decide about the depot?")
    assert "RECALL DEGRADED" not in last
