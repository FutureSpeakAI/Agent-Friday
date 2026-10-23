"""/api/capabilities/state carries each capability's health in the nine states
(north star §28.3) and the group that keeps off-on-purpose apart from failing."""
from __future__ import annotations

from agent_friday.services import capability_state as cs


def test_the_api_names_the_states_and_groups(client):
    d = client.get("/api/capabilities/state").get_json()
    assert d["health_states"] == list(cs.HEALTH_STATES) and d["groups"] == list(cs.GROUPS)
    assert d["capabilities"]
    for c in d["capabilities"]:
        assert c["health"] in cs.HEALTH_STATES and c["group"] in cs.GROUPS
