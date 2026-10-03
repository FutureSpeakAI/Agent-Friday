"""A local seat reads tool results without formatting boilerplate, and with
every datum kept. A cloud seat reads them exactly as the tool returned them."""
from __future__ import annotations

import json

from agent_friday.services.tool_result_compact import compact

PRETTY = json.dumps({
    "status": "ok", "error": "", "note": None, "count": 0, "unread": False,
    "hits": [], "title": "Café — notes",
    "items": [{"id": "a", "url": "", "score": 1.5, "tags": None}],
}, indent=2)


def test_json_loses_indentation_nulls_and_empty_strings_and_keeps_the_data():
    out = compact(PRETTY)
    assert len(out) < len(PRETTY)
    assert json.loads(out) == {
        "status": "ok", "count": 0, "unread": False, "hits": [],
        "title": "Café — notes", "items": [{"id": "a", "score": 1.5}]}
    assert "\n" not in out and "\\u" not in out


def test_zero_false_and_an_empty_list_are_data_and_stay():
    out = json.loads(compact('{"n": 0, "ok": false, "hits": [], "e": ""}'))
    assert out == {"n": 0, "ok": False, "hits": []}


def test_plain_text_keeps_its_words_and_lines():
    text = "Line one   \nLine two\n\n\n\n\nLine three\t \n"
    assert compact(text) == "Line one\nLine two\n\nLine three"


def test_text_that_only_looks_like_json_is_treated_as_text():
    text = "[not json] see below   \n\n\n\nbody"
    assert compact(text) == "[not json] see below\n\nbody"


def test_non_strings_and_empty_results_pass_through():
    assert compact("") == ""
    assert compact(None) is None


def _drive(monkeypatch, *, provider, seat=None):
    from agent_friday.services import agent as ag
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: PRETTY)
    # Transcript compression is a separate stage with its own tests; held
    # still here so the message compared is the one this stage produced.
    from agent_friday.services import compaction
    monkeypatch.setattr(compaction, "compress_new_output", lambda m, s, **k: m)
    monkeypatch.setattr(compaction, "maybe_compact", lambda m, **k: m)
    sent = []

    def send_fn(convo, tools, **over):
        sent.append([dict(m) for m in convo])
        if len(sent) == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "x"})}}]},
                "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
        return {"choices": [{"message": {"content": "done."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    tools = [{"type": "function", "function": {
        "name": "read_file", "parameters": {"type": "object", "properties": {}}}}]
    kw = {"seat": seat} if seat else {}
    text, trace = ag._oai_agentic_loop([{"role": "user", "content": "go"}], tools, send_fn,
                                       provider=provider, model="m", session_ctx={}, **kw)
    tool_msgs = [m for m in sent[-1] if m.get("role") == "tool"]
    return text, trace, tool_msgs


def test_the_local_loop_sends_the_compact_result(monkeypatch):
    text, trace, tool_msgs = _drive(monkeypatch, provider="local")
    assert text == "done."
    assert tool_msgs[0]["content"] == compact(PRETTY)
    assert trace[0]["result"].startswith("{\n"), "the trace keeps the original"


def test_a_local_seat_on_the_openai_dialect_is_compacted_too(monkeypatch):
    _text, _trace, tool_msgs = _drive(monkeypatch, provider="openai", seat="local")
    assert tool_msgs[0]["content"] == compact(PRETTY)


def test_a_cloud_seat_gets_the_result_as_returned(monkeypatch):
    _text, _trace, tool_msgs = _drive(monkeypatch, provider="openai")
    assert tool_msgs[0]["content"] == PRETTY
