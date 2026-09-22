"""FIFO promotion off the busy local seat actually starts the next task.

Found 2026-09-22 while wiring the cloud-spill question, by driving the REAL
supervisor with the REAL thread factory instead of the injected fake the
existing seat tests use:

    probe-1: admitted as running
    probe-2: admitted as queued-for-seat
    -- first task ends; the queued one should now be promoted --
    PROMOTION RAISED: TypeError unhashable type: 'dict'

``SeatSupervisor._start`` calls ``thread_factory(record)``. ``agent.
_start_pending_task_thread`` took a task *id* and did
``_PENDING_TASK_THREADS.pop(task_or_id, None)``. A dict is unhashable, and
``pop`` only swallows that on an EMPTY dict — the dict is non-empty exactly
when a task is parked waiting for a seat, which is the only situation
promotion ever happens in. So every promotion raised, the queued task never
started, and the traceback surfaced in the finishing worker's ``finally``
block, nowhere near the task it stranded.

The seat tests that already existed all inject their own thread factory, so
none of them could ever see this: they tested the supervisor's contract, and
the defect was in the other side of it. This file tests the pair.
"""

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import seat_supervisor as ss


class _Thread:
    def __init__(self, tid):
        self.tid = tid
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return False


@pytest.fixture
def seat(monkeypatch):
    """A supervisor wired to the real factory, with no cloud-spill side trip."""
    monkeypatch.setattr(ag, "_PENDING_TASK_THREADS", {})
    from agent_friday.services import cloud_spill as cs
    monkeypatch.setattr(cs, "withdraw", lambda *a, **k: False)
    return ss.SeatSupervisor(thread_factory=ag._start_pending_task_thread)


def _submit(sup, tid, threads, local=True):
    rec = {"id": tid, "task_id": tid, "name": tid, "status": "queued",
           "seat": ("local/bonsai2:27b" if local else "cloud/claude-sonnet-5"),
           "seat_is_local": local, "created_at": 0.0, "prompt": "p"}
    th = _Thread(tid)
    threads[tid] = th
    status = sup.wire_spawn(rec)
    if status == ss.QUEUED_STATUS:
        ag._PENDING_TASK_THREADS[tid] = th
    else:
        th.start()          # what _spawn_task does on the running path
    return status


def test_the_queued_task_starts_when_the_seat_frees(seat):
    """The whole point of a queue. This raised TypeError before the fix."""
    threads = {}
    assert _submit(seat, "a", threads) == "running"
    assert _submit(seat, "b", threads) == ss.QUEUED_STATUS
    assert threads["b"].started is False

    promoted = seat.on_task_end("a")

    assert promoted == "b"
    assert threads["b"].started is True, "the queued task never started"


def test_promotion_does_not_raise_into_the_finishing_worker(seat):
    """The exception did not merely lose the queued task — it escaped through
    on_task_end, which is called from the finishing worker's finally block and
    from the cancel route."""
    threads = {}
    _submit(seat, "a", threads)
    _submit(seat, "b", threads)
    seat.on_task_end("a")           # must not raise


def test_the_factory_accepts_a_record_and_an_id(seat):
    """Both callers are real: the supervisor passes the record, and anything
    re-dispatching by hand has only the id."""
    th = _Thread("z")
    ag._PENDING_TASK_THREADS["z"] = th
    assert ag._start_pending_task_thread({"id": "z"}) is True
    assert th.started is True

    th2 = _Thread("y")
    ag._PENDING_TASK_THREADS["y"] = th2
    assert ag._start_pending_task_thread("y") is True
    assert th2.started is True


def test_an_unknown_task_is_a_false_not_a_crash(seat):
    ag._PENDING_TASK_THREADS["something"] = _Thread("something")
    assert ag._start_pending_task_thread("nobody") is False
    assert ag._start_pending_task_thread({"id": None}) is False
    assert ag._start_pending_task_thread({}) is False


def test_a_third_task_waits_behind_the_second(seat):
    """FIFO, and one at a time: freeing the seat promotes exactly one."""
    threads = {}
    _submit(seat, "a", threads)
    _submit(seat, "b", threads)
    _submit(seat, "c", threads)
    seat.on_task_end("a")
    assert threads["b"].started is True
    assert threads["c"].started is False, "two tasks took the single local seat"
    seat.on_task_end("b")
    assert threads["c"].started is True


def test_a_cloud_task_never_queues_behind_the_local_seat(seat):
    """Only the local card is single-slot. A cloud task has no reason to wait,
    and making it wait is what would create the pressure to spend."""
    threads = {}
    _submit(seat, "a", threads)
    assert _submit(seat, "cloudy", threads, local=False) == "running"
    assert threads["cloudy"].started is True


def test_promotion_withdraws_a_pending_cloud_spill_card(monkeypatch):
    """A "shall I pay to skip this wait?" card outliving the wait would spend
    money on work that is already running."""
    monkeypatch.setattr(ag, "_PENDING_TASK_THREADS", {})
    withdrawn = []
    from agent_friday.services import cloud_spill as cs
    monkeypatch.setattr(cs, "withdraw",
                        lambda tid, reason="": withdrawn.append(tid) or True)
    sup = ss.SeatSupervisor(thread_factory=ag._start_pending_task_thread)
    threads = {}
    _submit(sup, "a", threads)
    _submit(sup, "b", threads)
    sup.on_task_end("a")
    assert withdrawn == ["b"]
