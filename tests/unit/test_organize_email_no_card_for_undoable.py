"""The owner's ruling: mark read, star and label run WITHOUT a card; the rest still get one.

Those changes are instantly undoable and take nothing out of the inbox. Archive, trash, spam,
restore, move and "back to the inbox" stay one card per batch. A receipt is written either way.
"""
from __future__ import annotations

import pytest

from agent_friday.services import approvals as ap, desktop_bus, item_actions as ia
from tests.screen_fixtures import Page, install_fake_gmail


@pytest.fixture
def gmail(tmp_path, monkeypatch):
    desktop_bus.reset()
    fake = install_fake_gmail(tmp_path, monkeypatch)
    yield fake
    desktop_bus.reset()


def test_the_undoable_changes_run_at_once_with_a_receipt(gmail):
    for action, label in [("read", ""), ("unread", ""), ("star", ""), ("unstar", ""),
                          ("label", "Receipts"), ("unlabel", "Receipts")]:
        before = len(gmail.changes())
        out = ia.propose_email(action, query="from:shop.example", label=label)
        assert out["status"] in ("complete", "partial"), (action, out)
        assert out["receipt_id"] and out["count"] == 6, (action, out)
        assert not ap.list_approvals(), "no card for %s" % action
        assert len(gmail.changes()) > before, "and the mail changed for %s" % action


def test_everything_else_still_waits_for_one_card(gmail):
    for n, (action, label) in enumerate([("archive", ""), ("trash", ""), ("spam", ""), ("restore", ""),
                                         ("not_spam", ""), ("inbox", ""), ("move", "Orders")], 1):
        out = ia.propose_email(action, query="from:shop.example", label=label, owner_words="w%d" % n)
        assert out["status"] == "pending_approval" and out["approval_id"], (action, out)
        assert gmail.changes() == [], action
        assert len(ap.list_approvals()) == n, action


def test_a_no_card_change_is_undoable_from_its_receipt(gmail):
    out = ia.propose_email("read", query="from:shop.example")
    from agent_friday.services import action_journal as journal
    rec = journal.get(out["receipt_id"])
    assert rec["undo"]["email"]["acct_work"], "the receipt keeps what undo needs"


def test_a_no_card_change_on_ticked_rows_tells_the_page_it_is_done(gmail, monkeypatch):
    page = Page(monkeypatch)
    out = ia.propose_email("star", thread_ids=["acct_work:t1", "acct_work:t2"],
                           refs=["mail:acct_work:t1", "mail:acct_work:t2"], selection_id="sel_x", stage_rev=3)
    done = [a[0] for a in page.pushed]
    assert done and done[-1]["state"] == "done" and done[-1]["receipt_id"] == out["receipt_id"]
    assert done[-1]["action"] == "star"


def test_a_label_the_account_lacks_is_made_and_said(gmail):
    out = ia.propose_email("label", query="from:shop.example", label="Brand new")
    assert out["status"] == "complete", out
    assert ("modify", "acct_work", ("t1", "t2", "t3", "t4", "t5", "t6"), ("Label_new",), ()) in gmail.calls
