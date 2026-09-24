"""The Claude tool loop never sends an empty text block.

The Messages API rejects the whole request -- HTTP 400, "text content blocks
must be non-empty" -- and the turn dies with nothing for the user. It was
reproduced on AgentDojo banking user_task_3 (Haiku 4.5) on the code before and
after the governance work. Three ways one gets in:

  * the model's own turn carries a text block with "" next to its tool call,
    and the loop echoes it back;
  * a tool returns an empty string (a search with no hits, an empty list),
    which becomes a tool_result with empty text;
  * the history the caller passes in (a resumed or compacted conversation)
    already holds one.
"""
from __future__ import annotations

import types

import agent_friday.services.agent as ag


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)


def _empty_text_blocks(messages):
    bad = []
    for i, m in enumerate(messages):
        c = m.get("content")
        if isinstance(c, str):
            if not c.strip():
                bad.append((i, "message"))
            continue
        for b in c or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and not str(b.get("text") or "").strip():
                bad.append((i, "text block"))
            if b.get("type") == "tool_result":
                rc = b.get("content")
                if isinstance(rc, str) and not rc.strip():
                    bad.append((i, "tool_result"))
                if isinstance(rc, list) and any(
                        x.get("type") == "text" and not str(x.get("text") or "").strip()
                        for x in rc if isinstance(x, dict)):
                    bad.append((i, "tool_result block"))
    return bad


def test_no_request_carries_an_empty_text_block(monkeypatch):
    sent = []

    class _Messages:
        def create(self, **kwargs):
            sent.append(kwargs["messages"])
            if len(sent) == 1:
                return _Resp([_Block(type="text", text=""),
                              _Block(type="tool_use", id="tu1", name="search_wiki",
                                     input={"query": "nothing"})], "tool_use")
            return _Resp([_Block(type="text", text="Nothing found.")], "end_turn")

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Messages()))
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "search_wiki", lambda inp: "")

    history = [{"role": "user", "content": "look it up"},
               {"role": "assistant", "content": [{"type": "text", "text": ""}]},
               {"role": "user", "content": "go on"}]
    text, _trace = ag._call_claude_agent(
        history, system="s", session_ctx={"authenticated": True, "is_background_task": True})

    assert len(sent) == 2, "the loop did not reach its second round"
    for n, messages in enumerate(sent, 1):
        assert not _empty_text_blocks(messages), (n, _empty_text_blocks(messages))
    assert text == "Nothing found."
