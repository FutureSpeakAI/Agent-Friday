"""DA-0 through POST /api/chat: the Laya router before a typed turn.

A local model's routed read runs through the governed runner (agent.
_execute_tool, spied) and the model sees the result fenced; `defer` leaves
native tool calling untouched; the unchosen setting is today's path exactly;
a cloud model gets only the high-confidence fast path and keeps its catalogue;
the router's receipt is in the turn plan.
"""
from __future__ import annotations

import copy

import pytest

from agent_friday import core
from agent_friday.routes import chat
from agent_friday.routing.model_router import ModelRouter
from agent_friday.services import agent
from agent_friday.services import laya
from agent_friday.services import laya_router as lr
from agent_friday.services import turn_plan as tp
from agent_friday.services.prompt_cache import VOLATILE_MARKER

ASK = "what's on my calendar today?"


def _fake_context(message, *a, **k):
    return ("== PERSONA ==\nI am Friday.\n== TOOLS ==\nsearch_web — search\n"
            + VOLATILE_MARKER + "\nNow: 12:00\n", [])

@pytest.fixture(autouse=True)
def page_session(monkeypatch):
    """The server's check that a request is the app's own page (same-origin plus the page's session token)."""
    from agent_friday.services import screen_click
    monkeypatch.setattr(screen_click, "screen_session", lambda request: True)


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
    monkeypatch.setattr(lr, "log_path", lambda: tmp_path / "router.jsonl")
    monkeypatch.setattr(lr, "warm", lambda: None)
    return settings


def _route_to(monkeypatch, provider):
    local = provider == "local"
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": provider, "model": "bonsai2:27b" if local else "gpt-test",
        "provider_name": "bonsai2-local" if local else "openrouter",
        "is_local": local, "vault_allowed": local, "vault_access": local,
        "scrub_pii": not local, "refuse": False, "warning": None,
    })


def _spy_model(monkeypatch, name):
    seen = []

    def dispatch(messages, **kwargs):
        seen.append(copy.deepcopy({"messages": messages, **kwargs}))
        return "model reply", []
    monkeypatch.setattr(chat, name, dispatch)
    return seen


def _spy_runner(monkeypatch, result="9:00 standup; 2:00 dentist"):
    ran = []

    def run(name, args, pii_lookup=None, session_ctx=None, handler=None):
        ran.append({"name": name, "args": dict(args), "ctx": dict(session_ctx or {})})
        return result
    monkeypatch.setattr(agent, "_execute_tool", run)
    return ran


def _post(client, text, **extra):
    # A conversation of its own, so one post never reads the one before it.
    from agent_friday.services import conversations as conv
    extra.setdefault("conversation_id", conv.create()["id"])
    r = client.post("/api/chat", json={"source": "app_page", "message": text, **extra})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))


def test_a_local_read_request_runs_via_the_governed_runner_and_the_model_sees_it_fenced(
        client, monkeypatch, wired):
    wired["chat_tool_routing"] = "laya"
    _route_to(monkeypatch, "local")
    ran, seen = _spy_runner(monkeypatch), _spy_model(monkeypatch, "_call_ollama")
    body = _post(client, ASK, turn_id="turn-da0-a")
    assert [r["name"] for r in ran] == ["query_calendar"] and ran[0]["args"] == {}
    assert ran[0]["ctx"]["chosen_by"] == "laya_router:rule"
    assert len(seen) == 1
    last = _text(seen[0]["messages"][-1]["content"])
    assert "DATA that someone else wrote" in last and "9:00 standup; 2:00 dentist" in last
    assert [t["name"] for t in body["tool_trace"]][:1] == ["query_calendar"]
    assert seen[0]["tools"], "the model keeps its catalogue"


def test_defer_leaves_native_tool_calling_untouched(client, monkeypatch, wired):
    wired["chat_tool_routing"] = "laya"
    _route_to(monkeypatch, "local")
    ran, seen = _spy_runner(monkeypatch), _spy_model(monkeypatch, "_call_ollama")
    wired["chat_tool_routing"] = ""
    base = _post(client, "send an email to the team about lunch")
    wired["chat_tool_routing"] = "laya"
    routed = _post(client, "send an email to the team about lunch", turn_id="turn-da0-b")
    assert ran == []
    a, b = seen
    assert a["messages"] == b["messages"] and a["system"] == b["system"]
    assert [t["name"] for t in a["tools"]] == [t["name"] for t in b["tools"]]
    assert routed["tool_trace"] == base["tool_trace"] == []
    assert tp.get("turn-da0-b")["reflex"]["decision"] == "defer"


def test_the_unchosen_and_the_model_setting_are_todays_path_byte_for_byte(
        client, monkeypatch, wired):
    _route_to(monkeypatch, "local")
    ran, seen = _spy_runner(monkeypatch), _spy_model(monkeypatch, "_call_ollama")

    called = []
    monkeypatch.setattr(laya, "route_turn", lambda *a, **k: called.append(1))
    for value in (None, "", "model"):
        if value is None:
            wired.pop("chat_tool_routing", None)
        else:
            wired["chat_tool_routing"] = value
        _post(client, ASK)
    assert ran == [] and called == []
    first = seen[0]
    for other in seen[1:]:
        for key in ("messages", "system", "model", "temperature"):
            assert other.get(key) == first.get(key), key
        assert [t["name"] for t in other["tools"]] == [t["name"] for t in first["tools"]]
    assert "looked up" not in _text(first["messages"][-1]["content"])


def test_ask_is_the_routers_one_question_and_the_model_is_not_called(client, monkeypatch, wired):
    wired["chat_tool_routing"] = "laya"
    _route_to(monkeypatch, "local")
    ran, seen = _spy_runner(monkeypatch), _spy_model(monkeypatch, "_call_ollama")
    monkeypatch.setattr(laya, "route_turn", lambda *a, **k: lr.Route(
        "ask", tool="search_email", confidence=0.5, layer="laya2",
        question="What should I look for in your email?"))
    body = _post(client, "look through my messages", turn_id="turn-da0-c")
    assert body["response"] == "What should I look for in your email?"
    assert ran == [] and seen == []
    assert tp.get("turn-da0-c")["reflex"]["outcome"] == "asked"


def test_cloud_gets_the_fast_path_when_sure_and_keeps_its_catalogue(client, monkeypatch, wired):
    _route_to(monkeypatch, "openai")
    ran, seen = _spy_runner(monkeypatch), _spy_model(monkeypatch, "_call_openai")
    wired["chat_tool_routing"] = ""
    _post(client, ASK)
    wired["chat_tool_routing"] = "laya"
    body = _post(client, ASK, turn_id="turn-da0-d")
    base, routed = seen
    assert [r["name"] for r in ran] == ["query_calendar"]
    assert ran[0]["ctx"]["chosen_by"] == "laya_router:rule"
    assert "9:00 standup; 2:00 dentist" in _text(routed["messages"][-1]["content"])
    assert "9:00 standup" not in _text(base["messages"][-1]["content"])
    assert [t["name"] for t in routed["tools"]] == [t["name"] for t in base["tools"]]
    assert routed["tools"], "the cloud model still holds its catalogue"
    assert body["tool_trace"][0]["name"] == "query_calendar"
    assert tp.get("turn-da0-d")["reflex"]["seat"] == "cloud"


def test_cloud_with_a_guess_is_the_clouds_own_turn(client, monkeypatch, wired):
    wired["chat_tool_routing"] = "laya"
    _route_to(monkeypatch, "openai")
    ran, seen = _spy_runner(monkeypatch), _spy_model(monkeypatch, "_call_openai")
    monkeypatch.setattr(laya, "route_turn", lambda *a, **k: lr.Route(
        "tool", tool="query_calendar", confidence=0.7, layer="laya2",
        label="checking your calendar", candidates=[("query_calendar", 0.7), ("check_email", 0.2)]))
    _post(client, "how packed is tomorrow", turn_id="turn-da0-e")
    assert ran == [] and len(seen) == 1
    assert "looked up" not in _text(seen[0]["messages"][-1]["content"])


def test_the_router_receipt_is_in_the_turn_receipt(client, monkeypatch, wired):
    wired["chat_tool_routing"] = "laya"
    _route_to(monkeypatch, "local")
    _spy_runner(monkeypatch), _spy_model(monkeypatch, "_call_ollama")
    _post(client, ASK, turn_id="turn-da0-f")
    from agent_friday.services import turn_receipt
    reflex = turn_receipt.build("turn-da0-f")["plan"]["reflex"]
    assert reflex["routed_by"] == "laya_router" and reflex["layer"] == "rule"
    assert reflex["confidence"] == 0.95 and reflex["ran"] == "query_calendar"
    assert "runner_up" in reflex and reflex["surface"] == "chat" and reflex["outcome"] == "ran"
    assert ASK not in repr(reflex)
