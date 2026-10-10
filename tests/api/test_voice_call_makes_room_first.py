"""A local call makes room on the card BEFORE it admits its ear and mouth.

Admission measures the live card. When the call parks the brain (mode V-B),
an ear or mouth admitted while the brain still holds the card is refused
against the display reserve and lands on the CPU, although the park a moment
later frees gigabytes. The session therefore decides and carries out the park
first, then admits the ear and the mouth to the GPU by measured fit, then arms
the front. A CPU Kokoro fallback runs on the CPU, never in-process on CUDA.

No server, no model, no GPU: a fake card whose free memory is what the brain
leaves until the arbiter parks it, fake GPU workers that ask the real
``admit_gpu`` for room, a fake arbiter and a fake front seat.
"""
import inspect
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

BONSAI = "ternary-bonsai:1.7b"
#: The spoken end-to-end run's numbers: 1,843 MiB free beside the resident
#: brain against a 1,884 MiB display reserve; the brain's park frees ~6 GB.
RESERVE_MIB, FREE_WITH_BRAIN_MIB, BRAIN_MIB = 1884, 1843, 6200
WORKING_SETS = {"whisper-cuda": 320, "kokoro-cuda": 384}


class _Card:
    def __init__(self):
        self.parked = False
        self.taken = 0
        self.log = []

    def free_mib(self):
        return FREE_WITH_BRAIN_MIB + (BRAIN_MIB if self.parked else 0) - self.taken


class _Arbiter:
    def __init__(self, card):
        self.card = card

    def grant(self, kind, ttl_s=300):
        self.card.parked = True
        self.card.log.append(("park", kind))
        return {"ok": True}

    def release(self, kind=None):
        self.card.parked = False
        self.card.log.append(("unpark", kind))
        return {"ok": True}

    def renew(self, kind, ttl_s):
        return {"ok": True}


class _FrontSeat:
    def __init__(self, card):
        self.card = card

    def arm(self, model, holder="session"):
        self.card.log.append(("front", model))

    def prefill(self, prompt, contract):
        return {"ms": 1}

    def disarm(self, holder="session"):
        pass


@pytest.fixture
def call(app, monkeypatch, tmp_path):
    from agent_friday.routes import voice
    from agent_friday.services import (conversations, crew_runtime, hardware_profile,
                                      laya_router, local_seats, nemo_voice, notification_policy,
                                      presence, reflex_turn, residency_arbiter, voice_ear_stream,
                                      voice_engine, voice_front, voice_receipt, voice_session,
                                      voice_workers as vw)

    card = _Card()
    monkeypatch.setattr(conversations, "FRIDAY_DIR", tmp_path)
    conversations.create(title="Synthetic voice owner", cid="fixture-chat")

    # The card: the REAL admit_gpu reads these.
    monkeypatch.setattr(vw, "_display_reserve_mib", lambda: RESERVE_MIB)
    monkeypatch.setattr(nemo_voice, "gpu_status", lambda fresh=False: {
        "cuda": True, "vram_free_real_gb": card.free_mib() / 1024.0})
    monkeypatch.setattr(hardware_profile, "vram_headroom", lambda reserve_mib=None: {
        "ok": True, "free_mib": card.free_mib()})
    monkeypatch.setattr(vw, "declared_mib", lambda engine: WORKING_SETS.get(engine, 1024))

    # GPU workers that ask for room exactly as VoiceWorker.start does, then
    # occupy it; no child process.
    def start(self, progress=None):
        vw.admit_gpu(self.declared_mib, self.stage)
        card.taken += self.declared_mib
        card.log.append(("gpu", self.stage))
        self.device, self.model = "cuda", self.args.get("model") or self.engine
        self.voice = self.args.get("voice")
        self._alive = True
        return self
    monkeypatch.setattr(vw.VoiceWorker, "start", start)
    monkeypatch.setattr(vw.VoiceWorker, "alive", lambda self: True)
    monkeypatch.setattr(vw.VoiceWorker, "stop", lambda self, reason="": None)
    monkeypatch.setattr(vw, "_HELD", {})
    monkeypatch.setattr(vw, "NOTICES", [])

    class _CpuEar(vw.EarEngine):
        name, device = "faster-whisper", "cpu"

        def __init__(self, model="auto"):
            card.log.append(("cpu", "ear"))
            self.model = model

        def load(self, progress=None):
            pass

    class _CpuMouth(vw.MouthEngine):
        name, device = "kokoro", "cpu"

        def __init__(self, voice="af_heart"):
            card.log.append(("cpu", "mouth"))
            self.voice = voice

        def load(self, progress=None):
            pass

    class _Piper(vw.MouthEngine):
        # The clause-fallback floor is built for any non-Piper mouth.
        name, device = "piper", "cpu"

        def __init__(self, voice="en_US-amy-medium"):
            self.voice = voice

        def load(self, progress=None):
            pass
    monkeypatch.setattr(vw, "CpuWhisperEar", _CpuEar)
    monkeypatch.setattr(vw, "KokoroCpuMouth", _CpuMouth)
    monkeypatch.setattr(vw, "PiperMouth", _Piper)
    monkeypatch.setattr(vw, "gpu_queue", lambda: None)

    # The brain, the arbiter and the front.
    monkeypatch.setattr(residency_arbiter, "get_arbiter", lambda: _Arbiter(card))
    monkeypatch.setattr("agent_friday.services.build_hours.is_active", lambda *a, **k: False)
    monkeypatch.setattr(voice_front, "resolve", lambda settings=None: (BONSAI, ""))
    seat = _FrontSeat(card)
    monkeypatch.setattr(voice_front, "get", lambda: seat)
    monkeypatch.setattr(voice_engine, "build_voice_tool_contract",
                        lambda: {"tools": [], "names": []})
    monkeypatch.setattr(voice, "_build_front_speaker_prompt", lambda settings, label: "P")
    monkeypatch.setattr(laya_router, "warm", lambda: None)
    monkeypatch.setattr(voice, "_VOICE_LEASE_HOLDERS", set())

    # The session around it, as tests/api/test_crew_host_origin.py drives it.
    manifest = SimpleNamespace(refresh_selection=lambda settings: None,
                               snapshot=lambda: {"stages": {}, "contract": {}},
                               subscribe=lambda cb: None, unsubscribe=lambda cb: None)
    selection = {"ear": {"engine": "faster-whisper", "model": "small", "device_policy": "if_free"},
                 "mouth": {"engine": "kokoro", "voice": "af_bella", "device_policy": "required"}}

    class Session:
        def __init__(self, send, **kw):
            import threading
            self.done = threading.Event()

        def start(self):
            self.done.set()

        def close(self):
            pass

    monkeypatch.setattr(voice, "FRIDAY_WS_TOKEN", "")
    monkeypatch.setattr(voice, "_api_token_valid", lambda token: True)
    monkeypatch.setattr(voice, "_ws_auth_ok", lambda ok: True)
    monkeypatch.setattr(voice, "_load_settings", lambda: {
        "voice_engine": "local", "voice_brain_during_calls": "auto",
        "local_voice_kokoro_allow_cpu": True})
    monkeypatch.setattr(voice._vm, "get_manifest", lambda: manifest)
    monkeypatch.setattr(voice._vm, "read_selection", lambda settings: selection)
    monkeypatch.setattr(voice_receipt, "log_route", lambda *a, **kw: None)
    monkeypatch.setattr(local_seats, "resolve", lambda role: "fixture-brain")
    monkeypatch.setattr(voice, "_build_voice_system_prompt",
                        lambda *a, **kw: ("Fixture", {"provider": "local"}))
    monkeypatch.setattr(crew_runtime, "capture_host_origin", lambda: None)
    monkeypatch.setattr(notification_policy, "note_owner_turn", lambda: None)
    monkeypatch.setattr(reflex_turn, "shadow", lambda text: None)
    monkeypatch.setattr(presence, "acting_as", lambda person: nullcontext())
    monkeypatch.setattr(voice_ear_stream, "installed", lambda: False)
    monkeypatch.setattr(voice, "VADEndpointer", lambda **kw: None)
    monkeypatch.setattr(voice, "_local_talk_over_detector", lambda settings: None)
    monkeypatch.setattr(voice_session, "VoiceSession", Session)
    monkeypatch.setattr(voice._voice_live_channel, "register", lambda *a: None)
    monkeypatch.setattr(voice._voice_live_channel, "unregister", lambda *a: None)
    monkeypatch.setattr(voice, "_run_after_call", lambda *a: None)

    def run():
        import json
        frames = []
        socket = SimpleNamespace(send=lambda text: frames.append(json.loads(text)),
                                 close=lambda: None, receive=lambda timeout=None: None)
        with app.test_request_context("/ws/voice?conversation_id=fixture-chat"):
            inspect.unwrap(voice.ws_voice_local)(socket)
        return frames
    return SimpleNamespace(card=card, run=run)


def test_the_call_parks_the_brain_before_it_admits_the_ear_and_the_mouth(call):
    frames = call.run()
    log = call.card.log
    assert ("park", "voice_call") in log, log
    park = log.index(("park", "voice_call"))
    assert ("gpu", "ear") in log and ("gpu", "mouth") in log, (
        "the ear and the mouth are admitted to the GPU once the brain is parked: %s" % log)
    assert park < log.index(("gpu", "ear")) and park < log.index(("gpu", "mouth")), log
    assert log.index(("gpu", "mouth")) < log.index(("front", BONSAI)), log
    assert ("cpu", "ear") not in log and ("cpu", "mouth") not in log, log
    refused = [f for f in frames if f.get("code") == "local_voice_gpu_refused"]
    assert refused == [], refused
    served, = [f for f in frames if f.get("type") == "served_by"]
    assert served["mouth"] == "kokoro@cuda" and served["ear"].endswith("@cuda"), served
    assert served["brain"] == "parked (voice call)" and not served["degraded"]
    status = [f.get("text") for f in frames if f.get("type") == "status"]
    assert status.index("making room for the voice") < status.index("starting Ternary Bonsai 1.7B")


def test_the_park_is_given_back_when_the_call_ends(call):
    call.run()
    assert call.card.log[-1] == ("unpark", "voice_call") and not call.card.parked


def test_the_cpu_kokoro_fallback_never_loads_onto_cuda(monkeypatch):
    from agent_friday.services import kokoro_voice
    monkeypatch.setattr(kokoro_voice, "kokoro_deps_status", lambda: {"kokoro": True, "torch": True})
    monkeypatch.setattr(kokoro_voice, "kokoro_import_status",
                        lambda: {"ok": True, "missing": "", "error": ""})
    import sys
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True)))
    assert kokoro_voice.KokoroTTS("af_heart", allow_cpu=True)._resolve_device() == "cuda"
    assert kokoro_voice.KokoroTTS("af_heart", allow_cpu=True,
                                  cpu_only=True)._resolve_device() == "cpu"
    from agent_friday.services import voice_workers as vw
    assert vw.KokoroCpuMouth("af_heart")._tts.cpu_only is True
