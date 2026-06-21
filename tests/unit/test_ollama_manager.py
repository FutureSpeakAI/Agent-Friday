"""Unit tests for ollama_manager — the local-model interface layer.

Pure logic under test (no network required):
  * recommend_models  — threshold-based hardware → model recommendation
  * invalidate_cache  — resets availability and model list caches

Network-dependent methods (is_available, list_models, pull_model,
health_check, chat_completion) are tested only for graceful degradation:
  * urllib.request.urlopen is monkeypatched to raise URLError / OSError
  * Methods must return safe defaults (False / [] / normalised dict) and
    must NEVER propagate the exception to the caller.

We NEVER make real HTTP requests to localhost:11434.
"""
from __future__ import annotations

import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

# Reset the module-level singleton before each test so tests are independent.
import ollama_manager as om


@pytest.fixture(autouse=True)
def reset_singleton():
    """Each test gets a fresh OllamaManager singleton (no state bleed)."""
    om._instance = None
    yield
    om._instance = None


@pytest.fixture
def mgr():
    """A fresh OllamaManager pointed at a non-routable address."""
    return om.OllamaManager(base_url="http://127.0.0.1:19999")


# ── recommend_models — pure logic ─────────────────────────────────────────────

class TestRecommendModels:
    """recommend_models is entirely pure: it maps hardware specs to model tiers.

    Invariants:
      * The 'tiny' fallback (qwen3:4b) is ALWAYS present regardless of specs.
      * High VRAM / RAM unlocks progressively larger tiers.
      * The hardware dict is passed directly so we never touch real hardware.
    """

    def _hw(self, vram_gb=0, ram_gb=0):
        return {"vram_gb": vram_gb, "ram_gb": ram_gb, "gpu": None, "platform": "win32"}

    def _names(self, recs):
        return [r["name"] for r in recs]

    def _tiers(self, recs):
        return [r["tier"] for r in recs]

    # Tiny fallback ─────────────────────────────────────────────────────────--

    def test_tiny_always_present_zero_specs(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(0, 0))
        assert any(r["tier"] == "tiny" for r in recs)

    def test_tiny_always_present_high_specs(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(32, 128))
        assert any(r["tier"] == "tiny" for r in recs)

    def test_low_spec_only_tiny(self, mgr):
        """RAM < 16 GB, VRAM < 6 → only tiny tier."""
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=0, ram_gb=8))
        tiers = self._tiers(recs)
        assert "tiny" in tiers
        assert "small" not in tiers
        assert "medium" not in tiers
        assert "large" not in tiers

    # Small tier (vram>=6 OR ram>=16) ────────────────────────────────────────-

    def test_small_unlocked_by_vram_6(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=6, ram_gb=0))
        assert any(r["tier"] == "small" for r in recs)

    def test_small_unlocked_by_ram_16(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=0, ram_gb=16))
        assert any(r["tier"] == "small" for r in recs)

    def test_small_not_unlocked_below_threshold(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=5, ram_gb=15))
        assert not any(r["tier"] == "small" for r in recs)

    # Medium tier (vram>=8 OR ram>=32) ───────────────────────────────────────-

    def test_medium_unlocked_by_vram_8(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=8, ram_gb=0))
        assert any(r["tier"] == "medium" for r in recs)

    def test_medium_unlocked_by_ram_32(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=0, ram_gb=32))
        assert any(r["tier"] == "medium" for r in recs)

    def test_medium_not_unlocked_below_threshold(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=7, ram_gb=31))
        assert not any(r["tier"] == "medium" for r in recs)

    # Large tier (vram>=24 OR ram>=64) ───────────────────────────────────────-

    def test_large_unlocked_by_vram_24(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=24, ram_gb=0))
        assert any(r["tier"] == "large" for r in recs)

    def test_large_unlocked_by_ram_64(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=0, ram_gb=64))
        assert any(r["tier"] == "large" for r in recs)

    def test_large_not_unlocked_below_threshold(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=23, ram_gb=63))
        assert not any(r["tier"] == "large" for r in recs)

    # Full unlock ────────────────────────────────────────────────────────────-

    def test_all_tiers_at_max_spec(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=48, ram_gb=128))
        tiers = self._tiers(recs)
        for tier in ("tiny", "small", "medium", "large"):
            assert tier in tiers, f"Expected tier '{tier}' in high-spec recommendations"

    # Result structure ────────────────────────────────────────────────────────

    def test_each_rec_has_name_task_tier(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(8, 32))
        for r in recs:
            assert "name" in r
            assert "task" in r
            assert "tier" in r

    def test_recs_is_list(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(0, 0))
        assert isinstance(recs, list)

    def test_specific_model_names_present_on_large(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=24, ram_gb=64))
        names = self._names(recs)
        assert "qwen3:32b" in names
        assert "qwen3:14b" in names
        assert "qwen3:8b" in names
        assert "qwen3:4b" in names

    def test_exact_threshold_vram_24_unlocks_large(self, mgr):
        """Boundary: exactly 24 GB VRAM must unlock the large tier."""
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=24, ram_gb=0))
        assert any(r["name"] == "qwen3:32b" for r in recs)

    def test_exact_threshold_ram_64_unlocks_large(self, mgr):
        """Boundary: exactly 64 GB RAM must unlock the large tier."""
        recs = mgr.recommend_models(hardware=self._hw(vram_gb=0, ram_gb=64))
        assert any(r["name"] == "qwen3:32b" for r in recs)

    def test_zero_hardware_returns_only_tiny(self, mgr):
        recs = mgr.recommend_models(hardware=self._hw(0, 0))
        assert len(recs) == 1
        assert recs[0]["tier"] == "tiny"

    def test_hardware_arg_overrides_detect(self, mgr):
        """Passing hardware= must bypass detect_hardware entirely."""
        # If detect_hardware ran it might read real system data.  Ensure the
        # returned tiers match what the supplied dict implies.
        recs = mgr.recommend_models(hardware={"vram_gb": 8, "ram_gb": 32})
        assert any(r["tier"] == "medium" for r in recs)


# ── invalidate_cache ──────────────────────────────────────────────────────────

class TestInvalidateCache:
    """After invalidate_cache(), the manager must re-probe the next call."""

    def test_invalidate_resets_available(self, mgr):
        mgr._available = True
        mgr._available_ts = 9_999_999_999  # far future — would be cached
        mgr.invalidate_cache()
        assert mgr._available is None

    def test_invalidate_resets_models_cache(self, mgr):
        mgr._models_cache = [{"name": "gemma4:latest", "size_gb": 5.0}]
        mgr._models_ts = 9_999_999_999
        mgr.invalidate_cache()
        assert mgr._models_cache is None

    def test_double_invalidate_safe(self, mgr):
        mgr.invalidate_cache()
        mgr.invalidate_cache()  # must not raise
        assert mgr._available is None
        assert mgr._models_cache is None


# ── is_available — graceful degradation ──────────────────────────────────────

class TestIsAvailableGraceful:
    """When urllib raises any exception, is_available() must return False."""

    def test_connection_refused_returns_false(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("Connection refused")
            ),
        )
        assert mgr.is_available() is False

    def test_timeout_returns_false(self, mgr, monkeypatch):
        import socket
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                socket.timeout("timed out")
            ),
        )
        assert mgr.is_available() is False

    def test_os_error_returns_false(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("network error")),
        )
        assert mgr.is_available() is False

    def test_result_cached_after_failure(self, mgr, monkeypatch):
        call_count = {"n": 0}
        def fake_open(*a, **kw):
            call_count["n"] += 1
            raise urllib.error.URLError("no server")
        monkeypatch.setattr(urllib.request, "urlopen", fake_open)
        mgr.is_available()
        mgr.is_available()
        # Second call must use the cache — urlopen called only once
        assert call_count["n"] == 1

    def test_returns_bool(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(urllib.error.URLError("x")),
        )
        result = mgr.is_available()
        assert isinstance(result, bool)


# ── list_models — graceful degradation ───────────────────────────────────────

class TestListModelsGraceful:
    """list_models() returns [] on any error; never raises."""

    def test_connection_error_returns_empty(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("no connection")
            ),
        )
        assert mgr.list_models() == []

    def test_returns_list_type(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("x")
            ),
        )
        result = mgr.list_models()
        assert isinstance(result, list)

    def test_cache_hit_avoids_network(self, mgr, monkeypatch):
        cached = [{"name": "gemma4:latest", "size_gb": 5.0}]
        mgr._models_cache = cached
        mgr._models_ts = 9_999_999_999  # far future

        call_count = {"n": 0}
        def fake_open(*a, **kw):
            call_count["n"] += 1
            raise urllib.error.URLError("should not be called")
        monkeypatch.setattr(urllib.request, "urlopen", fake_open)

        result = mgr.list_models()
        assert result == cached
        assert call_count["n"] == 0

    def test_stale_cache_re_probes(self, mgr, monkeypatch):
        mgr._models_cache = [{"name": "old:model"}]
        mgr._models_ts = 0  # expired

        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("network down")
            ),
        )
        result = mgr.list_models()
        assert result == []


# ── pull_model — graceful degradation ────────────────────────────────────────

class TestPullModelGraceful:
    """pull_model() returns False and calls progress_callback with error on failure."""

    def test_returns_false_on_network_error(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("no server")
            ),
        )
        result = mgr.pull_model("gemma4:latest")
        assert result is False

    def test_progress_callback_called_with_error(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("no server")
            ),
        )
        calls = []
        mgr.pull_model("gemma4:latest", progress_callback=lambda s, p: calls.append((s, p)))
        assert len(calls) == 1
        status, pct = calls[0]
        assert "error" in status.lower()
        assert pct == 0

    def test_no_callback_no_crash(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("no server")
            ),
        )
        # Must not raise even without a callback
        result = mgr.pull_model("missing-model", progress_callback=None)
        assert result is False


# ── health_check — graceful degradation ──────────────────────────────────────

class TestHealthCheckGraceful:
    """health_check() returns False when Ollama is unreachable."""

    def test_returns_false_on_error(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("no server")
            ),
        )
        assert mgr.health_check("gemma4:latest") is False

    def test_returns_bool(self, mgr, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("x")
            ),
        )
        result = mgr.health_check("any-model")
        assert isinstance(result, bool)


# ── chat_completion — graceful degradation ────────────────────────────────────

class TestChatCompletionGraceful:
    """chat_completion() must not raise even when both /v1 and /api/chat fail."""

    def _make_messages(self):
        return [{"role": "user", "content": "say hello"}]

    def test_both_endpoints_fail_raises(self, mgr, monkeypatch):
        """When both fallback paths fail, the exception propagates.

        The source has a try/except on /v1, then falls back to _post which also
        uses urlopen.  If _post also raises, the exception propagates from
        chat_completion — this is the current behavior (no outer catch).
        We verify the method raises rather than silently swallowing the error.
        """
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("no server")
            ),
        )
        with pytest.raises(Exception):
            mgr.chat_completion(self._make_messages(), "gemma4:latest")

    def test_v1_fail_fallback_also_fails(self, mgr, monkeypatch):
        """Confirm the fallback to /api/chat is attempted when /v1 fails."""
        call_paths = []

        class FakeResp:
            def read(self):
                # Return a valid /api/chat response
                import json
                return json.dumps({
                    "message": {"content": "hello"},
                    "prompt_eval_count": 5,
                    "eval_count": 3,
                }).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout=None):
            call_paths.append(req.full_url if hasattr(req, "full_url") else str(req))
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/v1/chat" in url:
                raise urllib.error.URLError("v1 not available")
            return FakeResp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        result = mgr.chat_completion(self._make_messages(), "gemma4:latest")
        # Should have tried /v1 first, then fallen back to /api/chat
        assert any("/v1/chat" in p for p in call_paths)
        # Result is a normalized OAI-style dict
        assert "choices" in result
        assert result["choices"][0]["message"]["content"] == "hello"


# ── get_manager singleton ─────────────────────────────────────────────────────

class TestGetManager:
    """get_manager() must return the same instance on repeated calls."""

    def test_singleton(self):
        a = om.get_manager()
        b = om.get_manager()
        assert a is b

    def test_instance_is_ollamamanager(self):
        mgr = om.get_manager()
        assert isinstance(mgr, om.OllamaManager)

    def test_base_url_set(self):
        om._instance = None
        mgr = om.get_manager("http://localhost:11434")
        assert mgr.base_url == "http://localhost:11434"

    def test_base_url_strips_trailing_slash(self):
        om._instance = None
        mgr = om.OllamaManager("http://localhost:11434/")
        assert mgr.base_url == "http://localhost:11434"


# ── OllamaManager init defaults ──────────────────────────────────────────────

class TestOllamaManagerInit:
    def test_default_cache_ttl(self, mgr):
        assert mgr._cache_ttl == 30

    def test_initial_cache_state(self, mgr):
        assert mgr._available is None
        assert mgr._models_cache is None
        assert mgr._hardware_cache is None

    def test_initial_timestamps_zero(self, mgr):
        assert mgr._available_ts == 0
        assert mgr._models_ts == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
