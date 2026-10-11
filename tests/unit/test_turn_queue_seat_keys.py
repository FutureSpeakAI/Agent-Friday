"""One server, one line: the default seat, local/default and the configured
brain model are the same queue key."""
from turn_queue_helpers import fast

from agent_friday.services import local_seats
from agent_friday.services import turn_queue as tq


def test_default_local_default_and_the_configured_brain_share_one_key(monkeypatch):
    fast(monkeypatch)
    monkeypatch.setattr(local_seats, "_configured_local_model", lambda: "bonsai2:27b")
    keys = {tq.norm_seat(s) for s in ("", None, "default", "local/default",
                                      "bonsai2:27b", "local/bonsai2:27b", "BONSAI2:27B")}
    assert keys == {"default"}
    assert tq.norm_seat("some-other:7b") == "some-other:7b"


def test_a_chat_on_the_brain_model_waits_behind_a_task_on_the_default_seat(monkeypatch):
    fast(monkeypatch)
    monkeypatch.setattr(local_seats, "_configured_local_model", lambda: "bonsai2:27b")
    tq.queue_for("local/default").background_started("bg-1", label="job")
    from turn_queue_helpers import Runner, wait_until
    r = Runner("bonsai2:27b", "chat", "c1", [])
    assert wait_until(lambda: r.waiting())
    assert tq.status_for("c1")["running_kind"] == "background"
    tq.queue_for("default").background_finished("bg-1")
    r.finish()
