"""Idle change detection: what changed in a folder is found by a stat, queued as ONE job per folder, and read
only when the PC is idle; a file whose bytes did not change is not read again.

The queue is a stand-in (library_queue_helpers). Extraction runs in a child, so a test that must prove a file
was or was not read counts calls to `procrun.run_task`. Fixtures are synthetic.
"""
from __future__ import annotations

import os
import threading
import time
import types
from pathlib import Path

import pytest

from library_queue_helpers import install, teardown
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


def _store():
    from agent_friday.services.library.store import store_for
    return store_for("owner")


def _text(i, extra=""):
    return ("Meeting notes %d. Agenda and action items to follow up on, ideas to remember later about the harbor "
            "and the tides. Unique marker zebra%d. %s" % (i, i, extra))


def _read_folder(tmp_path, n=4):
    """A folder that has been read once, the way the owner's add reads it."""
    from agent_friday.services.library import grants, index_job, runtime
    root = tmp_path / "Notes"
    write_docs(root, {"n%d.txt" % i: _text(i) for i in range(n)})
    ev = grants.add_scope("owner", str(root))
    runtime.index_scope("owner", ev)
    assert _wait(lambda: runtime.indexer_for("owner").pending() == 0
                 and index_job.folders("owner").get(str(root), {}).get("state") == "done")
    assert _store().counts()["indexed"] == n
    return root, ev


def _settled(root):
    from agent_friday.services.library import index_job, runtime
    return lambda: (runtime.indexer_for("owner").pending() == 0
                    and index_job.folders("owner").get(str(root), {}).get("state") in ("done", "stopped", "failed"))


class Reads:
    """Counts the files the reader child is asked to open."""

    def __init__(self, monkeypatch):
        from agent_friday.services.library import procrun
        self.paths = []
        real = procrun.run_task

        def counting(task, args, **kw):
            if task == "extract":
                self.paths.append(Path(args["path"]).name)
            return real(task, args, **kw)
        monkeypatch.setattr(procrun, "run_task", counting)


def test_nothing_changed_means_nothing_found_and_nothing_read(tmp_path, monkeypatch):
    from agent_friday.services.library import changes, indexer, procrun
    root, _ev = _read_folder(tmp_path)
    monkeypatch.setattr(procrun, "run_task", lambda *a, **k: pytest.fail("a file was read"))
    monkeypatch.setattr(indexer, "file_sha256", lambda p: pytest.fail("a file was hashed"))
    assert changes.detect(_store(), root) == {"changed": [], "new": [], "gone": []}


def test_new_edited_touched_and_deleted_files_are_each_found_by_a_stat(tmp_path, monkeypatch):
    from agent_friday.services.library import changes, indexer, procrun
    root, _ev = _read_folder(tmp_path, 5)
    (root / "added.txt").write_text(_text(99), encoding="utf-8")
    (root / "n1.txt").write_text(_text(1, "A sentence added after the first reading."), encoding="utf-8")
    st = (root / "n2.txt").stat()
    os.utime(root / "n2.txt", (st.st_atime, st.st_mtime + 100))               # touched: same bytes, later time
    (root / "n3.txt").unlink()
    monkeypatch.setattr(procrun, "run_task", lambda *a, **k: pytest.fail("detection read a file"))
    monkeypatch.setattr(indexer, "file_sha256", lambda p: pytest.fail("detection hashed a file"))
    d = changes.detect(_store(), root)
    names = lambda xs: sorted(Path(x).name for x in xs)  # noqa: E731
    assert names(d["new"]) == ["added.txt"]
    assert names(d["changed"]) == ["n1.txt", "n2.txt"]
    assert names(d["gone"]) == ["n3.txt"]


def test_a_file_left_half_read_is_changed_and_a_failed_one_waits_for_an_edit(tmp_path):
    from agent_friday.services.library import changes
    root, _ev = _read_folder(tmp_path, 3)
    st = _store()
    a, b = (st.find_document(str((root / n).resolve())) for n in ("n0.txt", "n1.txt"))
    st.set_state(a["id"], "queued")
    st.set_state(b["id"], "failed:took too long to read")
    d = changes.detect(st, root)
    assert [Path(x).name for x in d["changed"]] == ["n0.txt"]
    (root / "n1.txt").write_text(_text(1, "Edited, so it is worth another try."), encoding="utf-8")
    d = changes.detect(st, root)
    assert sorted(Path(x).name for x in d["changed"]) == ["n0.txt", "n1.txt"]


def test_a_burst_of_changes_is_one_job_for_the_folder(tmp_path, _env):
    from agent_friday.services.library import changes, index_job, runtime
    root, ev = _read_folder(tmp_path)
    for i in range(3):
        (root / ("burst%d.txt" % i)).write_text(_text(50 + i), encoding="utf-8")
    ix = runtime.indexer_for("owner")
    before = len(_env.admitted)
    assert changes.queue("owner", ix, ev, _store()) == 1
    assert changes.queue("owner", ix, ev, _store()) == 0, "already waiting: not queued again"
    assert _wait(_settled(root))
    assert len(_env.admitted) == before + 1
    rec = index_job.folders("owner")[str(root)]
    assert rec["only"] is True and rec["total"] == 3 and rec["indexed"] == 3
    assert _env.admitted[-1]["label"].startswith("Reading changes in ")


def test_changes_wait_for_the_pc_to_be_idle_and_are_never_read_at_the_moment_they_happen(tmp_path, _env, monkeypatch):
    from agent_friday.services.library import changes, runtime
    root, ev = _read_folder(tmp_path)
    reads = Reads(monkeypatch)
    (root / "n0.txt").write_text(_text(0, "Rewritten while the owner is at the keyboard."), encoding="utf-8")
    _env.hold_admission = threading.Event()                 # the queue says "not idle yet"
    assert changes.queue("owner", runtime.indexer_for("owner"), ev, _store()) == 1
    time.sleep(0.5)
    assert reads.paths == [], "nothing was read while the queue held the job"
    assert "Rewritten" not in " ".join(b["text"] for b in _store().q("SELECT text FROM blocks"))
    _env.hold_admission.set()                               # the PC went idle
    assert _wait(_settled(root))
    assert reads.paths == ["n0.txt"]
    assert "Rewritten" in " ".join(b["text"] for b in _store().q("SELECT text FROM blocks"))
    assert _env.admitted[-1]["deferrable"] is True


def test_only_the_changed_files_are_read_and_a_file_with_the_same_bytes_is_not(tmp_path, monkeypatch):
    from agent_friday.services.library import changes, runtime
    root, ev = _read_folder(tmp_path, 4)
    reads = Reads(monkeypatch)
    (root / "n1.txt").write_text(_text(1, "Really changed."), encoding="utf-8")
    st = (root / "n2.txt").stat()
    os.utime(root / "n2.txt", (st.st_atime, st.st_mtime + 100))        # touched only
    assert changes.queue("owner", runtime.indexer_for("owner"), ev, _store()) == 1
    assert _wait(_settled(root))
    assert reads.paths == ["n1.txt"], "n0 and n3 were not looked at, n2's bytes were the same"
    doc = _store().find_document(str((root / "n2.txt").resolve()))
    assert doc["mtime"] == (root / "n2.txt").stat().st_mtime, "the touched file's time is brought up to date"
    assert changes.detect(_store(), root) == {"changed": [], "new": [], "gone": []}


def test_a_deleted_file_leaves_the_library_through_the_same_job(tmp_path):
    from agent_friday.services.library import changes, runtime
    root, ev = _read_folder(tmp_path, 3)
    (root / "n2.txt").unlink()
    assert changes.queue("owner", runtime.indexer_for("owner"), ev, _store()) == 1
    assert _wait(_settled(root))
    assert _store().counts()["indexed"] == 2


def test_a_paused_folder_is_left_alone_until_it_is_resumed(tmp_path):
    from agent_friday.services.library import changes, index_job, runtime
    root, ev = _read_folder(tmp_path)
    (root / "late.txt").write_text(_text(77), encoding="utf-8")
    index_job.pause("owner")
    assert changes.queue("owner", runtime.indexer_for("owner"), ev, _store()) == 0
    assert runtime.resweep("owner") == 0
    index_job.progress_for("owner").data["paused"] = False
    assert runtime.resweep("owner") == 1


def test_notes_not_yet_made_by_laya_are_a_reason_for_a_pass_that_reads_no_file(tmp_path, monkeypatch):
    from agent_friday.services.library import changes, index_job, runtime
    root, ev = _read_folder(tmp_path, 3)
    st = _store()
    st.x("DELETE FROM doc_meta")                                   # e.g. a pass that was cancelled in the notes
    assert len(st.untriaged()) == 3
    reads = Reads(monkeypatch)
    assert changes.queue("owner", runtime.indexer_for("owner"), ev, st) == 1
    assert _wait(_settled(root))
    assert reads.paths == [] and st.untriaged() == []


def test_the_idle_sweep_looks_only_while_the_pc_is_idle_and_not_more_than_every_fifteen_minutes(monkeypatch):
    from agent_friday.services.library import runtime
    looked = []
    monkeypatch.setattr(runtime, "resweep", lambda p=None: looked.append(p) or 0)
    monkeypatch.setattr(runtime, "purge_uncovered", lambda p=None: 0)
    monkeypatch.setattr(runtime.grants, "active_scopes", lambda p: [{"path": "x"}])
    runtime._indexers["owner"] = types.SimpleNamespace(stop=lambda: None)
    monkeypatch.setattr(runtime, "_owner_idle", lambda: False)
    assert runtime._sweep_once(0.0) == 0.0 and looked == [], "the owner is here"
    monkeypatch.setattr(runtime, "_owner_idle", lambda: True)
    t = runtime._sweep_once(0.0)
    assert t > 0 and looked == ["owner"]
    assert runtime._sweep_once(t) == t and looked == ["owner"], "not again within the interval"


def test_resweep_stats_instead_of_reading_every_file_again(tmp_path, monkeypatch):
    from agent_friday.services.library import procrun, runtime
    root, _ev = _read_folder(tmp_path)
    monkeypatch.setattr(procrun, "run_task", lambda *a, **k: pytest.fail("a file was read"))
    assert runtime.resweep("owner") == 0
