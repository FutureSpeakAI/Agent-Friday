"""The panels answer in time (north star §34.15-2, UI responsiveness at the
API): the requests the desktop polls most often, with some data in the
stores, each stay under a wide p95 ceiling on a shared CI runner."""
from __future__ import annotations

import time

import pytest

PANELS = ("/api/health", "/api/notifications", "/api/approvals", "/api/tasks")


@pytest.fixture
def some_data(tmp_path, monkeypatch):
    import agent_friday.notifications_engine as ne
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    for i in range(150):
        approvals.create_approval(kind="tool_confirm", subject_type="tool", subject_id=f"latency-{i}",
                                  title=f"Synthetic card {i}")
    for i in range(100):
        ne.push(title=f"Synthetic task {i} not recorded", source="task-journal", kind="task_unrecorded",
                dedupe_key=f"task_unrecorded:latency-{i}")


@pytest.mark.parametrize("url", PANELS)
def test_a_polled_panel_answers_in_time(client, some_data, url):
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        r = client.get(url)
        times.append(time.perf_counter() - t0)
        assert r.status_code < 500, (url, r.status_code)
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    print(f"[perf] {url}: median {times[len(times) // 2] * 1000:.1f} ms, p95 {p95 * 1000:.1f} ms")
    assert p95 < 2.0, f"{url} p95 {p95:.2f}s"
