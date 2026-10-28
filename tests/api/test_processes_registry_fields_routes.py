"""/api/processes shows the registry's ancestry, principal, cost, receipt link and stop
capability, and cancel refuses a process that cannot be stopped safely (BD-16; NS-29.2-1)."""
from __future__ import annotations

from agent_friday import core


def test_the_process_list_shows_the_new_fields(client):
    core.process_register("p-api-1", name="Work", task_id="t-api", stoppable=True)
    try:
        core.process_update("p-api-1", cost_usd=0.02, receipt="decision:9")
        rows = {r["id"]: r for r in client.get("/api/processes").get_json()["processes"]}
        r = rows["p-api-1"]
        assert r.get("principal") == "owner" and r.get("receipt_id") == "decision:9"
        assert r.get("cost_usd") == 0.02 and r.get("stoppable") is True
    finally:
        with core.PROCESSES_LOCK:
            core.PROCESSES.pop("p-api-1", None)


def test_cancel_refuses_a_process_that_cannot_be_stopped_safely(client):
    core.process_register("p-api-2", name="Backup", stoppable=False,
                          stop_note="Writing the backup; stopping now would leave it half-made.")
    try:
        r = client.post("/api/processes/p-api-2/cancel")
        assert r.status_code == 409, r.status_code
        assert "half-made" in r.get_json()["error"]
        with core.PROCESSES_LOCK:
            assert core.PROCESSES["p-api-2"]["status"] == "running"
    finally:
        with core.PROCESSES_LOCK:
            core.PROCESSES.pop("p-api-2", None)
