"""Unit tests for services/soul.py — SOUL.md personality config."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import soul  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """Point SOUL.md + history at a throwaway dir and clear the mtime cache."""
    monkeypatch.setattr(soul, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(soul, "SOUL_FILE", tmp_path / "SOUL.md")
    monkeypatch.setattr(soul, "HISTORY_DIR", tmp_path / "soul_history")
    monkeypatch.setattr(soul, "_cache", {"text": None, "mtime": 0.0})
    yield


def test_default_soul_is_markdown():
    d = soul.default_soul()
    assert d.startswith("# SOUL.md")
    assert "Agent Friday" in d


def test_ensure_seeds_default():
    p = soul.ensure_soul()
    assert p.exists()
    assert p.read_text(encoding="utf-8").startswith("# SOUL.md")


def test_load_seeds_when_missing():
    text = soul.load_soul()
    assert "Agent Friday" in text
    assert soul.soul_path().exists()


def test_save_and_load_roundtrip():
    res = soul.save_soul("# SOUL.md — Test\n\nYou are terse and kind.")
    assert res["ok"] is True
    assert soul.load_soul().endswith("terse and kind.")


def test_save_rejects_empty():
    assert soul.save_soul("")["ok"] is False
    assert soul.save_soul("   ")["ok"] is False


def test_save_rejects_oversize():
    big = "x" * (33 * 1024)
    assert soul.save_soul(big)["ok"] is False


def test_render_personality_strips_title_and_note():
    soul.save_soul(
        "# SOUL.md — Friday\n"
        "*Edit this file freely; Friday reads it on startup.*\n\n"
        "## Voice\nBe sharp and direct.\n"
    )
    body = soul.render_personality()
    assert "# SOUL.md" not in body
    assert "Edit this file" not in body
    assert "Be sharp and direct." in body


def test_save_snapshots_history():
    soul.save_soul("# SOUL.md\nversion one")
    soul.save_soul("# SOUL.md\nversion two")
    hist = soul.history()
    assert len(hist) >= 1  # the first save snapshotted the seed/first version


def test_reset_restores_default():
    soul.save_soul("# SOUL.md\ncustom")
    soul.reset_soul()
    assert soul.load_soul().strip() == soul.default_soul().strip()


def test_state_reports_fields():
    soul.ensure_soul()
    st = soul.state()
    assert st["exists"] is True
    assert st["bytes"] > 0
    assert "path" in st


def test_mtime_cache_refreshes_on_change():
    soul.save_soul("# SOUL.md\nfirst")
    assert "first" in soul.load_soul()
    soul.save_soul("# SOUL.md\nsecond")
    assert "second" in soul.load_soul()
