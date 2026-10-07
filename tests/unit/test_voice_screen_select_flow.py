"""The spoken flow: screen_select, then organize_email(selection=screen), then a spoken yes.

A spoken yes approves the batch only through local_context.decide_by_voice; "yes ... no" denies;
a room needs Friday's name. Nothing here adds a second way to say yes.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent, approvals as ap, desktop_bus, item_actions as ia
from agent_friday.services import voice_engine as ve
from tests.screen_fixtures import Page, install_fake_gmail, newsletter_stage, report


def _say(*_a, **_k):
    return None


@pytest.fixture
def world(tmp_path, monkeypatch):
    desktop_bus.reset()
    fake = install_fake_gmail(tmp_path, monkeypatch)
    ia._CHOICES.clear()
    yield fake
    desktop_bus.reset()
    ia._CHOICES.clear()


def _raise(monkeypatch, selected=(1, 2, 3)):
    report(newsletter_stage(selected=list(selected)))
    page = Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3})
    sel = ve._voice_tool_run("screen_select", {"op": "select", "scope": "screen", "category": "newsletters"},
                             _say, {"conversation_id": "c-v"})
    assert sel.startswith("SELECT_OK"), sel
    out = ve._voice_tool_run("organize_email", {"action": "archive", "selection": "screen"}, _say,
                             {"conversation_id": "c-v", "owner_text": "archive them"})
    assert out.startswith("CARD_RAISED"), out
    return next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER), page


def test_a_spoken_yes_approves_the_batch_and_the_rows_are_released(world, monkeypatch):
    card, page = _raise(monkeypatch)
    res = ia.answer_card(card["approval_id"], "approve", "yes, go ahead", surface="voice-live")
    assert res["ok"] and res["text"].startswith("Approved and done"), res
    assert [c for c in world.calls if c[0] == "apply"] == [("apply", "acct_work", ("t1", "t2", "t3"), "archive")]
    assert [a[0]["state"] for a in page.pushed if a[0]["type"] == "held"][-1] == "done"


def test_a_yes_with_a_no_in_it_declines_and_the_ticks_stay(world, monkeypatch):
    card, page = _raise(monkeypatch)
    res = ia.answer_card(card["approval_id"], "approve", "yes... no, wait", surface="voice-live")
    assert not res["ok"] or "declined" in res["text"].lower() or res["text"].startswith("NOT RECORDED")
    assert ap.get_approval(card["approval_id"])["status"] in ("pending", "denied")
    assert world.changes() == []


def test_in_a_room_a_yes_that_does_not_name_friday_approves_nothing(world, monkeypatch):
    card, _page = _raise(monkeypatch)
    res = ia.answer_card(card["approval_id"], "approve", "yes", room_mode=True, surface="voice-live")
    assert res["text"].startswith("NOT RECORDED"), res
    assert ap.get_approval(card["approval_id"])["status"] == "pending" and world.changes() == []


def test_the_words_that_raised_the_card_cannot_approve_it(world, monkeypatch):
    card, _page = _raise(monkeypatch)
    res = ia.answer_card(card["approval_id"], "approve", "archive them", surface="voice-live")
    assert not res["ok"], res
    assert ap.get_approval(card["approval_id"])["status"] == "pending"
