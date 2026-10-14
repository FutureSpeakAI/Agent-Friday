"""The last conditions on the Meetings port (spot-check of 9e062f980). Each test fails on that commit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import meeting_archive as ma
from agent_friday.services import meeting_capture as mc
from test_meeting_archive_critic_fixes import (A, B, _mgr, _store, env, proposals_db,  # noqa: F401
                                               record)

CAND = {"text": "Priya came up in a meeting.", "category": "relationship", "span": {"i": 1, "t": 20}}
ROOT = Path(__file__).resolve().parents[2]


def test_the_pending_names_are_sealed_dropped_on_delete_and_taken_only_after_the_write(env, monkeypatch):
    m = _mgr(monkeypatch)
    monkeypatch.setattr(mc, "_manager", m)
    live = record(A, state="transcribing")
    m._save(live)
    mc.people_purge({"Priya"}, set())
    raw = mc._pending_path().read_bytes()
    assert b"Priya" not in raw and b"priya" not in raw.lower(), "the file does not tie the name to the meeting in the clear"
    assert mc._pending_read() == {A["id"]: ["Priya"]}
    # the names are used up only once the settled record is written
    real = mc._write_enc

    def failing(path, data):
        if Path(path).name == "meeting.json.enc":
            raise OSError("disk full")
        return real(path, data)
    monkeypatch.setattr(mc, "_write_enc", failing)
    live["state"] = "transcribed"
    with pytest.raises(OSError):
        m._save(live)
    assert mc._pending_read() == {A["id"]: ["Priya"]}, "a failed write does not consume the names"
    monkeypatch.setattr(mc, "_write_enc", real)
    m._save(live)
    assert mc._pending_read() == {} and "Priya" not in json.dumps(mc._load(A["id"]))
    # a meeting deleted before it settled leaves no entry behind
    live2 = record(B, state="transcribing")
    m._save(live2)
    mc.people_purge({"Jonas"}, set())
    assert B["id"] in mc._pending_read()
    m.delete(B["id"])
    assert B["id"] not in mc._pending_read()


def test_a_meeting_that_settled_between_the_listing_and_the_note_is_redacted_at_once(env, monkeypatch):
    m = _mgr(monkeypatch)
    monkeypatch.setattr(mc, "_manager", m)
    m._save(record(A))                                          # already settled on disk
    monkeypatch.setattr(m, "list", lambda: [{"id": A["id"], "state": "transcribing"}])   # the listing was stale
    mc.people_purge({"Priya"}, set())
    assert "Priya" not in json.dumps(mc._load(A["id"])), "not left for a save that may never come (auto-notes off)"


def test_the_archive_copy_applies_no_global_list_of_forgotten_names(env, monkeypatch):
    from agent_friday.services import forget_person
    monkeypatch.setattr(forget_person, "forgotten_names", lambda: {"will", "may"})
    ma.index_meeting(record(A))
    text = " ".join(r["text"] for r in _store().q("SELECT text FROM blocks"))
    assert "I will ask Priya" in text and "[removed]" not in text


def test_forget_person_says_it_could_not_check_when_the_keystore_is_locked(env, monkeypatch, proposals_db):
    from agent_friday.services import credential_store as cs
    proposals_db.propose_from_meeting([CAND], A["id"], "2026-10-09", "m")

    def locked(blob):
        raise OSError("the keystore is locked")
    monkeypatch.setattr(cs, "unprotect", locked)
    with pytest.raises(proposals_db.SuggestionsLocked, match="could not check meeting suggestions"):
        proposals_db.people_find({"Priya"}, set())
    with pytest.raises(proposals_db.SuggestionsLocked):
        proposals_db.people_purge({"Priya"}, set())
    assert [p["text"] for p in proposals_db.pending()] == ["(locked)"], "a row that cannot be opened is not shown blank"


def test_the_control_byte_guard_covers_the_ui_mirror_and_the_served_page():
    src = (ROOT / "tests" / "unit" / "test_no_control_bytes_in_source.py").read_text(encoding="utf-8")
    assert '"ui_parts"' in src and "index.html" in src
    page = (ROOT / "static" / "meetings_ws.js").read_text(encoding="utf-8")
    assert "d.message" in page, "the page shows why a suggestion was not kept"
