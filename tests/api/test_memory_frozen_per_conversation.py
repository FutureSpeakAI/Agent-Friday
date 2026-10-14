"""What Friday remembers is frozen for the length of a conversation.

Every chat turn nudges the user model's style traits and workspace counters,
and a memory saved during a turn adds a line, so the USER MODEL block in the
system prompt changed from turn to turn. One changed byte in the system
prompt makes the local seat (llama-server) re-read the whole prompt, and on
the cloud it re-bills the cached prefix. Now each memory block is captured
the first time a conversation's prompt is built: a write mid-conversation
lands on disk at once and reaches the prompt in the NEXT conversation. An
owner's own edit or delete applies at once (services/prompt_freeze).

Red on the old code: the system prompt of turn 2 carried the new memory
(chat), and the voice builder and `_get_friday_system_prompt` took no
conversation to freeze for.
"""
from __future__ import annotations

import copy

import pytest

from agent_friday import core
from agent_friday.routes import chat
from agent_friday.routing.model_router import ModelRouter
from agent_friday.services import conversations
from agent_friday.services import prompt_freeze
from agent_friday.services import user_model
from agent_friday.services import learning_loop
from agent_friday.services.prompt_cache import VOLATILE_MARKER


def _fake_context(message, *a, **k):
    return ("== PERSONA ==\nI am Friday.\n== TOOLS ==\nsearch_web — search\n"
            + VOLATILE_MARKER + "\nNow: 12:00\n", [])


@pytest.fixture(autouse=True)
def _fresh_snapshots():
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


@pytest.fixture
def memory(monkeypatch):
    """Friday's memory as the prompt renders it, changeable mid-test the way
    a save during a turn changes the real stores."""
    state = {"facts": ["Prefers short answers."], "heuristics": ["Lead with the answer."]}
    monkeypatch.setattr(user_model, "render_user_model_prompt",
                        lambda *a, **k: "\n".join("• " + f for f in state["facts"]))
    monkeypatch.setattr(learning_loop, "render_heuristics_prompt",
                        lambda *a, **k: "\n".join("- " + h for h in state["heuristics"]))
    return state


@pytest.fixture
def wired(monkeypatch, patch_app, memory):
    settings = copy.deepcopy(core.DEFAULT_SETTINGS)
    settings["model_routing"].update({"mode": "local_preferred", "vault_local_only": False})
    patch_app("_load_settings", lambda: settings)
    patch_app("_build_memory_context_block", lambda *a, **k: "")
    patch_app("_build_session_continuity_block", lambda *a, **k: "")
    patch_app("_build_emotional_tone_block", lambda *a, **k: "")
    patch_app("_build_context_prompt", _fake_context)
    patch_app("_index_chat_turn", lambda *a, **k: None)
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": "local", "model": "bonsai2:27b", "provider_name": "bonsai2-local",
        "is_local": True, "vault_allowed": True, "vault_access": True,
        "scrub_pii": False, "refuse": False, "warning": None,
    })
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy({"messages": messages, **kwargs}))
        return "ok", []
    monkeypatch.setattr(chat, "_call_ollama", dispatch)
    return seen


def _say(client, cid, text):
    r = client.post("/api/chat", json={"message": text, "conversation_id": cid})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]


def test_a_memory_saved_mid_conversation_does_not_change_the_local_system_prompt(
        client, wired, memory):
    cid = conversations.create("Freeze test")["id"]
    _say(client, cid, "hello")
    memory["facts"].append("Has a dog called Biscuit.")      # saved during turn 1
    memory["heuristics"].append("Quote the source.")
    _say(client, cid, "and another thing")
    s1, s2 = wired[0]["system"], wired[1]["system"]
    assert s1 == s2, "a memory write changed this conversation's system prompt"
    assert "Prefers short answers." in s2
    assert "Biscuit" not in s2 and "Quote the source." not in s2


def test_the_next_conversation_sees_the_memory(client, wired, memory):
    first = conversations.create("First")["id"]
    _say(client, first, "hello")
    memory["facts"].append("Has a dog called Biscuit.")
    second = conversations.create("Second")["id"]
    _say(client, second, "hello again")
    assert "Biscuit" not in wired[0]["system"]
    assert "Biscuit" in wired[1]["system"], "the saved memory never reached a new conversation"


def test_an_owner_delete_reaches_the_open_conversation_at_once(client, wired, memory):
    """A line the owner deletes must not keep riding in the prompt of the chat
    they deleted it from. (A pin: the old code rendered live, so it passed
    there too; the freeze must not break it.)"""
    cid = conversations.create("Owner delete")["id"]
    _say(client, cid, "hello")
    assert "Prefers short answers." in wired[0]["system"]
    memory["facts"].clear()
    r = client.post("/api/user-model/forget", json={"category": "preference"})
    assert r.status_code == 200 and (r.get_json() or {}).get("ok")
    _say(client, cid, "still there?")
    assert "Prefers short answers." not in wired[1]["system"]


def test_the_voice_prompt_is_frozen_for_its_conversation(monkeypatch, memory):
    import agent_friday.routes.voice as rv
    monkeypatch.setattr(rv, "_vault_local_only", lambda: False)
    monkeypatch.setattr(rv, "_vault_cloud_fallback", lambda: "warn")
    monkeypatch.setattr(rv, "_build_session_continuity_block", lambda: "")
    monkeypatch.setattr(rv, "_build_emotional_tone_block", lambda: "")
    p1, _ = rv._build_voice_system_prompt({}, description="", seat="synthetic-seat",
                                          conversation_id="conv-voice1")
    memory["facts"].append("Has a dog called Biscuit.")
    p2, _ = rv._build_voice_system_prompt({}, description="", seat="synthetic-seat",
                                          conversation_id="conv-voice1")
    assert p1 == p2, "a save during the call changed the voice system prompt"
    assert "Prefers short answers." in p1 and "Biscuit" not in p2
    p3, _ = rv._build_voice_system_prompt({}, description="", seat="synthetic-seat",
                                          conversation_id="conv-voice2")
    assert "Biscuit" in p3


def test_the_live_self_account_is_frozen_in_the_head(monkeypatch, memory):
    """WHAT YOU ACTUALLY ARE reports seat status, which changes when a seat
    loads or parks; it stays the same bytes for a conversation (prompt audit
    2026-10-04, item 3). The live truth rides in CAPABILITY STATE below the
    marker."""
    from agent_friday.services import model_router as mr
    from agent_friday.services import self_account
    status = {"s": "brain: loaded"}
    monkeypatch.setattr(self_account, "describe", lambda *a, **k: "Seats — " + status["s"])
    p1 = mr._get_friday_system_prompt(provider="local", vault_control=None,
                                      freeze_key="conv-acct")
    status["s"] = "brain: parked"
    p2 = mr._get_friday_system_prompt(provider="local", vault_control=None,
                                      freeze_key="conv-acct")
    h1, h2 = p1.split(VOLATILE_MARKER)[0], p2.split(VOLATILE_MARKER)[0]
    assert h1 == h2
    assert "Seats — brain: loaded" in h1
    unfrozen = mr._get_friday_system_prompt(provider="local", vault_control=None)
    assert "Seats — brain: parked" in unfrozen, "a call with no conversation renders live"
