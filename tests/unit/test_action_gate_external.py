"""governance/action_gate.authorize_external: outward actions that are not tool
calls (a federated compute job, a mailbox change Friday proposes).

  * The card says in words what will happen when the caller gives the words.
  * One approved card authorises exactly one run of exactly that action.
  * A decision hook may name the card it was told about; only a card raised
    for this very action and detail is accepted that way, so naming a card
    never widens what an approval covers.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import approvals as ap

DETAIL = {"handler": "test", "ids": ["a", "b"]}


@pytest.fixture(autouse=True)
def _intact(monkeypatch):
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))


def _card(action, detail):
    import hashlib
    import json
    fp = hashlib.sha256(json.dumps({"a": action, "d": detail}, sort_keys=True,
                                   default=str).encode()).hexdigest()[:16]
    mine = [r for r in ap.list_approvals(kind="governed_action")
            if str(r.get("subject_id") or "").startswith(f"{action}:{fp}")]
    return mine[0] if mine else None       # newest first


def test_the_card_speaks_in_words_when_given_them():
    v = ag.authorize_external("test: words", DETAIL, requested_by="friday",
                              title="Friday wants to tidy two things",
                              description="Nothing happens unless you approve it.",
                              action_description="Friday proposes to tidy: a, b")
    assert v.action == "card"
    card = _card("test: words", DETAIL)
    assert card["title"] == "Friday wants to tidy two things"
    assert card["action_description"] == "Friday proposes to tidy: a, b"
    assert card["payload"] == DETAIL


def test_without_words_the_card_names_the_action_as_before():
    ag.authorize_external("test: plain", DETAIL, requested_by="federation")
    assert _card("test: plain", DETAIL)["title"] == "Allow test: plain"


def test_one_approval_buys_exactly_one_run():
    assert ag.authorize_external("test: once", DETAIL, requested_by="friday").action == "card"
    card = _card("test: once", DETAIL)
    ap.decide(card["approval_id"], "approve")
    first = ag.authorize_external("test: once", DETAIL, requested_by="owner",
                                  approval_id=card["approval_id"])
    assert first.action == "allow"
    again = ag.authorize_external("test: once", DETAIL, requested_by="owner",
                                  approval_id=card["approval_id"])
    assert again.action == "card"            # used up: a new decision is needed


def test_a_card_raised_again_is_still_the_one_that_authorises():
    """After a used card, the same change raises a new card whose subject has a
    suffix; the lookup by subject alone would miss it, the named card does not."""
    ag.authorize_external("test: again", DETAIL, requested_by="friday")
    c1 = _card("test: again", DETAIL)
    ap.decide(c1["approval_id"], "approve")
    assert ag.authorize_external("test: again", DETAIL, requested_by="o",
                                 approval_id=c1["approval_id"]).action == "allow"
    assert ag.authorize_external("test: again", DETAIL, requested_by="friday").action == "card"
    c2 = _card("test: again", DETAIL)
    assert c2["approval_id"] != c1["approval_id"]
    ap.decide(c2["approval_id"], "approve")
    assert ag.authorize_external("test: again", DETAIL, requested_by="o",
                                 approval_id=c2["approval_id"]).action == "allow"


def test_naming_a_card_for_something_else_authorises_nothing():
    ag.authorize_external("test: other", {"ids": ["x"]}, requested_by="friday")
    other = _card("test: other", {"ids": ["x"]})
    ap.decide(other["approval_id"], "approve")
    v = ag.authorize_external("test: mine", DETAIL, requested_by="o",
                              approval_id=other["approval_id"])
    assert v.action == "card"                 # not allowed on someone else's card
    assert ap.get_approval(other["approval_id"]).get("consumed") is not True


def test_a_declined_card_is_not_asked_again():
    ag.authorize_external("test: no", DETAIL, requested_by="friday")
    card = _card("test: no", DETAIL)
    ap.decide(card["approval_id"], "deny")
    assert ag.authorize_external("test: no", DETAIL, requested_by="friday").action == "deny"


def test_a_failed_integrity_check_holds_even_an_approved_card(monkeypatch):
    ag.authorize_external("test: held", DETAIL, requested_by="friday")
    card = _card("test: held", DETAIL)
    ap.decide(card["approval_id"], "approve")
    monkeypatch.setattr(ag, "verify_claws", lambda: (False, "tampered"))
    v = ag.authorize_external("test: held", DETAIL, requested_by="o", approval_id=card["approval_id"])
    assert v.action == "deny" and "tampered" in v.reason
