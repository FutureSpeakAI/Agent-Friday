"""An owner's correction on the memory card reaches what Friday recalls
(north star §34.9-3 the current state wins, §34.9-4 a correction reaches every
store): the owner's own earlier messages that stated the corrected or deleted
line stop being recalled as evidence, in conversation memory and in
conversation search. Strict: only messages holding every content word of the
line. Friday's own changes undone by the owner do not do this.

The real ConversationMemory over an in-memory collection with Chroma's
interface (CI installs no Chroma), the real full-text index and transcript."""
from __future__ import annotations

import re

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversations as cv
from agent_friday.services import core_memory as cm
from agent_friday.services import prompt_freeze
from agent_friday.services import user_model as um


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
        role = None
        if isinstance(where, dict):
            role = where.get("role") if isinstance(where.get("role"), str) else None
        scored = []
        for i, (d, m) in self.rows.items():
            if role and m.get("role") != role:
                continue
            scored.append((1.0 - len(q & _words(d)) / max(1, len(q)), i, d, m))
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
def world(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import model_router
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {})
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    prompt_freeze.reset()
    yield mem
    prompt_freeze.reset()


def _said(mem, cid, text, role="user"):
    m = cv.append(cid, {"role": role, "text": text, "meta": {"kind": "turn", "via": "chat"}})
    mid = m["id"] if isinstance(m, dict) and m.get("id") else \
        [x for x in cv._stored_messages(cid) if x.get("text") == text][-1]["id"]
    mem.index(text, role, session_id="2026-09-01", msg_id=mid)
    return mid


def _recalled_ids(mem, q):
    return {h["id"] for h in mem.search(q, n=10)}


def test_correcting_a_card_line_retires_the_messages_that_stated_it(world):
    mem = world
    cid = cv.create("Home")["id"]
    old = _said(mem, cid, "I live at 14 Elm Street in Harwich, by the harbour.")
    other = _said(mem, cid, "Elm trees are lovely in the autumn.")          # one word in common
    fts.ensure_built()
    cm.add("user", "Lives at 14 Elm Street, Harwich.", actor="owner")
    res = cm.owner_set("user", 0, "Lives at 3 Quay Lane, Harwich.")
    assert res["ok"] and res["superseded_turns"] == 1
    assert old not in _recalled_ids(mem, "where do I live elm street harwich")
    assert other in _recalled_ids(mem, "elm trees autumn")
    assert not any(h["message_id"] == old for h in fts.search("Elm Street")), \
        "conversation search still returns the corrected statement"


def test_deleting_a_card_line_retires_them_too(world):
    mem = world
    cid = cv.create("Diet")["id"]
    old = _said(mem, cid, "Please remember I am allergic to peanuts and cashews.")
    fts.ensure_built()
    cm.add("user", "Allergic to peanuts and cashews.", actor="owner")
    res = cm.owner_delete("user", 0)
    assert res["ok"] and res["superseded_turns"] == 1
    assert old not in _recalled_ids(mem, "allergic peanuts cashews")


def test_a_short_line_or_friday_s_own_words_are_never_retired(world):
    mem = world
    cid = cv.create("Misc")["id"]
    mine = _said(mem, cid, "Prefers tea.", role="friday")
    short = _said(mem, cid, "I prefer tea.")
    cm.add("user", "Prefers tea.", actor="owner")
    res = cm.owner_set("user", 0, "Prefers coffee.")
    assert res["superseded_turns"] == 0, "a two-word line is too short to say which messages stated it"
    assert {mine, short} <= _recalled_ids(mem, "prefer tea")


def test_undoing_friday_s_change_keeps_the_owner_s_messages(world):
    mem = world
    cid = cv.create("Boat")["id"]
    said = _said(mem, cid, "My sailing boat Wren is moored at Harwich marina.")
    added = cm.add("user", "Sailing boat Wren moored at Harwich marina.", actor="friday")
    assert added["ok"]
    assert cm.undo(added["change_id"])["ok"]
    assert said in _recalled_ids(mem, "sailing boat wren harwich marina")
