"""Two small doors on the posting routes stay shut.

* platforms.json is plain text: a secret sent inside a connect call's
  `options` (a developer app's client secret, a token, a password, an API
  key) is never written there. An OAuth platform's client secret sent that
  way goes to the credential store, like one sent at the top level.
* Opening a Media card that is not indexed reindexes posts only for a
  well-formed post card id of a post that exists, at most once a minute per
  card: any other id (malformed, unknown) costs no reindex.

No network; the credential store is an in-memory double; content store and
platform config on tmp.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import agent_friday.server as friday_server
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import media_index as mi
from agent_friday.services import platforms as preg
from agent_friday.services.platforms import base as pbase

APP_SECRET = "x-synthetic-app-secret-0003"     # pragma: allowlist secret
TOKEN = "x-synthetic-token-0004"               # pragma: allowlist secret
API_KEY = "x-synthetic-api-key-0005"           # pragma: allowlist secret


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


# ── secrets in options ──────────────────────────────────────────────────────

def test_a_client_secret_sent_in_options_never_reaches_platforms_json(client, stored, env):
    res = client.post("/api/content/platforms/linkedin/connect", json={
        "client_id": "synthetic-client-id",
        "options": {"client_secret": APP_SECRET, "access_token": TOKEN}})
    config = (env / "platforms.json").read_text(encoding="utf-8")
    assert APP_SECRET not in config and TOKEN not in config, config
    assert "synthetic-client-id" in config
    assert APP_SECRET not in res.get_data(as_text=True)
    assert stored.get("platform_linkedin") == APP_SECRET


def test_secret_looking_options_are_dropped_and_a_public_client_key_is_kept(env):
    preg.configure_platform("tiktok", {
        "client_key": "synthetic-public-client-key",
        "nested": {"api_key": API_KEY, "label": "synthetic"},
        "password": "x-synthetic-password",       # pragma: allowlist secret
        "handle": "synthetic_handle"})
    section = json.loads((env / "platforms.json").read_text(encoding="utf-8"))["tiktok"]
    text = json.dumps(section)
    assert API_KEY not in text and "x-synthetic-password" not in text, section
    assert section["client_key"] == "synthetic-public-client-key"
    assert section["handle"] == "synthetic_handle" and section["nested"] == {"label": "synthetic"}


# ── post card lookups ───────────────────────────────────────────────────────

@pytest.fixture
def reindexes(monkeypatch):
    calls = []
    monkeypatch.setattr(mi, "reindex_posts_only", lambda: calls.append(1))
    monkeypatch.setattr(mi, "get", lambda card_id: None)            # never indexed
    getattr(mi, "_POST_CARD_TRIED", {}).clear()
    return calls


def test_a_malformed_or_unknown_post_card_id_costs_no_reindex(client, reindexes):
    for cid in ("post:not-a-card", "post:0123456789ab", "post:" + "f" * 40, "post:"):
        res = client.get("/api/media/" + cid)
        assert res.status_code == 404, (cid, res.status_code)
    assert reindexes == [], "a card id that names no post made Media reindex every post"


def test_a_new_post_is_indexed_at_most_once_a_minute(client, reindexes):
    post = cpl.create_post(body="A synthetic post.", platforms=["mock"])["post"]
    card = "post:" + hashlib.sha1(("post|" + post["id"]).encode()).hexdigest()[:12]
    client.get("/api/media/" + card)
    client.get("/api/media/" + card)
    assert len(reindexes) == 1, reindexes
