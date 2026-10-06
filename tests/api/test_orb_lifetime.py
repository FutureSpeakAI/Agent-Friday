"""Completed process orbs drift for five minutes or until explicitly cleared.

Their records cover that visible lifetime; monitoring records and failures
remain explorable for longer under their independent retention policies.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.core import PROCESSES, PROCESSES_LOCK, process_register


@pytest.fixture(autouse=True)
def clean_processes():
    with PROCESSES_LOCK:
        before = dict(PROCESSES)
        PROCESSES.clear()
    yield
    with PROCESSES_LOCK:
        PROCESSES.clear()
        PROCESSES.update(before)


def _add(pid, *, status, ended_ago=None, category="monitoring",
         label="Hourly heartbeat", dismissed=False):
    process_register(pid, name="Friday", label=label, category=category,
                     icon="⏰", steps=[], model="gemma4:12b")
    with PROCESSES_LOCK:
        p = PROCESSES[pid]
        p["status"] = status
        p["started"] = time.time() - 120
        if ended_ago is not None:
            p["ended"] = time.time() - ended_ago
        if dismissed:
            p["dismissed"] = True


def _rows(client):
    return {p["id"]: p for p in client.get("/api/processes").get_json()["processes"]}


# ── the five minutes ───────────────────────────────────────────────────────────

def test_a_running_orb_is_visible(client):
    _add("p1", status="running")
    assert _rows(client)["p1"]["orb_visible"] is True


def test_a_just_finished_orb_is_still_visible(client):
    _add("p1", status="completed", ended_ago=5)
    assert _rows(client)["p1"]["orb_visible"] is True


def test_a_completed_orb_stops_orbiting_after_five_minutes(client):
    _add("p1", status="completed", ended_ago=301)
    assert _rows(client)["p1"]["orb_visible"] is False


def test_monitoring_category_does_not_buy_extra_orbit_time(client):
    """'monitoring' buys 900s of RECORD; the orb must not ride along for all
    of it."""
    _add("p1", status="completed", ended_ago=600, category="monitoring")
    assert _rows(client)["p1"]["orb_visible"] is False


def test_the_record_outlives_the_orb(client):
    """The detail — model, log, result — has to stay explorable after the orb
    goes."""
    _add("p1", status="completed", ended_ago=600, category="monitoring")
    row = _rows(client)["p1"]
    assert row["orb_visible"] is False
    assert row["model"] == "gemma4:12b"      # still returned, still explorable


# ── failures retain their status and records ────────────────────────────────────────

@pytest.mark.parametrize("status", ["error", "failed", "timeout", "cancelled", "interrupted"])
@pytest.mark.parametrize("ended_ago,visible", [(5, True), (299, True), (300, False), (600, False)])
def test_failure_visibility_ends_at_five_minutes_but_the_error_remains(client, status, ended_ago, visible):
    _add("p1", status=status, ended_ago=ended_ago)
    for _ in range(2):
        row = _rows(client)["p1"]
        assert row["orb_visible"] is visible
        assert row["orb_failed"] is True
        assert row["status"] == status


def test_a_very_old_failure_stops_orbiting_but_is_still_reported(client):
    """Error orbs must eventually go away.

    Failures that persist until dismissed, with nothing able to dismiss them,
    become permanent and accumulate around the avatar. An age cap ends the
    orbit; the ROW is still
    returned and still flagged, so nothing is hidden — it has just stopped
    standing in front of the work in progress.
    """
    _add("p1", status="error", ended_ago=7200)
    row = _rows(client)["p1"]
    assert row["orb_visible"] is False
    assert row["orb_failed"] is True          # still reported, still readable


def test_failures_cannot_swarm_the_ring(client):
    """Twenty failed orbits is not twenty times the information."""
    from agent_friday.routes.tasks import ORB_FAILED_MAX_VISIBLE
    for i in range(ORB_FAILED_MAX_VISIBLE + 4):
        _add("p%d" % i, status="error", ended_ago=60 + i)
    rows = _rows(client)
    visible = [r for r in rows.values() if r.get("orb_visible")]
    assert len(visible) == ORB_FAILED_MAX_VISIBLE
    # The newest survive — the oldest are the ones already seen.
    assert all(r["orb_failed"] for r in visible)
    assert len(rows) == ORB_FAILED_MAX_VISIBLE + 4      # all still reported


def test_dismissal_removes_a_failure_before_its_deadline(client):
    _add("p1", status="error", ended_ago=5, dismissed=True)
    assert _rows(client)["p1"]["orb_visible"] is False


def test_dismissing_is_an_explicit_act(client):
    _add("p1", status="error", ended_ago=60)
    assert _rows(client)["p1"]["orb_visible"] is True
    assert client.post("/api/processes/p1/dismiss").get_json()["ok"] is True
    assert _rows(client)["p1"]["orb_visible"] is False


def test_dismissing_something_that_is_not_there_is_a_404(client):
    assert client.post("/api/processes/nope/dismiss").status_code == 404


# ── the label ────────────────────────────────────────────────────────────────

def test_the_orb_carries_its_description_not_a_status_word(client):
    """Every finished orb used to read "Done (gemma4:12b)" — and the scene
    appends its own model badge, so it rendered the model twice and the task
    never. An orb should say what it IS; done is carried by status and colour.
    """
    _add("p1", status="completed", ended_ago=5, label="Hourly heartbeat")
    row = _rows(client)["p1"]
    assert row["label"] == "Hourly heartbeat"
    assert "Done" not in row["label"]
    assert row["model"] not in row["label"], \
        "the model has its own badge; putting it in the label prints it twice"


@pytest.mark.parametrize("category", ["default", "creative", "monitoring"])
@pytest.mark.parametrize("status", ["completed", "error", "failed", "timeout", "cancelled", "interrupted"])
def test_every_finished_process_remains_until_its_original_deadline(client, monkeypatch, category, status):
    from agent_friday.routes import tasks
    now = time.time()
    monkeypatch.setattr(tasks._time, "time", lambda: now)
    _add("p1", status=status, ended_ago=299, category=category)
    assert _rows(client)["p1"]["orb_visible"] is True
    assert _rows(client)["p1"]["orb_visible"] is True  # polling never deletes it early
    monkeypatch.setattr(tasks._time, "time", lambda: now + 1)
    assert _rows(client)["p1"]["orb_visible"] is False


def test_clear_finished_hides_successes_and_failures_without_hiding_live_work(client):
    _add("success", status="completed", ended_ago=5)
    _add("failure", status="error", ended_ago=5)
    _add("working", status="running")
    _add("queued", status="queued")
    _add("waiting", status="awaiting_approval")
    cleared = client.post("/api/orbs/clear", json={"scope": "finished"}).get_json()
    assert set(cleared["ids"]) == {"success", "failure"}
    for _ in range(2):
        rows = _rows(client)
        assert rows["success"]["orb_visible"] is False
        assert rows["failure"]["orb_visible"] is False
        assert rows["working"]["orb_visible"] is True
        assert rows["queued"]["orb_visible"] is True
        assert rows["waiting"]["orb_visible"] is True


def test_an_individually_dismissed_success_does_not_reappear(client):
    _add("p1", status="completed", ended_ago=5)
    assert client.post("/api/processes/p1/dismiss").get_json()["ok"] is True
    assert _rows(client)["p1"]["orb_visible"] is False


@pytest.mark.parametrize("status", ["completed", "error", "failed", "timeout", "cancelled", "interrupted"])
def test_a_terminal_status_without_an_end_time_still_has_a_fixed_deadline(client, monkeypatch, status):
    from agent_friday.routes import tasks
    now = time.time()
    monkeypatch.setattr(tasks._time, "time", lambda: now)
    _add("p1", status=status)
    assert _rows(client)["p1"]["ended"] == now
    monkeypatch.setattr(tasks._time, "time", lambda: now + 301)
    row = _rows(client)["p1"]
    assert row["ended"] == now
    assert row["orb_visible"] is False
    assert row["status"] == status
