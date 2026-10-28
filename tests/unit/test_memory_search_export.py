"""The owner's memory export (services/memory_export; north star NS-15.11-1,
the knowledge workspace's "export").

With a query the export holds the same results the search panel shows; with
none, everything Friday keeps about the owner, kind by kind, bounded. JSON
carries kind/text/when/source/status; Markdown is grouped by kind. Nobody the
owner asked Friday to forget is in it, and no key or password, whatever store
the text came from.

The real ConversationMemory over an in-memory collection with Chroma's
interface (CI installs no Chroma), the real transcript, full-text index, card,
user model and proposal table, all under tmp_path. Synthetic text.

Red on 05049f218: services/memory_export did not exist (every test here fails
at import).
"""
from __future__ import annotations

import json
import re
import time
import uuid

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversations as cv
from agent_friday.services import core_memory as cm
from agent_friday.services import memory_export as me
from agent_friday.services import memory_influence as mi
from agent_friday.services import memory_proposals as mp
from agent_friday.services import memory_search as ms
from agent_friday.services import pasted_secrets
from agent_friday.services import prompt_freeze
from agent_friday.services import user_model as um

#: A key of a listed shape (services/secret_shapes, "anthropic"); synthetic.
KEY = "sk-ant-api03-" + "Q7w" * 12


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class FakeCollection:
    """Chroma's collection interface, as ConversationMemory uses it
    (the pattern from tests/unit/test_memory_search_and_why.py)."""

    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = _words((query_texts or [""])[0])
        scored = []
        for i, (d, m) in self.rows.items():
            overlap = len(q & _words(d)) / max(1, len(q))
            scored.append((1.0 - overlap, i, d, m))
        scored.sort(key=lambda s: s[0])
        top = scored[:max(1, int(n_results))]
        return {"ids": [[s[1] for s in top]], "documents": [[s[2] for s in top]],
                "metadatas": [[dict(s[3]) for s in top]], "distances": [[s[0] for s in top]]}

    def get(self, ids=None, include=None, where=None, limit=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else list(self.rows)
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


@pytest.fixture
def mem(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    m = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    m._collection = FakeCollection()
    monkeypatch.setattr(m, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(m, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(ms, "_memory", lambda: m)
    return m


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(mp, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(mi, "_path", lambda: tmp_path / "recall-log.jsonl")
    monkeypatch.setattr(mi, "_off_record", lambda *a, **k: False)
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    monkeypatch.setattr(ms, "_forgotten_names", lambda: set())
    monkeypatch.setattr(ms, "_forgotten_emails", lambda: set())
    monkeypatch.setattr(ms, "_documents", lambda q: [])
    monkeypatch.setattr(ms, "_memory", lambda: None)
    monkeypatch.setattr(me, "_all_documents", lambda: [])
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


def _say(cid, text, role="user"):
    return cv.append(cid, {"role": role, "text": text, "meta": {"kind": "turn", "via": "chat"}})["id"]


def _proposal(text, *, day="2026-09-02", evidence="", status="pending"):
    fid = uuid.uuid4().hex[:16]
    conn = mp._connect()
    try:
        conn.execute("INSERT INTO proposed_facts (fact_id, ts, day, category, text, evidence, confidence, "
                     "model, status, decided_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (fid, time.time(), day, "preference", text, evidence, mp.MODEL_CONFIDENCE,
                      "local-test-model", status, time.time() if status != "pending" else None))
        conn.commit()
    finally:
        conn.close()
    return fid


def _everything():
    cid = cv.create("Boats")["id"]
    _say(cid, "My sailing boat Wren is moored at Harwich.")
    cm.add("user", "Keeps a sailing boat called Wren.", actor="friday")
    cm.queue("user", "add", text="Wren needs new sails before spring.", source="dream:2026-09-03",
             reason="The owner mentioned the sails twice.")
    um.note_fact("bio", "Owns a boat called Wren.", confidence=0.8, source="proposed:2026-09-02")
    _proposal("Wren is a wooden boat.", evidence="she's all oak, Wren")
    return cid


def _texts(exp, kind=None):
    return [r["text"] for r in exp["items"] if kind is None or r["kind"] == kind]


# ── what an export holds ─────────────────────────────────────────────────────

def test_with_no_query_the_whole_memory_is_exported_kind_by_kind():
    _everything()
    exp = me.build("")
    assert exp["ok"], exp
    kinds = {r["kind"] for r in exp["items"]}
    assert kinds >= {"conversation", "card", "suggestion", "fact", "proposal"}, exp["items"]
    assert "My sailing boat Wren is moored at Harwich." in _texts(exp, "conversation")
    assert "Keeps a sailing boat called Wren." in _texts(exp, "card")
    assert "Wren needs new sails before spring." in _texts(exp, "suggestion")
    assert "Owns a boat called Wren." in _texts(exp, "fact")
    assert "Wren is a wooden boat." in _texts(exp, "proposal")
    for r in exp["items"]:
        assert {"kind", "text", "when", "source", "status"} <= set(r), r
        assert isinstance(r["source"], str) and r["source"], r
    card = next(r for r in exp["items"] if r["kind"] == "card")
    assert card["status"] == "current" and "Friday saved this" in card["source"]
    assert next(r for r in exp["items"] if r["kind"] == "suggestion")["status"] == "pending"


def test_with_a_query_the_export_is_the_search_results():
    _everything()
    cm.add("user", "Prefers tea to coffee.", actor="friday")
    exp = me.build("Wren", kinds=["card", "fact"])
    found = ms.search("Wren", kinds=["card", "fact"], limit=ms.LIMIT_MAX)
    assert exp["ok"] and sorted(_texts(exp)) == sorted(r["text"] for r in found["results"])
    assert "Prefers tea to coffee." not in _texts(exp)
    assert {r["kind"] for r in exp["items"]} <= {"card", "fact"}


def test_a_corrected_turn_is_left_out_unless_asked_for(mem):
    cid = cv.create("Boats")["id"]
    old = _say(cid, "The mooring fee for Wren is 400 pounds a year.")
    new = _say(cid, "The mooring fee for Wren went up to 520 pounds a year.")
    mem.index("The mooring fee for Wren is 400 pounds a year.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id=old)
    mem.index("The mooring fee for Wren went up to 520 pounds a year.", "user", session_id="2026-09-20",
              timestamp="2026-09-20T10:00:00", msg_id=new)
    mem.supersede([old], "the owner said the fee went up to 520")
    plain = me.build("", kinds=["conversation"])
    assert "The mooring fee for Wren is 400 pounds a year." not in _texts(plain)
    assert "The mooring fee for Wren went up to 520 pounds a year." in _texts(plain)
    shown = me.build("", kinds=["conversation"], include_superseded=True)
    hit = next(r for r in shown["items"] if r["text"].startswith("The mooring fee for Wren is 400"))
    assert hit["status"] == "superseded" and hit["status_reason"] == "the owner said the fee went up to 520"


def test_nobody_the_owner_asked_friday_to_forget_is_exported(monkeypatch):
    _everything()
    cm.add("user", "Sails Wren with Ada Lovelace on Sundays.", actor="friday")
    um.note_fact("people", "Writes to ada@example.org about the boat.", confidence=0.8, source="manual")
    monkeypatch.setattr(ms, "_forgotten_names", lambda: {"Ada Lovelace"})
    monkeypatch.setattr(ms, "_forgotten_emails", lambda: {"ada@example.org"})
    for exp in (me.build(""), me.build("Wren"), me.build("boat")):
        assert exp["ok"], exp
        blob = json.dumps(exp)
        assert "Lovelace" not in blob and "ada@example.org" not in blob, blob[:400]
    whole = me.build("")
    assert "Keeps a sailing boat called Wren." in _texts(whole)
    assert any("forget" in n for n in whole["notes"])


def test_no_key_or_password_is_exported_whatever_store_it_came_from(monkeypatch):
    _everything()
    # A store that kept a key by some older road: the export withholds it on the way out.
    monkeypatch.setattr(um, "all_facts", lambda limit=2000: [
        {"fact_id": "f1", "ts": time.time(), "category": "bio", "text": "Wren's mooring login is " + KEY,
         "confidence": 0.9, "source": "manual"}])
    for exp in (me.build(""), me.build("mooring login", kinds=["fact"])):
        assert exp["ok"], exp
        blob = json.dumps(exp)
        assert KEY not in blob and "sk-ant-" not in blob, blob[:400]
        fact = next(r for r in exp["items"] if r["kind"] == "fact")
        assert pasted_secrets.MARKER in fact["text"], fact
        for chunk in (list(me.to_json(exp)), list(me.to_markdown(exp))):
            assert KEY not in "".join(chunk)


def test_the_export_is_bounded(monkeypatch):
    for line in ("Keeps a sailing boat called Wren.", "Prefers tea to coffee.", "Walks to work on Mondays.",
                 "Plays the cello badly.", "Grows tomatoes in the garden.", "Reads crime novels at night."):
        assert cm.add("user", line, actor="friday").get("ok"), line
    assert len(cm.entries("user")) >= 4
    monkeypatch.setattr(me, "EXPORT_MAX", 3)
    exp = me.build("", kinds=["card"])
    assert exp["count"] == 3 and len(exp["items"]) == 3 and exp["truncated"] is True
    assert any("first 3" in n for n in exp["notes"])


def test_this_sessions_off_record_turns_are_not_exported(monkeypatch):
    cid = cv.create("Boats")["id"]
    _say(cid, "Wren is kept at Harwich.")
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "recalled", lambda c: [{"id": "offrec1", "role": "user",
                                                           "text": "An off the record word about Wren.",
                                                           "ts": time.time()}])
    exp = me.build("", kinds=["conversation"])
    assert "Wren is kept at Harwich." in _texts(exp)
    assert "An off the record word about Wren." not in _texts(exp)


# ── the files ─────────────────────────────────────────────────────────────────

def test_json_and_markdown_files():
    _everything()
    exp = me.build("")
    doc = json.loads("".join(me.to_json(exp)))
    assert doc["count"] == len(doc["items"]) == exp["count"]
    assert doc["query"] == "" and doc["items"][0]["kind"] == "conversation"
    md = "".join(me.to_markdown(exp))
    assert md.startswith("# What Friday keeps about you")
    for heading in ("## Conversations", "## Memory card", "## Waiting for your review", "## Facts",
                    "## Suggested facts"):
        assert heading in md, heading
    assert md.index("## Conversations") < md.index("## Memory card") < md.index("## Facts")
    assert "- Keeps a sailing boat called Wren." in md
    empty = me.build("nothing at all matches this", kinds=["card"])
    assert json.loads("".join(me.to_json(empty)))["items"] == []
    assert me.filename("md").endswith(".md") and me.filename("json").endswith(".json")


@pytest.mark.parametrize("args", [
    {"query": "x" * 201}, {"kinds": "card,nope"}, {"since": "yesterday"}, {"until": "2026-02-30"},
    {"fmt": "pdf"},
])
def test_parameters_are_held_to_the_searchs_bounds(args):
    with pytest.raises(me.BadRequest):
        me.check_params(args.get("query", ""), args.get("kinds"), args.get("since"), args.get("until"),
                        args.get("fmt"))


def test_an_empty_query_means_the_whole_memory():
    q, ks, since, until, fmt = me.check_params("", None, None, None, None)
    assert q == "" and ks == ms.KINDS and fmt == "json"
    assert me.check_params("  Wren  ", "card", None, None, "markdown")[::4] == ("Wren", "md")
