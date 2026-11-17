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


class FakeDesktopTurn:
    """A desktop turn that hands over frames one by one (what _DesktopTurn.frames does)."""

    def __init__(self, frames):
        self.frames_to_send = frames

    def frames(self, idle_s=None):
        yield from self.frames_to_send


def test_a_phone_that_drops_the_connection_mid_turn_is_logged(caplog):
    """The likeliest way a long turn loses its reply: the next write raises GeneratorExit and nothing was logged."""
    import logging
    stream = owner_turn._stream(FakeDesktopTurn([*TOOLS, DONE]))
    for _ in range(4):
        next(stream)                      # message_start, content_block_start, the first pings
    with caplog.at_level(logging.INFO, logger=owner_turn.__name__):
        stream.close()                    # the phone went away
    assert any("ended=disconnected" in record.getMessage() for record in caplog.records)


def test_every_finished_turn_writes_one_line_saying_how_it_ended(client, wire, caplog):
    import logging
    wire([{"started": True}, *TOOLS, DONE])
    with caplog.at_level(logging.INFO, logger=owner_turn.__name__):
        ask(client, phone()).get_data()
    lines = [r.getMessage() for r in caplog.records if "owner turn finished" in r.getMessage()]
    assert len(lines) == 1 and "ended=done" in lines[0] and "tools=4" in lines[0]


def test_a_voice_answer_that_is_only_code_is_still_sent(client, wire):
    wire([{"done": True, "payload": {"response": "```\nprint('hi')\n```", "model": "m", "seat": "local"}}])
    assert spoken(ask(client, phone(), voice=True)).strip() != ""
