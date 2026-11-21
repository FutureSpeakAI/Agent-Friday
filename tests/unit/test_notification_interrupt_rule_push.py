"""The interrupt rule, end to end: what a card's tier is after `notifications_engine.push`, and that the
emitters that raise a scheduled delivery name themselves (a manual run does not)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday import notifications_engine as ne
from agent_friday.services import notice_registry, notification_policy as pol

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
            emitter=None, dedupe_key="front-page:manual")
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
        assert re.search(r'emitter\s*=\s*(?:None if manual else )?"%s"' % emitter, text), emitter
        assert emitter in registered
