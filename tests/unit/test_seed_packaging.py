"""PR-3 (packaging, OS-mode sequence) — tests for agent_friday.seed.

KNOWN_ISSUES.md Sec.3, "Blocking for a packaged release": `data/` and
`skills/` used to be repo-root-only directories that `routes/jobs.py` and
`skill_registry.BUNDLED_DIR` reached with sys.path surgery keyed off
`Path(__file__).resolve().parents[N]`. That resolves in a source checkout and
never in a `pip install` wheel/sdist, because `pyproject.toml`'s
`packages.find` only ships `src/`.

These tests pin two things that must never regress:
  1. The bundled seed content is discoverable as real INSTALLED package data —
     via `importlib.resources`, not a hardcoded relative path that only
     happens to work from a git checkout. This is the actual thing
     KNOWN_ISSUES.md was worried about; a test that imports
     `agent_friday.seed.data.job_tracker_schema` directly would pass in this
     checkout even if the packaging metadata were wrong, because editable/
     checkout imports don't go through the wheel's file manifest at all.
  2. `ensure_seed_skills_installed()` copies into an absent destination and is
     a true no-op (never overwrites, never duplicates) once the destination
     already exists — required because "when absent" implies a check, and
     `friday`/`friday setup` call this on every launch.

See docs/audits/ (packaging PR) for the real non-editable-install proof this
unit suite can't reproduce (a fresh venv + wheel build is out of scope for
the fast unit suite; it's demonstrated separately and captured in the PR
description).
"""
from __future__ import annotations

import importlib.resources as ires
from pathlib import Path

import pytest

from agent_friday import seed


# ── Seed content is real, installed package data ────────────────────────────

def test_seed_data_package_resolves_via_importlib_resources():
    """agent_friday.seed.data must be reachable the way a wheel's file
    manifest actually reaches it - importlib.resources.files() - not merely
    a relative path from this test file (which would pass identically
    whether or not the package-data declaration in pyproject.toml is even
    correct, since a checkout doesn't go through it)."""
    data_dir = ires.files("agent_friday.seed.data")
    assert data_dir.is_dir()
    assert (data_dir / "job_tracker_schema.py").is_file()


def test_seed_skills_package_resolves_via_importlib_resources():
    skills_dir = ires.files("agent_friday.seed.skills")
    assert skills_dir.is_dir()
    for name in ("application_engine", "job_scanner"):
        skill_dir = skills_dir / name
        assert skill_dir.is_dir(), f"missing bundled skill: {name}"
        assert (skill_dir / "SKILL.md").is_file(), f"{name} missing SKILL.md"
        assert (skill_dir / "config.yaml").is_file(), f"{name} missing config.yaml"


def test_seed_skills_are_importable_as_agent_friday_submodules():
    """The whole point of the move: routes/jobs.py imports these as plain
    agent_friday.seed.* submodules now, no sys.path insert, no repo-root
    dependency. If this import chain breaks, the career pipeline is dead in
    every install mode, source or packaged."""
    from agent_friday.seed.data.job_tracker_schema import JobTracker  # noqa: F401
    from agent_friday.seed.skills.application_engine import engine  # noqa: F401
    from agent_friday.seed.skills.job_scanner import scanner  # noqa: F401


def test_seed_module_constants_point_at_real_directories():
    assert seed.SEED_DATA_DIR.is_dir()
    assert seed.SEED_SKILLS_DIR.is_dir()
    assert (seed.SEED_DATA_DIR / "job_tracker_schema.py").is_file()
    assert (seed.SEED_SKILLS_DIR / "application_engine" / "SKILL.md").is_file()
    assert (seed.SEED_SKILLS_DIR / "job_scanner" / "SKILL.md").is_file()


# ── ensure_seed_skills_installed() — first-run copy ─────────────────────────

def test_ensure_seed_skills_installed_copies_into_absent_destination(tmp_path):
    dest_root = seed.ensure_seed_skills_installed(home=tmp_path)

    assert dest_root == tmp_path / "skills"
    assert dest_root.is_dir()
    assert (dest_root / "application_engine" / "SKILL.md").is_file()
    assert (dest_root / "application_engine" / "engine.py").is_file()
    assert (dest_root / "job_scanner" / "SKILL.md").is_file()
    assert (dest_root / "job_scanner" / "scanner.py").is_file()


def test_ensure_seed_skills_installed_is_idempotent_and_never_overwrites(tmp_path):
    """Second call must not touch, duplicate, or re-copy anything — a user's
    own edits under ~/.friday/skills/<name>/ must survive every later
    `friday` launch untouched."""
    first = seed.ensure_seed_skills_installed(home=tmp_path)

    marker = first / "application_engine" / "SKILL.md"
    original_bytes = marker.read_bytes()
    marker.write_text("user-edited content — must not be reverted", encoding="utf-8")

    # A brand-new file the user dropped in, to prove the whole directory is
    # left alone rather than merged/re-synced.
    (first / "a-users-own-skill.yaml").write_text("name: mine\n", encoding="utf-8")

    second = seed.ensure_seed_skills_installed(home=tmp_path)

    assert second == first
    assert marker.read_text(encoding="utf-8") == "user-edited content — must not be reverted"
    assert marker.read_bytes() != original_bytes  # confirms the edit really wasn't reverted
    assert (first / "a-users-own-skill.yaml").exists()


def test_ensure_seed_skills_installed_does_not_touch_pre_existing_empty_dir(tmp_path):
    """A destination that exists but is empty (e.g. a user deleted its
    contents, or an installer pre-created it) is still "already exists" —
    the check is presence, not contents — so nothing gets copied into it."""
    dest = tmp_path / "skills"
    dest.mkdir()

    result = seed.ensure_seed_skills_installed(home=tmp_path)

    assert result == dest
    assert list(dest.iterdir()) == []


def test_ensure_seed_skills_installed_honors_friday_home_env_var(tmp_path, monkeypatch):
    """With no explicit `home` argument, this must resolve through
    agent_friday.paths.friday_home() (FRIDAY_HOME env var support from PR-1),
    the same as every other first-run path in cli.py/setup_wizard.py."""
    custom = tmp_path / "custom-friday-home"
    monkeypatch.setenv("FRIDAY_HOME", str(custom))

    dest_root = seed.ensure_seed_skills_installed()

    assert dest_root == custom / "skills"
    assert (dest_root / "job_scanner" / "SKILL.md").is_file()
