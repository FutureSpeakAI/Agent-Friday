"""Where Friday may write (phase 4): the Message Center's composer, the calendar's quick-add line and
follow-up draft, the workflow editor and its compose box, and a running task's steer box. Each registers its
fields with the stage layer; none of them gives the stage a way to press Send, Save, Add or Run.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_composer_registers_to_subject_and_the_typed_part_of_the_body():
    mail = _read("static/friday_mail.js")
    for key in ("'.to'", "'.subject'", "'.body'"):
        assert "FS.registerField('messages', { key: kind + %s" % key in mail, key
    assert "const kind = init.mode && init.mode !== 'new' ? 'reply' : 'compose';" in mail
    # the body is the part above the signature and the quoted original: a fill never wipes the quote
    assert "const anchorOf = el =>" in mail and "'.fm-sig, .fm-quote'" in mail
    assert "FS.fillChip(h, 'messages', kind + '.body'" in mail


def test_the_compiled_page_and_its_jsx_twin_register_the_same_workflow_and_task_fields():
    for page in ("index.html", "ui_parts/app.html"):
        text = _read(page)
        for needle in ("registerField('workflows', { key: 'name'", "registerField('workflows', { key: 'description'",
                       "key: 'step.' + (i + 1) + '.prompt'", "registerField('workflows', { key: 'compose'",
                       "registerField('system', { key: 'steer.' + task.task_id"):
            assert needle in text, (page, needle)


def test_the_calendar_registers_its_quick_add_line_and_the_follow_up_draft():
    text = _read("index.html")
    assert "registerField('calendar', { key: 'quickadd'" in text
    assert "FS.registerField('calendar', { key: 'followup.' + k" in text
    assert "part('to', 'To'), part('subject', 'Subject'), part('body', 'Message')" in text


def test_every_fill_leaves_the_owners_button_as_the_only_way_forward():
    """None of the registration blocks calls a submit: they only set the field's own state."""
    for page in ("index.html", "ui_parts/app.html"):
        text = _read(page)
        for block in re.findall(r"// See & Touch: [^\n]*(?:\n  [^\n]*)+?\n  \}, \[[^\]]*\]\);|// See & Touch: the (?:quick-add|steer box|box that describes)[^\n]*(?:\n  [^\n]*){2,5}", text):
            assert not re.search(r"quickAdd\(|draft\(\)|save\(\)|post\('/api/agent/steer'|wfPost\(", block), block[:200]


def test_the_notes_beside_the_fields_use_the_shared_chips_and_say_who_wrote_it():
    for page in ("index.html", "ui_parts/app.html"):
        text = _read(page)
        assert text.count("window.fridayStage.fillChips(React.createElement, '") >= 3, page
    assert "fillChips(React.createElement, 'calendar', ['quickadd']" in _read("index.html")
