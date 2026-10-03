"""Vault shelf, remove, forget, the people path, principals and off the record."""
from __future__ import annotations

import os
import sqlite3

import pytest

from tests.library_fixtures import isolate_library, release_library, write_docs

LEASE = ("The tenant shall pay rent monthly to Margaret Ellison at the address given in schedule one. "
         "Either party may end the lease with ninety days notice in writing.")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    yield
    release_library(fg, lstore)


def _index(tmp_path, name="lease.txt", text=LEASE, shelf=None, key=None):
    from agent_friday.services.library import indexer, shelf as lshelf
    from agent_friday.services.library.store import store_for
    d = write_docs(tmp_path / "Lib", {name: text})
    st = store_for("owner")
    lshelf.attach(st, key)
    if key is None:
        st.set_sealer(None, None)
    r = indexer.index_file(st, d[name], tmp_path / "Lib", shelf=shelf)
    return st, r, d[name]


# -- test_library_vault_shelf_is_ciphertext_and_not_in_fts ------------------------

def test_vault_shelf_text_is_ciphertext_and_not_searchable(tmp_path):
    st, r, _ = _index(tmp_path, shelf="vault", key=os.urandom(32))
    assert r["state"] == "indexed"
    raw = st.q("SELECT text FROM blocks")[0]["text"]
    assert "tenant" not in raw and "Ellison" not in raw
    assert "tenant" not in st.q("SELECT text FROM passages")[0]["text"]
    assert st.q("SELECT count(*) n FROM fts")[0]["n"] == 0
    assert "Ellison" in st.dec(raw, "vault")
    db_bytes = st.path.read_bytes() + (st.path.parent / "library.sqlite-wal").read_bytes() \
        if (st.path.parent / "library.sqlite-wal").exists() else st.path.read_bytes()
    assert b"Margaret Ellison" not in db_bytes


def test_vault_shelf_is_unreadable_when_the_vault_is_locked(tmp_path):
    from agent_friday.services.library.store import VaultLocked
    st, r, _ = _index(tmp_path, shelf="vault", key=os.urandom(32))
    raw = st.q("SELECT text FROM blocks")[0]["text"]
    st.set_sealer(None, None)
    with pytest.raises(VaultLocked):
        st.dec(raw, "vault")


def test_a_sensitive_document_without_a_vault_key_is_not_stored_in_the_clear(tmp_path):
    st, r, _ = _index(tmp_path, shelf="vault", key=None)
    assert r["state"] == "skipped" and r["detail"] == "sensitive"
    assert st.q("SELECT count(*) n FROM blocks")[0]["n"] == 0
    assert st.get_document(r["doc_id"])["state"] == "skipped:sensitive"


def test_the_classifier_tiers_sensitive_text_onto_the_vault_shelf(tmp_path, monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc
    from agent_friday.services.library import shelf as lshelf
    monkeypatch.setattr(sc, "_embedding_tier", lambda text: (1, 0.0))     # no model load in a unit test
    assert lshelf.tier_of("Chart", "Patient SSN 123-45-6789 diagnosis and treatment plan") >= 2  # pragma: allowlist secret
    assert lshelf.tier_of("Notes", "The weather was mild and the picnic was pleasant.") == 1


# -- test_library_forget_purges_rows_vectors_fts_cache ----------------------------

def test_remove_purges_every_table_and_the_cache_but_not_the_file(tmp_path):
    from agent_friday.services.library import forget, store as lstore
    st, r, path = _index(tmp_path)
    doc = r["doc_id"]
    st.x("INSERT INTO vectors(node_kind,node_id,doc_id,vec) VALUES('passage',1,?,?)", (doc, b"\0" * 8))
    cache = lstore.principal_dir("owner") / "cache" / str(doc)
    cache.mkdir(parents=True)
    (cache / "1.webp").write_bytes(b"x")
    assert st.q("SELECT count(*) n FROM fts")[0]["n"] > 0
    assert forget.remove_document("owner", doc)["ok"]
    for t in ("blocks", "passages", "sections", "vectors", "profiles", "documents", "fts"):
        assert st.q(f"SELECT count(*) n FROM {t}")[0]["n"] == 0, t
    assert not cache.exists()
    assert path.exists()


def test_deleted_rows_are_overwritten_not_left_in_free_pages(tmp_path):
    from agent_friday.services.library import forget
    st, r, _ = _index(tmp_path)
    st.x("PRAGMA wal_checkpoint(TRUNCATE)")
    assert st.q("PRAGMA secure_delete")[0][0] == 1
    forget.remove_document("owner", r["doc_id"])
    st.x("PRAGMA wal_checkpoint(TRUNCATE)")
    assert b"Margaret Ellison" not in st.path.read_bytes()


def test_forget_tombstones_the_document_so_a_scope_does_not_bring_it_back(tmp_path):
    from agent_friday.services.library import forget, grants, indexer
    st, r, path = _index(tmp_path)
    forget.forget_document("owner", r["doc_id"])
    again = indexer.index_file(st, path, tmp_path / "Lib")
    assert again["state"] == "skipped" and again["detail"] == "forgotten"
    assert st.list_documents() == []
    assert forget.unforget("owner", str(path.resolve())) == 1
    assert indexer.index_file(st, path, tmp_path / "Lib")["state"] == "indexed"


def test_forget_rewrites_saved_chats_footnote_and_quote(tmp_path, monkeypatch):
    from agent_friday.services import conversations
    from agent_friday.services.library import forget
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "convs")
    st, r, _ = _index(tmp_path)
    doc = r["doc_id"]
    blk = st.q("SELECT id FROM blocks WHERE doc_id=?", (doc,))[0]["id"]
    quote = "The tenant shall pay rent monthly to Margaret Ellison at the address given in schedule one."
    conversations.create(cid="conv-a")
    m1 = conversations.append("conv-a", {"role": "assistant", "text":
                              f"The lease says:\n> {quote}\nSo rent is monthly [lib:{doc}#{blk}]. "
                              f"Another doc says otherwise [lib:999#1]."})
    m2 = conversations.append("conv-a", {"role": "user", "text": "thanks"})
    st.add_citation(blk, doc, "conv-a", m1["id"])
    out = forget.forget_document("owner", doc)
    assert out["ok"] and out["messages_rewritten"] == 1
    got = {m["id"]: m for m in conversations.messages("conv-a")}
    body = got[m1["id"]]["text"]
    assert forget.FORGOTTEN in body
    assert "Margaret Ellison" not in body and f"lib:{doc}#" not in body
    assert "[lib:999#1]" in body                       # other sources are untouched
    assert got[m2["id"]]["text"] == "thanks"
    assert st.q("SELECT count(*) n FROM cited_in")[0]["n"] == 0


# -- test_library_forget_registry_finds_named_person ------------------------------

def test_the_people_path_finds_and_purges_blocks_naming_a_person(tmp_path):
    from agent_friday.services import forget_registry
    st, r, _ = _index(tmp_path)
    _index(tmp_path, name="other.txt", text="A note about the weather and a picnic by the lake.")
    assert "library" in forget_registry.registered()
    found = forget_registry.find_all({"margaret ellison"}, set())
    assert found["library"] == 1
    purged = forget_registry.purge_all({"margaret ellison"}, set())
    assert purged["library"] == 1
    texts = " ".join(x["text"] for x in st.q("SELECT text FROM blocks"))
    assert "Ellison" not in texts and "picnic" in texts
    assert st.q("SELECT count(*) n FROM fts WHERE fts MATCH 'ellison'")[0]["n"] == 0


# -- test_library_principals_cannot_see_each_other --------------------------------

def test_principals_have_separate_files_and_cannot_reach_each_other(tmp_path):
    from agent_friday.services.library import cards, indexer, store as lstore
    from agent_friday.services.library.store import store_for
    a, b = store_for("owner"), store_for("guest")
    assert a.path != b.path and a.path.parent.name == "owner" and b.path.parent.name == "guest"
    d = write_docs(tmp_path / "Lib", {"lease.txt": LEASE})
    indexer.index_file(a, d["lease.txt"], tmp_path / "Lib")
    indexer.index_file(b, d["lease.txt"], tmp_path / "Lib")          # overlapping document
    write_docs(tmp_path / "Lib", {"secret.txt": "Owner only: the codename is Bluebird."})
    indexer.index_file(a, tmp_path / "Lib" / "secret.txt", tmp_path / "Lib")
    a.add_receipt("s1", {"menus": [1]})
    blk = a.q("SELECT id FROM blocks WHERE text LIKE '%Bluebird%'")[0]["id"]
    a.add_citation(blk, 2, "c1", "m1")
    # search, side channel, receipts, citations, blocks: none cross
    assert b.q("SELECT count(*) n FROM fts WHERE fts MATCH 'bluebird'")[0]["n"] == 0
    assert b.q("SELECT count(*) n FROM receipts")[0]["n"] == 0
    assert b.q("SELECT count(*) n FROM cited_in")[0]["n"] == 0
    assert b.q("SELECT count(*) n FROM vectors")[0]["n"] == 0
    assert b.one("SELECT id FROM blocks WHERE text LIKE '%Bluebird%'") is None
    assert cards.find_documents("guest", "secret") == []
    assert [x["title"] for x in a.list_documents()] == ["lease", "secret"]


def test_the_principal_is_never_an_argument_and_bad_names_are_refused(tmp_path):
    from agent_friday.services.library import principal, store as lstore
    for bad in ("../owner", "a/b", "", "OWNER", "x" * 60, "..", "a\\b"):
        with pytest.raises(ValueError):
            lstore.principal_dir(bad)
    assert principal.current() == "owner"           # outside a request: the owner's own process


def test_the_observer_has_no_library(tmp_path):
    from flask import Flask, g
    from agent_friday.services.library import principal
    app = Flask(__name__)
    with app.test_request_context("/"):
        g.friday_principal = "observer"
        assert principal.current() is None
    with app.test_request_context("/"):
        g.friday_principal = "user"
        assert principal.current() == "owner"


# -- test_library_off_record_writes_nothing ---------------------------------------

def test_off_the_record_no_receipts_no_citations_and_indexing_waits(tmp_path, monkeypatch):
    from agent_friday.services import off_record
    from agent_friday.services.library import runtime
    st, r, _ = _index(tmp_path)
    blk = st.q("SELECT id FROM blocks")[0]["id"]
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    st.add_receipt("s1", {"menus": [1]})
    st.add_citation(blk, r["doc_id"], "c1", "m1")
    assert st.q("SELECT count(*) n FROM receipts")[0]["n"] == 0
    assert st.q("SELECT count(*) n FROM cited_in")[0]["n"] == 0
    assert off_record.SKIPPED.get("library", 0) >= 2
    ok, why = runtime.machine_is_free()
    assert not ok and why == "off the record"
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    st.add_receipt("s2", {"menus": [1]})
    assert st.q("SELECT count(*) n FROM receipts")[0]["n"] == 1
