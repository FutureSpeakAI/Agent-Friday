"""The interrupt rule, end to end: what a card's tier is after `notifications_engine.push`, and that the
emitters that raise a scheduled delivery name themselves (a manual run does not)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import notice_registry, notification_policy as pol
from agent_friday.services import scheduler as scheduler_module

SERVICES = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services"


@pytest.fixture(autouse=True)
def _clean(friday_dir, monkeypatch):
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "_LAST_TURN", [0.0])
    monkeypatch.setattr(ne, "_log_only", lambda entry: None)


def _only_card():
    cards = ne.list_notifications(limit=50)
    assert len(cards) == 1, cards
    return cards[0]


def test_a_scheduled_job_that_ran_is_a_needs_you_card():
    ne.push(title="Start My Day ran", body="Completed.", source="scheduler", kind="scheduled_task",
            priority="low", emitter="E07", dedupe_key="sched-ok:s1")
    assert _only_card()["tier"] == pol.NEEDS_YOU


def test_the_same_words_from_a_waiting_job_stay_a_quiet_card():
    ne.push(title="Start My Day ran", body="Completed.", source="scheduler", kind="scheduled_task",
            priority="low", emitter="E05", dedupe_key="sched-wait:s1")
    assert all(card["tier"] != pol.NEEDS_YOU for card in ne.list_notifications(limit=50))


def test_a_failed_job_is_still_needs_you_as_before():
    ne.push(title="Start My Day failed", body="no seat", source="scheduler", kind="scheduled_failure",
            priority="high", emitter="E04", dedupe_key="sched-fail:s1")
    assert _only_card()["tier"] == pol.NEEDS_YOU


def test_a_delivered_front_page_edition_interrupts():
    ne.push(title="Front Page", body="ready", source="front-page", kind="front_page", priority="medium",
            emitter="E11", dedupe_key="front-page:a")
    assert _only_card()["tier"] == pol.NEEDS_YOU


def test_a_front_page_the_owner_asked_for_does_not():
    ne.push(title="Front Page", body="ready", source="front-page", kind="front_page", priority="medium",
            emitter="E11", tier="fyi", dedupe_key="front-page:manual")
    assert _only_card()["tier"] == pol.FYI


def test_the_daily_creation_waiting_on_you_is_not_the_creation_being_made():
    ne.push(title="Today's production is waiting on you", body="paused", source="daily-creation",
            kind="creation", priority="medium", dedupe_key="daily-creation-pending:r1")
    assert _only_card()["tier"] == pol.FYI


@pytest.mark.parametrize("rule_id, module", [("front_page", "news_engine.py"), ("daily_briefing", "news_engine.py"),
                                             ("weekly_editions", "news_engine.py"),
                                             ("daily_creation", "creations.py"), ("scheduled_runs", "scheduler.py")])
def test_each_emitter_a_rule_names_passes_its_own_id_where_it_pushes(rule_id, module):
    rule = next(r for r in pol.INTERRUPT_RULES if r.id == rule_id)
    text = (SERVICES / module).read_text(encoding="utf-8")
    registered = {e.id for e in notice_registry._ALL}
    for emitter in rule.emitters:
        assert re.search(r'emitter\s*=\s*"%s"' % emitter, text), emitter
        assert emitter in registered


# -- a needs-you success must not hide the job's next failure ---------------------

def test_dismissing_a_ran_card_does_not_hide_the_jobs_next_failure():
    ne.push(title="Nightly sync ran", body="Completed.", source="scheduler", kind="scheduled_task",
            priority="low", emitter="E07", dedupe_key="sched-ok:nightly")
    ne.dismiss_all()
    out = ne.push(title="Nightly sync failed", body="no seat", source="scheduler", kind="scheduled_failure",
                  priority="high", emitter="E04", dedupe_key="sched-fail:nightly",
                  resolve_key="sched-fail:nightly")
    assert not out.get("suppressed") and not out.get("dismissed"), out
    assert [c["kind"] for c in ne.list_notifications(limit=50)] == ["scheduled_failure"]


# -- one event must not interrupt twice ---------------------------------------

def _scheduler(monkeypatch):
    from agent_friday.services import scheduler
    from agent_friday.services import voice_engine
    monkeypatch.setattr(voice_engine, "_notif_engine", ne, raising=False)
    monkeypatch.setattr(scheduler, "_setup_finished", lambda: True)
    return scheduler


def test_an_ordinary_scheduled_job_that_ran_is_a_needs_you_card(monkeypatch):
    _scheduler(monkeypatch)._notify_run({"id": "sch_nightly_sync", "name": "Nightly sync"}, "complete", "ok")
    assert _only_card()["tier"] == pol.NEEDS_YOU


@pytest.mark.parametrize("schedule_id", sorted(scheduler_module.OWN_DELIVERY_NOTICE))
def test_a_routine_whose_delivery_has_its_own_notice_keeps_a_quiet_ran_card(monkeypatch, schedule_id):
    _scheduler(monkeypatch)._notify_run({"id": schedule_id, "name": "Routine"}, "complete", "ok")
    assert _only_card()["tier"] == pol.FYI


def test_the_heartbeat_is_not_a_routine_with_its_own_notice(monkeypatch):
    from agent_friday.services import scheduler
    assert "sch_heartbeat" not in scheduler.OWN_DELIVERY_NOTICE


# -- a dismissed ran-card is news again at the next run; a quiet one stays dismissed ----------

def test_a_dismissed_ran_card_that_interrupts_comes_back_at_the_next_run(monkeypatch):
    scheduler = _scheduler(monkeypatch)
    rec = {"id": "sch_nightly_sync", "name": "Nightly sync"}
    scheduler._notify_run(rec, "complete", "ok")
    ne.dismiss_all()
    assert ne.list_notifications(limit=50) == []
    scheduler._notify_run(rec, "complete", "ok")
    assert _only_card()["tier"] == pol.NEEDS_YOU


def test_a_dismissed_quiet_ran_card_stays_dismissed(monkeypatch):
    scheduler = _scheduler(monkeypatch)
    rec = {"id": "sch_front_page_evening", "name": "Front Page"}
    scheduler._notify_run(rec, "complete", "ok")
    ne.dismiss_all()
    scheduler._notify_run(rec, "complete", "ok")
    assert ne.list_notifications(limit=50) == []


def test_a_news_run_the_owner_asked_for_names_a_quiet_tier_at_every_delivery_push():
    text = (SERVICES / "news_engine.py").read_text(encoding="utf-8")
    for emitter in ("E11", "E12", "E13", "E14"):
        assert re.search(r'emitter\s*=\s*"%s",\s*tier\s*=\s*"fyi" if manual else None' % emitter, text), emitter
