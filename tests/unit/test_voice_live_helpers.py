"""Unit tests for the Gemini Live continuity helpers in routes/voice.py.

These pin the pieces that make an hours-long voice call survivable:
the cross-connection resumption cache (browser reconnect resumes the SAME
conversation), the speech-RMS gate that arms the stall watchdog, and the
GoAway time_left parsing that schedules a graceful drain.
"""
import math
import struct

import pytest

from agent_friday.routes import voice as v


@pytest.fixture(autouse=True)
def _clean_resume_cache():
    v._live_resume_clear()
    yield
    v._live_resume_clear()


# ── Resumption cache ──────────────────────────────────────────────────────

def test_resume_store_load_roundtrip():
    v._live_resume_store("handle-1", "model-a", "Aoede")
    assert v._live_resume_load("model-a", "Aoede") == "handle-1"


def test_resume_load_rejects_model_or_voice_mismatch():
    v._live_resume_store("handle-1", "model-a", "Aoede")
    # A handle only resumes the session it came from — different model or
    # voice must start fresh, not resume into a config Gemini will reject.
    assert v._live_resume_load("model-b", "Aoede") is None
    assert v._live_resume_load("model-a", "Kore") is None


def test_resume_clear_and_empty():
    assert v._live_resume_load("model-a", "Aoede") is None
    v._live_resume_store("handle-1", "model-a", "Aoede")
    v._live_resume_clear()
    assert v._live_resume_load("model-a", "Aoede") is None


def test_resume_load_expires_after_ttl(monkeypatch):
    v._live_resume_store("handle-1", "model-a", "Aoede")
    real_time = v._time.time
    monkeypatch.setattr(v._time, "time",
                        lambda: real_time() + v._LIVE_RESUME_TTL_S + 1)
    assert v._live_resume_load("model-a", "Aoede") is None


# ── Speech RMS gate ───────────────────────────────────────────────────────

def _pcm_sine(amplitude, n=1600, rate=16000, freq=440):
    return struct.pack(
        f"<{n}h",
        *(int(amplitude * math.sin(2 * math.pi * freq * i / rate))
          for i in range(n)))


def test_quick_rms_silence_vs_speech():
    assert v._quick_rms(b"\x00\x00" * 800) == 0
    quiet = v._quick_rms(_pcm_sine(150))    # room noise / speaker echo bleed
    loud = v._quick_rms(_pcm_sine(8000))    # actual speech
    assert quiet < v.LIVE_SPEECH_RMS <= loud


def test_quick_rms_empty_and_tiny():
    assert v._quick_rms(b"") == 0
    assert v._quick_rms(b"\x00") == 0          # odd byte → no full sample
    assert v._quick_rms(struct.pack("<h", 1000)) == 1000


# ── Barge-in detector ─────────────────────────────────────────────────────
#
# The contract: speaker bleed (Friday's own voice re-captured by the mic)
# must NEVER fire — that's the bug that got the old client-side detector
# removed — while deliberate, sustained talk-over fires within ~sustain_ms.

def _mk_detector(**kw):
    kw.setdefault("grace_ms", 800)
    kw.setdefault("sustain_ms", 200)
    d = v.LiveBargeDetector(**kw)
    d.reset_turn(now=1000.0)
    return d


def test_barge_ignores_grace_window_even_when_loud():
    d = _mk_detector()
    # Loud chunks inside the grace window never fire — they're sampled for the
    # baseline, which is seeded (percentile, capped) on the first post-grace feed.
    for i in range(9):
        assert d.feed(3000, 85, now=1000.0 + i * 0.085) is False
    d.feed(100, 85, now=1001.0)
    assert 0 < d.ema <= d.BLEED_EMA_CAP


def test_barge_grace_interjection_does_not_poison_baseline():
    # User interjects during PART of the grace window: the 25th-percentile
    # seeding keeps the baseline at the actual bleed level (the quiet chunks),
    # so their continued speech after grace still fires.
    d = _mk_detector()
    t = 1000.0
    for _ in range(5):                       # real bleed
        d.feed(300, 85, now=t)
        t += 0.085
    for _ in range(4):                       # user already talking
        d.feed(5000, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = [d.feed(2500, 85, now=t + i * 0.085) for i in range(4)]
    assert d.ema <= 400          # seeded from the bleed quartile, not the speech
    assert any(fired)


def test_barge_bleed_never_fires_and_sets_baseline_bar():
    d = _mk_detector()
    # Grace: bleed ~300 RMS learned as baseline.
    t = 1000.0
    for _ in range(10):
        assert d.feed(300, 85, now=t) is False
        t += 0.085
    t = 1001.0  # past grace
    # Post-grace bleed keeps not firing (300 < max(550, 3*300=900)).
    for _ in range(50):
        assert d.feed(320, 85, now=t) is False
        t += 0.085
    # Loud-ish echo spike below the relative bar also doesn't fire.
    assert d.feed(800, 85, now=t) is False


def test_barge_fires_on_sustained_talkover():
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):                      # learn ~300 bleed during grace
        d.feed(300, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = []
    for _ in range(4):                       # 4 × 85ms = 340ms of loud speech
        fired.append(d.feed(2500, 85, now=t))
        t += 0.085
    assert any(fired)
    # Fired at/after the sustain threshold, not on the first loud chunk.
    assert fired[0] is False and fired[-1] is True


def test_barge_single_spike_does_not_fire():
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):
        d.feed(200, 85, now=t)
        t += 0.085
    t = 1001.0
    assert d.feed(4000, 85, now=t) is False          # one 85ms bang (door slam)
    assert d.feed(150, 85, now=t + 0.085) is False   # back to quiet resets sustain
    assert d.sustained == 0.0


def test_barge_quiet_room_floor_governs():
    # Near-zero bleed (headset mic, AEC very good): the absolute floor is the
    # bar, so normal speech (well above 550) still fires.
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):
        d.feed(5, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = [d.feed(900, 85, now=t + i * 0.085) for i in range(4)]
    assert any(fired)


def test_barge_baseline_poisoning_is_capped():
    # A user who talks THROUGH the entire grace window must still be able to
    # interrupt afterwards: baseline seeding is capped, so the bar can't rise
    # beyond mult × BLEED_EMA_CAP.
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):                      # loud speech all through grace
        d.feed(6000, 85, now=t)
        t += 0.085
    t = 1001.0
    fired = [d.feed(6000, 85, now=t + i * 0.085) for i in range(4)]
    assert d.ema <= v.LiveBargeDetector.BLEED_EMA_CAP
    assert any(fired)


def test_barge_reset_turn_relearns_baseline():
    d = _mk_detector()
    t = 1000.0
    for _ in range(10):
        d.feed(300, 85, now=t)
        t += 0.085
    d.feed(2500, 85, now=1001.0)
    assert d.sustained > 0
    d.reset_turn(now=2000.0)                 # next response starts
    assert d.sustained == 0.0 and d.ema == 0.0
    # New grace window applies again.
    assert d.feed(2500, 85, now=2000.1) is False


# ── GoAway time_left parsing ──────────────────────────────────────────────

def test_duration_parsing_variants():
    from datetime import timedelta
    assert v._duration_to_seconds(None) is None
    assert v._duration_to_seconds(5) == 5.0
    assert v._duration_to_seconds(2.5) == 2.5
    assert v._duration_to_seconds("7s") == 7.0
    assert v._duration_to_seconds("3") == 3.0
    assert v._duration_to_seconds(timedelta(seconds=9)) == 9.0
    assert v._duration_to_seconds("not-a-duration") is None
