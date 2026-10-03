"""On the local seat the system prompt is the same bytes on every turn.

What this message chose (wiki matches, memories, skills, the clock) rides at
the top of the newest user turn; the system message is the stable head with
the action policy sealed last. So llama-server reuses its cache for the
system prompt and the whole history and reads only the new turn. On the
cloud the same tail follows the head in the system prompt, below the cache
marker, and the policy is still last.
"""
from __future__ import annotations

import copy

import pytest

from agent_friday import core
from agent_friday.routes import chat
from agent_friday.routing.model_router import ModelRouter
from agent_friday.services.action_policy import ACTION_PERMISSION_POLICY
from agent_friday.services.prompt_cache import VOLATILE_MARKER


def _fake_context(message, *a, **k):
    return ("== PERSONA ==\nI am Friday.\n== TOOLS ==\nsearch_web — search\n"
            + VOLATILE_MARKER + "\nNow: 12:00\n"
            + "== MATCHED SKILLS (follow when relevant) ==\nskill for " + message + "\n", [])


@pytest.fixture
def wired(monkeypatch, patch_app):
    settings = copy.deepcopy(core.DEFAULT_SETTINGS)
    settings["model_routing"].update({"mode": "local_preferred", "vault_local_only": False})
    patch_app("_load_settings", lambda: settings)
    patch_app("_build_memory_context_block", lambda *a, **k: "")
    patch_app("_build_session_continuity_block", lambda *a, **k: "")
    patch_app("_build_emotional_tone_block", lambda *a, **k: "")
    patch_app("_build_context_prompt", _fake_context)
    patch_app("_index_chat_turn", lambda *a, **k: None)
    return settings


def _route(monkeypatch, provider):
    local = provider == "local"
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": provider, "model": "bonsai2:27b" if local else "claude-sonnet-5-5",
        "provider_name": "bonsai2-local" if local else "anthropic",
        "is_local": local, "vault_allowed": local, "vault_access": local,
        "scrub_pii": not local, "refuse": False, "warning": None,
    })


def test_the_local_system_prompt_is_identical_across_turns_and_the_tail_rides_in_the_user_turn(
        client, monkeypatch, wired):
    _route(monkeypatch, "local")
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy({"messages": messages, **kwargs}))
        return "ok", []
    monkeypatch.setattr(chat, "_call_ollama", dispatch)

    r1 = client.post("/api/chat", json={"message": "tell me about cats"})
    assert r1.status_code == 200, r1.get_data(as_text=True)[:300]
    cid = (r1.get_json() or {}).get("conversation_id")
    r2 = client.post("/api/chat", json={"message": "what is the weather tomorrow",
                                        **({"conversation_id": cid} if cid else {})})
    assert r2.status_code == 200, r2.get_data(as_text=True)[:300]
    assert len(seen) == 2, "the local seat was not the one dispatched to"
    s1, s2 = seen[0]["system"], seen[1]["system"]
    assert s1 == s2, "the system prompt changed between two local turns"
    assert VOLATILE_MARKER not in s1 and "skill for" not in s1
    assert s1.rstrip().endswith(ACTION_PERMISSION_POLICY)
    last = seen[1]["messages"][-1]
    assert last["role"] == "user"
    assert last["content"].startswith("[CONTEXT FOR THIS TURN")
    assert "skill for what is the weather tomorrow" in last["content"]
    assert last["content"].rstrip().endswith("what is the weather tomorrow")


def test_the_cloud_keeps_the_tail_in_the_system_prompt_with_the_policy_last(
        client, monkeypatch, wired):
    _route(monkeypatch, "cloud")
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy({"messages": messages, **kwargs}))
        return "ok", []
    monkeypatch.setattr(chat, "_call_claude_agent", dispatch)
    r = client.post("/api/chat", json={"message": "tell me about cats"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert len(seen) == 1
    system = seen[0]["system"]
    head, tail = system.split(VOLATILE_MARKER, 1)
    assert "== PERSONA ==" in head and "skill for tell me about cats" in tail
    assert system.rstrip().endswith(ACTION_PERMISSION_POLICY)
    assert not seen[0]["messages"][-1]["content"].startswith("[CONTEXT FOR THIS TURN")
