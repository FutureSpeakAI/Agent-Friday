"""The idle gate that holds the podcast worker and every idle job measures the
time since the owner was last HERE, and a turn in the local queue counts as
being here. Before, "idle" was the time since the process started: nothing
marked activity, and the gate never looked at the queue."""
import time

import pytest
from turn_queue_helpers import fast

from agent_friday.services import notification_policy as pol
from agent_friday.services import scheduler
from agent_friday.services import turn_queue as tq
from agent_friday.services import work_queue as wq

OPEN_ALL_DAY = {"from_hour": 0, "to_hour": 24, "idle_after_s": 60}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    fast(monkeypatch)
    if hasattr(tq, "HELD"):
        tq.HELD.set(())
        tq.CURRENT.set(None)
    monkeypatch.setattr(scheduler, "_idle_settings",
                        lambda: {"enabled": True, "after_s": 60.0, "from_hour": 0, "to_hour": 24})
    from agent_friday.services import stand_down
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: False)
    wq._LAST_ACTIVITY[0] = time.time() - 3600          # an hour of quiet
    yield
    tq.reset_for_tests()
    if hasattr(tq, "HELD"):
        tq.HELD.set(())
        tq.CURRENT.set(None)


def test_an_hour_of_quiet_is_idle():
    assert scheduler.idle_work_blocked_reason(spec=OPEN_ALL_DAY) == ""


def test_a_chat_turn_running_in_the_local_queue_is_not_idle_whatever_the_clock_says():
    turn = tq.acquire("default", "chat", "chat-1")
    wq._LAST_ACTIVITY[0] = time.time() - 3600           # the clock still says an hour
    why = scheduler.idle_work_blocked_reason(spec=OPEN_ALL_DAY)
    assert why and "answering you" in why
    turn.release()


def test_a_voice_turn_or_chat_send_restarts_the_idle_clock():
    pol.note_owner_turn()                               # what a chat send and a voice turn call
    why = scheduler.idle_work_blocked_reason(spec=OPEN_ALL_DAY)
    assert why and "active" in why
    assert wq.idle_seconds() < 5


def test_the_pages_input_events_restart_it_too():
    from agent_friday.services import local_scheduler as ls
    ls.mark_activity("ui")
    assert "active" in scheduler.idle_work_blocked_reason(spec=OPEN_ALL_DAY)
    assert wq.idle_seconds() < 5


def test_the_activity_route_marks_the_scheduler():
    """The page's throttled POST lands here; the route is the only caller of the idle clock."""
    from flask import Flask
    from agent_friday.routes import work_plan
    from agent_friday.services import local_scheduler as ls
    handler = getattr(work_plan.touch_activity, "__wrapped__", work_plan.touch_activity)
    with Flask("t").test_request_context("/api/work/activity", method="POST"):
        body = handler().get_json()
    assert body["ok"] is True and body["idle_s"] < 5
    assert "ui" in ls.last_activity()


def test_a_chat_turn_in_the_core_registry_is_not_idle_either(monkeypatch):
    """A turn that never admitted at the queue (the chat registry) still counts."""
    from agent_friday.services import background_gate as bg
    monkeypatch.setattr(bg, "interactive_kinds", lambda: ["chat"])
    why = scheduler.idle_work_blocked_reason(spec=OPEN_ALL_DAY)
    assert why and "answering you" in why
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [bg.KIND_VOICE_CALL])
    assert scheduler.idle_work_blocked_reason(spec=OPEN_ALL_DAY) == "",         "an open call alone is the idle clock's business, not a turn"
