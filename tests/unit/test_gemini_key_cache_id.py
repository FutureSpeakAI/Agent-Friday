"""The Gemini key-validation cache is indexed by a keyed digest.

An entry id must not be a stable, unkeyed fingerprint of the API key: the
cache is indexed by an HMAC under a per-process random secret, and a repeat
check of the same key still hits the cache.
"""
from __future__ import annotations

import hashlib
import urllib.request

from agent_friday.services import voice_engine as ve


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_cache_id_is_keyed_and_the_cache_still_hits(monkeypatch):
    monkeypatch.delenv("FRIDAY_TESTING", raising=False)
    monkeypatch.setattr(ve, "_KEY_CHECK_CACHE", {})
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    key = "test-gemini-key-canary"  # pragma: allowlist secret

    assert ve.validate_gemini_key(key) == (True, "HTTP 200")
    assert ve.validate_gemini_key(key) == (True, "HTTP 200")
    assert len(calls) == 1, "a repeat check of the same key must hit the cache"

    unkeyed = hashlib.sha256(key.encode()).hexdigest()[:16]
    assert list(ve._KEY_CHECK_CACHE) and unkeyed not in ve._KEY_CHECK_CACHE
