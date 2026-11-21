"""approval_slot ids: which pending cards wait for one subject (NS-41.3-1)."""
import itertools

import pytest

from agent_friday.services import approval_slot as slot
from agent_friday.services import approvals as ap


@pytest.fixture(autouse=True)
def clean(friday_dir):
    if ap.APPROVALS_FILE.exists():
        ap.APPROVALS_FILE.unlink()
    yield


def _card(subject_type="crew", subject_id="c1", kind="send_email", n=[0]):
    n[0] += 1
    return ap.create_approval(kind=kind, subject_type=subject_type, subject_id=subject_id,
                              title="t%d" % n[0], action_description="a%d" % n[0], force_gate=True)


def test_only_pending_cards_for_the_exact_subject_are_listed():
    for decided, same_subject, same_kind in itertools.product((False, True), repeat=3):
        for f in (ap.APPROVALS_FILE,):
            if f.exists():
                f.unlink()
        card = _card("crew" if same_subject else "goal", "c1" if same_subject else "c2",
                     "send_email" if same_kind else "post_social")
        if decided:
            ap.decide(card["approval_id"], "deny")
        listed = slot.pending_ids("crew:c1", "send_email")
        want = [card["approval_id"]] if (not decided and same_subject and same_kind) else []
        assert listed == want, (decided, same_subject, same_kind)


def test_a_subject_id_that_only_starts_the_same_is_not_listed():
    _card("crew", "c10")
    assert slot.pending_ids("crew:c1") == []


def test_no_kind_lists_every_kind():
    a, b = _card(kind="send_email"), _card(kind="post_social")
    assert slot.pending_ids("crew:c1") == [a["approval_id"], b["approval_id"]]


def test_more_than_the_cap_gives_the_oldest(monkeypatch):
    cards = [_card(kind="k%d" % i) for i in range(slot.MAX_IDS + 3)]
    for i, c in enumerate(cards):
        ap._patch(c["approval_id"], created_at=1000 + i)
    assert slot.pending_ids("crew:c1") == [c["approval_id"] for c in cards[:slot.MAX_IDS]]


@pytest.mark.parametrize("bad", [None, "", "crew", "crew:", ":c1", "Crew:c1", "crew:c1:x", "crew:c 1",
                                 "crew:" + "x" * 81, "x" * 33 + ":c1", 5, ["crew:c1"], "crew:c1\n"])
def test_a_malformed_subject_is_refused_without_an_echo(bad):
    with pytest.raises(slot.BadSlot) as e:
        slot.pending_ids(bad)
    assert e.value.code == "bad_subject"
    assert e.value.args == ("bad_subject",)


@pytest.mark.parametrize("bad", ["", "A b", "x" * 65, 3])
def test_a_malformed_kind_is_refused(bad):
    with pytest.raises(slot.BadSlot) as e:
        slot.pending_ids("crew:c1", bad)
    assert e.value.code == "bad_kind"


def test_a_withdrawn_card_is_not_listed_though_its_status_still_says_pending():
    card = _card()
    ap._patch(card["approval_id"], withdrawn=True)
    assert ap.get_approval(card["approval_id"])["status"] == "pending"
    assert slot.pending_ids("crew:c1") == []
