"""organize_email(selection="screen"): ONE card, bound to exactly what was ticked (I1, I8).

The card lists the refs that were ticked when the tool was called. Mail that arrives after, and
rows the owner ticks after, are not in it; the page is told which rows are held, then how it ended.
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


def _org(**kw):
    return agent._tool_organize_email(kw)


def test_the_card_covers_exactly_the_ticked_conversations(gmail, monkeypatch):
    report(newsletter_stage(selected=[1, 2, 3], rev=7))
    page = Page(monkeypatch)
    out = _org(action="archive", selection="screen", why="the owner asked")
    assert out.startswith("CARD_RAISED"), out
    card = next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)
    pay = card["payload"]
    assert pay["accounts"] == {"acct_work": ["t1", "t2", "t3"]} and pay["count"] == 3
    assert pay["refs"] == [ref(1), ref(2), ref(3)] and pay["stage_rev"] == 7 and pay["selection_id"] == "sel_t1"
    assert not [c for c in gmail.calls if c[0] == "search"], "a ticked batch is never a search"
    assert gmail.changes() == [], "nothing changes before the owner answers"


def test_mail_that_arrives_after_is_never_swept_in(gmail, monkeypatch):
    report(newsletter_stage(selected=[1, 2, 3]))
    Page(monkeypatch)
    out = _org(action="archive", selection="screen")
    gmail.found["acct_work"].append("t99")            # a new newsletter lands
    report(newsletter_stage(selected=[1, 2, 3, 6]))    # and the owner ticks another row
    card = next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "complete"
    assert [c for c in gmail.calls if c[0] == "apply"] == [("apply", "acct_work", ("t1", "t2", "t3"), "archive")]


def test_the_page_is_told_the_rows_are_held_then_done_then_declined(gmail, monkeypatch):
    report(newsletter_stage(selected=[1, 2]))
    page = Page(monkeypatch)
    _org(action="archive", selection="screen")
    held = [a[0] for a in page.pushed if a[0].get("state") == "held"]
    assert held and held[0]["type"] == "held" and held[0]["refs"] == [ref(1), ref(2)] and held[0]["card_id"]
    card = next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    done = [a[0] for a in page.pushed if a[0].get("state") == "done"]
    assert done and done[-1]["receipt_id"] == rec["receipt_id"] and done[-1]["refs"] == [ref(1), ref(2)]
    # a second batch, declined: the ticks stay and the page is told
    report(newsletter_stage(selected=[4, 5]))
    page.pushed.clear()
    _org(action="trash", selection="screen")
    card2 = [c for c in ap.list_approvals() if c["status"] == "pending"][0]
    ap.decide(card2["approval_id"], "deny")
    declined = [a[0] for a in page.pushed if a[0].get("state") == "declined"]
    assert declined and declined[-1]["refs"] == [ref(4), ref(5)]


def test_an_old_stage_with_no_answer_from_the_page_refuses(gmail, monkeypatch):
    report(newsletter_stage(selected=[1, 2, 3]))
    desktop_bus._CLIENTS["pg1"]["stage_at"] -= 30
    Page(monkeypatch)                                  # the page does not answer stage?
    out = _org(action="archive", selection="screen")
    assert out.startswith("NOT DONE") and "can't see your list" in out, out
    assert not ap.list_approvals() and not [c for c in gmail.calls if c[0] == "search"], (
        "it must never fall back to a query")


def test_an_old_stage_is_asked_for_again_and_the_fresh_one_is_used(gmail, monkeypatch):
    report(newsletter_stage(selected=[1, 2, 3]))
    desktop_bus._CLIENTS["pg1"]["stage_at"] -= 30
    page = Page(monkeypatch, stage_on_ask=newsletter_stage(selected=[4, 5], rev=11))
    out = _org(action="archive", selection="screen")
    assert out.startswith("CARD_RAISED") and "stage_request" in page.types()
    card = next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)
    assert card["payload"]["accounts"] == {"acct_work": ["t4", "t5"]} and card["payload"]["stage_rev"] == 11


def test_nothing_ticked_raises_no_card(gmail, monkeypatch):
    report(newsletter_stage(selected=[]))
    Page(monkeypatch)
    out = _org(action="archive", selection="screen")
    assert out.startswith("NOT DONE") and "Nothing is ticked" in out
    assert not ap.list_approvals()


def test_more_than_the_cap_never_makes_a_bigger_card(gmail, monkeypatch):
    st = newsletter_stage()
    st["selection"]["refs"] = [ref(i) for i in range(1, 700)]
    report(st)
    Page(monkeypatch)
    gmail.found["acct_work"] = ["t%d" % i for i in range(1, 700)]
    _org(action="archive", selection="screen")
    card = next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)
    assert card["payload"]["count"] <= ia.MAX_ITEMS and len(card["payload"]["refs"]) <= ia.MAX_ITEMS


def test_a_plain_search_card_has_no_refs_and_still_works(gmail, monkeypatch):
    Page(monkeypatch)
    out = _org(action="archive", query="from:linkedin.com")
    assert out.startswith("CARD_RAISED")
    card = next(c for c in ap.list_approvals() if (c.get("payload") or {}).get("handler") == ia.HANDLER)
    assert "refs" not in card["payload"]
