"""The receipts of Friday's organize actions, and the owner's own Undo.

The page lists receipts without their undo internals, and the owner's Undo
button puts a change back at once.
"""
from __future__ import annotations

import pytest

from agent_friday.services import action_journal as journal
from agent_friday.services import studio_files as sf


@pytest.fixture
def moved(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import file_grants as fg
    monkeypatch.setattr(fg, "_ledger_path", lambda: friday_dir / "privacy" / "file_grants.jsonl")
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    fg._invalidate_cache()
    docs = tmp_path / "Documents"
    (docs / "Taxes").mkdir(parents=True)
    (docs / "w2.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(sf, "roots", lambda: {"documents": docs})
    from agent_friday.services import item_actions as ia
    out = ia.organize_files("move", items=["Documents/w2.pdf"], to="Documents/Taxes")
    yield docs, out["receipt_id"]
    journal.reset()
    fg._invalidate_cache()


def test_receipts_are_listed_without_their_undo_data(client, moved):
    _docs, rid = moved
    r = client.get("/api/actions/receipts?limit=5")
    assert r.status_code == 200
    recs = r.get_json()["receipts"]
    assert recs[0]["receipt_id"] == rid and recs[0]["undoable"] is True
    assert "undo" not in recs[0]
    one = client.get("/api/actions/receipts/" + rid).get_json()["receipt"]
    assert one["summary"].startswith("Done")


def test_the_owners_undo_puts_it_back_at_once(client, moved):
    docs, rid = moved
    assert (docs / "Taxes" / "w2.pdf").exists()
    r = client.post("/api/actions/receipts/%s/undo" % rid)
    assert r.status_code == 200 and r.get_json()["result"]["status"] == "complete"
    assert (docs / "w2.pdf").exists() and not (docs / "Taxes" / "w2.pdf").exists()
    again = client.post("/api/actions/receipts/%s/undo" % rid)
    assert again.get_json()["result"]["status"] == "nothing"


def test_a_bad_receipt_id_is_refused(client):
    assert client.post("/api/actions/receipts/..%2F..%2Fx/undo").status_code in (400, 404)
    assert client.get("/api/actions/receipts/rcpt_zz").status_code == 400
