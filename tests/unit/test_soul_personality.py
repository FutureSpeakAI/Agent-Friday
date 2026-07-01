"""Unit tests for the SOUL.md personality service — seeding the default,
mtime-cached load, validated atomic save, version snapshots, reset, rendering
(scaffold stripping), and size limits.

Every test redirects SOUL to a per-test tmp_path so we never touch (or shadow)
the shared test-home's real ~/.friday/SOUL.md.
"""
from __future__ import annotations

import pytest

from agent_friday.services import soul


@pytest.fixture(autouse=True)
def _isolated_soul(tmp_path, monkeypatch):
    monkeypatch.setattr(soul, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(soul, "SOUL_FILE", tmp_path / "SOUL.md")
    monkeypatch.setattr(soul, "HISTORY_DIR", tmp_path / "soul_history")
    monkeypatch.setattr(soul, "_cache", {"text": None, "mtime": 0.0})
    yield


class TestDefaultAndLoad:
    def test_default_soul_nonempty(self):
        assert "Agent Friday" in soul.default_soul()

    def test_ensure_seeds_file(self):
        assert not soul.SOUL_FILE.exists()
        soul.ensure_soul()
        assert soul.SOUL_FILE.exists()

    def test_load_returns_default_when_missing(self):
        assert "Agent Friday" in soul.load_soul()

    def test_load_is_cached(self):
        soul.load_soul()
        # Second read hits the mtime cache — no crash, same content.
        assert soul.load_soul() == soul.load_soul()


class TestSave:
    def test_save_persists_text(self):
        soul.save_soul("# Custom\nYou are a test persona.")
        assert "test persona" in soul.load_soul()

    def test_save_empty_rejected(self):
        assert soul.save_soul("")["ok"] is False
        assert soul.save_soul("   ")["ok"] is False
        assert soul.save_soul(None)["ok"] is False

    def test_save_oversized_rejected(self):
        big = "#" + "x" * (33 * 1024)
        r = soul.save_soul(big)
        assert r["ok"] is False
        assert "too large" in r["error"].lower()

    def test_save_reports_bytes(self):
        r = soul.save_soul("# Persona\nhello")
        assert r["ok"] is True
        assert r["bytes"] > 0


class TestHistory:
    def test_snapshot_created_on_overwrite(self):
        soul.save_soul("# V1\nfirst")
        soul.save_soul("# V2\nsecond")
        # At least one prior version snapshot retained.
        assert len(soul.history()) >= 1

    def test_reset_restores_default(self):
        soul.save_soul("# Custom\nnot the default")
        soul.reset_soul()
        assert soul.load_soul().strip() == soul.default_soul().strip()


class TestRendering:
    def test_render_strips_h1_title(self):
        soul.save_soul("# Agent Title\nActual personality body here.")
        rendered = soul.render_personality()
        assert "# Agent Title" not in rendered
        assert "personality body" in rendered

    def test_render_drops_editor_note(self):
        soul.save_soul(
            "# Title\n*This file defines the persona. Edit it freely.*\n\nReal content follows.")
        rendered = soul.render_personality()
        assert "Edit it freely" not in rendered
        assert "Real content" in rendered

    def test_render_falls_back_to_body(self):
        soul.save_soul("Just plain text, no markdown scaffold.")
        assert "plain text" in soul.render_personality()


class TestState:
    def test_state_shape(self):
        st = soul.state()
        for k in ("path", "exists", "bytes", "mtime", "versions", "is_default"):
            assert k in st

    def test_state_is_default_true_after_reset(self):
        soul.reset_soul()
        assert soul.state()["is_default"] is True

    def test_state_is_default_false_after_custom(self):
        soul.save_soul("# Custom\nsomething else entirely")
        assert soul.state()["is_default"] is False
