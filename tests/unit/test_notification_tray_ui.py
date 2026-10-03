"""Both copies of the UI show the tray as designed: approvals survive "Clear
all" and have no mute button, repeats show their count, FYI never bumps the
badge, and muted kinds are listed in Settings to unmute."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("ui", ["index.html", "ui_parts/app.html"])
def test_the_tray_ui(ui):
    text = (REPO / ui).read_text(encoding="utf-8")
    flat = re.sub(r"\s+", "", text)
    assert "/api/notifications/clear-all" in text, f"{ui}: Clear all does not use the route that keeps approvals"
    assert "n.kind==='approval_pending')" in flat, f"{ui}: Clear all drops approvals from the panel"
    assert "notif-mute" in text and "n.kind!=='approval_pending'&&" in flat, (
        f"{ui}: the mute button is missing or offered on approvals")
    assert "notif-count" in text and "n.count>1" in flat, f"{ui}: repeats do not show their count"
    assert "!n.read&&n.tier!=='fyi'" in flat, f"{ui}: FYI cards bump the badge"
    assert "functionSettingsNotificationMutes()" in flat and "createElement(SettingsNotificationMutes" in flat, (
        f"{ui}: muted kinds are not listed in Settings")
    # The old per-item dismiss loop that cleared approvals is gone.
    assert "notifs.forEach(n=>{if(n.id){dismissedNotifIds.current.add(n.id);_notifPost('/api/notifications/dismiss'" not in flat
