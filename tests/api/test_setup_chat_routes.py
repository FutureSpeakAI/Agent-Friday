"""The setup chat over HTTP: local only, every service listed, no secret out.

Fake credentials are assembled at runtime so no literal here has a key's shape.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.core as core

FAKE_KEY = "sk-" + "ant-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp"   # pragma: allowlist secret
FAKE_TWILIO = "fake" + "twiliosecret" + "q" * 20                  # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda d: saved.append(dict(d)))
    # The route module holds its own binding from `from agent_friday.core import`.
    from agent_friday.routes import core_routes
    monkeypatch.setattr(core_routes, "_save_settings", lambda d: saved.append(dict(d)))
    from agent_friday.services import setup_reader
    monkeypatch.setattr(setup_reader, "local_model", lambda: None)
    monkeypatch.setattr(setup_reader, "cloud_model", lambda: None)
    # Storing a key hot-reloads it into the process; put the process back.
    import os
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
    monkeypatch.setattr(core, "ANTHROPIC_API_KEY", getattr(core, "ANTHROPIC_API_KEY", ""),
                        raising=False)
    monkeypatch.setattr(core, "_anthropic_client", getattr(core, "_anthropic_client", None),
                        raising=False)
    return {"path": tmp_path, "settings": saved}


def test_the_routes_are_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    for r in ("/api/setup-chat/state", "/api/setup-chat/answer", "/api/setup/connections",
              "/api/setup-chat/skip-all", "/api/setup-chat/profile"):
        assert r in rules, r


def test_a_fresh_install_gets_the_first_stage(client, home):
    d = client.get("/api/setup-chat/state").get_json()
    assert d["ok"] and d["stage"] == "welcome" and d["consent_done"] is False
    assert d["completed"] is False and d["transcript"] == []


EXPECTED = [
    "local", "provider:anthropic", "provider:openai", "provider:openrouter",
    "provider:google-gemini", "provider:huggingface", "provider:groq",
    "provider:together", "provider:fireworks", "provider:mistral",
    "provider:deepseek", "provider:xai", "provider:perplexity", "provider:cohere",
    "google", "twilio", "connector:github", "connector:slack", "connector:discord",
    "channel:telegram", "channel:discord", "connector:linear", "connector:notion",
    "connector:higgsfield", "platform:youtube", "platform:linkedin",
    "platform:instagram", "platform:reddit", "platform:mastodon",
    "platform:twitter", "platform:tiktok", "platform:bluesky", "platform:medium",
    "provider:brave", "provider:firecrawl", "provider:elevenlabs",
    "provider:inworld", "cloudflare",
]


def test_the_checklist_lists_every_supported_service(client, home):
    d = client.get("/api/setup/connections").get_json()
    assert d["ok"] and not d["errors"], d["errors"]
    ids = {it["id"] for it in d["items"]}
    missing = [x for x in EXPECTED if x not in ids]
    assert not missing, missing
    for it in d["items"]:
        assert it["unlocks"], it["id"]
        assert it["permissions"], it["id"]
        assert it["status"] in ("connected", "needs_attention", "not_connected",
                                "skipped", "external"), it


def test_google_shows_every_scope_in_plain_words_with_send_and_modify_off(client, home):
    from agent_friday.services import google_accounts as ga
    g = next(it for it in client.get("/api/setup/connections").get_json()["items"]
             if it["id"] == "google")
    scopes = {p.get("scope"): p for p in g["permissions"]}
    for sc in ga.GOOGLE_MULTI_SCOPES:
        assert sc in scopes and not scopes[sc]["text"].startswith("http"), sc
    for sc in (ga.GMAIL_SEND, ga.GMAIL_MODIFY):
        assert scopes[sc]["optional"] is True and scopes[sc]["default"] is False
    assert g["connect"]["endpoint"] == "/api/google/accounts/connect"


def test_cloudflare_is_honest_about_being_outside_friday(client, home):
    c = next(it for it in client.get("/api/setup/connections").get_json()["items"]
             if it["id"] == "cloudflare")
    assert c["status"] == "external" and c["connect"]["kind"] == "external"
    assert "no Cloudflare credential" in c["permissions"][0]["text"]


def test_no_secret_value_ever_comes_back(client, home):
    from agent_friday.phone import config as pc
    from agent_friday.services import credential_store as cs
    try:
        r = client.post("/api/providers/anthropic/key", json={"key": FAKE_KEY})
        assert r.status_code == 200 and FAKE_KEY not in r.get_data(as_text=True)
        pc.set_secret("auth_token", FAKE_TWILIO)
        raw = client.get("/api/setup/connections").get_data(as_text=True)
        assert FAKE_KEY not in raw and FAKE_TWILIO not in raw
        assert FAKE_KEY[-12:] not in raw, "not even a masked tail"
        items = {it["id"]: it for it in json.loads(raw)["items"]}
        assert items["provider:anthropic"]["status"] == "connected"
        assert items["twilio"]["status"] == "needs_attention"
    finally:
        cs.delete_provider_key("anthropic")
        cs.clear_provider_key_live("anthropic")
        pc.delete_secret("auth_token")


def test_a_secure_field_goes_to_the_store_and_never_into_the_chat(client, home):
    from agent_friday.services import credential_store as cs
    from agent_friday.services import setup_chat, setup_profile
    try:
        client.post("/api/setup-chat/begin", json={"routing_mode": "cloud_only"})
        client.post("/api/providers/anthropic/key", json={"key": FAKE_KEY})
        assert cs.get_provider_key("anthropic") == FAKE_KEY
        state = client.get("/api/setup-chat/state").get_data(as_text=True)
        assert FAKE_KEY not in state
        assert FAKE_KEY not in setup_chat.state_path().read_text(encoding="utf-8")
        assert all(FAKE_KEY not in (e.get("text") or "")
                   for e in setup_profile.load_transcript())
    finally:
        cs.delete_provider_key("anthropic")
        cs.clear_provider_key_live("anthropic")


def test_a_key_typed_as_an_answer_is_refused_and_not_kept(client, home):
    from agent_friday.services import setup_profile
    client.post("/api/setup-chat/begin", json={"routing_mode": "cloud_only"})
    r = client.post("/api/setup-chat/answer",
                    json={"stage": "welcome", "text": "my key is " + FAKE_KEY})
    body = r.get_data(as_text=True)
    assert r.status_code == 422 and json.loads(body)["error"] == "key_shaped"
    assert FAKE_KEY not in body
    assert all(FAKE_KEY not in (e.get("text") or "")
               for e in setup_profile.load_transcript())


def test_a_skip_all_completes_setup(client, home):
    client.post("/api/setup-chat/begin", json={"routing_mode": "local_only"})
    d = client.post("/api/setup-chat/skip-all", json={}).get_json()
    assert d["completed"] is True and d["stage"] == "done"
    assert (home["path"] / ".setup_complete").exists()
    assert home["settings"][-1]["model_routing"]["mode"] == "local_only"


def test_a_remote_request_is_refused(app, home, monkeypatch):
    monkeypatch.setattr(core, "_is_local_request", lambda: False)
    c = app.test_client()
    for path in ("/api/setup-chat/state", "/api/setup/connections",
                 "/api/setup-chat/profile"):
        assert c.get(path).status_code in (401, 403), path


def test_an_authenticated_remote_session_is_still_refused(app, home, monkeypatch):
    """Past the app-wide login gate, the setup routes refuse anything not local."""
    monkeypatch.setattr(core, "_is_local_request", lambda: False)
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "a-configured-remote-key", raising=False)
    c = app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
    r = c.get("/api/setup-chat/profile")
    assert r.status_code == 403 and "own computer" in r.get_json()["error"]


def test_a_cross_origin_write_is_refused(client, home):
    r = client.post("/api/setup-chat/begin", json={},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_skipping_a_checklist_item_is_remembered(client, home):
    client.post("/api/setup/connections/provider:openai/skip", json={"skipped": True})
    items = {it["id"]: it for it in client.get("/api/setup/connections").get_json()["items"]}
    if items["provider:openai"]["status"] != "connected":
        assert items["provider:openai"]["status"] == "skipped"


def test_the_secret_shapes_are_served_without_values(client, home):
    d = client.get("/api/setup-chat/secret-shapes").get_json()
    assert d["ok"] and len(d["shapes"]) >= 15 and d["generic"]["pattern"]
