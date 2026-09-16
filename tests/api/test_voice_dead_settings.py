"""Dead-settings rule for every Settings → Voice control (clean-sheet §8.1;
docs/decisions/2026-09-04-five-dead-settings.md): each control ships with the
code that reads it, and a test that fails if that code is removed.

`voice_ear_gpu` / `voice_mouth_gpu` / `local_voice_tts_engine` /
`local_voice_kokoro_*` are covered in test_local_voice_settings_surface.py.
"""
import inspect

import agent_friday.core as core


def test_dead_setting_voice_engine_is_read_by_the_resolver(monkeypatch):
    import agent_friday.routes.voice as rv

    class _E:
        def available(self):
            return True

        def models_ready(self):
            return True

        def resolve_tier(self, s=None):
            return "cpu"
    monkeypatch.setattr(rv, "get_local_voice_engine", lambda: _E())
    monkeypatch.setattr(rv, "_local_brain_ready", lambda: True)
    monkeypatch.setattr(rv, "_network_status", lambda: {"offline": False})
    monkeypatch.setattr(rv, "resolve_gemini_key", lambda: {"valid": True})
    assert rv._resolve_voice_engine({"voice_engine": "local"})["engine"] == "local"
    assert rv._resolve_voice_engine({"voice_engine": "gemini"})["engine"] == "gemini"
    # `auto` is a synonym for local (settled 2026-09-09; removed from the picker)
    assert rv._resolve_voice_engine({"voice_engine": "auto"})["engine"] == "local"


def test_dead_setting_voice_max_tokens_caps_the_spoken_reply():
    from agent_friday.routes.voice import _voice_reply_cap, _VOICE_REPLY_TOKENS_DEFAULT
    assert _voice_reply_cap({}) == _VOICE_REPLY_TOKENS_DEFAULT
    assert _voice_reply_cap({"voice_max_tokens": 120}) == 120
    assert _voice_reply_cap({"voice_max_tokens": 99999}) == 2048     # clamped
    assert _voice_reply_cap({"voice_max_tokens": 1}) == 64            # floored
    from agent_friday.services import voice_manifest as vm
    assert vm.read_selection({"voice_max_tokens": 150})["mind"]["reply_cap"] == 150


def test_dead_setting_voice_silence_ms_reaches_the_endpointer():
    """The local ws handler builds its VADEndpointer from voice_silence_ms."""
    import agent_friday.routes.voice as rv
    src = inspect.getsource(rv)
    assert 'VADEndpointer(silence_ms=int(settings.get("voice_silence_ms")' in src
    from agent_friday.services.local_voice import VADEndpointer
    assert VADEndpointer(silence_ms=1234, use_silero=False).silence_ms == 1234


def test_dead_setting_voice_idle_unload_s_sets_the_worker_lease(monkeypatch):
    from agent_friday.services import voice_workers as vw
    monkeypatch.setattr("agent_friday.core._load_settings",
                        lambda: {"voice_idle_unload_s": 77})
    assert vw._idle_s() == 77.0
    from agent_friday.services import voice_manifest as vm
    assert vm.read_selection({"voice_idle_unload_s": 77})["idle_unload_s"] == 77


def test_dead_setting_voice_interruption_mode_configures_the_live_input():
    from agent_friday.routes.voice import _build_realtime_input_config

    class _T:
        class RealtimeInputConfig:
            def __init__(self, **kw):
                self.kw = kw

        class AutomaticActivityDetection:
            def __init__(self, **kw):
                self.kw = kw

        class ActivityHandling:
            NO_INTERRUPTION = "NO_INTERRUPTION"
            START_OF_ACTIVITY_INTERRUPTS = "START_OF_ACTIVITY_INTERRUPTS"

        class StartSensitivity:
            START_SENSITIVITY_LOW = "LOW"
            START_SENSITIVITY_HIGH = "HIGH"

        class EndSensitivity:
            END_SENSITIVITY_LOW = "LOW"
            END_SENSITIVITY_HIGH = "HIGH"
    a = _build_realtime_input_config(_T, interruption_mode="auto")
    b = _build_realtime_input_config(_T, interruption_mode="no-barge")
    assert a is not None and b is not None
    assert repr(vars(a)) != repr(vars(b)), "the mode must change the config"


def test_all_voice_controls_are_declared_in_default_settings():
    for key in ("voice_engine", "voice_silence_ms", "voice_max_tokens",
                "voice_idle_unload_s", "voice_ear_gpu", "voice_mouth_gpu",
                "voice_interruption_mode", "local_voice_tts_engine"):
        assert key in core.DEFAULT_SETTINGS, key
