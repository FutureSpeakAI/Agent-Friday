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
        assert tombstones.add(kind, text)["ok"]
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
    assert not tombstones.is_tombstoned("kg_entity", texts["card_line"])
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
    assert cm.owner_delete("user", 0)["ok"]                                    # the delete still runs
