"""3D rendering can be switched off entirely (north star NS-21.22-3; the shell
stays usable without it, NS-21.2-13; burn-down BD-21: "the 3D-off setting
leaves no WebGL context").

The switch is a setting (render_3d, Settings > Appearance) mirrored to
localStorage so it is known before anything draws. With it off, a script at
the top of the page makes canvas.getContext answer null for WebGL, the scene
is never started, the knowledge view is its flat Pages form, and the Files 3D
engine is never pre-built. tests/app/specs/three_d_off.spec.ts drives it in a
browser and counts the WebGL contexts made (none).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEADS = [ROOT / "index.html", ROOT / "ui_parts" / "head.html"]
APPS = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_setting_exists_and_is_on_by_default():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS.get("render_3d") is True


@pytest.mark.parametrize("page", HEADS, ids=lambda p: p.name)
def test_the_switch_is_read_before_any_script_can_draw(page):
    s = _text(page)
    at = s.index('id="friday-render-3d"')
    first_lib = s.index("<script src=")
    assert at < first_lib, "the 3D switch must run before the libraries load"
    block = s[at:s.index("</script>", at)]
    assert "localStorage.getItem('friday.render3d')==='off'" in block
    assert "HTMLCanvasElement.prototype.getContext" in block
    assert re.search(r"webgl2\?\|experimental-webgl", block), "every WebGL kind answers null"
    assert "data-render3d" in block


def test_the_scene_is_not_started_when_3d_is_off():
    for p in (ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"):
        s = _text(p)
        m = re.search(r"if \(!window\.__FRIDAY_STANDALONE__[^;]*window\.onload = init;", s, flags=re.S)
        assert m and "fridayRender3dOff()" in m.group(0), p.name


@pytest.mark.parametrize("page", APPS, ids=lambda p: p.name)
def test_the_knowledge_view_is_pages_only_when_3d_is_off(page):
    s = _text(page)
    load = s[s.index("function kwLoadView()"):][:300]
    assert "fridayRender3dOff()) return 'pages'" in load
    assert "KW_VIEWS.filter(v => v.id === 'pages' || !(window.fridayRender3dOff" in s
    assert re.search(r"typeof THREE\s*===\s*'undefined'\s*\|\|\s*\(window\.fridayRender3dOff", s), \
        "the galaxy never builds its renderer when 3D is off"


@pytest.mark.parametrize("page", APPS, ids=lambda p: p.name)
def test_settings_has_the_switch_and_the_page_follows_the_saved_setting(page):
    s = _text(page)
    assert '"aria-label": "3D rendering"' in s
    assert "render_3d: v === 'on'" in s and "fridaySyncRender3d(v === 'on')" in s
    assert "fridaySyncRender3d(agentSettings.render_3d)" in s


def test_files_3d_is_never_prebuilt_and_says_why_it_is_flat():
    s = _text(ROOT / "static" / "studio_files3d.js")
    pre = s[s.index("function prewarm()"):][:400]
    assert "fridayRender3dOff()) return;" in pre
    assert "3D is turned off (Settings > Appearance > 3D rendering)." in s


def test_the_app_suite_runs_the_spec_in_ci():
    assert "three_d_off.spec.ts" in _text(ROOT / ".github" / "workflows" / "tests.yml")
