"""A card's id reaches a cloud model intact.

A vault refusal tells the cloud seat "ONE card (appr_...) now offers the owner ...", and
every message to a cloud model goes through the outbound PII scrub (core._scrub_pii). An id
whose ten hex characters were all digits, starting 2-9, read there as a US phone number and
reached the model as ``appr_[PII:phone:...]``: the model was told a card that does not exist
(tests/unit/test_switch_seat_and_local_handoff.py failed on it about once in a hundred runs).
The draws below are forced, so the all-digit case is met every time.
"""
from __future__ import annotations

import types
import uuid

import pytest

import agent_friday.services.approvals as ap
from agent_friday import core

#: Suffixes the scrub reads as phone numbers: ten digits, the first one 2-9.
PHONE_LIKE = ("5551234567", "2025101712")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)


def _draw_phone_like_first(monkeypatch):
    draws = [s + "0" * 22 for s in PHONE_LIKE]

    def uuid4():
        return types.SimpleNamespace(hex=draws.pop(0)) if draws else uuid.uuid4()
    monkeypatch.setattr(ap, "uuid", types.SimpleNamespace(uuid4=uuid4))


def _said_to_the_cloud(aid: str) -> str:
    return core._scrub_pii("ONE card (%s) now offers the owner to run it." % aid)[0]


def test_the_scrub_reads_a_ten_digit_id_as_a_phone_number():
    """The premise: an all-digit suffix is what the scrub replaces."""
    for s in PHONE_LIKE:
        assert "appr_" + s not in _said_to_the_cloud("appr_" + s)


def test_a_new_card_id_is_never_ten_digits(store, monkeypatch):
    _draw_phone_like_first(monkeypatch)
    card = ap.create_approval(kind="seat_handoff", subject_type="conversation",
                              subject_id="conv-1:handoff:a", title="Run this on the local model?",
                              force_gate=True)
    aid = card["approval_id"]
    assert aid.startswith("appr_") and len(aid) == 15
    assert not aid[5:].isdigit(), aid
    assert aid in _said_to_the_cloud(aid), "the cloud model would be told a placeholder, not the card"
    assert ap.get_approval(aid)["status"] == "pending"


def test_a_pre_approved_record_id_is_never_ten_digits(store, monkeypatch):
    _draw_phone_like_first(monkeypatch)
    rec = ap.record_pre_approved(kind="tool_confirm", subject_type="tool", subject_id="t-1",
                                 title="Standing approval", grant_id="g-1")
    assert not rec["approval_id"][5:].isdigit(), rec["approval_id"]
    assert rec["approval_id"] in _said_to_the_cloud(rec["approval_id"])
