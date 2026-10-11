"""P7-5 through POST /api/chat: no model round for a whole yes or an opted-in open.

The model entry points are replaced by spies that count (or refuse) every call,
so "zero model calls" is a measured fact, not an inference.
"""
from __future__ import annotations

import pytest

import agent_friday.routes.chat as chat_mod
from agent_friday.services import agent, approvals
from agent_friday.services import conversations as conv

SID = "sess-api-reflex"


@pytest.fixture
def model_calls(monkeypatch):
    calls = []

    def spy(*a, **k):
        calls.append(1)
        return ("model reply", [])
    monkeypatch.setattr(chat_mod, "_call_claude_agent", spy)
    monkeypatch.setattr(chat_mod, "_current_session_id", lambda: SID)
    for name in ("_call_ollama", "_call_openai", "_call_claude"):
        monkeypatch.setattr(chat_mod, name, lambda *a, _n=name, **k: (calls.append(_n), "model reply")[1])
    return calls


@pytest.fixture
def pending(tmp_path, monkeypatch, client):
    agent._PENDING_CONFIRMATIONS.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(agent, "_sandbox_policy", lambda *a: (True, ""))
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"run_command"})
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: ran.append(dict(args)) or "observed output")
    cid = conv.resolve(None)
    ctx = agent.prepare_confirmation_ctx(SID, "run this", {"authenticated": True,
                                                           "conversation_id": cid})
    assert "CONFIRMATION REQUIRED" in agent._execute_tool(
        "run_command", {"command": "Write-Output first"}, session_ctx=ctx)
    yield cid, ran
    agent._PENDING_CONFIRMATIONS.clear()


def test_a_whole_yes_runs_the_action_with_zero_model_calls(client, model_calls, pending,
                                                          monkeypatch):
    """Before routing and prompt assembly: nothing of the turn's preparation runs."""
    from agent_friday.routing import model_router
    prepared = []
    real_get_router = model_router.get_router
    monkeypatch.setattr(model_router, "get_router",
                        lambda *a, **k: prepared.append(1) or real_get_router(*a, **k))
    cid, ran = pending
    resp = client.post("/api/chat", json={"message": "yes", "conversation_id": cid})
    body = resp.get_json()
    assert resp.status_code == 200
    assert ran == [{"command": "Write-Output first"}]
    assert "Result of the action you approved" in body["response"]
    assert "observed output" in body["response"]
    assert model_calls == []
    assert prepared == [], "the yes must not wait for routing and prompt assembly"


def test_yes_but_is_not_an_approval_and_reaches_the_model(client, model_calls, pending):
    cid, ran = pending
    resp = client.post("/api/chat", json={"message": "yes, but use the other folder",
                                          "conversation_id": cid})
    assert resp.status_code == 200
    assert ran == []
    assert model_calls, "a conditional reply is an ordinary turn"


def test_a_reply_marked_as_sms_is_not_a_reflex(client, model_calls, pending):
    """The ordinary claim path still answers it (it never needed a model); the
    reflex itself stays out, and says so in its counters."""
    from agent_friday.services import reflex_chat
    reflex_chat.reset_stats()
    cid, ran = pending
    client.post("/api/chat", json={"message": "yes", "conversation_id": cid, "source": "sms"})
    stats = reflex_chat.stats()
    assert stats.get("approval_untrusted_surface") == 1
    assert "approval_reply" not in stats


def test_opted_in_open_answers_with_zero_model_calls(client, model_calls, monkeypatch):
    from agent_friday.services import laya_resolver

    class Chosen:
        title = "Go-live checklist"
    res = laya_resolver.Resolution(
        "command", "Opening Go-live checklist.", kind="email",
        command={"tool": "navigate_to", "input": {"kind": "email", "id": "t-1"}},
        chosen=laya_resolver.Candidate("email", "t-1", "Go-live checklist"))
    asked = []
    monkeypatch.setattr(laya_resolver, "resolve", lambda text: asked.append(text) or res)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate_to", lambda inp: "NAV_OK:shown")
    real = chat_mod._load_settings
    monkeypatch.setattr(chat_mod, "_load_settings",
                        lambda *a, **k: dict(real(*a, **k), reflex_open_enabled=True))
    body = client.post("/api/chat", json={"message": "open the go-live checklist"}).get_json()
    assert body["response"] == "Done: opened Go-live checklist (instant, no model)."
    assert body["instant"] is True
    assert asked == ["open the go-live checklist"]
    assert model_calls == []


def test_opt_in_off_means_no_resolver_call_and_a_normal_turn(client, model_calls, monkeypatch):
    from agent_friday.services import laya_resolver
    monkeypatch.setattr(laya_resolver, "resolve",
                        lambda text: (_ for _ in ()).throw(AssertionError("resolver called")))
    resp = client.post("/api/chat", json={"message": "open the go-live checklist"})
    assert resp.status_code == 200
    assert model_calls


def test_the_palette_route_is_off_until_chosen(client, monkeypatch):
    from agent_friday.services import laya_resolver
    monkeypatch.setattr(laya_resolver, "resolve",
                        lambda text: (_ for _ in ()).throw(AssertionError("resolver called")))
    assert client.post("/api/reflex/open", json={"text": "open news"}).get_json()["status"] == "off"
    status = client.get("/api/reflex/status").get_json()
    assert status["approval_replies"] is True and status["open_enabled"] is False
