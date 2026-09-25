"""The Meetings panel and the recording indicator are in both UI files.

index.html is what runs; ui_parts/app.html is its JSX mirror. The indicator
is mounted at the app root so it shows on every screen, and the consent
statement is read from the server rather than copied into the page.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")


def test_components_are_defined_once_in_both_files():
    for src in (INDEX, APP):
        assert src.count("function MeetingsPanel(") == 1
        assert src.count("function MeetingRecIndicator(") == 1


def test_the_indicator_is_mounted_beside_the_top_bar():
    anchor = "React.createElement(MeetingRecIndicator, null), /*#__PURE__*/React.createElement(\"div\", {\n    className: `top-bar"
    assert INDEX.count(anchor) == 1
    assert APP.count("<MeetingRecIndicator/>\n    <div className={`top-bar") == 1


def test_the_panel_lives_in_the_calendar():
    cal = INDEX[INDEX.index("function CalendarWS()"):]
    cal = cal[:cal.index("\nfunction ", 10)]
    assert cal.count("React.createElement(MeetingsPanel, null)") == 1
    cal_jsx = APP[APP.index("function CalendarWS(){"):]
    cal_jsx = cal_jsx[:cal_jsx.index("\nfunction ", 10)]
    assert cal_jsx.count("<MeetingsPanel/>") == 1


def test_consent_text_comes_from_the_server():
    for src in (INDEX, APP):
        assert "consent_text" in src
        assert "many require everyone's consent" not in src
