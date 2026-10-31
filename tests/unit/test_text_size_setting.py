"""An in-app text size (north star NS-33.1-8 "scalable text"; WCAG 1.4.4;
burn-down BD-21 "an in-app text-size setting with a zoom test").

Settings > Appearance > Text size (100% to 200%) saves text_scale. The page
applies it as one CSS variable that window contents, the chat transcript and
the chat box read, so what a person reads grows and reflows inside its window
while frames, the dock and the scene keep their size.
tests/app/specs/text_size.spec.ts sets 200% and checks that the text really is
twice the size and that windows do not overflow sideways.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPS = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]
HEADS = [ROOT / "index.html", ROOT / "ui_parts" / "head.html"]


def test_the_setting_exists_and_starts_at_100():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS.get("text_scale") == 100


@pytest.mark.parametrize("page", HEADS, ids=lambda p: p.name)
def test_what_you_read_scales_by_one_variable(page):
    css = page.read_text(encoding="utf-8")
    assert re.search(r"\.fwin-body, \.ws-tab-body, \.fx-chat-transcript, \[data-chat-input\] \{ zoom: var\(--fr-text-zoom, 1\); \}", css)
    # Zoom multiplies: a chat inside a window is scaled once, by the window.
    assert ":is(.fwin-body, .ws-tab-body) :is(.fx-chat-transcript, [data-chat-input]) { zoom: 1; }" in css


@pytest.mark.parametrize("page", APPS, ids=lambda p: p.name)
def test_settings_offers_100_to_200_and_the_page_applies_the_saved_size(page):
    s = page.read_text(encoding="utf-8")
    assert "const FRIDAY_TEXT_SCALES = [100, 125, 150, 175, 200];" in s
    assert '"aria-label": "Text size"' in s and "text_scale: v" in s
    assert "fridayApplyTextScale(agentSettings.text_scale)" in s
    assert "setProperty('--fr-text-zoom'" in s
    # An unknown value never scales the page by something nobody chose.
    assert "return FRIDAY_TEXT_SCALES.includes(n) ? n : 100;" in s


def test_the_app_suite_runs_the_zoom_test():
    assert "text_size.spec.ts" in (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")


def test_a_calendar_drag_follows_the_pointer_at_any_text_size():
    """Pointer moves come in screen pixels; at 200% one grid pixel is two of them.
    Without the division an event dragged at 200% moved twice as far as the pointer."""
    js = (ROOT / "static" / "calendar_views.js").read_text(encoding="utf-8")
    assert "const z = el.currentCSSZoom || 1;" in js
    assert "minutes = Math.trunc(dy / z / stepPx) * STEP_MIN;" in js
    assert "shiftX = (hit.getBoundingClientRect().left - col0.getBoundingClientRect().left) / z;" in js