"""GET /api/schedules/health: the scheduled jobs that are not keeping up, from the real scheduler's records and run
history (services/schedule_health). Synthetic schedules only."""
from __future__ import annotations

import json
import time

HOUR, DAY = 3600.0, 86400.0


def _rec(sid, **kw):
    base = {"id": sid, "name": "Job " + sid, "trigger": "interval", "spec": {"every_minutes": 60}, "enabled": True,
            "created": time.time() - 30 * DAY, "in_flight": False, "task": {"kind": "builtin", "ref": "x"}}
    base.update(kw)
    return base


def test_a_missed_run_and_a_hung_run_are_flagged_and_a_job_on_time_is_not(client, tmp_path, monkeypatch):
    from agent_friday.services import scheduler
    now = time.time()
    store = [_rec("sch_missed"), _rec("sch_hung", in_flight=True, started_at=now - 15 * 60), _rec("sch_fine")]
    runs = [{"id": "sch_missed", "status": "complete", "ended": now - 3 * HOUR},
            {"id": "sch_hung", "status": "complete", "ended": now - 20 * 60},
            {"id": "sch_fine", "status": "complete", "ended": now - 600}]
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")
    (tmp_path / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n", encoding="utf-8")
    monkeypatch.setattr(scheduler, "_read_store", lambda: [dict(r) for r in store])
    body = client.get("/api/schedules/health").get_json()
    flagged = {f["id"]: f for f in body["flagged"]}
    assert body["status"] == "ok" and body["checked"] == 3
    assert set(flagged) == {"sch_missed", "sch_hung"}
    assert flagged["sch_missed"]["health"] == "overdue" and "3 hours" in flagged["sch_missed"]["reason"]
    assert flagged["sch_hung"]["health"] == "stalled" and "no progress" in flagged["sch_hung"]["reason"]


def test_with_every_job_on_time_nothing_is_flagged(client, tmp_path, monkeypatch):
    from agent_friday.services import scheduler
    now = time.time()
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")
    (tmp_path / "runs.jsonl").write_text(json.dumps({"id": "sch_fine", "status": "complete", "ended": now - 60}) + "\n",
                                         encoding="utf-8")
    monkeypatch.setattr(scheduler, "_read_store", lambda: [_rec("sch_fine")])
    assert client.get("/api/schedules/health").get_json()["flagged"] == []
