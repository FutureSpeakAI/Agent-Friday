"""The Media workspace is wired into the served page and its mirror, and keeps
the rules the spec sets (docs/design/active/media-workspace.md §2).

The workspace lives in static/media_ws.js as a plain script, the Files 3D
precedent, so index.html and ui_parts/app.html each carry one mapping line.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "media_ws.js").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
SCENE = (ROOT / "ui_parts" / "styles_and_scene.html").read_text(encoding="utf-8")
REGISTRY = (ROOT / "static" / "workspace_registry.js").read_text(encoding="utf-8")


def test_the_script_is_loaded_by_the_page_and_its_mirror():
    tag = '<script src="/static/media_ws.js"></script>'
    assert INDEX.count(tag) == 1
    assert SCENE.count(tag) == 1


def test_the_workspace_is_mapped_in_the_page_and_its_mirror():
    assert "media: window.MediaWS ? /*#__PURE__*/React.createElement(window.MediaWS, null)" in INDEX
    assert "media:window.MediaWS?<window.MediaWS/>" in APP


def test_the_registry_declares_media_as_a_core_work_workspace():
    m = re.search(r'\{"id": "media",[^\n]*', REGISTRY)
    assert m, "media is in the registry"
    assert '"group": "work"' in m.group(0) and '"core": true' in m.group(0)
    assert (ROOT / "assets" / "icons" / "media.svg").exists()


def test_the_script_defines_the_workspace_and_its_views():
    for name in ("window.MediaWS = MediaWS", "window.MediaBoard = MediaBoard", "window.MediaCalendar = MediaCalendar", "window.MediaCard = MediaCard"):
        assert name in JS
    assert "push(['media', {" in JS, "the views are declared for navigation"


def test_the_five_statuses_are_the_one_vocabulary():
    m = re.search(r"const STATUSES = \[\s*(.*?)\];", JS, re.S)
    assert m
    ids = re.findall(r"\['(\w+)', '[^']+'\]", m.group(1))
    assert ids == ["idea", "draft", "review", "scheduled", "published"]


def test_the_root_fills_the_frame_and_sets_no_width_of_its_own():
    assert "className: 'md-root ws-fill'" in JS
    root_rule = re.search(r"\.md-root\{([^}]*)\}", JS).group(1)
    assert not re.search(r"(^|;)(max-)?width:", root_rule), "the root sets no width; the shell owns the frame"
    assert "100vh" not in JS, "a workspace never measures the viewport; the shell pads for the bar and the tray"


def test_amber_only_means_needs_you():
    css = "\n".join(re.findall(r"const \w*CSS = `(.*?)`;", JS, re.S))
    for line in css.splitlines():
        if "f59e0b" in line or "--fr-warn" in line or "245,158,11" in line:
            assert "needs-you" in line or ".md-ev.review" in line, line
    # Hex literals: only the stage's black; every other colour is a token or a token's rgba.
    assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)) <= {"#000"}


def test_publishing_never_happens_in_the_page():
    """Every move into Published is a server call; the page never sets the status itself."""
    assert "status: 'published' }" not in JS
    assert "/publish'" in JS


def test_a_selected_segment_is_the_brand_control():
    assert "className: 'btn' + (view === v[0] && !card ? ' active' : '')" in JS
    assert "'aria-pressed': view === v[0] && !card" in JS
