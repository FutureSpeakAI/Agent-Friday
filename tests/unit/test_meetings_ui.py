"""The Meetings panel and the recording indicator are in both UI files.

index.html is what runs; ui_parts/app.html is its JSX mirror. The indicator
is mounted at the app root so it shows on every screen, and the consent
statement is read from the server rather than copied into the page.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")


def test_components_are_defined_once_in_both_files():
    for src in (INDEX, APP):
        assert src.count("function MeetingsPanel(") == 1
        assert src.count("function MeetingRecIndicator(") == 1


def test_the_indicator_is_mounted_beside_the_top_bar():
    # One element, drawn just before the one top bar by the desktop and by a
    # workspace tab (App draws the same shell in both).
    # The step panel (static/step_panel.js) rides in the same slot, so the pair is one fragment.
    assert INDEX.count("const shellMeeting = /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(MeetingRecIndicator, null), window.FridayStepPanel ?") == 1
    assert INDEX.count("shellMeeting, shellTopBar,") == 2
    assert APP.count("const shellMeeting = <><MeetingRecIndicator/>{window.FridayStepPanel?<window.FridayStepPanel/>:null}</>;") == 1
    assert len(re.findall(r"\{shellMeeting\}\s*\{shellTopBar\}", APP)) == 2


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


# ── MT-4: the indicator, the start card and the keep-and-delete choices ─────

def _meetings_region(src):
    a = src.index("// ═══ MEETINGS (")
    return src[a:src.index("// ═══ DRAFT PREVIEW HELPERS")]


def _indicator(src):
    r = _meetings_region(src)
    return r[r.index("function MeetingRecIndicator("):r.index("function MeetingsPanel(")]


def test_the_indicator_has_no_way_to_hide_it():
    for src in (INDEX, APP):
        ind = _indicator(src)
        # the one control is Stop; nothing dismisses, collapses or snoozes it
        assert ind.count("<button") + ind.count('"button"') == 1
        assert "Stop recording" in ind
        low = ind.lower()
        for word in ("dismiss", "snooze", "minimi", "collapse", "sethidden", "display: 'none'", "display:'none'"):
            assert word not in low, word
        # and no layout, setting or later style can switch it off
        assert "display:flex !important;visibility:visible !important;opacity:1 !important" in ind


def test_the_indicator_says_what_is_being_captured_and_is_findable():
    for src in (INDEX, APP):
        ind = _indicator(src)
        assert "meeting-rec-indicator" in ind
        assert "microphone and the other side of the call" in ind and "microphone only" in ind
        assert "both sides" in ind


def test_the_tray_tooltip_is_in_step_with_the_indicator():
    from agent_friday.services.meeting_capture import tray_tooltip
    one = tray_tooltip({"recording": True, "elapsed_s": 5})
    two = tray_tooltip({"recording": True, "elapsed_s": 5, "system_audio": {"captured": True}})
    assert "other side" not in one and "other side of the call" in two


def test_the_start_card_carries_the_consent_line_the_note_and_the_choices():
    for src in (INDEX, APP):
        r = _meetings_region(src)
        for text in ("Copy the line", "Save my wording", "Say it aloud when recording starts",
                     "Also record the other side of the call", "jurisdiction_note",
                     "I've told everyone", "I've checked what applies", "Consent line"):
            assert text in r, text
        assert "/api/meetings/consent" in r
        # nothing is ticked for the owner
        assert "useState(false)" in r and "defaultChecked" not in r


def test_retention_is_offered_with_nothing_preselected():
    for src in (INDEX, APP):
        r = _meetings_region(src)
        assert "Keeping and deleting" in r and "(Recommended)" in r
        # the choices and the Recommended label come from the server; the page decides nothing
        assert "o.recommended" in r
        for value in ("delete_after_notes", "30_days"):
            assert value not in r, value
        assert "ret.audio.selected" in r and "ret.text.selected" in r


def test_deleting_audio_is_its_own_button_and_the_meeting_delete_says_what_it_is():
    for src in (INDEX, APP):
        r = _meetings_region(src)
        assert "Delete audio only" in r and "Delete meeting" in r
        assert "'/audio'" in r and "'DELETE'" in r.replace('"', "'")
        assert "The transcript and notes stay" in r


def test_a_suggestion_is_a_card_with_no_pestering_and_a_way_back():
    for src in (INDEX, APP):
        r = _meetings_region(src)
        assert "Take notes?" in r and "Not now" in r and "Turn them back on" in r
        assert "/api/meetings/suggestion" in r
        assert "Nothing records until you start it" in r


def test_the_suggestion_is_spent_when_it_is_seen_not_when_it_loads():
    for src in (INDEX, APP):
        r = _meetings_region(src)
        assert "IntersectionObserver" in r and "meeting-suggestion-card" in r
        assert "shown" in r
        # the load path only asks for the card; nothing posts "shown" outside the observer
        assert r.count("'shown'") + r.count('"shown"') == 1


def test_retention_has_a_settings_row_in_privacy_and_data_with_the_same_keys():
    for src in (INDEX, APP):
        a = src.index("Meeting recordings")
        row = src[a:a + 4000]
        for key in ("meeting_audio_retention", "meeting_text_retention", "meeting_text_retention_days"):
            assert key in row, key
        assert "(Recommended)" in row and "disabled" in row         # a label, and no preselected choice
        assert "value=\"\"" in row or 'value: ""' in row
    # inside Privacy & Data, ahead of File access
    assert APP.index("Meeting recordings") < APP.index('<StSection title="File access"')
    assert INDEX.index('title: "Meeting recordings"') < INDEX.index('title: "File access"')


def test_the_transcript_shows_who_spoke():
    for src in (INDEX, APP):
        assert "g.speaker" in _meetings_region(src)


def test_calendar_declares_the_start_nav_key_in_both_files():
    assert "keys: ['date', 'view', 'meeting_id', 'event', 'start']" in INDEX
    assert "keys:['date','view','meeting_id','event','start']" in APP


def test_the_calendar_manifest_declares_the_start_key_and_keeps_recording_by_design():
    # The Laya manifests arrive with the WS-1 package; until they are on this
    # tree there is nothing to check, and once they are this test holds the line.
    import json
    import pytest
    path = ROOT / "src" / "agent_friday" / "resources" / "laya_manifests" / "calendar.json"
    if not path.exists():
        pytest.skip("no Laya manifests on this tree yet (WS-1)")
    m = json.loads(path.read_text(encoding="utf-8"))
    assert "start" in m["nav"]["keys"]
    nfl = {n["action"]: n for n in m["not_for_laya"]}
    rec = nfl["calendar.record_meeting"]
    assert rec["kind"] == "by_design" and "start recording" in rec["reason"]
    assert "\n" not in rec["reason"] and len(rec["reason"]) <= 160
