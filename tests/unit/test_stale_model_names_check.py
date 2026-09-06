"""Unit test for scripts/check_stale_model_names.py -- the static guard
against a user-facing doc naming a model retired from the brain ladder.

WHY THIS FILE EXISTS, NOT JUST THE TWO PROSE FIXES: this is what makes the
guard durable. The two fixes it was written alongside (KNOWN_ISSUES.md's
tool-calling example, Test-Installer.ps1's test fixture) are gone now --
but a checker that only ever ran once, by hand, protects nothing going
forward. This is the test that fails the next time a model family is
retired from `model_plan.BRAIN_MODELS` and a doc still names it.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import check_stale_model_names as checker  # noqa: E402


def test_real_tree_is_clean():
    assert checker.find_problems() == []


def test_catches_a_retired_model_named_in_a_checked_file(tmp_path, monkeypatch):
    doc = tmp_path / "README.md"
    doc.write_text("Friday's default local model is qwen3:8b.", encoding="utf-8")
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    problems = checker.find_problems()
    assert len(problems) == 1
    assert "qwen3:8b" in problems[0]


def test_does_not_flag_a_current_embedding_or_image_model():
    """The exact false-positive this check is designed to avoid: these are
    real, current, separate parts of the product, not the reasoning ladder
    this check is about."""
    banned_lower = [t.lower() for t in checker.BANNED_BRAIN_TOKENS]
    assert "qwen3-embedding:0.6b" not in banned_lower
    assert "qwen-image-q3ks" not in banned_lower


def test_ignores_files_outside_the_checked_list(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    audit = tmp_path / "docs" / "some-audit.md"
    audit.write_text("Historical record: qwen3:8b was the old default.",
                     encoding="utf-8")
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    assert checker.find_problems() == []
