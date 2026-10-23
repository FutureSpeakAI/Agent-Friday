"""A damaged search index is rebuilt from the transcripts; the transcripts are never touched (NS-32.6-1).

services/conversation_fts: a damaged index is found when it is read or written, moved aside (one
copy kept) and rebuilt from the conversations; the Doctor shows it and offers the rebuild as the
owner's press. The index file is damaged here by overwriting it with junk. On a tree without the
repair each test fails at the fixture with the reason.
"""
from __future__ import annotations

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversations as cv


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    if not hasattr(fts, "repair"):
        pytest.fail("a damaged search index is never rebuilt: conversation_fts.repair does not exist")
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "_LAST_AUTO", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    from agent_friday.services import recall_health
    recall_health._reset()
    yield tmp_path / "conversations"
    recall_health._reset()


def _say(cid, text):
    return cv.append(cid, {"role": "user", "text": text, "meta": {"kind": "turn", "via": "chat"}})


def _transcripts(root):
    return {p.name: p.read_bytes() for p in root.rglob("*") if p.is_file() and "index.sqlite" not in p.name}


def _damage(root):
    idx = root / "index.sqlite"
    assert idx.exists()
    for extra in root.glob("index.sqlite-*"):
        extra.unlink()
    idx.write_bytes(b"this is not a database at all " * 200)


def test_a_damaged_index_is_found_rebuilt_and_searchable_again(iso):
    c = cv.create("Depot")["id"]
    _say(c, "The depot closes in May, the council said.")
    fts.ensure_built()
    assert fts.search("depot")
    before = _transcripts(iso)
    _damage(iso)
    with pytest.raises(Exception):
        fts.search("depot")                       # this search cannot answer; the tool says so
    assert fts.health()["state"] == "ok"          # rebuilt (synchronously in tests)
    hits = fts.search("depot")
    assert hits and "council" in hits[0]["text"]
    assert _transcripts(iso) == before, "a transcript was changed by the rebuild"
    kept = list(iso.glob("index.sqlite.damaged-*"))
    assert len(kept) == 1                         # the damaged copy is kept once, for a look


def test_the_doctor_names_a_damaged_index(iso):
    from agent_friday.services import doctor
    c = cv.create("x")["id"]
    _say(c, "hello there friend")
    fts.ensure_built()
    assert doctor._c_search_index() == ("healthy", "ok")
    _damage(iso)
    assert doctor._c_search_index() == ("degraded", "conversation_search_index_damaged")


def test_an_index_not_built_yet_is_not_damaged(iso):
    from agent_friday.services import doctor
    assert fts.health()["state"] == "missing"
    assert doctor._c_search_index() == ("healthy", "not_built_yet")


def test_the_owners_rebuild_keeps_only_the_newest_damaged_copy(iso):
    c = cv.create("x")["id"]
    _say(c, "a message about the harbour")
    fts.ensure_built()
    old = iso / "index.sqlite.damaged-20200101-000000"
    old.write_bytes(b"old junk")
    out = fts.repair("owner:test")
    assert out["ok"] and out["indexed"] == 1
    kept = sorted(p.name for p in iso.glob("index.sqlite.damaged-*"))
    assert len(kept) == 1 and kept[0] != old.name
    assert fts.search("harbour")


def test_automatic_rebuilds_are_limited(iso, monkeypatch):
    calls = []
    monkeypatch.setattr(fts, "repair", lambda reason="owner": calls.append(reason) or {"ok": True})
    import sqlite3
    err = sqlite3.DatabaseError("database disk image is malformed")
    fts._auto_repair(err)
    fts._auto_repair(err)
    assert calls == ["damaged index found"]
    from agent_friday.services import recall_health
    assert recall_health.degraded() == ["conversation_search"]


def test_an_ordinary_error_is_not_treated_as_damage(iso, monkeypatch):
    import sqlite3
    calls = []
    monkeypatch.setattr(fts, "repair", lambda reason="owner": calls.append(reason) or {"ok": True})
    monkeypatch.setattr(fts, "_search", lambda *a, **k: (_ for _ in ()).throw(
        sqlite3.OperationalError("database is locked")))
    with pytest.raises(sqlite3.OperationalError):
        fts.search("anything")
    assert calls == []
