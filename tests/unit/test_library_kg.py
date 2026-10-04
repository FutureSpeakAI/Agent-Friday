"""The knowledge graph learns from Library documents only when the owner chose
it, only from the open shelf, always as private text, and forgets what a
forgotten document taught it."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.library_fixtures import (install_fake_encoder, isolate_library, release_library, write_docs)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    yield
    release_library(fg, lstore)


def _lib(tmp_path):
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, {"lease.txt": "Margaret Ellison owns the building on Elm Street.\n\nThe lease runs to 2030.",
                      "notes.txt": "A note about gardens."})
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    return st, root


def _choice(monkeypatch, value, **extra):
    from agent_friday.services.knowledge_graph import indexer as kg
    monkeypatch.setattr(kg, "_load_settings", lambda: {"library_kg_learn": value, **extra})
    return kg


def test_the_graph_reads_nothing_until_the_owner_says_so(tmp_path, monkeypatch):
    _lib(tmp_path)
    for value in ("", "off", None):
        kg = _choice(monkeypatch, value)
        assert kg.library_learning_on() is False and list(kg._library_chunks()) == []
    assert not any(c["id"].startswith("lib:") for c in _choice(monkeypatch, "off").gather_chunks({"wiki": False, "soul": False,
                                                                                               "cognitive": False, "conversations": False}))


def test_when_on_passages_are_offered_as_private_with_their_document(tmp_path, monkeypatch):
    st, _ = _lib(tmp_path)
    kg = _choice(monkeypatch, "on")
    chunks = list(kg._library_chunks())
    assert chunks and all(c["id"].startswith("lib:") and c["sensitivity"] == 2 for c in chunks)
    assert any("Margaret Ellison" in c["text"] for c in chunks)
    assert all(c["provenance"]["docs"] and c["provenance"]["sensitivity"] == 2 for c in chunks)
    assert all(Path(c["source_path"]).exists() for c in chunks)


def test_a_document_with_its_own_cloud_permission_is_the_only_public_one(tmp_path, monkeypatch):
    from agent_friday.services import file_grants as fg
    st, root = _lib(tmp_path)
    fg.create_file_grant(str(root / "notes.txt"))
    kg = _choice(monkeypatch, "on", library_cloud_answers=True)
    by_doc = {c["provenance"]["docs"][0]: c["sensitivity"] for c in kg._library_chunks()}
    notes = st.q("SELECT id FROM documents WHERE path LIKE '%notes.txt'")[0]["id"]
    assert by_doc[str(notes)] == 1 and 2 in by_doc.values()


def test_the_vault_shelf_is_never_offered_to_the_graph(tmp_path, monkeypatch):
    import os
    from agent_friday.services.library import grants, indexer, shelf
    from agent_friday.services.library.store import store_for
    st = store_for("owner")
    shelf.attach(st, os.urandom(32))
    root = tmp_path / "V"
    write_docs(root, {"secret.txt": "The diagnosis was confirmed in March."})
    grants.add_scope("owner", str(root))
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p), classify=lambda p, t, s: "vault")
    assert st.list_documents()[0]["shelf"] == "vault"
    kg = _choice(monkeypatch, "on")
    assert list(kg._library_chunks()) == []


def test_forgetting_a_document_takes_what_it_taught_the_graph(tmp_path, monkeypatch):
    from agent_friday.services.knowledge_graph import indexer as kg
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphManifest, KnowledgeGraphStore
    from agent_friday.services.library import forget
    st, root = _lib(tmp_path)
    doc = st.q("SELECT id FROM documents WHERE path LIKE '%lease.txt'")[0]["id"]
    d = str(doc)
    base = tmp_path / "kg"
    store = KnowledgeGraphStore(base)
    store.save("entities", [
        {"id": "ent_a", "title": "Margaret Ellison", "provenance": {"docs": [d], "sensitivity": 1}},
        {"id": "ent_b", "title": "Elm Street", "provenance": {"docs": [d, "999"], "sensitivity": 1}},
        {"id": "ent_c", "title": "Wiki thing", "provenance": {"wiki_pages": ["x.md"], "sensitivity": 1}}])
    store.save("relationships", [
        {"id": "r1", "source": "ent_a", "target": "ent_b", "provenance": {"docs": [d], "sensitivity": 1}},
        {"id": "r2", "source": "ent_b", "target": "ent_c", "provenance": {"docs": [d, "999"], "sensitivity": 1}}])
    monkeypatch.setattr(kg, "KnowledgeGraphStore", lambda: KnowledgeGraphStore(base))
    monkeypatch.setattr(kg, "KnowledgeGraphManifest", lambda: KnowledgeGraphManifest(base))
    out = forget.forget_document("owner", doc)
    assert out["ok"]
    ents = {e["id"]: e for e in KnowledgeGraphStore(base).load("entities")}
    assert "ent_a" not in ents and "ent_c" in ents
    assert ents["ent_b"]["provenance"]["docs"] == ["999"]
    rels = {r["id"] for r in KnowledgeGraphStore(base).load("relationships")}
    assert rels == {"r2"}


def test_library_documents_become_mentioned_in_nodes(tmp_path):
    from agent_friday.services.knowledge_graph import indexer as kg
    st, _ = _lib(tmp_path)
    doc = st.q("SELECT id FROM documents WHERE path LIKE '%lease.txt'")[0]["id"]
    ents = {"ent_x": {"id": "ent_x", "title": "Margaret Ellison", "provenance": {"docs": [str(doc)], "sensitivity": 2}}}
    nodes, rels = kg._link_to_library(ents)
    assert [n["title"] for n in nodes] == ["lease"] or nodes[0]["id"] == f"lib_{doc}"
    assert rels[0]["description"] == "mentioned in" and rels[0]["target"] == f"lib_{doc}"


def test_the_setup_question_names_a_recommendation_and_preselects_nothing():
    src = (Path(__file__).resolve().parents[2] / "static" / "library_ws.js").read_text(encoding="utf-8")
    i = src.index("const kgCard")
    card = src[i:i + 1200]
    assert card.count("(Recommended)") == 1 and "Yes, let it learn (Recommended)" in card
    assert "No, keep them separate" in card
    assert "active" not in card and "aria-pressed" not in card and "autoFocus" not in card


def test_the_status_reports_the_choice_so_the_question_is_asked_once(tmp_path, monkeypatch):
    from agent_friday.services.library import api
    st, _ = _lib(tmp_path)
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: {}, raising=False)
    assert api.status(st, "owner")["kg_learn"] == ""
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: {"library_kg_learn": "on"}, raising=False)
    assert api.status(st, "owner")["kg_learn"] == "on"
