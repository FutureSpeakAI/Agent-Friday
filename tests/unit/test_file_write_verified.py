"""A file Friday writes counts as done only once it is read back from disk (NS-4.6-1: verify effects
against an authoritative source; file hash).

Before: write_file's own "Wrote N chars" reply was the whole evidence, and a task whose only tool was
a write was verified by it. Now the file is read back after the write and compared with what was
meant to be written (a whole write: the same bytes; an append: the earlier size plus the new text, at
the end); a task whose write was not read back is completed_unverified and says so. Synthetic data.
"""
from __future__ import annotations

import os

import pytest

from agent_friday.services import action_journal as journal
from agent_friday.services import agent
from agent_friday.services import connector_outcome as co
from agent_friday.services import file_write_receipts as fwr
from agent_friday.services import whole_file


@pytest.fixture
def folder(tmp_path, monkeypatch, friday_dir):
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    monkeypatch.setenv("FRIDAY_SANDBOX_ROOT", str(tmp_path))
    yield tmp_path
    journal.reset()


def _write(path, content, mode="write"):
    with co.call() as notes:
        out = agent._tool_write_file({"path": str(path), "content": content, "mode": mode})
    return out, notes


def test_a_whole_write_found_on_disk_is_verified(folder):
    target = folder / "note.txt"
    out, notes = _write(target, "line one\nline two\n")
    assert out.startswith("Wrote")
    assert [v["kind"] for v in notes.verified] == ["file_write"]
    assert notes.verified[0]["ref"] == str(target.resolve())[:120]   # the note keeps 120 characters


def test_an_empty_file_is_verified(folder):
    _out, notes = _write(folder / "empty.txt", "")
    assert [v["kind"] for v in notes.verified] == ["file_write"]


def test_an_append_found_at_the_end_of_the_file_is_verified(folder):
    target = folder / "log.txt"
    _write(target, "first\n")
    _out, notes = _write(target, "second\n", mode="append")
    assert [v["kind"] for v in notes.verified] == ["file_write"]
    assert target.read_text(encoding="utf-8").replace(os.linesep, "\n") == "first\nsecond\n"


def test_a_write_that_did_not_land_as_asked_is_not_verified(folder, monkeypatch):
    real = whole_file.write_whole
    monkeypatch.setattr(whole_file, "write_whole", lambda dest, data: real(dest, data[:-1]))
    out, notes = _write(folder / "short.txt", "abcdef")
    assert out.startswith("Wrote")          # the write's own reply is what it was
    assert notes.verified == []             # but nothing read back says it is there


def test_a_file_changed_before_the_read_back_is_not_verified(folder):
    target = folder / "moved-under-us.txt"
    assert fwr.verify_written(target, "write", "x") is False       # nothing there
    target.write_bytes(b"x")
    assert fwr.verify_written(target, "write", "x") is True
    target.write_bytes(b"y")
    assert fwr.verify_written(target, "write", "x") is False
    assert fwr.verify_written(target, "append", "y", before_size=0) is True
    assert fwr.verify_written(target, "append", "y", before_size=3) is False


def test_a_task_whose_only_tool_was_a_write_is_complete_only_when_it_was_read_back():
    made = {"name": "write_file", "input": {}, "result": "Wrote 5 chars to C:\\x\\a.txt (new file; receipt_id r1; undo_action moves it to Friday's trash)."}
    verified, summary, status = agent._evidence_verdict([made])
    assert status == "completed_unverified" and "not read back" in summary
    verified, summary, status = agent._evidence_verdict([dict(made, verified=[{"kind": "file_write", "ref": "C:\\x\\a.txt"}])])
    assert status == "complete" and verified is True
