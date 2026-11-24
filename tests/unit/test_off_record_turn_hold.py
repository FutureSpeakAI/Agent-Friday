"""One turn can be held off the record, and every store that asks `off_record` honours it.

A turn that carries outside text from the owner's phone (services/phone_outside_text) is kept off the record while
it runs. That must be the ONE off-record mechanism: a parallel flag that only the chat rows read left the
reasoning trace and every other store that asks ``off_record.skip()`` writing for such a turn (critic, #1060).
"""
import threading

from agent_friday.services import off_record, reasoning_trace

SETTINGS = {"off_record": False}


def test_by_default_nothing_is_held():
    assert off_record.turn_held() is False
    assert off_record.active(SETTINGS) is False and off_record.skip("x", SETTINGS) is False
    assert off_record.excludes_learning(SETTINGS) is False


def test_a_held_turn_is_off_the_record_for_every_question_a_store_can_ask():
    token = off_record.hold_turn()
    try:
        assert off_record.turn_held() is True
        assert off_record.active(SETTINGS) is True                 # storage: nothing is written
        assert off_record.skip("reasoning_traces", SETTINGS) is True      # the store is told, and counted
        assert off_record.excludes_learning(SETTINGS) is True      # learning: nothing is inferred
    finally:
        off_record.release_turn(token)
    assert off_record.active(SETTINGS) is False and off_record.excludes_learning(SETTINGS) is False


def test_the_reasoning_trace_writes_nothing_for_a_held_turn():
    assert reasoning_trace._off_record() is False
    token = off_record.hold_turn()
    try:
        assert reasoning_trace._off_record() is True
    finally:
        off_record.release_turn(token)


def test_the_hold_belongs_to_the_turn_not_to_other_threads():
    seen = []
    token = off_record.hold_turn()
    try:
        worker = threading.Thread(target=lambda: seen.append(off_record.turn_held()))
        worker.start()
        worker.join()
    finally:
        off_record.release_turn(token)
    assert seen == [False]                                        # a thread the turn did not start is not held


def test_a_skipped_write_is_counted_without_content():
    before = off_record.SKIPPED.get("a_store", 0)
    token = off_record.hold_turn()
    try:
        off_record.skip("a_store", SETTINGS)
    finally:
        off_record.release_turn(token)
    assert off_record.SKIPPED.get("a_store", 0) == before + 1
