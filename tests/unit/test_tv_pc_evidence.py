"""An owner phone triggers the evidence pack; the pack is saved on the PC and never sent to the phone (PW-G10).

Stephen, 2026-11-17: full control of the PC from his owner phone, so the phone may trigger the pack as a signed owner
approval, the pack is still built and SAVED ON THE PC only, the phone sees its status and the PC path, and no file is
transferred. These tests hold (tv_gateway/pc_evidence.py), with the real approvals store and the real signed approve of
the approvals routes, and a stand-in for the builder (its own tests are tests/api/test_evidence_pack_save.py):

* only an owner phone or tablet with an active approval key may ask, and a bad range makes no card;
* asking raises one card for the range (the same range is the same card) and builds nothing;
* nothing is built until the card is signed; a denied, expired or revoked card builds nothing;
* one signature makes one pack: reading the status again, or twice at once, never builds again;
* the answer carries a path, a name, a count and a hash and never the file; a failure says why and gives no path;
* a card the PC spent and lost track of (a restart) says "interrupted" and does not build.
"""
import sys
import time
import types

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import evidence_pack
from agent_friday.tv_gateway import pc_evidence
from tests.unit.test_tv_gateway import FakeTv, auth, open_session
from tests.unit import test_tv_pc_approve_decide as base

client = base.client
_clean = base._clean
Enrolled = base.Enrolled

BODY = {"since": "2026-11-01", "until": "2026-11-17"}


class Builder:
    def __init__(self):
        self.calls, self.fail, self.slow = [], None, 0.0

    def export_to_disk(self, rng, version, by, now=None, folder=None):
        self.calls.append((rng, by))
        time.sleep(self.slow)
        if self.fail:
            raise self.fail
        return types.SimpleNamespace(path="C:/Friday/evidence_packs/friday-evidence-x.zip", filename="friday-evidence-x.zip",
                                     manifest_sha256="ab" * 32, entries=12, recorded=True)

    @staticmethod
    def app_version():
        return "test"


@pytest.fixture
def builder(monkeypatch):
    fake = Builder()
    import agent_friday.services as services
    monkeypatch.setitem(sys.modules, "agent_friday.services.evidence_pack_export", fake)
    monkeypatch.setattr(services, "evidence_pack_export", fake, raising=False)
    monkeypatch.setattr(pc_evidence, "_JOBS", {})
    return fake


def ask(phone, body=BODY):
    return phone.post("/tv/v1/evidence/pack", body)


def state(phone, approval_id):
    return phone.get("/tv/v1/evidence/pack/" + approval_id).get_json()


def settle(phone, approval_id, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        got = state(phone, approval_id)
        if got["state"] not in ("running", "waiting_for_approval"):
            return got
        time.sleep(0.02)
    return got


def test_asking_raises_one_card_for_the_range_and_builds_nothing(client, builder):
    phone = Enrolled(client)
    r = ask(phone)
    got = r.get_json()
    assert r.status_code == 202 and got["state"] == "waiting_for_approval" and got["ok"] is True
    card = ap.get_approval(got["approval_id"])
    assert card["kind"] == "evidence_pack_export" and card["status"] == "pending"
    assert card["payload"] == {"since": "2026-11-01", "until": "2026-11-17", "device_id": phone.id}
    assert "saved on this PC only" in card["description"] and builder.calls == []


def test_the_same_range_is_the_same_card(client, builder):
    phone = Enrolled(client)
    assert ask(phone).get_json()["approval_id"] == ask(phone).get_json()["approval_id"]
    assert ask(phone, {"since": "2026-11-01", "until": "2026-11-16"}).get_json()["approval_id"] != ask(phone).get_json()["approval_id"]
    assert len([c for c in ap.list_approvals() if c["kind"] == "evidence_pack_export"]) == 2


def test_nothing_is_built_until_the_card_is_signed_and_one_signature_makes_one_pack(client, builder):
    phone = Enrolled(client)
    approval_id = ask(phone).get_json()["approval_id"]
    for _ in range(3):
        assert state(phone, approval_id)["state"] == "waiting_for_approval"
    assert builder.calls == []
    assert phone.approve(approval_id).get_json()["status"] == "approved"
    done = settle(phone, approval_id)
    assert done["state"] == "done" and done["path"].endswith("friday-evidence-x.zip") and done["entries"] == 12
    for _ in range(3):
        assert state(phone, approval_id)["state"] == "done"
    assert len(builder.calls) == 1 and builder.calls[0][1] == "device:" + phone.id
    assert ap.get_approval(approval_id)["consumed"] is True


def test_the_answer_has_a_path_and_numbers_and_never_the_file(client, builder):
    phone = Enrolled(client)
    approval_id = ask(phone).get_json()["approval_id"]
    phone.approve(approval_id)
    done = settle(phone, approval_id)
    assert set(done) == {"ok", "approval_id", "state", "path", "filename", "entries", "manifest_sha256", "recorded", "note"}
    assert "Nothing was sent to your phone" in done["note"]
    raw = phone.get("/tv/v1/evidence/pack/" + approval_id)
    assert raw.mimetype == "application/json" and b"PK" not in raw.data[:4]


def test_two_status_reads_at_once_build_once(client, builder):
    phone = Enrolled(client)
    builder.slow = 0.3
    approval_id = ask(phone).get_json()["approval_id"]
    phone.approve(approval_id)
    first = state(phone, approval_id)
    second = state(phone, approval_id)
    assert first["state"] == second["state"] == "running"
    assert settle(phone, approval_id)["state"] == "done" and len(builder.calls) == 1


def test_a_card_that_is_denied_builds_nothing(client, builder):
    phone = Enrolled(client)
    approval_id = ask(phone).get_json()["approval_id"]
    ap.decide(approval_id, "deny", decided_by="owner")
    assert state(phone, approval_id)["state"] == "denied" and builder.calls == []


def test_a_build_that_fails_says_why_and_gives_no_path(client, builder):
    phone = Enrolled(client)
    builder.fail = evidence_pack.PackRefused("The archive could not be read; nothing was exported.")
    approval_id = ask(phone).get_json()["approval_id"]
    phone.approve(approval_id)
    got = settle(phone, approval_id)
    assert got["state"] == "failed" and "nothing was exported" in got["why"] and "path" not in got


def test_an_unexpected_failure_is_plain_and_names_nothing(client, builder):
    phone = Enrolled(client)
    builder.fail = RuntimeError("C:/secret/place failed")
    approval_id = ask(phone).get_json()["approval_id"]
    phone.approve(approval_id)
    got = settle(phone, approval_id)
    assert got["state"] == "failed" and "secret" not in str(got)


def test_a_spent_card_with_no_record_of_its_build_is_interrupted_and_is_not_built_again(client, builder):
    phone = Enrolled(client)
    approval_id = ask(phone).get_json()["approval_id"]
    phone.approve(approval_id)
    settle(phone, approval_id)
    pc_evidence._JOBS.clear()                            # the PC restarted
    got = state(phone, approval_id)
    assert got["state"] == "interrupted" and len(builder.calls) == 1


@pytest.mark.parametrize("body", [{}, {"since": "2026-11-01"}, {"since": "11/01/2026", "until": "2026-11-17"},
                                  {"since": "2026-11-17", "until": "2026-11-01"}, {"since": "2026-11-01", "until": "2026-11-01x"},
                                  {"since": "2024-01-01", "until": "2026-11-17"}, {"since": 5, "until": 6}])
def test_a_bad_range_makes_no_card(client, builder, body):
    phone = Enrolled(client)
    assert ask(phone, body).status_code == 400
    assert not [c for c in ap.list_approvals() if c["kind"] == "evidence_pack_export"]


def test_the_same_day_for_both_ends_is_one_day(client, builder):
    phone = Enrolled(client)
    assert ask(phone, {"since": "2026-11-17", "until": "2026-11-17"}).status_code == 202


def test_a_device_with_no_approval_key_or_that_is_not_a_phone_or_tablet_is_refused(client, builder):
    no_key = FakeTv()
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import household
    device_pairing.add_device(no_key.spki, name="A phone", kind="phone")
    household.bind_device(no_key.id, "stephen")
    device_pairing.set_right(no_key.id, "pc_read", True)
    h = auth(open_session(client, no_key))
    assert client.post("/tv/v1/evidence/pack", json=BODY, headers=h).status_code == 403
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A TV", kind="tv")
    household.bind_device(tv.id, "stephen")
    device_pairing.set_right(tv.id, "pc_read", True)
    assert client.post("/tv/v1/evidence/pack", json=BODY, headers=auth(open_session(client, tv))).status_code == 403
    assert not [c for c in ap.list_approvals() if c["kind"] == "evidence_pack_export"]


def test_only_a_pack_request_has_a_status(client, builder):
    phone = Enrolled(client)
    other = ap.create_approval(kind="desk_test_card", subject_type="x", subject_id="y", title="t", force_gate=True)
    assert phone.get("/tv/v1/evidence/pack/" + other["approval_id"]).status_code == 404
    assert phone.get("/tv/v1/evidence/pack/nope").status_code == 404
    assert phone.get("/tv/v1/evidence/pack/" + "x" * 200).status_code == 404
