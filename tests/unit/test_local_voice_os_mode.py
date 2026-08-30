"""PR-2 (OS mode switch, OS-mode sequence) — behavior 6: voice asset lookup
must check FRIDAY_VOICE_ASSETS (via agent_friday.paths.voice_assets_dir())
BEFORE falling back to the existing lazy-download behavior, so the sealed
Friday Linux image never phones home for a Piper voice or Whisper checkpoint
it already ships baked-in.

Gated ONLY on OS mode (core/os_mode.py's is_os_mode(), duplicated here as
local_voice._os_mode_active() for import-cost reasons — see that function's
own docstring): a Windows-default install must keep downloading into
~/.friday/local_voice exactly as before this PR, even if FRIDAY_VOICE_ASSETS
happens to be set for some unrelated reason.

No network call is exercised in this file: `urllib.request.urlopen` is
monkeypatched to raise if ever called, so a test that reaches the download
path by mistake fails loudly instead of hanging on a real (or refused)
connection.
"""
from __future__ import annotations

import urllib.request

import pytest

from agent_friday.services import local_voice as lv


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError(
            "a voice asset download was attempted — the baked-asset check "
            "should have short-circuited before this point"
        )
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture(autouse=True)
def _isolated_cache_dirs(monkeypatch, tmp_path):
    """Point the normal (downloaded-into) cache dirs at a scratch tmp_path
    subtree, independent of FRIDAY_VOICE_ASSETS, so a test can populate
    "already downloaded" state without it colliding with "baked" state."""
    monkeypatch.setattr(lv, "PIPER_DIR", tmp_path / "cache" / "piper")
    monkeypatch.setattr(lv, "WHISPER_DIR", tmp_path / "cache" / "whisper")
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    monkeypatch.delenv("FRIDAY_VOICE_ASSETS", raising=False)


def _write_piper_voice(directory, voice="en_US-amy-medium"):
    directory.mkdir(parents=True, exist_ok=True)
    onnx = directory / f"{voice}.onnx"
    onnx.write_bytes(b"fake-onnx-weights")
    (directory / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    return onnx


# ── PiperTTS._baked_voice_path / _ensure_voice_file ─────────────────────────

class TestPiperBakedAsset:
    def test_baked_asset_used_under_os_mode(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        baked_dir = tmp_path / "baked-voice"
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(baked_dir))
        baked_onnx = _write_piper_voice(baked_dir)

        tts = lv.PiperTTS()
        assert tts._baked_voice_path() == baked_onnx
        assert tts._ensure_voice_file() == baked_onnx

    def test_baked_asset_ignored_when_os_mode_off(self, monkeypatch, tmp_path):
        """Regression guard: even with FRIDAY_VOICE_ASSETS pointing at a
        perfectly good baked voice, OS mode off must fall through to the
        pre-existing ~/.friday cache path unchanged."""
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        baked_dir = tmp_path / "baked-voice"
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(baked_dir))
        _write_piper_voice(baked_dir)

        tts = lv.PiperTTS()
        assert tts._baked_voice_path() is None

        # Simulate a previously-completed normal download so the fallback
        # path resolves without touching the network.
        normal_onnx = _write_piper_voice(lv.PIPER_DIR)
        assert tts._ensure_voice_file() == normal_onnx

    def test_falls_through_to_normal_cache_when_no_baked_asset_under_os_mode(self, monkeypatch, tmp_path):
        """OS mode on, FRIDAY_VOICE_ASSETS set but empty (nothing baked for
        this voice) — must fall through to the normal cache, not error."""
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(tmp_path / "empty-baked"))

        tts = lv.PiperTTS()
        assert tts._baked_voice_path() is None

        normal_onnx = _write_piper_voice(lv.PIPER_DIR)
        assert tts._ensure_voice_file() == normal_onnx

    def test_baked_asset_requires_both_onnx_and_json(self, monkeypatch, tmp_path):
        """A half-baked directory (weights with no config, or vice versa)
        must not be treated as a usable baked asset."""
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        baked_dir = tmp_path / "baked-voice"
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(baked_dir))
        baked_dir.mkdir(parents=True)
        (baked_dir / "en_US-amy-medium.onnx").write_bytes(b"weights-only")

        tts = lv.PiperTTS()
        assert tts._baked_voice_path() is None


# ── WhisperASR._download_root ───────────────────────────────────────────────

class TestWhisperBakedAsset:
    def test_baked_whisper_dir_used_under_os_mode(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        baked_dir = tmp_path / "baked-voice"
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(baked_dir))
        whisper_baked = baked_dir / "whisper"
        whisper_baked.mkdir(parents=True)
        (whisper_baked / "model.bin").write_bytes(b"fake-whisper-weights")

        asr = lv.WhisperASR()
        assert asr._download_root() == whisper_baked

    def test_empty_baked_whisper_dir_falls_back_to_normal_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        baked_dir = tmp_path / "baked-voice"
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(baked_dir))
        (baked_dir / "whisper").mkdir(parents=True)  # exists but empty

        asr = lv.WhisperASR()
        assert asr._download_root() == lv.WHISPER_DIR

    def test_missing_baked_whisper_dir_falls_back_to_normal_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(tmp_path / "nonexistent"))

        asr = lv.WhisperASR()
        assert asr._download_root() == lv.WHISPER_DIR

    def test_baked_whisper_dir_ignored_when_os_mode_off(self, monkeypatch, tmp_path):
        """Regression guard: Windows default (OS mode off) always resolves
        to WHISPER_DIR, even with a perfectly good baked model sitting at
        FRIDAY_VOICE_ASSETS."""
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        baked_dir = tmp_path / "baked-voice"
        monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(baked_dir))
        whisper_baked = baked_dir / "whisper"
        whisper_baked.mkdir(parents=True)
        (whisper_baked / "model.bin").write_bytes(b"fake-whisper-weights")

        asr = lv.WhisperASR()
        assert asr._download_root() == lv.WHISPER_DIR
