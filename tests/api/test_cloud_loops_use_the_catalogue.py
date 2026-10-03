"""Cloud turns send the opening set, not every schema, and load the rest.

The Anthropic loop and the chat route's cloud OpenAI path used to send every
tool on every round (~46k tokens a call). They now send what the local seat
sends: the resident tools and the loader. A `load_tools` call by name or by
query adds schemas to the next request, and a tool called without its schema
still runs and gets its schema for the round after.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import tool_catalogue as tc
from agent_friday.services import turn_budget as tb


class _Block:
    def __init__(self, btype, text="", name="", inp=None, bid="b1"):
        self.type = btype
        self.text = text
        self.name = name
        self.input = inp or {}
        self.id = bid


class _Resp:
    def __init__(self, blocks, stop_reason):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                    "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0})()


class _Messages:
    def __init__(self, script):
        self._script = script
        self.calls = 0
        self.sent = []

    def create(self, **kw):
        self.calls += 1
        self.sent.append(kw)
        return self._script(self.calls, kw)


class _Client:
    def __init__(self, script):
        self.messages = _Messages(script)


def _names(tools):
    return [(t.get("function") or t).get("name") for t in (tools or [])]


def _drive(monkeypatch, script, ran):
    import agent_friday.core as core
    client = _Client(script)
    monkeypatch.setattr(ag, "get_anthropic_client", lambda *a, **k: client)
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: ran.append((n, a)) or "ok")
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_ledger_tool_call", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_orb_tool_trace", lambda *a, **k: None, raising=False)
    monkeypatch.delenv("FRIDAY_TOOL_CATALOGUE", raising=False)
    text, trace = ag._call_claude_agent([{"role": "user", "content": "go"}],
                                        model="claude-sonnet-5")
    return text, trace, client.messages


def test_the_first_cloud_round_sends_the_opening_set_not_everything(monkeypatch):
    ran = []

    def script(n, kw):
        return _Resp([_Block("text", text="done")], "end_turn")
    _, _, msgs = _drive(monkeypatch, script, ran)
    sent = _names(msgs.sent[0]["tools"])
    assert tc.LOADER_NAME in sent
    for r in tc.ALWAYS_RESIDENT:
        assert r in sent
    assert len(sent) < len(ag.CLAUDE_TOOLS) / 2, "the cloud still gets every schema"


def test_a_query_load_adds_the_schema_to_the_next_round(monkeypatch):
    ran = []

    def script(n, kw):
        if n == 1:
            return _Resp([_Block("tool_use", name=tc.LOADER_NAME,
                                 inp={"query": "compose an email"}, bid="t1")], "tool_use")
        return _Resp([_Block("text", text="done")], "end_turn")
    text, trace, msgs = _drive(monkeypatch, script, ran)
    assert ran == [], "load_tools is a description hand-over, not an execution"
    first, second = _names(msgs.sent[0]["tools"]), _names(msgs.sent[1]["tools"])
    added = set(second) - set(first)
    assert added and any("email" in n for n in added), added
    loader_result = [m for m in msgs.sent[1]["messages"] if m["role"] == "user"][-1]["content"][0]
    assert "Loaded for" in loader_result["content"]


def test_a_tool_called_without_its_schema_still_runs_and_gets_it(monkeypatch):
    ran = []
    target = next(t["name"] for t in ag.CLAUDE_TOOLS
                  if t["name"] not in tc.ALWAYS_RESIDENT and t["name"] != tc.LOADER_NAME)

    def script(n, kw):
        if n == 1:
            return _Resp([_Block("tool_use", name=target, inp={}, bid="t1")], "tool_use")
        return _Resp([_Block("text", text="done")], "end_turn")
    _, _, msgs = _drive(monkeypatch, script, ran)
    assert [n for n, _ in ran] == [target]
    assert target not in _names(msgs.sent[0]["tools"])
    assert target in _names(msgs.sent[1]["tools"])


def test_the_chat_routes_cloud_openai_path_uses_the_same_opening_set(monkeypatch):
    from agent_friday.routes import chat as chat_mod
    monkeypatch.delenv("FRIDAY_TOOL_CATALOGUE", raising=False)
    tools, catalogue = chat_mod._cloud_tool_set(None)
    assert catalogue is ag.CLAUDE_TOOLS
    assert tc.LOADER_NAME in _names(tools) and len(tools) < len(ag.CLAUDE_TOOLS) / 2
    monkeypatch.setenv("FRIDAY_TOOL_CATALOGUE", "0")
    tools, catalogue = chat_mod._cloud_tool_set(None)
    assert tools is ag.CLAUDE_TOOLS and catalogue is None
