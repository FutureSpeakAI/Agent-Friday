"""A key pasted into chat never reaches the model, the transcript or the
history, and the reply offers the secure form instead.

Every chat entry point (`/api/chat`, `/api/chat/send`, `/api/chat/stream`)
cleans the message before the turn starts (routes/chat._withhold_pasted_keys).
The tests read what actually reached the model and what was persisted.
"""
from __future__ import annotations

import json

import pytest

from tests.api.turn_context_helpers import CHAT_ROUTES, capture_requests, route_to, whole_request
from tests.unit.test_pasted_secrets import ANTHROPIC, GITHUB, GOOGLE, OPENAI

PASTES = [("anthropic", f"Here's my Anthropic key, hook it up: {ANTHROPIC}", ANTHROPIC),
          ("openai", f"and the OpenAI one is {OPENAI} thanks", OPENAI),
          ("gemini", f"Gemini key: {GOOGLE}", GOOGLE),
          ("github", f"my github token is {GITHUB}, remember it", GITHUB)]


@pytest.fixture
def captured(patch_app):
    return capture_requests(patch_app)


def _new_conv(client):
    return client.post("/api/conversations", json={"title": "Keys"}).get_json()["conversation"]["id"]


def _payload(route, r):
    if route != "/api/chat/stream":
        return r.get_json()
    done = [json.loads(line[6:]) for line in r.get_data(as_text=True).splitlines()
            if line.startswith("data: ") and '"done"' in line]
    assert done, r.get_data(as_text=True)[:400]
    return done[-1]["payload"]


ROUTES = CHAT_ROUTES + [("/api/chat/stream", None)]


@pytest.mark.parametrize("route,provider", ROUTES)
@pytest.mark.parametrize("shape,message,key", PASTES, ids=[p[0] for p in PASTES])
def test_a_pasted_key_reaches_no_model_and_no_store(client, captured, monkeypatch,
                                                    route, provider, shape, message, key):
    route_to(monkeypatch, provider)
    cid = _new_conv(client)
    r = client.post(route, json={"message": message, "conversation_id": cid})
    assert r.status_code == 200, r.data[:300]
    d = _payload(route, r)

    # Nothing the model was handed holds the key.
    assert captured, "no model call was made"
    assert key not in whole_request(captured)

    # The reply says what was withheld, never the value, and opens the form.
    assert key not in json.dumps(d)
    assert [k["id"] for k in d["key_withheld"]["kinds"]] == [shape]
    forms = [a for a in d.get("actions") or [] if a.get("type") == "key_form"]
    assert len(forms) == 1 and forms[0]["reason"] == "pasted"

    # The transcript and the legacy history hold the marker, not the key.
    msgs = client.get(f"/api/conversations/{cid}/messages").get_json()["messages"]
    assert msgs and all(key not in (m.get("text") or "") for m in msgs)
    assert any("[credential withheld]" in (m.get("text") or "") for m in msgs)
    hist = client.get("/api/chat/history").get_data(as_text=True)
    assert key not in hist


def test_an_ordinary_message_is_untouched(client, captured):
    cid = _new_conv(client)
    r = client.post("/api/chat", json={"message": "how do API keys work?", "conversation_id": cid})
    d = r.get_json()
    assert "key_withheld" not in d
    assert not [a for a in d.get("actions") or [] if a.get("type") == "key_form"]
    assert "how do API keys work?" in whole_request(captured)
