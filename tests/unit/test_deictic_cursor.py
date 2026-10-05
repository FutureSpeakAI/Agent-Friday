"""The hand cursor joins the stage: "archive this" means the row the hand is on, and a disagreement is a
question, not a guess (spec section 3.5, phase 3).

The page reports `cursor: {ref, state, age_s}` for the row the reticle is locked on (never a guarded
control). The server resolves "this" and "these" in a fixed order: the ticked rows, the hand, the open
item, the keyboard row, the last pointed set; and says which rule fired on the card.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent, approvals as ap, desktop_bus, item_actions as ia
from tests.screen_fixtures import Page, install_fake_gmail, newsletter_stage, ref, report


@pytest.fixture
def gmail(tmp_path, monkeypatch):
    desktop_bus.reset()
    fake = install_fake_gmail(tmp_path, monkeypatch)
    yield fake
    desktop_bus.reset()


def _stage(selected=(), cursor=None, open_=None, focus=None):
    st = newsletter_stage(selected=list(selected))
    st["cursor"], st["open"], st["focus"] = cursor, open_, focus
    return st


def _org(**kw):
    return agent._tool_organize_email(dict({"selection": "screen"}, **kw))


def _card():
    return next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)


def test_archive_this_with_the_hand_on_row_seven_is_row_seven(gmail, monkeypatch):
    items_extra = [dict(ref=ref(7), n=7, facets={"lane": "all"}, title="t7", who="w7")]
    st = _stage(cursor={"ref": ref(7), "state": "locked", "age_s": 0.4})
    st["items"] += items_extra
    gmail.found["acct_work"].append("t7")
    report(st)
    Page(monkeypatch)
    out = _org(action="archive")
    assert out.startswith("CARD_RAISED"), out
    card = _card()
    assert card["payload"]["accounts"] == {"acct_work": ["t7"]} and card["payload"]["refs"] == [ref(7)]
    assert "the row your hand was on" in card["action_description"], "the card says how the row was chosen"


def test_a_selection_of_three_with_the_hand_on_another_row_asks(gmail, monkeypatch):
    report(_stage(selected=[1, 2, 3], cursor={"ref": ref(6), "state": "pinched", "age_s": 0.2}))
    Page(monkeypatch)
    out = _org(action="archive")
    assert out.startswith("NOT DONE") and "3 ticked ones or the one your hand is on" in out, out
    assert not ap.list_approvals()


def test_the_ticked_rows_win_when_the_hand_is_on_one_of_them(gmail, monkeypatch):
    report(_stage(selected=[1, 2, 3], cursor={"ref": ref(2), "state": "locked", "age_s": 0.2}))
    Page(monkeypatch)
    assert _org(action="archive").startswith("CARD_RAISED")
    assert _card()["payload"]["refs"] == [ref(1), ref(2), ref(3)]


def test_the_hand_stops_counting_after_three_seconds(gmail, monkeypatch):
    report(_stage(cursor={"ref": ref(4), "state": "locked", "age_s": 5.0}, open_=ref(5)))
    Page(monkeypatch)
    _org(action="archive")
    assert _card()["payload"]["refs"] == [ref(5)], "the open message, not the row the hand left"
    assert "the one you have open" in _card()["action_description"]


def test_with_nothing_ticked_open_or_under_the_hand_it_asks(gmail, monkeypatch):
    report(_stage())
    Page(monkeypatch)
    out = _org(action="archive")
    assert out.startswith("NOT DONE") and "Nothing is ticked, open or pointed at" in out
    assert not ap.list_approvals()


def test_the_row_the_keyboard_is_on_counts_only_when_the_page_reports_it(gmail, monkeypatch):
    report(_stage(focus=ref(3)))
    Page(monkeypatch)
    _org(action="archive")
    assert _card()["payload"]["refs"] == [ref(3)] and "the row you were on" in _card()["action_description"]


def test_what_was_just_pointed_at_is_those(gmail, monkeypatch):
    report(_stage())
    page = Page(monkeypatch, answer={"ok": True, "count": 2})
    agent._tool_screen_select({"op": "point", "workspace": "messages", "match": {"category": "unread"}})
    out = _org(action="archive")
    assert out.startswith("CARD_RAISED") and _card()["payload"]["refs"] == [ref(1), ref(5)]
    assert "the ones I just pointed at" in _card()["action_description"]


def test_a_guarded_control_is_never_reported_as_the_cursor_target():
    """bound_stage drops a cursor that is not a locked or pinched ref; the page never sends a guarded one."""
    from agent_friday.services import screen_stage as ss
    st = _stage(cursor={"ref": "btn:send", "state": "locked", "age_s": 0})
    assert ss.bound_stage(st)["cursor"] is None, "only a row ref can be a cursor target"
    st = _stage(cursor={"ref": ref(2), "state": "free", "age_s": 0})
    assert ss.bound_stage(st)["cursor"] is None
