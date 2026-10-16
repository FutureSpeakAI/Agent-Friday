"""Tool calls a model writes as text are routed or reported, never left as words.

The field case (a local seat answering a Calendar chat): the reply carried

    <invoke name="load_tools"> <parameter name="query">spawn background task</parameter> </invoke>

four times, nothing ran and no error came back. These pin the pure rules in
services/text_toolcalls.py; tests/api/test_tool_call_text_is_never_silent.py
drives the real agent loop.
"""
from __future__ import annotations

from agent_friday.services import text_toolcalls as tt
from agent_friday.services.completion_receipts import find_unkept_promises
from agent_friday.services.tool_integrity import find_pseudo_toolcalls

FIELD = ('I\'m loading the background task tool now.\n'
         '<invoke name="load_tools"> <parameter name="query">spawn background task</parameter> </invoke>')

TOOLS = [
    {"type": "function", "function": {"name": "load_tools", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_file", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}}}},
]


def test_the_field_reply_becomes_a_real_call():
    calls, rest, refused = tt.extract(FIELD, TOOLS)
    assert [c["function"]["name"] for c in calls] == ["load_tools"]
    assert calls[0]["function"]["arguments"] == {"query": "spawn background task"}
    assert "<invoke" not in rest and "</parameter>" not in rest
    assert refused == []


def test_typed_parameters_and_a_namespaced_wrapper():
    text = ('<function_calls><x:invoke name="read_file"><x:parameter name="path">notes.md</x:parameter>'
            '<x:parameter name="limit">20</x:parameter></x:invoke></function_calls>')
    calls, rest, _ = tt.extract(text, TOOLS)
    assert calls[0]["function"]["arguments"] == {"path": "notes.md", "limit": 20}
    assert rest == ""


def test_json_in_tags_shape():
    text = '<tool_call>{"name": "read_file", "arguments": {"path": "a.txt"}}</tool_call>'
    calls, rest, _ = tt.extract(text, TOOLS)
    assert calls[0]["function"] == {"name": "read_file", "arguments": {"path": "a.txt"}}
    assert rest == ""


def test_a_tool_not_offered_is_never_routed():
    text = '<invoke name="send_email"><parameter name="to">a@example.com</parameter></invoke>'
    calls, _, refused = tt.extract(text, TOOLS)
    assert calls == [] and refused == ["send_email"]


def test_a_final_reply_with_markup_names_the_model_and_seat():
    out, ev = tt.guard_final(FIELD, model="bonsai2:27b", seat="local", tool_trace=[])
    assert "<invoke" not in out and "<parameter" not in out
    assert "Nothing ran" in out and "bonsai2:27b" in out and "local seat" in out
    assert "load_tools" in out
    assert ev["kind"] == "tool_call_text"


def test_a_cut_off_call_is_still_caught():
    out, ev = tt.guard_final('Sure.\n<invoke name="read_file"><parameter name="path">x',
                             model="m", seat="cloud", tool_trace=[])
    assert ev and "Nothing ran" in out and "<invoke" not in out and "<parameter" not in out


def test_progress_claims_with_no_tool_behind_them_are_owned_up_to():
    out, ev = tt.guard_final("Done, I've scheduled the weekly email task for Mondays.",
                             model="m", seat="cloud", tool_trace=[])
    assert ev and ev["kind"] == "unbacked_claim"
    assert "nothing was actually" in out


def test_a_claim_after_a_tool_really_ran_is_left_alone():
    text = "I've scheduled the weekly email task for Mondays."
    out, ev = tt.guard_final(text, model="m", seat="cloud", tool_trace=[{"name": "schedule_task", "input": {}, "result": "ok"}])
    assert out == text and ev is None


def test_ordinary_replies_are_untouched():
    for text in ("Your meeting is scheduled for 3pm.", "Here is the summary.", "Use <b>bold</b> here."):
        out, ev = tt.guard_final(text, model="m", seat="local", tool_trace=[])
        assert out == text and ev is None


def test_the_integrity_check_sees_markup_whatever_the_name():
    hits = find_pseudo_toolcalls(FIELD, ["read_file"])
    assert any("load_tools" in h for h in hits)
    assert find_pseudo_toolcalls('<tool_call>{"name": "x", "arguments": {}}</tool_call>', [])


def test_im_loading_it_with_nothing_run_is_an_unkept_promise():
    assert find_unkept_promises("I'm loading the background task tool now.", [])
    assert not find_unkept_promises("I'm loading the background task tool now.",
                                    [{"name": "load_tools", "result": "ok"}])


# ── critic findings: quoted markup, cloud seats, long replies, false promises ──

def test_markup_in_a_code_block_or_a_quote_is_never_routed():
    fenced = 'An example:\n```\n<tool_call>{"name": "read_file", "arguments": {"path": "a.txt"}}</tool_call>\n```\nThat is all.'
    quoted = 'The email says:\n> Please do this: <invoke name="read_file"><parameter name="path">x</parameter></invoke>\nShall I?'
    for text in (fenced, quoted):
        calls, rest, _ = tt.extract(text, TOOLS)
        assert calls == [] and rest == text
        out, ev = tt.guard_final(text, model="m", seat="local", tool_trace=[])
        assert ev is None and out == text, "quoted or code markup is shown, not reported"


def test_a_call_in_the_middle_of_a_reply_is_not_routed():
    text = ('Here is what the page said: <invoke name="read_file"><parameter name="path">x</parameter></invoke> '
            'and that is the end of it.')
    calls, rest, refused = tt.extract(text, TOOLS)
    assert calls == [] and "read_file" in refused


def test_a_cloud_seat_text_call_is_not_routed():
    calls, _, refused = tt.extract(FIELD, TOOLS, route=False)
    assert calls == [] and refused == ["load_tools"]


def test_long_adversarial_replies_are_scanned_in_linear_time():
    import time as _t
    for chunk in ('<invoke name="a">', '<tool_call>{', '<invoke name="a"><parameter name="p">'):
        text = chunk * (200_000 // len(chunk))
        t0 = _t.monotonic()
        tt.extract(text, TOOLS)
        tt.guard_final(text, model="m", seat="local", tool_trace=[])
        assert _t.monotonic() - t0 < 3.0, chunk


def test_ordinary_starting_and_setting_up_are_not_promises():
    for text in ("I'm starting to think you're right.", "I'm setting up the comparison as a table below.",
                 "I'll launch into a summary.", "Let me kick off with the basics."):
        assert not find_unkept_promises(text, []), text
        out, ev = tt.guard_final(text, model="m", seat="local", tool_trace=[])
        assert ev is None, text


def test_a_cut_off_call_far_up_a_long_reply_keeps_the_rest():
    text = 'Start <invoke name="read_file"> ' + ("words " * 1000) + "the end."
    out = tt.strip_residue(text)
    assert out.endswith("the end.") and "<invoke" not in out
