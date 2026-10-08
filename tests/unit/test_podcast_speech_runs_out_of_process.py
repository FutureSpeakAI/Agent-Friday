"""A podcast render speaks in a separate process, and that process ends with the render.

Kokoro on the processor brings torch, its thread pools and its native buffers
with it: loaded inside the server it stayed there after the episode, about five
gigabytes and two hundred threads. The renderer's speaker is a child process;
releasing the speaker ends the process, and with it everything it loaded. The
child runs in its tone mode here so the test needs no model.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import podcast_render as render

np = pytest.importorskip("numpy", reason="the child speaker returns numpy audio (podcast extra)")


@pytest.fixture
def fake_child(monkeypatch):
    monkeypatch.setenv("FRIDAY_PODCAST_SPEAKER_FAKE", "1")
    render.release_speaker()
    yield
    render.release_speaker()


def test_the_speaker_never_loads_kokoro_in_this_process(fake_child, monkeypatch):
    def refuse(self):
        raise AssertionError("Kokoro was loaded inside the server process")
    monkeypatch.setattr(render.CpuKokoro, "load", refuse)
    samples = render.speaker().speak("Good evening, here is the news.", "af_heart")
    assert isinstance(samples, np.ndarray) and samples.dtype == np.float32 and len(samples) > 0


def test_releasing_the_speaker_ends_its_process(fake_child):
    sp = render.speaker()
    sp.speak("One line.", "af_heart")
    proc = getattr(sp, "process", None)
    assert proc is not None, "the speaker has no process of its own"
    assert proc.poll() is None, "the speaking process is not running during the render"
    render.release_speaker()
    deadline = time.time() + 10
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    assert proc.poll() is not None, "the speaking process outlived the render"


def test_lines_keep_their_voice_and_order(fake_child):
    sp = render.speaker()
    a = sp.speak("First line.", "af_heart")
    b = sp.speak("A second, longer line from the other host.", "bf_emma")
    assert len(b) > len(a), "each line comes back as its own audio, in order"


def test_a_failure_in_the_child_is_a_render_error(fake_child):
    with pytest.raises(render.RenderError) as caught:
        render.speaker().speak("FAIL", "af_heart")
    assert caught.value.code == "voice_failed"


def test_an_empty_line_costs_no_process(monkeypatch):
    render.release_speaker()
    out = render.speaker().speak("   ", "af_heart")
    assert len(out) == 0
    assert getattr(render.speaker(), "process", None) is None
    render.release_speaker()
