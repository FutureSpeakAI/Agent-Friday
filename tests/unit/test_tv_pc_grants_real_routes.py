"""The phone's standing-grants calls reach the desktop's REAL routes (threat assessment fix #4).

tests/unit/test_tv_pc_grants.py replaces the desktop call with a stand-in, so it cannot notice that a path the gateway
calls does not exist on the desktop (GET /api/governance/standing-grants, POST .../stop-offering and DELETE
/api/governance/grants/<id> must all be served). Nothing here fakes the desktop: the gateway calls ``core.app`` in
process, as it does in production, and the grants are made by the same signed phone approval brief test 13 describes.
"""
from __future__ import annotations

from agent_friday import server  # noqa: F401 (importing server registers every blueprint on core.app)
from agent_friday.services import approvals, standing_grants as sg
from tests.unit.test_tv_pc_standing_offer import _grant_stores, _offer, _grants  # noqa: F401 (fixture + helpers)
from tests.unit.test_tv_pc_standing_offer import Enrolled, _clean, client  # noqa: F401 (fixtures)


def test_the_phone_lists_the_grant_it_made_through_the_real_desktop_route(client):
    phone = Enrolled(client)
    offer = _offer()
    assert phone.approve(offer["approval_id"]).get_json()["won"] is True
    answer = client.get("/tv/v1/grants", headers=phone.session())
    assert answer.status_code == 200, answer.get_data(as_text=True)
    [row] = answer.get_json()["grants"]
    assert row["tool"] == "create_task" and row["pressed_by"] == "device:%s" % phone.id


def test_the_phone_revokes_a_grant_through_the_real_desktop_route(client):
    phone = Enrolled(client)
    offer = _offer()
    phone.approve(offer["approval_id"])
    [grant] = _grants()
    answer = client.delete("/tv/v1/grants/%s" % grant["grant_id"], headers=phone.session())
    assert answer.status_code == 200 and answer.get_json().get("revoked") is True, answer.get_json()
    assert _grants() == []
    assert client.get("/tv/v1/grants", headers=phone.session()).get_json()["grants"] == []


def test_the_phone_stops_an_offer_through_the_real_desktop_route(client):
    phone = Enrolled(client)
    offer = _offer()
    pattern = approvals.get_approval(offer["approval_id"])["payload"]["pattern"]
    answer = client.post("/tv/v1/grants/stop-offering", json={"pattern": pattern}, headers=phone.session())
    assert answer.status_code == 200 and answer.get_json().get("stopped") is True, answer.get_json()
    listing = client.get("/tv/v1/grants", headers=phone.session()).get_json()
    assert pattern in str(listing.get("stopped")), listing
