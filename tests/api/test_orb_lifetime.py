"""Green orbs leave after 30 seconds. Failed ones do not leave on a timer.

Green ('done') process orbs leave the holographic desktop 30 seconds after
they finish; they do not stay in orbit around Friday's avatar.

Every agent and inference orb registers with category 'monitoring', which has
a 900-second RECORD retention. The record's long life is deliberate (the detail
has to outlive the orb); drawing the orb for as long as the record lives is
not — one lifetime must not do two jobs.

The asymmetry for failures is a judgement call and it is the one this codebase
keeps making: a success that vanishes is fine, because you either saw it or did
not need to. A failure that vanishes before you looked at it is the machine
hiding its own bad news.
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


# ── the 30 seconds ───────────────────────────────────────────────────────────

def test_a_running_orb_is_visible(client):
    _add("p1", status="running")
    assert _rows(client)["p1"]["orb_visible"] is True


def test_a_just_finished_orb_is_still_visible(client):
    _add("p1", status="completed", ended_ago=5)
    assert _rows(client)["p1"]["orb_visible"] is True


def test_a_completed_orb_stops_orbiting_after_30s(client):
    _add("p1", status="completed", ended_ago=45)
    assert _rows(client)["p1"]["orb_visible"] is False


def test_monitoring_category_does_not_buy_extra_orbit_time(client):
    """'monitoring' buys 900s of RECORD; the orb must not ride along for all
    of it."""
    _add("p1", status="completed", ended_ago=120, category="monitoring")
    assert _rows(client)["p1"]["orb_visible"] is False


def test_the_record_outlives_the_orb(client):
    """The detail — model, log, result — has to stay explorable after the orb
    goes."""
    _add("p1", status="completed", ended_ago=120, category="monitoring")
    row = _rows(client)["p1"]
    assert row["orb_visible"] is False
    assert row["model"] == "gemma4:12b"      # still returned, still explorable


# ── failures do not vanish on a timer ────────────────────────────────────────

@pytest.mark.parametrize("status", ["error", "failed", "timeout", "cancelled"])
def test_a_failure_does_not_vanish_on_the_success_timer(client, status):
    """A failure outlives the 30s success timer, so it cannot disappear before
    the user has looked at it."""
    _add("p1", status=status, ended_ago=120)
    row = _rows(client)["p1"]
    assert row["orb_visible"] is True
    assert row["orb_failed"] is True


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


def test_a_failure_leaves_only_when_it_is_dismissed(client):
    _add("p1", status="error", ended_ago=3600, dismissed=True)
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
