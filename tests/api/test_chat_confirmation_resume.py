"""Both chat APIs resume stored approvals before asking a model to act."""
import pytest

from agent_friday.routes import chat
from agent_friday.services import agent, conversations


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/send"])
def test_chat_approval_returns_observed_output_without_model_regeneration(
        endpoint, client, monkeypatch, patch_app):
    cid = conversations.create("Approval replay")["id"]
    ctx = agent.prepare_confirmation_ctx("same-day", "run it", {
        "authenticated": True, "conversation_id": cid})
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"run_command"})
    monkeypatch.setattr(agent, "_sandbox_policy", lambda *a: (True, ""))
    inp = {"command": "Write-Output exact"}
    assert "CONFIRMATION REQUIRED" in agent._execute_tool("run_command", inp, session_ctx=ctx)
    calls = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: calls.append(args) or "<b>observed</b> ``` data")

    def no_generation(*a, **kw):
        raise AssertionError("An approved action must not depend on a new model tool call")

    for name in ("_generate_agent", "_call_claude_agent", "_call_ollama", "_call_openai"):
        patch_app(name, no_generation)
    response = client.post(endpoint, json={"message": "yes", "conversation_id": cid})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert calls == [inp]
    assert body["tool_trace"][0]["input"] == inp
    assert "````\n<b>observed</b> ``` data\n````" in body["friday_msg"]["text"]
    stored = conversations.messages(cid)
    assert any("observed" in row.get("text", "") for row in stored)


def test_approved_navigation_returns_the_action_the_desktop_executes(client, monkeypatch):
    cid = conversations.create("Navigation approval")["id"]
    ctx = agent.prepare_confirmation_ctx("same-day", "switch workspace", {
        "authenticated": True, "conversation_id": cid})
    assert "CONFIRMATION REQUIRED" in agent._execute_tool(
        "navigate", {"workspace": "knowledge"}, session_ctx=ctx)
    approved = agent.prepare_confirmation_ctx("same-day", "yes", {
        "authenticated": True, "conversation_id": cid})
    with client.application.test_request_context():
        body = chat._confirmed_action_response("yes", approved).get_json()
    assert body["actions"] == [{"type": "navigate", "workspace": "knowledge"}]
