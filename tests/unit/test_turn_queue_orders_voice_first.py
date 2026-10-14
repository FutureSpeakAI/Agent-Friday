"""Who is next on a local seat: voice, then chat, then the local API, then
background; first in first out inside a class; a waiting voice turn goes
next and never interrupts the turn that is running."""
from turn_queue_helpers import Runner, fast, wait_until

from agent_friday.services import turn_queue as tq


def test_voice_goes_before_chat_before_api_and_fifo_inside_a_class(monkeypatch):
    fast(monkeypatch)
    log = []
    holder = Runner("brain", "chat", "holder", log)
    assert wait_until(lambda: ("holder", "admitted") in log)
    # Arrival order: api, chat 1, chat 2, voice.
    rs = []
    for tid, cls in (("api-1", "api"), ("chat-1", "chat"), ("chat-2", "chat"), ("voice-1", "voice")):
        rs.append(Runner("brain", cls, tid, log))
        assert wait_until(lambda r=rs[-1]: r.waiting())
    # The voice turn is next, but it does not interrupt what is running.
    assert [t.id for t in tq.queue_for("brain").running_tickets()] == ["holder"]
    holder.finish()
    by_id = {r.turn_id: r for r in rs}
    for tid in ("voice-1", "chat-1", "chat-2", "api-1"):          # one at a time
        r = by_id[tid]
        assert wait_until(lambda r=r: any(x[0] == r.turn_id for x in log)), log
        assert len([t for t in tq.queue_for("brain").running_tickets()]) == 1
        r.finish()
    assert [t for t, _ in log] == ["holder", "voice-1", "chat-1", "chat-2", "api-1"]


def test_a_free_seat_admits_at_once_and_a_second_slot_admits_two(monkeypatch):
    fast(monkeypatch)
    one = tq.acquire("solo", "chat", "a")
    assert one.outcome == "admitted" and one.waited_s < 0.5
    two_a = tq.acquire("duo", "chat", "b", capacity=2)
    two_b = tq.acquire("duo", "chat", "c", capacity=2)
    assert two_a.outcome == two_b.outcome == "admitted"
    for adm in (one, two_a, two_b):
        adm.release()


def test_each_seat_has_its_own_line(monkeypatch):
    fast(monkeypatch)
    a = tq.acquire("seat-a", "chat", "a1")
    b = tq.acquire("seat-b", "chat", "b1")
    assert a.outcome == b.outcome == "admitted"
    a.release()
    b.release()


def test_capacity_comes_from_the_seats_np():
    assert tq.capacity_from_args(["-ngl", "99", "-np", "2", "-b", "4096"]) == 2
    assert tq.capacity_from_args("--parallel 3") == 3
    assert tq.capacity_from_args(["-ngl", "99"]) == 1          # default
    assert tq.norm_seat("local/bonsai2:27b") == tq.norm_seat("bonsai2:27b")
    assert tq.norm_seat("") == tq.norm_seat("local/default") == "default"
