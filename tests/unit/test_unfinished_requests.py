"""services/unfinished_requests: explicit note requests, detected conservatively.

The chat route is driven end to end in tests/api/test_failed_turn_keeps_the_message.py.
Here: which words open a request, which close it, and which tool results count
as a write.
"""
import pytest

from agent_friday.services import unfinished_requests as ur


@pytest.mark.parametrize("text", [
    "Make a note of this in my wiki.",
    "make note of this in my wiki",
    "The gate code changed. Please add this to my wiki",
    "save that to the wiki",
    "Put it in my memory, please.",
    "note this in my wiki",
    "Remember this.",
    "Please remember this for me",
])
def test_explicit_requests_are_detected(text):
    assert ur.is_note_request(text)


@pytest.mark.parametrize("text", [
    "",
    None,
    "What's in my wiki about the garden?",
    "Do you remember this?",
    "I remember that day at the lake.",
    "Search the wiki for the boiler manual",
    "Remember when we went to Riverside Park?",
])
def test_ordinary_messages_open_nothing(text):
    assert not ur.is_note_request(text)


@pytest.mark.parametrize("text", ["Never mind.", "forget it", "Don't save that", "drop it"])
def test_the_owner_can_withdraw(text):
    assert ur.is_drop_request(text)


def _u(text, i):
    return {"id": "u%d" % i, "role": "user", "text": text, "meta": {}}


def _f(i, saved=False):
    return {"id": "f%d" % i, "role": "friday", "text": "Okay.", "meta": {"note_saved": saved}}


def test_a_request_stays_open_until_a_write():
    ask = "The bins go out on Tuesday. Make a note of this in my wiki."
    rows = [_u(ask, 1), _u("hello?", 2), _f(2)]
    assert ur.open_requests(rows) == [ask]
    assert ur.open_requests(rows + [_u("thanks", 3), _f(3, saved=True)]) == []


def test_the_running_turn_is_left_out():
    ask = "Remember this."
    assert ur.open_requests([_u(ask, 1)], exclude_id="u1") == []


def test_a_drop_closes_it():
    rows = [_u("Add this to my wiki", 1), _f(1), _u("Never mind.", 2)]
    assert ur.open_requests(rows) == []


def test_the_note_quotes_the_owner():
    note = ur.note_for(["Add this to my wiki"])
    assert 'Unfinished request from the owner: "Add this to my wiki"' in note
    assert "the wiki note was not saved yet" in note
    assert ur.note_for([]) == ""


def test_only_a_successful_write_counts():
    ok = [{"name": "propose_wiki_update", "result": "Wiki update proposed (id=p1)"}]
    failed = [{"name": "propose_wiki_update",
               "result": "propose_wiki_update error: 'file' and 'new_value' are required."}]
    denied = [{"name": "correct_wiki", "result": "[CREW DENY] no tool authority"}]
    read = [{"name": "read_wiki", "result": "page text"}]
    assert ur.wrote_note(ok)
    assert not ur.wrote_note(failed)
    assert not ur.wrote_note(denied)
    assert not ur.wrote_note(read)
    assert not ur.wrote_note(None)
