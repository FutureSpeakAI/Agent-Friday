"""The scheduler's admission: work inside an admitted turn does not wait
again, the class of a call comes from the work it serves, a cancelled wait
never reaches the model. (The classes' order, capacity from -np, the voice
front and the transports are in their own files, written against the queue
alone.) Fake seats, no model."""
import threading

import pytest
from turn_queue_helpers import fast, wait_until

from agent_friday.services import local_scheduler as ls
from agent_friday.services import turn_queue as tq


@pytest.fixture(autouse=True)
def scheduler(monkeypatch, tmp_path):
    fast(monkeypatch)
    ls.reset_for_tests()
    tq.HELD.set(())                                 # no admission leaks in from another test
    tq.CURRENT.set(None)
    monkeypatch.setattr(ls, "metrics_path", lambda: tmp_path / "metrics.jsonl")
    ls.set_residency_probe(lambda m: True)          # no swaps in these tests: order only
    ls.set_ram_probe(lambda: 64.0)
    yield
    ls.reset_for_tests()
    tq.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)


def test_work_inside_an_admitted_turn_does_not_wait_for_its_own_slot():
    """A chat turn holds the seat; the model call it makes there is part of it."""
    turn = tq.acquire("default", "chat", "turn-1")
    got = ls.admit("model_router.ollama", model=None)
    assert got.nested and got.cls == "chat"
    assert [t.id for t in tq.queue_for("default").running_tickets()] == ["turn-1"], "no second ticket"
    got.release()
    turn.release()


def test_the_same_call_on_another_thread_does_wait():
    """Nesting is the turn's own context, not a free pass for everyone."""
    turn = tq.acquire("default", "chat", "turn-1")
    outcome = []

    def other():
        g = ls.admit("test.other", model=None, cls="user_job", timeout_s=0.3)
        outcome.append(g.how)
        g.release()
    th = threading.Thread(target=other, daemon=True)
    th.start()
    th.join(5)
    assert outcome == ["failopen"], "it queued behind the turn and gave up the wait, it did not walk in"
    turn.release()


def test_work_on_another_seat_inside_a_turn_waits_there_at_the_turns_class():
    turn = tq.acquire("default", "voice", "turn-1")
    g = ls.admit("test.side", model="sidekick")
    assert g.how == "admitted" and g.cls == "voice"
    running = tq.queue_for("sidekick").running_tickets()
    assert [t.cls for t in running] == ["voice"]
    g.release()
    turn.release()


def test_a_background_task_holding_its_seat_makes_calls_without_waiting():
    sq_slot = tq.queue_for("local/brain").background_started("task-1", label="a task")
    with tq.holding("local/brain", "background"):
        g = ls.admit("model_router.ollama", model="brain")
        assert g.nested
    tq.queue_for("local/brain").background_finished("task-1")
    assert sq_slot.cls == "background"


def test_the_class_comes_from_the_work_the_call_serves():
    assert ls.resolve_class(None, {"is_voice": True}) == "voice"
    assert ls.resolve_class(None, {"is_background_task": True}) == "background"
    assert ls.resolve_class("api") == "api"
    assert ls.resolve_class() == "background", "a call nobody is waiting on is background"
    from agent_friday import core
    core._TURN_LOCAL.turn_id = "turn-x"
    try:
        assert ls.resolve_class() == "chat", "a typed chat turn's own thread is chat"
        assert ls.resolve_class(None, {"is_voice": True}) == "voice"
    finally:
        core._TURN_LOCAL.turn_id = None
    with ls.work("a research commission", cls="user_job"):
        assert ls.resolve_class() == "user_job"
    from flask import Flask
    with Flask("t").test_request_context("/x"):
        assert ls.resolve_class() == "user_job", "a web request is a person waiting"


def test_a_cancelled_wait_never_reaches_the_model():
    turn = tq.acquire("default", "chat", "turn-1")
    seen = []

    def job():
        try:
            with ls.work("podcast", cls="background", quiet=False):
                with ls.admit("test.job", model=None):
                    seen.append("reached the model")
        except ls.LocalWorkCancelled:
            seen.append("cancelled")
    th = threading.Thread(target=job, daemon=True)
    th.start()
    assert wait_until(lambda: any(t.kind == "test.job" for t in tq.all_waiting()))
    waiting = [t for t in tq.all_waiting() if t.kind == "test.job"][0]
    assert ls.cancel(waiting.id)["ok"]
    th.join(5)
    assert seen == ["cancelled"]
    turn.release()


def test_a_cancelled_wait_is_not_an_exception_the_router_can_swallow():
    """The router's fallback ladders catch Exception and would send a cancelled
    local job to the cloud."""
    assert issubclass(ls.LocalWorkCancelled, BaseException)
    assert not issubclass(ls.LocalWorkCancelled, Exception)
