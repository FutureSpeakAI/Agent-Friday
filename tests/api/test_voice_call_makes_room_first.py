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
        self.brain_mib = BRAIN_MIB
        self.low = None          # the least free the card ever had
        # A real park returns before the seat's process has exited and the
        # driver has the memory back: it frees after `release_polls` polls of
        # the wait (0: at once; None: never).
        self.release_polls = 0
        self.polls_since_park = 0
        self.clock = 0.0

    def brain_freed(self):
        return self.parked and self.release_polls is not None \
            and self.polls_since_park >= self.release_polls

    def free_mib(self):
        return FREE_WITH_BRAIN_MIB + (self.brain_mib if self.brain_freed() else 0) - self.taken

    def tick(self, seconds):
        self.clock += seconds
        self.polls_since_park += 1

    def take(self, mib):
        self.taken += mib
        free = self.free_mib()
        self.low = free if self.low is None else min(self.low, free)


class _Arbiter:
    def __init__(self, card):
        self.card = card

    def grant(self, kind, ttl_s=300):
        self.card.parked = True
        self.card.polls_since_park = 0
        self.card.log.append(("park", kind))
        return {"ok": True, "lease": {"kind": kind, "displaced": [{"role": "brain"}]}}

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
        # The front's llama-server takes its real need from the card.
        from agent_friday.services import voice_front
        self.card.take(voice_front.vram_need_mib(model))
        self.card.log.append(("front", model))

    def prefill(self, prompt, contract):
        return {"ms": 1}

    def disarm(self, holder="session"):
        # The front's llama-server stops and its memory comes back.
        from agent_friday.services import voice_front
        if ("front", BONSAI) in self.card.log:
            self.card.take(-voice_front.vram_need_mib(BONSAI))
            self.card.log.append(("stop", "front"))


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
    # The park wait's clock and sleep: fake, so a slow release costs no time.
    monkeypatch.setattr(vw, "_sleep", card.tick, raising=False)
    monkeypatch.setattr(vw, "_clock", lambda: card.clock, raising=False)

    # GPU workers that ask for room exactly as VoiceWorker.start does, then
    # occupy it; no child process.
    def start(self, progress=None):
        vw.admit_gpu(self.declared_mib, self.stage)
        card.take(self.declared_mib)
        card.log.append(("gpu", self.stage))
        self.device, self.model = "cuda", self.args.get("model") or self.engine
        self.voice = self.args.get("voice")
        self._alive = True
        return self
    monkeypatch.setattr(vw.VoiceWorker, "start", start)
    monkeypatch.setattr(vw.VoiceWorker, "alive", lambda self: True)
    def stop(self, reason=""):
        if getattr(self, "_alive", False):
            self._alive = False
            card.take(-self.declared_mib)
        card.log.append(("stop", self.stage))
    monkeypatch.setattr(vw.VoiceWorker, "stop", stop)
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
    # The arbiter's survey of live seats: the brain answers until parked.
    monkeypatch.setattr(residency_arbiter, "survey_live_seats", lambda *a, **k: (
        {} if card.parked else {"fixture-brain": (4242, 8090)}))
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


def test_the_whole_voice_stays_above_the_display_reserve(call):
    # 12 GB card: everything fits; nothing ever dips under the reserve.
    frames = call.run()
    served, = [f for f in frames if f.get("type") == "served_by"]
    assert served["mouth"] == "kokoro@cuda"
    assert call.card.low >= RESERVE_MIB, call.card.low


def test_on_an_8_gb_card_the_front_is_counted_before_the_ear_and_mouth(call):
    # The park frees only 2.2 GB: ear + mouth fit on their own, but not with
    # the front that arms after them. Its need is held while they are
    # admitted, so a stage that would push the front under the reserve runs
    # on the CPU instead, and the card never goes under the reserve.
    call.card.brain_mib = 2200
    frames = call.run()
    assert ("park", "voice_call") in call.card.log
    assert ("front", BONSAI) in call.card.log
    assert call.card.low >= RESERVE_MIB, (call.card.low, call.card.log)
    refused = [f["message"] for f in frames if f.get("code") == "local_voice_gpu_refused"]
    assert refused and "held for the voice front" in refused[0], refused


def test_a_failure_after_the_park_gives_the_room_back(call, monkeypatch):
    from agent_friday.services import voice_delivery

    def boom(*a, **kw):
        raise RuntimeError("preferences unavailable")
    monkeypatch.setattr(voice_delivery, "initialize_session_preferences", boom)
    with pytest.raises(RuntimeError, match="preferences unavailable"):
        call.run()
    assert ("park", "voice_call") in call.card.log
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


def test_the_call_waits_for_the_parked_brain_to_free_the_card(call):
    # The brain's memory comes back four polls (2 s) after the park returns:
    # admitted at once, the ear and mouth would see the old free figure.
    call.card.release_polls = 4
    frames = call.run()
    log = call.card.log
    assert ("gpu", "ear") in log and ("gpu", "mouth") in log, log
    assert ("cpu", "ear") not in log and ("cpu", "mouth") not in log, log
    served, = [f for f in frames if f.get("type") == "served_by"]
    assert served["mouth"] == "kokoro@cuda" and served["ear"].endswith("@cuda"), served
    assert not served.get("park")
    status = [f.get("text") for f in frames if f.get("type") == "status"]
    assert "waiting for the card to clear" in status
    assert 0 < call.card.clock <= 20


def test_a_park_that_never_frees_falls_back_and_says_so(call):
    call.card.release_polls = None
    frames = call.run()
    assert ("cpu", "ear") in call.card.log and ("cpu", "mouth") in call.card.log
    served, = [f for f in frames if f.get("type") == "served_by"]
    assert "still clearing after the main model was parked" in served["park"], served
    notes = [f for f in frames if f.get("code") == "local_voice_park_slow"]
    assert notes and "after the main model was parked" in notes[0]["message"], notes
    assert call.card.clock <= 20.5, "the wait is bounded"


def test_a_release_still_rising_at_the_limit_stops_at_twenty_seconds(call, monkeypatch):
    from agent_friday.services import voice_workers as vw
    # Free memory creeps up 1 MiB a poll and never reaches the room needed.
    base = call.card.free_mib
    monkeypatch.setattr(call.card, "free_mib", lambda: base() + call.card.polls_since_park)
    call.card.release_polls = None
    w = vw.wait_for_room(100000)
    assert w["ok"] is False and w["settled"] is False and w["waited_s"] == 20.0


def test_served_by_says_parked_only_when_the_brain_is_measured_gone(call, monkeypatch):
    # The live failure: the grant displaced nothing and the brain kept serving.
    from agent_friday.services import residency_arbiter
    monkeypatch.setattr(_Arbiter, "grant", lambda self, kind, ttl_s=300: (
        self.card.log.append(("park", kind)) or
        {"ok": True, "lease": {"kind": kind, "displaced": []}}))
    monkeypatch.setattr(residency_arbiter, "survey_live_seats",
                        lambda *a, **k: {"fixture-brain": (4242, 8090)})
    frames = call.run()
    served, = [f for f in frames if f.get("type") == "served_by"]
    assert served["brain"] != "parked (voice call)", served
    codes = {f.get("code") for f in frames if f.get("type") == "error-nonfatal"}
    assert "local_voice_brain_not_parked" in codes, codes
    assert ("front", BONSAI) not in call.card.log, "no front loaded into a full card"
    assert call.card.low is None or call.card.low >= RESERVE_MIB


def test_a_refused_front_moves_the_engines_off_the_card_before_the_brain_returns(call, monkeypatch):
    # After the ear and mouth are in, another program takes 5 GB: the front
    # no longer fits. The ear and mouth leave the card BEFORE the lease (and
    # with it the brain) is given back.
    take = call.card.take

    def take_then_grab(mib):
        take(mib)
        if mib == WORKING_SETS["kokoro-cuda"] and not getattr(call.card, "grabbed", False):
            call.card.grabbed = True
            call.card.taken += 5000
    monkeypatch.setattr(call.card, "take", take_then_grab)
    frames = call.run()
    log = call.card.log
    assert ("front", BONSAI) not in log, log
    assert ("stop", "ear") in log and ("stop", "mouth") in log, log
    unpark = log.index(("unpark", "voice_call"))
    assert log.index(("stop", "ear")) < unpark and log.index(("stop", "mouth")) < unpark, log
    served, = [f for f in frames if f.get("type") == "served_by"]
    assert served["mind"] == "fixture-brain@local (thinking mode)", served
    assert served["ear"].endswith("@cpu") and served["mouth"].endswith("@cpu"), served
    codes = {f.get("code") for f in frames if f.get("type") == "error-nonfatal"}
    assert "local_voice_front_refused" in codes, codes


def test_a_call_that_parked_the_brain_ends_by_clearing_the_card_before_the_restore(call):
    frames = call.run()
    served, = [f for f in frames if f.get("type") == "served_by"]
    assert served["ear"].endswith("@cuda") and served["mouth"] == "kokoro@cuda"
    log = call.card.log
    end = log[log.index(("front", BONSAI)) + 1:]
    assert end == [("stop", "front"), ("stop", "ear"), ("stop", "mouth"),
                   ("unpark", "voice_call")], end
    # Back where it started: the brain restored and nothing of the voice left.
    assert not call.card.parked and call.card.taken == 0
    assert call.card.free_mib() == FREE_WITH_BRAIN_MIB


def test_a_call_that_did_not_park_keeps_the_idle_unload(call, monkeypatch):
    from agent_friday.routes import voice as rv
    monkeypatch.setattr(rv, "_brain_parked_for_call", lambda *a, **k: False)
    call.card.parked = True       # room enough beside the brain for the whole voice
    call.run()
    log = call.card.log
    assert ("unpark", "voice_call") not in log and ("park", "voice_call") not in log
    assert ("stop", "ear") not in log and ("stop", "mouth") not in log, log
