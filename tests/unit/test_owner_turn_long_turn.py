"""A long remote turn always ends with the answer or a plain reason, never with silence (P0, 2026-11-17).

Stephen asked "start my day" from his paired phone: the PC ran the turn (visible on the desktop, four minutes of tool
rounds) and the phone never got the reply. The gateway logged nothing at the end of the turn, so a dropped final message,
a closed stream and a timeout could not be told apart. Guarded here:

* a slow turn with several tool rounds sends progress `ping`s the whole time and then the FINAL answer, last;
* a desktop stream that closes without a final answer is an `error` event saying so, never an empty "success";
* an answer that is only a reasoning scratchpad is the same error, not an empty message;
* a turn longer than MAX_TURN_S is ended with an `error` event that says the PC is still working, not endless pings;
* if the stream closes early but words arrived, those words are delivered.
"""
import pytest

from agent_friday.tv_gateway import owner_turn
from tests.unit.test_owner_turn_stream import (DONE, _clean, ask, client, events, phone, spoken,  # noqa: F401
                                               wire)

TOOLS = [{"tool": {"name": name}} for name in ("Searching the wiki", "Reading your calendar", "Searching the web",
                                               "Reading your email")]


def test_a_slow_multi_tool_turn_sends_progress_then_the_final_answer_last(client, wire, monkeypatch):
    monkeypatch.setattr(owner_turn, "STATUS_EVERY_S", 0.05)
    wire([{"started": True}, *TOOLS, DONE], delay=0.3)
    ev = events(ask(client, phone()))
    working = [e for e in ev if e["type"] == "ping" and e["friday"]["status"] == "working"]
    assert len(working) == len(TOOLS)
    answer_at = next(i for i, e in enumerate(ev) if e["type"] == "content_block_delta")
    assert all(ev.index(e) < answer_at for e in ev if e["type"] == "ping")
    assert [e["type"] for e in ev[-3:]] == ["content_block_stop", "message_delta", "message_stop"]
    assert ev[answer_at]["delta"]["text"] == "Jere Jay is your father-in-law."
    elapsed = [e["friday"]["elapsed_s"] for e in ev if e["type"] == "ping"]
    assert elapsed == sorted(elapsed)


def test_a_desktop_stream_that_closes_without_an_answer_is_an_error_not_an_empty_success(client, wire):
    wire([{"started": True}, *TOOLS])
    ev = events(ask(client, phone()))
    assert ev[-1]["type"] == "error" and "without an answer" in ev[-1]["error"]["message"]
    assert not any(e["type"] == "content_block_delta" for e in ev) and not any(e["type"] == "message_stop" for e in ev)


def test_an_answer_that_is_only_a_scratchpad_is_an_error_not_an_empty_message(client, wire):
    wire([*TOOLS, {"done": True, "payload": {"response": "<think>so then I should look at the calendar..."}}])
    ev = events(ask(client, phone()))
    assert ev[-1]["type"] == "error" and not any(e["type"] == "content_block_delta" for e in ev)


def test_a_turn_past_the_longest_wait_is_ended_with_a_plain_reason(client, wire, monkeypatch):
    monkeypatch.setattr(owner_turn, "STATUS_EVERY_S", 0.05)
    monkeypatch.setattr(owner_turn, "MAX_TURN_S", 0.5)
    wire([{"started": True}, *TOOLS, *TOOLS, DONE], delay=0.3)
    ev = events(ask(client, phone()))
    assert ev[-1]["type"] == "error" and "still working" in ev[-1]["error"]["message"]
    assert any(e["type"] == "ping" for e in ev) and not any(e["type"] == "content_block_delta" for e in ev)


def test_words_that_arrived_before_an_early_close_are_delivered(client, wire):
    wire([{"started": True}, {"delta": "Good morning. Two meetings today."}])
    assert spoken(ask(client, phone())) == "Good morning. Two meetings today."
