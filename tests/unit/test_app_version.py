"""The one answer to "what version am I actually running".

WHY THIS EXISTS. `install-manifest.json` claimed 5.6.4 on installs whose files
were 5.6.3, because `Invoke-Step` ran app.copy's -Verify BEFORE the action and
the verify only checked that four files EXISTED. Every 5.6.0..5.6.4 upgrade
copied 0 of 489 files and then wrote the new version number into the manifest.

So the manifest is not evidence. `app\\pyproject.toml` is — it ships in every
payload and it is what the 5.6.5 installer's `Get-InstalledAppVersion` reads
back off disk before recording anything.

An update checker that trusted the manifest would tell a 5.6.3 user they were
current. That is worse than having no checker, because it manufactures
confidence. Hence: ONE function, reading DISK, consumed by both `friday status`
and the update check, and a disagreement between disk and manifest is itself
reportable — it is the fingerprint of a copy that did not take.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agent_friday.services import app_version as av  # noqa: E402


def _make_install(tmp_path, *, disk_version=None, manifest=None):
    """Lay out <root>/app/pyproject.toml + <root>/install-manifest.json.

    Mirrors the real installer layout: PROJ_ROOT is <InstallRoot>\\app and the
    manifest is its SIBLING, not its child.
    """
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    if disk_version is not None:
        app.joinpath("pyproject.toml").write_text(
            '[project]\nname = "agent-friday"\nversion = "%s"\n' % disk_version,
            encoding="utf-8",
        )
    if manifest is not None:
        tmp_path.joinpath("install-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    return app


# ── Disk is the truth ────────────────────────────────────────────────────────

def test_running_version_comes_from_disk_not_the_manifest(tmp_path):
    """The 5.6.5 defect, reproduced: a manifest that lies must not be believed."""
    app = _make_install(
        tmp_path,
        disk_version="5.6.3",                       # what is ACTUALLY on disk
        manifest={"version": "5.6.4", "installer_version": "5.6.4"},
    )
    assert av.running_version(app) == "5.6.3"


def test_disagreement_between_disk_and_manifest_is_reported(tmp_path):
    app = _make_install(
        tmp_path,
        disk_version="5.6.3",
        manifest={"version": "5.6.4", "installer_version": "5.6.4"},
    )
    truth = av.version_truth(app)

    assert truth["running"] == "5.6.3"
    assert truth["manifest"] == "5.6.4"
    assert truth["disagrees"] is True
    # The user has to be TOLD, not just have the field set.
    assert truth["disagreement"]
    assert "5.6.3" in truth["disagreement"] and "5.6.4" in truth["disagreement"]


def test_honest_manifest_recording_a_failed_copy_is_also_a_disagreement(tmp_path):
    """5.6.5+ manifests measure the disk, so a failed copy shows up as
    version != installer_version. That is still a broken install and the user
    still needs to hear about it."""
    app = _make_install(
        tmp_path,
        disk_version="5.6.3",
        manifest={"version": "5.6.3", "installer_version": "5.6.4"},
    )
    truth = av.version_truth(app)

    assert truth["running"] == "5.6.3"
    assert truth["disagrees"] is True
    assert "5.6.4" in truth["disagreement"]


def test_agreeing_install_reports_no_disagreement(tmp_path):
    app = _make_install(
        tmp_path,
        disk_version="5.7.0",
        manifest={"version": "5.7.0", "installer_version": "5.7.0"},
    )
    truth = av.version_truth(app)

    assert truth["running"] == "5.7.0"
    assert truth["disagrees"] is False
    assert truth["disagreement"] is None


def test_git_checkout_with_no_manifest_is_not_a_disagreement(tmp_path):
    """A developer checkout has no manifest. Absence is not a lie."""
    app = _make_install(tmp_path, disk_version="5.7.0", manifest=None)
    truth = av.version_truth(app)

    assert truth["running"] == "5.7.0"
    assert truth["packaged"] is False
    assert truth["disagrees"] is False


def test_unreadable_pyproject_yields_none_not_a_guess(tmp_path):
    """`Get-InstalledAppVersion` returns '' for "cannot tell" and every caller
    treats unknown as "not what we expect". Same discipline here: never guess a
    version, because a guessed version is what the manifest bug WAS."""
    app = _make_install(tmp_path, disk_version=None, manifest={"version": "5.6.4"})
    truth = av.version_truth(app)

    assert truth["running"] is None
    assert av.running_version(app) is None


# ── One implementation, not two ──────────────────────────────────────────────

def test_cli_delegates_to_this_module(monkeypatch):
    """`friday status` and the update check must not drift apart.

    The brief was explicit: do not write a second version-detection
    implementation. This test fails if cli.py grows its own pyproject parser
    again.
    """
    from agent_friday import cli

    sentinel = "9.9.9-from-the-shared-module"
    monkeypatch.setattr(av, "running_version", lambda *a, **k: sentinel)
    assert cli._app_version() == sentinel


def test_cli_manifest_reader_delegates_too(monkeypatch):
    from agent_friday import cli

    sentinel = {"version": "9.9.9", "installer_version": "9.9.9"}
    monkeypatch.setattr(av, "installed_manifest", lambda *a, **k: sentinel)
    assert cli._installed_manifest() == sentinel


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
