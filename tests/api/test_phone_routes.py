"""Settings → Phone over HTTP: local only, secrets in and never out."""
import json

import pytest

from agent_friday.phone import config

FAKE_SECRET = "fakesecret" + "c" * 22        # pragma: allowlist secret


@pytest.fixture
def phone_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    return tmp_path


def test_the_blueprint_is_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/phone/status" in rules and "/api/phone/secret" in rules
    assert not any(r.startswith("/twilio") for r in rules)     # webhooks live elsewhere


def test_status_is_off_by_default_and_carries_no_secret(client, phone_home):
    config.set_secret("api_key_secret", FAKE_SECRET)
    raw = client.get("/api/phone/status").get_data(as_text=True)
    d = json.loads(raw)
    assert d["ok"] and d["config"]["enabled"] is False
    assert d["config"]["api_key_secret"] == "stored"
    assert FAKE_SECRET not in raw


def test_a_secret_goes_in_and_only_its_status_comes_back(client, phone_home):
    r = client.post("/api/phone/secret", json={"name": "auth_token", "value": FAKE_SECRET})
    assert r.status_code == 200 and FAKE_SECRET not in r.get_data(as_text=True)
    assert config.get_secret("auth_token") == FAKE_SECRET
    assert client.delete("/api/phone/secret/auth_token").get_json()["status"] == "missing"


@pytest.mark.parametrize("headers", [
    {"CF-Connecting-IP": "203.0.113.9"},
    {"X-Forwarded-For": "203.0.113.9"},
])
def test_a_tunnelled_request_cannot_touch_phone_settings(app, phone_home, headers):
    c = app.test_client()          # fresh client: no session carried over
    r = c.post("/api/phone/secret", json={"name": "auth_token", "value": FAKE_SECRET},
               headers=headers, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403)
    assert config.get_secret("auth_token") is None
    r = c.post("/api/phone/config", json={"patch": {"enabled": True}},
               headers=headers, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code in (401, 403) and config.load()["enabled"] is False


def test_an_authenticated_remote_session_is_still_refused(app, phone_home, monkeypatch):
    from agent_friday.routes import phone as phone_routes
    monkeypatch.setattr(phone_routes, "_is_local_request", lambda: False)
    r = app.test_client().post("/api/phone/config", json={"patch": {"enabled": True}})
    assert r.status_code == 403 and config.load()["enabled"] is False


def test_writes_need_same_origin_json(client, phone_home):
    assert client.post("/api/phone/config", data="patch=1").status_code == 415
    r = client.post("/api/phone/config", json={"patch": {"voicemail": False}},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and config.load()["voicemail"] is True


def test_config_validation_is_reported(client, phone_home):
    r = client.post("/api/phone/config", json={"patch": {"owner_cell_verified": True}})
    assert r.status_code == 400
    r = client.post("/api/phone/config", json={"patch": {"owner_cell": "(512) 555-0111"}})
    assert r.get_json()["config"]["owner_cell"] == "+15125550111"


def test_test_text_needs_a_verified_cell(client, phone_home):
    r = client.post("/api/phone/test-sms", json={})
    assert r.status_code == 400 and "verify" in r.get_json()["error"]
