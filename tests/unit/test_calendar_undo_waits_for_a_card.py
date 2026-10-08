"""Putting calendar events back writes to Google, like moving them did, so when Friday is asked to
undo a move it raises one card, as the move had; the owner's own Undo on their page puts them back
at once, and the approved card puts back exactly that receipt's events. Google is faked."""
from __future__ import annotations

import pytest

from agent_friday.services import item_actions as ia

REC = {"receipt_id": "rcpt_cal_1", "domain": "calendar", "action": "move", "summary": "Moved 2 events a day later.",
       "undo": {"calendar": [{"id": "ev1", "start": "2031-03-10T09:00:00-05:00", "end": "2031-03-10T10:00:00-05:00"},
                             {"id": "ev2", "start": "2031-03-11T09:00:00-05:00", "end": "2031-03-11T09:30:00-05:00"}]}}


@pytest.fixture
def faked(monkeypatch):
    calls = {"undo": 0, "cards": []}
    monkeypatch.setattr(ia, "_undo_target", lambda rid, cid, strict=True: dict(REC))
    monkeypatch.setattr(ia, "undo_calendar", lambda rec: calls.__setitem__("undo", calls["undo"] + 1)
                        or {"status": "complete", "receipt_id": "rcpt_undo", "summary": "Put back 2 events."})
    monkeypatch.setattr(ia, "_raise_card", lambda domain, action, detail, **k: calls["cards"].append((domain, detail))
                        or {"status": "needs_approval", "approval_id": "appr_x"})
    monkeypatch.setattr(ia, "_tell_pages", lambda *a, **k: None)
    return calls


def test_the_gate_reads_a_calendar_undo_as_outward(faked):
    assert ia.classify_undo({"receipt_id": "rcpt_cal_1"})[0] == "outward"


def test_friday_asked_to_undo_a_move_raises_one_card_and_writes_nothing(faked):
    out = ia.undo("rcpt_cal_1", conversation_id="c1")
    assert out["status"] == "needs_approval" and faked["undo"] == 0
    (domain, detail), = faked["cards"]
    assert domain == "calendar_undo" and detail["receipt_id"] == "rcpt_cal_1" and detail["count"] == 2


def test_the_owner_s_own_undo_puts_them_back_at_once(faked):
    ia.undo("rcpt_cal_1", conversation_id="c1", by_owner=True)
    assert faked["undo"] == 1 and not faked["cards"]


def test_the_approved_card_puts_back_exactly_that_receipt(faked, monkeypatch):
    seen = {}
    monkeypatch.setattr(ia.journal, "get", lambda rid: seen.setdefault("rid", rid) and dict(REC))
    ia._execute({"domain": "calendar_undo", "receipt_id": "rcpt_cal_1"}, "appr_x")
    assert seen["rid"] == "rcpt_cal_1" and faked["undo"] == 1
