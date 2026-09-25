"""The checklist and the key check say which one key is in use, and that it
is enough; saving Anthropic or OpenRouter is followed by a one-token check.

No key value ever appears in a response. Fake keys are assembled at runtime.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.core as core

FAKE_OR = "sk-" + "or-" + "v1-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp"   # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch, patch_app):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "_SETUP_MARKER", tmp_path / ".setup_complete")
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "get_provider_key", lambda name: None)
    monkeypatch.setattr(cs, "provider_key_status",
                        lambda name: "connected" if name == "openrouter" else "missing")
    patch_app("get_anthropic_client", lambda *a, **k: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OR)
    return tmp_path


def test_the_checklist_names_the_one_key_in_use_and_says_it_is_enough(client, home):
    d = client.get("/api/setup/connections").get_json()
    one = d["one_key"]
    assert one["in_use"] == "openrouter" and one["sufficient"] is True
    assert "OpenRouter" in one["line"] and "enough" in one["line"]
    assert [lnk["url"] for lnk in one["links"]] == [
        "https://console.anthropic.com/settings/keys", "https://openrouter.ai/keys"]
    ai = [it for it in d["items"] if it["group"] == "ai" and it["id"] != "local"]
    assert [it["id"] for it in ai[:2]] == ["provider:anthropic", "provider:openrouter"]
    orow = ai[1]
    assert orow["connect"]["verify"] == "/api/setup/verify-key/openrouter"
    assert orow["connect"]["signup_url"] == "https://openrouter.ai/keys"
    assert "enough" in orow["detail"]
    assert ai[0]["connect"]["signup_url"] == "https://console.anthropic.com/settings/keys"
    assert FAKE_OR not in json.dumps(d)


class _Resp:
    def __init__(self, code, body=""):
        self.status_code, self.text = code, body


@pytest.mark.parametrize("code, body, verdict, can_think", [
    (200, "{}", "ok", True),
    (401, "", "rejected", False),
    (402, '{"error":{"message":"Insufficient credit balance"}}', "no_credit", False),
])
def test_the_key_check_reuses_the_one_token_verdict(client, home, monkeypatch,
                                                    code, body, verdict, can_think):
    import requests
    sent = []

    def _post(url, headers=None, json=None, timeout=None, **kw):
        sent.append({"url": url, "json": json, "headers": headers or {}})
        return _Resp(code, body)
    monkeypatch.setattr(requests, "post", _post)
    d = client.post("/api/setup/verify-key/openrouter", json={}).get_json()
    assert d["ok"] and d["verdict"] == verdict and d["can_think"] is can_think
    assert sent[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert sent[0]["json"]["max_tokens"] == 1
    assert sent[0]["json"]["model"] == "anthropic/claude-haiku-4.5"
    assert FAKE_OR not in json.dumps(d)


def test_the_key_check_is_only_for_the_two_thinking_providers(client, home):
    assert client.post("/api/setup/verify-key/brave", json={}).status_code == 404
