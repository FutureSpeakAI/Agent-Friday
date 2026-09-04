"""Gauntlet finding F6: camera_auto_describe was declared in DEFAULT_SETTINGS
and synced into a React state var in both index.html and ui_parts/app.html,
but had no UI control anywhere to ever set it true, and the synced state was
never read back by anything -- a settings key nobody could enable, that
would have done nothing even if they could. Removed rather than built,
mirroring the same "control that does nothing" bias applied to the dead
Scheduler section (Q25) and the Scene setting.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_default_settings_no_longer_declares_camera_auto_describe():
    from agent_friday.core import DEFAULT_SETTINGS
    assert "camera_auto_describe" not in DEFAULT_SETTINGS


def test_no_remaining_references_in_either_html_file():
    for name in ("index.html", "ui_parts/app.html"):
        text = (_REPO_ROOT / name).read_text(encoding="utf-8")
        assert "camera_auto_describe" not in text
        assert "cameraAutoDescribe" not in text
