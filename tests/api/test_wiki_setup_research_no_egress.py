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


@pytest.fixture
def request_thread_calls(offline_calls, monkeypatch):
    """Transport calls made on THIS thread, the one the test client runs the
    route on. The shared `offline_calls` recorder also sees other threads in
    the process (a seat or alias read on a background timer), which are not
    this route's traffic."""
    import threading
    import urllib.request
    import requests

    me = threading.get_ident()
    mine = []

    def wrap(name, fn):
        def call(*a, **k):
            if threading.get_ident() == me:
                mine.append((name, a[0] if a else k.get("url")))
            return fn(*a, **k)
        return call

    monkeypatch.setattr(urllib.request, "urlopen", wrap("urlopen", urllib.request.urlopen))
    monkeypatch.setattr(requests, "get", wrap("get", requests.get))
    monkeypatch.setattr(requests, "post", wrap("post", requests.post))
    return mine


def _off_machine(url) -> bool:
    url = url.get_full_url() if hasattr(url, "get_full_url") else str(url)
    host = url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]").lower()
    return host not in ("localhost", "127.0.0.1", "::1")


def _post(client):
    return client.post("/api/wiki/setup-research", json={
        "full_name": NAME, "birthdate": BIRTHDATE, "location": PLACE})


def test_no_model_is_called_even_with_a_cloud_key(
        client, model_calls, proposals, mock_gemini, offline_calls, request_thread_calls):
    resp = _post(client)
    assert resp.status_code == 200
    assert model_calls == [], "setup research called the model router"
    assert mock_gemini["prompts"] == []
    assert offline_calls["anthropic"] == []
    # The route itself makes no network call at all, not even to this PC.
    assert request_thread_calls == [], request_thread_calls
    # And nothing in the process sent anything off this machine or carried
    # the person's fields while it ran.
    for row in offline_calls["requests"] + offline_calls["urlopen"]:
        url = row.get("url") if isinstance(row, dict) else row
        assert not _off_machine(url), row
        assert not any(v in repr(row) for v in (NAME, BIRTHDATE, PLACE)), row


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
