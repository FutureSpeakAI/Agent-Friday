"""/api/capabilities/state serves the same live picture the model reads.

One truth, several surfaces: the model's prompt block, the
search tool's failure text and any settings panel that wants to say what a
backend is doing all come from services/capability_state.py. This route is
how a UI gets it. It never guesses: absent and unconfigured are distinct,
and unconfigured names its key.
"""
from __future__ import annotations


def test_capabilities_state_route_reports_the_live_picture(client, monkeypatch):
    from agent_friday.services import web_search as ws
    from agent_friday.services import firecrawl as fc
    monkeypatch.setattr(ws, "brave_key", lambda: "")
    monkeypatch.setattr(fc, "configured", lambda: False)
    monkeypatch.setattr(ws, "_firecrawl_ready", lambda: False)
    r = client.get("/api/capabilities/state")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    by_key = {c["key"]: c for c in body["capabilities"]}
    assert by_key["brave"]["state"] == "unconfigured"
    assert "BRAVE_SEARCH_API_KEY" in by_key["brave"]["needs"]
    assert by_key["firecrawl"]["state"] == "unconfigured"
    assert "FIRECRAWL_API_KEY" in by_key["firecrawl"]["needs"]
    assert "absent" not in (by_key["brave"]["state"], by_key["firecrawl"]["state"])
    assert {"local_brain", "google", "duckduckgo"} <= set(by_key)
    assert set(body["states"]) == {"working", "present_unverified", "present_failing",
                                   "unconfigured", "absent"}
