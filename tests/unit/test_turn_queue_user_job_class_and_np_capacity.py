"""The queue's fifth class and its capacity. Written against the queue alone:
the class a job you asked for belongs to, where it stands between the Local
API and background work, and a seat's slots read from its real ``-np``."""
import pytest
from turn_queue_helpers import Runner, fast, wait_until

from agent_friday.services import local_seats
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import turn_queue as tq


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    fast(monkeypatch)
    yield
    tq.reset_for_tests()


def test_a_job_you_asked_for_goes_after_the_local_api_and_before_background():
    log = []
    holder = Runner("brain", "chat", "holder", log)
    assert wait_until(lambda: ("holder", "admitted") in log)
    rs = {}
    for tid, cls in (("bg-1", "background"), ("job-1", "user_job"), ("api-1", "api"),
                     ("job-2", "user_job"), ("chat-1", "chat"), ("voice-1", "voice")):
        # bg-1 is time-bound (a chat turn ending would restart the idle clock for work that can wait).
        rs[tid] = Runner("brain", cls, tid, log, deferrable=False)
        assert wait_until(lambda r=rs[tid]: r.waiting())
    holder.finish()
    for tid in ("voice-1", "chat-1", "api-1", "job-1", "job-2", "bg-1"):
        assert wait_until(lambda t=tid: any(x[0] == t for x in log)), log
        rs[tid].finish()
    assert [t for t, _ in log] == ["holder", "voice-1", "chat-1", "api-1", "job-1", "job-2", "bg-1"]


def test_the_five_classes_are_named_in_order():
    assert tq.CLASSES == ("voice", "chat", "api", "user_job", "background")
    assert tq.class_priority("user_job") > tq.class_priority("api")
    assert tq.class_priority("user_job") < tq.class_priority("background")


def test_capacity_comes_from_the_serve_args_the_seat_was_declared_with(monkeypatch):
    monkeypatch.setattr(local_seats, "_configured_local_model", lambda: "local/test-brain")
    monkeypatch.setattr(ra.LlamaServerBackend, "_declared_serve_args",
                        staticmethod(lambda m: ["-ub", "2048", "-np", "3"] if m == "test-brain" else []))
    monkeypatch.setattr(ra, "get_arbiter", lambda: None)
    tq.reset_for_tests()
    assert tq.seat_capacity("default") == 3, "a seat that runs three slots admits three turns"
    held = [tq.acquire("default", "chat", "c%d" % i) for i in range(3)]
    assert all(h.outcome == "admitted" for h in held)
    fourth = Runner("default", "chat", "c3", [])
    assert wait_until(lambda: fourth.waiting())
    held[0].release()
    assert wait_until(lambda: not fourth.waiting())
    fourth.finish()
    for h in reversed(held[1:]):
        h.release()


def test_capacity_reads_the_live_command_line_over_the_declared_args(monkeypatch):
    class Proc:
        args = ["llama-server", "-m", "x.gguf", "-np", "4", "-c", "8192"]

    class Arb:
        class llama:
            procs = {"test-brain": (Proc(), 8090)}
    monkeypatch.setattr(local_seats, "_configured_local_model", lambda: "test-brain")
    monkeypatch.setattr(ra, "get_arbiter", lambda: Arb())
    monkeypatch.setattr(ra.LlamaServerBackend, "_declared_serve_args", staticmethod(lambda m: ["-np", "2"]))
    tq.reset_for_tests()
    assert tq.seat_capacity("default") == 4


def test_the_owners_slot_setting_outranks_the_seat_and_no_record_means_one(monkeypatch):
    monkeypatch.setattr(local_seats, "_configured_local_model", lambda: "plain-brain")
    monkeypatch.setattr(ra, "get_arbiter", lambda: None)
    monkeypatch.setattr(ra.LlamaServerBackend, "_declared_serve_args", staticmethod(lambda m: []))
    tq.reset_for_tests()
    assert tq.seat_capacity("default") == 1
    monkeypatch.setattr(tq, "_settings", lambda: {"turn_queue_seat_slots": {"default": 2}})
    assert tq.seat_capacity("default") == 2
