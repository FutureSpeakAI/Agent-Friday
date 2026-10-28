"""Under Stop everything Friday never says a card is waiting when none was made (BD-13).

The gate answers 'card' while everything is stopped (callers wait, nothing is used up), but no
card is created. The organize tools used to read that as 'pending approval' and tell the model
CARD_RAISED, so Friday could tell the owner to approve a card that does not exist.
"""
from __future__ import annotations

from agent_friday.governance import action_gate as ag
from agent_friday.services import item_actions as ia
from tests.unit.test_organize_calendar import _org, _pending, world  # noqa: F401  (fixture)


def test_under_stop_no_card_is_claimed(world, monkeypatch):
    from agent_friday.services import global_stop
    monkeypatch.setattr(ag, "outward_hold", lambda: None)
    global_stop._flag().set()
    try:
        out = _org(events=["e1"], days=1)
    finally:
        global_stop._reset_for_tests()
    assert _pending() == []
    assert "CARD_RAISED" not in out, "Friday claimed a card that was never made: %r" % (out,)
    assert "stop" in out.lower(), out


def test_a_card_verdict_with_no_card_on_file_is_refused(monkeypatch):
    monkeypatch.setattr(ag, "authorize_external",
                        lambda *a, **k: ag.Verdict("card", ag.OUTWARD, "waiting for the owner's decision"))
    monkeypatch.setattr(ia, "_find_card", lambda name, detail: None)
    try:
        out = ia._raise_card("calendar", "shift", {"lines": []}, title="Move 1 event", body="move")
    except ia.Refused as e:
        assert "no card" in str(e).lower() or "nothing was proposed" in str(e).lower(), str(e)
        return
    raise AssertionError("a card the gate did not make was reported as pending: %r" % (out,))
