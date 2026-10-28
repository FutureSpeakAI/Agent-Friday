"""The conversation memory is rebuilt safely (BD-16 C2b; North Star 32.5, NS-32.6-1, 34.9-9).

The Doctor's press builds a NEW store from the transcripts beside the one in use, then switches.
Kept: the owner's corrections with their reasons (a memory-card correction can still be
undone), every chat write, delete and correction made while it builds, and any holder of the
old store object (its writes land in the new one). A failed rebuild, or one a Library purge
overtook, changes nothing and leaves no copy. The real ConversationMemory over in-memory
collections with Chroma's interface, one per folder (CI installs no Chroma).
"""
from __future__ import annotations

import os
import re
import time
from types import SimpleNamespace

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

    def upsert(self, ids, documents, metadatas=None, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas or [{}] * len(ids)):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = _words((query_texts or [""])[0])
        scored = sorted(((1.0 - len(q & _words(d)) / max(1, len(q)), i, d, m)
                         for i, (d, m) in self.rows.items()), key=lambda s: s[0])
        top = scored[:max(1, int(n_results))]
        return {"ids": [[s[1] for s in top]], "documents": [[s[2] for s in top]],
                "metadatas": [[dict(s[3]) for s in top]], "distances": [[s[0] for s in top]]}

    def get(self, ids=None, include=None, where=None, limit=None, offset=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else list(self.rows)
        for k, v in (where or {}).items():
            keys = [i for i in keys if self.rows[i][1].get(k) == v]
        keys = keys[int(offset or 0):][:limit] if limit is not None else keys[int(offset or 0):]
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for n, i in enumerate(ids):
            if str(i) in self.rows:
                if metadatas:
                    self.rows[str(i)][1] = dict(metadatas[n])
                if documents:
                    self.rows[str(i)][0] = documents[n]

    def delete(self, ids=None, where=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


@pytest.fixture
def live(tmp_path, monkeypatch):
    from agent_friday import conversation_memory as cm
    from agent_friday.services import model_router
    try:
        from agent_friday.services import memory_store_repair as msr
    except ImportError:
        msr = None
    if msr is None or not hasattr(msr, "rebuild_vector"):
        pytest.fail("the conversation memory cannot be rebuilt: there is no rebuild_vector")
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    monkeypatch.setattr(cm, "DEFAULT_PERSIST_DIR", tmp_path / "memory" / "conversations")
    monkeypatch.setattr(cm, "_instance", None)
    monkeypatch.setattr(model_router, "_CONVERSATION_MEMORY", None)
    disk = {}

    class Client:
        def __init__(self, path):
            self.path = path

        def get_or_create_collection(self, name, **k):
            return disk.setdefault((self.path, name), FakeCollection())

        def close(self):
            pass

    def ensure(self):
        if self._collection is None:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = Client(str(self.persist_dir))
            self._collection = self._client.get_or_create_collection(cm.COLLECTION_NAME)
        return True
    monkeypatch.setattr(cm.ConversationMemory, "_ensure", ensure)
    monkeypatch.setattr(cm.ConversationMemory, "_assert_embedding_space", lambda self: None)
    monkeypatch.setattr(cm.ConversationMemory, "_shared_query_ok", lambda self: False)
    monkeypatch.setattr(msr, "_kg_entities_to_embed", lambda: [])
    yield SimpleNamespace(cm=cm, msr=msr, router=model_router, tmp=tmp_path)
    cm.journal_take(stop=True)
    msr._VEC_REBUILDING.clear()


def _say(cid, text, role="user"):
    m = cv.append(cid, {"role": role, "text": text, "meta": {"kind": "turn", "via": "chat"}})
    return m["id"] if isinstance(m, dict) and m.get("id") else \
        [x for x in cv._stored_messages(cid) if x.get("text") == text][-1]["id"]


def _two_turns(live):
    cid = cv.create("Boats")["id"]
    keep = _say(cid, "Our boat Wren is moored at Harwich marina.")
    wrong = _say(cid, "The berth number at Harwich marina is 12.")
    fts.ensure_built()
    old = live.router._get_conversation_memory()
    old.index("Our boat Wren is moored at Harwich marina.", "user", msg_id=keep)
    old.index("The berth number at Harwich marina is 12.", "user", msg_id=wrong)
    return old, keep, wrong


def _meta(mem):
    assert mem._ensure()
    got = mem._collection.get()
    return dict(zip(got["ids"], got["metadatas"]))


def test_a_rebuild_builds_beside_the_store_in_use_then_switches_keeping_the_owners_corrections(live):
    from agent_friday.services.core_memory import CARD_CORRECTED
    old, keep, wrong = _two_turns(live)
    old.supersede([wrong], CARD_CORRECTED)
    old_dir = old.persist_dir
    out = live.msr.rebuild_vector("owner:test")
    assert out.get("ok"), out
    new = live.router._get_conversation_memory()
    assert new is not old and new.persist_dir != old_dir, "the chat path still holds the old store"
    assert live.cm.live_dir() == new.persist_dir
    meta = _meta(new)
    assert keep in meta and wrong in meta
    assert meta[wrong].get(live.cm._SUPERSEDED_REASON) == CARD_CORRECTED, \
        "the correction lost its reason, so the memory card can no longer undo it"
    assert not meta[keep].get(live.cm._SUPERSEDED_KEY)
    assert not old_dir.exists(), "the replaced store (which may hold forgotten words) was kept"


def test_writes_deletes_and_corrections_made_while_it_builds_reach_the_new_store(live):
    old, keep, wrong = _two_turns(live)
    real = live.cm.ConversationMemory.rebuild_from_transcripts

    def during(self, *a, **k):
        out = real(self, *a, **k)
        old.index("We moved Wren to Ipswich haven on Tuesday.", "user", msg_id="m-late")
        old.delete(ids=[keep])
        old.supersede([wrong], "the owner corrected it")
        return out
    live.cm.ConversationMemory.rebuild_from_transcripts = during
    try:
        out = live.msr.rebuild_vector("owner:test")
    finally:
        live.cm.ConversationMemory.rebuild_from_transcripts = real
    assert out.get("ok"), out
    meta = _meta(live.router._get_conversation_memory())
    assert "m-late" in meta, "a turn said while the store was rebuilt was lost"
    assert keep not in meta, "a message deleted while the store was rebuilt came back"
    assert meta[wrong].get(live.cm._SUPERSEDED_KEY) == "1", "a correction made meanwhile was lost"


def test_a_holder_of_the_old_store_writes_into_the_new_one(live):
    old, keep, wrong = _two_turns(live)
    assert live.msr.rebuild_vector("owner:test").get("ok")
    old.index("After the switch Wren needs new antifouling.", "user", msg_id="m-after")
    new = live.router._get_conversation_memory()
    assert "m-after" in _meta(new), "a write to the replaced store object was lost"
    assert "m-after" not in old._collection.rows


def test_a_rebuild_that_fails_changes_nothing_and_leaves_no_copy(live):
    old, keep, wrong = _two_turns(live)
    real = live.cm.ConversationMemory.rebuild_from_transcripts
    live.cm.ConversationMemory.rebuild_from_transcripts = lambda self, *a, **k: {"ok": False, "error": "x"}
    try:
        out = live.msr.rebuild_vector("owner:test")
    finally:
        live.cm.ConversationMemory.rebuild_from_transcripts = real
    assert not out.get("ok")
    assert live.router._get_conversation_memory() is old
    assert live.cm.live_dir() == old.persist_dir
    left = [d.name for d in old.persist_dir.parent.iterdir() if d.name.startswith("conversations.rebuild-")]
    assert left == [], "a failed rebuild left a copy of the conversations behind"
    assert not live.cm._JOURNAL["on"]


def test_a_library_purge_during_the_rebuild_abandons_it(live):
    old, keep, wrong = _two_turns(live)
    real = live.cm.ConversationMemory.rebuild_from_transcripts

    def during(self, *a, **k):
        out = real(self, *a, **k)
        live.cm.note_rewrite()                 # the purge rewrites the store in use, in place
        return out
    live.cm.ConversationMemory.rebuild_from_transcripts = during
    try:
        out = live.msr.rebuild_vector("owner:test")
    finally:
        live.cm.ConversationMemory.rebuild_from_transcripts = real
    assert not out.get("ok"), "a store built from words a purge removed replaced the purged one"
    assert live.router._get_conversation_memory() is old


def test_turns_the_transcripts_no_longer_hold_are_kept(live):
    # Transcripts are pruned to their last turns; the store in use is the only copy of older ones.
    old, keep, wrong = _two_turns(live)
    old.index("Two years ago we bought Wren from a yard in Woodbridge.", "user", msg_id="m-pruned")
    out = live.msr.rebuild_vector("owner:test")
    assert out.get("ok"), out
    assert "m-pruned" in _meta(live.router._get_conversation_memory()), \
        "a turn only the old store held was dropped by the rebuild"


def test_a_failed_rebuild_leaves_conversation_search_unchanged(live):
    cid = cv.create("Boats")["id"]
    said = "Remind me to call the marina on Friday at noon please."
    twin = _say(cid, said)
    fts.ensure_built()
    old = live.router._get_conversation_memory()
    old.index(said, "user", msg_id="older-random-id")
    old.supersede(["older-random-id"], "the owner corrected it")
    before = set(fts.superseded_ids())
    real = live.cm.ConversationMemory.rebuild_from_transcripts

    def during(self, *a, **k):
        out = real(self, *a, **k)
        live.cm.note_rewrite()                 # makes the rebuild fail after the new store wrote
        return out
    live.cm.ConversationMemory.rebuild_from_transcripts = during
    try:
        out = live.msr.rebuild_vector("owner:test")
    finally:
        live.cm.ConversationMemory.rebuild_from_transcripts = real
    assert not out.get("ok")
    assert set(fts.superseded_ids()) == before and twin not in fts.superseded_ids(), \
        "the abandoned new store changed what conversation search hides"


def test_an_unreadable_store_is_rebuilt_from_the_transcripts_only_with_the_owners_yes(live):
    old, keep, wrong = _two_turns(live)

    def broken(*a, **k):
        raise RuntimeError("database disk image is malformed")
    old._collection.get = broken
    out = live.msr.rebuild_vector("owner:test")
    assert not out.get("ok") and out.get("needs_confirm"), out
    assert live.router._get_conversation_memory() is old and live.cm.live_dir() == old.persist_dir
    out = live.msr.rebuild_vector("owner:test", accept_loss=True)
    assert out.get("ok") and out.get("from_transcripts_only"), out
    assert {keep, wrong} <= set(_meta(live.router._get_conversation_memory()))


def test_an_unreadable_pointer_never_goes_back_to_the_replaced_store(live):
    old, keep, wrong = _two_turns(live)
    assert live.msr.rebuild_vector("owner:test").get("ok")
    new_dir = live.cm.live_dir()
    old.persist_dir.mkdir(parents=True, exist_ok=True)      # Windows kept the old folder
    live.cm._pointer().write_text("", encoding="utf-8")       # and the pointer reads empty
    assert live.cm.live_dir() == new_dir


def test_conversation_search_keeps_a_corrections_reason(live):
    fts.ensure_built()
    fts.mark_superseded(["m1"], True, reason="the owner corrected this on the memory card",
                        superseded_by="card:1")
    assert fts.superseded_reasons().get("m1") == ("the owner corrected this on the memory card", "card:1")
    fts.mark_superseded(["m1"], False)
    assert "m1" not in fts.superseded_reasons()


def test_only_a_store_no_rebuild_is_using_is_stale(live):
    cm = live.cm
    base = cm.DEFAULT_PERSIST_DIR.parent
    (base / "conversations").mkdir(parents=True)
    crashed = base / "conversations.rebuild-1000000001"
    running = base / "conversations.rebuild-1000000002"
    crashed.mkdir()
    running.mkdir()
    cm.build_mark(crashed).write_text("999999999:0", encoding="utf-8")    # its builder is gone
    cm.build_mark(running).write_text("%d:0" % os.getpid(), encoding="utf-8")   # a live builder
    assert [d.name for d in cm.stale_stores()] == [crashed.name]
    cm._pointer().write_text("../../elsewhere", encoding="utf-8")
    assert cm.stale_stores() == [], "an untrusted pointer let a store be judged stale"
    assert cm.live_dir() == cm.DEFAULT_PERSIST_DIR
    old = time.time() - cm.BUILD_MARK_TTL - 60
    os.utime(cm.build_mark(running), (old, old))
    cm._pointer().unlink()
    assert {d.name for d in cm.stale_stores()} == {crashed.name, running.name}
