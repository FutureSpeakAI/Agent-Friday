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


def _press():
    """What Friday's page sends on a press: same-origin browser metadata, its session token and,
    where this tree has one, this launch's desk key."""
    import agent_friday.core as core
    h = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", "X-Friday-Token": core._current_api_token()}
    try:
        from agent_friday.services import desk_press
        h["X-Friday-Desk"] = desk_press._KEY
    except ImportError:
        pass
    return h


def test_the_owners_undo_puts_it_back_at_once(client, moved):
    docs, rid = moved
    assert (docs / "Taxes" / "w2.pdf").exists()
    r = client.post("/api/actions/receipts/%s/undo" % rid, headers=_press())
    assert r.status_code == 200 and r.get_json()["result"]["status"] == "complete"
    assert (docs / "w2.pdf").exists() and not (docs / "Taxes" / "w2.pdf").exists()
    again = client.post("/api/actions/receipts/%s/undo" % rid, headers=_press())
    assert again.get_json()["result"]["status"] == "nothing"


def test_a_bad_receipt_id_is_refused(client):
    assert client.post("/api/actions/receipts/..%2F..%2Fx/undo").status_code in (400, 404)
    assert client.get("/api/actions/receipts/rcpt_zz").status_code == 400


def test_an_undo_without_the_owners_press_puts_nothing_back(client, moved):
    """The route acts as the owner. A program on this PC that can reach the API, with or without
    some of the page's headers, is not the owner's press: nothing moves."""
    docs, rid = moved
    import agent_friday.core as core
    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
                    {"X-Friday-Token": core._current_api_token()}):
        r = client.post("/api/actions/receipts/%s/undo" % rid, headers=headers)
        assert r.status_code == 403, headers
        assert "Nothing was put back" in r.get_json()["error"]
    assert (docs / "Taxes" / "w2.pdf").exists() and not (docs / "w2.pdf").exists()
