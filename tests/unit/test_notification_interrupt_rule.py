"""Stephen's interrupt rule: the successful completion or delivery of a scheduled task or routine
may interrupt; status and routing notices and news do not; failures keep today's behaviour.

The rule keys on the notice's emitter (which names what happened and how it ended), never on its
words. His own 12 notification answers are the fixture."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import notice_registry, notification_policy as pol, triage_heads

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "notify_interrupt_labels.json"
LABELS = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Rule settings come from here; the owner's own settings file is never read."""
    store = {}
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: store)
    return store


def _tier(row, text=None):
    return pol.tier_for(row["kind"], row["source"], row["priority"], None, text, emitter=row["emitter"])


@pytest.mark.parametrize("row", LABELS, ids=lambda r: "item-%d" % r["item"])
def test_every_notification_stephen_answered_gets_the_tier_he_answered(row):
    want = pol.NEEDS_YOU if row["interrupts"] else pol.FYI
    assert _tier(row) == want


def test_the_fixture_is_all_twelve_notification_items_with_both_answers():
    assert len(LABELS) == 12
    assert {r["interrupts"] for r in LABELS} == {True, False}


def test_the_rule_never_reads_the_words():
    row = next(r for r in LABELS if r["interrupts"])
    assert _tier(row, "an unrelated sentence") == _tier(row, "Everything ran successfully") == pol.NEEDS_YOU
    other = next(r for r in LABELS if not r["interrupts"])
    assert _tier(other, "Completed successfully. Ran on schedule.") == pol.FYI


def test_a_scheduled_job_that_did_not_finish_well_keeps_todays_tier():
    assert pol.tier_for("scheduled_failure", "scheduler", "high", None, None, emitter="E04") == pol.NEEDS_YOU
    for emitter, kind in (("E05", "scheduled_task"), ("SJ1", "scheduled_task"), ("E08", "scheduled_status")):
        assert pol.tier_for(kind, "scheduler", "low", None, None, emitter=emitter) == pol.FYI, emitter


def test_status_routing_and_other_notices_do_not_interrupt_by_default():
    for kind, source, emitter in (("avatar", "avatar", "E54"), ("info", "system", "E99"),
                                  ("podcast_ready", "podcasts", "E15"), ("task_result", "agent", "E02")):
        assert pol.tier_for(kind, source, "low", None, None, emitter=emitter) == pol.FYI, emitter


def test_an_emitter_that_names_its_own_tier_keeps_it():
    assert pol.tier_for("scheduled_task", "scheduler", "low", pol.LOG_ONLY, None, emitter="E07") == pol.LOG_ONLY


def test_the_daily_briefing_and_the_weekly_editions_are_off_until_he_turns_them_on(_settings):
    for emitter in ("E14", "E12", "E13"):
        assert pol.tier_for("briefing", "briefing", "low", None, None, emitter=emitter) == pol.FYI
    _settings[pol.INTERRUPT_SETTING] = {"daily_briefing": True}
    assert pol.tier_for("briefing", "briefing", "low", None, None, emitter="E14") == pol.NEEDS_YOU


def test_turning_a_rule_off_takes_the_notice_back_to_a_quiet_card(_settings):
    _settings[pol.INTERRUPT_SETTING] = {"front_page": False}
    assert pol.tier_for("front_page", "front-page", "high", None, None, emitter="E11") == pol.FYI
    assert pol.tier_for("scheduled_task", "scheduler", "low", None, None, emitter="E07") == pol.NEEDS_YOU


@pytest.mark.parametrize("bad", ["yes", ["front_page"], {"front_page": "no"}, {"made_up": True}, None])
def test_a_damaged_setting_falls_back_to_the_defaults(_settings, bad):
    _settings[pol.INTERRUPT_SETTING] = bad
    assert pol.tier_for("front_page", "front-page", "high", None, None, emitter="E11") == pol.NEEDS_YOU


def test_the_rule_runs_ahead_of_the_notify_head_which_stays_in_shadow(monkeypatch):
    seen = []
    monkeypatch.setattr(triage_heads, "notification_tier", lambda *a, **k: seen.append(a) or a[0])
    assert pol.tier_for("front_page", "front-page", "high", None, "x", emitter="E11") == pol.NEEDS_YOU
    assert seen == []
    assert pol.tier_for("info", "system", "low", None, "x", emitter="E99") == pol.FYI
    assert len(seen) == 1


def test_every_emitter_a_rule_names_is_a_real_push_that_is_not_a_failure():
    known = {e.id: e for e in notice_registry._ALL}
    for rule in pol.INTERRUPT_RULES:
        for emitter in rule.emitters:
            assert emitter in known, emitter
            assert "fail" not in known[emitter].kind and known[emitter].route == notice_registry.PUSH


def test_the_rules_read_in_plain_words_without_codes(_settings):
    _settings[pol.INTERRUPT_SETTING] = {"daily_briefing": True}
    rules = {r["id"]: r for r in pol.rules_in_words()}
    assert rules["daily_briefing"]["on"] is True and rules["front_page"]["on"] is True
    for rule in rules.values():
        assert not any(emitter in rule["words"] for r in pol.INTERRUPT_RULES for emitter in r.emitters)
        assert rule["words"][0].isupper() and rule["words"].endswith(".")
