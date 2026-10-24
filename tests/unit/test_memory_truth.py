"""Memory truth for the release gate (north star §34.9-1 cross-session recall,
§34.9-2 source citation, §34.9-3 the current state wins, §34.9-4 a correction
reaches every store, §34.9-9 rebuilding an index brings nothing back).

The real ConversationMemory, conversation full-text index and transcript;
CI does not install Chroma (the "local" extra), so the vector collection
under ConversationMemory is a small in-memory one with Chroma's interface
(word overlap as distance). Synthetic text; nothing reaches a network."""
from __future__ import annotations

import re

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversations as cv


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class FakeCollection:
    """Chroma's collection interface, as ConversationMemory uses it."""

    def __init__(self):
        self.rows = {}                       # id -> [document, metadata]

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
    m._collection = FakeCollection()          # ready: _ensure() returns True
    monkeypatch.setattr(m, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(m, "_shared_query_ok", lambda: False)
    assert m.available()
    return m


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)


def _say(cid, text):
    m = cv.append(cid, {"role": "user", "text": text, "meta": {"kind": "turn", "via": "chat"}})
    if isinstance(m, dict) and m.get("id"):
        return m["id"]
    return [x for x in cv._stored_messages(cid) if x.get("text") == text][-1]["id"]


def _block(monkeypatch, memory, message, session="s-today"):
    from agent_friday.services import model_router
    from agent_friday.services import turn_vector
    monkeypatch.setattr(turn_vector, "compute", lambda *a, **k: None)      # no model in a test
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: memory)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    return model_router._build_memory_context_block(message, session, min_relevance=0.0)


# ── §34.9-1 and §34.9-2: recall across conversations, with its source ──────

def test_a_fact_from_an_earlier_conversation_is_recalled_with_its_date_and_source(mem, monkeypatch):
    mid = "msg-boat-0001"
    mem.index("My sailing boat is called Wren and she is moored at Harwich.", "user",
              session_id="2026-09-01", timestamp="2026-09-01T10:00:00", msg_id=mid)
    hits = mem.search("what is my boat called", n=3)
    assert hits and hits[0]["id"] == mid, "a recalled turn does not say which message it was"
    block = _block(monkeypatch, mem, "what is my boat called")
    line = [ln for ln in block.splitlines() if "Wren" in ln]
    assert line, "the earlier conversation's fact was not recalled"
    assert line[0].startswith("- [2026-09-01 ref msg-boat-000"), line[0]


def test_when_two_recalled_facts_disagree_the_newer_comes_first_and_is_named_current(monkeypatch):
    class Mem:
        def available(self):
            return True

        def search(self, message, n=5, **kw):
            return [  # the older line is the more "relevant" one
                {"id": "m-old", "text": "I live in Boston.", "date": "2025-03-02",
                 "timestamp": "2025-03-02T09:00:00", "role": "user", "relevance": 0.95},
                {"id": "m-new", "text": "We moved to Denver last month.", "date": "2026-08-20",
                 "timestamp": "2026-08-20T18:30:00", "role": "user", "relevance": 0.6},
            ]
    block = _block(monkeypatch, Mem(), "where do I live")
    lines = [ln for ln in block.splitlines() if ln.startswith("- [")]
    assert "Denver" in lines[0] and "Boston" in lines[1]
    assert "newer one is the current state" in block
    assert "ref m-new" in lines[0] and "ref m-old" in lines[1]


# ── §34.9-4: a correction (supersession) reaches every store ────────────────

def test_a_superseded_turn_leaves_conversation_search_and_comes_back_when_restored(mem, transcripts):
    cid = cv.create("Boats")["id"]
    old = _say(cid, "The mooring fee for Wren is 400 pounds a year.")
    _say(cid, "Something else entirely about the harbour walk.")
    fts.ensure_built()
    mem.index("The mooring fee for Wren is 400 pounds a year.", "user", session_id="2026-09-01", msg_id=old)
    assert any(h["message_id"] == old for h in fts.search("mooring fee"))

    mem.supersede([old], "the owner corrected the fee")
    assert not any(h["message_id"] == old for h in fts.search("mooring fee")), \
        "a superseded turn still comes back from conversation search"
    assert mem.search("mooring fee", n=3) == [] or all(h["id"] != old for h in mem.search("mooring fee", n=3))

    mem.unsupersede([old])
    assert any(h["message_id"] == old for h in fts.search("mooring fee"))


def test_a_superseded_turn_is_not_consolidated_overnight():
    from agent_friday.services import memory_dreaming as md

    class Mem:
        def recent(self, n=20, roles=None):
            return [{"text": "Wren's fee is 400 pounds.", "date": "2026-09-01", "superseded": True},
                    {"text": "Wren's fee is 520 pounds.", "date": "2026-09-01", "superseded": False}]
    turns, _ = md._pull_turns(Mem(), "2026-09-01")
    assert [t["text"] for t in turns] == ["Wren's fee is 520 pounds."]


def test_a_superseded_turn_is_not_fed_to_the_knowledge_graph(monkeypatch):
    import agent_friday.conversation_memory as cmod
    from agent_friday.services.knowledge_graph import indexer

    class Fake:
        def __init__(self, *a, **k):
            pass

        def available(self):
            return True

        def recent(self, n=20, roles=None):
            return [{"text": "Old: the harbour master at Harwich is called Peter Smallwood, as noted.",
                     "role": "user", "timestamp": "2026-09-01T10:00:00", "date": "2026-09-01",
                     "session_id": "2026-09-01", "superseded": True},
                    {"text": "New: the harbour master at Harwich is now called Ana Ferreira, since September.",
                     "role": "user", "timestamp": "2026-09-20T10:00:00", "date": "2026-09-20",
                     "session_id": "2026-09-20", "superseded": False}]
    monkeypatch.setattr(cmod, "ConversationMemory", Fake)
    texts = " ".join(str(c) for c in indexer._conversation_chunks())
    assert "Ana Ferreira" in texts and "Smallwood" not in texts


# ── §34.9-9: rebuilding the index brings nothing back ───────────────────────

def test_rebuilding_conversation_search_keeps_deletions_and_corrections(mem, transcripts, monkeypatch):
    from agent_friday.services import model_router
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)     # delete reaches this store
    cid = cv.create("Boats")["id"]
    gone = _say(cid, "The old harbour code is 4471.")
    wrong = _say(cid, "The berth number is 12.")
    _say(cid, "The berth number is 31 since the move.")
    fts.ensure_built()
    fts.delete_message(cid, gone)
    mem.index("The berth number is 12.", "user", session_id="2026-09-01", msg_id=wrong)
    mem.supersede([wrong], "moved berth")

    fts.build(force=True)
    assert fts.search("harbour code") == [], "a deleted message came back after a rebuild"
    berths = fts.search("berth number")
    assert berths and all(h["message_id"] != wrong for h in berths), \
        "a superseded turn came back after a rebuild"
    assert any("31" in h["text"] for h in berths)
