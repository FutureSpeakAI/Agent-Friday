"""Arbiter leases go through the scheduler: a second lease request queues (with
a timeout) instead of being refused, and a lease stands the seats down only
when no work of a higher class runs or waits. Fake arbiter, no GPU."""
import threading
import time

import pytest
from turn_queue_helpers import fast, wait_until

from agent_friday.services import local_scheduler as ls
from agent_friday.services import turn_queue as tq


class FakeArbiter:
    """What lease_grant needs of an Arbiter: a lease slot and one attempt."""

    def __init__(self):
        self.lease = None
        self.granted = []
        self.lock = threading.Lock()

    def _grant_now(self, kind, *, role=None, ttl_s=300):
        with self.lock:
            if self.lease is not None:
                return {"ok": False, "error": "lease %s already held" % self.lease["kind"]}
            self.lease = {"kind": kind}
            self.granted.append(kind)
            return {"ok": True, "lease": dict(self.lease)}

    def release(self):
        with self.lock:
            self.lease = None


@pytest.fixture(autouse=True)
def scheduler(monkeypatch, tmp_path):
    fast(monkeypatch)
    ls.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)
    monkeypatch.setattr(ls, "metrics_path", lambda: tmp_path / "metrics.jsonl")
    monkeypatch.setattr(ls, "LEASE_POLL_S", 0.01)
    ls.set_ram_probe(lambda: 64.0)
    yield
    ls.reset_for_tests()
    tq.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)


def _request(arb, kind, out, **kw):
    def run():
        out.append((kind, arb_result(arb, kind, **kw)))
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def arb_result(arb, kind, **kw):
    return ls.lease_grant(arb, kind, **kw)


def test_a_second_lease_waits_for_the_first_instead_of_being_refused():
    arb = FakeArbiter()
    assert ls.lease_grant(arb, "heavy_turn")["ok"]
    out = []
    th = _request(arb, "image_job", out, wait_s=5)
    assert wait_until(lambda: ls.lease_waiters())
    w = ls.lease_waiters()[0]
    assert w["kind"] == "image_job" and "heavy model turn" in w["reason"]
    assert out == [], "it did not come back refused"
    arb.release()
    th.join(5)
    assert out[0][1]["ok"] is True
    assert arb.granted == ["heavy_turn", "image_job"]
    assert ls.lease_waiters() == []


def test_a_lease_that_waits_its_whole_allowance_is_refused_with_the_reason():
    arb = FakeArbiter()
    ls.lease_grant(arb, "heavy_turn")
    t0 = time.monotonic()
    got = ls.lease_grant(arb, "image_job", wait_s=0.3)
    assert got["ok"] is False and got["timed_out"] is True
    assert "Waited" in got["error"] and "heavy model turn" in got["error"]
    assert 0.25 <= time.monotonic() - t0 < 3


def test_wait_zero_asks_once_and_takes_the_answer():
    arb = FakeArbiter()
    ls.lease_grant(arb, "heavy_turn")
    got = ls.lease_grant(arb, "image_job", wait_s=0)
    assert got["ok"] is False and "already held" in got["error"]


def test_the_arbiter_grant_goes_through_the_scheduler():
    """Arbiter.grant is the scheduler's lease_grant, over the one-attempt _grant_now."""
    from agent_friday.services import residency_arbiter as ra

    class Stub:
        lease = None
        calls = []

        def _grant_now(self, kind, *, role=None, ttl_s=300):
            self.calls.append((kind, ttl_s))
            return {"ok": True}
    stub = Stub()
    assert ra.Arbiter.grant(stub, "image_job", ttl_s=77, wait_s=0) == {"ok": True}
    assert stub.calls == [("image_job", 77)]
    assert hasattr(ra.Arbiter, "_grant_now")


def test_waiting_leases_are_served_by_class_then_arrival():
    arb = FakeArbiter()
    ls.lease_grant(arb, "heavy_turn")
    out = []
    ths = [_request(arb, "podcast_voice", out, wait_s=10)]
    assert wait_until(lambda: len(ls.lease_waiters()) == 1)
    ths.append(_request(arb, "image_job", out, wait_s=10))
    assert wait_until(lambda: len(ls.lease_waiters()) == 2)
    ths.append(_request(arb, "voice_call", out, wait_s=10))
    assert wait_until(lambda: len(ls.lease_waiters()) == 3)
    for _ in range(3):
        arb.release()
        assert wait_until(lambda: arb.lease is not None, timeout=5)
        time.sleep(0.05)
    arb.release()
    for th in ths:
        th.join(5)
    assert arb.granted == ["heavy_turn", "voice_call", "image_job", "podcast_voice"]


def test_a_lease_does_not_stand_the_seats_down_under_a_running_chat():
    arb = FakeArbiter()
    chat = tq.acquire("default", "chat", "chat-1")
    box = []

    def ask():                                  # another thread: this lease is not the chat's own
        box.append(ls.lease_grant(arb, "image_job", wait_s=10))
    th = threading.Thread(target=ask, daemon=True)
    th.start()
    assert wait_until(lambda: ls.lease_waiters() and "chat" in ls.lease_waiters()[0]["reason"])
    assert arb.granted == [] and box == []
    chat.release()
    th.join(5)
    assert box[0]["ok"] and arb.granted == ["image_job"]


def test_a_lease_waits_for_higher_class_work_that_is_still_waiting():
    arb = FakeArbiter()
    runner = tq.acquire("default", "background", "bg-1")
    voice = []

    def waiting_voice():
        voice.append(tq.acquire("default", "voice", "v-1"))
    vt = threading.Thread(target=waiting_voice, daemon=True)
    vt.start()
    assert wait_until(lambda: tq.status_for("v-1") and tq.status_for("v-1")["state"] == "waiting")
    box = []
    th = threading.Thread(target=lambda: box.append(ls.lease_grant(arb, "image_job", wait_s=10)), daemon=True)
    th.start()
    assert wait_until(lambda: ls.lease_waiters() and "voice" in ls.lease_waiters()[0]["reason"])
    runner.release()
    assert wait_until(lambda: bool(voice))
    time.sleep(0.1)
    assert arb.granted == [], "a voice turn is running now"
    voice[0].release()
    th.join(5)
    assert box[0]["ok"]


def test_the_turn_that_asks_for_a_lease_is_not_a_conflict_with_itself():
    """A chat turn's image tool asks for the card from inside the turn."""
    arb = FakeArbiter()
    chat = tq.acquire("default", "chat", "chat-1")
    got = ls.lease_grant(arb, "image_job", wait_s=2)
    assert got["ok"]
    chat.release()


def test_a_voice_call_takes_the_card_from_lower_class_work():
    """The call is the owner talking: it outranks a chat that is running."""
    arb = FakeArbiter()
    chat = tq.acquire("default", "chat", "chat-1")
    box = []
    th = threading.Thread(target=lambda: box.append(ls.lease_grant(arb, "voice_call", wait_s=2)), daemon=True)
    th.start()
    th.join(5)
    assert box[0]["ok"]
    chat.release()


def test_a_waiting_lease_can_be_taken_out_of_the_line():
    arb = FakeArbiter()
    ls.lease_grant(arb, "heavy_turn")
    box = []
    th = threading.Thread(target=lambda: box.append(ls.lease_grant(arb, "image_job", wait_s=30)), daemon=True)
    th.start()
    assert wait_until(lambda: ls.lease_waiters())
    lease_job = [j for j in ls.jobs() if j["source"] == "lease"][0]
    assert lease_job["cancellable"] and lease_job["state"] == "waiting"
    assert ls.cancel(lease_job["id"])["ok"]
    th.join(5)
    assert box[0]["ok"] is False and box[0]["cancelled"] is True
    assert arb.granted == ["heavy_turn"]


def test_a_load_waits_for_memory():
    arb = FakeArbiter()
    free = [2.0]
    ls.set_ram_probe(lambda: free[0])
    box = []
    th = threading.Thread(target=lambda: box.append(ls.lease_grant(arb, "heavy_turn", wait_s=10)), daemon=True)
    th.start()
    assert wait_until(lambda: ls.lease_waiters() and "memory" in ls.lease_waiters()[0]["reason"])
    assert arb.granted == []
    free[0] = 16.0
    th.join(5)
    assert box[0]["ok"]
    # A lease that only stands seats down loads nothing, so memory is not asked.
    arb.release()
    free[0] = 1.0
    assert ls.lease_grant(arb, "voice_call", wait_s=2)["ok"]
