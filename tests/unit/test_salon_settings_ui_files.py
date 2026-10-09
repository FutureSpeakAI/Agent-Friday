"""The Salon section of Settings → Connections, and the Costs split (salon spec §6.1 table rows
"Settings → Salon" and "Costs"; Settings keeps its eight task-shaped tabs, so the Salon is a section, not a ninth tab), as the served files carry them, in index.html and its
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
def test_the_salon_is_a_section_of_connections_not_a_ninth_tab(html):
    assert "id: 'salon'" not in html
    # One branch per tab in SettingsWS (desktop_targets.settings_parts reads the
    # sections per tab): the Salon is drawn inside the Connections branch.
    sw = html[html.index("function SettingsWS("):]
    sw = sw[:sw.index("\nfunction ", 10)]
    marks = [i for i in range(len(sw)) if sw.startswith("tab === 'connections' && ", i)]
    assert len(marks) == 1, "Settings draws Connections from more than one branch"
    nxt = sw.index("tab === '", marks[0] + 10)
    assert "window.FridaySalonSettings" in sw[marks[0]:nxt]


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


def test_the_salon_settings_offer_the_engine_choice_with_its_disclosure():
    """Spec §4.7: engine is part of the routing record; choosing Claude's agent
    is the user's call and the disclosure is shown where the choice is made."""
    assert "/engine" in JS and "claude_agent" in JS
    assert "'data-engine'" in JS
    assert "read this PC's files" in JS
