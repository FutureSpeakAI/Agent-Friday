"""Decoration never carries a status colour.

The four reserved hues (ok, warn, deny, error) mean something. A category
accent, an account badge, a lane dot or a state that is not a status must not
borrow one, because the reader will read the meaning instead of the label.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI_SOURCES = ("index.html", "ui_parts/head.html", "ui_parts/app.html",
              "ui_parts/styles_and_scene.html")
STATUS_TOKENS = ("--fr-ok", "--fr-warn", "--fr-deny", "--fr-error")
#: Off-brand values earlier surfaces spelled for themselves.
OFF_BRAND = ("#7c3aed", "124,58,237", "#ff4466", "#ffae5b", "255,174,91", "#e0e0ff", "#0a0a0f")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def brand():
    return _load(ROOT / "src" / "agent_friday" / "brand.py", "_brand_dec")


@pytest.fixture(scope="module")
def guard():
    return _load(ROOT / "scripts" / "check_brand_tokens.py", "_guard_dec")


def _status(brand):
    return {brand.OK, brand.WARN, brand.DENY, brand.ERROR}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_account_palette_holds_no_status_hue(brand):
    assert not set(brand.ACCOUNT_PALETTE) & _status(brand)
    assert len(set(brand.ACCOUNT_PALETTE)) == len(brand.ACCOUNT_PALETTE) >= 6


def test_live_microphone_is_not_a_failure(brand):
    from agent_friday.services import ptt_indicator as p
    assert p._COLOR["recording"] not in _status(brand)
    assert p._COLOR["recording"] != p._COLOR["error"]


def test_priority_colours_do_not_paint_an_alert_as_a_failure(brand):
    from agent_friday import notifications_engine as n
    vals = n.PRIORITY_COLORS
    assert brand.ERROR not in vals.values() and brand.DENY not in vals.values()
    assert vals["critical"] == brand.WARN      # needs you, nothing failed
    assert len(set(vals.values())) == 4


def test_a_connector_that_needs_setup_needs_you(brand):
    from agent_friday.services import connectors as c
    assert c._STATUS_COLORS["needs_setup"] == brand.WARN


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/head.html"))
def test_category_and_lane_properties_never_point_at_status(rel):
    text = _read(rel)
    hits = re.findall(r"(--(?:c|lane|cat)-[\w-]+)\s*:\s*([^;}]+)", text)
    assert hits, "expected category / lane custom properties"
    for name, value in hits:
        assert not any(t in value for t in STATUS_TOKENS), (rel, name, value)
        assert not re.search(r"#(?:00ff80|f59e0b|ff0080|ef4444|22c55e)\b", value, re.I), (rel, name, value)


@pytest.mark.parametrize("rel", UI_SOURCES + ("static/js/friday_push_to_transcribe.js",
                                              "src/agent_friday/core/__init__.py",
                                              "src/agent_friday/routes/creations.py",
                                              "src/agent_friday/services/misc_engine.py"))
def test_no_off_brand_value_survives(rel):
    text = _read(rel).lower()
    found = [v for v in OFF_BRAND if v in text]
    assert not found, (rel, found)


def test_green_that_is_not_ok_is_gone_from_the_ui():
    for rel in UI_SOURCES + ("static/js/friday_push_to_transcribe.js",):
        assert "22c55e" not in _read(rel).lower(), rel


def test_login_page_speaks_brand_and_types_plainly():
    from agent_friday import brand as b
    from agent_friday.core import LOGIN_HTML
    assert b.css_root_block() in LOGIN_HTML
    assert "--fr-cyan" in LOGIN_HTML and "var(--fr-deny)" in LOGIN_HTML
    field = re.search(r"input\[type=email\][^{]*\{([^}]*)\}", LOGIN_HTML).group(1)
    button = re.search(r"(?m)^button\{([^}]*)\}", LOGIN_HTML).group(1)
    assert "letter-spacing" not in field, "typed text takes no micro-label tracking"
    assert "text-transform" not in field
    assert "var(--fr-font-body)" in field and "var(--fr-font-body)" in button


def test_draft_page_gradient_is_the_triad():
    from agent_friday import brand as b
    text = _read("src/agent_friday/services/misc_engine.py")
    assert "brand.TRIAD" in text or all(h in text for h in ("brand.CYAN", "brand.VIOLET", "brand.MAGENTA"))
    assert "#7c3aed" not in text.lower() and "#ff0080" not in text.lower()
    assert b.TRIAD[1] == "#7b61ff"


# -- the guard enforces it ---------------------------------------------------------
def _stage(dst: Path) -> None:
    for rel in (*UI_SOURCES, "static/live/friday_live.html",
                "static/js/friday_push_to_transcribe.js",
                "src/agent_friday/brand.py", "src/agent_friday/core/__init__.py",
                "src/agent_friday/routes/creations.py",
                "src/agent_friday/notifications_engine.py",
                "src/agent_friday/services/connectors.py",
                "src/agent_friday/services/ptt_indicator.py",
                "src/agent_friday/services/google_accounts.py",
                "src/agent_friday/services/showcase_engine.py",
                "src/agent_friday/services/misc_engine.py"):
        src = ROOT / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst / rel)


def test_guard_passes_on_the_repository(guard):
    assert guard.find_problems(ROOT) == []


def test_guard_flags_a_category_accent_that_points_at_status(guard, tmp_path):
    _stage(tmp_path)
    p = tmp_path / "index.html"
    p.write_text(p.read_text(encoding="utf-8") + "\n<style>.x{--c-sports:var(--fr-warn)}</style>\n",
                 encoding="utf-8")
    assert any("--c-sports" in x and "status" in x for x in guard.find_problems(tmp_path))


def test_guard_flags_a_status_hue_in_the_account_palette(guard, tmp_path):
    _stage(tmp_path)
    p = tmp_path / "src" / "agent_friday" / "brand.py"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "ACCOUNT_PALETTE = (CYAN,", "ACCOUNT_PALETTE = (OK, CYAN,"), encoding="utf-8")
    assert any("ACCOUNT_PALETTE" in x and "status" in x for x in guard.find_problems(tmp_path))


def test_guard_flags_an_off_brand_value(guard, tmp_path):
    _stage(tmp_path)
    p = tmp_path / "src" / "agent_friday" / "core" / "__init__.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n_X = '#7c3aed'\n", encoding="utf-8")
    assert any("#7c3aed" in x for x in guard.find_problems(tmp_path))
