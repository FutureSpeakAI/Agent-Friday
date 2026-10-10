"""The local voice front calls its tools; it does not perform calling them.

Measured on the release bench (2026-10-09): with the cloud voice's tool
choreography ("announce what you are about to do, END the sentence before
invoking the tool, then confirm 'Okay, it's up on screen.'"), Qwen3-4B made
2 tool calls in 9 requests and recited "Pulling that story up now. Okay, it's
up on screen." over invented results; Ternary Bonsai 1.7B made none. Qwen3-1.7B
on the same prompt at temperature 0: 2 of 9 with the choreography, 4 of 9 with
the front's call-first rule. The cloud voice keeps its choreography.

And a call cut off by the token budget (arguments '{') was sent back in the
turn's history verbatim; llama-server could not render it and failed the
whole turn with HTTP 500 ("Failed to parse tool call arguments as JSON",
reproduced against a real server).
"""
import json

import pytest


@pytest.fixture
def front_prompt(monkeypatch):
    import agent_friday.routes.voice as rv
    from agent_friday.services import voice_context_digest
    monkeypatch.setattr(voice_context_digest, "build", lambda settings=None, **kw: "DIGEST-TEXT")
    monkeypatch.setattr(rv, "_get_voice_style_prompt", lambda: "")
    contract = {"names": ["query_calendar", "check_email", "ask_friday"]}
    return rv._build_front_system_prompt({}, contract, "Ternary Bonsai 1.7B"), rv


def test_the_front_calls_first_and_never_scripts_an_announcement(front_prompt):
    p, rv = front_prompt
    assert rv.VOICE_FRONT_TOOL_RULE.strip() in p
    assert "END the sentence before invoking" not in p
    assert "it's up on screen" not in p
    assert "say a short line such as 'let me dig into that', then call" not in p


def test_the_cloud_voice_keeps_its_choreography():
    import inspect
    import agent_friday.routes.voice as rv
    src = inspect.getsource(rv)
    assert "+ VOICE_TOOL_CHOREOGRAPHY" in src.split("def _build_front_system_prompt")[0], \
        "the brain's local voice prompt still carries it"
    live = src[src.index("You are having a LIVE VOICE conversation"):]
    assert "+ VOICE_TOOL_CHOREOGRAPHY" in live[:2000], "Gemini Live still carries it"


class _Resp:
    def __init__(self, chunks):
        self._lines = ["data: " + json.dumps(c) for c in chunks] + ["data: [DONE]"]
        self.encoding = "utf-8"

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        pass


def test_a_truncated_call_goes_back_renderable_and_is_not_run():
    from agent_friday.services import voice_front as vf
    contract = {"tools": [{"type": "function", "function": {
        "name": "search_files", "parameters": {"type": "object",
                                               "properties": {"query": {"type": "string"}}}}}]}
    rounds = [
        [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "type": "function",
                                                  "function": {"name": "search_files", "arguments": "{"}}]}}]},
         {"choices": [{"delta": {}, "finish_reason": "length"}]}],
        [{"choices": [{"delta": {"content": "I couldn't search just then."}}]},
         {"choices": [{"delta": {}, "finish_reason": "stop"}]}],
    ]
    sent, ran = [], []
    seat = vf.FrontSeat(8199)
    seat.model = "ternary-bonsai:1.7b"
    seat._post = lambda body, stream: (sent.append(json.loads(json.dumps(body))),
                                       _Resp(rounds[len(sent) - 1]))[1]
    reply = seat.run_turn("sys", [{"role": "user", "content": "find budget"}], contract,
                          run_tool=lambda n, a: ran.append(n) or "x")
    assert reply == "I couldn't search just then."
    assert ran == [], "a malformed call is never run"
    assistant = [m for m in sent[1]["messages"] if m.get("tool_calls")][0]
    args = assistant["tool_calls"][0]["function"]["arguments"]
    assert isinstance(json.loads(args), dict), "the history the server re-renders must be JSON"
    tool_msg = sent[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "not valid JSON" in tool_msg["content"]


def test_a_well_formed_call_goes_back_unchanged():
    from agent_friday.services import voice_front as vf
    call = {"id": "a", "type": "function",
            "function": {"name": "check_email", "arguments": '{"urgent_only": true}'}}
    assert vf._renderable(call) is call
