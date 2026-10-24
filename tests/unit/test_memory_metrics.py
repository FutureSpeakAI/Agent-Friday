"""Memory measures (north star §15.12-1) and the offline retrieval evaluation
(§36.2-12): services/memory_metrics and services/memory_retrieval_eval.

Each hook on the memory paths records a count, never the memory's text; the
summary turns the counts into rates; a broken or unwritable store never reaches
the caller; counters from modules that may be absent read as zero; and the
retrieval evaluation runs through the real ConversationMemory.search and meets
its floor.

Red on 51a160fcb (feat/memory-metrics base): services/memory_metrics and
services/memory_retrieval_eval did not exist and no memory path counted
anything. CI installs no Chroma: the vector store is an in-memory collection
with Chroma's interface (as tests/unit/test_memory_truth.py). Synthetic text.
"""
from __future__ import annotations

import json
import re
import sys
import time
import types

import pytest

from agent_friday.services import memory_metrics as mm

SECRET_WORDS = ("Wren", "Harwich", "Skoda", "bees", "Suffolk", "Norfolk", "oolong", "msg-boat")


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    p = tmp_path / "metrics" / "memory_metrics.jsonl"
    monkeypatch.setattr(mm, "_path", lambda: p)
    monkeypatch.setattr(mm, "_off_record", lambda: False)
    mm._reset_for_tests()
    yield p
    mm._reset_for_tests()


def _rows(p):
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _kind(p, kind):
    return [r for r in _rows(p) if r["kind"] == kind]


def _no_content(p, *extra):
    raw = p.read_text(encoding="utf-8") if p.exists() else ""
    for w in SECRET_WORDS + extra:
        assert w.lower() not in raw.lower(), f"the metrics store holds memory content: {w!r}"
    for r in _rows(p):
        for k, v in r.items():
            if isinstance(v, str):
                assert " " not in v and len(v) <= 40, f"a free-text field was stored: {k}={v!r}"


class Collection:
    """Chroma's collection interface, as ConversationMemory uses it."""

    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = set(re.findall(r"[a-z]{3,}", (query_texts or [""])[0].lower()))
        scored = sorted(((1.0 - len(q & set(re.findall(r"[a-z]{3,}", d.lower()))) / max(1, len(q)), i, d, m)
                         for i, (d, m) in self.rows.items()), key=lambda s: s[0])[:n_results]
        return {"ids": [[s[1] for s in scored]], "documents": [[s[2] for s in scored]],
                "metadatas": [[dict(s[3]) for s in scored]], "distances": [[s[0] for s in scored]]}

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
    from agent_friday.services import conversation_fts
    monkeypatch.setattr(conversation_fts, "mark_superseded", lambda ids, on=True: 0)
    m = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    m._collection = Collection()
    monkeypatch.setattr(m, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(m, "_shared_query_ok", lambda: False)
    return m


class _Recall:
    def available(self):
        return True

    def search(self, message, n=5, **kw):
        return [
            {"id": "msg-boat-0001", "text": "My sailing boat is called Wren, moored at Harwich.",
             "date": "2026-09-01", "timestamp": "2026-09-01T10:00:00", "role": "user", "relevance": 0.9},
            {"id": "msg-car-00001", "text": "My car is a green Skoda.",
             "date": "2026-08-01", "timestamp": "2026-08-01T10:00:00", "role": "user", "relevance": 0.7},
        ]

    def index_exchange(self, *a, **k):
        return []


def _block(monkeypatch, memory, message, session="s-today"):
    from agent_friday.services import model_router, turn_vector
    monkeypatch.setattr(turn_vector, "compute", lambda *a, **k: None)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: memory)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    return model_router._build_memory_context_block(message, session, min_relevance=0.0)


def _end_turn(monkeypatch, memory, message, reply, session="s-today"):
    from agent_friday.services import memory_review, model_router
    monkeypatch.setattr(memory_review, "note_turn", lambda *a, **k: None)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: memory)
    monkeypatch.setattr(model_router, "_get_emotional_arc",
                        lambda: types.SimpleNamespace(record=lambda *a, **k: None))
    model_router._index_chat_turn_body(message, reply, session)


# ── recall and citation, from the chat's own memory block and end of turn ────

def test_a_recall_counts_candidates_and_shown_and_the_reply_counts_citations(store, monkeypatch):
    block = _block(monkeypatch, _Recall(), "what is my boat called")
    assert "Wren" in block
    rec = _kind(store, "recall")
    assert len(rec) == 1 and rec[0]["candidates"] == 2 and rec[0]["shown"] == 2
    assert len(rec[0]["refs"]) == 2 and all(re.fullmatch(r"[0-9a-f]{12}", r) for r in rec[0]["refs"])

    _end_turn(monkeypatch, _Recall(), "what is my boat called",
              "She is called Wren [conversation:2026-09-01/msg-boat-000].")
    cit = _kind(store, "citation")
    assert cit and cit[0]["shown"] == 2 and cit[0]["cited"] == 1
    _no_content(store, "msg-car", "sailing")


def test_a_reply_after_no_recall_records_no_citation(store, monkeypatch):
    _end_turn(monkeypatch, _Recall(), "hello", "Hello [conversation:2026-09-01/msg-boat-000].", session="other")
    assert _kind(store, "citation") == []


# ── supersession, forgetting, and stale errors ────────────────────────────────

def test_supersession_and_its_reverse_are_counted_with_hashes_only(store, mem):
    mem.index("The mooring fee for Wren is 400 pounds.", "user", session_id="2026-09-01", msg_id="m-fee-old")
    mem.index("Harwich harbour master is Peter.", "user", session_id="2026-09-01", msg_id="m-hm")
    mem.supersede(["m-fee-old"], "the owner corrected the fee")
    mem.supersede(["m-hm"], "display said so", superseded_by="services.harbour.live_status")
    mem.unsupersede(["m-fee-old"])
    sup = _kind(store, "supersession")
    assert [(r["n"], r["live"]) for r in sup] == [(1, 0), (1, 1)]
    assert sup[0]["refs"] == [mm.ref_hash("m-fee-old")]
    assert _kind(store, "unsupersession")[0]["n"] == 1
    _no_content(store, "m-fee-old", "mooring", "Peter")


def test_a_delete_is_counted_as_a_forget(store, mem):
    mem.index("I keep bees in Suffolk.", "user", session_id="2026-09-01", msg_id="m-bees")
    assert mem.delete(ids=["m-bees"]) == 1
    f = _kind(store, "forget")
    assert f and f[0]["n"] == 1 and f[0]["store"] == "conversation"
    _no_content(store, "m-bees")


def test_a_recalled_item_corrected_soon_after_is_a_stale_error_and_lowers_the_precision_proxy(store):
    mm.note_recall("s1", 4, ["a1", "b2", "c3"])
    mm.note_supersession(["b2"], True)                                   # stale
    mm.note_supersession(["c3"], True, "services.google_accounts.summary")   # live state
    mm.note_supersession(["never-recalled"], True)                       # not an error of recall
    # recalled long ago, corrected now: outside the window
    old = time.time() - (mm.STALE_WINDOW_DAYS + 6) * 86400
    with open(store, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": old, "kind": "recall", "candidates": 1, "shown": 1,
                            "refs": [mm.ref_hash("old-1")]}) + "\n")
    mm.note_supersession(["old-1"], True)
    s = mm.summary(30)
    c, r = s["counts"], s["rates"]
    assert c["stale_errors"] == 1 and c["live_state_errors"] == 1
    assert c["recalled_items_later_corrected"] == 2
    assert r["precision_proxy"] == pytest.approx(1 - 2 / 4, abs=1e-4)   # 4 items recalled in the window
    assert "labelled" in s["notes"]["precision_proxy"]


# ── the memory card and the review queue ──────────────────────────────────────

@pytest.fixture
def card(tmp_path, monkeypatch):
    from agent_friday.services import core_memory as cm
    from agent_friday.services import prompt_freeze
    from agent_friday.services import user_model as um
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {})
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    prompt_freeze.reset()
    yield cm
    prompt_freeze.reset()


def test_owner_card_edits_are_corrections_and_review_decisions_are_counted(store, card):
    cm = card
    assert cm.add("user", "I keep bees in Suffolk.", actor="owner")["ok"]
    assert cm.owner_set("user", 0, "I keep bees in Norfolk.")["ok"]
    q1 = cm.queue("user", "add", text="Prefers oolong tea.", source="review")
    q2 = cm.queue("user", "add", text="Drives a green Skoda.", source="dream")
    dup = cm.queue("user", "add", text="I keep bees in Norfolk.", source="review")
    assert q1["ok"] and q2["ok"] and dup.get("unchanged")
    assert cm.decide(q1["id"], True)["ok"]
    assert cm.decide(q2["id"], False)["ok"]
    assert cm.owner_delete("user", 0)["ok"]

    corr = _kind(store, "correction")
    assert [(r["store"], r["op"]) for r in corr] == [("core_memory", "edit"), ("core_memory", "delete")]
    assert sum(r["n"] for r in _kind(store, "inference_queued")) == 2
    assert sum(r["n"] for r in _kind(store, "inference_kept")) == 1
    assert sum(r["n"] for r in _kind(store, "inference_discarded")) == 1
    d = _kind(store, "duplicate_suppressed")
    assert d and d[0]["path"] == "core_memory_queue"
    _no_content(store, "Prefers", "Drives")


def test_memory_proposals_approve_and_reject_are_counted(store, tmp_path, monkeypatch):
    from unittest.mock import patch

    from agent_friday.services import core_memory
    from agent_friday.services import memory_proposals as mp
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "dreams.db")
    conn = mp._connect()
    for fid, text in (("f1", "Keeps bees in Suffolk."), ("f2", "Sails a boat called Wren."),
                      ("f3", "Drives a Skoda.")):
        conn.execute("INSERT INTO proposed_facts (fact_id, ts, day, category, text, evidence, "
                     "confidence, model, status) VALUES (?,?,?,?,?,?,?,?,'pending')",
                     (fid, time.time(), "2026-09-01", "preference", text, "", 0.4, "m"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(core_memory, "add", lambda *a, **k: {"ok": True})
    with patch("agent_friday.services.user_model.note_fact"):
        assert mp.approve(["f1"])["approved"] == 1
    assert mp.reject(["f2", "f3"])["rejected"] == 2
    assert sum(r["n"] for r in _kind(store, "inference_kept")) == 1
    assert sum(r["n"] for r in _kind(store, "inference_discarded")) == 2
    assert all(r.get("path") == "proposals" for r in _rows(store))
    _no_content(store, "Keeps", "Sails", "Drives")


# ── leakage: the egress gate withholding recalled memory from a cloud call ────

def test_recalled_memory_withheld_by_the_egress_gate_is_counted(store, tmp_path, monkeypatch):
    from agent_friday.services import egress_gate as eg
    from agent_friday.services import judgment_gate, memory_fence
    monkeypatch.setattr(judgment_gate, "never_send_hits", lambda text: [])
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    monkeypatch.setattr(eg, "_classify_cloud", lambda text: eg.Tier.SENSITIVE)
    monkeypatch.setattr(eg, "_run_appeals", lambda *a, **k: 0)
    monkeypatch.setattr(eg, "_note_held", lambda *a, **k: None)
    monkeypatch.setattr(eg, "_owner_released", lambda *a, **k: False)
    fenced = memory_fence.fence("past_conversations", "- [2026-09-01 ref m1] User: my boat Wren is at Harwich")
    out = eg._gate_text_span(fenced, "anthropic", "system", log_path=tmp_path / "egress.jsonl")
    assert "Wren" not in out
    lb = _kind(store, "leakage_blocked")
    assert lb and lb[0]["n"] == 1 and lb[0]["where"] == "field"
    # Text that is not recalled memory is not counted as a memory leak.
    eg._gate_text_span("a private note\nabout something else", "anthropic", "system",
                       log_path=tmp_path / "egress.jsonl")
    assert len(_kind(store, "leakage_blocked")) == 1
    # Span-wise: one withheld line inside a recalled block counts the block once.
    monkeypatch.setattr(eg, "_classify_cloud",
                        lambda text: eg.Tier.SENSITIVE if "Wren" in text else eg.Tier.PUBLIC)
    out = eg._gate_text_span("Plan the week.\n" + fenced, "anthropic", "system",
                             log_path=tmp_path / "egress.jsonl")
    assert "Plan the week." in out and "Wren" not in out
    spans = [r for r in _kind(store, "leakage_blocked") if r.get("where") == "span"]
    assert len(spans) == 1 and spans[0]["n"] == 1
    _no_content(store)


# ── the summary ───────────────────────────────────────────────────────────────

def test_the_summary_computes_its_rates(store, monkeypatch):
    _counters(monkeypatch)                                       # this test's own events only
    mm.note_recall("s1", 6, ["item-0001", "item-0002", "item-0003", "item-0004"])
    mm.note_reply("s1", "x [conversation:2026-09-01/item-0001] y [memory:2026-09-01/item-0003] "
                        "z [conversation:2026-09-01/zzzz]")
    mm.note_count("correction", 1, store="core_memory", op="edit")
    mm.note_supersession(["unrelated"], True)
    for _ in range(3):
        mm.note_count("inference_kept", 1, path="core_memory")
    mm.note_count("inference_discarded", 1, path="proposals")
    mm.note_count("inference_queued", 5, path="proposals")
    mm.note_count("duplicate_suppressed", 2, path="core_memory_add")
    mm.note_count("leakage_blocked", 2, where="span")
    mm.note_count("forget", 1, store="conversation")
    mm.note_count("forget", 0, store="conversation")            # nothing removed: not an event
    s = mm.summary(30)
    assert s["ok"]
    c, r = s["counts"], s["rates"]
    assert (c["recalls"], c["candidates"], c["shown"], c["cited"]) == (1, 6, 4, 2)
    assert r["citation_coverage"] == pytest.approx(0.5)
    assert c["corrections"] == 2 and r["correction_rate"] == pytest.approx(0.5)
    assert r["inference_acceptance_rate"] == pytest.approx(0.75)
    assert c["inference_queued"] == 5
    assert c["duplicates_suppressed"] == 2 and s["duplicates_by_path"] == {"core_memory_add": 2}
    assert c["leakage_blocked"] == 2 and c["forgets"] == 1
    assert r["precision_proxy"] == 1.0 and c["stale_errors"] == 0


def test_an_empty_store_gives_zeros_and_no_rates(store):
    s = mm.summary(30)
    assert s["ok"] and s["counts"]["recalls"] == 0
    assert all(v is None for v in s["rates"].values())


def test_unknown_kinds_and_free_text_labels_are_not_stored(store):
    mm.record("a_new_kind", n=1)
    mm.record("correction", n=1, store="I keep bees in Suffolk", op="edit", text="Wren at Harwich")
    rows = _rows(store)
    assert len(rows) == 1 and "store" not in rows[0] and "text" not in rows[0]
    _no_content(store)


def test_nothing_is_recorded_off_the_record(store, monkeypatch):
    monkeypatch.setattr(mm, "_off_record", lambda: True)
    mm.note_recall("s1", 2, ["a", "b"])
    mm.note_count("forget", 1, store="conversation")
    assert not store.exists()


# ── never raises ──────────────────────────────────────────────────────────────

def test_hooks_never_raise_when_the_store_is_unwritable(tmp_path, monkeypatch, card, mem):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(mm, "_path", lambda: blocker / "memory_metrics.jsonl")
    mm.record("forget", n=1, store="conversation")
    mm.note_recall("s", 2, ["a", "b"])
    mm.note_reply("s", "[conversation:2026-09-01/a]")
    mm.note_supersession(["a"], True)
    mm.note_count("leakage_blocked", 1, where="span")
    assert mm.summary(30)["ok"]

    # And a metrics module that raises outright never reaches the memory paths.
    def boom(*a, **k):
        raise RuntimeError("metrics broke")
    for name in ("record", "note_recall", "note_reply", "note_supersession", "note_count"):
        monkeypatch.setattr(mm, name, boom)
    assert card.add("user", "Likes sailing.", actor="owner")["ok"]
    assert card.owner_set("user", 0, "Likes dinghy sailing.")["ok"]
    q = card.queue("user", "add", text="Works nights at the hospital.", source="review")
    assert q["ok"] and card.decide(q["id"], False)["ok"]
    mem.index("Some synthetic line.", "user", session_id="2026-09-01", msg_id="m-x")
    assert mem.supersede(["m-x"], "corrected")["superseded"] == ["m-x"]
    assert mem.delete(ids=["m-x"]) == 1
    assert "Wren" in _block(monkeypatch, _Recall(), "what is my boat called")
    _end_turn(monkeypatch, _Recall(), "q", "a [conversation:2026-09-01/msg-boat-000]")


# ── counters kept by modules that may not be installed ────────────────────────

def _counters(monkeypatch, recall_dedupe=None, sensitive_categories=None):
    """Install (or, with None, remove) the two optional counter modules."""
    import agent_friday.services as pkg
    for name, mod in (("recall_dedupe", recall_dedupe), ("sensitive_categories", sensitive_categories)):
        monkeypatch.setitem(sys.modules, "agent_friday.services." + name, mod)
        if mod is None:
            monkeypatch.delattr(pkg, name, raising=False)
        else:
            monkeypatch.setattr(pkg, name, mod, raising=False)


def test_absent_counter_modules_read_as_zero(store, monkeypatch):
    _counters(monkeypatch)
    s = mm.summary(30)
    assert s["counts"]["duplicates_suppressed"] == 0
    assert s["counts"]["special_category_dropped"] == 0
    assert s["special_category_by_path"] == {}


def test_present_counter_modules_are_read(store, monkeypatch):
    rd = types.ModuleType("agent_friday.services.recall_dedupe")
    rd.suppressed_counts = lambda: {"chat_recall": 3, "Not A Label!": 9}
    sc = types.ModuleType("agent_friday.services.sensitive_categories")
    sc.dropped_counts = lambda: {"memory_review:health": 2, "dreaming:religion": 1}
    _counters(monkeypatch, rd, sc)
    s = mm.summary(30)
    assert s["duplicates_by_path"] == {"recall:chat_recall": 3}
    assert s["counts"]["duplicates_suppressed"] == 3
    assert s["counts"]["special_category_dropped"] == 3
    assert s["special_category_by_path"] == {"memory_review": 2, "dreaming": 1}


# ── the route ─────────────────────────────────────────────────────────────────

def test_the_route_is_login_required_and_returns_the_summary(store):
    from flask import Flask

    from agent_friday.routes import core_memory as routes
    fn = routes.memory_metrics_summary
    assert getattr(fn, "__wrapped__", None) is not None, "GET /api/memory/metrics is not login_required"
    mm.note_count("forget", 2, store="conversation")
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session
    app.register_blueprint(routes.core_memory_bp)
    with app.test_client() as c:
        r = c.get("/api/memory/metrics?days=7")
    assert r.status_code in (200, 401, 403), r.status_code
    if r.status_code == 200:
        d = r.get_json()
        assert d["ok"] and d["days"] == 7 and d["counts"]["forgets"] == 2


# ── the offline retrieval evaluation ──────────────────────────────────────────

def test_the_retrieval_evaluation_meets_its_floor_without_content():
    from agent_friday.services import memory_retrieval_eval as ev
    res = ev.run(k=5)
    assert res["queries"] >= 15 and res["messages"] >= 20
    assert res["recall_at_k"] >= ev.FLOOR["recall_at_k"] >= 0.8, res
    assert res["mrr"] >= ev.FLOOR["mrr"], res
    assert res["superseded_leaks"] == 0, res
    assert res["meets_floor"]
    dumped = json.dumps(res)
    assert "Wren" not in dumped and "Bluebell" not in dumped


def test_the_retrieval_evaluation_catches_a_superseded_turn_coming_back(monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import memory_retrieval_eval as ev
    real = ConversationMemory.search

    def leaky(self, query, n=5, **kw):
        kw["include_superseded"] = True
        return real(self, query, n=n, **kw)
    monkeypatch.setattr(ConversationMemory, "search", leaky)
    res = ev.run(k=5)
    assert res["superseded_leaks"] >= 1 and not res["meets_floor"]
