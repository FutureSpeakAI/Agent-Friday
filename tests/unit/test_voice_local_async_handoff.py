"""The async brain lane on LOCAL voice (local voice spec P3): a deep question
goes to the brain, pinned to its seat, and comes back to the front without
being sealed for a cloud model; a result that finishes later is spoken
between turns, introduced as not from the owner and never stored as
something he said.
"""
import threading
import time

import pytest

from agent_friday.services import voice_engine as ve
from agent_friday.services import voice_session as vs


@pytest.fixture
def brain(monkeypatch):
    calls = {"gen": [], "gate": 0}

    def fake_generate(messages, system=None, model=None, **kw):
        calls["gen"].append({"model": model, "ctx": kw.get("session_ctx")})
        return "Your sister's birthday is in June.", []

    def fake_gate(result, fname):
        calls["gate"] += 1
        return "[EGRESS-GATE: withheld]"
    monkeypatch.setattr("agent_friday.services.agent._generate_agent", fake_generate)
    monkeypatch.setattr("agent_friday.routes.voice._gate_voice_tool_result", fake_gate)
    monkeypatch.setattr("agent_friday.routes.voice._build_voice_system_prompt",
                        lambda settings=None, description=None, seat=None: ("P", {"volatile": ""}))
    monkeypatch.setattr(ve, "_brain_serving", lambda: "bonsai2:27b")
    return calls


def test_the_front_asks_the_brain_pinned_and_gets_the_answer_unsealed(brain):
    session = {"engine": "local", "conversation_id": "c1", "async_routing": "local_only"}
    out = ve._tool_ask_friday({"question": "when is my sister's birthday?"}, session)
    assert out == "Your sister's birthday is in June."
    assert brain["gate"] == 0, "a local answer for a local front was sealed for the cloud"
    call, = brain["gen"]
    assert call["model"] == "bonsai2:27b" and call["ctx"]["pin_to_seat"] is True
    assert call["ctx"]["provider"] == "local"


def test_the_cloud_path_still_seals_and_is_pinned_too(brain, monkeypatch):
    monkeypatch.setattr("agent_friday.services.local_seats.resolve",
                        lambda role, configured=None: "bonsai2:27b")
    out = ve._tool_ask_friday({"question": "q"}, {"conversation_id": "c1"})
    assert out == "[EGRESS-GATE: withheld]" and brain["gate"] == 1
    assert brain["gen"][0]["ctx"]["pin_to_seat"] is True, (
        "a prompt gated for the local seat must never ride a cloud leg")


def test_local_tool_calls_carry_the_local_provider():
    ctx = ve._voice_ctx({"engine": "local", "conversation_id": "c9", "owner_text": "yes"})
    assert ctx["provider"] == "local" and ctx["surface"] == "voice-local"
    assert ctx["conversation_id"] == "c9" and ctx["owner_text"] == "yes"
    assert "provider" not in ve._voice_ctx({"conversation_id": "c9"})


class _Mouth:
    name, device = "kokoro", "cpu"

    def __init__(self):
        self.spoken = []

    def synthesize_stream(self, text, cancel=None):
        self.spoken.append(text)
        yield b"\x00\x01" * 240


class _VAD:
    _buf = bytearray()
    _in_speech = False

    def feed(self, pcm):
        return None

    def flush(self):
        return None


def test_a_late_result_is_spoken_between_turns_and_not_stored_as_his_words():
    frames, persisted, minds = [], [], []
    release = threading.Event()

    def gen(text, on_delta, cancel):
        minds.append(text)
        if text == "a long question":
            release.wait(3)
        on_delta("Okay. ")
        return "Okay."
    s = vs.VoiceSession(lambda o: frames.append(o) or True, ear=object(), mouth=_Mouth(),
                        vad=_VAD(), generate=gen,
                        hooks={"persist": lambda u, a, cid: persisted.append((u, a))})
    try:
        th = threading.Thread(target=s.run_turn, args=("a long question",), daemon=True)
        th.start()
        time.sleep(0.1)
        s.deliver("[Not from the user: the work has finished:]\nThe report is saved.",
                  "task_result")
        time.sleep(0.2)
        assert len(minds) == 1, "a late result interrupted a running turn"
        release.set()
        th.join(3)
        end = time.monotonic() + 3
        while len(persisted) < 2 and time.monotonic() < end:
            time.sleep(0.02)
    finally:
        s.close()
    assert minds[1].startswith("[Not from the user")
    assert persisted[1][0] == "", "a late result was stored as the owner's words"
    shown = [f["text"] for f in frames if f["type"] == "input_transcript"]
    assert shown == ["a long question"]
