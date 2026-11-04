"""Every governed call is named in the spec's six action classes (NS-18.2-1): observe, internal
create, internal modify, outward reversible, outward consequential, forbidden. The name is on the
verdict and on the signed receipt; an unknown tool is outward consequential (§18.3).

Before: the gate knew four classes (internal, outward, observe, forbidden). A calendar entry on the
owner's own calendar and an email to a stranger were the same "outward", and the receipt could not
say which one the owner was asked about. The label never loosens a decision. Synthetic calls only.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as gate


def test_the_six_classes_are_the_spec_names():
    assert gate.ACTION_CLASSES == ("observe", "internal_create", "internal_modify",
                                   "outward_reversible", "outward_consequential", "forbidden")


@pytest.mark.parametrize("tool,args,klass,want", [
    ("search_email", {"query": "x"}, gate.OBSERVE, "observe"),
    ("read_file", {"path": "x"}, gate.INTERNAL, "observe"),
    ("create_note", {"text": "x"}, gate.INTERNAL, "internal_create"),
    ("organize_wiki", {"op": "move"}, gate.INTERNAL, "internal_modify"),
    ("create_task", {"title": "x"}, gate.OUTWARD, "outward_reversible"),
    ("create_calendar_event", {"summary": "x"}, gate.OUTWARD, "outward_reversible"),
    ("create_calendar_event", {"summary": "x", "attendees": ["a@example.test"]}, gate.OUTWARD,
     "outward_consequential"),
    ("create_calendar_event", {"summary": "x", "calendar_id": "team@example.test"}, gate.OUTWARD,
     "outward_consequential"),
    ("update_calendar_event", {"event_id": "e1", "start": "2026-11-02T10:00"}, gate.OUTWARD,
     "outward_consequential"),
    ("draft_email", {"to": "a@example.test", "send": True}, gate.OUTWARD, "outward_consequential"),
    ("text_by_phone", {"to": "x", "body": "y"}, gate.OUTWARD, "outward_consequential"),
    ("some_tool_nobody_registered", {}, gate.OUTWARD, "outward_consequential"),
    ("write_file", {"path": "x"}, "forbidden", "forbidden"),
])
def test_each_call_is_named_in_one_class(tool, args, klass, want):
    assert gate.action_class(tool, args, klass) == want


def test_a_file_write_is_a_modify_without_looking_at_the_disk(tmp_path):
    assert gate.action_class("write_file", {"path": str(tmp_path / "new.md")}, gate.INTERNAL) == "internal_modify"


def test_an_unknown_tool_is_outward_consequential_end_to_end(monkeypatch):
    receipts = []
    monkeypatch.setattr(gate, "_receipt", lambda entry: receipts.append(entry) or entry)
    v = gate.authorize("some_tool_nobody_registered", {"x": 1}, {})
    assert v.klass == gate.OUTWARD and v.action != "allow"
    assert v.detail.get("action_class") == "outward_consequential"
    assert receipts and receipts[-1].get("action_class") == "outward_consequential"


def test_the_label_never_loosens_a_decision(monkeypatch):
    monkeypatch.setattr(gate, "_receipt", lambda entry: entry)
    # a reversible outward change still waits for the owner exactly as before
    v = gate.authorize("create_task", {"title": "synthetic"}, {})
    assert v.action != "allow" and v.detail.get("action_class") == "outward_reversible"
    base = gate.authorize("some_tool_nobody_registered", {"x": 1}, {})
    for label in gate.ACTION_CLASSES:
        monkeypatch.setattr(gate, "action_class", lambda *a, _l=label: _l)
        v = gate.authorize("some_tool_nobody_registered", {"x": 1}, {})
        assert (v.action, v.klass, v.reason) == (base.action, base.klass, base.reason), label

    def boom(*a):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(gate, "action_class", boom)
    v = gate.authorize("some_tool_nobody_registered", {"x": 1}, {})
    assert (v.action, v.klass, v.reason) == (base.action, base.klass, base.reason)
