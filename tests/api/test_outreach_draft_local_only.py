"""POST /api/outreach/draft goes through the model router, so Local only holds.

The draft prompt carries a contact's name and the person's notes. Under Local
only none of it may reach a cloud model: not Gemini through the SDK, not
Anthropic through the router's ladder. With the cloud allowed the route still
returns a draft.
"""
from __future__ import annotations

import pytest

CONTACT = "Pat Example"


def _routing(patch_app, mode):
    patch_app("_load_settings", lambda *a, **k: {
        "model_routing": {"mode": mode, "vault_local_only": True},
    })


def test_local_only_outreach_makes_no_cloud_call(
        client, patch_app, mock_gemini, offline_calls):
    _routing(patch_app, "local_only")
    resp = client.post("/api/outreach/draft",
                       json={"contact": CONTACT, "angle": "reconnect",
                             "context": "met at a conference"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok" and body["draft"]
    assert mock_gemini["prompts"] == [], "outreach prompt reached Gemini under Local only"
    assert offline_calls["anthropic"] == [], "outreach prompt reached Anthropic under Local only"


def test_local_only_outreach_uses_the_router_not_the_sdk(
        client, patch_app, mock_gemini):
    """The draft is whatever the router returned, not a Gemini answer."""
    _routing(patch_app, "local_only")
    seen = []

    def _router(messages, **kw):
        seen.append((messages, kw))
        return "Subject: hi\n\nrouted draft"

    patch_app("_generate_text", _router)
    resp = client.post("/api/outreach/draft", json={"contact": CONTACT})
    assert resp.get_json()["draft"] == "Subject: hi\n\nrouted draft"
    assert len(seen) == 1 and CONTACT in seen[0][0][0]["content"]
    assert mock_gemini["prompts"] == []


def test_router_refusal_falls_back_to_the_template(client, patch_app):
    from agent_friday.services.model_router import RoutedRefusal
    patch_app("_generate_text", lambda *a, **k: RoutedRefusal("needs a local model"))
    resp = client.post("/api/outreach/draft", json={"contact": CONTACT})
    draft = resp.get_json()["draft"]
    assert "needs a local model" not in draft
    assert draft.startswith("Subject:")


def test_cloud_allowed_outreach_still_drafts(client, patch_app):
    _routing(patch_app, "cloud_only")
    patch_app("_generate_text", lambda *a, **k: "A warm note to Pat.")
    resp = client.post("/api/outreach/draft", json={"company": "Acme"})
    assert resp.status_code == 200
    assert resp.get_json()["draft"] == "A warm note to Pat."


def test_cloud_allowed_outreach_through_the_real_router_uses_the_cloud_leg(
        client, patch_app, offline_calls):
    """Real router body, fake transport: cloud_only reaches the Anthropic leg."""
    _routing(patch_app, "cloud_only")
    resp = client.post("/api/outreach/draft", json={"contact": CONTACT})
    assert resp.status_code == 200 and resp.get_json()["draft"]
    assert offline_calls["anthropic"], "cloud_only should have used the cloud leg"
