"""Standing grants from an owner phone or tablet (K5 PR D). Each rule has a case that fails if its check is removed:

* the list needs an owner phone or tablet that may approve (``pc_approve`` with an active key); a revoke and a
  stop-offering need an owner device only, since they narrow;
* the device never names a path: the desktop call is built here from a checked id or a plain pattern;
* nothing is made from here: there is no route that creates a grant;
* the desktop's refusal is passed on, a failure is a plain 503, and each act writes a receipt.
"""
import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pc_grants
from tests.ledger_support import rows
from tests.unit import test_tv_pc_approve_decide as base
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

_clean, client, Enrolled = base._clean, base.client, base.Enrolled

LISTING = {"grants": [{"grant_id": "g_1", "what": "send to Sam", "uses": 2}], "declined": [], "stopped": []}


class Desk:
    def __init__(self):
        self.calls, self.status, self.body, self.boom = [], 200, {"ok": True}, None

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.boom:
            raise self.boom
        return self.status, self.body


@pytest.fixture
def desk(monkeypatch):
    stand_in = Desk()
    monkeypatch.setattr(pc_grants, "_desk", stand_in)
    monkeypatch.setattr(pc_grants.pc_read, "_json_from_desk",
                        lambda path, params=None, area="": pc_grants.jsonify(LISTING))
    return stand_in


def plain(client, kind="phone", member="stephen"):
    tv = FakeTv()
    device_pairing.add_device(tv.spki, name="A device", kind=kind)
    if member:
        base.household.bind_device(tv.id, member)
    return tv, auth(open_session(client, tv))


def test_an_owner_device_that_may_approve_reads_the_list(client, desk):
    phone = Enrolled(client)
    answer = client.get("/tv/v1/grants", headers=phone.session())
    assert answer.status_code == 200 and answer.get_json()["grants"][0]["grant_id"] == "g_1"
    assert rows("device_read")[-1]["area"] == "grants"


def test_an_owner_device_without_the_approve_right_cannot_read_the_list(client, desk):
    _, headers = plain(client)
    answer = client.get("/tv/v1/grants", headers=headers)
    assert answer.status_code == 403 and answer.get_json()["code"] == "needs_right"


@pytest.mark.parametrize("kind,member", [("watch", "stephen"), ("tv", "stephen"), ("phone", "family"), ("phone", None)])
def test_a_watch_a_tv_a_family_or_unbound_device_cannot_read_revoke_or_stop(client, desk, kind, member):
    _, headers = plain(client, kind, member)
    assert client.get("/tv/v1/grants", headers=headers).status_code == 403
    assert client.delete("/tv/v1/grants/g_1", headers=headers).status_code == 403
    assert client.post("/tv/v1/grants/stop-offering", json={"pattern": "p"}, headers=headers).status_code == 403
    assert desk.calls == []


def test_an_owner_device_revokes_through_one_fixed_desktop_delete(client, desk):
    _, headers = plain(client)
    answer = client.delete("/tv/v1/grants/g_1", headers=headers)
    assert answer.status_code == 200 and answer.get_json()["ok"] is True
    assert desk.calls == [("delete", "/api/governance/grants/g_1", None)]


@pytest.mark.parametrize("gid", ["..%2Fsettings", ".hidden", "a" * 97])
def test_a_grant_id_that_is_not_an_id_never_reaches_the_desktop(client, desk, gid):
    _, headers = plain(client)
    assert client.delete("/tv/v1/grants/" + gid, headers=headers).status_code in (400, 404)
    assert desk.calls == []


def test_stop_offering_sends_only_the_pattern(client, desk):
    _, headers = plain(client)
    answer = client.post("/tv/v1/grants/stop-offering", headers=headers,
                         json={"pattern": "send_email:sam", "path": "/x", "tools": ["all"]})
    assert answer.status_code == 200
    assert desk.calls == [("post", "/api/governance/standing-grants/stop-offering", {"pattern": "send_email:sam"})]


@pytest.mark.parametrize("body", [{}, {"pattern": ""}, {"pattern": "  "}, {"pattern": 7}, {"pattern": "x" * 201}, [1]])
def test_a_missing_or_unreasonable_pattern_is_refused_before_the_desktop(client, desk, body):
    _, headers = plain(client)
    answer = client.post("/tv/v1/grants/stop-offering", json=body, headers=headers)
    assert answer.status_code == 400 and desk.calls == []


def test_a_device_cannot_make_a_grant(client, desk):
    _, headers = plain(client)
    for method in ("post", "put", "patch"):
        sent = getattr(client, method)("/tv/v1/grants", json={"tools": ["send_email"]}, headers=headers)
        assert sent.status_code in (404, 405)
    assert desk.calls == []


def test_the_desktops_refusal_is_passed_on_and_still_a_receipt(client, desk):
    desk.status, desk.body = 404, {"ok": False, "error": "no such grant"}
    _, headers = plain(client)
    answer = client.delete("/tv/v1/grants/g_9", headers=headers)
    assert answer.status_code == 404 and answer.get_json()["error"] == "no such grant"
    assert rows("device_act")[-1]["outcome"] == 404


@pytest.mark.parametrize("failure,recorded", [(RuntimeError("boom"), 503), (None, 500)])
def test_a_desktop_that_fails_is_a_plain_503_and_still_a_receipt(client, desk, failure, recorded):
    desk.boom = failure
    desk.status = 500
    _, headers = plain(client)
    answer = client.delete("/tv/v1/grants/g_1", headers=headers)
    assert answer.status_code == 503 and "boom" not in answer.get_data(as_text=True)
    assert rows("device_act")[-1]["outcome"] == recorded


def test_a_revoke_writes_one_receipt_naming_the_grant_and_never_a_pattern(client, desk):
    tv, headers = plain(client)
    client.delete("/tv/v1/grants/g_1", headers=headers)
    client.post("/tv/v1/grants/stop-offering", json={"pattern": "CANARY"}, headers=headers)
    revoke, stop = rows("device_act")[-2:]
    assert (revoke["action"], revoke["grant"], revoke["via"]) == ("revoke_grant", "g_1", "lan")
    assert stop["action"] == "stop_offering" and "CANARY" not in str(stop)
