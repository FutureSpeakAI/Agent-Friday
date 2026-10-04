"""A tool loaded mid-task never changes the request's `tools` array.

SENSITIVE path: the request every cloud turn sends. Models that check
replayed thinking compare the tools of each request with the ones the thinking
was produced under, and the array renders first, so growing it re-bills the
whole cached prefix. On models that accept mid-conversation tool changes,
every tool is declared from the first request (non-resident ones deferred)
and a load is surfaced by an appended tool_addition message.
"""
import copy
import types

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import prompt_cache as pc
from agent_friday.services import tool_catalogue as tc

_USAGE = types.SimpleNamespace(input_tokens=1, output_tokens=1)
WANT = "write_clipboard"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_execute_tool", lambda name, args, **kw: f"result of {name}")
    monkeypatch.setattr(tc, "_REFUSED_TOOL_CHANGES", set())
    monkeypatch.delenv("FRIDAY_TOOL_CATALOGUE", raising=False)
    monkeypatch.delenv("FRIDAY_TOOL_CHANGES", raising=False)


def _load_round():
    return types.SimpleNamespace(content=[
        types.SimpleNamespace(type="thinking", thinking="need the clipboard", signature="SIG-1"),
        types.SimpleNamespace(type="tool_use", id="t1", name=tc.LOADER_NAME, input={"names": [WANT]}),
    ], stop_reason="tool_use", usage=_USAGE)


def _final():
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="done")],
                                 stop_reason="end_turn", usage=_USAGE)


def _client(monkeypatch, replies):
    sent = []

    class _Msgs:
        def create(self, **kw):
            sent.append(copy.deepcopy(kw))
            r = replies[len(sent) - 1]
            if isinstance(r, Exception):
                raise r
            return r

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Msgs()))
    return sent


def _plain(items):
    """Tools or messages with the moving cache markers removed."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k != "cache_control"}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    out = []
    for m in strip(items):
        if isinstance(m, dict) and isinstance(m.get("content"), str):
            m = {**m, "content": [{"type": "text", "text": m["content"]}]}
        out.append(m)
    return out


def test_a_supported_model_surfaces_the_tool_and_keeps_the_tools_array(monkeypatch):
    sent = _client(monkeypatch, [_load_round(), _final()])
    ag._call_claude_agent([{"role": "user", "content": "copy this"}], system="s",
                          model="claude-sonnet-5-5")
    assert len(sent) == 2
    assert _plain(sent[0]["tools"]) == _plain(sent[1]["tools"]), "the tools array changed mid-task"
    by_name = {t["name"]: t for t in sent[0]["tools"]}
    assert by_name[WANT].get("defer_loading") is True
    assert not by_name[tc.LOADER_NAME].get("defer_loading")
    for kw in sent:
        assert kw["extra_headers"]["anthropic-beta"] == tc.TOOL_CHANGES_BETA
    first, second = _plain(sent[0]["messages"]), _plain(sent[1]["messages"])
    assert second[:len(first)] == first, "a message already sent was edited"
    assert second[-1] == {"role": "system", "content": [
        {"type": "tool_addition", "tool": {"type": "tool_reference", "name": WANT}}]}
    assert "SIG-1" in str(second), "the round's thinking is replayed intact"


def test_a_model_without_the_beta_keeps_the_grown_list(monkeypatch):
    sent = _client(monkeypatch, [_load_round(), _final()])
    ag._call_claude_agent([{"role": "user", "content": "copy this"}], system="s",
                          model="claude-sonnet-5")
    names = [[t["name"] for t in kw["tools"]] for kw in sent]
    assert WANT not in names[0] and WANT in names[1]
    assert "extra_headers" not in sent[1]
    assert not any(m.get("role") == "system" for m in sent[1]["messages"])


def test_a_refused_beta_falls_back_without_replaying_thinking(monkeypatch):
    refused = Exception("Error code: 400 - tool_addition blocks are not supported")
    refused.status_code = 400
    sent = _client(monkeypatch, [_load_round(), refused, _final()])
    ag._call_claude_agent([{"role": "user", "content": "copy this"}], system="s",
                          model="claude-sonnet-5-5")
    assert len(sent) == 3
    retry = sent[2]
    assert "extra_headers" not in retry
    assert WANT in [t["name"] for t in retry["tools"]]
    assert not any(t.get("defer_loading") for t in retry["tools"])
    assert not any(m.get("role") == "system" for m in retry["messages"])
    assert "SIG-1" not in str(retry["messages"])
    assert not tc.tool_changes_supported("claude-sonnet-5-5"), "the refusal is remembered"


def test_cache_breakpoints_skip_tool_changes_and_deferred_tools():
    tools = [{"name": "a", "description": "x" * 20000, "input_schema": {}},
             {"name": "b", "description": "y", "input_schema": {}, "defer_loading": True}]
    marked, hit = pc._mark_last_tool(tools, "claude-sonnet-5-5")
    assert hit and "cache_control" in marked[0] and "cache_control" not in marked[1]
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "r"}]},
            {"role": "system", "content": [{"type": "tool_addition",
                                            "tool": {"type": "tool_reference", "name": "b"}}]}]
    marked, hit = pc._mark_last_message(msgs)
    assert hit and "cache_control" in marked[0]["content"][-1]
    assert "cache_control" not in marked[1]["content"][0]


def test_a_steer_goes_before_a_trailing_tool_change():
    convo = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "r"}]},
             {"role": "system", "content": [{"type": "tool_addition",
                                             "tool": {"type": "tool_reference", "name": "b"}}]}]
    ag._append_steer(convo, "check the second source")
    assert convo[-1]["role"] == "system"
    assert "check the second source" in str(convo[0])
