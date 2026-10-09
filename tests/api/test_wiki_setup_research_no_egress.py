"""POST /api/wiki/setup-research is a deterministic template: no model call.

The name, birthdate and location are written to three pending drafts on this
machine and go nowhere else, whatever keys are configured or the routing mode.
"""
from __future__ import annotations

import pytest

NAME = "Sample Person"
BIRTHDATE = "1970-02-03"
PLACE = "Sampletown"


@pytest.fixture
def model_calls(patch_app, mock_gemini, offline_calls):
    """Every way a model could be reached: the router, the Anthropic client,
    the Gemini SDK, and the raw transports."""
    router = []
    patch_app("_generate_text", lambda *a, **k: router.append((a, k)) or "MODEL TEXT")
    patch_app("cloud_key_present", lambda *a, **k: True)
    return router


@pytest.fixture
def proposals(patch_app):
    sink = []

    def _propose(**kw):
        sink.append(kw)
        return "pid-%d" % len(sink)

    patch_app("_propose_wiki_update", _propose)
    return sink


def _post(client):
    return client.post("/api/wiki/setup-research", json={
        "full_name": NAME, "birthdate": BIRTHDATE, "location": PLACE})


def test_no_model_is_called_even_with_a_cloud_key(
        client, model_calls, proposals, mock_gemini, offline_calls):
    resp = _post(client)
    assert resp.status_code == 200
    assert model_calls == [], "setup research called the model router"
    assert mock_gemini["prompts"] == []
    assert offline_calls["anthropic"] == []
    assert offline_calls["requests"] == [] and offline_calls["urlopen"] == []


def test_output_is_the_template_with_the_profile_fields(client, model_calls, proposals):
    body = _post(client).get_json()
    assert body["status"] == "ok" and body["count"] == 3
    assert [d["file"] for d in body["drafts"]] == [
        "identity/core-profile.md", "identity/career-timeline.md",
        "identity/education.md"]
    assert len(proposals) == 3
    for kw in proposals:
        assert "MODEL TEXT" not in kw["new_value"]
        assert "**Name:** %s" % NAME in kw["new_value"]
        assert "**Birthdate:** %s" % BIRTHDATE in kw["new_value"]
        assert "**Location:** %s" % PLACE in kw["new_value"]


def test_missing_fields_are_marked_for_research(client, model_calls, proposals):
    resp = client.post("/api/wiki/setup-research", json={})
    assert resp.status_code == 200
    assert all("[needs research]" in kw["new_value"] for kw in proposals)
    assert model_calls == []
