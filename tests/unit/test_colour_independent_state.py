"""State is never shown by colour alone (WCAG 2.2 1.4.1; north star NS-33.1-11).

An audit found fifteen places where a state was a coloured dot and nothing else:
the camera light, the data-source dot in every window's title bar, the voice
bar's ear/mind/mouth, the dock's open and notice dots, AI provider health,
connector chips, the model lists, a process thread's steps, and the news tone
and trust marks. Each now has a shape that differs by state and a name a screen
reader says. tests/app/specs/colour_independent_state.spec.ts renders the
shapes with the page's real CSS and proves no two look alike without colour.

Charts (series told apart by colour) are NS-33.1-12, a separate piece.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
APP = ROOT / "ui_parts" / "app.html"
HEAD = ROOT / "ui_parts" / "head.html"


def _t(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", [INDEX, HEAD], ids=lambda p: p.name)
def test_one_shape_per_kind_of_state(page):
    css = _t(page)
    rules = {st: re.search(r'\.fr-state\[data-state="%s"\] \{([^}]*)\}' % st, css) for st in ("ok", "busy", "warn", "bad", "off")}
    assert all(rules.values()), {k: bool(v) for k, v in rules.items()}
    assert "linear-gradient(90deg, currentColor 50%, transparent 50%)" in rules["busy"].group(1)
    assert "rotate(45deg)" in rules["warn"].group(1)
    assert "border-radius:1px" in rules["bad"].group(1)
    assert "background:transparent" in rules["off"].group(1)


@pytest.mark.parametrize("page", [INDEX, HEAD], ids=lambda p: p.name)
def test_the_existing_dots_take_shapes_too(page):
    css = _t(page)
    assert ".thread-dot.pending { background: transparent; border: 2px solid #3b82f6;" in css
    assert ".thread-dot.in-progress { background: linear-gradient(" in css
    assert ".trust-dot.yellow, .trust-obs-dot.yellow { border-radius:1px; transform:rotate(45deg)" in css
    assert ".sentiment-dot.neutral { background:transparent;" in css
    assert ".dock-btn.has-notice .dot { background: transparent; border: 1px solid #00d4ff;" in css
    assert '#camera-indicator:not(.holo-active):not([data-status="live"]) { background: transparent;' in css


def test_the_camera_light_has_a_name_that_follows_it():
    for page in (INDEX, ROOT / "ui_parts" / "styles_and_scene.html"):
        s = _t(page)
        assert '<div id="camera-indicator" role="img" aria-label="Camera off" title="Camera off"></div>' in s, page.name
    s = _t(INDEX)
    assert s.count("'Camera on, following your face' : ind.dataset.status === 'live' ? 'Camera on' : (!ind.dataset.status || ind.dataset.status === 'off') ? 'Camera off' : 'Camera not live'") == 2


@pytest.mark.parametrize("page", [INDEX, APP], ids=lambda p: p.name)
def test_status_marks_carry_a_shape_and_a_name(page):
    s = _t(page)
    connector = s[s.index("function ConnectorDot("):]
    connector = connector[:connector.index("\n}\n")]
    provider = s[s.index("function ProviderStatusDot("):]
    provider = provider[:provider.index("\n}\n")]
    for body in (connector, provider):
        assert "fr-state" in body and "data-state" in body and "aria-label" in body
        assert "background" not in body, "the shape comes from the stylesheet, not an inline colour fill"
    sentiment = s[s.index("function SentimentDot("):]
    assert "aria-label" in sentiment[:sentiment.index("\n}\n")]
    assert "'Trust: high'" in s and "'Good signal'" in s
    assert "'aria-label':({done:'Done','in-progress':'In progress',pending:'Waiting',error:'Failed'})" in s
    assert "\"aria-label\": c.connected ? 'Connected' : 'Not connected'" in s


def test_index_only_marks():
    s = _t(INDEX)
    # The voice bar: a different mark per state, not only a colour.
    assert "const mark = st === 'busy' ? '◐' : st === 'degraded' ? '▲' : st === 'refused' ? '✕' : '●';" in s
    # The model lists: on this PC or in the cloud, by shape and by name.
    assert s.count("'Runs on this PC'") >= 2 and "'data-kind': m.local ? 'local' : 'cloud'" in s
    assert "'aria-label': p.connected ? 'Connected' : 'Not connected'" in s


def test_the_app_suite_runs_the_shape_check():
    assert "colour_independent_state.spec.ts" in _t(ROOT / ".github" / "workflows" / "tests.yml")
