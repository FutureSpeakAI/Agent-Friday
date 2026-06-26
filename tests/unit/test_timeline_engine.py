"""Unit tests for the timeline composition engine (services/timeline_engine.py).

Offline-only and ffmpeg-free: the filter-graph BUILDER is a pure function, so we
unit-test the generated argv structure without ever invoking ffmpeg. compose()'s
no-ffmpeg demo fallback is exercised by stubbing ffmpeg discovery to None.
"""
import core
from services import timeline_engine as te


def _clip(name, data=b"FAKEVIDEO"):
    core.CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    p = core.CREATIONS_DIR / name
    p.write_bytes(data)
    return name


# ── Export presets ───────────────────────────────────────────────────────────
def test_export_formats_include_platform_presets():
    fmts = te.export_formats()
    for k in ("mp4-1080p", "mp4-vertical-9x16", "webm", "gif-preview", "audio-mp3"):
        assert k in fmts


def test_profile_aliases_resolve():
    key, prof = te._resolve_profile("tiktok")
    assert key == "tiktok-vertical" and prof["h"] == 1920


# ── Validation ───────────────────────────────────────────────────────────────
def test_validate_rejects_empty_and_missing_files():
    ok, errs = te.validate_timeline({})
    assert ok is False
    ok, errs = te.validate_timeline({"tracks": [{"kind": "video",
                                                 "clips": [{"file": "nope.mp4"}]}]})
    assert ok is False and any("not found" in e for e in errs)


def test_validate_accepts_real_clip():
    name = _clip("tl-valid.mp4")
    ok, errs = te.validate_timeline({"tracks": [{"kind": "video",
                                                "clips": [{"file": name}]}]})
    assert ok is True and not errs


def test_validate_flags_unknown_transition():
    name = _clip("tl-trans.mp4")
    ok, errs = te.validate_timeline({"tracks": [{"kind": "video", "clips": [
        {"file": name, "transition_in": {"type": "warp"}}]}]})
    assert ok is False and any("transition" in e for e in errs)


# ── Pure filter-graph builder ────────────────────────────────────────────────
def test_build_command_concat_for_cuts():
    a, b = _clip("tl-a.mp4"), _clip("tl-b.mp4")
    tl = {"fps": 30, "tracks": [{"kind": "video", "clips": [
        {"file": a, "in": 0, "out": 5}, {"file": b, "in": 0, "out": 5}]}],
        "exports": ["mp4-1080p"]}
    cmd = te.build_ffmpeg_command(tl, "mp4-1080p", "ffmpeg", "out.mp4")
    assert cmd[0] == "ffmpeg" and cmd[-1] == "out.mp4"
    assert cmd.count("-i") == 2
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=2" in fc                      # cuts → fast concat
    assert "-c:v" in cmd and "libx264" in cmd


def test_build_command_xfade_for_crossfade():
    a, b = _clip("tl-xa.mp4"), _clip("tl-xb.mp4")
    tl = {"tracks": [{"kind": "video", "clips": [
        {"file": a, "in": 0, "out": 5},
        {"file": b, "in": 0, "out": 5, "transition_in": {"type": "crossfade", "dur": 0.5}}]}]}
    fc = te.build_ffmpeg_command(tl, "mp4-1080p", "ffmpeg", "out.mp4")
    fcs = fc[fc.index("-filter_complex") + 1]
    assert "xfade=transition=fade" in fcs


def test_build_command_audio_ducking_under_dialogue():
    v = _clip("tl-v.mp4")
    d = _clip("tl-dia.wav", b"WAV")
    m = _clip("tl-mus.wav", b"WAV")
    tl = {"tracks": [
        {"kind": "video", "clips": [{"file": v, "in": 0, "out": 5}]},
        {"kind": "audio", "clips": [
            {"file": d, "role": "dialogue"},
            {"file": m, "role": "music", "gain_db": -4.0}]}]}
    fc = te.build_ffmpeg_command(tl, "mp4-1080p", "ffmpeg", "out.mp4")
    fcs = fc[fc.index("-filter_complex") + 1]
    assert "sidechaincompress" in fcs              # music ducked under dialogue
    assert "amix=inputs=" in fcs


def test_build_command_overlay_drawtext_title():
    v = _clip("tl-ov.mp4")
    tl = {"tracks": [
        {"kind": "video", "clips": [{"file": v, "in": 0, "out": 5}]},
        {"kind": "overlay", "clips": [{"text": "THE WANDERER", "t": 0.5, "dur": 3}]}]}
    fc = te.build_ffmpeg_command(tl, "mp4-1080p", "ffmpeg", "out.mp4")
    fcs = fc[fc.index("-filter_complex") + 1]
    # drawtext is only emitted when a usable font is found on this machine; the
    # title text must appear in the graph when it is. (CI without fonts skips it.)
    if "drawtext=" in fcs:
        assert "text='THE WANDERER'" in fcs


def test_build_command_no_audio_map_for_gif():
    """GIF carries no audio codec — the builder must not map (or even build) an
    audio chain into it, which would error ffmpeg on an unconnected output."""
    v = _clip("tl-gifv.mp4")
    m = _clip("tl-gifa.wav", b"WAV")
    tl = {"tracks": [
        {"kind": "video", "clips": [{"file": v, "in": 0, "out": 3}]},
        {"kind": "audio", "clips": [{"file": m, "role": "music"}]}]}
    cmd = te.build_ffmpeg_command(tl, "gif-preview", "ffmpeg", "out.gif")
    fcs = cmd[cmd.index("-filter_complex") + 1] if "-filter_complex" in cmd else ""
    assert "amix" not in fcs and "aout" not in fcs   # no audio chain at all
    assert cmd.count("-i") == 1                        # audio input dropped too


def test_build_command_audio_only_strips_video():
    m = _clip("tl-ao.wav", b"WAV")
    tl = {"tracks": [{"kind": "audio", "clips": [{"file": m, "role": "music"}]}]}
    cmd = te.build_ffmpeg_command(tl, "audio-mp3", "ffmpeg", "out.mp3")
    assert "-vn" in cmd and "libmp3lame" in cmd


# ── compose() demo fallback when ffmpeg is unavailable ───────────────────────
def test_compose_demo_when_no_ffmpeg(monkeypatch):
    name = _clip("tl-demo.mp4")
    monkeypatch.setattr(te, "ffmpeg_exe", lambda: None)
    monkeypatch.setattr(te, "_notify", lambda *_a, **_k: None)
    res = te.compose({"tracks": [{"kind": "video", "clips": [{"file": name}]}],
                      "exports": ["mp4-1080p"]})
    assert res["status"] == "demo"
    assert res["files"][0]["filename"].endswith(".md")
    assert (core.CREATIONS_DIR / res["files"][0]["filename"]).exists()


def test_compose_rejects_invalid_timeline():
    res = te.compose({"tracks": []})
    assert res["status"] == "error" and "Invalid timeline" in res["message"]
