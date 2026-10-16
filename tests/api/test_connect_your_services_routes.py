"""Connect your services over HTTP, on a fresh install with zero keys.

Fake credentials are assembled at runtime so no literal here has a key's
shape. Nothing reaches the network: every provider call is a stub.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.core as core

FAKE_GROQ = "gsk_" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp" + "Oo9Nn8"   # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "MCP_SERVERS_FILE", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(agent, "_mcp_reload", lambda: {"ok": True})
    monkeypatch.setattr(agent, "_MCP_MANAGER", None, raising=False)
    from agent_friday.services import setup_reader
    monkeypatch.setattr(setup_reader, "local_model", lambda: None)
    monkeypatch.setattr(setup_reader, "cloud_model", lambda: None)
    import os
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.delenv("GROQ_API_KEY")
    yield {"path": tmp_path}
    os.environ.pop("GROQ_API_KEY", None)


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


# ── The list ─────────────────────────────────────────────────────────────────

def test_a_fresh_install_lists_every_category_with_guides_and_no_errors(client, home):
    d = client.get("/api/setup/connections").get_json()
    assert d["ok"] and not d["errors"], d["errors"]
    cats = [c["id"] for c in d["categories"]]
    assert cats[:5] == ["models", "creative", "search", "accounts", "mcp"]
    items = {it["id"]: it for it in d["items"]}
    for iid in ("provider:anthropic", "provider:openai", "provider:google-gemini",
                "provider:openrouter", "provider:groq", "provider:mistral", "provider:xai",
                "provider:deepseek", "provider:together", "local:ollama", "local:lmstudio",
                "custom:openai", "connector:higgsfield", "provider:elevenlabs",
                "provider:brave", "provider:firecrawl", "google", "connector:github",
                "connector:notion", "connector:slack", "mcp:add"):
        assert iid in items, iid
    by_cat = {}
    for it in d["items"]:
        by_cat.setdefault(it["category"], []).append(it["id"])
    assert "connector:higgsfield" in by_cat["creative"] and "provider:elevenlabs" in by_cat["creative"]
    assert "provider:brave" in by_cat["search"]
    assert {"google", "connector:github", "connector:notion", "connector:slack"} <= set(by_cat["accounts"])
    assert "mcp:add" in by_cat["mcp"]
    # Keyed entries: a sign-up link, three steps, what it can reach, a test.
    for iid in ("provider:openrouter", "provider:groq", "provider:brave"):
        it = items[iid]
        assert it["connect"]["signup_url"].startswith("https://"), iid
        assert len(it["how"]) >= 3 and it["access"] and it["cost"], iid
        assert it["test"] == "/api/setup/connections/%s/test" % iid
    # Nothing is connected, nothing needs attention, nothing is recommended-by-default.
    for it in d["items"]:
        assert it["status"] in ("not_connected", "external"), (it["id"], it["status"])
    assert d["local_only"] is False
    soon = {s["label"].split(" ")[0].lower() for s in d["coming_soon"]}
    assert {"replicate", "fal", "runway", "tavily", "exa", "spotify"} <= soon
    assert not any(s["id"] in items for s in d["coming_soon"])
    assert all(m["url"].startswith("https://") for m in d["curated_mcp"])


def test_a_built_in_switched_off_provider_is_switched_on_by_its_key(client, home):
    it = next(i for i in client.get("/api/setup/connections").get_json()["items"]
              if i["id"] == "provider:groq")
    assert it["connect"]["enable"] == "/api/providers/groq"


# ── "I'll just use local AI" ─────────────────────────────────────────────────

def test_local_ai_is_an_answer_and_the_key_advice_stops(client, home):
    assert client.get("/api/setup/connections").get_json()["one_key"] is not None
    r = client.post("/api/setup/connections/local-only", json={"on": True})
    assert r.status_code == 200 and r.get_json()["local_only"] is True
    d = client.get("/api/setup/connections").get_json()
    assert d["local_only"] is True and d["one_key"] is None
    from agent_friday.services import setup_chat
    assert setup_chat._local_ai_only() is True
    client.post("/api/setup/connections/local-only", json={"on": False})
    assert client.get("/api/setup/connections").get_json()["local_only"] is False


def test_local_ai_changes_no_privacy_or_routing_setting(client, home, monkeypatch):
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda d, **kw: saved.append(dict(d)))
    client.post("/api/setup/connections/local-only", json={"on": True})
    assert saved == []


# ── Test connection ──────────────────────────────────────────────────────────

def test_test_connection_says_not_connected_then_fail_then_pass(client, home, monkeypatch):
    from agent_friday.services import credential_store as cs
    import requests
    r = client.post("/api/setup/connections/provider:groq/test").get_json()
    assert r["state"] == "not_connected" and r["fix"], r
    try:
        assert client.post("/api/providers/groq/key", json={"key": FAKE_GROQ}).status_code == 200
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(401, {"error": "invalid"}))
        r = client.post("/api/setup/connections/provider:groq/test")
        body = r.get_data(as_text=True)
        r = r.get_json()
        assert r["state"] == "fail" and "refused" in r["text"] and r["fix"], r
        assert FAKE_GROQ not in body and FAKE_GROQ[-8:] not in body
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, {"data": [{"id": "m"}]}))
        r = client.post("/api/setup/connections/provider:groq/test").get_json()
        assert r["state"] == "pass", r
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(429, {"error": {"message": "insufficient_quota"}}))
        r = client.post("/api/setup/connections/provider:groq/test").get_json()
        assert r["state"] == "fail" and "credit" in r["text"], r
        listing = client.get("/api/setup/connections").get_data(as_text=True)
        assert FAKE_GROQ not in listing and FAKE_GROQ[-8:] not in listing
    finally:
        cs.delete_provider_key("groq")
        cs.clear_provider_key_live("groq")


def test_an_unreachable_provider_is_unknown_not_failed(client, home, monkeypatch):
    from agent_friday.services import credential_store as cs
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("offline")
    try:
        client.post("/api/providers/groq/key", json={"key": FAKE_GROQ})
        monkeypatch.setattr(requests, "get", boom)
        r = client.post("/api/setup/connections/provider:groq/test").get_json()
        assert r["state"] == "unknown" and "isn't a verdict" in r["text"], r
    finally:
        cs.delete_provider_key("groq")
        cs.clear_provider_key_live("groq")


def test_a_local_runtime_that_is_not_running_says_how_to_start_it(client, home, monkeypatch):
    import agent_friday.routing.ollama_manager as om

    class _Off:
        def list_models(self):
            return []

        def is_available(self):
            return False
    monkeypatch.setattr(om, "get_manager", lambda *a, **k: _Off())
    r = client.post("/api/setup/connections/local:ollama/test").get_json()
    assert r["state"] == "fail" and "ollama.com/download" in r["fix"], r


# ── MCP servers ──────────────────────────────────────────────────────────────

def test_an_mcp_server_is_added_by_address_listed_and_removed(client, home):
    r = client.post("/api/setup/connections/mcp",
                    json={"name": "docs", "url": "https://mcp.context7.com/mcp"})
    assert r.status_code == 200 and r.get_json()["item"] == "mcp:docs", r.get_json()
    cfg = json.loads((home["path"] / "mcp_servers.json").read_text(encoding="utf-8"))
    assert cfg["servers"]["docs"]["url"] == "https://mcp.context7.com/mcp"
    items = {i["id"]: i for i in client.get("/api/setup/connections").get_json()["items"]}
    assert items["mcp:docs"]["category"] == "mcp"
    assert items["mcp:docs"]["connect"]["sign_in"]
    dup = client.post("/api/setup/connections/mcp", json={"name": "docs", "url": "https://x.example/mcp"})
    assert dup.status_code == 422 and "already" in dup.get_json()["message"]
    assert client.delete("/api/setup/connections/mcp/docs").status_code == 200
    cfg = json.loads((home["path"] / "mcp_servers.json").read_text(encoding="utf-8"))
    assert "docs" not in cfg["servers"]


def test_an_mcp_server_by_command_is_split_not_run(client, home):
    r = client.post("/api/setup/connections/mcp",
                    json={"name": "local-tools", "command": 'npx -y "some server" --flag'})
    assert r.status_code == 200, r.get_json()
    spec = json.loads((home["path"] / "mcp_servers.json").read_text(encoding="utf-8"))["servers"]["local-tools"]
    assert spec["command"] == "npx" and spec["args"] == ["-y", "some server", "--flag"]


@pytest.mark.parametrize("body,words", [
    ({"name": "x", "url": "http://mcp.example.com/mcp"}, "https"),
    ({"name": "x", "url": "https://mcp.example.com/mcp?apiKey=abc"}, "keys out of the address"),
    ({"name": "x", "url": "https://user:pw@mcp.example.com/mcp"}, "password"),
    ({"name": "Bad Name!", "url": "https://mcp.example.com/mcp"}, "short name"),
    ({"name": "x", "url": "https://a.example/mcp", "command": "npx x"}, "not both"),
    ({"name": "x"}, "not both"),
])
def test_an_mcp_server_the_screen_refuses_in_plain_words(client, home, body, words):
    r = client.post("/api/setup/connections/mcp", json=body)
    assert r.status_code == 422 and words in r.get_json()["message"], r.get_json()


def test_a_connector_server_is_removed_from_its_own_card_only(client, home):
    r = client.delete("/api/setup/connections/mcp/notion")
    assert r.status_code == 422 and "its own card" in r.get_json()["message"]


# ── Local and custom endpoints ───────────────────────────────────────────────

def test_a_custom_endpoint_is_added_without_a_key_and_gets_its_own_card(client, home):
    from agent_friday.services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    try:
        r = client.post("/api/setup/connections/endpoint",
                        json={"kind": "custom", "label": "Team Gateway",
                              "base_url": "https://gateway.example.com/v1"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["item"] == "custom:team-gateway"
        p = reg.get_provider("team-gateway")
        assert p["base_url"] == "https://gateway.example.com/v1" and p["enabled"] is True
        items = {i["id"]: i for i in client.get("/api/setup/connections").get_json()["items"]}
        card = items["custom:team-gateway"]
        assert card["connect"]["kind"] == "secret"
        assert card["connect"]["endpoint"] == "/api/providers/team-gateway/key"
        r = client.post("/api/setup/connections/endpoint",
                        json={"kind": "custom", "label": "Plain", "base_url": "http://gateway.example.com/v1"})
        assert r.status_code == 422 and "https" in r.get_json()["message"]
    finally:
        reg.remove_provider("team-gateway")


def test_lm_studio_stays_on_this_computer(client, home):
    from agent_friday.services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    try:
        r = client.post("/api/setup/connections/endpoint",
                        json={"kind": "lmstudio", "base_url": "https://example.com/v1"})
        assert r.status_code == 422
        r = client.post("/api/setup/connections/endpoint", json={"kind": "lmstudio", "base_url": ""})
        assert r.status_code == 200, r.get_json()
        p = reg.get_provider("lmstudio")
        assert p["base_url"] == "http://localhost:1234/v1"
        assert (p.get("auth") or {}).get("type") == "none"
    finally:
        reg.remove_provider("lmstudio")


# ── The gate ─────────────────────────────────────────────────────────────────

def test_the_actions_are_refused_from_another_origin(client, home):
    r = client.post("/api/setup/connections/local-only", json={"on": True},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
