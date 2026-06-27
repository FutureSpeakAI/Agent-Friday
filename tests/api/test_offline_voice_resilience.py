"""Tests for offline-first resilience + voice-everywhere features.

Covers the network monitor state machine, the offline routing overlay, the
offline task queue, cached-RSS fallback, the local TTS fallback status, and the
per-workspace voice-context + start-my-day endpoints. All offline behavior is
driven by core.NETWORK_STATE, which we set directly (the background monitor
thread never runs under FRIDAY_TESTING=1), then always reset so tests don't leak
an offline state into each other.
"""
import json

import pytest

import agent_friday.core as core


@pytest.fixture(autouse=True)
def _reset_network():
    """Every test starts and ends with a clean 'unknown' network state."""
    core._set_network_state("unknown")
    yield
    core._set_network_state("unknown")


# ── Network status route ──────────────────────────────────────────────────────
class TestNetworkStatus:
    def test_shape(self, client):
        r = client.get("/api/system/network-status")
        assert r.status_code == 200
        d = r.get_json()
        assert d["status"] == "ok"
        assert "network" in d and "status" in d["network"]
        assert "ollama_available" in d
        assert "active_routing_mode" in d

    def test_offline_flag_propagates(self, client):
        core._set_network_state("offline")
        d = client.get("/api/system/network-status").get_json()
        assert d["network"]["status"] == "offline"
        assert d["network"]["offline"] is True


# ── Offline routing overlay ───────────────────────────────────────────────────
class TestOfflineRoutingOverlay:
    def test_overlay_forces_local_only_when_offline(self):
        core._set_network_state("offline")
        s = core._load_settings()
        mr = s.get("model_routing") or {}
        assert mr.get("mode") == "local_only"
        assert mr.get("fallback_to_cloud") is False

    def test_overlay_absent_when_online(self):
        core._set_network_state("online")
        raw_mode = (core._load_settings_raw().get("model_routing") or {}).get("mode")
        live_mode = (core._load_settings().get("model_routing") or {}).get("mode")
        assert live_mode == raw_mode  # no overlay applied

    def test_overlay_does_not_persist(self):
        core._set_network_state("offline")
        core._load_settings()  # would persist the overlay if it leaked
        core._set_network_state("online")
        raw = core._load_settings_raw().get("model_routing") or {}
        assert raw.get("mode") != "local_only" or raw.get("fallback_to_cloud") is not False


# ── Offline task queue ────────────────────────────────────────────────────────
class TestOfflineQueue:
    def test_add_list_remove(self, client):
        r = client.post("/api/system/offline-queue",
                        json={"kind": "notify", "payload": {"title": "x"}})
        assert r.status_code == 200
        qid = r.get_json()["entry"]["id"]
        items = client.get("/api/system/offline-queue").get_json()["items"]
        assert any(e["id"] == qid for e in items)
        r = client.delete(f"/api/system/offline-queue?id={qid}")
        assert r.get_json()["status"] == "ok"
        items = client.get("/api/system/offline-queue").get_json()["items"]
        assert not any(e["id"] == qid for e in items)

    def test_clear(self, client):
        client.post("/api/system/offline-queue", json={"kind": "notify", "payload": {}})
        client.delete("/api/system/offline-queue?clear=1")
        assert client.get("/api/system/offline-queue").get_json()["count"] == 0

    def test_post_requires_kind(self, client):
        assert client.post("/api/system/offline-queue", json={}).status_code == 400

    def test_flush_unknown_kind_is_dropped(self):
        core._offline_queue_add("totally-unknown-kind", {})
        import agent_friday.services.notifications as n
        result = n._flush_offline_queue(reason="test")
        assert result["kept"] == 0  # unknown kinds are dropped, not retried forever


# ── Cached-RSS fallback ───────────────────────────────────────────────────────
class TestCachedNews:
    def test_offline_uses_archive_not_network(self, monkeypatch):
        import agent_friday.services.news_engine as ne

        def _boom(*a, **k):
            raise AssertionError("live RSS must not be hit while offline")

        monkeypatch.setattr(ne, "_rss_results", _boom, raising=False)
        monkeypatch.setattr(ne, "_brave_results", _boom, raising=False)
        core._set_network_state("offline")
        items = ne._fetch_news_items(limit_per=2)
        # Archive is empty in the temp home, so this is [] — but crucially the
        # live fetch was never called (no AssertionError raised).
        assert isinstance(items, list)


# ── Voice fallback status ─────────────────────────────────────────────────────
class TestVoiceFallbackStatus:
    def test_shape(self, client):
        d = client.get("/api/voice/fallback-status").get_json()
        assert d["status"] == "ok"
        assert d["recommended_mode"] in ("cloud", "local", "tts_only", "unavailable")
        assert "local_tts" in d and "local_llm" in d

    def test_offline_disables_cloud_voice(self, client):
        core._set_network_state("offline")
        d = client.get("/api/voice/fallback-status").get_json()
        assert d["cloud_voice"] is False


# ── Voice-context endpoints ───────────────────────────────────────────────────
class TestVoiceContext:
    @pytest.mark.parametrize("ws", ["calendar", "finance", "news", "home", "messages"])
    def test_workspace_context_returns_prompt(self, client, ws):
        # Force offline so any live fetch inside a builder short-circuits to cache.
        core._set_network_state("offline")
        d = client.get(f"/api/voice-context/{ws}").get_json()
        assert d["status"] == "ok"
        assert d["workspace"]
        assert isinstance(d["prompt"], str) and len(d["prompt"]) > 20
        assert d["label"]

    def test_spec_alias_path_works_for_uncollided_ws(self, client):
        # The /api/<workspace>/voice-context spec alias works for a workspace
        # that doesn't own a competing /api/<ws>/<id> route (e.g. finance).
        core._set_network_state("offline")
        d = client.get("/api/finance/voice-context").get_json()
        assert d["status"] == "ok"
        assert d["workspace"] == "finance"

    def test_start_my_day_returns_prompt(self, client):
        core._set_network_state("offline")
        d = client.get("/api/voice/start-my-day").get_json()
        assert d["status"] == "ok"
        assert "calendar" in d["prompt"].lower()
        assert "news" in d["prompt"].lower()


# ── Voice live-tool registry ──────────────────────────────────────────────────
class TestVoiceTools:
    def test_calendar_and_email_tools_registered(self):
        import agent_friday.services.voice_engine as v
        names = {t[0] for t in v._VOICE_LIVE_TOOLS}
        assert "query_calendar" in names
        assert "check_email" in names

    def test_local_tts_helper_never_raises(self):
        import agent_friday.services.voice_engine as v
        # Returns a BytesIO or None depending on whether pyttsx3 is installed,
        # but must never raise.
        out = v._synthesize_tts_wav_local("hello")
        assert out is None or hasattr(out, "read")
