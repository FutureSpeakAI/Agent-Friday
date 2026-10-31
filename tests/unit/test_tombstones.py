"""What the owner deleted is not silently made again from a derived index.

North star NS-30.20-1: "Tombstones prevent silent re-creation of deleted
entities from derived indexes, with minimal PII."

For each kind the owner can delete, the delete runs through its real entry
point and then the derived path that used to make the thing again runs:

  card_line   core_memory.owner_delete, then nightly dreaming (the review
              queue), Friday's update_core_memory tool (add and replace) and
              the review pass's queue
  user_fact   user_model.forget_fact (POST /api/user-model/fact/delete), then
              note_fact from a dream and a proposal run reading it again
  proposal    memory_proposals.reject, then a later run proposing it again
              (other case and punctuation), a dream fact and a card suggestion
  kg_entity   knowledge_graph.integration.owner_delete_entity (POST
              /api/knowledge-graph/entity/delete), then a Tier B full rebuild
              and a Tier A rebuild from the same sources
  person, conversation_turn, library_document
              the existing tombstone lists, now answered through the one
              facade (services/tombstones), conversation memory included

and none of them comes back. The owner adding the thing again lifts the
tombstone and it works. The new list holds keyed hashes, never the text
(asserted on the file's bytes); normalisation (case, punctuation) still
matches; unrelated items are untouched; an unreadable list never breaks a
caller.

Red on the base: there is no services/tombstones and no owner_delete_entity;
dreaming re-queues a deleted card line, Friday's tool re-adds it, a deleted
fact is stored again from a dream, a rejected proposal reworded is proposed
again, and a deleted graph entity is rebuilt by the next pass.

Synthetic data. CI installs no Chroma: conversation memory runs over an
in-memory collection, as tests/unit/test_forget_reaches_everything.py does.
"""
from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest

DAY, DAY2 = "2026-10-20", "2026-10-21"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday import core
    from agent_friday.services import core_memory
    from agent_friday.services import memory_dreaming
    from agent_friday.services import memory_proposals
    from agent_friday.services import user_model
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)          # forget_person's list and its neighbours
    for mod in (user_model, memory_dreaming, memory_proposals):
        monkeypatch.setattr(mod, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(user_model, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(memory_dreaming, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(memory_dreaming, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(memory_dreaming, "_enabled", lambda: True)
    monkeypatch.setattr(memory_proposals, "DB_PATH", tmp_path / "dreams.db")
    # The one-time move of old facts onto the card is not what is tested here.
    (tmp_path / "core_memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "core_memory" / ".migrated_user_model_facts").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(core_memory._MIGRATION_CHECKED, str(tmp_path / "core_memory"), True)
    _reset()
    yield tmp_path
    _reset()


def _reset():
    try:
        from agent_friday.services import tombstones
    except ImportError:
        return
    tombstones._reset_for_tests()


def _norm(s):
    return " ".join(re.sub(r"[\W_]+", " ", str(s).casefold()).split())


class _Turns:
    """The slice of ConversationMemory nightly dreaming reads."""

    def __init__(self, texts, day=DAY):
        self.rows = [{"date": day, "role": "user", "text": t, "topic_keywords": ["morning"]}
                     for t in texts]

    def recent(self, n=20):
        return list(self.rows)


def _seat():
    from agent_friday.services import memory_proposals as mp
    return patch.object(mp, "seat", return_value={"assigned": True, "model": "gemma4:12b",
                                                  "provider": "ollama-local", "reason": ""})


def _propose(day, *texts):
    from agent_friday.services import memory_proposals as mp
    raw = json.dumps([{"category": "workflow", "text": t, "evidence": "said so"} for t in texts])
    turns = [{"role": "user", "text": "a day of work on builds and trains"}] * 3
    with _seat(), patch.object(mp, "_ask_seat", return_value=raw), \
            patch("agent_friday.services.memory_dreaming._pull_turns", return_value=(turns, False)):
        return mp.propose(day)


def _fact_texts():
    from agent_friday.services import user_model
    return [f["text"] for f in user_model._recent_facts(200)]


# ── card_line ────────────────────────────────────────────────────────────────
def test_a_deleted_card_line_is_not_suggested_again_by_the_nightly_pass(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import memory_dreaming as md
    assert cm.add("user", "I prefer tea in the morning.", actor="owner")["ok"]
    assert cm.owner_delete("user", 0)["ok"]
    assert cm.entries("user") == []

    # The same sentence, said again in other case and punctuation, and an
    # unrelated one: the nightly pass reads both.
    out = md.dream(DAY, memory=_Turns(["I prefer TEA in the morning! I always run on Sundays."]))
    assert out["ok"], out
    waiting = [_norm(r.get("text")) for r in cm.pending()]
    assert "i prefer tea in the morning" not in waiting, \
        "nightly dreaming suggested the line the owner deleted for the card again"
    assert "i always run on sundays" in waiting, "an unrelated suggestion was held back"


def test_friday_does_not_put_a_deleted_line_back_with_her_tool(home, monkeypatch):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import core_memory_tools as cmt
    monkeypatch.setattr(cmt, "_cloud_reader", lambda: False)
    monkeypatch.setattr(cmt, "_owner_decided", lambda: True)     # her save would otherwise go straight in
    assert cm.add("user", "Works the night desk at the paper.", actor="owner")["ok"]
    assert cm.owner_delete("user", 0)["ok"]

    said = cmt._tool_update_core_memory({"action": "add", "target": "user",
                                         "text": "works the night-desk at the paper"})
    assert cm.entries("user") == [], "Friday's tool wrote the deleted line back"
    assert "NOT SAVED" in said

    # Rewriting another line into the deleted one is the same re-creation.
    cm.add("user", "Likes jazz.", actor="owner")
    cmt._tool_update_core_memory({"action": "replace", "target": "user", "old": "jazz",
                                  "text": "Works the night desk at the paper."})
    assert cm.entries("user") == ["Likes jazz."]

    # The review pass cannot queue it for the owner either.
    r = cm.queue("user", "add", text="Works the night desk at the paper", source="review")
    assert not r.get("ok") and r.get("tombstoned")
    assert cm.pending() == []

    # Unrelated: her ordinary save still works.
    cmt._tool_update_core_memory({"action": "add", "target": "user", "text": "Has two cats."})
    assert "Has two cats." in cm.entries("user")


def test_the_owner_adding_a_deleted_line_again_lifts_its_tombstone(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import tombstones
    line = "Lead with the answer, then the source."
    assert cm.add("friday", line, actor="owner")["ok"]
    assert cm.owner_delete("friday", 0)["ok"]
    assert tombstones.is_tombstoned("card_line", "lead with the answer then the source")
    assert not cm.add("friday", line, actor="friday")["ok"]

    r = cm.add("friday", line, actor="owner")
    assert r["ok"] and not r.get("unchanged")
    assert cm.entries("friday") == [line]
    assert not tombstones.is_tombstoned("card_line", line)
    # From now on Friday may write it as before (she removes it, then adds it).
    assert cm.remove("friday", "Lead with", actor="friday")["ok"]
    assert cm.add("friday", line, actor="friday")["ok"]


def test_an_owner_edit_tombstones_the_old_wording_and_undo_of_fridays_line_sticks(home):
    from agent_friday.services import core_memory as cm
    assert cm.add("user", "Lives in Leeds.", actor="owner")["ok"]
    assert cm.owner_set("user", 0, "Lives in York.")["ok"]
    assert not cm.add("user", "lives in leeds", actor="friday")["ok"]
    # Friday's own add, undone by the owner, is not made again by her.
    r = cm.add("user", "Owns a red bicycle.", actor="friday")
    assert r["ok"]
    assert cm.undo(r["change_id"])["ok"]
    assert not cm.add("user", "Owns a red bicycle.", actor="friday")["ok"]
    assert cm.entries("user") == ["Lives in York."]


# ── user_fact ────────────────────────────────────────────────────────────────
def test_a_deleted_fact_is_not_stored_again_from_a_dream_or_proposed_again(home):
    from agent_friday.services import tombstones
    from agent_friday.services import user_model as um
    fid = um.note_fact("preference", "Takes the 7:40 train to work.", source="manual")["fact_id"]
    assert um.forget_fact(fid)["ok"]

    again = um.note_fact("preference", "takes the 7:40 train to work", source="dream:%s" % DAY)
    assert not again["ok"], "a dream stored the fact the owner deleted again"
    assert all(_norm(t) != "takes the 7 40 train to work" for t in _fact_texts())

    out = _propose(DAY, "Takes the 7:40 train to work.", "Reads on the train home.")
    assert out["stored"] == 1, "a proposal run proposed the deleted fact again"

    # Unrelated derived facts are still stored.
    assert um.note_fact("preference", "Reads on the train home.", source="dream:%s" % DAY)["ok"]

    # The owner says it again: stored, and the tombstone is gone.
    assert um.note_fact("preference", "Takes the 7:40 train to work.", source="manual")["ok"]
    assert "Takes the 7:40 train to work." in _fact_texts()
    assert not tombstones.is_tombstoned("user_fact", "Takes the 7:40 train to work.")


# ── proposal ─────────────────────────────────────────────────────────────────
def test_a_rejected_proposal_is_not_proposed_or_stored_again(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    from agent_friday.services import user_model as um
    assert _propose(DAY, "The user commits one build at a time.")["stored"] == 1
    fid = mp.pending()[0]["fact_id"]
    assert mp.reject([fid])["rejected"] == 1

    out = _propose(DAY2, "the user commits ONE build at a time!", "The user names builds after rivers.")
    assert out["stored"] == 1, "a reworded rejected proposal was proposed again"
    assert [_norm(p["text"]) for p in mp.pending()] == ["the user names builds after rivers"]

    # Nightly dreaming's routes for the same statement are closed too.
    assert not um.note_fact("workflow", "The user commits one build at a time.",
                            source="dream:%s" % DAY2)["ok"]
    assert not cm.queue("user", "add", text="The user commits one build at a time.",
                        source="dream:%s" % DAY2).get("ok")

    # The owner puts it on the card themselves: that is their word, and it holds.
    r = cm.add("user", "The user commits one build at a time.", actor="owner")
    assert r["ok"] and not r.get("unchanged")
    assert not tombstones.is_tombstoned("proposal", "The user commits one build at a time.")


# ── kg_entity ────────────────────────────────────────────────────────────────
_EXTRACT = ('("entity"<|>BOB<|>person<|>Bob leads the work.)\n##\n'
            '("entity"<|>{atlas}<|>project<|>The Atlas migration.)\n##\n'
            '("relationship"<|>BOB<|>{atlas}<|>Bob leads it<|>8)\n<|COMPLETE|>')


class _LLM:
    def __init__(self, atlas="PROJECT ATLAS"):
        self.extraction = _EXTRACT.format(atlas=atlas)

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        return self.extraction if "-Goal-" in messages[0]["content"] else "merged description"


def test_a_deleted_graph_entity_is_not_rebuilt_by_the_next_pass(home, monkeypatch):
    from agent_friday.services.knowledge_graph import indexer, integration
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    chunks = [{"id": "c1", "source_path": "wiki/atlas.md", "sensitivity": 1,
               "text": "Bob leads the Atlas migration.", "provenance": {"sensitivity": 1}}]
    monkeypatch.setattr(indexer, "gather_chunks", lambda: chunks)
    store = KnowledgeGraphStore(base_dir=home / "kg")
    indexer.reindex_tier_b(store=store, mode="full", llm=_LLM())
    ents = {e["title"]: e for e in store.load("entities")}
    assert {"Bob", "Project Atlas"} <= set(ents)
    atlas = ents["Project Atlas"]["id"]

    r = integration.owner_delete_entity(atlas, store=store)
    assert r["ok"] and r["entities"] == 1
    assert "Project Atlas" not in {e["title"] for e in store.load("entities")}

    # The same sources, a full rebuild, and a spelling that differs only in
    # punctuation: none of it brings the entity or its edge back.
    for llm in (_LLM(), _LLM("PROJECT-ATLAS")):
        indexer.reindex_tier_b(store=store, mode="full", llm=llm)
        titles = {_norm(e["title"]) for e in store.load("entities")}
        assert "project atlas" not in titles, "the rebuild re-created the deleted entity"
        assert "bob" in titles, "an unrelated entity was dropped"
        ids = {e["id"] for e in store.load("entities")}
        assert all(rel["source"] in ids and rel["target"] in ids
                   for rel in store.load("relationships"))

    # The owner's undo: the next rebuild may derive it again.
    assert integration.owner_restore_entity("Project Atlas")["removed"] == 1
    indexer.reindex_tier_b(store=store, mode="full", llm=_LLM())
    assert "Project Atlas" in {e["title"] for e in store.load("entities")}


def test_a_deleted_page_entity_stays_out_of_the_structural_rebuild(home):
    from agent_friday.services.knowledge_graph import integration, wiki_graph
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    wiki = home / "wiki"
    wiki.mkdir()
    (wiki / "atlas.md").write_text("# Project Atlas\n\nThe Atlas migration.\n", encoding="utf-8")
    (wiki / "bob.md").write_text("# Bob\n\nBob leads [[atlas]].\n", encoding="utf-8")
    store = KnowledgeGraphStore(base_dir=home / "kg")
    wiki_graph.rebuild_tier_a(store=store, wiki_dir=wiki)
    ents = {e["title"]: e for e in store.load("entities")}
    assert "Project Atlas" in ents and "Bob" in ents

    assert integration.owner_delete_entity(ents["Project Atlas"]["id"], store=store)["ok"]
    wiki_graph.rebuild_tier_a(store=store, wiki_dir=wiki)
    titles = {e["title"] for e in store.load("entities")}
    assert "Project Atlas" not in titles and "Bob" in titles
    assert (wiki / "atlas.md").exists(), "the owner's own page is never touched"


# ── the existing lists, through the one facade ──────────────────────────────
def test_people_turns_and_library_documents_are_answered_by_the_facade(home):
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import tombstones
    (home / "forgotten-people.json").write_text(json.dumps({"forgotten": [
        {"name": "Dana Okafor", "aliases": ["Dana"], "emails": [],
         "forgotten_at": "2026-10-01T09:00:00+00:00"}]}), encoding="utf-8")
    assert tombstones.is_tombstoned("person", "dana okafor")
    assert not tombstones.is_tombstoned("person", "Sam Reyes")

    assert tombstones.add("conversation_turn", "m-gone")["ok"]
    assert fts.is_forgotten("m-gone") and not fts.is_forgotten("m-kept")
    assert tombstones.is_tombstoned("conversation_turn", "m-gone")
    assert not tombstones.remove("conversation_turn", "m-gone")["ok"]       # no undo for a purged turn

    from agent_friday.services.library import store as lstore
    st = lstore.store_for(lstore.OWNER)
    st.x("INSERT OR REPLACE INTO tombstones(sha256, path, ts) VALUES(?,?,?)",
         ("ab" * 32, "C:/notes/old.pdf", 1.0))
    assert tombstones.is_tombstoned("library_document", "ab" * 32)
    assert tombstones.remove("library_document", "C:/notes/old.pdf")["removed"] == 1
    assert not tombstones.is_tombstoned("library_document", "ab" * 32)

    listed = tombstones.list()
    assert listed["kinds"]["person"]["count"] == 1
    assert listed["kinds"]["conversation_turn"]["count"] == 1
    said = json.dumps(listed)
    assert "Dana" not in said and "m-gone" not in said, "the list shows more than counts and dates"
    assert tombstones.remove("person", "Dana Okafor")["removed"] == 1
    assert not tombstones.is_tombstoned("person", "Dana Okafor")


class FakeCollection:
    """An in-memory collection (CI installs no Chroma), as in
    tests/unit/test_forget_reaches_everything.py."""

    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def get(self, ids=None, include=None, where=None, limit=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else [k for k in self.rows]
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


def test_conversation_memory_does_not_index_a_forgotten_turn_again(home, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import tombstones
    mem = ConversationMemory(persist_dir=home / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    assert tombstones.add("conversation_turn", "m-1")["ok"]
    assert mem.index("Dinner with the neighbours on Friday.", "user", msg_id="m-1") is None
    assert mem.index("The boiler service is due in May.", "user", msg_id="m-2")
    assert set(mem._collection.rows) and all("m-1" not in k for k in mem._collection.rows)


# ── minimal PII, normalisation, never raising ────────────────────────────────
def test_the_list_holds_no_text_and_normalisation_still_matches(home):
    from agent_friday.services import tombstones
    texts = {"card_line": "Allergic to penicillin since 2009.",
             "user_fact": "Banks with Northwind Credit Union.",
             "proposal": "The user is seeing Dr. Ellery on Tuesdays.",
             "kg_entity": "Ellery Clinic"}
    for kind, text in texts.items():
        ident = {"type": "organization", "title": text} if kind == "kg_entity" else text
        assert tombstones.add(kind, ident)["ok"]
    raw = (home / "tombstones.json").read_bytes()
    for text in texts.values():
        for form in (text, text.lower(), _norm(text)):
            assert form.encode("utf-8") not in raw, "the tombstone list holds the text itself"
        for word in re.findall(r"[A-Za-z]{5,}", text):
            assert word.lower().encode("utf-8") not in raw.lower()
    rows = json.loads(raw.decode("utf-8"))["entries"]
    assert all(set(r) == {"kind", "h", "day"} for r in rows)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["day"]) for r in rows)

    # Case, punctuation, a bullet and invisible characters do not hide it ...
    assert tombstones.is_tombstoned("card_line", "- allergic to PENICILLIN, since 2009")
    assert tombstones.is_tombstoned("card_line", "Allergic\u200b to penicillin since 2009")
    # ... a different statement, or the same text as another kind, is not it.
    assert not tombstones.is_tombstoned("card_line", "Allergic to penicillin since 2010.")
    assert not tombstones.is_tombstoned("kg_entity", {"type": "organization", "title": texts["card_line"]})
    assert tombstones.is_tombstoned("kg_entity", {"type": "organization", "title": "ELLERY-CLINIC"})
    assert tombstones.is_tombstoned(tombstones.STATEMENT_KINDS, texts["user_fact"])

    listed = json.dumps(tombstones.list())
    assert all(t not in listed for t in texts.values())
    assert json.loads(listed)["kinds"]["card_line"]["count"] == 1


def test_an_unreadable_list_never_breaks_a_caller_and_is_not_overwritten(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import tombstones
    assert tombstones.add("card_line", "Keeps bees.")["ok"]
    (home / "tombstones.json").write_text("{not json", encoding="utf-8")
    tombstones._reset_for_tests()
    assert tombstones.is_tombstoned("card_line", "Keeps bees.") is False      # fails open
    assert cm.add("user", "Keeps bees.", actor="friday")["ok"]                 # memory keeps working
    r = tombstones.add("card_line", "Grows tomatoes.")
    assert not r["ok"]
    assert (home / "tombstones.json").read_text(encoding="utf-8") == "{not json"
    gone = cm.owner_delete("user", 0)                                          # the delete still runs ...
    assert gone["ok"] and gone["tombstoned"] is False and gone["warning"]     # ... and says it is not held back


# ── review fixes: what a graph tombstone names ───────────────────────────────
def _kg_store(home, ents, reports=()):
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore(base_dir=home / "kg")
    store.save("entities", [dict(e, provenance={"sensitivity": 1}) for e in ents])
    if reports:
        store.save("community_reports", [dict(r) for r in reports])
    return store


def test_c_and_c_plus_plus_are_different_entities(home):
    from agent_friday.services.knowledge_graph import integration
    langs = [("e-c", "C"), ("e-cpp", "C++"), ("e-cs", "C#"), ("e-net", ".NET")]
    ents = [{"id": i, "title": t, "type": "tool"} for i, t in langs]
    store = _kg_store(home, ents)
    r = integration.owner_delete_entity("e-c", store=store)
    assert r["ok"] and r["entities"] == 1
    assert r["deleted"] == [{"id": "e-c", "title": "C", "type": "tool"}]
    assert {e["title"] for e in store.load("entities")} == {"C++", "C#", ".NET"}
    # A rebuild that writes every one of them again: only C is left out, and
    # the save says so as a count by kind (no title, no id).
    saved = store.save("entities", [dict(e, provenance={"sensitivity": 1}) for e in ents])
    assert saved["tombstoned"] == {"tool": 1}
    assert {e["title"] for e in store.load("entities")} == {"C++", "C#", ".NET"}


def test_deleting_the_person_mercury_keeps_the_page_mercury(home):
    from agent_friday.services.knowledge_graph import integration
    store = _kg_store(home, [{"id": "p-1", "title": "Mercury", "type": "person"},
                             {"id": "p-2", "title": "MERCURY", "type": "person"},    # the same thing twice
                             {"id": "pg-1", "title": "Mercury", "type": "page"},
                             {"id": "o-1", "title": "Venus", "type": "person"}])
    r = integration.owner_delete_entity("p-1", store=store)
    assert r["ok"] and sorted(d["id"] for d in r["deleted"]) == ["p-1", "p-2"]
    left = {(e["type"], e["title"]) for e in store.load("entities")}
    assert left == {("page", "Mercury"), ("person", "Venus")}
    from agent_friday.services import tombstones
    assert tombstones.is_tombstoned("kg_entity", {"type": "person", "title": "mercury"})
    assert not tombstones.is_tombstoned("kg_entity", {"type": "page", "title": "Mercury"})
    # The owner's undo, by what the delete returned.
    assert integration.owner_restore_entity("Mercury", type="person")["removed"] == 1


def test_two_facts_that_open_with_the_same_eight_words_are_two_facts(home, monkeypatch):
    from agent_friday.services.knowledge_graph import integration
    from agent_friday.services.knowledge_graph import store as kg_store
    monkeypatch.setattr(kg_store, "KG_DIR", home / "kg")
    monkeypatch.setattr(integration, "kg_settings", lambda: {"enabled": True})
    one = "The user keeps the spare house key under the blue flowerpot by the door."
    two = "The user keeps the spare house key under the mat at the back."
    assert one.split()[:8] == two.split()[:8]
    f1 = integration.ingest_fact(one, source_kind="conversation", source_key="proposed:x", sensitivity=1)
    f2 = integration.ingest_fact(two, source_kind="conversation", source_key="proposed:x", sensitivity=1)
    assert f1 and f2 and f1 != f2
    store = kg_store.KnowledgeGraphStore()

    r = integration.owner_delete_entity(f1, store=store)
    assert r["ok"] and [d["id"] for d in r["deleted"]] == [f1]
    ids = {e["id"] for e in store.load("entities")}
    assert f1 not in ids and f2 in ids, "the other owner-approved fact was dropped"
    # A live fact cannot ignite the deleted one again; the other still saves.
    assert integration.ingest_fact(one, source_kind="conversation", source_key="dream:x", sensitivity=1) is None
    assert integration.ingest_fact(two, source_kind="conversation", source_key="dream:x", sensitivity=1) == f2
    saved = store.save("entities", store.load("entities"))
    assert "tombstoned" not in saved and f2 in {e["id"] for e in store.load("entities")}
    assert integration.owner_restore_entity(node_id=f1)["removed"] == 1


def test_deleting_an_entity_scrubs_its_title_from_the_community_reports(home):
    from agent_friday.services.knowledge_graph import integration
    reports = [{"community": "c1", "title": "Atlas work",
                "summary": "Project Atlas is led by Bob Hale; the project atlas team ships weekly.",
                "findings": [{"explanation": "Bob Hale reviews Project Atlas releases."}]},
               {"community": "c2", "title": "Garden", "summary": "Tomatoes and bees."}]
    store = _kg_store(home, [{"id": "a", "title": "Project Atlas", "type": "project"},
                             {"id": "b", "title": "Bob Hale", "type": "person"}], reports)
    r = integration.owner_delete_entity("a", store=store)
    assert r["ok"] and r["reports_scrubbed"] == 1
    said = json.dumps(store.load("community_reports"))
    assert "project atlas" not in said.lower()
    assert "Bob Hale" in said and "Tomatoes and bees." in said, "the rest of the cluster was lost"


# ── review fixes: a missing key ──────────────────────────────────────────────
def test_a_missing_key_beside_existing_tombstones_is_not_silently_replaced(home):
    from agent_friday.services import tombstones
    assert tombstones.add("card_line", "Keeps bees.")["ok"]
    (home / "tombstones.key").unlink()
    tombstones._reset_for_tests()
    r = tombstones.add("card_line", "Grows tomatoes.")
    assert not r["ok"] and r.get("key_missing")
    assert not (home / "tombstones.key").exists(), "a new key was made beside entries it cannot match"
    assert len(json.loads((home / "tombstones.json").read_text(encoding="utf-8"))["entries"]) == 1
    listed = tombstones.list()
    assert listed["key"] == "missing" and "missing" in listed["problem"]
    assert "bees" not in json.dumps(listed).lower()


def test_a_key_is_made_for_an_empty_list(home):
    from agent_friday.services import tombstones
    assert not (home / "tombstones.key").exists()
    assert tombstones.add("card_line", "Keeps bees.")["ok"]
    assert (home / "tombstones.key").exists()
    assert tombstones.list()["key"] == "ok"


# ── review fixes: the owner restating a deleted statement ────────────────────
def test_the_owner_restating_a_deleted_household_line_reaches_the_review_queue_flagged(home, monkeypatch):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import core_memory_tools as cmt
    from agent_friday.services import taint
    monkeypatch.setattr(cmt, "_active_profile", lambda: "owner")
    monkeypatch.setattr(cmt, "_minor_profile", lambda p: False)
    monkeypatch.setattr(taint, "ledger_key", lambda ctx: "conv-t")
    monkeypatch.setattr(taint, "read_source_of", lambda key, sent: "")
    said = "I am 52, my husband is 50, our son is 9. Please make a note of that."
    ctx = {"owner_surface": True, "conversation_id": "conv-t"}
    assert cmt.capture_household(said, ctx)["status"] == "saved"
    assert cm.owner_delete("user", 0)["ok"]

    # Words that are not the owner's own statement stay out, silently as before.
    assert cmt.capture_household(said, {"conversation_id": "conv-t"})["status"] == "none"
    assert cm.pending() == []
    # The owner says it again in their own words: not saved by Friday, but
    # waiting for their OK, flagged.
    out = cmt.capture_household(said, ctx)
    assert out["status"] == "queued"
    assert cm.entries("user") == []
    [row] = cm.pending()
    assert row.get("deleted_before") is True and row["reason"].startswith("You deleted this before.")


def test_a_rejected_proposal_blocks_a_proposal_but_not_the_owner_saying_it(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import memory_proposals as mp
    assert _propose(DAY, "The user is training for a marathon.")["stored"] == 1
    assert mp.reject([mp.pending()[0]["fact_id"]])["rejected"] == 1
    assert _propose(DAY2, "The user is training for a marathon!")["stored"] == 0
    assert cm.queue("user", "add", text="The user is training for a marathon.",
                    source="dream:%s" % DAY2).get("tombstoned")
    r = cm.queue("user", "add", text="The user is training for a marathon.", source="friday",
                 reason="The user said so.", restated=True)
    assert r["ok"] and r.get("deleted_before")
    assert [_norm(p["text"]) for p in cm.pending() if p.get("deleted_before")] == \
        ["the user is training for a marathon"]


# ── review fixes: discarding a queued suggestion ─────────────────────────────
def test_a_discarded_card_suggestion_is_not_suggested_again(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import memory_dreaming as md
    assert md.dream(DAY, memory=_Turns(["I always run on Sundays."]))["ok"]
    [row] = [r for r in cm.pending() if _norm(r.get("text")) == "i always run on sundays"]
    d = cm.decide(row["id"], False)
    assert d["ok"] and d["decided"] == "discarded" and "tombstoned" not in d
    assert all(r["id"] != row["id"] for r in cm.pending())
    assert md.dream(DAY2, memory=_Turns(["I always run on Sundays!"], day=DAY2))["ok"]
    assert all(_norm(r.get("text")) != "i always run on sundays" for r in cm.pending()), \
        "the nightly pass suggested the line the owner discarded again"
    # The owner may still put it on the card themselves.
    assert cm.add("user", "I always run on Sundays.", actor="owner")["ok"]


# ── review fixes: a tombstone that cannot be written is said so ──────────────
def test_every_owner_delete_says_when_its_tombstone_could_not_be_written(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    from agent_friday.services import user_model as um
    from agent_friday.services.knowledge_graph import integration
    assert cm.add("user", "Keeps bees.", actor="owner")["ok"]
    assert cm.add("user", "Likes jazz.", actor="owner")["ok"]
    fid = um.note_fact("preference", "Takes the 7:40 train to work.", source="manual")["fact_id"]
    assert _propose(DAY, "The user commits one build at a time.")["stored"] == 1
    pid = mp.pending()[0]["fact_id"]
    store = _kg_store(home, [{"id": "a", "title": "Project Atlas", "type": "project"}])

    (home / "tombstones.json").write_text("{not json", encoding="utf-8")       # no tombstone can be written
    tombstones._reset_for_tests()

    for r in (cm.remove("user", "Keeps bees", actor="owner"),
              cm.replace("user", "Likes jazz", "Likes blues.", actor="owner"),
              um.forget_fact(fid),
              mp.reject([pid])):
        assert r["ok"] and r["tombstoned"] is False and r["warning"], r
    kg = integration.owner_delete_entity("a", store=store)
    assert not kg["ok"] and kg["tombstoned"] is False and kg["warning"]
    assert [e["id"] for e in store.load("entities")] == ["a"], "deleted with nothing to stop the rebuild"

# ── owner ruling 2026-10-28: discarded suggestions, listed and undoable ──────
DAY3 = "2026-10-22"


def _discard_card_suggestion(home, said="I always run on Sundays.", day=DAY):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import memory_dreaming as md
    assert md.dream(day, memory=_Turns([said], day=day))["ok"]
    [row] = [r for r in cm.pending() if _norm(r.get("text")) == _norm(said)]
    d = cm.decide(row["id"], False)
    assert d["ok"] and d["decided"] == "discarded" and "tombstoned" not in d, d
    return row


def test_a_discarded_card_suggestion_is_listed_blocked_and_can_be_allowed_again(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import memory_dreaming as md
    from agent_friday.services import tombstones
    _discard_card_suggestion(home)

    # Listed for the owner, with its label opened.
    [item] = tombstones.list_discarded()
    assert item["kind"] == "card_line" and item["sealed"] is True
    assert _norm(item["label"]) == "i always run on sundays"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["day"])

    # Blocked: the nightly pass does not suggest it again.
    assert md.dream(DAY2, memory=_Turns(["I always run on Sundays!"], day=DAY2))["ok"]
    assert all(_norm(r.get("text")) != "i always run on sundays" for r in cm.pending())

    # Allow again: the tombstone goes, nothing is put back in the queue ...
    r = tombstones.undo_discard(item["id"])
    assert r["ok"] and r["removed"] == 1
    assert tombstones.list_discarded() == []
    assert all(_norm(p.get("text")) != "i always run on sundays" for p in cm.pending())
    assert not tombstones.is_tombstoned("card_line", "I always run on Sundays.")
    # ... and a later pass may suggest it again.
    assert md.dream(DAY3, memory=_Turns(["I always run on Sundays."], day=DAY3))["ok"]
    assert any(_norm(p.get("text")) == "i always run on sundays" for p in cm.pending()), \
        "a suggestion the owner allowed again was still held back"
    assert tombstones.undo_discard(item["id"])["ok"] is False      # gone: a second undo finds nothing


def test_a_rejected_proposal_is_listed_and_can_be_allowed_again(home):
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    assert _propose(DAY, "The user is training for a marathon.")["stored"] == 1
    assert mp.reject([mp.pending()[0]["fact_id"]])["rejected"] == 1
    [item] = tombstones.list_discarded()
    assert item["kind"] == "proposal" and _norm(item["label"]) == "the user is training for a marathon"
    assert _propose(DAY2, "The user is training for a marathon!")["stored"] == 0      # blocked
    assert tombstones.undo_discard(item["id"])["ok"]
    assert mp.pending() == [], "allowing it again does not restore the suggestion to the queue"
    assert _propose(DAY3, "The user is training for a marathon.")["stored"] == 1      # suggested again


def test_both_kinds_appear_in_one_list_newest_first(home):
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    _discard_card_suggestion(home)
    assert _propose(DAY, "The user commits one build at a time.")["stored"] == 1
    assert mp.reject([mp.pending()[0]["fact_id"]])["rejected"] == 1
    kinds = [i["kind"] for i in tombstones.list_discarded()]
    assert kinds == ["proposal", "card_line"]
    # An owner's own card delete is a tombstone, but not a discarded suggestion.
    from agent_friday.services import core_memory as cm
    assert cm.add("user", "Keeps bees.", actor="owner")["ok"]
    assert cm.owner_delete("user", 0)["ok"]
    assert len(tombstones.list_discarded()) == 2


def test_labels_are_sealed_at_rest(home):
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    _discard_card_suggestion(home)
    assert _propose(DAY, "The user is training for a marathon.")["stored"] == 1
    assert mp.reject([mp.pending()[0]["fact_id"]])["rejected"] == 1
    raw = (home / "tombstones.json").read_bytes()
    for word in ("Sundays", "sundays", "marathon", "training"):
        assert word.encode("utf-8") not in raw, "a discard label is readable at rest"
    rows = [r for r in json.loads(raw.decode("utf-8"))["entries"] if r.get("discard")]
    assert len(rows) == 2
    assert all(str(r.get("label") or "").startswith("enc:") for r in rows)
    assert all(re.fullmatch(r"[0-9a-f]{16}", r["id"]) for r in rows)
    # The counts-only view never shows a label.
    assert "marathon" not in json.dumps(tombstones.list()).lower()


def test_without_sealing_the_entry_is_hashed_only_and_listed_by_day(home, monkeypatch):
    from agent_friday.services import credential_store
    from agent_friday.services import tombstones

    def no_keystore(data):
        raise RuntimeError("no keystore")
    monkeypatch.setattr(credential_store, "protect", no_keystore)
    r = tombstones.add_discard("card_line", "Keeps a sourdough starter.")
    assert r["ok"] and r["sealed"] is False
    [item] = tombstones.list_discarded()
    assert item["label"] == "a discarded suggestion from %s" % item["day"] and item["sealed"] is False
    raw = (home / "tombstones.json").read_text(encoding="utf-8")
    assert "sourdough" not in raw and '"label"' not in raw
    assert tombstones.is_tombstoned("card_line", "Keeps a sourdough starter.")


def test_forgetting_a_person_purges_labels_that_name_them(home):
    from agent_friday.services import forget_registry as fr
    from agent_friday.services import tombstones
    assert tombstones.add_discard("card_line", "Dana Okafor runs the Thursday standup.")["ok"]
    assert tombstones.add_discard("proposal", "The user prefers tea in the morning.")["ok"]
    assert "tombstone_labels" in fr.registered()
    find, purge = fr._STORES["tombstone_labels"]
    assert find({"dana okafor"}, set()) == 1
    out = purge({"dana okafor"}, set())
    assert out["removed"] == 1
    labels = [i["label"] for i in tombstones.list_discarded()]
    assert not any("Dana" in x for x in labels)
    assert any(x.startswith("a discarded suggestion from ") for x in labels)
    assert any("prefers tea" in x for x in labels), "a label that does not name them was purged"
    # The keyed hash names nobody and keeps blocking.
    assert tombstones.is_tombstoned("card_line", "Dana Okafor runs the Thursday standup.")
    assert find({"dana okafor"}, set()) == 0


def _status_of(fact_id):
    from agent_friday.services import memory_proposals as mp
    conn = mp._connect()
    try:
        row = conn.execute("SELECT status, text, provenance FROM proposed_facts WHERE fact_id=?",
                           (fact_id,)).fetchone()
    finally:
        conn.close()
    return row


def _entries(home):
    return json.loads((home / "tombstones.json").read_text(encoding="utf-8"))["entries"]


def test_a_rejected_row_still_blocks_its_exact_text_until_allowed_again(home):
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    said = "The user bakes bread on Saturdays."
    assert _propose(DAY, said)["stored"] == 1
    fid = mp.pending()[0]["fact_id"]
    assert mp.reject([fid])["rejected"] == 1
    [item] = tombstones.list_discarded()
    # Even with the tombstone list lost, the rejected row itself holds the exact text back.
    saved = (home / "tombstones.json").read_text(encoding="utf-8")
    (home / "tombstones.json").write_text(json.dumps({"version": 1, "entries": []}), encoding="utf-8")
    tombstones._reset_for_tests()
    assert _propose(DAY2, said)["stored"] == 0, "a rejected row stopped blocking its text"
    (home / "tombstones.json").write_text(saved, encoding="utf-8")
    tombstones._reset_for_tests()
    # Allow again marks the row, and the same words may be suggested again.
    r = tombstones.undo_discard(item["id"])
    assert r["ok"] and r["allowed_again"] == 1, r
    assert _status_of(fid)[0] == "allowed_again"
    assert _propose(DAY3, said)["stored"] == 1


def _meeting_suggestion(text, meeting_id="meet-1"):
    from agent_friday.services import memory_proposals as mp
    span = {"i": 0, "t": 1.0}
    assert mp.propose_from_meeting([{"category": "project", "text": text, "span": span}],
                                   meeting_id, DAY, "gemma4:12b") == 1
    [row] = [p for p in mp.pending() if (p.get("provenance") or {}).get("meeting_id") == meeting_id]
    return row["fact_id"]


def test_a_rejected_meeting_suggestion_keeps_no_label_and_can_be_allowed_again(home):
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    said = "Project Larch ships in the spring."
    fid = _meeting_suggestion(said)
    assert mp.reject([fid])["rejected"] == 1
    [item] = tombstones.list_discarded()
    assert item["label"] == "a discarded meeting suggestion from %s" % item["day"], item
    assert item["sealed"] is False
    raw = (home / "tombstones.json").read_text(encoding="utf-8")
    assert '"label"' not in raw and "Larch" not in raw
    status, text, prov = _status_of(fid)
    assert status == "rejected" and text == ""
    assert re.fullmatch(r"[0-9a-f]{32}", json.loads(prov)["tomb"])          # a keyed hash, no words
    assert tombstones.is_tombstoned("proposal", said)
    r = tombstones.undo_discard(item["id"])
    assert r["ok"] and r["allowed_again"] == 1, r
    assert _status_of(fid)[0] == "allowed_again"
    assert not tombstones.is_tombstoned("proposal", said)


def test_deleting_a_meeting_drops_its_discard_labels_and_keeps_the_block(home):
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    said = "Project Larch ships in the spring."
    fid = _meeting_suggestion(said)
    assert mp.reject([fid])["rejected"] == 1
    # A label from before meeting suggestions stopped keeping one.
    entries = _entries(home)
    for e in entries:
        if e.get("discard"):
            e["label"] = tombstones._seal_label(said)
            e.pop("src", None)
    (home / "tombstones.json").write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    tombstones._reset_for_tests()
    assert "Larch" in tombstones.list_discarded()[0]["label"]
    assert mp.purge_meeting("meet-1") == 1
    [item] = tombstones.list_discarded()
    assert item["label"] == "a discarded meeting suggestion from %s" % item["day"]
    assert not any(e.get("label") for e in _entries(home))
    assert tombstones.is_tombstoned("proposal", said), "deleting the meeting lifted the owner's no"


def test_an_owner_delete_tombstone_is_never_turned_into_a_discard(home):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import tombstones
    assert cm.add("user", "Keeps bees.", actor="owner")["ok"]
    assert cm.owner_delete("user", 0)["ok"]
    before = _entries(home)
    r = tombstones.add_discard("card_line", "Keeps bees.")
    assert r["ok"] and r["id"] is None and r["listed"] is False, r
    assert _entries(home) == before, "the owner's delete tombstone was changed"
    assert tombstones.list_discarded() == []
    assert tombstones.is_tombstoned("card_line", "Keeps bees.")


def test_the_same_text_discarded_as_a_card_line_and_a_proposal_is_one_row_and_allowed_again_together(home):
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import tombstones
    said = "I always run on Sundays."
    assert _propose(DAY, said)["stored"] == 1
    fid = mp.pending()[0]["fact_id"]
    assert mp.reject([fid])["rejected"] == 1
    # The card's Discard of the same words (core_memory.decide calls this; a
    # real card suggestion of them is already held back by the proposal's no).
    assert tombstones.add_discard("card_line", said)["id"]
    [item] = tombstones.list_discarded()
    assert sorted(item["kinds"]) == ["card_line", "proposal"], item
    assert _norm(item["label"]) == "i always run on sundays"
    r = tombstones.undo_discard(item["id"])
    assert r["ok"] and r["removed"] == 2 and r["allowed_again"] == 1, r
    assert tombstones.list_discarded() == []
    assert not tombstones.is_tombstoned(tombstones.STATEMENT_KINDS, said)
    assert _status_of(fid)[0] == "allowed_again"


def test_undo_refuses_a_malformed_or_unknown_id(home):
    from agent_friday.services import tombstones
    assert tombstones.add_discard("card_line", "Keeps bees.")["ok"]
    for bad in ("", "../x", "zz" * 8, "0" * 40, None):
        assert tombstones.undo_discard(bad)["ok"] is False
    assert tombstones.undo_discard("0123456789abcdef")["ok"] is False
    assert len(tombstones.list_discarded()) == 1
    assert tombstones.add_discard("kg_entity", "x")["ok"] is False
