"""NS-40.1-3, the route half: /api/capabilities/state serves the six federation capabilities under their own key and
leaves "capabilities" as it was (tests/unit/test_federation_capability_state.py holds the states themselves)."""
from agent_friday.services import held_features as hf


def test_the_route_serves_them_under_their_own_key_and_leaves_capabilities_alone(client):
    body = client.get("/api/capabilities/state").get_json()
    assert [r["key"] for r in body["federation"]] == ["federation_" + c for c in hf.CAPABILITIES]
    assert all(not r["key"].startswith("federation_") for r in body["capabilities"])
    assert {r["group"] for r in body["federation"]} <= set(body["groups"])
