"""Settings > Privacy & Data carries "Bring your AI history in", in both copies of the page.

The section previews first, stages on the owner's choice, and lists what waits for an
answer (StagedImportReview). On the old tree it does not exist, so these fail by assertion.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


def _component(text: str) -> str:
    m = re.search(r"function SettingsAiHistoryImport\(\) \{.*?\n\}\n", text, re.S)
    assert m, "there is no AI-history import section in the page"
    return m.group(0)


def test_both_copies_carry_the_same_section():
    a, b = (_component(p.read_text(encoding="utf-8")) for p in FILES)
    assert a == b


def test_it_sits_in_privacy_and_data_after_the_locks():
    assert "React.createElement(SettingsLocks, null), /*#__PURE__*/React.createElement(SettingsAiHistoryImport, null)" \
        in FILES[0].read_text(encoding="utf-8")
    assert "<SettingsLocks/>\n    <SettingsAiHistoryImport/>" in FILES[1].read_text(encoding="utf-8")


def test_it_previews_before_staging_and_lists_both_services():
    comp = _component(FILES[0].read_text(encoding="utf-8"))
    assert "if (f) send(f, true);" in comp                       # choosing a file only previews
    assert "Nothing has been saved." in comp
    assert "source: 'chatgpt'" in comp and "source: 'claude'" in comp
