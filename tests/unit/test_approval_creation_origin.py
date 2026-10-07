"""Delayed card construction retains the caller's trusted publication guard."""
import copy

import pytest

from agent_friday.services import approvals


@pytest.fixture
def card_world(monkeypatch):
    from agent_friday.services import off_record
    events, records = [], []
    monkeypatch.setattr(approvals, "find_for_subject", lambda *a: None)
    monkeypatch.setattr(approvals, "effective_policy_table", lambda: {"outward": {}})
    monkeypatch.setattr(approvals, "classify", lambda *a, **k: {"gated": True, "policy_class": "outward", "expires_seconds": 900})
    monkeypatch.setattr(approvals, "_safe_check_dissent", lambda *a: {})
    monkeypatch.setattr(approvals, "_current_provenance", lambda: None)
    monkeypatch.setattr(approvals, "_upsert", lambda record: records.append(copy.deepcopy(record)))
    monkeypatch.setattr(approvals, "_notify_pending", lambda record: events.append("notification"))
    monkeypatch.setattr(approvals, "_feed", lambda *a: events.append("feed"))
    monkeypatch.setattr(off_record, "skip", lambda *a: False)
    return events, records


def create(**kwargs):
    return approvals.create_approval(kind="domain_change", subject_type="domain_operation", subject_id="synthetic",
                                     title="Review synthetic change", action_class="outward", force_gate=True, **kwargs)


@pytest.mark.parametrize("boundary", ["classify", "_safe_check_dissent", "_current_provenance"])
def test_privacy_change_during_card_construction_prevents_storage_and_notification(card_world, monkeypatch, boundary):
    events, records = card_world
    active = {"value": True}
    original = getattr(approvals, boundary)
    def delayed(*args, **kwargs):
        active["value"] = False
        return original(*args, **kwargs)
    monkeypatch.setattr(approvals, boundary, delayed)
    def guard():
        if not active["value"]:
            raise ValueError("The originating privacy context ended.")
    with pytest.raises(ValueError, match="privacy context ended"):
        create(_before_publish=guard)
    assert records == [] and events == []


def test_valid_guard_is_transient_and_other_callers_keep_the_same_default(card_world):
    events, records = card_world
    guarded = create(_before_publish=lambda: None)
    ordinary = create()
    assert [record["status"] for record in records] == ["pending", "pending"]
    assert events == ["notification", "feed", "notification", "feed"]
    assert "_before_publish" not in guarded and "_before_publish" not in ordinary


def test_transition_after_storage_suppresses_late_notification_and_feed(card_world, monkeypatch):
    events, records = card_world
    active = {"value": True}
    def save(record):
        records.append(copy.deepcopy(record))
        active["value"] = False
    monkeypatch.setattr(approvals, "_upsert", save)
    def guard():
        if not active["value"]:
            raise ValueError("The originating privacy context ended.")
    with pytest.raises(ValueError, match="privacy context ended"):
        create(_before_publish=guard)
    assert len(records) == 1 and events == []


def test_transition_during_notification_suppresses_late_feed(card_world, monkeypatch):
    events, records = card_world
    active = {"value": True}
    def notify(record):
        events.append("notification")
        active["value"] = False
    monkeypatch.setattr(approvals, "_notify_pending", notify)
    def guard():
        if not active["value"]:
            raise ValueError("The originating privacy context ended.")
    with pytest.raises(ValueError, match="privacy context ended"):
        create(_before_publish=guard)
    assert len(records) == 1 and events == ["notification"]
