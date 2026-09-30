"""Friday organizing the owner's Gmail (services/item_actions).

  * A request becomes ONE card listing the conversations it found; nothing in
    Gmail changes until the owner approves.
  * Approving changes exactly the conversations on the card, even if the same
    search would find more by then, and the receipt keeps what undo needs.
  * The card can be answered in words, typed or spoken, and only the owner's
    own words count: words said after the card was raised, where a no wins.
    Typed words follow the chat gate's whole-reply rule; spoken ones go
    through the voice path (local_context.decide_by_voice).
  * An account that has not allowed changes is left alone and named.
  * Friday putting mail back raises a card; the owner's own Undo button acts
    at once.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import approvals as ap
from agent_friday.services import gmail_mailbox as gm
from agent_friday.services import google_accounts as G
from agent_friday.services import item_actions as ia

ACCOUNTS = [
    {"id": "acct_work", "label": "Work", "email": "me@work.example", "services": {"gmail": True},
     "mail": {"read": True, "modify": True}, "health": {"healthy": True}},
    {"id": "acct_home", "label": "Home", "email": "me@home.example", "services": {"gmail": True},
     "mail": {"read": True, "modify": False}, "health": {"healthy": True}},
]


class FakeGmail:
    def __init__(self):
        self.found = {"acct_work": ["t1", "t2", "t3"], "acct_home": ["h1"]}
        self.labels = {"acct_work": [{"id": "Label_7", "name": "Receipts"}]}
        self.calls = []

    def search(self, acct, q, limit):
        self.calls.append(("search", acct, q))
        return list(self.found.get(acct, []))[:limit], False

    def heads(self, acct, tids):
        return {t: {"subject": "Subject %s" % t, "sender": "LinkedIn Jobs", "date": ""} for t in tids}

    def apply_action(self, acct, tids, action):
        self.calls.append(("apply", acct, tuple(tids), action))
        return {"changed": {t: {"added": [], "removed": ["INBOX"]} for t in tids}, "failed": {}}

    def modify_threads(self, acct, tids, add=(), remove=(), _allow=()):
        self.calls.append(("modify", acct, tuple(tids), tuple(add), tuple(remove)))
        return {"changed": {t: {"added": list(add), "removed": list(remove)} for t in tids}, "failed": {}}

    def undo(self, acct, changed):
        self.calls.append(("undo", acct, dict(changed)))
        return {"failed": {}}

    def list_labels(self, acct):
        return list(self.labels.get(acct, []))

    def create_label(self, acct, name):
        self.calls.append(("create_label", acct, name))
        lab = {"id": "Label_new", "name": name}
        self.labels.setdefault(acct, []).append(lab)
        return lab


@pytest.fixture
def gmail(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    fake = FakeGmail()
    monkeypatch.setattr(G, "list_accounts", lambda: [dict(a) for a in ACCOUNTS])
    monkeypatch.setattr(gm, "can_modify", lambda acct: acct == "acct_work")
    monkeypatch.setattr(ia, "_search_threads", fake.search)
    monkeypatch.setattr(ia, "_thread_heads", fake.heads)
    for name in ("apply_action", "modify_threads", "undo", "list_labels", "create_label"):
        monkeypatch.setattr(gm, name, getattr(fake, name))
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    yield fake
    journal.reset()


def _changes(fake):
    return [c for c in fake.calls if c[0] in ("apply", "modify", "undo", "create_label")]


def test_a_request_is_one_card_and_nothing_changes_yet(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com older_than:1m",
                           owner_words="archive everything from LinkedIn older than a month")
    assert out["status"] == "pending_approval", out
    assert out["count"] == 3
    assert _changes(gmail) == [], "nothing in Gmail changes before approval"
    card = ap.get_approval(out["approval_id"])
    assert card["title"] == "Friday wants to archive 3 conversations"
    assert "Subject t1 \u2014 LinkedIn Jobs" in card["payload"]["lines"]
    assert "Subject t1" in card["description"], "a surface that shows only text still has the list"
    assert "from:linkedin.com older_than:1m" in card["action_description"]
    assert "Subject" not in card["action_description"], (
        "the action text is what the harm check reads: the items are not the action")
    assert "I found 3 conversations" in out["readback"] and "Shall I archive" in out["readback"]


def test_an_account_that_cannot_be_changed_is_left_alone_and_named(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com")
    card = ap.get_approval(out["approval_id"])
    assert "acct_home" not in card["payload"]["accounts"]
    assert any("Home" in n and "left alone" in n for n in out["notes"])


def test_approval_changes_exactly_the_conversations_on_the_card(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com")
    gmail.found["acct_work"].append("t4")            # new mail arrives before the decision
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    assert rec["status"] == "complete", rec
    applied = [c for c in gmail.calls if c[0] == "apply"]
    assert applied == [("apply", "acct_work", ("t1", "t2", "t3"), "archive")]
    assert rec["undo"]["email"]["acct_work"]["t1"] == {"added": [], "removed": ["INBOX"]}
    assert ap.get_approval(out["approval_id"])["consumed"] is True


def test_a_move_makes_the_label_when_it_is_missing(gmail):
    out = ia.propose_email("move", query="from:shop.example", label="Orders")
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    assert ("create_label", "acct_work", "Orders") in gmail.calls
    assert ("modify", "acct_work", ("t1", "t2", "t3"), ("Label_new",), ("INBOX",)) in gmail.calls
    assert "made the label" in rec["summary"]


def test_unlabel_with_no_such_label_fails_honestly(gmail):
    out = ia.propose_email("unlabel", query="from:shop.example", label="Nope")
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    assert rec["status"] == "failed"
    assert not [c for c in gmail.calls if c[0] in ("modify", "create_label")]


def test_a_spoken_yes_after_the_card_approves_it(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com",
                           owner_words="archive everything from LinkedIn")
    res = ia.answer_card(out["approval_id"], "approve", "yes, go ahead", surface="voice-live")
    assert res["ok"] and res["text"].startswith("Approved and done"), res
    assert [c for c in gmail.calls if c[0] == "apply"]


def test_the_words_that_raised_the_card_cannot_approve_it(gmail):
    words = "archive everything from LinkedIn, yes"
    out = ia.propose_email("archive", query="from:linkedin.com", owner_words=words)
    res = ia.answer_card(out["approval_id"], "approve", words, surface="voice-live")
    assert not res["ok"] and "has not answered" in res["text"]
    assert ap.get_approval(out["approval_id"])["status"] == "pending"


def test_typed_no_wins_over_yes_and_a_claim_their_words_do_not_make_is_refused(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com", owner_words="archive linkedin")
    res = ia.answer_card(out["approval_id"], "approve", "yes, but not the ones from Dana")
    assert not res["ok"]
    assert ap.get_approval(out["approval_id"])["status"] == "pending"
    res = ia.answer_card(out["approval_id"], "approve", "hmm let me think")
    assert not res["ok"]
    res = ia.answer_card(out["approval_id"], "decline", "no, leave them")
    assert res["ok"] and "declined" in res["text"]
    assert ap.get_approval(out["approval_id"])["status"] == "denied"
    assert _changes(gmail) == []


def test_in_a_room_a_spoken_yes_must_name_friday(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com", owner_words="archive linkedin",
                           room_mode=True)
    assert "LinkedIn" not in out["readback"] and "Subject" not in out["readback"], (
        "a room hears the shape of the batch, never a sender or a subject")
    assert not ia.answer_card(out["approval_id"], "approve", "yes", room_mode=True,
                              surface="voice-live")["ok"]
    assert ia.answer_card(out["approval_id"], "approve", "yes Friday, do it", room_mode=True,
                          surface="voice-live")["ok"]


def test_a_spoken_no_wins_even_with_a_yes_in_it(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com", owner_words="archive linkedin")
    res = ia.answer_card(out["approval_id"], "approve", "yes... no, cancel that", surface="voice-live")
    assert not res["ok"]
    assert ap.get_approval(out["approval_id"])["status"] == "pending"


def test_answer_card_decides_only_organize_cards(gmail):
    other = ap.create_approval(kind="governed_action", subject_type="external_action",
                               subject_id="something:else", title="Send money", force_gate=True,
                               payload={"handler": "not_ours"})
    res = ia.answer_card(other["approval_id"], "approve", "yes")
    assert not res["ok"]
    assert ap.get_approval(other["approval_id"])["status"] == "pending"


def test_friday_putting_mail_back_raises_a_card(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com")
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    back = ia.undo(rec["receipt_id"])
    assert back["status"] == "pending_approval"
    assert not [c for c in gmail.calls if c[0] == "undo"]
    ap.decide(back["approval_id"], "approve")
    ia.wait_for(back["approval_id"], 10)
    undos = [c for c in gmail.calls if c[0] == "undo"]
    assert undos and undos[0][1] == "acct_work" and set(undos[0][2]) == {"t1", "t2", "t3"}
    assert journal.get(rec["receipt_id"])["undone"] is True


def test_the_owners_own_undo_acts_at_once(gmail):
    out = ia.propose_email("archive", query="from:linkedin.com")
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    back = ia.undo(rec["receipt_id"], by_owner=True)
    assert back["status"] == "complete"
    assert [c for c in gmail.calls if c[0] == "undo"]


def test_nothing_found_raises_no_card(gmail):
    gmail.found = {"acct_work": [], "acct_home": []}
    out = ia.propose_email("archive", query="from:nobody.example")
    assert out["status"] == "nothing" and "approval_id" not in out
    assert ap.list_approvals(kind="governed_action") == []


def test_an_unknown_action_is_refused(gmail):
    with pytest.raises(ia.Refused):
        ia.propose_email("delete_forever", query="from:x")
