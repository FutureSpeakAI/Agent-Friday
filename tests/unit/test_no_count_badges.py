"""The desktop shows no unread-count badges: not on the bell, the dock, a
workspace tab or the app icon. What needs the owner reaches the owner through the
Activity chip ("N need you") and the approval card, not a red circle.

The notifications tray holds notifications only. Running tasks, reasoning
traces and background processes are in Activity (the lane panel and the
Activity overlay), where every model task shows its model, status, trace and
queue position.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UI = ["index.html", "ui_parts/app.html"]
STYLE = ["index.html", "ui_parts/head.html", "ui_parts/app.html",
         *[str(p.relative_to(REPO)).replace("\\", "/") for p in sorted((REPO / "static").glob("*.css"))],
         *[str(p.relative_to(REPO)).replace("\\", "/") for p in sorted((REPO / "static").glob("*.js"))]]

# The removed counters, by name. A new badge would bring one of these back.
GONE = ["bellBadgeCount", "bell-badge-pulse", "bellBadgePulse", "wikiPending",
        "msgActionable", "contentAlerts", "setUnseen", "fx-nav-badge",
        "setAppBadge", "ReasoningTraySection"]

# The shape every count badge here had: a small absolutely-placed pill.
PILL = re.compile(r"position:'absolute',top:-?\d+,right:-?\d+,minWidth:1\d,height:1\d,"
                  r"padding:'0\dpx',borderRadius:\d")


def _flat(rel: str) -> str:
    return re.sub(r"\s+", "", (REPO / rel).read_text(encoding="utf-8", errors="replace"))


@pytest.mark.parametrize("rel", STYLE)
def test_no_removed_badge_comes_back(rel):
    text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
    back = [g for g in GONE if g in text]
    assert not back, f"{rel}: count badge code is back: {back}"


@pytest.mark.parametrize("rel", UI)
def test_no_count_pill_is_drawn(rel):
    hits = PILL.findall(_flat(rel))
    assert not hits, f"{rel}: a count pill is drawn ({len(hits)}x): {hits[:2]}"


def _tray(rel: str) -> str:
    flat = _flat(rel)
    i = flat.index("constshellNotifs=notifOpen&&")
    j = flat.index("constshell", i + 10)
    return flat[i:j]


@pytest.mark.parametrize("rel", UI)
def test_the_tray_holds_notifications_only(rel):
    tray = _tray(rel)
    for gone in ("TaskCard", "RUNNINGNOW", "CLEARFINISHED", "FAILED\xb7", "FAILED·",
                 "liveProcs", "failedProcs", "NEW"):
        assert gone not in tray, f"{rel}: the tray still shows {gone}"
    assert "notif-activity-link" in tray, f"{rel}: the tray does not point to Activity"
    assert "setActivityTab('activity')" in tray and "setActivityTab('traces')" in tray
    assert "clearAllNotifs" in tray, f"{rel}: the tray lost Clear all"


@pytest.mark.parametrize("rel", UI)
def test_the_bell_has_no_count(rel):
    flat = _flat(rel)
    assert "aria-label\":'Opennotifications'" in flat or 'aria-label="Opennotifications"' in flat
    assert "tasksrunning,opennotifications" not in flat
