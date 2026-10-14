"""The tray holds what needs the owner and the results of things the owner
started or scheduled. A scheduled job's finish is announced by default (the
owner opts out per job; tests/unit/test_scheduler.py). Background starts and
waits (the Activity place) live in Activity, not as tray cards. An FYI card
leaves the tray after a day, or as soon as it is dismissed.
"""
from __future__ import annotations

import json
import time

import pytest

import agent_friday.notifications_engine as ne


@pytest.fixture(autouse=True)
def clean_queue(monkeypatch):
    from agent_friday.services import notification_policy as _pol
    monkeypatch.setattr(_pol, "owner_in_conversation", lambda now=None: False)
    for f in (ne.NOTIF_FILE, ne.TRIGGER_STATE_FILE):
        if f.exists():
            f.unlink()
    yield
    for f in (ne.NOTIF_FILE, ne.TRIGGER_STATE_FILE):
        if f.exists():
            f.unlink()


def _titles():
    return [n["title"] for n in ne.list_notifications(limit=200)]


def _stored():
    raw = json.loads(ne.NOTIF_FILE.read_text(encoding="utf-8")) if ne.NOTIF_FILE.exists() else []
    return raw.get("items", []) if isinstance(raw, dict) else raw


def test_a_scheduled_job_finishing_is_a_tray_card():
    out = ne.push(title="✓ Hourly heartbeat ran", body="Completed.", priority="low",
                  source="scheduler", kind="scheduled_task", emitter="E07",
                  dedupe_key="sched-ok:sch_heartbeat")
    assert not out.get("logged") and out.get("place") == "updates"
    assert _titles() == ["✓ Hourly heartbeat ran"]
    assert ne.unread_count() == 0          # an FYI result, not a "needs you"


def test_a_job_waiting_for_the_local_seat_is_not_a_tray_card():
    ne.push(title="⏳ Daily creation is waiting for the local seat", priority="low",
            source="scheduler", kind="scheduled_task", emitter="E05",
            dedupe_key="sched-wait:sch_creation")
    assert _titles() == []


def test_the_heartbeat_status_is_one_card_in_the_tray():
    for _ in range(3):
        ne.upsert_status(key="sched-status:sch_heartbeat", title="Heartbeat last ran",
                         source="scheduler", kind="scheduled_status", emitter="E08")
    assert _titles() == ["Heartbeat last ran"]


def test_paused_jobs_still_reach_the_tray():
    # The owner can act on it (Settings > Spending), so it is not routine.
    ne.upsert_status(key="scheduled-cloud-paused", title="Scheduled jobs are paused",
                     source="scheduler", kind="scheduled_status", emitter="E10")
    assert _titles() == ["Scheduled jobs are paused"]


def test_results_and_failures_still_reach_the_tray():
    ne.push(title="Afternoon briefing failed", priority="high", source="scheduler",
            kind="scheduled_failure", emitter="E04", dedupe_key="sched-fail:sch_pm")
    ne.push(title="Queued work finished", emitter="E09", dedupe_key="queue-drained")
    ne.push(title="Task finished: research", dedupe_key="task-done:t1", emitter="E02")
    assert set(_titles()) == {"Afternoon briefing failed", "Queued work finished",
                              "Task finished: research"}


def test_a_legacy_waiting_card_on_disk_leaves_the_tray():
    # Stored before cards carried a place: the registry places it ("waiting for
    # the local seat" is the first scheduler emitter of its kind, in Activity).
    ne.NOTIF_FILE.write_text(json.dumps({"items": [{
        "id": "old-wait", "title": "Daily creation is waiting for the local seat",
        "kind": "scheduled_task", "source": "scheduler", "priority": "low", "read": True,
        "dismissed": False, "created_at": ne._now_iso(),
        "dedupe_key": "sched-wait:sch_creation"}],
        "dismissed_jobs": {}}), encoding="utf-8")
    assert _titles() == []


def test_an_fyi_card_expires_after_a_day_but_needs_you_does_not():
    ne.push(title="Front page is ready", source="front-page", kind="front_page")
    ne.push(title="Relationship sync failed", priority="high", source="scheduler",
            kind="scheduled_failure", emitter="E04", dedupe_key="sched-fail:sch_rel")
    ne.push(title="Approval needed: send the email", source="approvals",
            kind="approval_pending", dedupe_key="approval:a1")
    assert len(_titles()) == 3
    later = time.time() + ne.FYI_TTL_S + 60
    kept = [n for n in _stored() if ne._in_tray(n, now=later)]
    assert {n["title"] for n in kept} == {"Relationship sync failed",
                                         "Approval needed: send the email"}


def test_a_fresh_fyi_card_is_in_the_tray():
    ne.push(title="Your daily briefing is ready", source="briefing", kind="briefing")
    assert _titles() == ["Your daily briefing is ready"], _stored()


def test_a_result_leaves_the_tray_when_dismissed_before_its_day_is_up():
    card = ne.push(title="Your podcast is ready", source="podcasts", kind="info")
    assert _titles() == ["Your podcast is ready"]
    ne.dismiss(card["id"])
    assert _titles() == []


def test_seen_is_not_read_and_not_dismissed():
    card = ne.push(title="Front page is ready", source="front-page", kind="front_page",
                   target={"workspace": "news", "tab": "frontpage"})
    assert ne.mark_seen([card["id"], "no-such-card"]) == 1
    (n,) = ne.list_notifications()
    assert n["seen"] is True and not n.get("dismissed")
    assert ne.mark_seen([card["id"]]) == 0
