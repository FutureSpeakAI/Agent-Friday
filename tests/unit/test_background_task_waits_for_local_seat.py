"""A background task whose local-only route finds the local seat not serving waits for it instead of failing at once
(agent._wait_for_local_seat). The commonest recorded cause of a failed background task was exactly that: one look,
"no local model is serving", though the seat was back within minutes. Synthetic router and clock only."""
from __future__ import annotations

import pytest

from agent_friday.services import agent


class FakeRouter:
    """Says the seat is unavailable for the first ``down`` calls, then serves."""

    def __init__(self, down):
        self.down, self.calls = down, 0

    def route(self, messages, task_context=None):
        self.calls += 1
        if self.calls <= self.down:
            return {"local_unavailable": True}
        return {"provider": "local", "model": "seat-1"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _wait(router, clock, **kw):
    return agent._wait_for_local_seat(router, [], {}, {"task_id": ""}, sleep=clock.sleep, clock=clock, **kw)


def test_a_seat_that_comes_back_within_the_wait_lets_the_task_go_on():
    clock, router = Clock(), FakeRouter(down=3)
    route = _wait(router, clock, max_wait_s=600, poll_s=15)
    assert route == {"provider": "local", "model": "seat-1"} and router.calls == 4 and clock.now == 60


def test_a_seat_that_stays_away_ends_in_the_unavailable_verdict_after_the_wait_and_no_longer():
    clock, router = Clock(), FakeRouter(down=10 ** 6)
    route = _wait(router, clock, max_wait_s=120, poll_s=15)
    assert route.get("local_unavailable") is True and clock.now == 120 and router.calls == 8


def test_the_owner_stopping_the_task_ends_the_wait_at_once(monkeypatch):
    clock, router = Clock(), FakeRouter(down=10 ** 6)

    class Journal:
        def stop_requested(self, task_id):
            return True

    monkeypatch.setattr(agent, "_journal", lambda: Journal())
    route = agent._wait_for_local_seat(router, [], {}, {"task_id": "t1"}, max_wait_s=600, poll_s=15,
                                       sleep=clock.sleep, clock=clock)
    assert route.get("local_unavailable") is True and clock.now == 0 and router.calls == 0


def test_a_router_that_fails_while_waiting_gives_back_the_last_verdict_not_an_error():
    clock = Clock()

    class Broken:
        def route(self, *a, **k):
            raise RuntimeError("router down")

    assert _wait(Broken(), clock, max_wait_s=60, poll_s=15).get("local_unavailable") is True


def test_the_wait_is_noted_in_the_tasks_own_log(monkeypatch):
    lines = []
    monkeypatch.setattr(agent, "_task_log", lambda task_id, line: lines.append((task_id, line)))
    clock = Clock()
    agent._wait_for_local_seat(FakeRouter(down=1), [], {}, {"task_id": "t9"}, max_wait_s=600, poll_s=15,
                               sleep=clock.sleep, clock=clock)
    assert lines[0][0] == "t9" and "waiting for it" in lines[0][1] and "is back" in lines[-1][1]


@pytest.mark.parametrize("ctx_extra,expect_waits", [({"is_background_task": True}, True), ({}, False),
                                                      ({"is_background_task": True, "scheduled": True}, False)])
def test_only_a_background_task_waits_and_an_interactive_turn_or_a_scheduled_run_answers_at_once(
        monkeypatch, ctx_extra, expect_waits):
    """Through the agent turn itself: a local-only route that finds no seat waits for a background task and raises
    the offer-able LocalUnavailable at once for a person's turn and for a scheduled run (the scheduler retries those)."""
    from agent_friday.routing import model_router as routing
    from agent_friday.services import cloud_intent
    router = FakeRouter(down=2)
    monkeypatch.setattr(routing, "get_router", lambda cfg: router)
    waited = []
    monkeypatch.setattr(agent, "_wait_for_local_seat", lambda *a, **k: waited.append(1) or {"local_unavailable": True})
    monkeypatch.setattr(agent, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    ctx = dict({"authenticated": True}, **ctx_extra)
    with pytest.raises(cloud_intent.LocalUnavailable):
        agent._generate_agent_untraced([{"role": "user", "content": "hi"}], session_ctx=ctx)
    assert bool(waited) is expect_waits
