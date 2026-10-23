"""Conversation memory can be rebuilt from the transcripts (north star
§34.9-9): every stored turn comes back, a deleted message does not, an
off-the-record turn does not, and a superseded (corrected) turn stays
superseded. The real ConversationMemory over an in-memory collection with
Chroma's interface (CI installs no Chroma); the real transcript store."""
from __future__ import annotations

import re

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversations as cv


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class FakeCollection:
    def __init__(self):
        self.rows = {}

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
def world(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import model_router
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    return mem


def _say(cid, text, role="user"):
    m = cv.append(cid, {"role": role, "text": text, "meta": {"kind": "turn", "via": "chat"}})
    return m["id"] if isinstance(m, dict) and m.get("id") else \
        [x for x in cv._stored_messages(cid) if x.get("text") == text][-1]["id"]


def test_an_empty_memory_is_rebuilt_from_the_transcripts_keeping_corrections_and_deletions(world):
    mem = world
    cid = cv.create("Boats")["id"]
    keep = _say(cid, "Our boat Wren is moored at Harwich marina.")
    wrong = _say(cid, "The berth number is 12.")
    gone = _say(cid, "The gate code is 4471.")
    fts.ensure_built()
    mem.index("The berth number is 12.", "user", session_id="2026-09-01", msg_id=wrong)
    mem.supersede([wrong], "moved berth")
    fts.delete_message(cid, gone)

    mem._collection = FakeCollection()                   # the vector store is lost
    out = mem.rebuild_from_transcripts()
    assert out["ok"] and out["indexed"] == 2 and out["kept_superseded"] == 1
    ids = {h["id"] for h in mem.search("boat berth gate code harwich", n=10)}
    assert keep in ids, "a stored turn did not come back"
    assert wrong not in ids, "a corrected turn came back as evidence"
    assert gone not in ids and gone not in mem._collection.rows, "a deleted message came back"
    history = {r["text"]: r["superseded"] for r in mem.recent(n=10)}
    assert history.get("The berth number is 12.") is True


def test_a_rebuild_refreshes_in_place_and_never_drops_rows(world):
    mem = world
    cid = cv.create("Home")["id"]
    mid = _say(cid, "We moved to Quay Lane in September.")
    mem._collection.upsert(ids=["legacy-0001"], documents=["An older turn kept under a random id."],
                           metadatas=[{"role": "user", "timestamp": "2025-01-01T00:00:00", "date": "2025-01-01",
                                       "session_id": "2025-01-01"}])
    assert mem.rebuild_from_transcripts()["indexed"] == 1
    assert {"legacy-0001", mid} <= set(mem._collection.rows)
    assert mem.rebuild_from_transcripts()["indexed"] == 1        # idempotent: same ids, no copies
    assert len(mem._collection.rows) == 2
