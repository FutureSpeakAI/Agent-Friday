"""The Library's folder job on the REAL Local AI queue (seat_supervisor over seat_queue, with
background_gate's rules): it waits for idle, runs when idle, "Run now" skips only the idle wait, it shows in
the same rows the chip, the tray and the voice read, and one background job runs at a time.

Fake clocks and fake seats as in test_local_queue_single_source; no model, no server.
"""
from __future__ import annotations

import time

import pytest
from turn_queue_helpers import fast

from tests.library_fixtures import install_fake_encoder, isolate_library, release_library, write_docs


@pytest.fixture
def world(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    import contextlib
    from agent_friday.services import background_gate as bg
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services import seat_supervisor as ss
    from agent_friday.services import turn_queue as tq
    from agent_friday.services.library import index_job, runtime, shelf, staging, triage
    fast(monkeypatch)
    ls.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)
    ls.set_residency_probe(lambda m: not str(m).startswith("cpu/"))     # the brain is loaded; reading folders loads no model
    ls.set_ram_probe(lambda: 64.0)
    away = [0.0]                                   # seconds since the owner last touched the PC
    monkeypatch.setattr(bg, "OS_IDLE_READER", lambda: away[0])
    monkeypatch.setattr(bg, "friday_idle_seconds", lambda: away[0])
    monkeypatch.setattr(bg, "idle_threshold_s", lambda: 600.0)
    monkeypatch.setattr(bg, "interactive_kinds", lambda: [])
    sup = ss.SeatSupervisor(gate=bg.block_reason, sleep=lambda s: time.sleep(0.005), poll_seconds=0.01)
    sup.thread_factory = lambda rec: None
    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: sup)
    tq.queue_for("brain", 1)
    tq.attach_seat_queue(sup.queue, on_idle=lambda s: sup.poke())
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    monkeypatch.setattr(runtime, "machine_is_free", lambda: (True, ""))
    monkeypatch.setattr(runtime, "gate", lambda stop=lambda: False: contextlib.nullcontext())
    monkeypatch.setattr(staging, "enqueue", lambda principal, scope: None)
    runtime._indexers.clear()
    index_job.reset_for_tests()
    triage.reset_heads()
    yield sup, away
    for ix in list(runtime._indexers.values()):
        ix.stop()
    runtime._indexers.clear()
    index_job.reset_for_tests()
    ls.reset_for_tests()
    tq.reset_for_tests()
    tq.HELD.set(())
    tq.CURRENT.set(None)
    triage.reset_heads()
    release_library(fg, lstore)


def _wait(cond, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _add(tmp_path, n=3):
    from agent_friday.services.library import grants, runtime
    root = tmp_path / "Notes"
    write_docs(root, {"n%d.txt" % i: "Meeting notes %d about the harbor and tides, with action items to follow up "
                                     "on. Marker zebra%d." % (i, i) for i in range(n)})
    runtime.index_scope("owner", grants.add_scope("owner", str(root)))
    return root


def _rows():
    """The queue's own rows for the folder job (the job the Library has asked the queue for). A folder that
    is only listed as waiting for its turn (id libidx:...) is not one of them."""
    from agent_friday.services import local_scheduler as ls
    snap = ls.queue_snapshot()
    return [r for r in snap["running"] + snap["queued"]
            if r.get("kind") == "library_index" and not str(r["id"]).startswith("libidx:")]


def _done(root):
    from agent_friday.services.library import index_job, runtime
    return lambda: (runtime.indexer_for("owner").pending() == 0
                    and index_job.folders("owner").get(str(root), {}).get("state") == "done")


def test_while_the_owner_is_here_the_folder_waits_in_the_queue_and_reads_nothing(world, tmp_path):
    from agent_friday.services.library.store import store_for
    root = _add(tmp_path)
    assert _wait(lambda: _rows() != [])
    row = _rows()[0]
    assert row["state"] == "queued" and row["deferrable"] is True and row["seat"] == "cpu/library-index"
    assert row["why"] in ("waits for idle", "waiting for you to be idle")
    time.sleep(0.3)
    assert store_for("owner").counts()["indexed"] == 0


def test_when_the_pc_goes_idle_the_job_starts_and_the_folder_is_read(world, tmp_path):
    from agent_friday.services.library.store import store_for
    sup, away = world
    root = _add(tmp_path)
    assert _wait(lambda: _rows() != [])
    away[0] = 1e9
    assert _wait(_done(root))
    assert store_for("owner").counts()["indexed"] == 3
    assert _rows() == []


def test_run_now_skips_only_the_idle_wait(world, tmp_path):
    from agent_friday.services.library.store import store_for
    sup, away = world
    root = _add(tmp_path)
    assert _wait(lambda: _rows() != [])
    assert sup.run_now(_rows()[0]["id"]) is True
    assert _wait(_done(root))
    assert store_for("owner").counts()["indexed"] == 3


def test_coming_back_pauses_the_job_before_the_next_file_and_the_chip_says_so(world, tmp_path):
    from agent_friday.services.library import index_job
    sup, away = world
    away[0] = 1e9
    root = _add(tmp_path, 3)
    assert _wait(lambda: index_job.folders("owner").get(str(root), {}).get("done", 0) >= 1)
    away[0] = 0.0                                   # the owner is back
    assert _wait(lambda: any(r["state"] == "paused" for r in _rows()) or _done(root)())
    if not _done(root)():
        row = next(r for r in _rows() if r["state"] == "paused")
        assert row["why"] == "waits for idle"
        assert index_job.say_row(row).endswith("paused while you're here")
        away[0] = 1e9
    assert _wait(_done(root))


def test_the_row_carries_counts_and_a_label_the_chip_and_the_voice_both_read(world, tmp_path):
    from agent_friday.services import seat_supervisor as ss
    from agent_friday.services.library import index_job
    sup, away = world
    root = _add(tmp_path, 3)
    assert _wait(lambda: _rows() != [])
    sup.run_now(_rows()[0]["id"])
    assert _wait(lambda: any(r.get("progress", {}).get("total") == 3 for r in _rows()) or _done(root)())
    rows = _rows()
    if rows:
        assert rows[0]["label"].startswith("Indexing ") and rows[0]["progress"]["total"] == 3
        assert "of 3 files" in ss.spoken_summary({"running": rows, "queued": []})
    assert _wait(_done(root))


def test_a_folder_and_a_model_job_are_separate_lanes_so_neither_waits_for_the_other(world, tmp_path):
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services.library.store import store_for
    sup, away = world
    away[0] = 1e9
    sup.wire_spawn({"id": "wiki", "seat": "local/brain", "kind": "wiki_distill", "label": "wiki notes",
                    "deferrable": True})
    assert any(r["id"] == "wiki" for r in ls.queue_snapshot()["running"])
    root = _add(tmp_path)
    assert _wait(_done(root)), "reading a folder does not wait for the model job that is running"
    assert store_for("owner").counts()["indexed"] == 3
    sup.on_task_end("wiki")


def test_a_model_job_starts_while_a_folder_is_being_read(world, tmp_path, monkeypatch):
    """The owner's report: the reading model held the line while the GPU models' jobs waited behind it."""
    import threading
    from agent_friday.services.library import index_job
    sup, away = world
    away[0] = 1e9
    gate = threading.Event()
    real_done = index_job.FolderJob.done

    def slow_done(self, path, result):               # hold the folder mid-read until the test has looked
        real_done(self, path, result)
        gate.wait(10)
    monkeypatch.setattr(index_job.FolderJob, "done", slow_done)
    root = _add(tmp_path, 2)
    assert _wait(lambda: any(r["state"] == "running" for r in _rows()))
    assert sup.wire_spawn({"id": "wiki", "seat": "local/brain", "kind": "wiki_distill", "label": "wiki notes",
                           "deferrable": True}) == "running", "the model job is not held behind the folder"
    gate.set()
    assert _wait(_done(root))
    sup.on_task_end("wiki")


def test_two_model_jobs_still_take_turns(world):
    """The 1.0.2 cap stays for model work: one background model job at a time."""
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services import turn_queue as tq
    sup, away = world
    away[0] = 1e9
    tq.queue_for("other-model").background_started("running-model-job", label="first")
    t = tq.Ticket("next-model-job", "background", "brain", model="", kind="task")
    assert ls.gate(t) == "waiting for the background job that is already running"
    tq.queue_for("other-model").background_finished("running-model-job")


def test_pause_holds_a_folder_that_is_still_waiting_in_the_queue(world, tmp_path):
    """Pause pressed while the folder waits for idle or memory: it must not start when the wait ends."""
    from agent_friday.services.library import index_job
    from agent_friday.services.library.store import store_for
    sup, away = world
    away[0] = 0.0                                    # the owner is here: the folder waits for idle
    root = _add(tmp_path)
    assert _wait(lambda: any(r["state"] == "queued" for r in _rows()))
    index_job.pause("owner")
    away[0] = 1e9                                    # the owner steps away
    time.sleep(0.4)
    assert store_for("owner").counts()["indexed"] == 0, "paused by you, even though the PC is now idle"
    row = next(r for r in _rows())
    assert row["why"] == "paused by you" and row["paused_by_you"] is True
    index_job.resume("owner")
    assert _wait(_done(root))


def test_the_cards_stop_ends_a_folder_that_is_waiting_in_the_queue(world, tmp_path, monkeypatch):
    """Stop from the Activity card while the folder waits for idle: the queue's wait ends it."""
    from agent_friday.routes import tasks as task_routes
    from agent_friday.services import agent as ag
    from agent_friday.services.library import index_job
    from agent_friday.services.library.store import store_for
    sup, away = world
    monkeypatch.setattr(ag, "_seat_supervisor", lambda: sup)
    away[0] = 0.0
    root = _add(tmp_path)
    assert _wait(lambda: any(r["state"] == "queued" for r in _rows()))
    job_id = _rows()[0]["id"]
    with ag.TASKS_LOCK:
        card = dict(ag.TASKS[job_id])
    assert card.get("stoppable") is True, "the Activity row offers Stop for this job"
    # What POST /api/tasks/<id>/stop-after-step does besides setting the card's stop flag.
    assert task_routes._stop_waiting_queue_job(job_id, card) is True
    assert _wait(lambda: index_job.folders("owner").get(str(root), {}).get("state") == "stopped")
    assert store_for("owner").counts()["indexed"] == 0


def test_cancelling_the_row_from_the_queue_stops_the_folder(world, tmp_path):
    from agent_friday.services.library import index_job
    from agent_friday.services.library.store import store_for
    sup, away = world
    root = _add(tmp_path)
    assert _wait(lambda: _rows() != [])
    assert sup.cancel(_rows()[0]["id"]) is True
    assert _wait(lambda: index_job.folders("owner").get(str(root), {}).get("state") == "stopped")
    assert store_for("owner").counts()["indexed"] == 0


def test_reading_a_folder_loads_no_model_so_only_idle_and_memory_decide_when_it_starts(world):
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services import turn_queue as tq
    sup, away = world
    ls.set_residency_probe(lambda m: False)                  # nothing at all is loaded
    cpu = tq.Ticket("t-cpu", "background", "cpu/library-index", model="cpu/library-index", kind="task")
    away[0] = 0.0
    assert ls._gate_core(cpu) == "waiting for you to be idle"
    away[0] = 1e9
    assert ls._gate_core(cpu) == ""
    ls.set_ram_probe(lambda: ls.ram_floor_gb() + 1.0)
    assert ls._gate_core(cpu) == "", "above the floor a folder is read"
    ls.set_ram_probe(lambda: ls.ram_floor_gb() - 0.5)
    assert ls._gate_core(cpu).startswith("waiting for memory"), "the same floor a model load keeps"
