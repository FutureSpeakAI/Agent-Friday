"""API + wiring tests for Tier-1 local voice.

Covers the provider-agnostic integration surface that runs in CI with no model:
the provider registers, asr/tts capabilities resolve, the /ws/voice-local route
is registered, /api/voice/session-info honors the local-default ethos and the
voice_engine switch, /api/health/full reports local voice, and the model catalog
surfaces the local voice models in the voice role.
"""
import core


# ── provider registry + capability router ─────────────────────────────────────

def test_local_voice_provider_registered():
    from services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    p = reg.get_provider("local-voice-lite")
    assert p is not None
    assert p["type"] == "local-voice"
    assert "voice" in p["roles"]
    assert set(p["capabilities"]) == {"asr", "tts"}


def test_nemo_provider_registered_and_gpu_gated():
    from services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    p = reg.get_provider("nvidia-nemo")
    assert p is not None and p["enabled"] is True
    assert p["type"] == "nemo-local"
    assert set(p["capabilities"]) == {"asr", "tts"}
    # Enabled but availability is gated on the GPU stack (torch+NeMo+CUDA+VRAM);
    # absent in CI → reported unavailable, so it never blocks Tier-1.
    assert reg.is_provider_available("nvidia-nemo") is False


def test_asr_tts_capabilities_exist():
    from services import capability_router
    assert "asr" in capability_router.CAPABILITIES
    assert "tts" in capability_router.CAPABILITIES
    for cap in ("asr", "tts"):
        r = capability_router.resolve(cap)
        assert r["provider"] == "local-voice-lite"
        # Either ready (deps present) or a helpful install hint — never a
        # bare "connect a key" message for an on-device engine.
        if not r["available"]:
            assert "voice-local-lite" in (r["unlock_hint"] or "")


def test_capabilities_route_includes_asr_tts(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    caps = {c["capability"] for c in r.get_json()["capabilities"]}
    assert {"asr", "tts"} <= caps


# ── /ws/voice-local route registration ────────────────────────────────────────

def test_ws_voice_local_route_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/ws/voice-local" in rules
    assert "/ws/live" in rules  # cloud path still present (opt-in)


# ── session-info: local is default, cloud is opt-in ───────────────────────────

class _FakeEngine:
    def __init__(self, available=True, ready=True, tier="cpu"):
        self._a, self._r, self._tier = available, ready, tier

    def available(self):
        return self._a

    def models_ready(self):
        return self._r

    def resolve_tier(self, settings=None):
        return self._tier


def test_session_info_defaults_to_local(client, monkeypatch):
    import routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _FakeEngine(True, True))
    # No voice_engine set → defaults to local (the ethos).
    monkeypatch.setattr(rv, "_load_settings", lambda: {})
    r = client.get("/api/voice/session-info")
    assert r.status_code == 200
    body = r.get_json()
    assert body["engine"] == "local"
    assert body["ws_url"] == "/ws/voice-local"


def test_session_info_cloud_opt_in(client, monkeypatch):
    import routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _FakeEngine(True, True))
    monkeypatch.setattr(rv, "_load_settings", lambda: {"voice_engine": "gemini"})
    monkeypatch.setattr(core, "GEMINI_API_KEY", "AQ.fake-key-for-test")  # pragma: allowlist secret
    monkeypatch.setattr(rv, "_network_status", lambda: {"offline": False})
    r = client.get("/api/voice/session-info")
    body = r.get_json()
    assert body["engine"] == "gemini"
    assert body["ws_url"] == "/ws/live"


def test_session_info_falls_back_to_cloud_when_local_missing(client, monkeypatch):
    import routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _FakeEngine(False, False))
    monkeypatch.setattr(rv, "_load_settings", lambda: {"voice_engine": "local"})
    monkeypatch.setattr(core, "GEMINI_API_KEY", "AQ.fake-key-for-test")  # pragma: allowlist secret
    monkeypatch.setattr(rv, "_network_status", lambda: {"offline": False})
    r = client.get("/api/voice/session-info")
    body = r.get_json()
    # Local deps absent but a cloud key is present → graceful fall-through.
    assert body["engine"] == "gemini"


def test_session_info_demo_when_nothing_available(client, monkeypatch):
    import routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _FakeEngine(False, False))
    monkeypatch.setattr(rv, "_load_settings", lambda: {"voice_engine": "local"})
    monkeypatch.setattr(core, "GEMINI_API_KEY", "")
    monkeypatch.setattr(rv, "_network_status", lambda: {"offline": False})
    r = client.get("/api/voice/session-info")
    body = r.get_json()
    assert body["engine"] == "demo"
    assert body["ws_url"] is None


# ── /api/health/full reports local voice ──────────────────────────────────────

def test_health_full_has_local_voice_block(client):
    r = client.get("/api/health/full")
    assert r.status_code == 200
    lv = r.get_json().get("local_voice")
    assert lv is not None
    assert lv["engine"] == "local-voice-lite"
    assert lv["status"] in ("ok", "needs_download", "missing", "error")
    # Tier-2 (NeMo GPU) sub-block + active tier + perf are surfaced for the UI.
    assert lv["active_tier"] in ("cpu", "gpu")
    assert "perf" in lv
    assert lv.get("gpu", {}).get("engine") == "nvidia-nemo"


def test_health_full_lists_nemo_provider(client):
    r = client.get("/api/health/full")
    provs = {p.get("provider") for p in r.get_json().get("providers", [])}
    assert "nvidia-nemo" in provs


def test_session_info_reports_tier(client, monkeypatch):
    import routes.voice as rv
    monkeypatch.setattr(rv, "get_local_voice_engine",
                        lambda: _FakeEngine(True, True, tier="gpu"))
    monkeypatch.setattr(rv, "_load_settings", lambda: {"voice_engine": "local-gpu"})
    body = client.get("/api/voice/session-info").get_json()
    assert body["engine"] == "local"
    assert body["tier"] == "gpu"
    assert "GPU" in body["label"]


# ── model catalog surfaces local voice in the voice role ──────────────────────

def test_models_route_lists_local_voice(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    body = r.get_json()
    voice_ids = {m["id"] for m in body["roles"]["voice"]}
    assert "piper-en_US-amy-medium" in voice_ids
    assert "whisper-small" in voice_ids


# ── default settings: voice_engine is local; asr/tts route on-device ──────────

def test_default_settings_local_voice_defaults():
    cr = core.DEFAULT_SETTINGS["capability_routing"]
    assert cr["asr"]["provider"] == "local-voice-lite"
    assert cr["tts"]["provider"] == "local-voice-lite"
    assert core.DEFAULT_SETTINGS["voice_engine"] == "local"


def test_capability_routing_sync_preserves_asr_tts():
    # asr/tts have no legacy flat key — they must pass straight through the sync
    # (like embedding/local) and never be dropped.
    s = {"capability_routing": {}}
    core._sync_capability_routing(s)
    assert s["capability_routing"]["asr"]["provider"] == "local-voice-lite"
    assert s["capability_routing"]["tts"]["provider"] == "local-voice-lite"


# ── demo mode: voice-only providers don't lift demo on their own ──────────────

def test_local_voice_alone_does_not_lift_demo(monkeypatch):
    """A machine with local voice deps but NO reasoning provider is still demo —
    voice needs a brain. (Guards against local-voice auth:none counting as a
    live provider.)"""
    from services import demo_mode
    from services.provider_registry import get_provider_registry

    reg = get_provider_registry()

    def _only_voice_available(name):
        return name == "local-voice-lite"

    monkeypatch.setattr(reg, "is_provider_available", _only_voice_available)
    assert demo_mode._any_provider_available() is False
