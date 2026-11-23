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


SMALL, BIG = 50_000, 200_000       # the big input is four times the small one
GROWTH_LIMIT = 8.0                 # linear is about 4x; a quadratic scan is about 16x
FLOOR_S = 0.02                     # below this a timing says nothing


def _best_of_three(run, clock):
    best = None
    for _ in range(3):
        t0 = clock()
        run()
        spent = clock() - t0
        best = spent if best is None else min(best, spent)
    return max(best, FLOOR_S)


def _grows_linearly(make_run, clock=None):
    """True when four times the input costs well under sixteen times the time. Compares the machine with itself,
    so a slow or busy runner cannot fail it; only a scan that grows faster than linear does."""
    import time as _t
    clock = clock or _t.monotonic
    return _best_of_three(make_run(BIG), clock) <= GROWTH_LIMIT * _best_of_three(make_run(SMALL), clock)


def test_long_adversarial_replies_are_scanned_in_linear_time():
    for chunk in ('<invoke name="a">', '<tool_call>{', '<invoke name="a"><parameter name="p">'):
        def make_run(n, chunk=chunk):
            text = chunk * (n // len(chunk))
            return lambda: (tt.extract(text, TOOLS), tt.guard_final(text, model="m", seat="local", tool_trace=[]))
        assert _grows_linearly(make_run), chunk


def _cost_counter():
    """A clock that reads the work done instead of the time passed, so the check can be tried on a known cost."""
    work = [0]
    return work, (lambda: float(work[0]))


def test_the_growth_check_still_catches_a_scan_that_is_quadratic():
    work, clock = _cost_counter()
    assert _grows_linearly(lambda n: (lambda: work.__setitem__(0, work[0] + n * n)), clock=clock) is False


def test_the_growth_check_passes_a_scan_that_is_linear():
    work, clock = _cost_counter()
    assert _grows_linearly(lambda n: (lambda: work.__setitem__(0, work[0] + n)), clock=clock) is True


def test_the_linear_time_check_does_not_depend_on_how_fast_the_machine_is(monkeypatch):
    """A busy shared runner is slow all over, not quadratic: the same scan, with every read of the clock four
    seconds after the last, must still be judged linear (F1 in HANDOFF-TASKS-INBOX-flaky-tests-2026-11-18)."""
    import time as _t
    ticks = iter(range(0, 10_000_000, 4))
    monkeypatch.setattr(_t, "monotonic", lambda: float(next(ticks)))
    test_long_adversarial_replies_are_scanned_in_linear_time()


def test_the_linear_time_check_does_not_depend_on_how_fast_the_machine_is(monkeypatch):
    """A busy shared runner is slow all over, not quadratic: the same scan, with every read of the clock four
    seconds after the last, must still be judged linear (F1 in HANDOFF-TASKS-INBOX-flaky-tests-2026-11-18)."""
    import time as _t
    ticks = iter(range(0, 10_000_000, 4))
    monkeypatch.setattr(_t, "monotonic", lambda: float(next(ticks)))
    test_long_adversarial_replies_are_scanned_in_linear_time()


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
