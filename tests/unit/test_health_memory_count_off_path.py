"""A health computation never walks the memory tree.

``memory_entries`` (the number of .json files anywhere under the memory
directory) is counted on a background thread at most every few minutes, and
the health computation reports the last count. A recursive glob on the
request path is what queued every /api/health caller once tracing slowed it.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


@pytest.fixture
def memory_home(tmp_path, monkeypatch):
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import provider_health, health_check, capability_preflight

    monkeypatch.setattr(provider_health, "inference_health",
                        lambda: {"status": "ok", "providers": []})
    monkeypatch.setattr(health_check, "boot_critical_report", lambda **k: {
        "health_schema_version": 1, "boot_critical_ok": True, "boot_status": "ok",
        "subsystems": {}, "deployment": "test"})
    monkeypatch.setattr(capability_preflight, "status",
                        lambda: {"missing_required": [], "detail": "ok"})
    mem = tmp_path / "memory"
    (mem / "a" / "b").mkdir(parents=True)
    (mem / "one.json").write_text("{}", encoding="utf-8")
    (mem / "a" / "two.json").write_text("{}", encoding="utf-8")
    (mem / "a" / "b" / "three.json").write_text("{}", encoding="utf-8")
    (mem / "a" / "note.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cr, "FRIDAY_DIR", tmp_path)
    reset = getattr(cr, "_reset_memory_count_for_tests", None)
    if reset:
        reset()
    yield cr, mem
    if reset:
        reset()


def test_a_health_computation_does_not_glob_the_memory_directory(memory_home, monkeypatch):
    cr, mem = memory_home
    here = threading.get_ident()
    walked = []
    real_rglob, real_glob = Path.rglob, Path.glob

    def _rglob(self, pattern, *a, **k):
        if threading.get_ident() == here and str(self).startswith(str(mem)):
            walked.append(("rglob", str(self), pattern))
        return real_rglob(self, pattern, *a, **k)

    def _glob(self, pattern, *a, **k):
        if threading.get_ident() == here and str(self).startswith(str(mem)):
            walked.append(("glob", str(self), pattern))
        return real_glob(self, pattern, *a, **k)

    monkeypatch.setattr(Path, "rglob", _rglob)
    monkeypatch.setattr(Path, "glob", _glob)
    cr._health_payload()
    cr._health_payload()
    assert walked == [], "the health computation walked the memory tree: %r" % walked


def test_memory_entries_still_counts_every_json_file_under_memory(memory_home):
    cr, mem = memory_home
    deadline = time.monotonic() + 10.0
    value = cr._health_payload()["memory_entries"]
    while value is None and time.monotonic() < deadline:
        time.sleep(0.05)
        value = cr._health_payload()["memory_entries"]
    assert value == 3


def test_the_count_is_refreshed_at_most_every_few_minutes(memory_home, monkeypatch):
    cr, mem = memory_home
    cr._memory_entries_refresh_now()
    assert cr._health_payload()["memory_entries"] == 3
    (mem / "four.json").write_text("{}", encoding="utf-8")
    assert cr._health_payload()["memory_entries"] == 3
    monkeypatch.setattr(cr, "_MEMORY_COUNT_TTL_S", 0.0)
    deadline = time.monotonic() + 10.0
    value = cr._health_payload()["memory_entries"]
    while value != 4 and time.monotonic() < deadline:
        time.sleep(0.05)
        value = cr._health_payload()["memory_entries"]
    assert value == 4
