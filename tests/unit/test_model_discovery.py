"""Unit tests for services/model_discovery — discovery parsers, TTL cache,
and stale-while-revalidate. Fully offline: HTTP is monkeypatched.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.services.model_discovery as md


OPENROUTER_PAYLOAD = {
    "data": [
        {
            "id": "meta-llama/llama-4-maverick",
            "name": "Meta: Llama 4 Maverick",
            "context_length": 1048576,
            "pricing": {"prompt": "0.0000002", "completion": "0.0000006"},
            "architecture": {"input_modalities": ["text", "image"],
                             "output_modalities": ["text"]},
            "supported_parameters": ["tools", "temperature"],
            "top_provider": {"max_completion_tokens": 16384},
        },
        {
            "id": "meta-llama/llama-4-maverick:free",
            "name": "Meta: Llama 4 Maverick (free)",
            "context_length": 256000,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["temperature"],
        },
    ]
}


class TestOpenRouterParser:
    def test_parses_ids_and_labels(self):
        models = md.parse_openrouter(OPENROUTER_PAYLOAD)
        assert [m["id"] for m in models] == [
            "meta-llama/llama-4-maverick", "meta-llama/llama-4-maverick:free"]
        assert models[0]["label"] == "Meta: Llama 4 Maverick"

    def test_price_per_token_converted_to_per_1m(self):
        m = md.parse_openrouter(OPENROUTER_PAYLOAD)[0]
        assert m["price_in"] == pytest.approx(0.20)
        assert m["price_out"] == pytest.approx(0.60)

    def test_tools_signal_from_supported_parameters(self):
        models = md.parse_openrouter(OPENROUTER_PAYLOAD)
        assert models[0]["supports_tools"] is True
        assert models[1]["supports_tools"] is False

    def test_free_suffix_detected(self):
        models = md.parse_openrouter(OPENROUTER_PAYLOAD)
        assert models[0]["free"] is False
        assert models[1]["free"] is True

    def test_vision_modality(self):
        m = md.parse_openrouter(OPENROUTER_PAYLOAD)[0]
        assert "vision" in m["modalities"]

    def test_context_window(self):
        m = md.parse_openrouter(OPENROUTER_PAYLOAD)[0]
        assert m["context_window"] == 1048576
        assert m["max_output"] == 16384

    def test_malformed_entries_skipped(self):
        assert md.parse_openrouter({"data": [{}, "junk", None]}) == []
        assert md.parse_openrouter({}) == []


class TestOpenAIParser:
    def test_ids_only(self):
        models = md.parse_openai({"data": [{"id": "llama-3.3-70b-versatile"},
                                           {"id": "gemma2-9b-it"}]})
        assert [m["id"] for m in models] == ["llama-3.3-70b-versatile",
                                             "gemma2-9b-it"]
        assert models[0]["supports_tools"] is None  # unknown, not False

    def test_bare_list_accepted(self):
        models = md.parse_openai([{"id": "m1"}, "m2"])
        assert [m["id"] for m in models] == ["m1", "m2"]


class TestHFRouterParser:
    def test_hub_ids(self):
        models = md.parse_hf_router({"data": [
            {"id": "meta-llama/Llama-3.3-70B-Instruct",
             "providers": [{"provider": "groq", "supports_tools": True}]}]})
        assert models[0]["id"] == "meta-llama/Llama-3.3-70B-Instruct"
        assert models[0]["label"] == "Llama-3.3-70B-Instruct"
        assert models[0]["supports_tools"] is True


class TestCache:
    def test_write_read_roundtrip(self):
        models = md.parse_openrouter(OPENROUTER_PAYLOAD)
        md.write_cache("cache-test-prov", models, ttl_s=3600)
        got, stale = md.cached_models("cache-test-prov")
        assert [m["id"] for m in got] == [m["id"] for m in models]
        assert stale is False
        assert "meta-llama/llama-4-maverick" in md.cached_model_ids("cache-test-prov")
        md.invalidate_cache("cache-test-prov")
        assert md.cached_models("cache-test-prov") == ([], True)

    def test_ttl_expiry_marks_stale(self):
        md.write_cache("cache-ttl-prov", [{"id": "m"}], ttl_s=1)
        blob = md.read_cache("cache-ttl-prov")
        blob["fetched_at"] = time.time() - 10
        assert md.cache_is_stale(blob) is True
        md.invalidate_cache("cache-ttl-prov")

    def test_missing_cache_is_stale_empty(self):
        assert md.cached_models("never-cached-prov") == ([], True)


class TestRefresh:
    def _prov(self, **over):
        base = {
            "name": "refresh-test-prov",
            "type": "openai-compatible",
            "base_url": "https://api.test.example/v1",
            "auth": {"type": "env_var", "key": "REFRESH_TEST_KEY"},
            "discovery": {"mode": "api", "endpoint": "/models",
                          "parser": "openrouter", "ttl_s": 3600,
                          "max_models": 0},
            "features": {},
        }
        base.update(over)
        return base

    def test_refresh_writes_cache(self, monkeypatch):
        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return OPENROUTER_PAYLOAD
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
        monkeypatch.setenv("REFRESH_TEST_KEY", "k")
        res = md.refresh_models(self._prov())
        assert res["ok"] is True and res["count"] == 2
        ids = md.cached_model_ids("refresh-test-prov")
        assert "meta-llama/llama-4-maverick:free" in ids
        md.invalidate_cache("refresh-test-prov")

    def test_refresh_needs_key_unless_keyless(self, monkeypatch):
        monkeypatch.delenv("REFRESH_TEST_KEY", raising=False)
        res = md.refresh_models(self._prov())
        assert res["ok"] is False and "key" in res["error"].lower()

        # keyless_discovery (OpenRouter's /models needs no auth) skips the gate.
        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return OPENROUTER_PAYLOAD
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
        res = md.refresh_models(self._prov(features={"keyless_discovery": True}))
        assert res["ok"] is True
        md.invalidate_cache("refresh-test-prov")

    def test_stale_while_revalidate_on_failure(self, monkeypatch):
        """A failing refresh must NOT clobber the previous good cache."""
        md.write_cache("refresh-test-prov", [{"id": "old-model"}], ttl_s=1)
        import requests
        def _boom(*a, **k):
            raise requests.exceptions.ConnectionError("network down")
        monkeypatch.setattr(requests, "get", _boom)
        monkeypatch.setenv("REFRESH_TEST_KEY", "k")
        res = md.refresh_models(self._prov())
        assert res["ok"] is False
        got, stale = md.cached_models("refresh-test-prov")
        assert [m["id"] for m in got] == ["old-model"]  # stale data survives
        md.invalidate_cache("refresh-test-prov")

    def test_max_models_cap(self, monkeypatch):
        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return OPENROUTER_PAYLOAD
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
        monkeypatch.setenv("REFRESH_TEST_KEY", "k")
        prov = self._prov()
        prov["discovery"]["max_models"] = 1
        res = md.refresh_models(prov)
        assert res["count"] == 1
        md.invalidate_cache("refresh-test-prov")

    def test_static_provider_refresh_rejected(self):
        res = md.refresh_models(self._prov(discovery={"mode": "static"}))
        assert res["ok"] is False

    def test_background_refresh_disabled_under_tests(self):
        assert md.ensure_background_refresh() is False  # FRIDAY_TESTING=1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
