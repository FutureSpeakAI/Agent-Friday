"""The cloud tool loop never edits what it already sent, and never goes silent.

Claude models that check replayed thinking compare the system prompt, the
tools and every earlier message with the request the thinking came from. An
edit there makes the provider reject the request or drop the reasoning, and
it re-bills the cached prefix. These tests drive the real loop with a fake
client and look at the requests it actually sent.
"""

import copy
import types

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import compaction


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_execute_tool", lambda name, args, **kw: f"result of {name}")


def _client(monkeypatch, replies):
    """Fake Anthropic that answers with `replies` in order and records each
    request exactly as it was sent."""
    sent = []

    class _Msgs:
        def create(self, **kw):
            sent.append(copy.deepcopy(kw))
            return replies[min(len(sent), len(replies)) - 1]

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Msgs()))
    return sent


_USAGE = types.SimpleNamespace(input_tokens=1, output_tokens=1)


def _wire(messages):
    """Messages as the provider compares them: string content is one text
    block, and cache_control markers (which move every round, and which the
    provider ignores for this check) are left out."""
    out = []
    for m in messages:
        c = m["content"]
        blocks = [{"type": "text", "text": c}] if isinstance(c, str) else c
        out.append({"role": m["role"],
                    "content": [{k: v for k, v in b.items() if k != "cache_control"} for b in blocks]})
    return out


def _tool_round(i):
    return types.SimpleNamespace(content=[
        types.SimpleNamespace(type="text", text=f"step {i}"),
        types.SimpleNamespace(type="tool_use", id=f"t{i}", name="search_web", input={"q": f"x{i}"}),
    ], stop_reason="tool_use", usage=_USAGE)


def _final(text="done"):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)],
                                 stop_reason="end_turn", usage=_USAGE)


def test_a_refusal_is_reported_to_the_user_not_returned_empty(monkeypatch):
    _client(monkeypatch, [types.SimpleNamespace(
        content=[], stop_reason="refusal", model="claude-sonnet-5-5",
        stop_details=types.SimpleNamespace(type="refusal", category="cyber", explanation=None),
        usage=_USAGE)])
    text, _trace = ag._call_claude_agent([{"role": "user", "content": "go"}], system="s")
    assert "declined" in text and "cyber" in text, text


def test_an_operator_steer_leaves_the_system_prompt_and_sent_turns_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(ag, "FRIDAY_DIR", tmp_path)
    sent = _client(monkeypatch, [_tool_round(1), _final()])
    real_execute = ag._execute_tool

    def _execute_and_steer(name, args, **kw):
        # The owner writes the steer file while the first tool runs.
        (tmp_path / "STEER.md").write_text("check the second source too", encoding="utf-8")
        return real_execute(name, args, **kw)

    monkeypatch.setattr(ag, "_execute_tool", _execute_and_steer)
    ag._call_claude_agent([{"role": "user", "content": "go"}], system="SYSTEM")
    assert len(sent) == 2
    assert sent[0]["system"] == sent[1]["system"], "the steer edited the system prompt"
    first, second = _wire(sent[0]["messages"]), _wire(sent[1]["messages"])
    assert second[:len(first)] == first, "a message already sent was edited"
    assert "check the second source too" in str(second[-1]), "the steer never reached the model"


def test_strip_thinking_removes_only_thinking_blocks():
    convo = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "plan", "signature": "S"},
            {"type": "redacted_thinking", "data": "D"},
            {"type": "text", "text": "looking"},
            {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "only", "signature": "S2"}]},
    ]
    out = compaction.strip_thinking(convo)
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use"]
    assert out[2] == convo[2]
    assert out[3]["content"] and all(b["type"] == "text" and b["text"] for b in out[3]["content"])
    assert convo[1]["content"][0]["type"] == "thinking", "the input list is not mutated"


def test_a_compacted_transcript_replays_no_thinking(monkeypatch):
    """Keep-tail compaction rewrites the history before the kept turns; the
    kept turns' thinking no longer matches it, so none is replayed."""
    thinking_round = types.SimpleNamespace(content=[
        types.SimpleNamespace(type="thinking", thinking="plan", signature="SIG-KEPT"),
        types.SimpleNamespace(type="text", text="step 1"),
        types.SimpleNamespace(type="tool_use", id="t1", name="search_web", input={"q": "x1"}),
    ], stop_reason="tool_use", usage=_USAGE)
    sent = _client(monkeypatch, [thinking_round, _final()])

    def _fake_compact(messages, **kw):
        if "SIG-KEPT" not in str(messages):
            return messages
        return [{"role": "user", "content": "[Context Summary] earlier work"}] + list(messages[1:])

    monkeypatch.setattr(compaction, "maybe_compact", _fake_compact)
    ag._call_claude_agent([{"role": "user", "content": "go"}], system="s")
    assert len(sent) == 2
    assert "SIG-KEPT" not in str(sent[1]["messages"])
