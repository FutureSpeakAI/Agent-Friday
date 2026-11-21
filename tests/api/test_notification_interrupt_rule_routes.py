"""What may interrupt you: the rule as the owner reads and edits it in Settings."""
from __future__ import annotations

import pytest

from agent_friday.services import notification_policy as pol


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    store = {}
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: store)
    monkeypatch.setattr("agent_friday.core._save_settings", lambda patch: store.update(patch))
    return store


def _rules(client):
    return {r["id"]: r for r in client.get("/api/notifications/interrupt-rules").get_json()["rules"]}


def test_the_rules_come_back_in_plain_words_with_their_state(client):
    rules = _rules(client)
    assert rules["front_page"]["on"] is True and rules["daily_briefing"]["on"] is False
    assert rules["scheduled_runs"]["words"] == "A scheduled job that finished successfully."


def test_turning_a_rule_off_and_on_changes_what_interrupts(client):
    front = dict(kind="front_page", source="front-page", priority="high", tier=None, text=None, emitter="E11")
    assert pol.tier_for(**front) == pol.NEEDS_YOU
    r = client.post("/api/notifications/interrupt-rules", json={"id": "front_page", "on": False})
    assert r.status_code == 200 and {x["id"]: x["on"] for x in r.get_json()["rules"]}["front_page"] is False
    assert pol.tier_for(**front) == pol.FYI
    client.post("/api/notifications/interrupt-rules", json={"id": "front_page", "on": True})
    assert pol.tier_for(**front) == pol.NEEDS_YOU


def test_a_rule_the_owner_turns_on_takes_effect(client):
    briefing = dict(kind="briefing", source="briefing", priority="medium", tier=None, text=None, emitter="E14")
    assert pol.tier_for(**briefing) == pol.FYI
    client.post("/api/notifications/interrupt-rules", json={"id": "daily_briefing", "on": True})
    assert pol.tier_for(**briefing) == pol.NEEDS_YOU


@pytest.mark.parametrize("body", [{"id": "made_up", "on": True}, {"id": "front_page", "on": "no"},
                                  {"id": "front_page"}, {}, ["front_page"]])
def test_a_bad_request_is_refused_and_changes_nothing(client, _settings, body):
    r = client.post("/api/notifications/interrupt-rules", json=body)
    assert r.status_code == 400
    assert pol.INTERRUPT_SETTING not in _settings


def test_one_rule_changed_leaves_the_others_as_they_were(client):
    client.post("/api/notifications/interrupt-rules", json={"id": "daily_briefing", "on": True})
    client.post("/api/notifications/interrupt-rules", json={"id": "front_page", "on": False})
    rules = _rules(client)
    assert rules["daily_briefing"]["on"] is True and rules["front_page"]["on"] is False
    assert rules["scheduled_runs"]["on"] is True
