"""BD-16 / NS-11.3-8: Friday is asked to get ready before she is stopped, and the next start
tells the owner's close from a crash.

Before: the tray terminated the server (on Windows that runs no exit handler), every exit
looked the same at the next start ("process restarted while the task was running"), and a
journal line torn by the stop swallowed the next event appended after it. Now the tray asks
first (POST /api/server/prepare-for-exit: flush, stop add-ons and browser sessions, mark a
clean exit); the next start says "Friday was closed" or "Friday stopped without being closed";
and an append after a torn line starts on a fresh line. Synthetic tasks only.
"""
from __future__ import annotations

import json
import sys
import time

import pytest

from agent_friday.services import task_journal as tj


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", str(tmp_path / "tasks"))
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw) or {"id": "n1"})
    return pushed


def _running(tid="run-task-0001", seen_ago=30.0):
    tj.append(tid, "created", name="Weekly report", prompt="draft the report")
    tj.append(tid, "started")
    tj.append(tid, "checkpoint", summary="Step 2 of 4")
    tj.write_state(tid, {"task_id": tid, "name": "Weekly report", "status": "running",
                         "created": time.time() - 120, "started": time.time() - 100, "result": ""})
    tj.index_put(tid, "Weekly report", "running", time.time() - 120)
    return tid


def _halt(tid):
    return [e for e in tj.read(tid) if e["kind"] == "halt"][-1]


def test_a_stop_nobody_asked_for_is_called_what_it_was(journal):
    tid = _running()
    tj.reconcile_on_boot()
    assert "stopped without being closed" in _halt(tid)["detail"]


def test_a_close_is_told_from_a_crash(journal):
    tid = _running()
    assert tj.mark_clean_exit("tray_quit") is True
    out = tj.reconcile_on_boot()
    assert out["previous_exit"]["kind"] == "closed"
    assert _halt(tid)["detail"].startswith("Friday was closed")
    tj.announce_interrupted(out["interrupted"], out["previous_exit"])
    assert journal[-1]["title"] == "1 task was stopped when Friday was closed"
    assert not (tj.tasks_dir() / tj.EXIT_MARKER).exists(), "the marker describes one exit only"


def test_a_restart_says_restarted(journal):
    tid = _running()
    tj.mark_clean_exit("tray_restart", planned=True)
    tj.reconcile_on_boot()
    assert _halt(tid)["detail"].startswith("Friday was restarted")


def test_a_marker_written_while_work_went_on_is_not_believed(journal):
    tid = _running()
    tj.mark_clean_exit("tray_quit")
    m = json.loads((tj.tasks_dir() / tj.EXIT_MARKER).read_text(encoding="utf-8"))
    m["ts"] = time.time() - 600            # long before the task's last sign of life
    (tj.tasks_dir() / tj.EXIT_MARKER).write_text(json.dumps(m), encoding="utf-8")
    out = tj.reconcile_on_boot()
    assert out["previous_exit"]["kind"] == "unexpected"
    assert "stopped without being closed" in _halt(tid)["detail"]


def test_an_append_after_a_torn_line_is_kept(journal):
    tid = "torn-task-0001"
    tj.append(tid, "created", name="Torn")
    p = tj.task_dir(tid) / "journal.jsonl"
    with open(p, "ab") as fh:
        fh.write(b'{"seq": 2, "kind": "checkpoint", "summ')      # the stop cut this line
    tj._SEQ.pop(tid, None)
    tj.append(tid, "checkpoint", summary="after the stop")
    kinds = [(e["kind"], e.get("summary")) for e in tj.read(tid)]
    assert ("checkpoint", "after the stop") in kinds, kinds


@pytest.mark.skipif(sys.platform != "win32", reason="the tray (pystray) runs on Windows only")
def test_the_tray_asks_before_it_stops_the_server(monkeypatch):
    from agent_friday import friday_tray as ft
    calls = []

    class _Stub:
        icon = None
        _restart_in_flight = __import__("threading").Lock()

        def _prepare_for_exit(self, reason, planned=False):
            calls.append(("prepare", reason, planned))

        def _release_seats(self, reason):
            calls.append(("release", reason))

        def stop_server(self):
            calls.append(("stop",))

        def start_server(self):
            calls.append(("start",))

    ft.FridayTray._quit(_Stub(), None, None)
    assert calls == [("release", "tray_quit"), ("prepare", "tray_quit", False), ("stop",)], \
        "the clean-exit record is written right before the stop"
    calls.clear()
    monkeypatch.setattr(ft.time, "sleep", lambda s: None)
    ft.FridayTray.restart_server(_Stub())
    assert calls[:2] == [("prepare", "tray_restart", True), ("stop",)]


@pytest.mark.skipif(sys.platform != "win32", reason="the tray (pystray) runs on Windows only")
def test_a_server_the_tray_did_not_start_is_not_told_to_get_ready(monkeypatch):
    from agent_friday import friday_tray as ft
    sent = []
    monkeypatch.setattr(ft.urllib.request, "urlopen", lambda *a, **k: sent.append(a) or None)

    class _Adopted:
        server_proc = None
        server_url = "http://127.0.0.1:1"
    ft.FridayTray._prepare_for_exit(_Adopted(), "tray_quit")
    assert sent == [], "an adopted server keeps running; it is not told it is about to stop"


def test_an_index_row_after_a_torn_line_is_kept(journal):
    tj.index_put("a-task-0001", "First", "running", time.time())
    with open(tj._index_path(), "ab") as fh:
        fh.write(b'{"task_id": "torn-0002", "na')
    tj.index_put("b-task-0003", "Second", "running", time.time())
    assert "b-task-0003" in tj.index_read()
