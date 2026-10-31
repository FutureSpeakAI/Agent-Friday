"""The phone list's Settings section (Connections > Google) is in both copies of the page.

index.html is what runs; ui_parts/app.html is its source copy. Both must carry the same
TasksInboxPanel and render it on the Google accounts part, with nothing pre-selected (no
account, no text level) and Recommended shown as a label. On the old tree the section does
not exist, so these fail by assertion.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


def _panel(text: str) -> str:
    m = re.search(r"function TasksInboxPanel\(\) \{.*?\n\}\n", text, re.S)
    assert m, "there is no phone list section (TasksInboxPanel) in the page"
    return m.group(0)


def test_both_copies_carry_the_same_section():
    a, b = (_panel(p.read_text(encoding="utf-8")) for p in FILES)
    assert a == b


def test_the_section_is_rendered_on_the_google_accounts_part():
    for p in FILES:
        text = p.read_text(encoding="utf-8")
        assert "React.createElement(TasksInboxPanel, null)" in text, p.name


def test_nothing_is_pre_selected_and_recommended_is_only_a_label():
    panel = _panel(FILES[0].read_text(encoding="utf-8"))
    assert "useState('')" in panel                       # no account, no level chosen for the owner
    assert re.search(r"const \[acct, setAcct\] = useState\(''\)", panel)
    assert re.search(r"const \[level, setLevel\] = useState\(''\)", panel)
    assert "(Recommended)" in panel
    assert "disabled: busy || !acct || !level" in panel


def test_the_section_says_her_name_through_the_helper():
    panel = _panel(FILES[0].read_text(encoding="utf-8"))
    for literal in re.findall(r"'[^']*'", panel):
        assert "Friday" not in literal, literal
