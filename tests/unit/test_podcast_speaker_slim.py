"""The podcast speaker stays small and leaves when it is not needed.

(a) The speaking child runs with a small thread cap, reports its private
    memory after every line, and is ended and replaced once it passes its
    budget, so an episode never grows it past the ceiling. The real model's
    peak is measured on a fixture render (opt-in: FRIDAY_SPEAKER_PEAK_TEST=1,
    it loads Kokoro).
(b) The speaker is released whenever no episode can run now (all waiting on
    a retry or a gate), not only when the queue is empty.
(c) A speaker is launched only with >= 6 GB of commit headroom; a speaker
    already running is not stalled by its own memory.
"""
from __future__ import annotations

import os
import time

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_render as render


@pytest.fixture
def fake_speaker(monkeypatch):
    monkeypatch.setenv("FRIDAY_PODCAST_SPEAKER_FAKE", "1")
    kok = render.ProcessKokoro()
    yield kok
    kok.unload()


# (a) ─────────────────────────────────────────────────────────────────────────

def test_the_speaker_child_reports_its_private_memory(fake_speaker):
    samples = fake_speaker.speak("A short line.", "af_heart")
    assert len(samples) > 0
    assert isinstance(fake_speaker.last_private_mb, int) and fake_speaker.last_private_mb >= 0
    assert fake_speaker.peak_private_mb >= fake_speaker.last_private_mb


def test_a_speaker_over_its_budget_is_replaced_after_the_line(fake_speaker, monkeypatch):
    monkeypatch.setenv("FRIDAY_PODCAST_SPEAKER_FAKE_MB", str(render.SPEAKER_RECYCLE_MB + 500))
    first = fake_speaker.speak("One line.", "af_heart")
    assert len(first) > 0                                  # the line itself is kept
    assert not fake_speaker.loaded()                       # and the child is gone
    assert fake_speaker.recycled == 1
    monkeypatch.setenv("FRIDAY_PODCAST_SPEAKER_FAKE_MB", "100")
    assert len(fake_speaker.speak("The next line.", "af_heart")) > 0
    assert fake_speaker.loaded()                           # a fresh child spoke it


def test_the_child_runs_with_a_small_thread_cap():
    env = render.ProcessKokoro()._env()
    for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "FRIDAY_PODCAST_SPEAKER_THREADS"):
        assert env[k] == str(render.SPEAKER_THREADS), k
    assert render.SPEAKER_THREADS <= 4


@pytest.mark.skipif(os.environ.get("FRIDAY_SPEAKER_PEAK_TEST") != "1",
                    reason="loads the real Kokoro model; run deliberately (FRIDAY_SPEAKER_PEAK_TEST=1)")
def test_the_real_speakers_peak_stays_under_three_gigabytes():
    from podcast_briefing_fixture import docs, good_lines
    kok = render.ProcessKokoro()
    try:
        for ln in good_lines(docs()):
            kok.speak(ln["text"], "af_heart" if ln["speaker"] == "a" else "bf_emma")
        assert kok.peak_private_mb < 3072, kok.peak_private_mb
    finally:
        kok.unload()


# (b) ─────────────────────────────────────────────────────────────────────────

def test_the_speaker_is_released_when_no_episode_can_run_now(monkeypatch):
    released = []
    monkeypatch.setattr(render, "release_speaker", lambda: released.append(1))
    later = {"id": "E1", "status": "queued", "retry_after": time.time() + 600}
    monkeypatch.setattr(pe, "pending", lambda: [later])
    assert pe._worker_tick() == "idle" and released == [1]


def test_the_speaker_stays_while_an_episode_runs(monkeypatch):
    released, produced = [], []
    monkeypatch.setattr(render, "release_speaker", lambda: released.append(1))
    monkeypatch.setattr(pe, "pending", lambda: [{"id": "E2", "status": "queued", "priority": "now"}])
    monkeypatch.setattr(pe, "_gate_reason", lambda ep: "")
    monkeypatch.setattr(pe, "_update", lambda eid, **k: {})
    monkeypatch.setattr(pe, "produce", lambda eid, **k: produced.append(eid))
    assert pe._worker_tick() == "ran" and produced == ["E2"] and released == []


def test_an_empty_queue_releases_the_speaker(monkeypatch):
    released = []
    monkeypatch.setattr(render, "release_speaker", lambda: released.append(1))
    monkeypatch.setattr(pe, "pending", lambda: [])
    assert pe._worker_tick() == "empty" and released == [1]


# (c) ─────────────────────────────────────────────────────────────────────────

def test_a_speaker_is_launched_only_with_its_measured_headroom(monkeypatch):
    monkeypatch.setattr(render, "speaker_running", lambda: False)
    monkeypatch.setattr(render, "commit_headroom_mb", lambda: pe.VOICE_HEADROOM_MB - 1)
    assert pe._room_to_speak() is False
    monkeypatch.setattr(render, "commit_headroom_mb", lambda: pe.VOICE_HEADROOM_MB + 1)
    assert pe._room_to_speak() is True


def test_a_running_speaker_is_not_stalled_by_its_own_memory(monkeypatch):
    monkeypatch.setattr(render, "speaker_running", lambda: True)
    monkeypatch.setattr(render, "commit_headroom_mb", lambda: 2000)
    assert pe._gate_reason({"priority": "now"}) == ""
    assert pe._room_to_speak() is True
