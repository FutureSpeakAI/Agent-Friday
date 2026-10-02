"""The settings route moves the routing mode only for the owner's own mode
controls, which send owner_action: routing_mode (see the unit test of the same
name for the rule)."""
from agent_friday import core


def _mode():
    core._invalidate_settings_cache()
    return (core._load_settings_raw().get("model_routing") or {}).get("mode")


def test_the_settings_route_needs_the_owner_action(client):
    core._save_settings({"model_routing": {"mode": "local_preferred"}},
                        owner_routing_change=True)
    r = client.post("/api/settings", json={"settings": {"model_routing": {"mode": "cloud_only"}}})
    assert r.status_code == 200
    assert _mode() == "local_preferred"
    r = client.post("/api/settings", json={"owner_action": "routing_mode",
                                           "settings": {"model_routing": {"mode": "cloud_only"}}})
    assert r.status_code == 200
    assert _mode() == "cloud_only"


