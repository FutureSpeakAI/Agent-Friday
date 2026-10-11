"""Cloud seats are never queued: a cloud turn does not wait for the local
brain and takes no slot."""
from turn_queue_helpers import fast

from agent_friday.services import turn_queue as tq


def test_a_cloud_turn_bypasses_a_busy_local_seat(monkeypatch):
    fast(monkeypatch)
    busy = tq.acquire("brain", "chat", "local-1")
    cloud = tq.acquire("claude-sonnet-5-5", "chat", "cloud-1", local=False)
    assert cloud.outcome == "bypass" and cloud.held is False
    assert tq.status_for("cloud-1") is None
    assert tq.overview()[0]["running"][0]["kind"] == "chat" and len(tq.overview()) == 1
    cloud.release()
    assert [t.id for t in tq.queue_for("brain").running_tickets()] == ["local-1"]
    busy.release()
