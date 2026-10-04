"""With the optional SQLCipher binding the whole Library index is encrypted at
rest, an existing plain index is encrypted in place, and without the binding the
index stays a plain file and says so."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("sqlcipher3")

from tests.library_fixtures import install_fake_encoder, isolate_library, make_pdf, release_library, write_docs  # noqa: E402

KEY = bytes(range(32))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    yield
    release_library(fg, lstore)


def _fill(st, text="Margaret Ellison signed the lease."):
    from agent_friday.services.library import structure
    blocks = [{"kind": "para", "text": text, "ord": 0, "page": 1, "bbox": [1, 2, 3, 4], "level": 0}]
    secs = structure.build_sections(blocks, "Lease")
    for s in secs:
        for p in structure.build_passages(s):
            p["section"] = s["id"]
    passages = [dict(p, section=s["id"]) for s in secs for p in structure.build_passages(s)]
    did = st.upsert_document("C:/x/lease.txt", sha256="a", size=1, mtime=1.0, kind="text", ext="txt", scope="C:/x",
                             folder_id=st.folder_id("C:/x", ""), title="Lease")
    st.replace_content(did, title="Lease", pages=1, shelf="open", blocks=blocks, sections=secs, passages=passages,
                       doc_profile="Lease", section_profiles={s["id"]: s["heading"] for s in secs})
    return did


def test_an_encrypted_index_shows_no_text_on_disk_and_reopens_with_its_key(tmp_path):
    from agent_friday.services.library.store import Store
    p = tmp_path / "idx" / "library.sqlite"
    st = Store(p, key=KEY)
    assert st.encrypted
    _fill(st)
    assert st.q("SELECT count(*) n FROM fts WHERE fts MATCH 'ellison'")[0]["n"] == 1
    st.compact()
    st.close()
    on_disk = b"".join(f.read_bytes() for f in p.parent.iterdir() if f.is_file())
    assert b"SQLite format 3" not in on_disk[:16] and b"Ellison" not in on_disk
    again = Store(p, key=KEY)
    assert again.encrypted and again.q("SELECT title FROM documents")[0]["title"] == "Lease"


def test_a_plain_index_is_encrypted_in_place_and_keeps_its_contents(tmp_path):
    from agent_friday.services.library.store import SCHEMA_VERSION, Store
    p = tmp_path / "idx" / "library.sqlite"
    plain = Store(p)
    assert not plain.encrypted
    _fill(plain)
    plain.compact()
    plain.close()
    assert b"Ellison" in p.read_bytes() or b"SQLite format 3" in p.read_bytes()[:16]
    enc = Store(p, key=KEY)
    assert enc.encrypted
    assert enc.q("SELECT count(*) n FROM passages")[0]["n"] == 1
    assert enc.q("SELECT count(*) n FROM fts WHERE fts MATCH 'ellison'")[0]["n"] == 1
    assert enc.q("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    enc.close()
    assert p.read_bytes()[:16] != b"SQLite format 3\x00"
    assert not list(p.parent.glob("*.enc"))


def test_a_lost_key_sets_the_index_aside_and_starts_fresh_with_a_note(tmp_path):
    from agent_friday.services.library.store import Store
    p = tmp_path / "idx" / "library.sqlite"
    a = Store(p, key=KEY)
    _fill(a)
    a.close()
    b = Store(p, key=bytes(reversed(KEY)))
    assert b.encrypted and b.q("SELECT count(*) n FROM documents")[0]["n"] == 0
    assert "set aside" in b.note and (p.parent / "library.sqlite.unreadable").exists()


def test_without_a_key_the_index_is_plain_and_the_status_says_so(tmp_path):
    from agent_friday.services.library import api
    from agent_friday.services.library.store import Store
    st = Store(tmp_path / "idx2" / "library.sqlite")
    assert api.status(st, "owner")["index_encrypted"] is False
    enc = Store(tmp_path / "idx3" / "library.sqlite", key=KEY)
    assert api.status(enc, "owner")["index_encrypted"] is True


def test_the_key_is_held_through_the_credential_store_and_never_replaced_when_unreadable(tmp_path, monkeypatch):
    from agent_friday.services import credential_store as cs
    from agent_friday.services.library import store as lstore
    monkeypatch.setattr(cs, "write_secret", lambda path, data: path.write_bytes(b"P" + data) or "test")
    monkeypatch.setattr(cs, "read_secret", lambda path: path.read_bytes()[1:])
    monkeypatch.setattr(cs, "harden_permissions", lambda path: None)
    d = tmp_path / "keys"
    k1 = lstore.index_key(d)
    assert len(k1) == 32 and lstore.index_key(d) == k1
    monkeypatch.setattr(cs, "read_secret", lambda path: (_ for _ in ()).throw(OSError("gone")))
    assert lstore.index_key(d) is None                      # not silently replaced: that would orphan the index
    assert (d / "index.key").read_bytes()[1:] == k1


def test_a_whole_index_and_search_runs_on_the_encrypted_store(tmp_path, monkeypatch):
    from agent_friday.services.library import grants, indexer, search, store as lstore
    monkeypatch.setattr(lstore, "index_key", lambda d: KEY)
    st = lstore.store_for("owner")
    assert st.encrypted
    root = tmp_path / "Lib"
    write_docs(root, {"a.pdf": make_pdf([["# Lease", "Either party may end the lease with ninety days notice."]])})
    grants.add_scope("owner", str(root))
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    res = search.run("how much notice to end the lease")
    assert res["evidence"] and "ninety" in res["evidence"][0]["text"]
