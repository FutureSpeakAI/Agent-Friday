"""On every seat the system prompt is the same bytes on every turn.

What this message chose (wiki matches, memories, skills, the clock) rides at
the top of the newest user turn; the system message is the stable head with
the action policy sealed last. So llama-server reuses its cache for the
system prompt and the whole history and reads only the new turn, and on the
cloud the cached prefix and the earlier turns' thinking stay valid. On the
cloud the context is its own text block, so the egress gate judges it apart
from the user's words.
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


def test_the_cloud_system_prompt_is_identical_across_turns_and_the_tail_rides_in_the_user_turn(
        client, monkeypatch, wired):
    """SENSITIVE path. A system prompt that changes every turn re-bills the
    whole replayed history and invalidates the earlier turns' thinking."""
    _route(monkeypatch, "cloud")
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy({"messages": messages, **kwargs}))
        return "ok", []
    monkeypatch.setattr(chat, "_call_claude_agent", dispatch)
    r1 = client.post("/api/chat", json={"message": "tell me about cats"})
    assert r1.status_code == 200, r1.get_data(as_text=True)[:300]
    cid = (r1.get_json() or {}).get("conversation_id")
    r2 = client.post("/api/chat", json={"message": "what is the weather tomorrow",
                                        **({"conversation_id": cid} if cid else {})})
    assert r2.status_code == 200, r2.get_data(as_text=True)[:300]
    assert len(seen) == 2
    s1, s2 = seen[0]["system"], seen[1]["system"]
    assert s1 == s2, "the system prompt changed between two cloud turns"
    assert VOLATILE_MARKER not in s1 and "skill for" not in s1
    assert "== PERSONA ==" in s1 and s1.rstrip().endswith(ACTION_PERMISSION_POLICY)
    last = seen[1]["messages"][-1]
    assert last["role"] == "user" and isinstance(last["content"], list)
    ctx, said = last["content"][0]["text"], last["content"][-1]["text"]
    assert ctx.startswith("[CONTEXT FOR THIS TURN") and "skill for what is the weather tomorrow" in ctx
    assert said == "what is the weather tomorrow"


def test_a_guarded_cloud_turn_keeps_the_newest_user_message_scrubbed(
        client, monkeypatch, wired):
    """The restore of the user's own text happens before the cloud scrub,
    never after it: a scrubbed newest turn must reach the cloud scrubbed."""
    from agent_friday import core as _core
    wired["model_routing"].update({"unrestricted_cloud": False,
                                   "cloud_consent": {"answered": True, "choice": "cloud_guarded"}})
    monkeypatch.setattr(_core, "_load_privacy_watchlist", lambda: ["Private appointment"])
    monkeypatch.setattr(_core, "_owner_emails", lambda: [])
    _route(monkeypatch, "cloud")
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy({"messages": messages, **kwargs}))
        return "ok", []
    monkeypatch.setattr(chat, "_call_claude_agent", dispatch)
    r = client.post("/api/chat", json={"message": "Discuss Private appointment with sender@example.test"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert len(seen) == 1
    last = str(seen[0]["messages"][-1]["content"])
    assert "sender@example.test" not in last and "Private appointment" not in last, last
    assert seen[0]["pii_lookup"]
