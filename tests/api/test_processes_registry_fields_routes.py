"""/api/processes shows the registry's ancestry, principal, cost, receipt link and stop
capability (BD-16; NS-29.2-1)."""
from __future__ import annotations

from agent_friday import core


def test_the_process_list_shows_the_new_fields(client):
    core.process_register("p-api-1", name="Work", task_id="t-api")
    try:
        try:
            core.process_update("p-api-1", cost_usd=0.02, receipt="decision:9")
        except TypeError:
            pass                                   # the old registry: the asserts below say what is missing
        rows = {r["id"]: r for r in client.get("/api/processes").get_json()["processes"]}
        r = rows["p-api-1"]
        assert r.get("principal") == "owner" and r.get("receipt_id") == "decision:9"
        assert r.get("cost_usd") == 0.02 and r.get("stop_capability") == "stop_everything"
    finally:
        with core.PROCESSES_LOCK:
            core.PROCESSES.pop("p-api-1", None)
