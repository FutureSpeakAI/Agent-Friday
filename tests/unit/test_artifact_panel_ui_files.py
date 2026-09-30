"""The artifact panel's UI surface, pinned in the files that ship it.

`index.html` is served; `ui_parts/app.html` is its mirror; the panel itself
lives in `static/friday_artifacts.js`. These tests read the files, so a
missing half of the two-file edit, or a frame that stopped being a sandbox,
fails before anyone opens a browser.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
MIRROR = ROOT / "ui_parts" / "app.html"
STYLES = ROOT / "ui_parts" / "styles_and_scene.html"
PANEL_JS = ROOT / "static" / "friday_artifacts.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_panel_script_is_loaded_by_the_page_and_its_mirror():
    tag = '<script src="/static/friday_artifacts.js"></script>'
    assert tag in _read(INDEX)
    assert tag in _read(STYLES), "ui_parts keeps its script tags in styles_and_scene.html"


def test_chat_surface_renders_through_the_artifact_shell_in_both_files():
    for p in (INDEX, MIRROR):
        s = _read(p)
        assert "function FridayChatShell(" in s, p.name
        body = s[s.index("function ChatSurface("):]
        body = body[:body.index("\nfunction ", 10)]
        assert "FridayChatShell" in body, f"{p.name}: ChatSurface does not use the shell"


def test_the_shell_falls_back_to_a_fragment_when_the_script_is_absent():
    s = _read(INDEX)
    fn = s[s.index("function FridayChatShell("):]
    fn = fn[:fn.index("\n}", 10) + 2]
    assert "window.FridayArtifactHost" in fn
    assert "React.Fragment" in fn


def test_the_panel_toggle_exists_in_settings_in_both_files():
    for p in (INDEX, MIRROR):
        assert "artifact_panel_enabled" in _read(p), p.name


@pytest.fixture
def js():
    assert PANEL_JS.exists(), "static/friday_artifacts.js is missing"
    return _read(PANEL_JS)


def test_the_frame_is_an_opaque_origin_sandbox(js):
    m = re.search(r"SANDBOX\s*=\s*['\"]([^'\"]*)['\"]", js)
    assert m, "the frame's sandbox attribute must be one named constant"
    assert "allow-scripts" in m.group(1)
    assert "allow-same-origin" not in m.group(1)
    assert "allow-top-navigation" not in m.group(1)
    assert "allow-popups" not in m.group(1)


def test_the_frame_csp_is_strict(js):
    m = re.search(r"FRAME_CSP\s*=\s*(\[[^\]]*\]|['\"][^'\"]*['\"])", js, re.S)
    assert m, "the frame CSP must be one named constant"
    host = re.search(r"PACKAGE_HOST\s*=\s*['\"]([^'\"]+)['\"]", js)
    assert host, "the package host is one named constant"
    csp = m.group(1).replace("PACKAGE_HOST", host.group(1))
    assert "default-src 'none'" in csp
    assert "form-action 'none'" in csp
    assert "https://esm.sh" in csp, "the one pinned package host"
    assert "unpkg" not in csp and "jsdelivr" not in csp
    # The frame never talks to Friday. Only the package host may be fetched.
    connect = re.search(r"connect-src([^;'\"]*(?:'[^']*'[^;'\"]*)*)", csp)
    assert connect and "127.0.0.1" not in connect.group(1) and "localhost" not in connect.group(1)


def test_the_panel_defines_its_globals(js):
    for name in ("window.FridayArtifactHost", "window.FridayArtifactPanel",
                 "window.fridayArtifactFrameDoc"):
        assert name in js, name


def test_every_kind_has_a_renderer(js):
    for kind in ("markdown", "table", "chart", "html", "diff", "image", "svg"):
        assert re.search(r"kind === '%s'|case '%s'|%s:" % (kind, kind, kind), js), kind


def test_the_page_shares_one_event_stream_across_every_chat_surface(js):
    """A browser allows about six connections to one host and Friday's page
    already holds several open; one stream per chat surface queued ordinary
    requests behind them (15 s for one fetch on the desktop page)."""
    assert js.count("new EventSource(") == 1
    assert "busSubscribe" in js


CHART_JS = ROOT / "static" / "friday_chart.js"


def test_the_chart_renderer_is_one_shared_file_loaded_before_the_panel():
    """The panel draws charts with it live and a published chart page carries
    it inlined, so the two can never drift."""
    assert CHART_JS.exists()
    cj = _read(CHART_JS)
    assert "window.FridayChart" in cj or "root.FridayChart" in cj
    assert "React" not in cj and "document." not in cj, "the renderer depends on nothing"
    for p in (INDEX, STYLES):
        s = _read(p)
        assert s.index('/static/friday_chart.js') < s.index('/static/friday_artifacts.js'), p.name
    assert "FridayChart.renderSVG" in _read(PANEL_JS)


def test_the_svg_frame_has_no_scripts(js):
    m = re.search(r"SVG_SANDBOX\s*=\s*['\"]([^'\"]*)['\"]", js)
    assert m, "svg is framed with its own sandbox constant"
    assert "allow-scripts" not in m.group(1)
