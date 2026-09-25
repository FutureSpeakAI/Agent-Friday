"""The local-voice settings surface: every control, its enforcement, and a test
that fails if the enforcement is removed.

The failure this guards against is a dead setting: a control that reports
success and changes nothing.
"""
import pytest

import agent_friday.core as core
from agent_friday.routes.core_routes import _check_voice_enums
from agent_friday.services.local_voice import LocalVoiceEngine
from agent_friday.services.model_catalog import _tts_engines


# ------------------------------------------------------- the setting persists

@pytest.mark.parametrize("key, default", [
    ("local_voice_tts_engine", "piper"),
    ("local_voice_kokoro_voice", "af_heart"),
    ("local_voice_kokoro_allow_cpu", False),
    # clean-sheet §8.1 B: per-stage GPU policy + idle unload
    ("voice_ear_gpu", "if_free"),
    ("voice_mouth_gpu", "if_free"),
    ("voice_idle_unload_s", 600),
])
def test_tts_keys_are_declared_in_default_settings(key, default):
    """`_load_settings_raw()` drops any persisted key absent from
    DEFAULT_SETTINGS. A key the service layer reads but this dict does not
    declare is a control that saves, says "Saved", and reverts on next read.
    Deleting the declaration is exactly how that regresses."""
    assert key in core.DEFAULT_SETTINGS, (
        "%s is read by services/local_voice.py but not declared here, so it "
        "will not survive a reload" % key)
    assert core.DEFAULT_SETTINGS[key] == default


# --------------------------------------------------- the setting is validated

@pytest.mark.parametrize("payload", [
    {"voice_engine": "local"},
    {"voice_engine": "local-gpu"},
    {"voice_engine": "gemini"},
    {"voice_engine": "auto"},
    {"local_voice_tts_engine": "piper"},
    {"local_voice_tts_engine": "kokoro"},
    {"voice_silence_ms": 900},          # unrelated keys pass through untouched
])
def test_voice_enums_accept_valid_values(payload):
    assert _check_voice_enums(payload) is None


@pytest.mark.parametrize("payload", [
    {"voice_engine": "banana"},
    {"voice_engine": ""},
    {"voice_engine": None},
    {"local_voice_tts_engine": "espeak"},
    {"local_voice_tts_engine": 123},
])
def test_voice_enums_reject_out_of_range_values(payload):
    """Both keys are consumed with `or <default>` fallbacks, so an unrecognised
    value silently resolves to something other than what was written. Rejecting
    the write is what keeps the setting's meaning."""
    err = _check_voice_enums(payload)
    assert err is not None, "%r should be refused" % payload
    assert err["status"] == "error"
    key = list(payload)[0]
    assert key in err["message"], "the error must name the offending key"


def test_settings_post_refuses_an_unknown_voice_engine(client):
    """End to end through the route the UI actually calls."""
    r = client.post("/api/settings",
                    json={"settings": {"voice_engine": "banana"}})
    assert r.status_code == 400
    assert "voice_engine" in (r.get_json() or {}).get("message", "")


def test_settings_post_accepts_a_known_tts_engine(client):
    r = client.post("/api/settings",
                    json={"settings": {"local_voice_tts_engine": "piper"}})
    assert r.status_code == 200


# ------------------------------------- unavailable options explain themselves

def test_catalog_lists_both_engines_rather_than_hiding_one():
    """A missing option is indistinguishable from a missing feature, so an
    engine that cannot run is listed and greyed, never dropped."""
    ids = [e["id"] for e in _tts_engines()]
    assert "piper" in ids and "kokoro" in ids


def test_unavailable_engine_carries_a_reason(monkeypatch):
    import agent_friday.services.model_catalog as mc
    monkeypatch.setattr(
        "agent_friday.services.kokoro_voice.kokoro_health",
        lambda: {"engine": "local-kokoro", "status": "broken",
                 "detail": "Kokoro is installed but fails to import -- no "
                           "module named 'spacy'. Reinstall it with "
                           "`pip install kokoro misaki espeakng-loader`.",
                 "available": False, "gpu_ready": False})
    kok = [e for e in mc._tts_engines() if e["id"] == "kokoro"][0]
    assert kok["available"] is False
    assert kok["hint"], "a disabled control with no reason is a dead end"
    assert "pip install" in kok["hint"]


def test_available_engine_is_offered(monkeypatch):
    import agent_friday.services.model_catalog as mc
    monkeypatch.setattr(
        "agent_friday.services.kokoro_voice.kokoro_health",
        lambda: {"engine": "local-kokoro", "status": "ok",
                 "detail": "Kokoro GPU voice ready",
                 "available": True, "gpu_ready": True})
    kok = [e for e in mc._tts_engines() if e["id"] == "kokoro"][0]
    assert kok["available"] is True


# ------------------------------- "serving now" reports reality, not intention

def test_running_status_is_none_before_anything_loads():
    """The 'Serving now' row must be able to say nothing is loaded. If this
    ever falls back to settings, the UI starts reporting the request as though
    it were an observation."""
    assert LocalVoiceEngine().running_status() is None


def test_running_status_reports_the_loaded_object_not_the_setting(monkeypatch):
    class _FakeKokoro:
        voice = "af_bella"
        device = "cuda"

    eng = LocalVoiceEngine()
    # Settings ask for Piper; a Kokoro object is what is actually loaded.
    monkeypatch.setattr(eng, "_settings",
                        lambda: {"local_voice_tts_engine": "piper",
                                 "local_voice_tts_voice": "en_US-amy-medium"})
    fake = _FakeKokoro()
    fake.__class__.__name__ = "KokoroTTS"
    eng._tts = fake
    eng._tier = "cpu"

    st = eng.running_status()
    assert st is not None
    assert st["tts_engine"] == "kokoro", "must report what is loaded"
    assert st["tts_voice"] == "af_bella"
    assert st["tts_device"] == "cuda"
    # And health() must carry it alongside, not instead of, the settings view.
    h = eng.health()
    assert h["running"]["tts_engine"] == "kokoro"
    assert h["tts_engine"] == "piper", "the settings view stays the settings view"


# ------------------------------------------ clean-sheet §8.1 B: GPU policy

@pytest.mark.parametrize("payload", [
    {"voice_ear_gpu": "never"},
    {"voice_mouth_gpu": "if_free"},
    {"voice_mouth_gpu": "required"},
])
def test_gpu_policy_enums_accept_valid_values(payload):
    assert _check_voice_enums(payload) is None


@pytest.mark.parametrize("payload", [
    {"voice_ear_gpu": "always"},
    {"voice_mouth_gpu": ""},
])
def test_gpu_policy_enums_reject_out_of_range_values(payload):
    err = _check_voice_enums(payload)
    assert err is not None and err["status"] == "error"


def test_gpu_policy_is_read_and_enforced_by_the_manifest(monkeypatch):
    """The dead-settings rule: the control ships with the code that reads it.
    `voice_mouth_gpu: required` must REFUSE a CPU mouth, not merely persist."""
    from agent_friday.services import voice_manifest as vm
    monkeypatch.setattr(vm, "_settings", lambda: {"local_voice_tts_engine": "piper",
                                                   "voice_mouth_gpu": "required",
                                                   "voice_ear_gpu": "never",
                                                   "voice_idle_unload_s": 42})
    m = vm.VoiceManifest()
    assert m.stages["mouth"]["selected"]["device_policy"] == "required"
    assert m.stages["ear"]["selected"]["device_policy"] == "never"
    assert m.idle_unload_s == 42
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mouth",
                        lambda sel, text, prog: (b"\x00\x01" * 24000,
                                                 {"engine": "piper", "device": "cpu"}))
    m.prove("mouth")
    assert m.snapshot_stage("mouth")["proof"]["code"] == "local_voice_gpu_refused"
