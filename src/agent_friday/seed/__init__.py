"""Bundled seed content — installable package data, not a checkout-only path.

Formerly `data/` and `skills/` lived at the REPO ROOT, one level above
`src/`. Two consumers depended on that: `routes/jobs.py` (imported
`data.job_tracker_schema`, `skills.application_engine`, `skills.job_scanner`
directly as top-level packages, via a `sys.path` insert keyed off
`Path(__file__).resolve().parents[3]`) and `skill_registry.BUNDLED_DIR`
(computed the same way, `parents[2]` from its own location). Both resolve
only when the repo root is actually on disk next to an importable
`agent_friday` — true for a source checkout, never true for a `pip install`
wheel or sdist, since `pyproject.toml`'s `[tool.setuptools.packages.find]`
only packages `src/`. KNOWN_ISSUES.md Sec.3 ("Blocking for a packaged
release") tracked this as "the career pipeline cannot work in a pip
install" and deliberately left it unfixed, calling it a product decision
about what the skills system *is* rather than a packaging typo.

This module is that decision, made: bundled skills are first-class
installed package content. `data/job_tracker_schema.py` and
`skills/application_engine/`, `skills/job_scanner/` now live under
`agent_friday/seed/` and ship inside the wheel/sdist like any other
package module — `routes/jobs.py` and `skill_registry.py` import/reference
them from here directly, no sys.path surgery, no repo-root dependency.

`ensure_seed_skills_installed()` is the other half: a first-run step that
copies the bundled SKILL.md-format skills into the user's own
`friday_home() / "skills"` (the directory `agent_friday.skill_registry` and
the Skills UI already treat as the user's editable/hot-reloadable skill
store) so they show up there too, are editable, and survive uninstalling
the package. It is intentionally a straight one-shot copy, never a sync:
once the destination directory exists at all, this is a no-op forever,
so a user's own edits under `~/.friday/skills/<name>/` are never
overwritten or duplicated by a later call (e.g. every `friday` launch).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from agent_friday.paths import friday_home

SEED_DIR = Path(__file__).resolve().parent
SEED_DATA_DIR = SEED_DIR / "data"
SEED_SKILLS_DIR = SEED_DIR / "skills"


def ensure_seed_skills_installed(home: "Path | None" = None) -> Path:
    """Copy the bundled seed skills into ``<friday_home>/skills`` on first run.

    Idempotent by design: if the destination directory already exists —
    whether from a previous call, an earlier install, or the user's own
    skills — this does nothing and never overwrites or duplicates anything.
    Only a completely absent destination gets seeded.

    ``home`` overrides ``friday_home()`` for callers that already resolved
    it (or, in tests, want an isolated temp directory without touching
    ``FRIDAY_HOME``/``HOME`` env vars).

    Returns the destination skills directory (created empty if the bundled
    seed is somehow missing, so callers can always rely on the path
    existing afterward).
    """
    dest_root = (home or friday_home()) / "skills"
    if dest_root.exists():
        return dest_root
    if SEED_SKILLS_DIR.is_dir():
        shutil.copytree(
            SEED_SKILLS_DIR, dest_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    else:  # pragma: no cover — defensive; the seed ships with the package
        dest_root.mkdir(parents=True, exist_ok=True)
    return dest_root
