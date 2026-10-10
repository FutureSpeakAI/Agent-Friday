"""The calendar cross-reference behind ``related_event`` reads seven days of
calendar. It is built once per TTL, not on every /api/messages request, and a
slow calendar cannot hold the response."""
import threading
import time

from agent_friday.routes import messages as rm


def _reset():
    with rm._related_events_lock:
        rm._related_events_cache.update(at=0.0, map={}, building=None)


def test_the_cross_reference_is_not_rebuilt_within_its_ttl(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(rm, "_events_for_day", lambda d: calls.append(d) or [
        {"id": "e1", "title": "Sync", "start_time": "10:00", "attendees": ["Ada@Example.test"]}])
    first = rm._related_events()
    after_first = len(calls)
    assert after_first == 7 and first["ada@example.test"][0]["id"] == "e1"
    for _ in range(5):
        assert rm._related_events() is first
    assert len(calls) == after_first


def test_a_slow_calendar_does_not_hold_the_response(monkeypatch):
    _reset()
    release = threading.Event()
    monkeypatch.setattr(rm, "_RELATED_EVENTS_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(rm, "_events_for_day", lambda d: release.wait(5) and [])
    started = time.monotonic()
    out = rm._related_events()
    release.set()
    assert out == {} and time.monotonic() - started < 2.0
