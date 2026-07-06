"""API regression tests for the first-run voice setup surface.

Pins the 2026-07-06 fixes: /friday-live 404s after the src/ restructure, the
setup TTS test 500ing on every call (b64encode on BytesIO), wizard step
statuses derived from strings health() never emits, and the new in-UI
installer endpoints.
"""
import base64
import io


# ── Friday Live PWA routes (Tier-3 client) ────────────────────────────────────

def test_friday_live_page_served(client):
    r = client.get("/friday-live")
    assert r.status_code == 200, "/friday-live must serve the Tier-3 voice client"
    assert b"FRIDAY" in r.data or b"friday" in r.data


def test_friday_live_manifest_and_sw_served(client):
    assert client.get("/friday-live/manifest.json").status_code == 200
    sw = client.get("/friday-live/sw.js")
    assert sw.status_code == 200
    assert sw.headers.get("Service-Worker-Allowed") == "/friday-live/"


# ── /api/voice/setup/test — must return playable base64 WAV ──────────────────

def test_setup_test_returns_decodable_audio(client, monkeypatch):
    import agent_friday.services.voice_engine as ve

    wav = b"RIFF" + b"\x00" * 40  # minimal stand-in; contract is BytesIO in
    monkeypatch.setattr(ve, "_synthesize_tts_wav",
                        lambda text, **kw: io.BytesIO(wav))
    r = client.post("/api/voice/setup/test", json={"text": "hello"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert base64.b64decode(body["audio_b64"]) == wav


def test_setup_test_no_engine_is_actionable_not_500(client, monkeypatch):
    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(ve, "_synthesize_tts_wav", lambda text, **kw: None)
    r = client.post("/api/voice/setup/test", json={"text": "hello"})
    assert r.status_code == 503
    assert "install" in r.get_json()["message"].lower()


# ── /api/voice/setup/status — step statuses must match health() vocabulary ───

def test_setup_status_steps_use_real_health_fields(client, monkeypatch):
    import agent_friday.routes.voice as vr

    def fake_health():
        return {"engine": "local-voice-lite", "status": "missing",
                "detail": "Tier-1 voice deps not installed (.[voice-local-lite])",
                "available": False, "models_ready": False,
                "gpu": {"engine": "nvidia-nemo", "status": "missing",
                        "detail": "NeMo not installed", "available": False}}

    import agent_friday.services.local_voice as lv
    monkeypatch.setattr(lv, "local_voice_health", fake_health)
    monkeypatch.setattr(vr, "_resolve_voice_engine",
                        lambda *a, **k: {"engine": "local"})
    r = client.get("/api/voice/setup/status")
    assert r.status_code == 200
    body = r.get_json()
    steps = {s["id"]: s for s in body["steps"]}
    # Deps missing must NOT render as ok, and must gate readiness.
    assert steps["deps"]["status"] == "missing"
    assert steps["models"]["status"] == "unknown"  # can't assess without deps
    assert body["ready"] is False
    # The GPU tier appears as an informational step with the actionable detail.
    assert "gpu" in steps
    assert steps["gpu"]["status"] != "ok"


# ── In-UI installer endpoints ─────────────────────────────────────────────────

def test_install_rejects_unknown_target(client):
    r = client.post("/api/voice/setup/install", json={"target": "evil-package"})
    assert r.status_code == 400
    assert "unknown target" in r.get_json()["error"]


def test_install_status_endpoint(client):
    r = client.get("/api/voice/setup/install/status")
    assert r.status_code == 200
    assert "state" in r.get_json()
