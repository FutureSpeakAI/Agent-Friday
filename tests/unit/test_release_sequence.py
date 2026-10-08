"""Releases are ordered by build sequence, and the places that carry the
sequence agree.

Agent Friday Beta 1.0 (1.0.0b1) is numerically below the 5.x line it replaces.
The ordering lives in `agent_friday.release`; this file holds the rest of the
tree to it: `pyproject.toml`'s version, the installer script's constants, and
the workflow's tag check.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from agent_friday import release  # noqa: E402


def _pyproject_version() -> str:
    # services/app_version.py is the one reader of the version (see test_one_version_source).
    from agent_friday.services.app_version import running_version
    version = running_version(REPO)
    assert version
    return version


def test_beta_one_is_after_5_14_3_although_its_number_is_lower():
    assert release.sequence_for_version("1.0.0b1") > release.sequence_for_version("5.14.3")
    assert release.sequence_for_version("v1.0.0-beta.1") > release.sequence_for_version("v5.14.3")


def test_the_pep440_and_the_tag_spelling_are_the_same_release():
    assert release.sequence_for_version("1.0.0b1") == release.sequence_for_version("v1.0.0-beta.1")


def test_order_within_the_new_line():
    seq = release.sequence_for_version
    chain = ["v1.0.0-beta.1", "v1.0.0-beta.2", "v1.0.0-rc.1", "v1.0.0", "v1.0.1", "v1.1.0-beta.1", "v1.1.0", "v2.0.0-beta.1"]
    values = [seq(v) for v in chain]
    assert values == sorted(values) and len(set(values)) == len(values), dict(zip(chain, values))


def test_order_within_the_5x_line_is_by_number():
    seq = release.sequence_for_version
    assert seq("5.9.0") < seq("5.10.0") < seq("5.14.3")
    assert seq("5.14.3") < release.ERA_FLOOR


def test_a_non_version_has_no_sequence():
    assert release.sequence_for_version("not a version") is None
    assert release.sequence_for_version("") is None
    assert release.sequence_for_version(None) is None


def test_the_notes_line_wins_over_the_tag():
    rel = {"tag_name": "v1.0.0-beta.1", "body": "x\nBuild sequence: 123456789\n"}
    assert release.sequence_of_release(rel) == 123456789
    assert release.sequence_of_release({"tag_name": "v5.14.3", "body": "no line"}) == 51403


def test_build_sequence_is_the_sequence_of_the_pyproject_version():
    """Bump one without the other and this fails."""
    assert release.BUILD_SEQUENCE == release.sequence_for_version(_pyproject_version())


def test_the_release_tag_is_the_pyproject_version_under_its_tag_spelling():
    assert release.sequence_for_version(release.RELEASE_TAG) == release.BUILD_SEQUENCE


def test_the_app_reports_its_release_name_for_this_build(tmp_path):
    from agent_friday.services import app_version as av

    (tmp_path / "pyproject.toml").write_text('version = "%s"\n' % _pyproject_version(), encoding="utf-8")
    assert av.display_version(tmp_path) == release.RELEASE_NAME
    assert av.running_sequence(tmp_path) == release.BUILD_SEQUENCE
    (tmp_path / "pyproject.toml").write_text('version = "5.14.3"\n', encoding="utf-8")
    assert av.display_version(tmp_path) == "5.14.3"
    assert av.running_sequence(tmp_path) == 51403
