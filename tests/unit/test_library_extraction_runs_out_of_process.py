"""A hostile document cannot hang or bloat the server: extraction runs in a
child that is killed at a wall clock and held under a memory cap, and the
calling thread comes back with a named failure."""
from __future__ import annotations

import time

import pytest

from agent_friday.services.library import procrun
from tests.library_fixtures import make_pdf


def test_a_hanging_task_is_killed_at_the_wall_clock(monkeypatch):
    monkeypatch.setenv("FRIDAY_LIBRARY_SELFTEST", "1")
    t0 = time.monotonic()
    with pytest.raises(procrun.TaskFailed) as ei:
        procrun.run_task("_selftest_sleep", {"seconds": 60}, wall_s=2)
    assert ei.value.kind == "timeout"
    assert time.monotonic() - t0 < 15


def test_a_task_over_the_memory_cap_is_stopped(monkeypatch):
    monkeypatch.setenv("FRIDAY_LIBRARY_SELFTEST", "1")
    with pytest.raises(procrun.TaskFailed) as ei:
        procrun.run_task("_selftest_alloc", {"mb": 1500}, wall_s=30, memory_mb=300)
    assert ei.value.kind in ("memory", "failed")


def test_extraction_result_comes_back_from_the_child(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(make_pdf([["# Title", "Body words."]]))
    res = procrun.run_task("extract", {"path": str(p)})
    assert res["pages"] == 1 and res["blocks"][0]["text"] == "Title"


def test_the_child_does_not_import_the_flask_app(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(make_pdf([["Body."]]))
    from agent_friday.services.library import worker
    import sys
    before = "agent_friday.core" in sys.modules
    procrun.run_task("extract", {"path": str(p)})
    assert ("agent_friday.core" in sys.modules) == before
    src = (procrun.Path(worker.__file__)).read_text(encoding="utf-8")
    assert "agent_friday.core" not in src and "import flask" not in src
