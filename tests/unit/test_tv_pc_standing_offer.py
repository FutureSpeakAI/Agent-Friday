"""The owner's phone accepts the standing-grant offer with a signed approval (Compressor eval K5, PR D, brief test 13).

Rules guarded here, each with a case that fails if the check is removed:
* a device that holds pc_approve with an active key, and signs the offer card as challenged, makes the grant, and the
  grant says which device (``created_by`` = ``device:<id>``);
* a device without the right, a watch, a TV and a replayed nonce are each refused, and nothing is granted;
* nothing but the gateway's verified signed path can approve the offer in the name of a device: a label alone, or a
  label with a source but no signature, is the plain owner, which the offer does not accept;
* the offer is the ONLY screen-only card a device may approve; the others stay on the PC's own button.
"""
from __future__ import annotations

import itertools
import time

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approval_novelty as an, approvals
from agent_friday.services import standing_grants as sg
from agent_friday.services import standing_offers as so
from tests.unit.test_tv_pc_approve_decide import Enrolled, _clean, card, client, status  # noqa: F401 (fixtures)
from tests.unit.test_tv_gateway import FakeTv

NEXT = itertools.count()


@pytest.fixture(autouse=True)
def _grant_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_HOOKS", {k: list(v) for k, v in approvals._HOOKS.items()})
    monkeypatch.setattr(an, "_DIR", tmp_path)
    monkeypatch.setattr(an, "COUNTS_FILE", tmp_path / "counts.json")
    monkeypatch.setattr(an, "LABELS_FILE", tmp_path / "labels.jsonl")
    monkeypatch.setattr(so, "STATE_FILE", tmp_path / "offers.json")
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    sg.register()


def _offer():
    """The offer card, raised the way the shell raises it: after the owner approved the same action enough times."""
    for _ in range(so.OFFER_AFTER_APPROVALS):
        rec = approvals.create_approval(
            kind="tool_confirm", subject_type="tool_action", subject_id="s%d" % next(NEXT), title="t",
            action_description="run it", force_gate=True, requested_by="confirmation_gate",
            payload={"tool": "create_task", "task_id": "", "input": {"list": "home"}})
        approvals.decide(rec["approval_id"], "approve")
    [offer] = [c for c in approvals.list_approvals(status="pending") if c["kind"] == sg.OFFER_KIND]
    return offer


def _grants():
    return [g for g in action_gate.list_grants() if str(g["scope"]).startswith(sg.PATTERN_SCOPE)]


def test_the_owner_phone_accepts_the_offer_and_the_grant_names_the_device(client):
    phone = Enrolled(client)
    offer = _offer()
    answer = phone.approve(offer["approval_id"])
    assert answer.status_code == 200 and answer.get_json()["won"] is True, answer.get_json()
    [grant] = _grants()
    assert grant["tools"] == ["create_task"] and grant["created_by"] == "device:%s" % phone.id
    assert grant["max_uses"] == sg.GRANT_USES
    assert approvals.get_approval(offer["approval_id"])["decided_source"] == "device_signed"


def test_a_device_without_the_right_cannot_accept_the_offer(client):
    from agent_friday.services import device_pairing
    phone = Enrolled(client)
    device_pairing.set_right(phone.id, "pc_approve", False)
    offer = _offer()
    assert phone.challenge(offer["approval_id"]).get_json()["code"] == "needs_right"
    body = {"decision": "approve", "nonce": "n", "time": int(time.time()), "sig": "s"}
    assert phone.post("/tv/v1/approvals/%s/decide" % offer["approval_id"], body).status_code == 403
    assert status(offer["approval_id"]) == "pending" and _grants() == []


@pytest.mark.parametrize("kind", ["watch", "tv"])
def test_a_watch_and_a_tv_cannot_accept_the_offer(client, kind):
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import household
    from tests.unit.test_tv_gateway import auth, open_session
    device = FakeTv()
    device_pairing.add_device(device.spki, name="Other", kind=kind)
    household.bind_device(device.id, "stephen")
    offer = _offer()
    headers = auth(open_session(client, device))
    assert client.post("/tv/v1/approvals/%s/challenge" % offer["approval_id"], json={}, headers=headers).status_code == 403
    body = {"decision": "approve", "nonce": "n", "time": int(time.time()), "sig": "s"}
    assert client.post("/tv/v1/approvals/%s/decide" % offer["approval_id"], json=body, headers=headers).status_code == 403
    assert status(offer["approval_id"]) == "pending" and _grants() == []


def test_a_replayed_signature_cannot_accept_the_offer_twice(client):
    phone = Enrolled(client)
    offer = _offer()
    got = phone.challenge(offer["approval_id"]).get_json()
    when = int(time.time())
    body = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)}
    path = "/tv/v1/approvals/%s/decide" % offer["approval_id"]
    first = phone.post(path, body)
    assert first.status_code == 200 and len(_grants()) == 1
    again = phone.post(path, body)
    assert again.status_code != 200 or again.get_json().get("already_decided") is True, again.get_json()
    assert again.get_json().get("won") is not True and len(_grants()) == 1


def test_a_device_label_without_the_signed_path_is_the_plain_owner_and_the_offer_refuses_it(client):
    offer = _offer()
    for kwargs in ({"decided_by": "device:phone-1"},
                   {"decided_by": "device:phone-1", "source": "device_signed"},
                   {"decided_by": "device:phone-1", "source": "device_signed", "proof": {"sig": "x"}},
                   {"decided_by": "device:phone-1", "source": "device_signed", "expect_fp": "x"},
                   {"decided_by": "owner", "source": "device_signed", "proof": {"sig": "x"}, "expect_fp": "x"}):
        rec, won = approvals.decide_with_outcome(offer["approval_id"], "approve", **kwargs)
        assert won is False and rec["status"] == "pending", kwargs
    assert _grants() == []


def test_the_other_screen_only_cards_still_cannot_be_approved_from_a_device(client):
    phone = Enrolled(client)
    for kind in sorted(set(approvals.SCREEN_ONLY_KINDS) - {sg.OFFER_KIND}):
        pending = card(kind=kind)
        assert phone.challenge(pending["approval_id"]).status_code in (403, 422), kind
        assert status(pending["approval_id"]) == "pending", kind


def test_a_device_may_decline_the_offer_without_a_signature(client):
    phone = Enrolled(client)
    offer = _offer()
    answer = phone.post("/tv/v1/approvals/%s/decide" % offer["approval_id"], {"decision": "deny"})
    assert answer.status_code == 200 and status(offer["approval_id"]) == "denied" and _grants() == []


def test_the_second_guard_layer_refuses_every_other_screen_only_kind_even_with_the_whole_signed_path():
    """Defence in depth: the gateway refuses these cards at the challenge; the decision itself must refuse them too,
    whatever reaches it (device_signed_ok names the kinds a device may approve, and only those)."""
    for kind in sorted(set(approvals.SCREEN_ONLY_KINDS) - {sg.OFFER_KIND}):
        pending = card(kind=kind)
        rec, won = approvals.decide_with_outcome(pending["approval_id"], "approve", decided_by="device:phone-1",
                                                 source="device_signed", proof={"sig": "x"},
                                                 expect_fp=approvals._action_fp(pending))
        assert won is False and rec["status"] == "pending", kind


def test_a_device_whose_right_is_withdrawn_between_challenge_and_decide_makes_no_grant(client):
    from agent_friday.services import device_pairing
    phone = Enrolled(client)
    offer = _offer()
    got = phone.challenge(offer["approval_id"]).get_json()
    when = int(time.time())
    device_pairing.set_right(phone.id, "pc_approve", False)
    body = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)}
    assert phone.post("/tv/v1/approvals/%s/decide" % offer["approval_id"], body).status_code == 403
    assert status(offer["approval_id"]) == "pending" and _grants() == []


def test_an_offer_changed_after_the_challenge_is_refused_by_the_signed_fingerprint(client):
    phone = Enrolled(client)
    offer = _offer()
    got = phone.challenge(offer["approval_id"]).get_json()
    when = int(time.time())
    rec = approvals.get_approval(offer["approval_id"])
    rec["payload"] = dict(rec["payload"], pattern="another-pattern")
    approvals._upsert(rec)
    body = {"decision": "approve", "nonce": got["nonce"], "time": when, "sig": phone.sign(got, when)}
    answer = phone.post("/tv/v1/approvals/%s/decide" % offer["approval_id"], body)
    assert answer.status_code != 200 and status(offer["approval_id"]) == "pending" and _grants() == []


def test_a_grant_the_phone_made_is_listed_as_the_devices(client):
    phone = Enrolled(client)
    offer = _offer()
    assert phone.approve(offer["approval_id"]).get_json()["won"] is True
    [row] = sg.listing()["grants"]
    assert row["pressed_by"] == "device:%s" % phone.id and row["device"] == "owner_device"
