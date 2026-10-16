"""A typed household statement is saved before the model runs, and the turn says so.

/api/chat prepares the turn (agent.prepare_confirmation_ctx), which saves the
household basics the owner states to the memory card; the same turn's context
tail then carries one MEMORY CARD note, so the reply neither claims a save
that did not happen nor calls update_core_memory again. A turn with no such
statement carries no note.
"""
from __future__ import annotations

import copy

import pytest

from agent_friday import core
from agent_friday.routes import chat
from agent_friday.routing.model_router import ModelRouter
from agent_friday.services import core_memory as cm
from agent_friday.services import taint
from agent_friday.services.prompt_cache import VOLATILE_MARKER


def _fake_context(message, *a, **k):
    return ("== PERSONA ==\nI am Friday.\n" + VOLATILE_MARKER + "\nNow: 12:00\n", [])


@pytest.fixture
def wired(monkeypatch, patch_app, tmp_path):
    settings = copy.deepcopy(core.DEFAULT_SETTINGS)
    settings["model_routing"].update({"mode": "local_preferred", "vault_local_only": False})
    patch_app("_load_settings", lambda: settings)
    patch_app("_build_memory_context_block", lambda *a, **k: "")
    patch_app("_build_session_continuity_block", lambda *a, **k: "")
    patch_app("_build_emotional_tone_block", lambda *a, **k: "")
    patch_app("_build_context_prompt", _fake_context)
    patch_app("_index_chat_turn", lambda *a, **k: None)
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr("agent_friday.services.laya_personal.is_minor", lambda *a, **k: False)
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": "local", "model": "bonsai2:27b", "provider_name": "bonsai2-local",
        "is_local": True, "vault_allowed": True, "vault_access": True,
        "scrub_pii": False, "refuse": False, "warning": None,
    })
    taint.reset()
    yield settings
    taint.reset()


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


def test_the_household_statement_is_saved_and_the_turn_context_says_so(client, monkeypatch, wired):
    last = _send(client, monkeypatch, "I am 52, my husband is 50, our son is 9. Note that please.")
    lines = cm.entries("user")
    assert len(lines) == 1 and lines[0].endswith("): the user is 52; husband is 50; son is 9.")
    assert "MEMORY CARD:" in last and lines[0] in last
    assert last.rstrip().endswith("Note that please.")


def test_a_turn_without_a_household_statement_carries_no_note(client, monkeypatch, wired):
    last = _send(client, monkeypatch, "find weekend events near Riverside Park")
    assert cm.entries("user") == []
    assert "MEMORY CARD:" not in last
