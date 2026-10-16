"""The approvals store is never read as empty because it was busy for a moment.

On Windows a read or a replace can meet the file mid-replace (another thread,
an indexer, antivirus) and get a sharing violation. Read as "no cards", the
next write would drop every card in the store.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import approvals


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals.time, "sleep", lambda s: None)
    yield


def _card(i):
    return approvals.create_approval(
        kind="media_diet_rule", subject_type="rule", subject_id="busy-%d" % i,
        title="Change rule %d" % i, action_description="add the rule 'limit %d'" % i,
        description="Friday would add this rule.", force_gate=True,
        payload={}, requested_by="media_diet")


def _busy_reads(monkeypatch, times):
    real = Path.read_text
    left = {"n": times}

    def read_text(self, *a, **k):
        if self == approvals.APPROVALS_FILE and left["n"] > 0:
            left["n"] -= 1
            raise PermissionError(32, "The process cannot access the file because it is being used")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", read_text)


def test_a_busy_read_is_retried_not_read_as_empty(monkeypatch):
    first = _card(1)
    _busy_reads(monkeypatch, 2)
    got = approvals.get_approval(first["approval_id"])
    assert got is not None and got["approval_id"] == first["approval_id"]


def test_a_card_written_after_a_busy_read_keeps_the_others(monkeypatch):
    first = _card(1)
    _busy_reads(monkeypatch, 5)
    second = _card(2)
    ids = {r["approval_id"] for r in approvals.list_approvals()}
    assert {first["approval_id"], second["approval_id"]} <= ids


def test_a_store_that_stays_busy_raises_rather_than_reading_empty(monkeypatch):
    _card(1)
    _busy_reads(monkeypatch, 100)
    with pytest.raises(PermissionError):
        approvals.get_approval("anything")
