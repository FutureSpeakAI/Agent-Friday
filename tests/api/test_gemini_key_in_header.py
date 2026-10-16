"""The Gemini key goes to Google in the x-goog-api-key header, never in the
URL. A URL is what an exception message, a proxy log or an error page quotes
back, so a key in its query string ends up wherever those are kept.

Both places that test a Google key are checked: Settings' Test Connection
(/api/providers/<name>/test) and the 1-token key verdict
(services/key_verdict.ping_detail).
"""
from __future__ import annotations

import requests

from agent_friday.services import key_verdict as kv

FAKE = "AI" + "zaSy" + "Hd3rT3stK3yN0tR3al0123456789abcde"  # pragma: allowlist secret


class _Resp:
    status_code = 200
    text = '{"models": []}'

    def json(self):
        return {"models": []}


def _no_key_in_url(call):
    url = call["url"]
    params = call.get("params") or {}
    assert FAKE not in url and "key=" not in url, "the key is in the URL"
    assert FAKE not in repr(params), "the key is in the query parameters"
    assert call["headers"].get("x-goog-api-key") == FAKE
    assert FAKE not in call["headers"].get("Authorization", "")


def test_test_connection_sends_the_gemini_key_in_a_header(client, monkeypatch):
    from agent_friday.routing import provider_descriptors as pd
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None, **_):
        calls.append({"url": url, "params": params, "headers": headers or {}})
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(pd, "provider_api_key", lambda prov: FAKE)
    r = client.post("/api/providers/google-gemini/test", json={})
    assert r.status_code == 200, r.data[:300]
    assert calls, "no request was made"
    for c in calls:
        _no_key_in_url(c)
    assert FAKE not in r.get_data(as_text=True)


def test_the_key_verdict_ping_sends_the_gemini_key_in_a_header(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None, **_):
        calls.append({"url": url, "params": None, "headers": headers or {}})
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    prov = {"name": "google-gemini", "type": "google",
            "base_url": "https://generativelanguage.googleapis.com",
            "auth": {"type": "env_var", "key": "GEMINI_API_KEY"}}
    verdict, detail = kv.ping_detail(prov, "gemini-3.8-flash", api_key=FAKE)
    assert calls, "no request was made"
    _no_key_in_url(calls[0])
    assert FAKE not in repr(detail)
