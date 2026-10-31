"""North star §33.1 (BD-21, slice AX1): controls a person reaches by keyboard.

The app suite (tests/app/specs/accessibility_keyboard.spec.ts) drives the page;
these read the page source for the controls a test in a browser cannot easily
reach (the first-run consent choices and the quick toggles), so a choice made by
clicking a bare <div> cannot come back unnoticed.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


def _block(src: str, anchor: str, n: int = 900) -> str:
    i = src.index(anchor)
    return src[i:i + n]


def test_the_first_run_route_choices_are_radios_in_a_named_group():
    block = _block(INDEX, "s.name === 'routing' && React.createElement('div', {")
    assert "role: 'radiogroup'" in block and "role: 'radio'" in block
    assert "tabIndex: 0" in block and "'aria-checked': mode === c.mode" in block
    assert "e.key === 'Enter' || e.key === ' '" in block


def test_the_cloud_consent_choices_are_radios_in_a_named_group():
    i = INDEX.index("{ mode: 'local_private', label: 'Private (local)',")
    before, after = INDEX[i - 400:i], INDEX[i:i + 1500]
    assert "role: 'radiogroup'" in before
    assert "role: 'radio'" in after and "'aria-checked': choice === c.mode" in after
    assert "e.key === 'Enter' || e.key === ' '" in after


def test_the_quick_toggles_are_switches_a_keyboard_can_flip():
    for anchor, state in (("show_all_workspaces: !showAllWorkspaces", "!!showAllWorkspaces"),
                          ("onClick: () => fridayToggleOffRecord(!!agentSettings.off_record, saveAgentSettings)",
                           "!!agentSettings.off_record")):
        setting = state.lstrip("!")
        i = INDEX.index(anchor)
        block = INDEX[i - 600:i]
        assert 'role: "switch"' in block and ('"aria-checked": %s' % state) in block, setting
        assert "tabIndex: 0" in block and "e.key === 'Enter' || e.key === ' '" in block, setting


def test_no_settings_toggle_is_a_bare_clickable_div():
    for m in re.finditer(r'className: "settings-toggle",', INDEX):
        block = INDEX[m.start():m.start() + 500]
        if "onClick" in block.split("}, /*#__PURE__*/")[0]:
            assert "tabIndex: 0" in block and "role:" in block, block[:200]


def test_the_page_has_a_skip_link_a_main_region_and_a_named_dock():
    assert '<a class="skip-link" href="#ui-inner">' in INDEX
    # The skip link's target takes focus; the app's own <main> elements stay the landmarks.
    assert '<div id="ui-inner" tabindex="-1"' in INDEX
    assert 'role: "navigation",\n    "aria-label": "Workspaces"' in INDEX
    assert '"aria-pressed": openWins.has(w.id),' in INDEX


def test_focus_is_never_hidden_and_the_system_choices_are_honoured():
    for needle in ('[role="radio"]:focus-visible', '[role="switch"]:focus-visible',
                   '.fr-snap:focus-visible', "@media (forced-colors: active)",
                   "@media (prefers-contrast: more)"):
        assert needle in INDEX, needle


def test_the_app_suite_runs_the_accessibility_spec_in_ci():
    wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "accessibility_keyboard.spec.ts" in wf
