"""Unit tests for services/work_log.py"""
import time
import uuid
import pytest

from agent_friday.services import work_log as wl

wl._ensure_schema()


def _fake_task(task_type="CUSTOM", workspace="test-ws"):
    class T:
        task_id = str(uuid.uuid4())
        prompt = "unit test prompt"
        adapter_type = None
        context = {"workspace": workspace}
    t = T()
    t.task_type = type("TT", (), {"value": task_type})()
    return t


def _fake_result(status="COMPLETED", tokens=100, cost=500):
    class R:
        pass
    r = R()
    r.status = type("S", (), {"value": status})()
    r.tokens_used = tokens
    r.cost_mψ = cost
    r.quality_score = 0.8
    r.error = None
    r.artifacts = []
    return r


class TestLogStart:
    def test_log_start_returns_work_id(self):
        task = _fake_task()
        work_id = wl.log_start(task)
        assert work_id
        assert len(work_id) == 36

    def test_log_start_creates_entry(self):
        task = _fake_task()
        work_id = wl.log_start(task)
        entry = wl.get_entry(work_id)
        assert entry is not None
        assert entry["task_id"] == task.task_id

    def test_log_start_status_running(self):
        task = _fake_task()
        work_id = wl.log_start(task)
        entry = wl.get_entry(work_id)
        assert entry["status"] == "RUNNING"

    def test_log_start_records_workspace(self):
        task = _fake_task(workspace="finance-ws")
        work_id = wl.log_start(task)
        entry = wl.get_entry(work_id)
        assert entry["workspace"] == "finance-ws"


class TestLogFinish:
    def test_log_finish_updates_status(self):
        task = _fake_task()
        work_id = wl.log_start(task)
        result = _fake_result("COMPLETED")
        wl.log_finish(task, result)
        entry = wl.get_entry(work_id)
        assert entry["status"] == "COMPLETED"

    def test_log_finish_records_tokens(self):
        task = _fake_task()
        wl.log_start(task)
        result = _fake_result(tokens=200)
        wl.log_finish(task, result)

    def test_log_finish_failed_status(self):
        task = _fake_task()
        work_id = wl.log_start(task)
        result = _fake_result("FAILED")
        result.error = "test error"
        wl.log_finish(task, result)
        entry = wl.get_entry(work_id)
        assert entry["status"] == "FAILED"

    def test_log_finish_no_matching_start(self):
        # Should not raise
        task = _fake_task()  # never logged start
        result = _fake_result()
        wl.log_finish(task, result)  # no-op


class TestGetLog:
    def test_get_log_returns_list(self):
        entries = wl.get_log(limit=10)
        assert isinstance(entries, list)

    def test_get_log_respects_limit(self):
        # Create several entries
        for _ in range(5):
            t = _fake_task()
            wl.log_start(t)
        entries = wl.get_log(limit=3)
        assert len(entries) <= 3

    def test_get_log_filter_workspace(self):
        ws = f"ws-filter-{uuid.uuid4().hex[:6]}"
        task = _fake_task(workspace=ws)
        wl.log_start(task)
        entries = wl.get_log(workspace=ws)
        assert all(e["workspace"] == ws for e in entries)

    def test_get_log_entries_are_dicts(self):
        entries = wl.get_log(limit=5)
        for e in entries:
            assert isinstance(e, dict)


class TestGetEntry:
    def test_get_entry_not_found(self):
        assert wl.get_entry("no-such-work-id") is None

    def test_get_entry_has_required_fields(self):
        task = _fake_task()
        work_id = wl.log_start(task)
        entry = wl.get_entry(work_id)
        for field in ("work_id", "task_id", "status", "started_at"):
            assert field in entry


class TestDeleteOld:
    def test_delete_old_entries_returns_int(self):
        count = wl.delete_old_entries(days=9999)  # nothing old enough
        assert isinstance(count, int)
        assert count >= 0
