"""The Settings pane's shape: eight task-shaped tabs plus About, nothing on hold.

Settings grew one tab per feature until it had thirteen, with the same control
reachable from several of them and developer diagnostics mixed in with what
every user needs. The structure is now fixed by what a user is trying to do:

    General · Intelligence · Accounts & Keys · Privacy & Approvals ·
    Appearance & 3D · Voice & Tracking · Spending · Advanced   (+ About)

These tests hold that shape in BOTH UI files (index.html is served;
ui_parts/app.html is its hand-maintained mirror), and hold the specific
cleanups that came with it:

  * Federation and Economy are on hold (V6 wholeness spec: they must not
    surface). Their panels stay in the source behind one switch,
    SETTINGS_SHOW_HELD_FEATURES, which is false.
  * Computer Control is switched on only from Privacy & Approvals, where the
    warning and the grant status sit beside it -- never from the one-click
    Quick Settings menu.
  * Controls whose keys nothing reads (compact_mode, startup_workspace,
    auto_open_chat) are gone rather than saving into the void.
  * DEFAULT_SETTINGS has no key written twice; a dict literal keeps the last
    one silently, so the first reads as the default while the second is.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
APP = ROOT / "ui_parts" / "app.html"
CORE = ROOT / "src" / "agent_friday" / "core" / "__init__.py"

EXPECTED_TABS = [
    ("general", "General"),
    ("intelligence", "Intelligence"),
    ("accounts", "Accounts & Keys"),
    ("privacy", "Privacy & Approvals"),
    ("appearance", "Appearance & 3D"),
    ("voice", "Voice & Tracking"),
    ("costs", "Spending"),
    ("advanced", "Advanced"),
    ("about", "About"),
]
UI_FILES = pytest.mark.parametrize("path", [INDEX, APP], ids=["index.html", "app.html"])


def _settings_ws(src: str) -> str:
    start = src.index("function SettingsWS(")
    end = src.index("\nfunction ", start + 10)
    return src[start:end]


def _tabs(body: str):
    # Both spellings: index.html's precompiled `id: 'x',\n label: 'Y'` and
    # app.html's `{id:'x',label:'Y'}`.
    return re.findall(r"id:\s*'([a-z_]+)',\s*label:\s*'([^']+)'", body)


@UI_FILES
def test_settings_rail_is_the_eight_task_tabs_plus_about(path):
    tabs = _tabs(_settings_ws(path.read_text(encoding="utf-8")))
    assert tabs == EXPECTED_TABS, "%s rail is %s" % (path.name, tabs)


@UI_FILES
def test_every_rail_tab_has_a_render_branch(path):
    body = _settings_ws(path.read_text(encoding="utf-8"))
    for tab_id, _ in EXPECTED_TABS:
        assert re.search(r"tab\s*===\s*'%s'\s*&&" % tab_id, body), (
            "%s: tab %r selects a blank pane" % (path.name, tab_id))


@UI_FILES
def test_held_features_do_not_surface(path):
    src = path.read_text(encoding="utf-8")
    body = _settings_ws(src)
    assert re.search(r"const SETTINGS_SHOW_HELD_FEATURES\s*=\s*false;", src), (
        "%s: the switch for Federation/Economy is missing or on" % path.name)
    for held in ("federation", "economy"):
        assert not re.search(r"id:\s*'%s'" % held, body), (
            "%s: %s is on hold and must not be a Settings tab" % (path.name, held))
        # The panel may stay in the source, but only behind the switch.
        for m in re.finditer(r"SettingsTab%s\b" % held.capitalize(), body):
            before = body[max(0, m.start() - 200):m.start()]
            assert "SETTINGS_SHOW_HELD_FEATURES" in before, (
                "%s: %s renders without the held-features switch" % (path.name, held))


@UI_FILES
def test_no_leftover_false_stub_branches(path):
    body = _settings_ws(path.read_text(encoding="utf-8"))
    assert not re.search(r"\),\s*false,\s*tab", body), (
        "%s: a literal `false,` stub is still in the render chain" % path.name)


@UI_FILES
def test_legacy_deep_links_still_land(path):
    """Old ids ('providers', 'connectors', 'dock', 'knowledge', 'work') came
    from buttons and events all over the app; they must open the tab that now
    holds that content instead of a blank pane."""
    src = path.read_text(encoding="utf-8")
    m = re.search(r"const SETTINGS_TAB_ALIASES\s*=\s*\{(.*?)\};", src, flags=re.S)
    assert m, "%s: SETTINGS_TAB_ALIASES is missing" % path.name
    aliases = dict(re.findall(r"(\w+)\s*:\s*'([a-z]+)'", m.group(1)))
    real = {tab_id for tab_id, _ in EXPECTED_TABS}
    for old in ("providers", "connectors", "dock", "knowledge", "work"):
        assert aliases.get(old) in real, (
            "%s: legacy tab id %r has no alias to a real tab" % (path.name, old))
    assert "settingsTabId(" in _settings_ws(src), (
        "%s: SettingsWS does not resolve aliases" % path.name)


def _quick_settings(src: str) -> str:
    i = src.index("settings-panel ${settingsOpen")
    return src[i:i + 12000]


@UI_FILES
def test_quick_settings_cannot_switch_on_computer_control(path):
    qs = _quick_settings(path.read_text(encoding="utf-8"))
    assert "computer_control_enabled:" not in qs, (
        "%s: Quick Settings writes computer_control_enabled with no warning "
        "and no grant status" % path.name)
    assert "Computer Control" in qs, (
        "%s: Quick Settings should still point to where Computer Control lives" % path.name)


@UI_FILES
def test_dead_controls_are_gone(path):
    src = path.read_text(encoding="utf-8")
    for key in ("compact_mode", "startup_workspace", "auto_open_chat"):
        assert not re.search(r"\b%s\s*:" % key, src), (
            "%s still writes %s, which nothing reads" % (path.name, key))


def test_default_settings_has_no_key_written_twice():
    tree = ast.parse(CORE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DEFAULT_SETTINGS" for t in node.targets):
            assert isinstance(node.value, ast.Dict)
            seen, dupes = set(), []
            for k in node.value.keys:
                if isinstance(k, ast.Constant):
                    if k.value in seen:
                        dupes.append(k.value)
                    seen.add(k.value)
            assert not dupes, "DEFAULT_SETTINGS repeats %s" % dupes
            return
    pytest.fail("DEFAULT_SETTINGS literal not found")


@UI_FILES
def test_settings_shell_sets_its_own_text_colour(path):
    """Panels that do not colour their own labels (the dock list, the dock
    chips) inherit from the shell. Without a colour here they inherit the
    window's near-black and render all but invisible on the dark pane."""
    body = _settings_ws(path.read_text(encoding="utf-8"))
    assert re.search(r'className: "st-root",\s*style: \{[^}]*color: \'var\(--st-text\)\'', body), (
        "%s: the Settings shell does not set a text colour" % path.name)
