"""write_file keeps a copy and a receipt BEFORE it overwrites, and undo puts the file back.

Before: write_file replaced an existing file with no backup and no receipt, so a
wrong write lost the owner's document for good, and the card asked "create
<path>?" even when the path already held a file. Now an existing file is copied
(and checked) and a receipt naming it is written before the first byte changes;
a write that fails half way still has its receipt and its backup; undo_action
puts the earlier version back (or moves a created file to Friday's trash), and
keeps any newer version made since. The card says "replace" for an existing file.
Synthetic files only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import action_journal as journal
from agent_friday.services import file_write_receipts as fwr
from agent_friday.services import item_actions


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(fwr, "backups_root", lambda: tmp_path / "backups")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "trash" / rid)
    journal.reset()
    yield tmp_path
    journal.reset()


def _write(path, content, mode="write"):
    from agent_friday.services.agent import _tool_write_file
    return _tool_write_file({"path": str(path), "content": content, "mode": mode})


def _rid(result):
    import re
    m = re.search(r"receipt_id (rcpt_[0-9a-f]+)", result)
    assert m, result
    return m.group(1)


def test_overwriting_keeps_the_earlier_version_and_a_receipt(store):
    doc = store / "work" / "report.txt"
    doc.parent.mkdir()
    doc.write_text("the owner's original", encoding="utf-8")
    out = _write(doc, "Friday's new text")
    assert doc.read_text(encoding="utf-8") == "Friday's new text"
    rec = journal.get(_rid(out))
    assert rec["domain"] == "file_write" and rec["action"] == "overwrite" and rec["status"] == "complete"
    item = rec["undo"]["file"]
    assert Path(item["backup"]).read_text(encoding="utf-8") == "the owner's original"
    assert item["before_sha256"] and item["after_sha256"] and item["before_sha256"] != item["after_sha256"]


def test_the_receipt_and_backup_exist_before_the_write_and_survive_a_failed_one(store, monkeypatch):
    doc = store / "notes.txt"
    doc.write_text("keep me", encoding="utf-8")
    seen = {}

    def failing_write(self, data, encoding=None, errors=None, newline=None):
        # At the moment of the write the receipt and the backup already exist.
        recs = journal.recent(5)
        seen["receipt"] = recs[0] if recs else None
        seen["backup_ok"] = bool(recs) and Path(recs[0]["undo"]["file"]["backup"]).read_text(encoding="utf-8") == "keep me"
        with open(self, "w", encoding="utf-8") as fh:
            fh.write("half")
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", failing_write)
        out = _write(doc, "new")
    assert out.startswith("Write error")
    assert seen["receipt"] and seen["receipt"]["status"] == "pending" and seen["backup_ok"]
    rid = seen["receipt"]["receipt_id"]
    assert rid in out and journal.get(rid)["status"] == "partial"
    item_actions.undo(rid, by_owner=True)
    assert doc.read_text(encoding="utf-8") == "keep me"


def test_undo_puts_the_earlier_version_back(store):
    doc = store / "plan.md"
    doc.write_text("v1", encoding="utf-8")
    rid = _rid(_write(doc, "v2"))
    res = item_actions.undo(rid, by_owner=True)
    assert res["status"] == "complete", res
    assert doc.read_text(encoding="utf-8") == "v1"
    assert journal.get(rid)["undone"] is True


def test_undo_of_a_created_file_moves_it_to_trash_and_removes_the_folders_it_made(store):
    doc = store / "new" / "deep" / "made.txt"
    rid = _rid(_write(doc, "fresh"))
    assert journal.get(rid)["action"] == "create"
    res = item_actions.undo(rid, by_owner=True)
    assert res["status"] == "complete", res
    assert not doc.exists() and not (store / "new").exists()
    assert (store / "trash" / rid / "made.txt").read_text(encoding="utf-8") == "fresh"


def test_undo_after_a_later_edit_keeps_that_newer_version(store):
    doc = store / "list.txt"
    doc.write_text("v1", encoding="utf-8")
    rid = _rid(_write(doc, "v2"))
    doc.write_text("v3 by the owner", encoding="utf-8")
    res = item_actions.undo(rid, by_owner=True)
    assert res["status"] == "complete"
    assert doc.read_text(encoding="utf-8") == "v1"
    kept = store / "trash" / rid / "newer-list.txt"
    assert kept.read_text(encoding="utf-8") == "v3 by the owner"
    assert str(kept) in res["text"]


def test_append_keeps_the_earlier_version_too(store):
    doc = store / "log.txt"
    doc.write_text("line1\n", encoding="utf-8")
    rid = _rid(_write(doc, "line2\n", mode="append"))
    assert doc.read_text(encoding="utf-8") == "line1\nline2\n"
    item_actions.undo(rid, by_owner=True)
    assert doc.read_text(encoding="utf-8") == "line1\n"


def test_no_backup_means_no_write(store, monkeypatch):
    doc = store / "big.bin"
    doc.write_text("precious", encoding="utf-8")

    def no_copy(src, dst):
        raise OSError("copy failed")

    monkeypatch.setattr(fwr, "_copy_checked", no_copy)
    out = _write(doc, "replacement")
    assert out.startswith("Write error: nothing was written")
    assert doc.read_text(encoding="utf-8") == "precious"


def test_the_card_says_replace_for_an_existing_file_and_create_for_a_new_one(store):
    from agent_friday.services import agent
    doc = store / "exists.txt"
    doc.write_text("x", encoding="utf-8")
    ask = agent._confirmation_question("write_file", {"path": str(doc), "content": "y"})
    assert "replace" in ask and "keep a copy" in ask
    new = agent._confirmation_question("write_file", {"path": str(store / "nope.txt"), "content": "y"})
    assert "create" in new


def test_undoing_a_write_outside_friday_output_is_asked_like_the_write(store):
    doc = store / "elsewhere.txt"
    doc.write_text("a", encoding="utf-8")
    rid = _rid(_write(doc, "b"))
    klass, _why = item_actions.classify_undo({"receipt_id": rid})
    assert klass == "outward"


def test_a_file_too_large_to_back_up_is_not_replaced_and_the_answer_says_why(store, monkeypatch):
    monkeypatch.setattr(fwr, "MAX_BACKUP_FILE_BYTES", 10)
    doc = store / "large.txt"
    doc.write_text("more than ten bytes here", encoding="utf-8")
    out = _write(doc, "short")
    assert out.startswith("Write error: nothing was written") and "keeps copies of files up to" in out
    assert doc.read_text(encoding="utf-8") == "more than ten bytes here"


def test_an_append_is_undone_by_cutting_back_not_by_a_copy(store):
    doc = store / "journal.log"
    doc.write_text("a\n", encoding="utf-8")
    rid = _rid(_write(doc, "b\n", mode="append"))
    assert "backup" not in journal.get(rid)["undo"]["file"]
    assert not (store / "backups" / rid).exists()
    item_actions.undo(rid, by_owner=True)
    assert doc.read_text(encoding="utf-8") == "a\n"


def test_a_created_file_the_owner_changed_since_is_left_where_it_is(store):
    doc = store / "fresh.txt"
    rid = _rid(_write(doc, "Friday's"))
    doc.write_text("the owner's own words now", encoding="utf-8")
    res = item_actions.undo(rid, by_owner=True)
    assert res["status"] == "failed" and "left where it is" in res["text"]
    assert doc.read_text(encoding="utf-8") == "the owner's own words now"


def test_a_receipt_naming_some_other_file_as_its_backup_restores_nothing(store, tmp_path):
    doc = store / "target.txt"
    doc.write_text("v1", encoding="utf-8")
    rid = _rid(_write(doc, "v2"))
    secret = tmp_path / "elsewhere-secret.txt"
    secret.write_text("not a backup", encoding="utf-8")
    rec = journal.get(rid)
    rec["undo"]["file"]["backup"] = str(secret)
    res = fwr.undo(rec)
    assert res["status"] == "failed"
    assert doc.read_text(encoding="utf-8") == "v2"


def test_off_the_record_the_copy_stays_in_memory_and_still_undoes(store, monkeypatch):
    monkeypatch.setattr(fwr, "_off_record", lambda: True)
    doc = store / "private.txt"
    doc.write_text("before", encoding="utf-8")
    rid = _rid(_write(doc, "after"))
    assert not (store / "backups").exists() or not any((store / "backups").iterdir())
    assert journal.get(rid)["undo"]["file"]["backup"].startswith("memory:")
    item_actions.undo(rid, by_owner=True)
    assert doc.read_text(encoding="utf-8") == "before"


def test_a_write_that_never_happened_has_nothing_to_undo(store):
    doc = store / "never.txt"
    rec = fwr.begin(doc, "write", 3)
    doc.write_text("someone else's file", encoding="utf-8")
    res = fwr.undo(journal.get(rec["receipt_id"]))
    assert res["status"] == "failed" and "did not happen" in res["summary"]
    assert doc.read_text(encoding="utf-8") == "someone else's file"


def test_a_file_that_cannot_be_read_first_is_not_written_and_the_answer_says_why(store, monkeypatch):
    doc = store / "locked.log"
    doc.write_text("a\n", encoding="utf-8")

    def locked(path, limit=None):
        raise PermissionError("locked by another program")

    monkeypatch.setattr(fwr, "_sha256", locked)
    out = _write(doc, "b\n", mode="append")
    assert out.startswith("Write error: nothing was written") and "could not be read" in out
    assert doc.read_text(encoding="utf-8") == "a\n"


def test_a_write_cut_off_mid_way_can_still_be_undone_from_its_copy(store):
    doc = store / "interrupted.txt"
    doc.write_text("whole", encoding="utf-8")
    rec = fwr.begin(doc, "write", 4)             # Friday closed before finish() ran
    doc.write_text("ha", encoding="utf-8")
    res = fwr.undo(journal.get(rec["receipt_id"]))
    assert res["status"] == "complete", res["summary"]
    assert doc.read_text(encoding="utf-8") == "whole"
