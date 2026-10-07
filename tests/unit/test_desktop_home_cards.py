"""Home cards persist bounded text and native navigation, never executable UI."""
import json

import pytest

from agent_friday.services import desktop_cards as cards, desktop_bus, off_record


@pytest.fixture(autouse=True)
def isolated_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(cards, "CARDS_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(off_record, "active", lambda: False)
    desktop_bus.reset()
    yield
    desktop_bus.reset()


def payload(card_id="prepare-day", **extra):
    return dict(id=card_id, title="Prepare the day", body="Review your calendar, then choose the next thing.",
                actions=[{"label": "Open calendar", "workspace": "calendar"}], **extra)


def test_upsert_roundtrip_preserves_id_and_original_order(monkeypatch):
    monkeypatch.setattr(cards.time, "time", lambda: 100)
    original = cards.upsert_card(payload())
    cards.upsert_card(payload("second"))
    cards.upsert_card(payload("first", priority=90))
    monkeypatch.setattr(cards.time, "time", lambda: 200)
    edited = cards.upsert_card(dict(payload(), title="Plan tomorrow"))
    assert edited["created_at"] == original["created_at"] == 100
    assert edited["updated_at"] == 200
    assert [c["id"] for c in cards.list_cards()] == ["first", "prepare-day", "second"]
    assert json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))["cards"]
    assert cards.remove_card("prepare-day") is True
    assert cards.remove_card("prepare-day") is False
    assert [c["id"] for c in cards.list_cards()] == ["first", "second"]


@pytest.mark.parametrize("change", [
    {"id": "../outside"}, {"id": "MixedCase"}, {"id": ""}, {"title": ""},
    {"title": "x" * 121}, {"body": "x" * 2001}, {"body": "bad\x00text"},
    {"priority": True}, {"priority": 1.5}, {"priority": -1}, {"priority": 101},
    {"actions": [{"label": "Run", "workspace": "calendar", "script": "alert(1)"}]},
    {"actions": [{"label": "Run", "workspace": "javascript:alert(1)"}]},
    {"actions": [{"label": "Run", "workspace": "missing"}]},
    {"actions": [{"label": "Open", "workspace": "calendar"}] * 4},
    {"actions": [{"label": "", "workspace": "calendar"}]},
    {"html": "<script>bad()</script>"},
])
def test_invalid_cards_never_write(change):
    with pytest.raises(cards.CardError):
        cards.upsert_card(dict(payload(), **change))
    assert not cards.CARDS_PATH.exists()


def test_plain_text_is_retained_for_text_only_rendering():
    text = "<img src=x onerror=alert(1)>"
    cards.upsert_card(dict(payload(), title=text, body=text))
    assert cards.list_cards()[0]["body"] == text


def test_a_held_workspace_cannot_be_added_but_does_not_erase_a_saved_card(monkeypatch):
    from agent_friday.services import workspace_registry
    cards.upsert_card(payload())
    monkeypatch.setattr(workspace_registry, "is_held", lambda _: True)
    with pytest.raises(cards.CardError, match="not available"):
        cards.upsert_card(payload("other"))
    assert cards.list_cards()[0]["id"] == "prepare-day"


def test_limit_refuses_extra_cards_without_evicting_existing_ones():
    for index in range(cards.MAX_CARDS):
        cards.upsert_card(payload(f"card-{index}"))
    with pytest.raises(cards.CardError, match="Remove one"):
        cards.upsert_card(payload("extra"))
    cards.upsert_card(dict(payload("card-0"), title="Updated"))
    assert len(cards.list_cards()) == cards.MAX_CARDS
    assert any(c["title"] == "Updated" for c in cards.list_cards())


def test_save_failure_leaves_previous_board_and_sends_no_success_event(monkeypatch):
    cards.upsert_card(payload())
    before = cards.CARDS_PATH.read_bytes()
    queue = desktop_bus.subscribe("card-test", "desktop")
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(cards.os, "replace", fail)
    with pytest.raises(OSError):
        cards.upsert_card(payload("second"))
    assert cards.CARDS_PATH.read_bytes() == before
    assert queue.empty()
    assert not list(cards.CARDS_PATH.parent.glob(".desktop-cards-*.tmp"))


def test_success_notifies_each_desktop_without_disclosing_card_contents():
    one = desktop_bus.subscribe("card-test-one", "desktop")
    two = desktop_bus.subscribe("card-test-two", "desktop")
    cards.upsert_card(payload())
    assert one.get_nowait() == two.get_nowait() == {"type": "desktop_cards_changed"}


@pytest.mark.parametrize("raw", ["{", '{"version":2,"cards":[]}', '{"version":1,"cards":[{}]}'])
def test_corrupt_board_is_not_silently_replaced(raw):
    cards.CARDS_PATH.write_text(raw, encoding="utf-8")
    with pytest.raises(OSError):
        cards.list_cards()
    with pytest.raises(OSError):
        cards.upsert_card(payload())
    assert cards.CARDS_PATH.read_text(encoding="utf-8") == raw


def test_off_record_refuses_durable_changes(monkeypatch):
    cards.upsert_card(payload())
    before = cards.CARDS_PATH.read_bytes()
    monkeypatch.setattr(off_record, "active", lambda: True)
    with pytest.raises(cards.CardError, match="Off the record"):
        cards.upsert_card(payload("second"))
    with pytest.raises(cards.CardError, match="Off the record"):
        cards.remove_card("prepare-day")
    assert cards.CARDS_PATH.read_bytes() == before


def test_reading_an_empty_board_does_not_create_storage():
    assert cards.list_cards() == []
    assert not cards.CARDS_PATH.exists()
