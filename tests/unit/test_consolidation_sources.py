"""Consolidation keeps its sources (north star NS-15.9-1).

Overnight dreaming and the end-of-day summary point at the turns they came
from (message ids, not just the day), say what made them (a model summary is
marked as one, in the record and in the saved file), carry their principal
and are read back filtered by it, skip off-the-record and special-category
content, never change or delete a stored turn, and can be rebuilt from the
stored turns with the same result.

Red on 51a160fcb: items carried no message ids or principal, summaries were
not marked, rebuild_day did not exist, off the record still consolidated,
and a health sentence was filed as a suggestion.

The real ConversationMemory over an in-memory collection with Chroma's
interface (CI installs no Chroma), as in test_memory_truth. Synthetic text."""
from __future__ import annotations

import json
import re

import pytest

from agent_friday.services import core_memory as cm
from agent_friday.services import memory_dreaming as md

DAY = "2026-09-14"


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class FakeCollection:
    """Chroma's collection interface, as ConversationMemory uses it; every
    write and delete is recorded so a test can say none happened."""

    def __init__(self):
        self.rows = {}
        self.deleted = []
        self.updated = []

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = _words((query_texts or [""])[0])
        scored = sorted(((1.0 - len(q & _words(d)) / max(1, len(q)), i, d, m)
                         for i, (d, m) in self.rows.items()), key=lambda s: s[0])
        top = scored[:max(1, int(n_results))]
        return {"ids": [[s[1] for s in top]], "documents": [[s[2] for s in top]],
                "metadatas": [[dict(s[3]) for s in top]], "distances": [[s[0] for s in top]]}

    def get(self, ids=None, include=None, where=None, limit=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else list(self.rows)
        if where and "session_id" in where:
            keys = [k for k in keys if self.rows[k][1].get("session_id") == where["session_id"]]
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        self.updated.extend(ids)
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        self.deleted.extend(ids or [])
        for i in ids or []:
            self.rows.pop(str(i), None)


TURNS = [
    ("msg-a1", "user", "I prefer short morning briefings with the weather first.", "10:00"),
    ("msg-a2", "friday", "Noted, weather first from tomorrow.", "10:01"),
    ("msg-a3", "user", "We decided to sail Wren to Harwich on Saturday.", "11:00"),
    ("msg-a4", "user", "I always take my medication for anxiety at night.", "12:00"),
    ("msg-a5", "user", "I prefer short morning briefings with the weather first.", "15:00"),
    ("msg-a6", "user", "thanks", "15:01"),
]


@pytest.fixture
def mem(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    m = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    m._collection = FakeCollection()
    monkeypatch.setattr(m, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(m, "_shared_query_ok", lambda: False)
    assert m.available()
    for mid, role, text, hhmm in TURNS:
        assert m.index(text, role, session_id=DAY, timestamp=f"{DAY}T{hhmm}:00", msg_id=mid) == mid
    return m


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(md, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(md, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(md, "_enabled", lambda: True)
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    yield


# ── dreaming ────────────────────────────────────────────────────────────────

def test_consolidated_items_carry_their_message_ids_and_principal(mem):
    res = md.dream(DAY, memory=mem)
    assert res["ok"] and res["consolidated"], res
    stored = {mid for mid, *_ in TURNS}
    for item in res["consolidated"]:
        assert item["message_ids"], item
        assert set(item["message_ids"]) <= stored
        assert item["principal"] == "owner"
    briefing = [i for i in res["consolidated"] if "morning briefings" in i["text"]]
    assert len(briefing) == 1 and sorted(briefing[0]["message_ids"]) == ["msg-a1", "msg-a5"], \
        "a fact said twice is one item that names both turns"
    sail = [i for i in res["consolidated"] if "Harwich" in i["text"]]
    assert sail and sail[0]["message_ids"] == ["msg-a3"]
    for t in res["topics"]:
        assert set(t["message_ids"]) <= stored and t["principal"] == "owner"
    assert set(res["source_message_ids"]) == {"msg-a1", "msg-a2", "msg-a3", "msg-a4",
                                              "msg-a5", "msg-a6"}
    assert res["principal"] == "owner"


def test_the_saved_record_says_what_made_it_and_names_its_sources(mem):
    md.dream(DAY, memory=mem)
    text = (md.DREAMS_DIR / f"{DAY}.md").read_text(encoding="utf-8")
    assert "not a model summary" in text and "INFERRED" in text
    assert "msg-a3" in text, "the md names the messages an item came from"
    rec = json.loads((md.DREAMS_DIR / f"{DAY}.json").read_text(encoding="utf-8"))
    assert rec["principal"] == "owner" and rec["model_summary"] is False
    assert rec["made_by"] == "heuristic"
    assert all(i["message_ids"] for i in rec["consolidated"])
    [row] = md.recent_dreams()
    assert row["principal"] == "owner" and row["consolidated"] == rec["consolidated"]


def test_reads_are_filtered_by_principal(mem):
    md.dream(DAY, memory=mem)
    assert md.recent_dreams(principal="owner")
    assert md.recent_dreams(principal="guest") == []


def test_rebuild_day_reproduces_the_same_items(mem):
    first = md.dream(DAY, memory=mem)
    queued = sorted(p["text"] for p in cm.pending())
    again = md.rebuild_day(DAY, memory=mem)
    third = md.rebuild_day(DAY, memory=mem)
    for r in (again, third):
        assert r["ok"] and r["rebuilt"] is True
        assert r["consolidated"] == first["consolidated"]
        assert r["topics"] == first["topics"]
        assert r["source_message_ids"] == first["source_message_ids"]
    assert md.state()["total_dreams"] == 1, "one record per day"
    assert sorted(p["text"] for p in cm.pending()) == queued, \
        "a rebuild files nothing new for review"


def test_rebuild_day_recomputes_from_the_stored_turns(mem):
    md.dream(DAY, memory=mem)
    mem.index("My deadline is the end of October.", "user", session_id=DAY,
              timestamp=f"{DAY}T16:00:00", msg_id="msg-a7")
    r = md.rebuild_day(DAY, memory=mem)
    assert any(i["message_ids"] == ["msg-a7"] for i in r["consolidated"])


def test_no_source_is_changed_or_deleted(mem):
    before = {k: (v[0], dict(v[1])) for k, v in mem._collection.rows.items()}
    md.dream(DAY, memory=mem)
    md.rebuild_day(DAY, memory=mem)
    col = mem._collection
    assert col.deleted == [] and col.updated == []
    assert {k: (v[0], dict(v[1])) for k, v in col.rows.items()} == before
    # The view consolidation reads through refuses to change a source.
    view = md._ReadOnlySource(mem)
    assert view.recent(n=3)
    for name in ("delete", "supersede", "update", "index", "index_exchange"):
        with pytest.raises(md.SourceDeletionRefused):
            getattr(view, name)
    with pytest.raises(md.SourceDeletionRefused):
        md._assert_derived("conversation_memory")
    md._assert_derived("dreams")


def test_special_category_content_is_not_mined(mem):
    res = md.dream(DAY, memory=mem)
    assert not any("medication" in i["text"] or "anxiety" in i["text"]
                   for i in res["consolidated"])
    assert not any("medication" in p["text"] for p in cm.pending())


def test_off_the_record_nothing_is_consolidated(mem, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    res = md.dream(DAY, memory=mem)
    assert res.get("skipped") and res.get("reason") == "off_record"
    assert not (md.DREAMS_DIR / f"{DAY}.md").exists()
    assert cm.pending() == []


# ── review fixes ────────────────────────────────────────────────────────────

def test_rebuilding_a_day_with_no_turns_left_clears_its_record(mem):
    md.dream(DAY, memory=mem)
    assert (md.DREAMS_DIR / f"{DAY}.md").exists() and (md.DREAMS_DIR / f"{DAY}.json").exists()
    assert md.state()["total_dreams"] == 1
    mem._collection.rows.clear()                      # the day's turns are gone (forgotten)
    r = md.rebuild_day(DAY, memory=mem)
    assert r["ok"] and r["turns_reviewed"] == 0 and r["cleared"] is True
    assert md.state()["total_dreams"] == 0
    assert not (md.DREAMS_DIR / f"{DAY}.md").exists()
    assert not (md.DREAMS_DIR / f"{DAY}.json").exists()


def test_an_unreadable_store_is_not_an_empty_day(mem):
    md.dream(DAY, memory=mem)

    class Down:
        def available(self):
            return True

        def recent(self, n=20, roles=None):
            raise RuntimeError("store down")

    r = md.rebuild_day(DAY, memory=Down())
    assert r["ok"] and r["cleared"] is False
    assert md.state()["total_dreams"] == 1
    assert (md.DREAMS_DIR / f"{DAY}.md").exists() and (md.DREAMS_DIR / f"{DAY}.json").exists()


def test_a_refused_source_write_is_an_error_envelope_not_an_exception(mem, monkeypatch, caplog):
    before = {k: (v[0], dict(v[1])) for k, v in mem._collection.rows.items()}
    # A consolidation bug that reaches for a delete through the read-only view.
    monkeypatch.setattr(md, "_pull_turns", lambda source, day: source.delete(["msg-a1"]))
    with caplog.at_level("ERROR", logger="friday.dreaming"):
        r = md.dream(DAY, memory=mem)
        r2 = md.rebuild_day(DAY, memory=mem)
    for res in (r, r2):
        assert res["ok"] is False and res["reason"] == "source_deletion_refused"
        assert "delete" in res["error"]
    assert any(rec.levelname == "ERROR" for rec in caplog.records)
    assert {k: (v[0], dict(v[1])) for k, v in mem._collection.rows.items()} == before
    assert mem._collection.deleted == []


def test_the_read_only_view_is_an_allowlist(mem):
    view = md._ReadOnlySource(mem)
    assert view.available() and view.recent(n=2)
    assert isinstance(view.get_session(DAY), list)
    for name in ("search", "find_ids", "stats"):
        assert callable(getattr(view, name))
    # Anything not on the list is refused, the collection included.
    for name in ("_collection", "_inner", "frobnicate", "rebuild_index", "_set_supersession",
                 "persist_dir"):
        with pytest.raises(md.SourceDeletionRefused):
            getattr(view, name)
    with pytest.raises(md.SourceDeletionRefused):
        view.recent = lambda *a, **k: []


def test_the_migration_adds_only_the_missing_columns(tmp_path):
    import sqlite3
    conn = sqlite3.connect(str(md.DB_PATH))
    conn.execute("CREATE TABLE dreams(dream_id TEXT PRIMARY KEY, ts REAL, day TEXT, "
                 "turns_reviewed INTEGER, topics_json TEXT, consolidated_json TEXT, "
                 "pruned INTEGER, summary TEXT, principal TEXT)")
    conn.execute("INSERT INTO dreams(dream_id, ts, day, turns_reviewed, topics_json, "
                 "consolidated_json, pruned, summary, principal) VALUES "
                 "('d1', 1.0, '2026-09-01', 1, '[]', '[]', 0, 'old', NULL)")
    conn.commit()
    conn.close()
    for _ in range(2):                                 # and again: nothing to add, no error
        c = md._connect()
        cols = [r[1] for r in c.execute("PRAGMA table_info(dreams)").fetchall()]
        c.close()
        assert cols.count("principal") == 1 and cols.count("meta_json") == 1
    [row] = md.recent_dreams()
    assert row["summary"] == "old" and row["principal"] == "owner"


def test_a_day_skipped_off_the_record_is_consolidated_by_the_next_run(mem, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    assert md.dream(DAY, memory=mem)["reason"] == "off_record"
    assert md.skipped_days() == [DAY], "only the date is kept"
    assert not (md.DREAMS_DIR / f"{DAY}.md").exists()
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    r = md.dream("2026-09-20", memory=mem)             # the next night, on the record
    assert r["ok"] and r.get("caught_up") == [DAY]
    assert md.skipped_days() == []
    assert (md.DREAMS_DIR / f"{DAY}.md").exists()
    assert md.recent_dreams()[0]["day"] == DAY


def test_forgetting_a_person_reaches_the_dreams(mem):
    from agent_friday.services import forget_registry as fr
    mem.index("I prefer meeting Priya Shah on Monday mornings.", "user", session_id=DAY,
              timestamp=f"{DAY}T17:00:00", msg_id="msg-p1")
    res = md.dream(DAY, memory=mem)
    assert any("Priya" in i["text"] for i in res["consolidated"])
    assert "dreams" in fr.registered()

    names = {"priya shah", "priya"}
    assert md.people_find(names, set()) == 1
    assert md.people_purge(names, set()) == 1
    assert md.people_find(names, set()) == 0
    [row] = md.recent_dreams()
    assert not any("priya" in i["text"].lower() for i in row["consolidated"])
    assert not any("priya" in t["topic"].lower() for t in row["topics"])
    assert any("Harwich" in i["text"] for i in row["consolidated"]), "the rest of the day stays"
    for ext in ("md", "json"):
        text = (md.DREAMS_DIR / f"{DAY}.{ext}").read_text(encoding="utf-8")
        assert "priya" not in text.lower(), ext
        assert "Harwich" in text


# ── the end-of-day summary ──────────────────────────────────────────────────

def _summarise(monkeypatch, tmp_path, mem):
    from agent_friday.services import model_router as mr
    monkeypatch.setattr(mr, "SESSION_SUMMARY_DIR", tmp_path / "session_summaries")
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: mem)
    monkeypatch.setattr(mr, "_get_friday_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(mr, "_predict_route_provider", lambda *a, **k: "local")
    monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(mr, "_generate_text",
                        lambda messages, system=None, **kw: "I helped them plan the sail to Harwich.")
    return mr, mr._generate_session_summary(DAY, force=True)


def test_the_day_summary_is_marked_a_model_summary_with_its_turns(mem, tmp_path, monkeypatch):
    mr, out = _summarise(monkeypatch, tmp_path, mem)
    assert out == "I helped them plan the sail to Harwich."
    raw = (tmp_path / "session_summaries" / f"{DAY}.md").read_text(encoding="utf-8")
    assert raw.startswith(mr.MODEL_SUMMARY_MARKER), "the saved md says a model wrote it"
    assert "Model summary" in raw
    meta = json.loads((tmp_path / "session_summaries" / f"{DAY}.json").read_text(encoding="utf-8"))
    assert meta["model_summary"] is True and meta["principal"] == "owner"
    assert sorted(meta["message_ids"]) == sorted(mid for mid, *_ in TURNS)
    # The summary text itself is unchanged when read back.
    assert mr._load_session_summary(DAY) == out
    assert mem._collection.deleted == [] and mem._collection.updated == []


def test_the_latest_summary_is_the_owners(mem, tmp_path, monkeypatch):
    mr, _ = _summarise(monkeypatch, tmp_path, mem)
    d = tmp_path / "session_summaries"
    (d / "2026-09-15.md").write_text("Someone else's day.", encoding="utf-8")
    (d / "2026-09-15.json").write_text(json.dumps({"principal": "guest"}), encoding="utf-8")
    date, text = mr._latest_session_summary()
    assert date == DAY and "Harwich" in text
