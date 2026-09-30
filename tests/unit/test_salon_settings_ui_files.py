"""Settings → Salon and the Costs split (salon spec §6.1 table rows "Settings →
Salon" and "Costs"), as the served files carry them, in index.html and its
mirror: the documented two-place edit."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
SCENE = (ROOT / "ui_parts" / "styles_and_scene.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "friday_salon.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("html", [INDEX, APP], ids=["index", "mirror"])
def test_settings_has_a_salon_tab_in_the_list_and_in_the_branch(html):
    assert "id: 'salon'" in html
    assert "tab === 'salon'" in html and "FridaySalonSettings" in html


@pytest.mark.parametrize("html", [INDEX, APP], ids=["index", "mirror"])
def test_costs_split_by_key_and_by_codebase(html):
    assert 'title: "By key"' in html and "by_key_profile" in html
    assert 'title: "By codebase"' in html and "by_codebase" in html


def test_the_script_loads_after_the_bundles_script_in_both_shells():
    for s in (INDEX, SCENE):
        assert s.index("friday_bundles.js") < s.index("friday_salon.js")


def test_the_salon_settings_show_seats_and_guest_keys_and_never_echo_a_key():
    assert "window.FridaySalonSettings" in JS
    for route in ("/api/codebases", "/seats", "/keys", "/header"):
        assert route in JS, route
    # The key field is a password field and the value is cleared after the add.
    assert "type: 'password'" in JS
    assert "Remove" in JS and "deleted" in JS.lower()
    # Default posture and box backend are shown as what they are, not as switches.
    assert "announce" in JS.lower() and "B0" in JS
