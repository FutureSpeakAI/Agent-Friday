"""Every surface that shows a state reads the same brand colours.

The in-page push-to-talk card, the account badges and the sign-in button are
checked against src/agent_friday/brand.py, the one place the palette lives.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PTT_JS = "static/js/friday_push_to_transcribe.js"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def brand():
    return _load(ROOT / "src" / "agent_friday" / "brand.py", "_brand_surf")


@pytest.fixture(scope="module")
def guard():
    return _load(ROOT / "scripts" / "check_brand_tokens.py", "_guard_surf")


def _js() -> str:
    return (ROOT / PTT_JS).read_text(encoding="utf-8")


def _card_colours() -> dict:
    block = re.search(r"var colour = \{(.*?)\}\[state\]", _js(), re.S).group(1)
    return dict(re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", block))


def test_in_page_card_shows_a_live_microphone_in_cyan_not_error_red(brand):
    colours = _card_colours()
    assert colours["recording"].lower() == brand.CYAN
    assert colours["recording"].lower() != colours["error"].lower()
    assert colours["error"].lower() == brand.ERROR
    assert colours["done"].lower() == brand.OK


def test_in_page_card_level_meter_is_not_a_failure_colour(brand):
    css = re.search(r"bar\.style\.cssText = '([^']*)'", _js()).group(1)
    assert brand.ERROR not in css.lower()
    assert brand.CYAN in css.lower()


def test_every_card_colour_is_a_brand_colour(brand):
    known = {v.lower() for v in vars(brand).values() if isinstance(v, str) and v.startswith("#")}
    for state, hue in _card_colours().items():
        assert hue.lower() in known, f"{state} uses {hue}, which brand.py does not define"


def test_guard_flags_a_red_microphone_in_the_page_card(guard, tmp_path):
    for rel in ("src/agent_friday/brand.py", PTT_JS):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    assert not [p for p in guard.find_problems(tmp_path) if "friday_push_to_transcribe" in p]
    js = tmp_path / PTT_JS
    js.write_text(js.read_text(encoding="utf-8").replace("recording: '#00d4ff'", "recording: '#ef4444'"),
                  encoding="utf-8")
    assert any("friday_push_to_transcribe" in p and "recording" in p
               for p in guard.find_problems(tmp_path))


def _distance(a: str, b: str) -> float:
    ra = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    rb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5


def test_account_hues_are_told_apart_at_a_glance(brand):
    palette = brand.ACCOUNT_PALETTE
    for i, a in enumerate(palette):
        for b in palette[i + 1:]:
            assert _distance(a, b) >= 60, f"{a} and {b} are too close to tell apart"


def test_connected_accounts_saved_with_a_status_hue_are_recoloured_on_read(brand):
    from agent_friday.services import google_accounts as g
    for legacy in ("#22c55e", "#00ff80", "#f59e0b", "#ef4444"):
        shown = g._account_color({"color": legacy})
        assert shown in brand.ACCOUNT_PALETTE
        assert shown not in (brand.OK, brand.WARN, brand.DENY, brand.ERROR)
    assert g._account_color({"color": brand.ACCOUNT_PINK}) == brand.ACCOUNT_PINK
    assert g._account_color({}) is None


def test_public_record_uses_the_recoloured_hue(brand):
    from agent_friday.services import google_accounts as g
    rec = {"id": "a", "email": "a@example.com", "color": "#ef4444", "status": "connected"}
    shown = g._public_record(rec)["color"]
    assert shown in brand.ACCOUNT_PALETTE and shown != "#ef4444"


def test_sign_in_button_takes_no_micro_label_tracking():
    from agent_friday.core import LOGIN_HTML
    button = re.search(r"(?m)^button\{([^}]*)\}", LOGIN_HTML).group(1)
    assert "letter-spacing" not in button
