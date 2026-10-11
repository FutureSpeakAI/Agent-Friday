"""A spoken turn on the local voice front is admitted as the ``voice`` class
(the class that had no caller). Written against the queue alone: fake front,
no model."""
import threading
import time

import pytest
from turn_queue_helpers import Runner, fast, wait_until

from agent_friday.services import model_router, voice_front
from agent_friday.services import turn_queue as tq


@pytest.fixture
def front(monkeypatch):
    fast(monkeypatch)

    class Resp:
        def raise_for_status(self):
            pass
    seat = voice_front.FrontSeat()
    seat.model = voice_front.DEFAULT_FRONT_MODEL
    monkeypatch.setattr(seat, "_post", lambda body, stream: Resp())
    seen = []

    def consume(resp, on_delta=None):
        seen.append([(t.cls, t.state) for t in tq.queue_for("voice-front").running_tickets()])
        return {"choices": [{"message": {"content": "Hi."}, "finish_reason": "stop"}], "timings": {}}
    monkeypatch.setattr(model_router, "_consume_sse_completion", consume)
    seat.seen = seen
    yield seat
    tq.reset_for_tests()


def _turn(seat, **kw):
    return seat.run_turn("You are Friday.", [{"role": "user", "content": "hello"}], {"tools": []}, **kw)


def test_a_spoken_turn_holds_a_voice_ticket_while_the_front_answers(front):
    assert _turn(front) == "Hi."
    assert front.seen == [[("voice", "running")]]
    assert tq.queue_for("voice-front").running_tickets() == [], "and lets go when it is done"


# bg-w is time-bound background work (a schedule that is due): a chat turn ending restarts
# the idle clock, and work that can wait would then wait for the owner to be away.
def test_the_brains_queue_does_not_make_a_spoken_turn_wait(front):
    """The front is its own process: the brain's running job and its queue of
    chats and background work are not in its way."""
    tq.queue_for("default").background_started("bg-run", label="knowledge graph")
    log = []
    waiting = [Runner("default", "chat", "chat-w", log), Runner("default", "background", "bg-w", log, deferrable=False)]
    for r in waiting:
        assert wait_until(lambda r=r: r.waiting())
    t0 = time.monotonic()
    assert _turn(front) == "Hi."
    assert time.monotonic() - t0 < 1.0
    tq.queue_for("default").background_finished("bg-run")
    for r in waiting:
        assert wait_until(lambda r=r: any(x[0] == r.turn_id for x in log))
        r.finish()


def test_a_spoken_turn_on_the_brain_goes_next_ahead_of_everything_that_arrived_first(front):
    tq.queue_for("default").background_started("bg-run", label="knowledge graph")
    log = []
    waiting = [Runner("default", "chat", "chat-w", log), Runner("default", "user_job", "job-w", log),
               Runner("default", "background", "bg-w", log, deferrable=False)]
    for r in waiting:
        assert wait_until(lambda r=r: r.waiting())
    v = Runner("default", "voice", "voice-w", log)
    assert wait_until(lambda: v.waiting())
    tq.queue_for("default").background_finished("bg-run")
    assert wait_until(lambda: ("voice-w", "admitted") in log), log
    v.finish()
    for r in waiting:
        assert wait_until(lambda r=r: any(x[0] == r.turn_id for x in log))
        r.finish()
    assert [x[0] for x in log] == ["voice-w", "chat-w", "job-w", "bg-w"]


def test_a_spoken_turn_that_waits_says_the_one_line_once(front, monkeypatch):
    monkeypatch.setattr(tq, "VOICE_WAIT_S", 0.05)
    spoken = []
    tq.queue_for("voice-front").background_started("front-busy", label="a prefill")
    box = []
    th = threading.Thread(target=lambda: box.append(_turn(front, on_delta=spoken.append)), daemon=True)
    th.start()
    assert wait_until(lambda: len(spoken) == 1), spoken
    time.sleep(0.15)                          # it waits on: still only one line
    assert spoken == [tq.VOICE_WAIT_LINE + " "]
    tq.queue_for("voice-front").background_finished("front-busy")
    th.join(5)
    assert box == ["Hi."]
    assert spoken == [tq.VOICE_WAIT_LINE + " "]
