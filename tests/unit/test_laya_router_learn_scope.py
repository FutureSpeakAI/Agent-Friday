"""Which turns the router's correction capture may hold: no tools/tool_head needed (state only)."""
import pytest

from agent_friday.services import laya_router as lr
from agent_friday.services import laya_router_learn as ll


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ll.reset()
    monkeypatch.setattr(ll, "_settings", lambda: {})
    yield
    ll.reset()


def _r():
    return lr.Route("defer", layer="point_act", confidence=0.9)


def test_only_the_owners_turns_are_held():
    ll.note_route("archive these", _r(), conversation_id="c1")
    ll.note_route("archive these", _r(), conversation_id="c1", owner=False)
    assert not ll._held
    ll.note_route("archive these", _r(), conversation_id="c1", owner=True)
    assert len(ll._held) == 1


def test_the_router_hands_the_trust_flag_on_as_the_owner_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(ll, "note_route", lambda *a, **k: seen.append(k.get("owner")))
    lr.route("what's on my calendar", log=True, budget_ms=50, trusted=False)
    lr.route("what's on my calendar", log=True, budget_ms=50, trusted=True, stage=None, pointed=None)
    assert seen == [False, True]


def test_a_missing_conversation_id_matches_nothing():
    ll.note_route("archive these", _r(), conversation_id="c1", owner=True)
    ent = ll._held[-1]
    assert ll._same_conversation(ent, "c1") is True
    assert ll._same_conversation(ent, None) is False and ll._same_conversation(ent, "") is False
    assert ll._same_conversation(dict(ent, conversation_id=None), "c1") is False
    assert ll.note_undo(conversation_id=None) is False


def test_an_undo_in_a_tainted_turn_records_nothing():
    ll.note_route("archive these", _r(), conversation_id="c1", owner=True)
    assert ll.note_undo(conversation_id="c1", tainted=True) is False
    assert ll.note_undo(conversation_id="c1", tainted=lambda: True) is False
    assert not ll._held[-1]["resolved"]
