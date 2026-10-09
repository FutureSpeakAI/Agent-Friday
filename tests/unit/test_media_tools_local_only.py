"""inspect_image / inspect_audio honour the routing mode.

Under Local only no image, video frame or audio byte reaches Gemini: the tool
describes on the local seat, or says plainly that it cannot do the look
locally. With the cloud allowed the Gemini path still runs. Both the Gemini
client and the local seat are stubbed; the assertion is on who was called.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday import core
from agent_friday.services import egress_gate
from agent_friday.services import local_vision
from agent_friday.services import media_tools as mt


class _Resp:
    text = "a red square (cloud)"


class _Models:
    def __init__(self, sink):
        self.sink = sink

    def generate_content(self, model, contents):
        self.sink.append((model, contents))
        return _Resp()


class _Client:
    def __init__(self, sink):
        self.models = _Models(sink)


@pytest.fixture
def cloud_calls(monkeypatch):
    sink = []
    monkeypatch.setattr(mt, "_gemini_client", lambda: _Client(sink))
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda text, provider, field, log_path=None: text)
    return sink


@pytest.fixture
def ledger(monkeypatch):
    rows = []
    monkeypatch.setattr(
        egress_gate, "record_binary_egress",
        lambda provider, field, **kw: rows.append((field, kw.get("action"))) or {})
    return rows


def _mode(monkeypatch, mode):
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"model_routing": {"mode": mode}})


@pytest.fixture
def seat(monkeypatch):
    """A working local vision seat; records each call."""
    calls = []

    def _describe(image_b64, *, mime="image/png", prompt="", **kw):
        calls.append((mime, prompt))
        return {"ok": True, "text": "a red square (local)", "model": "seat-x"}

    monkeypatch.setattr(local_vision, "describe", _describe)
    return calls


@pytest.fixture
def no_seat(monkeypatch):
    monkeypatch.setattr(
        local_vision, "describe",
        lambda *a, **k: {"ok": False, "text": None, "model": None,
                         "reason": "no model is assigned to the conversational seat"})


@pytest.fixture
def image(tmp_path, monkeypatch):
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
    monkeypatch.setattr(mt, "_resolve_media_path", lambda raw: (img, None))
    return img


# ── images ──────────────────────────────────────────────────────────────────

def test_local_only_image_uses_the_local_seat_never_the_cloud(
        monkeypatch, image, cloud_calls, seat, ledger):
    _mode(monkeypatch, "local_only")
    out = mt._tool_inspect_image({"path": str(image), "question": "what colour?"})
    assert cloud_calls == [], "an image reached Gemini under Local only"
    assert "a red square (local)" in out
    assert seat and seat[0][1] == "what colour?"
    assert not any(f == "inspect_image.image" for f, _a in ledger)  # nothing left the machine


def test_local_only_image_without_a_seat_refuses_honestly(
        monkeypatch, image, cloud_calls, no_seat, ledger):
    _mode(monkeypatch, "local_only")
    out = mt._tool_inspect_image({"path": str(image)})
    assert cloud_calls == [], "an image reached Gemini under Local only"
    assert "Local only" in out and "locally" in out
    assert "no model is assigned" in out
    assert ("inspect_image.image", "block") in ledger


def test_local_preferred_falls_back_to_the_cloud_when_the_seat_cannot(
        monkeypatch, image, cloud_calls, no_seat):
    _mode(monkeypatch, "local_preferred")
    out = mt._tool_inspect_image({"path": str(image)})
    assert len(cloud_calls) == 1
    assert "a red square (cloud)" in out


@pytest.mark.parametrize("mode", ["smart", "cloud_only"])
def test_cloud_allowed_modes_keep_the_gemini_path(
        monkeypatch, image, cloud_calls, seat, mode):
    _mode(monkeypatch, mode)
    out = mt._tool_inspect_image({"path": str(image)})
    assert len(cloud_calls) == 1 and cloud_calls[0][0] == "gemini-2.5-flash"
    assert seat == [], "the local seat should not be consulted when the cloud is allowed"
    assert "a red square (cloud)" in out


def test_an_unreadable_routing_setting_fails_closed(
        monkeypatch, image, cloud_calls, no_seat):
    def _boom():
        raise RuntimeError("settings unreadable")
    monkeypatch.setattr(core, "_load_settings", _boom)
    out = mt._tool_inspect_image({"path": str(image)})
    assert cloud_calls == []
    assert "Local only" in out


def test_an_active_local_only_run_is_local_only_whatever_the_mode(
        monkeypatch, image, cloud_calls, no_seat):
    from agent_friday.services import local_only_guard
    _mode(monkeypatch, "cloud_only")
    with local_only_guard.local_only("test run"):
        out = mt._tool_inspect_image({"path": str(image)})
    assert cloud_calls == []
    assert "Local only" in out


# ── video frames ────────────────────────────────────────────────────────────

@pytest.fixture
def video(tmp_path, monkeypatch):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"not really a video")
    monkeypatch.setattr(mt, "_resolve_media_path", lambda raw: (vid, None))
    monkeypatch.setattr(mt, "_ffprobe_duration", lambda p: 3.0)

    def _run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"\x89PNG\r\n\x1a\nframe")

    monkeypatch.setattr(mt.subprocess, "run", _run)
    return vid


def test_local_only_video_frames_stay_local(
        monkeypatch, video, cloud_calls, seat, ledger):
    _mode(monkeypatch, "local_only")
    out = mt._tool_inspect_image({"path": str(video)})
    assert cloud_calls == [], "video frames reached Gemini under Local only"
    assert len(seat) == 3
    assert "3 frames sampled" in out and "a red square (local)" in out
    assert not any(f == "inspect_image.video_frames" for f, _a in ledger)  # nothing left the machine


def test_local_only_video_frames_without_a_seat_refuse(
        monkeypatch, video, cloud_calls, no_seat, ledger):
    _mode(monkeypatch, "local_only")
    out = mt._tool_inspect_image({"path": str(video)})
    assert cloud_calls == []
    assert "Local only" in out
    assert ("inspect_image.video_frames", "block") in ledger


def test_cloud_allowed_video_frames_still_go_to_gemini(
        monkeypatch, video, cloud_calls):
    _mode(monkeypatch, "smart")
    out = mt._tool_inspect_image({"path": str(video)})
    assert len(cloud_calls) == 1
    assert "a red square (cloud)" in out


# ── audio ───────────────────────────────────────────────────────────────────

class _Seg:
    text = " hello there "


class _Whisper:
    def transcribe(self, path):
        return [_Seg()], None


@pytest.fixture
def audio(tmp_path, monkeypatch):
    wav = tmp_path / "say.wav"
    wav.write_bytes(b"RIFFfake")
    monkeypatch.setattr(mt, "_resolve_media_path", lambda raw: (wav, None))
    monkeypatch.setattr(mt, "_ffprobe_duration", lambda p: 2.0)
    monkeypatch.setattr(mt, "_volume_stats", lambda p: ("-20 dB", "-3 dB"))
    monkeypatch.setattr(mt, "_whisper", lambda: _Whisper())
    return wav


def test_local_only_audio_keeps_the_transcript_and_skips_the_listen_check(
        monkeypatch, audio, cloud_calls, ledger):
    _mode(monkeypatch, "local_only")
    out = mt._tool_inspect_audio({"path": str(audio), "question": "is it calm?"})
    assert cloud_calls == [], "audio reached Gemini under Local only"
    assert "transcript: hello there" in out
    assert "listen check unavailable: Local only" in out
    assert ("inspect_audio.audio", "block") in ledger


def test_cloud_allowed_audio_still_gets_the_listen_check(
        monkeypatch, audio, cloud_calls):
    _mode(monkeypatch, "smart")
    out = mt._tool_inspect_audio({"path": str(audio), "question": "is it calm?"})
    assert len(cloud_calls) == 1
    assert "listen check: a red square (cloud)" in out
