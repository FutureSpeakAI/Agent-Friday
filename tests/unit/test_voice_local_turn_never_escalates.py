"""A local voice turn runs on the local seat or fails honestly; it never
escalates to a cloud provider (local voice spec P0, defect 6c).

``_generate_agent`` has a resilience ladder (local -> cloud -> openai). For a
local voice turn that ladder is a silent lie: the owner chose a local mind and
a dead seat had a cloud model answer for it. ``session_ctx["pin_to_seat"]``
makes the seat the only leg; its failure raises, and the voice session speaks
a plain sentence instead of an exception string.
"""
import threading
import time

import pytest

from agent_friday.services import agent
from agent_friday.services import voice_session as vs


@pytest.fixture
def providers(monkeypatch):
    calls = {"local": 0, "cloud": 0, "openai": 0}

    def _local(*a, **k):
        calls["local"] += 1
        raise ConnectionResetError(10054, "the seat dropped mid-turn")

    def _cloud(*a, **k):
        calls["cloud"] += 1
        return "a cloud model answered", []

    def _openai(*a, **k):
        calls["openai"] += 1
        return "an openai-compatible model answered", []
    monkeypatch.setattr(agent, "_call_ollama", _local)
    monkeypatch.setattr(agent, "_call_claude_agent", _cloud)
    monkeypatch.setattr(agent, "_call_openai", _openai)
    monkeypatch.setattr(agent, "get_anthropic_client", lambda: object())
    monkeypatch.setattr("agent_friday.services.demo_mode.is_demo", lambda: False)
    return calls


def _turn(pinned):
    return agent._generate_agent_untraced(
        [{"role": "user", "content": "what's on my calendar"}],
        system="You are Friday.", model="bonsai2:27b", max_tokens=32,
        session_ctx={"authenticated": True, "provider": "local", "is_voice": True,
                     "surface": "voice-local", "pin_to_seat": pinned})


def test_a_pinned_turn_whose_seat_dies_reaches_no_cloud_provider(providers):
    with pytest.raises(RuntimeError) as ei:
        _turn(pinned=True)
    assert providers["local"] == 1
    assert providers["cloud"] == 0 and providers["openai"] == 0, (
        "a local voice turn was answered by a cloud provider: %r" % providers)
    assert "bonsai2:27b" in str(ei.value)


def test_the_resilience_ladder_is_unchanged_for_unpinned_turns(providers):
    # The control: without the pin the same failure walks the ladder, which
    # is what made the pin necessary.
    try:
        _turn(pinned=False)
    except Exception:
        pass
    assert providers["cloud"] + providers["openai"] >= 1


class _Mouth:
    name, device = "kokoro", "cpu"

    def __init__(self):
        self.spoken = []

    def synthesize_stream(self, text, cancel=None):
        self.spoken.append(text)
        yield b"\x00\x01" * 2400


class _VAD:
    _buf = bytearray()
    _in_speech = False

    def feed(self, pcm):
        return None

    def flush(self):
        return None


def test_the_session_speaks_an_honest_line_not_the_exception():
    mouth = _Mouth()
    frames = []

    def gen(text, on_delta, cancel):
        raise RuntimeError("the local seat bonsai2:27b could not answer "
                           "(local (bonsai2:27b): [WinError 10061])")
    s = vs.VoiceSession(lambda o: frames.append(o) or True, ear=object(), mouth=mouth,
                        vad=_VAD(), generate=gen)
    try:
        th = threading.Thread(target=s.run_turn, args=("hello",), daemon=True)
        th.start()
        th.join(5)
        end = time.monotonic() + 3
        while not mouth.spoken and time.monotonic() < end:
            time.sleep(0.01)
    finally:
        s.close()
    said = " ".join(mouth.spoken)
    assert said == vs.MIND_FAILED_LINE
    assert "WinError" not in said and "RuntimeError" not in said
