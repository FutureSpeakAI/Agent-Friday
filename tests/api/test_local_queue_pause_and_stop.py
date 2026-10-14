"""Every job in the Local AI queue has a Pause and a Stop that work, in every state it can be in.

Pause (POST /api/local-queue/<id>/pause, and /resume): a waiting job is not started until it is resumed;
a running one waits at its next model call and lets the next job in line have the seat. The Activity row
offers Pause exactly where it works. The orb's cancel of a task stops the task itself and never takes the
GPU lease of whichever job holds it.

The real supervisor over a fake seat; fake idle clock; no model.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import background_gate as bg
from agent_friday.services import local_scheduler as ls
from agent_friday.services import seat_supervisor as ss
from agent_friday.services import task_journal as tj
from agent_friday.services import turn_queue as tq


@pytest.fixture
def sup(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    monkeypatch.setattr(tq, "DEFAULT_STATUS_EVERY_S", 0.03)
    monkeypatch.setattr(tq, "_WAIT_POLL_S", 0.01)
    monkeypatch.setattr(ls, "WRAP_BACKGROUND", [False])
    tq.reset_for_tests()
    ls.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)
    ls.set_residency_probe(lambda m: True)
    ls.set_ram_probe(lambda: 64.0)
    monkeypatch.setattr(bg, "OS_IDLE_READER", lambda: 1e9)
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: 1e9)
    monkeypatch.setattr(bg, "idle_threshold_s", lambda: 600.0)
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    s = ss.SeatSupervisor(gate=bg.block_reason, sleep=lambda x: time.sleep(0.005), poll_seconds=0.01)
    s.thread_factory = lambda rec: None
    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: s)
    monkeypatch.setattr(ag, "_seat_supervisor", lambda: s)
    tq.queue_for("brain", 1)
    tq.attach_seat_queue(s.queue, on_idle=lambda seat: s.poke())
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    yield s
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)
    ls.reset_for_tests()
    tq.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)


def _job(i, **kw):
    rec = {"id": i, "seat": "local/brain", "kind": "wiki_distill", "label": "wiki notes " + i, "deferrable": True}
    rec.update(kw)
    return rec


def _row(job_id):
    return next(r for r in ls.queue_snapshot()["jobs"] if r["id"] == job_id)


def test_a_paused_waiting_job_is_not_started_until_it_is_resumed(sup, client):
    assert sup.wire_spawn(_job("first")) == "running"
    assert sup.wire_spawn(_job("second")) == "queued-for-seat"
    r = client.post("/api/local-queue/second/pause")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    sup.on_task_end("first")
    assert sup.queue.get("second")["status"] == "queued-for-seat", "paused: the free seat is not its"
    assert _row("second")["why"] == "paused by you" and _row("second")["paused_by_you"] is True
    assert client.post("/api/local-queue/second/resume").get_json()["ok"] is True
    assert sup.queue.get("second")["status"] == "running"
    sup.on_task_end("second")


def test_a_paused_running_job_waits_at_its_next_model_call_and_lets_the_next_job_go(sup):
    import threading
    assert sup.wire_spawn(_job("long")) == "running"
    assert sup.wire_spawn(_job("next")) == "queued-for-seat"
    assert sup.pause("long") is True
    done = threading.Event()
    threading.Thread(target=lambda: (sup.wait_while_blocked("long"), done.set()), daemon=True).start()
    t0 = time.time()
    while sup.queue.get("next")["status"] != "running" and time.time() - t0 < 5:
        time.sleep(0.01)
    assert sup.queue.get("next")["status"] == "running", "the paused job gave the seat to the next one"
    assert not done.is_set(), "and it is still waiting"
    sup.on_task_end("next")
    assert sup.resume("long") is True
    assert done.wait(5), "Resume lets it carry on"
    sup.on_task_end("long")


def test_pause_and_resume_of_a_job_that_is_gone_say_so(sup, client):
    assert client.post("/api/local-queue/no-such-job/pause").status_code == 404
    assert sup.resume("no-such-job") is False


def test_the_activity_row_offers_pause_only_where_it_works(sup):
    from agent_friday.services import activity_lane
    from agent_friday.services import visible_jobs as vj
    assert sup.wire_spawn(_job("queued-job")) == "running"
    with vj.visible_job("A cloud job", kind="x", workspace="w", queued=False) as cloud:
        rows = {r["id"]: r for r in activity_lane.activity()["tasks"]}
        assert rows["queued-job"]["pausable"] is True and rows["queued-job"]["paused_by_you"] is False
        assert rows[cloud.id]["pausable"] is False, "no queue checkpoint, so no Pause"
    sup.pause("queued-job")
    rows = {r["id"]: r for r in activity_lane.activity()["tasks"]}
    assert rows["queued-job"]["paused_by_you"] is True
    sup.on_task_end("queued-job")


def test_both_ui_files_draw_the_pause_on_a_pausable_row():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in ("index.html", "ui_parts/app.html"):
        text = (root / name).read_text(encoding="utf-8")
        assert "'data-testid': 'lane-pause'" in text and "t.pausable" in text, name
        assert "onLocal(t.id, t.paused_by_you ? 'resume' : 'pause')" in text, name


def test_the_orb_cancel_of_a_task_stops_the_task_and_leaves_the_gpu_lease_alone(sup, client, monkeypatch):
    """Cancelling a task's orb asked nothing of the task and released the GPU lease of whatever held it."""
    from agent_friday import core
    from agent_friday.services import residency_arbiter as ra
    from agent_friday.services import visible_jobs as vj

    class Arbiter:
        lease = {"holder": "an image render"}
        released = 0

        def release(self):
            Arbiter.released += 1
            Arbiter.lease = None
    monkeypatch.setattr(ra, "get_arbiter", lambda: Arbiter())
    with vj.visible_job("Reading changes in content", kind="library_reindex", workspace="library",
                        queued=False) as job:
        assert core.PROCESSES.get(job.id, {}).get("task_id") == job.id
        r = client.post("/api/processes/%s/cancel" % job.id)
        assert r.status_code == 200 and "the job" in r.get_json()["stopped"]
        assert tj.stop_requested(job.id), "the task itself was asked to stop"
        assert Arbiter.released == 0 and Arbiter.lease is not None, "another job's GPU lease was not touched"
        with pytest.raises(bg.JobCancelled):
            job.check_stop()
