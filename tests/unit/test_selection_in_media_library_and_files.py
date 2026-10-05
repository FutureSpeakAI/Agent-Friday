"""Ticks in Media, the Library and Files are the same ticks as in the Message Center (phase 3): one shared
reducer, a checkbox the hand cursor can reach, a pinch that ticks and a hold that opens, and a held edge.
The Files browser used to drop the {add, range} its engine passes on a ctrl- or shift-click.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_media_ticks_go_through_the_shared_reducer_and_say_who_made_them():
    media = _read("static/media_ws.js")
    assert "const [ss, setSs] = useState(() => FS.emptySelection());" in media
    assert "commitSel({ type: 'toggle', ref: 'media:' + c.id })" in media
    assert "FS.chipText(ss," in media and "'data-testid': 'md-selchip'" in media
    assert "multi: ss.refs.indexOf('media:' + c.id) >= 0, held: !!ss.held['media:' + c.id]" in media
    assert "ids: multi" in media, "the owner's bar still acts on the ticked ids, with no card"


def test_a_media_card_has_a_checkbox_a_pinch_that_ticks_and_a_hold_that_opens():
    media = _read("static/media_ws.js")
    assert "role: 'checkbox'" in media and "'data-fr-tick': ''" in media
    assert "'data-fr-pinch': 'tick', 'data-fr-open-event': 'dblclick', 'data-fr-guarded': 'off'" in media
    assert ".md-card.needs-you{border-color:var(--fr-warn)" in media and "(held ? ' needs-you' : '')" in media


def test_library_documents_tick_the_same_way_and_a_vault_document_cannot_be_ticked():
    lib = _read("static/library_ws.js")
    assert "const [ss, setSs] = useState(() => FS.emptySelection());" in lib
    assert "d.shelf === 'vault' ? null : h('input', { type: 'checkbox', 'data-fr-tick': ''" in lib
    assert "'data-fr-pinch': d.shelf === 'vault' ? undefined : 'tick'" in lib


def test_the_files_browser_keeps_the_add_and_range_its_engine_passes():
    f = _read("static/studio_files3d.js")
    assert "onPick: (i, activate, mods) => pick(i, activate, mods)," in f, "the third argument is no longer dropped"
    assert "function pick(i, activate, mods)" in f
    assert "mods.add || mods.range" in f and "commitSel({ type: 'toggle', ref: fref(it) })" in f
    assert "FS.register('files', FS.makeAdapter('files'" in f
    assert "e.setMarked(" in f and "e.setHeld(" in f, "the engine draws the marks and the amber held tiles"
    assert "const fref = it => 'file:' + rootRef.current + ':' + it.rel;" in f


def test_the_message_center_reports_the_keyboard_row_only_once_the_owner_moved_it_and_the_cursor_row():
    mail = _read("static/friday_mail.js")
    assert "focus: focusTouched.current && shown[focus] ? refOf(shown[focus]) : null" in mail
    assert "cursor: FS.cursorNow(shown.slice(0, FS.MAX_ROWS).map(refOf))" in mail
    assert mail.count("focusTouched.current = true") >= 5


def test_the_stage_css_gives_every_list_a_held_edge_and_a_ticked_ground_from_tokens():
    js = _read("static/friday_stage.js")
    assert "[data-fr-held]{box-shadow:inset 2px 0 0 var(--fr-warn)}" in js
    assert "[data-fr-sel]{background-image:linear-gradient(var(--fr-cyan-soft),var(--fr-cyan-soft));box-shadow:inset 2px 0 0 var(--fr-cyan)}" in js


def test_organize_files_media_and_email_all_read_the_same_deictic_target():
    agent = _read("src/agent_friday/services/agent.py")
    assert agent.count("_screen_target(") >= 4          # the helper and its three callers
    for ws in ('"messages"', '"files"', '"media"'):
        assert "_screen_target(%s)" % ws in agent, ws
