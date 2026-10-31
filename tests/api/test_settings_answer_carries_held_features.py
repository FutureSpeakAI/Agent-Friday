"""The UI learns which features are held from GET /api/settings alone
(index.html: agentSettings -> fridaySetHeldFeatures), so that answer must
carry held_features, with the federation off on an install that never chose
otherwise (BD-00)."""
from __future__ import annotations


def test_settings_answer_carries_the_held_switches(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    held = (r.get_json() or {}).get("settings", {}).get("held_features")
    assert isinstance(held, dict), "the settings answer has no held_features"
    assert held.get("federation") is False
