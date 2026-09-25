"""A cloud model sees the user's private values as [PII:kind:hash] tags and
uses those tags in tool arguments. The tool runs on this machine and must
receive the real value, or a send goes to a literal tag."""
from __future__ import annotations

import agent_friday.services.agent as agent

TAG = "[PII:email:1a2b3c4d]"
REAL = "someone" + "@example.com"


def test_a_tool_receives_the_real_value_behind_a_placeholder(monkeypatch):
    seen = {}

    def handler(inp):
        seen.update(inp)
        return "ok"

    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "zz_capture_tool", handler)
    monkeypatch.setattr(agent._hooks, "run_pre_hooks",
                        lambda ctx: type("V", (), {"action": "allow", "reason": ""})())
    out = agent._execute_tool(
        "zz_capture_tool",
        {"to": TAG, "cc": [TAG, "plain"], "meta": {"reply_to": TAG}, "n": 3},
        pii_lookup={TAG: REAL},
        session_ctx={"authenticated": True})
    assert "ok" in str(out)
    assert seen["to"] == REAL
    assert seen["cc"] == [REAL, "plain"]
    assert seen["meta"] == {"reply_to": REAL}
    assert seen["n"] == 3


def test_without_a_lookup_the_arguments_are_untouched():
    args = {"to": TAG}
    assert agent._restore_placeholders(args, None) == args
    assert agent._restore_placeholders(args, {}) == args
