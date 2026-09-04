"""Gauntlet finding Q16: the daily "short-production" creation mode
(services/creations.py's _generate_media_daily, called from the unattended
once-a-day generate_daily_creation() builtin task) ran the full-production
pipeline template with until_checkpoint=False, auto-advancing through all
three of the template's own checkpoints -- including the one commented
"human reviews the look before any spend", the one commented "cost gate --
video is the expensive call", and the one commented "final review before
the work is published". It was the ONLY caller of this template that ran
unattended with no human ever reviewing before the expensive stage or
before publishing to the user's Desktop.

Stephen: "fix it... a checkpoint that exists and is skipped is the placebo
pattern in a different costume... it should do what it says" (2026-09-04,
git history checked first per his own instruction -- no deliberate reason
for the bypass was found: the line was simply how this mode was originally
written, not a considered later decision).

This probe proves the daily short-production path now stops at its first
checkpoint instead of auto-advancing through all three, records that day
as handled (not falling through to an unrelated text creation, and not
starting a second production tomorrow while this one waits), and fires a
review-needed notification -- rather than silently spending through to a
published file with nobody having looked at it.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import agent_friday.services.creations as creations
import agent_friday.services.creative_pipeline as cp


class TestDailyShortProductionHonorsCheckpoints:
    def test_run_is_called_with_until_checkpoint_true(self, monkeypatch, tmp_path):
        captured = {}

        def _fake_create_run(pipeline_id, initial_input):
            return {"run_id": "run-test123", "status": "ok"}

        def _fake_run(run_id, *, until_checkpoint):
            captured["until_checkpoint"] = until_checkpoint
            return {"run_id": run_id, "state": cp.AWAITING_CHECKPOINT, "context": {}}

        monkeypatch.setattr(cp, "create_run", _fake_create_run)
        monkeypatch.setattr(cp, "run", _fake_run)
        monkeypatch.setattr(creations, "_notif_engine", MagicMock())

        path = tmp_path / "2026-09-04.json"
        creations._generate_media_daily(
            "2026-09-04", {"mode": "short-production", "concept": "a test logline"}, path)

        assert captured.get("until_checkpoint") is True, (
            "the daily short-production path still auto-advances through "
            "its own pipeline's checkpoints (Q16 regressed) -- it must stop "
            "at the first one like every other caller of this template"
        )

    def test_a_paused_run_is_recorded_as_pending_not_dropped_or_faked_complete(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(cp, "create_run",
                            lambda pipeline_id, initial_input: {"run_id": "run-abc999"})
        monkeypatch.setattr(cp, "run", lambda run_id, *, until_checkpoint: {
            "run_id": run_id, "state": cp.AWAITING_CHECKPOINT, "context": {}})
        monkeypatch.setattr(creations, "_notif_engine", MagicMock())

        path = tmp_path / "2026-09-04.json"
        result = creations._generate_media_daily(
            "2026-09-04",
            {"mode": "short-production", "concept": "a test logline",
             "title": "Test Title"},
            path)

        assert result is not None, (
            "a checkpoint pause must not look like total failure -- it "
            "should return a real pending record, not None (which would "
            "make generate_daily_creation() fall through to an unrelated "
            "text creation on the same day)"
        )
        assert result.get("pending_review") is True
        assert result.get("run_id") == "run-abc999"
        assert path.exists(), "today's record must be written so tomorrow's tick doesn't start a second production"
        import json
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk.get("pending_review") is True
        assert on_disk.get("file") is None, (
            "a pending record must not claim a finished file exists"
        )

    def test_a_paused_run_fires_a_review_needed_notification(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cp, "create_run",
                            lambda pipeline_id, initial_input: {"run_id": "run-notify1"})
        monkeypatch.setattr(cp, "run", lambda run_id, *, until_checkpoint: {
            "run_id": run_id, "state": cp.AWAITING_CHECKPOINT, "context": {}})
        fake_notif = MagicMock()
        monkeypatch.setattr(creations, "_notif_engine", fake_notif)

        creations._generate_media_daily(
            "2026-09-04",
            {"mode": "short-production", "concept": "a test logline"},
            tmp_path / "2026-09-04.json")

        assert fake_notif.push.called, (
            "a checkpoint-paused daily production must notify the user it "
            "needs review -- silently waiting is no better than silently "
            "publishing unreviewed"
        )
        _, kwargs = fake_notif.push.call_args
        assert "run-notify1" in str(kwargs)

    def test_a_completed_run_is_still_recorded_normally(self, monkeypatch, tmp_path):
        """Falsifiability / grounding check: a run that actually finishes
        (no checkpoint pending) must still be recorded as a real, complete
        creation -- this fix must not turn every short-production run into
        a permanent pending state."""
        monkeypatch.setattr(cp, "create_run",
                            lambda pipeline_id, initial_input: {"run_id": "run-done1"})
        monkeypatch.setattr(cp, "run", lambda run_id, *, until_checkpoint: {
            "run_id": run_id, "state": cp.COMPLETED,
            "context": {"production_file": {"filename": "clip.mp4", "url": "/x/clip.mp4"}}})
        monkeypatch.setattr(creations, "_notif_engine", MagicMock())

        path = tmp_path / "2026-09-04.json"
        result = creations._generate_media_daily(
            "2026-09-04",
            {"mode": "short-production", "concept": "a test logline"},
            path)

        assert result is not None
        assert not result.get("pending_review")
        assert result.get("file") == "clip.mp4"
