"""A task's state is never read as missing because another thread was replacing it.

On Windows a file that is being replaced (os.replace) or read cannot be opened or
replaced for a moment: a sharing violation, raised as PermissionError. The task
heartbeat rewrites state.json while readers read it, so a read that met the
replace used to come back None, as if the task had no state. A short violation is
retried; one that lasts is still reported.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_friday.services import task_journal as tj

TID = "sharing-task-0001"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(tj, "_SHARING_SLEEP_S", 0.001, raising=False)
    tj.reset_for_tests()
    yield
    tj.reset_for_tests()


def _busy_then(real, failures):
    calls = {"n": 0}

    def f(*a, **k):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(*a, **k)
    return f, calls


def test_a_state_read_that_meets_a_replace_in_progress_is_retried(monkeypatch):
    assert tj.write_state(TID, {"task_id": TID, "status": "running"})
    busy, calls = _busy_then(Path.read_bytes, 2)
    monkeypatch.setattr(Path, "read_bytes", busy)
    st = tj.read_state(TID)
    assert st is not None and st["status"] == "running"
    assert calls["n"] == 3


def test_a_blob_read_is_retried_the_same_way(monkeypatch):
    assert tj.write_blob(TID, "result.json", {"answer": 42})
    busy, calls = _busy_then(Path.read_bytes, 1)
    monkeypatch.setattr(Path, "read_bytes", busy)
    assert tj.read_blob(TID, "result.json") == {"answer": 42}
    assert calls["n"] == 2


def test_a_replace_blocked_by_a_reader_is_retried(monkeypatch):
    busy, calls = _busy_then(os.replace, 2)
    monkeypatch.setattr(tj.os, "replace", busy)
    assert tj.write_state(TID, {"task_id": TID, "status": "running"})
    assert calls["n"] == 3
    assert tj.read_state(TID)["status"] == "running"


def test_a_violation_that_lasts_is_still_reported(monkeypatch):
    assert tj.write_state(TID, {"task_id": TID, "status": "running"})

    def always(*a, **k):
        raise PermissionError(32, "busy")
    monkeypatch.setattr(Path, "read_bytes", always)
    assert tj.read_state(TID) is None
