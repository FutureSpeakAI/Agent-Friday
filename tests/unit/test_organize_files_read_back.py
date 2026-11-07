"""A file change counts as done only once the disk shows it (NS-4.6-1: verify effects against an
authoritative source).

Before: organize_files wrote "Done: moved 1 file" because the move call returned. Now each change is
read back (a move or rename: the new place has it and the old one does not; trash: the kept copy is
in Friday's trash and the original is gone; a new folder: it is there), the receipt says which items
were found, and a change that cannot be found is never called done. Synthetic files in a temp folder.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import studio_files as sf


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch, friday_dir):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import file_grants as fg
    monkeypatch.setattr(fg, "_ledger_path", lambda: friday_dir / "privacy" / "file_grants.jsonl")
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "friday-trash" / rid)
    journal.reset()
    fg._invalidate_cache()
    yield
    journal.reset()
    fg._invalidate_cache()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    downloads = tmp_path / "Downloads"
    (docs / "Taxes").mkdir(parents=True)
    downloads.mkdir()
    monkeypatch.setattr(sf, "roots", lambda: {"documents": docs, "downloads": downloads})
    (downloads / "notes.txt").write_text("keep me", encoding="utf-8")
    (downloads / "w2.pdf").write_bytes(b"%PDF-1 w2")
    return {"docs": docs, "downloads": downloads}


def test_a_move_found_at_its_new_place_is_read_back(roots):
    out = ia.organize_files("move", items=["Downloads/notes.txt"], to="Documents/Taxes")
    assert out["status"] == "complete", out
    assert journal.get(out["receipt_id"])["items"][0]["verified"] is True


def test_a_rename_is_read_back(roots):
    out = ia.organize_files("rename", items=["Downloads/notes.txt"], new_name="todo.txt")
    assert out["status"] == "complete", out
    assert journal.get(out["receipt_id"])["items"][0]["verified"] is True


def test_a_new_folder_is_read_back(roots):
    out = ia.organize_files("new_folder", to="Documents/Taxes 2026")
    assert out["status"] == "complete", out
    assert journal.get(out["receipt_id"])["items"][0]["verified"] is True


def test_trash_is_read_back_from_fridays_trash(roots):
    out = ia.organize_files("trash", items=["Downloads/w2.pdf"])
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    assert rec["status"] == "complete", rec
    assert journal.get(rec["receipt_id"])["items"][0]["verified"] is True


def test_a_move_that_did_not_happen_is_not_called_done(roots, monkeypatch):
    monkeypatch.setattr(journal, "safe_move", lambda src, dst: None)    # returns, moves nothing
    out = ia.organize_files("move", items=["Downloads/notes.txt"], to="Documents/Taxes")
    assert (roots["downloads"] / "notes.txt").exists()
    rec = journal.get(out["receipt_id"])
    assert rec["items"][0]["verified"] is False
    assert out["status"] != "complete", out
    assert not rec["summary"].startswith("Done"), rec["summary"]
    assert "confirm" in rec["summary"], rec["summary"]


def test_one_item_not_found_does_not_hide_the_ones_that_were(roots, monkeypatch):
    real = journal.safe_move

    def flaky(src, dst):
        if src.name == "w2.pdf":
            return None
        return real(src, dst)

    monkeypatch.setattr(journal, "safe_move", flaky)
    out = ia.organize_files("move", items=["Downloads/notes.txt", "Downloads/w2.pdf"], to="Documents/Taxes")
    ap.decide(out["approval_id"], "approve")
    rec = ia.wait_for(out["approval_id"], 10)
    got = {i["name"]: i.get("verified") for i in journal.get(rec["receipt_id"])["items"]}
    assert got == {"notes.txt": True, "w2.pdf": False}, got
    assert rec["status"] == "partial", rec
