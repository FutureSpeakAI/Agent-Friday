"""A folder the owner adds is read as ONE Local AI queue job: idle-gated, one at a time, pausable and
resumable with per-file checkpoints, aware of the memory floor, and shown with counts.

The queue is a small stand-in with the supervisor's interface (admit_inline, annotate, wait_while_blocked,
on_task_end), so these tests watch exactly what the Library asks of the queue. The real supervisor is
exercised in test_library_index_job_on_the_queue.py. Files are tiny and synthetic.
"""
from __future__ import annotations

import time

import pytest

from library_queue_helpers import FakeQueue, install, teardown  # noqa: F401
from tests.library_fixtures import install_fake_encoder, isolate_library, release_library, write_docs


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    queue = install(monkeypatch)
    yield queue
    teardown()
    release_library(fg, lstore)


def _wait(cond, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _folder(tmp_path, n=3, name="Notes"):
    root = tmp_path / name
    write_docs(root, {"n%d.txt" % i: "Meeting notes %d. Agenda and action items to follow up on, plus ideas to "
                                     "remember later about the harbor and tides. Unique marker zebra%d." % (i, i)
                      for i in range(n)})
    return root


def _add(root):
    from agent_friday.services.library import grants, runtime
    ev = grants.add_scope("owner", str(root))
    runtime.index_scope("owner", ev)
    return ev


def _settled(root):
    from agent_friday.services.library import index_job, runtime
    return lambda: (runtime.indexer_for("owner").pending() == 0 and
                    index_job.folders("owner").get(str(root), {}).get("state") in ("done", "stopped", "failed"))


def test_a_folder_is_one_job_on_its_own_seat_with_the_queues_rules(tmp_path, _env):
    from agent_friday.services import background_gate as bg
    from agent_friday.services.library import index_job
    root = _folder(tmp_path)
    _add(root)
    assert _wait(_settled(root))
    assert len(_env.admitted) == 1
    spec = _env.admitted[0]
    assert spec["kind"] == index_job.KIND == "library_index"
    assert spec["seat"] == index_job.JOB_SEAT and spec["seat_is_local"] is True
    assert spec["deferrable"] is True and spec["memory_floor"] is True
    assert spec["label"].startswith("Indexing ")
    assert _env.ended == [("job-1", "completed")]
    # the queue's own rule, applied to this record: waits for idle, runs when idle
    idle = {"idle": True, "idle_s": 9999, "threshold_s": 600, "os_readable": True}
    try:
        bg.idle_state = lambda: dict(idle, idle=False)
        assert bg.block_reason(spec) == bg.REASON_IDLE
        bg.idle_state = lambda: idle
        assert bg.block_reason(dict(spec, memory_floor=False)) == ""
    finally:
        del bg.idle_state


def test_the_folders_counts_are_the_checkpoint_the_chip_shows(tmp_path, _env):
    from agent_friday.services.library import index_job
    root = _folder(tmp_path, 3)
    _add(root)
    assert _wait(_settled(root))
    rec = index_job.folders("owner")[str(root)]
    assert rec["state"] == "done" and rec["total"] == 3 and rec["done"] == 3 and rec["indexed"] == 3
    assert rec["started_ts"] and rec["first_doc_ts"] and rec["passages_done_ts"]
    assert _env.records["job-1"]["job_label"].endswith(" · 3 of 3")
    assert _env.records["job-1"]["progress"] == {"done": 3, "total": 3}
    rep = index_job.first_answer_report("owner")[0]
    assert rep["files"] == 3 and 0 <= rep["to_first_file_s"] <= rep["to_passages_done_s"]


def test_the_counts_survive_a_restart(tmp_path, _env):
    from agent_friday.services.library import index_job
    root = _folder(tmp_path, 2)
    _add(root)
    assert _wait(_settled(root))
    index_job.reset_for_tests()                                   # the process forgot everything
    rec = index_job.folders("owner")[str(root)]
    assert rec["state"] == "done" and rec["total"] == 2 and rec["indexed"] == 2


def test_the_job_asks_the_queue_before_every_file(tmp_path, _env):
    root = _folder(tmp_path, 3)
    _add(root)
    assert _wait(_settled(root))
    assert _env.waits >= 3, "a pause point before each of the three files"


def test_the_chip_label_is_the_kind_of_folder_and_a_count(tmp_path, _env):
    from agent_friday.services.library import index_job
    rec = {"name": "your Obsidian vault", "total": 3100, "done": 1240}
    assert index_job.label(rec) == "Indexing your Obsidian vault · 1,240 of 3,100"
    assert index_job.label(dict(rec, only=True)) == "Reading changes in your Obsidian vault · 1,240 of 3,100"
    assert index_job.label({"name": "Notes"}) == "Indexing Notes"
    vault = tmp_path / "V"
    (vault / ".obsidian").mkdir(parents=True)
    assert index_job.noun(str(vault)) == "your Obsidian vault"


def test_the_words_for_a_wait_say_where_the_folder_is_in_its_run():
    from agent_friday.services.library import index_job
    assert index_job.why_words("waits for idle", "paused") == "paused while you're here"
    assert index_job.why_words("waits for your chat to finish", "paused") == "paused while you're here"
    assert index_job.why_words("waits for idle", "queued") == "waits until you've stepped away"
    assert index_job.why_words(index_job.OWNER_HOLD) == "paused by you"
    row = {"label": "Indexing your Obsidian vault · 1,240 of 3,100", "state": "paused", "why": "waits for idle",
           "progress": {"done": 1240, "total": 3100}}
    assert index_job.say_row(row) == "Indexing your Obsidian vault, 1,240 of 3,100 files, paused while you're here"


def test_cancelling_from_the_queue_stops_that_folder_and_keeps_what_was_read(tmp_path, _env):
    from agent_friday.services.library import changes, index_job, runtime
    from agent_friday.services.library.store import store_for
    root = _folder(tmp_path, 5)
    _env.cancel_at = 3
    ev = _add(root)
    assert _wait(_settled(root))
    rec = index_job.folders("owner")[str(root)]
    assert rec["state"] == "stopped"
    assert _env.ended[-1] == ("job-1", "cancelled")
    read = store_for("owner").counts()["indexed"]
    assert 0 < read < 5
    # a stopped folder is left alone by the change sweep until the owner resumes it
    assert changes.queue("owner", runtime.indexer_for("owner"), ev, store_for("owner")) == 0


def test_the_first_cited_answer_is_possible_while_the_folder_is_still_being_read(tmp_path, _env):
    """Passages come first: a question can be answered, with a citation, before the folder is finished."""
    from agent_friday.services.library import search
    root = _folder(tmp_path, 6)
    _env.cancel_at = 4                                  # the folder is stopped part-way, as a restart would
    _add(root)
    assert _wait(_settled(root))
    from agent_friday.services.library.store import store_for
    assert 0 < store_for("owner").counts()["indexed"] < 6
    res = search.run("Meeting notes agenda action items zebra0", principal="owner")
    assert res["evidence"], "an answer can already be given from what is in"
    assert str(res["evidence"][0]["ref"]).startswith("lib:"), "and it carries its citation"


def test_cancelling_while_waiting_for_idle_reads_nothing(tmp_path, _env):
    from agent_friday.services.library import index_job
    from agent_friday.services.library.store import store_for
    root = _folder(tmp_path, 2)
    _env.cancel_admit = True
    _add(root)
    assert _wait(_settled(root))
    assert index_job.folders("owner")[str(root)]["state"] == "stopped"
    assert store_for("owner").counts()["indexed"] == 0


def test_pause_hands_the_queue_slot_back_and_resume_takes_it_again_where_it_stopped(tmp_path, _env):
    from agent_friday.services.library import index_job, runtime
    from agent_friday.services.library.store import store_for
    root = _folder(tmp_path, 4)
    index_job.pause("owner")
    _add(root)
    assert _wait(lambda: index_job.folders("owner").get(str(root), {}).get("state") == "paused")
    assert _env.ended == [("job-1", "paused")], "the slot is given back, so a model job is not held up behind it"
    rows = index_job.listed_jobs()
    assert len(rows) == 1 and rows[0]["kind"] == "library_index" and rows[0]["reason"] == "paused by you"
    assert rows[0]["cancellable"] and rows[0]["state"] == "waiting"
    assert store_for("owner").counts()["indexed"] == 0
    index_job.resume("owner")
    assert _wait(_settled(root))
    assert len(_env.admitted) == 2, "it took its place in the queue again"
    assert store_for("owner").counts()["indexed"] == 4
    assert index_job.listed_jobs() == []


def test_the_machines_own_holds_pause_it_the_same_way(tmp_path, _env, monkeypatch):
    from agent_friday.services.library import index_job, runtime
    hold = {"on": True}
    monkeypatch.setattr(runtime, "machine_is_free", lambda: (False, "on battery") if hold["on"] else (True, ""))
    root = _folder(tmp_path, 2)
    _add(root)
    assert _wait(lambda: index_job.folders("owner").get(str(root), {}).get("state") == "paused")
    assert index_job.listed_jobs()[0]["reason"] == "paused: on battery"
    hold["on"] = False
    assert _wait(_settled(root))
    assert index_job.folders("owner")[str(root)]["state"] == "done"


def test_a_folder_held_off_the_queue_can_be_cancelled_from_the_queue_list(tmp_path, _env):
    from agent_friday.services.library import index_job
    from agent_friday.services import local_scheduler as ls
    root = _folder(tmp_path, 2)
    index_job.pause("owner")
    _add(root)
    assert _wait(lambda: index_job.folders("owner").get(str(root), {}).get("state") == "paused")
    row = index_job.listed_jobs()[0]
    assert ls.cancel(row["id"])["ok"] is True
    assert _wait(_settled(root))
    assert index_job.folders("owner")[str(root)]["state"] == "stopped"
    assert index_job.listed_jobs() == []


def test_the_scheduler_lists_a_held_folder_with_its_counts_and_the_chips_pause_state(tmp_path, _env):
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services.library import index_job
    root = _folder(tmp_path, 2)
    index_job.pause("owner")
    _add(root)
    assert _wait(lambda: index_job.folders("owner").get(str(root), {}).get("state") == "paused")
    snap = ls.queue_snapshot()
    row = next(r for r in snap["jobs"] if r["kind"] == "library_index")
    assert row["state"] == "queued" and row["why"] == "paused by you" and row["cancellable"] is True
    assert snap["library"] == {"paused": True}


def test_one_folder_waiting_is_one_job_however_many_changes_arrive(tmp_path):
    from agent_friday.services.library import runtime
    ix = runtime.indexer_for("owner")
    ix.can_run = lambda: False                       # the first folder is taken off the queue and parked
    ix.enqueue(str(tmp_path / "First"))
    assert _wait(lambda: ix.current is None and not ix._q or ix._q == [] and ix._thread is not None)
    time.sleep(0.2)
    ix.enqueue(str(tmp_path / "X"), paths=["a"])
    ix.enqueue(str(tmp_path / "X"), paths=["b"])
    ix.enqueue(str(tmp_path / "X"), paths=["a"])
    xs = [(s, kw) for s, kw in ix._q if s.endswith("X")]
    assert len(xs) == 1 and xs[0][1]["paths"] == ["a", "b"]
    ix.enqueue(str(tmp_path / "X"))                  # a whole-folder request covers every change
    xs = [(s, kw) for s, kw in ix._q if s.endswith("X")]
    assert len(xs) == 1 and "paths" not in xs[0][1]
    ix.enqueue(str(tmp_path / "X"), force=True)      # a different kind of request is its own
    assert len([1 for s, _ in ix._q if s.endswith("X")]) == 2


def test_after_a_restart_the_folders_that_were_under_way_are_taken_up_and_stopped_ones_are_not(tmp_path, _env):
    from agent_friday.services.library import grants, index_job, runtime
    from agent_friday.services.library.store import store_for
    a, b = _folder(tmp_path, 2, "A"), _folder(tmp_path, 2, "B")
    grants.add_scope("owner", str(a))
    grants.add_scope("owner", str(b))
    pr = index_job.progress_for("owner")
    pr.set(str(a), state=index_job.RUNNING, name="A", total=2, done=1)      # the process died mid-folder
    pr.set(str(b), state=index_job.STOPPED, name="B")                       # the owner stopped this one
    pr.save()
    index_job.reset_for_tests()
    assert runtime.resume_unfinished("owner") == 1
    assert _wait(_settled(a))
    got = {Path_name(r["path"]) for r in store_for("owner").list_documents("indexed")}
    assert got == {"n0.txt", "n1.txt"} and len(store_for("owner").list_documents("indexed")) == 2
    assert index_job.folders("owner")[str(b)]["state"] == "stopped"


def Path_name(p):
    from pathlib import Path
    return Path(p).name


def test_resume_also_takes_back_a_stopped_folder(tmp_path, _env):
    from agent_friday.services.library import grants, index_job
    from agent_friday.services.library.store import store_for
    root = _folder(tmp_path, 2)
    grants.add_scope("owner", str(root))
    pr = index_job.progress_for("owner")
    pr.set(str(root), state=index_job.STOPPED, name="Notes")
    pr.save()
    index_job.resume("owner")
    assert _wait(_settled(root))
    assert store_for("owner").counts()["indexed"] == 2


def test_the_memory_floor_pauses_a_job_that_asks_for_it_and_nobody_else(monkeypatch):
    from agent_friday.services import background_gate as bg
    from agent_friday.services import local_scheduler as ls
    ls.set_ram_probe(lambda: 1.0)
    try:
        held = bg.block_reason({"seat_is_local": True, "memory_floor": True})
        assert held.startswith("waiting for memory")
        assert bg.block_reason({"seat_is_local": True}) == ""
        assert bg.block_reason({"seat_is_local": False, "memory_floor": True}) == ""
        ls.set_ram_probe(lambda: 64.0)
        assert bg.block_reason({"seat_is_local": True, "memory_floor": True}) == ""
        # reading folders keeps the same free-RAM floor a model load keeps (local_scheduler_ram_floor_gb)
        ls.set_ram_probe(lambda: ls.ram_floor_gb() + 0.5)
        assert bg.block_reason({"seat_is_local": True, "memory_floor": True}) == ""
        ls.set_ram_probe(lambda: ls.ram_floor_gb() - 0.5)
        assert bg.block_reason({"seat_is_local": True, "memory_floor": True}).startswith("waiting for memory")
        assert ls.memory_block(0.0).startswith("waiting for memory")
    finally:
        ls.set_ram_probe(None)


def test_the_voice_read_out_says_how_the_indexing_is_going():
    from agent_friday.services import seat_supervisor as ss
    snap = {"running": [{"id": "j", "kind": "library_index", "state": "paused", "why": "waits for idle",
                         "label": "Indexing your Obsidian vault · 1,240 of 3,100",
                         "progress": {"done": 1240, "total": 3100}}],
            "queued": [{"id": "k", "kind": "library_index", "state": "queued", "why": "waits for the folder ahead of it",
                        "label": "Indexing your Documents folder", "progress": {"done": 0, "total": 0}}]}
    say = ss.spoken_summary(snap)
    assert say.startswith("Indexing your Obsidian vault, 1,240 of 3,100 files, paused while you're here.")
    assert "Next: Indexing your Documents folder" in say


def test_the_snapshot_row_carries_the_progress_counts(_env):
    from agent_friday.services import seat_supervisor as ss
    sup = ss.SeatSupervisor(gate=lambda r: "", sleep=lambda s: None)
    sup.thread_factory = lambda rec: None
    sup.wire_spawn({"id": "j", "kind": "library_index", "label": "Indexing Notes", "seat": "cpu/x", "seat_is_local": True,
                    "deferrable": True, "progress": {"done": 4, "total": 9}})
    assert sup.annotate("j", job_label="Indexing Notes · 5 of 9", progress={"done": 5, "total": 9}) is True
    row = sup.snapshot()["running"][0]
    assert row["label"] == "Indexing Notes · 5 of 9" and row["progress"] == {"done": 5, "total": 9}
    sup.cancel("j")
    assert sup.annotate("j", job_label="x") is False, "a cancelled job is told to stop"


def test_the_voice_can_pause_and_resume_the_reading_and_ask_how_it_is_going(tmp_path, _env):
    from agent_friday.services import queue_tools
    from agent_friday.services.library import index_job, staging
    assert "Paused" in queue_tools.handle_local_work({"action": "pause"})
    assert index_job.progress_for("owner").paused is True
    assert "Carrying on" in queue_tools.handle_local_work({"action": "resume"})
    assert index_job.progress_for("owner").paused is False
    assert index_job.status_line("owner") == ""
    root = _folder(tmp_path, 2)
    _add(root)
    assert _wait(_settled(root))
    assert index_job.status_line("owner") == "Your folders are read: 2 documents in the Library."


def test_drafts_waiting_for_review_are_part_of_the_read_out(tmp_path, _env, monkeypatch):
    from agent_friday.services.library import index_job, staging
    monkeypatch.setattr(staging, "counts", lambda: {"proposed": 3, "accepted": 0, "rejected": 0, "deferred": 0})
    assert index_job.status_line("owner") == "3 drafts from your documents are waiting for your review."
    monkeypatch.setattr(staging, "counts", lambda: {"proposed": 1})
    assert "1 draft from your documents is waiting" in index_job.status_line("owner")


def test_how_is_the_indexing_going_reaches_the_queue_read_out():
    from agent_friday.services import laya_router
    rules = [p for t, p in laya_router._RULES if t == "queue_status"]
    for said in ("how's the indexing going?", "How is the indexing coming along", "is the indexing done yet",
                 "are you done indexing my folders"):
        assert any(r.search(said) for r in rules), said
    for other in ("what's the weather", "how's the weather going to be", "open my notes"):
        assert not any(r.search(other) for r in rules), other


def test_the_queue_list_labels_and_controls_are_in_both_page_files():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for f in ("index.html", "ui_parts/app.html"):
        t = (root / f).read_text(encoding="utf-8")
        assert "library_index: 'indexing'" in t and "paused while you're here" in t, f
        assert "local-ai-library-pause" in t and "/api/local-queue/' + encodeURIComponent(id)" in t, f


# ── every indexing job is ONE visible job in Activity ───────────────────────────

def _library_tasks(kind=None):
    from agent_friday.services import agent
    with agent.TASKS_LOCK:
        return [dict(t) for t in agent.TASKS.values()
                if t.get("workspace") == "library" and (kind is None or t.get("job_kind") == kind)]


def _forget_library_tasks():
    from agent_friday.services import agent
    with agent.TASKS_LOCK:
        for k in [k for k, t in agent.TASKS.items() if t.get("workspace") == "library"]:
            agent.TASKS.pop(k)


def test_a_folder_pass_is_exactly_one_visible_job_that_names_the_reading_model(tmp_path, _env):
    """Activity shows the pass: the model by name, its status, a log, a reasoning trace and its place in the
    queue, which is the card's own id. No second card is made for the same pass."""
    from agent_friday.services.library import index_job
    _forget_library_tasks()
    root = _folder(tmp_path, 3)
    _add(root)
    assert _wait(_settled(root))
    tasks = _library_tasks()
    assert len(tasks) == 1, [t["job_kind"] for t in tasks]
    t = tasks[0]
    assert t["job_kind"] == "library_index" and t["name"].startswith("Indexing ") and str(root) not in t["name"]
    assert t["model"] == index_job.ENCODER_MODEL and t["seat"] == index_job.JOB_SEAT
    assert "trace_id" in t, "the card carries the reasoning trace the Trace button opens"
    assert t["status"] in ("complete", "completed") and "3 of 3" in t["result"]
    assert any("Noted the type" in line for line in t["log"])
    assert _env.admitted[0]["task_id"] == t["task_id"], "the queue row and the card are one job"


def test_a_change_pass_is_its_own_visible_kind_and_a_stopped_pass_is_cancelled(tmp_path, _env):
    from agent_friday.services.library import changes, index_job, runtime
    from agent_friday.services.library.store import store_for
    _forget_library_tasks()
    root = _folder(tmp_path, 4)
    _env.cancel_at = 3
    ev = _add(root)
    assert _wait(_settled(root))
    assert [t["status"] for t in _library_tasks("library_index")] == ["cancelled"]
    _env.cancel_at = None
    index_job.resume("owner")
    assert _wait(_settled(root))
    (root / "extra.txt").write_text("A new file about the harbor and the tides for the change sweep to find.",
                                    encoding="utf-8")
    assert _wait(lambda: changes.queue("owner", runtime.indexer_for("owner"), ev, store_for("owner")) == 1)
    assert _wait(_settled(root))
    assert _library_tasks("library_reindex"), "the change pass is a card of its own kind"


def test_a_meeting_holds_the_folder_and_says_why(tmp_path, monkeypatch):
    from agent_friday.services import turn_queue as tq
    from agent_friday.services.library import index_job
    job = index_job.FolderJob("owner", str(tmp_path))
    assert job._own_hold() == ""
    monkeypatch.setattr(tq, "background_pause_reason", lambda: "You are in a meeting")
    assert job._own_hold() == "paused: you are in a meeting"


def test_the_manifest_declares_every_job_the_library_starts():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    m = json.loads((root / "src/agent_friday/resources/laya_manifests/library.json").read_text(encoding="utf-8"))
    jobs = {j["task_kind"]: j for j in m["jobs"] if j["id"].startswith(("library.index", "library.reindex", "library.stage"))}
    assert set(jobs) == {"library_index", "library_reindex", "library_stage"}
    assert jobs["library_index"]["entry"] == jobs["library_reindex"]["entry"] == \
        "agent_friday.services.library.indexer:Indexer._run"
    assert jobs["library_stage"]["entry"] == "agent_friday.services.library.staging:Stager._one"
    assert all(j["model_class"] in ("encoder", "brain") for j in jobs.values())


def test_the_drafting_job_is_one_visible_deferrable_job_in_the_library_workspace(monkeypatch):
    """Activity shows it as a card of its own kind; the local model is named by the call that uses it."""
    from agent_friday.services import visible_jobs
    from agent_friday.services.library import staging
    seen = {}

    def fake_pass(principal, scope, progress=None, before=None, **kw):
        job = visible_jobs.current()
        seen.update(kind=job.kind, workspace=job.workspace, deferrable=job.deferrable, name=job.name)
        before()
        progress(1, 2, 3)
        return {"state": "done", "done": 2, "total": 2, "proposed": 3}
    monkeypatch.setattr(staging, "run_pass", fake_pass)
    from agent_friday.services.library import index_job
    staging.Stager("owner")._one("/scope", index_job)
    assert seen["kind"] == "library_stage" and seen["workspace"] == "library" and seen["deferrable"] is True
    assert seen["name"].startswith("Drafting notes from ") and "/scope" not in seen["name"]
