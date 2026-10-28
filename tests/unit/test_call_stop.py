"""Stop everything said during a live phone call (BD-13; NS-19.8-1, NS-20.8-5).

Acceptance: "With ... the model loop wedged, STOP from the hotkey, the phone and voice each
release the key and halt within one second, with a receipt." #436 did texts and the chat
bridges; this is a live call from the owner's verified cell (the ingress connects one only
from that cell; on a call Friday placed, the other person is not the owner). The one fixed
reply, "Stopped.", is spoken (A28), then the call ends. No model, no Twilio, no audio device.
"""
from __future__ import annotations

import array
import json
import threading
import time

import pytest

from agent_friday.phone import live_call as L
from tests.unit.phone_fakes import phone_home  # noqa: F401


@pytest.fixture
def gs(tmp_path, monkeypatch):
    from agent_friday.services import global_stop, agent, local_image
    monkeypatch.setattr(global_stop, "_path", lambda: tmp_path / "global_stop.json")
    monkeypatch.setattr(agent, "_HAS_PYAUTOGUI", False, raising=False)
    monkeypatch.setattr(local_image, "interrupt_comfy", lambda timeout=10: False)
    had_perm, had_kill = agent._CC_PERMISSION.is_set(), agent._CC_KILL.is_set()
    global_stop._reset_for_tests()
    yield global_stop
    global_stop._reset_for_tests()
    (agent._CC_PERMISSION.set if had_perm else agent._CC_PERMISSION.clear)()
    (agent._CC_KILL.set if had_kill else agent._CC_KILL.clear)()


class FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, s):
        self.sent.append(json.loads(s))


class Engine:
    """Hears the given lines in order; records what Friday says."""
    def __init__(self, heard):
        self.heard = list(heard)
        self.said = []

    def transcribe(self, pcm):
        return self.heard.pop(0) if self.heard else ""

    def synthesize(self, text):
        self.said.append(text)
        return array.array("h", [0] * 240).tobytes()


class NoVAD:
    def feed(self, pcm):
        return None


def _bridge(heard, *, direction="inbound", agent=None):
    eng = Engine(heard)
    b = L.CallBridge(FakeWS(), engine=eng, vad=NoVAD(), threaded=True,
                     agent=agent or (lambda h: "Sure."))
    b.handle({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA77",
                                          "customParameters": {"direction": direction}}})
    return b, eng


def _decisions():
    from tests import ledger_support
    return ledger_support.decisions()


def test_a_stop_said_on_the_owners_call_halts_within_a_second_while_the_turn_is_stuck(
        phone_home, gs):
    release, started = threading.Event(), threading.Event()

    def stuck(history):
        started.set()
        release.wait(10)
        return "late"
    b, eng = _bridge(["summarise my day", "Friday, stop everything"], agent=stuck)
    before = len(_decisions())
    try:
        threading.Thread(target=b.on_utterance, args=(b"x",), daemon=True).start()
        assert started.wait(5), "the first turn never started"
        t0 = time.monotonic()
        threading.Thread(target=b.on_utterance, args=(b"y",), daemon=True).start()
        while time.monotonic() < t0 + 1.0 and not gs.active():
            time.sleep(0.01)
        assert gs.active(), "a stuck turn kept the stop said on the call from being heard"
        assert b.ack_done.wait(3) and eng.said[-1] == "Stopped."
        new = _decisions()[before:]
        assert any(d.get("tool") == "global_stop" and d.get("surface") == "phone_call" for d in new)
        assert [d for d in new if d.get("tool") == "stop_ack"][0]["surface"] == "phone_call"
    finally:
        release.set()
    time.sleep(0.05)
    assert "late" not in eng.said, "the stuck turn spoke after the stop"


@pytest.mark.parametrize("words", ["Friday stop", "stop everything", "halt all work now"])
def test_the_stop_words_work_on_the_owners_call(phone_home, gs, words):
    b, eng = _bridge([words], agent=lambda h: pytest.fail("a stop reached the model"))
    b.on_utterance(b"x")
    assert gs.active() and eng.said[-1] == "Stopped."


@pytest.mark.parametrize("words", ["stop", "STOP", "please stop talking"])
def test_a_bare_stop_on_a_call_is_just_words(phone_home, gs, words):
    b, eng = _bridge([words])
    b.on_utterance(b"x")
    assert not gs.active() and eng.said[-1] == "Sure."


def test_the_other_person_on_a_call_friday_placed_cannot_stop_her(phone_home, gs):
    b, eng = _bridge(["Friday stop everything"], direction="outbound")
    b.on_utterance(b"x")
    assert not gs.active(), "someone Friday called stopped everything"
    assert eng.said[-1] == "Sure."


def test_one_stopped_per_stop_on_the_call(phone_home, gs):
    b, eng = _bridge(["stop everything", "stop everything"])
    b.on_utterance(b"x")
    b.on_utterance(b"y")
    assert eng.said.count("Stopped.") == 1
