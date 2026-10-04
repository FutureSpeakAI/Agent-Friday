"""A malformed tool call from the voice front is never executed (local voice
spec P1). llama-server constrains calls with the template's grammar; this is
the check that runs anyway: an unknown name, non-JSON arguments, a missing
required field or a wrong type is refused, the model is told why, and the
turn goes on. No server: the seat's HTTP is a fake that replays SSE.
"""
import json

import pytest

from agent_friday.services import voice_front as vf

CONTRACT = {"tools": [
    {"type": "function", "function": {
        "name": "query_calendar", "description": "calendar",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "check_email", "description": "mail",
        "parameters": {"type": "object",
                       "properties": {"urgent_only": {"type": "boolean"},
                                      "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "search_news", "description": "news",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
]}


def _call(name, args, cid="c1"):
    return {"id": cid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


@pytest.mark.parametrize("call, why", [
    (_call("run_command", {"cmd": "dir"}), "no tool called"),
    (_call("check_email", "{not json"), "not valid JSON"),
    (_call("check_email", "[1, 2]"), "JSON object"),
    (_call("search_news", {}), "required argument 'query'"),
    (_call("check_email", {"limit": "five"}), "'limit' must be of type integer"),
    (_call("check_email", {"limit": True}), "'limit' must be of type integer"),
])
def test_malformed_calls_are_refused_with_the_reason(call, why):
    name, args, err = vf.validate_tool_call(call, CONTRACT)
    assert args is None and why in err


def test_a_well_formed_call_passes():
    assert vf.validate_tool_call(_call("check_email", {"urgent_only": True, "limit": 3}),
                                 CONTRACT) == ("check_email", {"urgent_only": True, "limit": 3}, None)


class _Resp:
    def __init__(self, lines):
        self.lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        yield from self.lines

    def close(self):
        pass


def _sse(delta, finish=None):
    return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]})


def _tool_round(name, args):
    return [_sse({"tool_calls": [{"index": 0, "id": "t1", "function": {
        "name": name, "arguments": args}}]}), _sse({}, "tool_calls"), "data: [DONE]"]


def test_the_turn_loop_never_runs_a_malformed_call_and_tells_the_model(monkeypatch):
    rounds = [_tool_round("search_news", "{}"),                       # missing query
              _tool_round("search_news", json.dumps({"query": "rates"})),
              [_sse({"content": "Rates held steady."}), _sse({}, "stop"), "data: [DONE]"]]
    sent = []

    def fake_post(self, body, stream):
        sent.append(json.loads(json.dumps(body)))
        return _Resp(rounds.pop(0))
    monkeypatch.setattr(vf.FrontSeat, "_post", fake_post)
    ran = []
    seat = vf.FrontSeat()
    seat.model = "qwen3-4b-instruct-2507"
    out = seat.run_turn("SYS", [{"role": "user", "content": "news on rates"}], CONTRACT,
                        run_tool=lambda n, a: ran.append((n, a)) or "3 stories")
    assert ran == [("search_news", {"query": "rates"})], "the malformed call was executed"
    tool_msgs = [m for m in sent[1]["messages"] if m["role"] == "tool"]
    assert "required argument 'query'" in tool_msgs[0]["content"]
    assert out == "Rates held steady."
    assert all(b["cache_prompt"] is True and b["id_slot"] == 0 for b in sent)


def test_the_last_round_forbids_more_tool_calls(monkeypatch):
    calls = []

    def fake_post(self, body, stream):
        calls.append(body)
        if body.get("tool_choice") == "none":
            return _Resp([_sse({"content": "Here's what I found."}), _sse({}, "stop")])
        return _Resp(_tool_round("query_calendar", "{}"))
    monkeypatch.setattr(vf.FrontSeat, "_post", fake_post)
    seat = vf.FrontSeat()
    seat.model = "qwen3-4b-instruct-2507"
    out = seat.run_turn("SYS", [{"role": "user", "content": "hi"}], CONTRACT,
                        run_tool=lambda n, a: "ok")
    assert len(calls) == vf.MAX_TOOL_ROUNDS + 1
    assert out == "Here's what I found."
