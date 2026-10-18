"""The publishing accounts say what is true, and post nothing to check.

* A Medium integration token is checked with Medium's own account lookup
  (GET /v1/me): a valid token connects, a rejected one does not. It used to
  be checked with a follower lookup Medium does not have, so every token,
  good or bad, was reported as failed.
* "Check connection" (it was "Test post", which said a post was sent while
  nothing was) checks the saved credential, posts nothing, and says so; it
  never calls an adapter hook that could publish.
* LinkedIn, X, Instagram, Reddit and TikTok have a built sign-in flow that
  needs the owner's own developer app. Without one, connecting says which
  details are needed and where the sign-in comes back to; with them, the
  secret goes to the credential store (never platforms.json, never echoed)
  and the sign-in address is issued. YouTube says it can't be connected yet.
* A held, blocked or failed post's notice opens the post's card in Media
  (where posts are now), and a re-auth notice opens Settings, Connections.

No network: Medium's transport seam is faked; the credential store is an
in-memory double; content store and platform config are on tmp.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import agent_friday.server as friday_server
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import media_index
from agent_friday.services import platforms as preg
from agent_friday.services import publisher as pub
from agent_friday.services.platforms import base as pbase
from agent_friday.services.platforms import medium as med

TOKEN = "synthetic-medium-token-0001"            # pragma: allowlist secret
APP_SECRET = "synthetic-app-secret-0002"         # pragma: allowlist secret
RETRY_LAST = 99                                  # past the retry ladder: fail now


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    yield tmp_path
    preg._reset_for_tests()


@pytest.fixture(autouse=True)
def stored(monkeypatch):
    """In-memory credential store: what was stored, under which key."""
    from agent_friday.services import credential_store as cs
    keys = {}

    def _write(path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return "fake-vault"

    monkeypatch.setattr(cs, "write_secret", _write)
    monkeypatch.setattr(cs, "read_secret", lambda p: Path(p).read_bytes())
    monkeypatch.setattr(cs, "audit_event", lambda *a, **k: None)
    monkeypatch.setattr(cs, "set_provider_key",
                        lambda prov, val: (keys.__setitem__(prov, val), "fake-vault")[1])
    monkeypatch.setattr(cs, "get_provider_key", lambda prov: keys.get(prov))
    monkeypatch.setattr(cs, "delete_provider_key", lambda prov: keys.pop(prov, None) is not None)
    return keys


@pytest.fixture
def client():
    friday_server.app.config.update(TESTING=True)
    return friday_server.app.test_client()


def _medium_answers(monkeypatch, code, data):
    calls = []

    def fake(method, url, bearer, payload=None, timeout=15.0):
        calls.append((method, url))
        return code, data
    monkeypatch.setattr(med, "_http_json", fake)
    return calls


# ── Medium token check ──────────────────────────────────────────────────────

def test_medium_token_check_follows_the_token(client, monkeypatch):
    calls = _medium_answers(monkeypatch, 200, {"data": {"id": "author-0001",
                                                        "username": "synthetic_writer"}})
    res = client.post("/api/content/platforms/medium/connect", json={"secret": TOKEN})
    good = res.get_json()
    assert good["ok"] is True and good["verified"] is True, good
    assert ("GET", "https://api.medium.com/v1/me") in calls
    assert all(m == "GET" for m, _ in calls), "checking a token must not post"
    assert TOKEN not in res.get_data(as_text=True)

    preg._reset_for_tests()
    _medium_answers(monkeypatch, 401, {})
    bad = client.post("/api/content/platforms/medium/connect",
                      json={"secret": "synthetic-rejected-token"}).get_json()
    assert bad["ok"] is False and bad["verified"] is False, bad


# ── Check connection ───────────────────────────────────────────────────────

def test_check_connection_posts_nothing_and_says_so(client):
    adapter = preg.get_adapter("mock")
    adapter.reset()
    body = client.post("/api/content/platforms/mock/test", json={}).get_json()
    assert body["ok"] is True and body["posted"] is False, body
    assert "Nothing was posted" in body["message"], body
    assert adapter.publish_calls == 0


def test_check_connection_never_runs_a_publishing_hook(client, monkeypatch):
    adapter = preg.get_adapter("mock")
    adapter.reset()
    ran = []
    monkeypatch.setattr(adapter, "test_post", lambda: ran.append("posted") or {"ok": True},
                        raising=False)
    client.post("/api/content/platforms/mock/test", json={})
    assert ran == [], "Check connection ran an adapter hook that can publish"


def test_check_connection_names_the_medium_account(client, monkeypatch, stored):
    stored["platform_medium"] = TOKEN
    _medium_answers(monkeypatch, 200, {"data": {"id": "author-0001",
                                                "username": "synthetic_writer"}})
    body = client.post("/api/content/platforms/medium/test", json={}).get_json()
    assert body["ok"] is True, body
    assert "synthetic_writer" in body["message"] and "Nothing was posted" in body["message"]

    _medium_answers(monkeypatch, 401, {})
    body = client.post("/api/content/platforms/medium/test", json={}).get_json()
    assert body["ok"] is False and "Nothing was posted" in body["message"], body


# ── OAuth platforms: the owner's developer app ─────────────────────────────

@pytest.mark.parametrize("name,fields", [
    ("linkedin", ["client_id", "client_secret"]),
    ("x_twitter", ["client_id", "client_secret"]),
    ("instagram", ["client_id", "client_secret"]),
    ("reddit", ["client_id", "client_secret"]),
    ("tiktok", ["client_key", "client_secret"]),
], ids=["linkedin", "x", "instagram", "reddit", "tiktok"])
def test_an_oauth_platform_without_an_app_asks_for_its_details(client, name, fields):
    body = client.post(f"/api/content/platforms/{name}/connect", json={}).get_json()
    assert body["ok"] is False and body["needs"] == "client", body
    assert body["fields"] == fields
    assert body["redirect_uri"].endswith(f"/api/content/platforms/{name}/callback"), body
    assert "developer app" in body["message"]


def test_app_details_are_stored_safely_and_the_sign_in_is_issued(client, stored, env):
    res = client.post("/api/content/platforms/linkedin/connect",
                      json={"client_id": "synthetic-client-id", "client_secret": APP_SECRET})
    body = res.get_json()
    assert body["ok"] is True and body["mode"] == "oauth", body
    assert body["connect_url"].startswith("https://www.linkedin.com/oauth/v2/authorization?")
    assert "client_id=synthetic-client-id" in body["connect_url"]
    assert stored.get("platform_linkedin") == APP_SECRET
    config = (env / "platforms.json").read_text(encoding="utf-8")
    assert "synthetic-client-id" in config and APP_SECRET not in config
    assert APP_SECRET not in res.get_data(as_text=True)


def test_youtube_says_it_cannot_be_connected_yet(client):
    body = client.post("/api/content/platforms/youtube/connect", json={}).get_json()
    assert body["ok"] is False and "connect_url" not in body, body
    assert "can't be connected" in body["message"]


# ── Notices go where posts are ─────────────────────────────────────────────

def test_a_held_post_notice_opens_its_card_in_media(monkeypatch):
    notes = []
    monkeypatch.setattr(pub, "_notify", lambda title, body="", **kw: notes.append(kw))
    post = cpl.create_post(body="A synthetic post.", platforms=["mock"])["post"]
    target = post["targets"][0]
    pub._hold(target, "held for review")
    action = notes[-1]["actions"][0]
    assert action["target"] == {"workspace": "media",
                                "card": media_index.post_card_id(post["id"])}, action


def test_a_reauth_notice_opens_connections(monkeypatch):
    notes = []
    monkeypatch.setattr(pub, "_notify", lambda title, body="", **kw: notes.append(kw))
    post = cpl.create_post(body="A synthetic post.", platforms=["mock"])["post"]
    target = dict(post["targets"][0], attempt=RETRY_LAST)

    class NoRefresh:
        def refresh(self):
            return False
    pub._handle_failure(target, NoRefresh(), {"error": "auth_error"}, stage="publish",
                        started=time.time())
    assert notes[-1]["actions"][0]["target"] == {"workspace": "settings", "tab": "connections"}
