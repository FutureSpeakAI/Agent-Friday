"""The owner's memory search and "why do you know this" (services/memory_search;
north star NS-15.11-1, NS-36.2-11).

One search reaches past conversations, the memory card, its review queue,
user-model facts and the Memory keeper's suggestions, each result with its
source and whether it still counts; corrected things stay hidden unless asked
for; "why" gives the original words and date, who put a card line there and
from which suggestion, a fact's source, and which replies recalled a turn.

The real ConversationMemory over an in-memory collection with Chroma's
interface (CI installs no Chroma), the real full-text index, transcript, card,
user model and proposal table, all under tmp_path. Synthetic text.

Red on 51a160fcb: services/memory_search did not exist (every test here fails
at import), the card's change record did not name the suggestion a kept line
came from, and nothing recorded which replies recalled a stored turn.
"""
from __future__ import annotations

import re
import time
import uuid

import pytest

from agent_friday.services import conversation_fts as fts
from agent_friday.services import conversations as cv
from agent_friday.services import core_memory as cm
from agent_friday.services import memory_influence as mi
from agent_friday.services import memory_proposals as mp
from agent_friday.services import memory_search as ms
from agent_friday.services import prompt_freeze
from agent_friday.services import user_model as um

#: The real off-record check, before the fixture below stands one in (the off-record tests use it).
_REAL_OFF_RECORD = mi._off_record


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class FakeCollection:
    """Chroma's collection interface, as ConversationMemory uses it
    (the pattern from tests/unit/test_memory_truth.py)."""

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
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


def _say(cid, text, role="user"):
    m = cv.append(cid, {"role": role, "text": text, "meta": {"kind": "turn", "via": "chat"}})
    return m["id"]


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


def _kinds(res):
    return {r["kind"] for r in res["results"]}


# ── search across kinds, with source and status ─────────────────────────────

def test_one_search_finds_a_fact_in_every_store_with_its_source_and_status(mem):
    cid = cv.create("Boats")["id"]
    mid = _say(cid, "My sailing boat Wren is moored at Harwich.")
    mem.index("My sailing boat Wren is moored at Harwich.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id=mid)
    cm.add("user", "Keeps a sailing boat called Wren.", actor="friday")
    cm.queue("user", "add", text="Wren needs new sails before spring.", source="dream:2026-09-03",
             reason="The owner mentioned the sails twice.")
    um.note_fact("bio", "Owns a boat called Wren.", confidence=0.8, source="proposed:2026-09-02")
    um.note_fact("bio", "Probably sails Wren most weekends.", confidence=0.5, source="dream:2026-09-04")
    _proposal("Wren is a wooden boat.", evidence="she's all oak, Wren")

    res = ms.search("Wren")
    assert res["ok"], res
    assert _kinds(res) >= {"conversation", "card", "suggestion", "fact", "proposal"}, res["results"]
    by = {(r["kind"], r["text"]): r for r in res["results"]}

    conv = next(r for r in res["results"] if r["kind"] == "conversation")
    assert conv["id"] == mid and conv["status"] == "current"
    assert conv["source"]["conversation_id"] == cid and conv["source"]["message_id"] == mid
    assert conv["source"]["conversation_title"] == "Boats" and conv["source"]["date"]

    card = by[("card", "Keeps a sailing boat called Wren.")]
    assert card["status"] == "current" and card["source"]["label"].startswith("Friday saved this")

    sug = by[("suggestion", "Wren needs new sails before spring.")]
    assert sug["status"] == "pending" and "nightly dreaming" in sug["source"]["label"]

    approved = by[("fact", "Owns a boat called Wren.")]
    assert approved["status"] == "current"
    assert approved["source"]["label"] == "Approved suggestion from 2026-09-02"
    inferred = by[("fact", "Probably sails Wren most weekends.")]
    assert inferred["status"] == "inferred"

    prop = by[("proposal", "Wren is a wooden boat.")]
    assert prop["status"] == "pending" and "2026-09-02" in prop["source"]["label"]

    only = ms.search("Wren", kinds=["fact"])
    assert _kinds(only) == {"fact"}


def test_a_corrected_turn_is_hidden_by_default_and_shown_with_its_reason_when_asked(mem):
    cid = cv.create("Boats")["id"]
    old = _say(cid, "The mooring fee for Wren is 400 pounds a year.")
    new = _say(cid, "The mooring fee for Wren went up to 520 pounds a year.")
    mem.index("The mooring fee for Wren is 400 pounds a year.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id=old)
    mem.index("The mooring fee for Wren went up to 520 pounds a year.", "user", session_id="2026-09-20",
              timestamp="2026-09-20T10:00:00", msg_id=new)
    mem.supersede([old], "the owner said the fee went up to 520")

    default = ms.search("mooring fee", kinds=["conversation"])
    ids = [r["id"] for r in default["results"]]
    assert new in ids and old not in ids, "a corrected turn came back by default"

    shown = ms.search("mooring fee", kinds=["conversation"], include_superseded=True)
    hit = next(r for r in shown["results"] if r["id"] == old)
    assert hit["status"] == "superseded"
    assert hit["status_reason"] == "the owner said the fee went up to 520"


def test_a_card_line_friday_replaced_shows_only_when_corrected_items_are_asked_for():
    cm.add("user", "Moors Wren at Harwich.", actor="friday")
    cm.replace("user", "Harwich", "Moors Wren at Ipswich since September.", actor="friday")
    plain = ms.search("Wren moors", kinds=["card"])
    assert [r["text"] for r in plain["results"]] == ["Moors Wren at Ipswich since September."]
    full = ms.search("Wren moors", kinds=["card"], include_superseded=True)
    old = next(r for r in full["results"] if r["text"] == "Moors Wren at Harwich.")
    assert old["status"] == "superseded" and "Ipswich" in old["status_reason"]
    # Its own kind, so the panel's "Why do you know this?" sends kind and id back as they came.
    assert old["kind"] == "card_change" and old["id"].startswith("card_change:")
    why = ms.why(old["kind"], old["id"])
    assert why["ok"] and why["status"] == "superseded" and why["source"]["replaced_by"].startswith("Moors Wren at Ipswich")


def test_a_person_the_owner_asked_to_forget_never_appears(mem, monkeypatch):
    cm.add("user", "Sails Wren with Jane Doe on Sundays.", actor="owner")
    cm.add("user", "Sails Wren alone in winter.", actor="owner")
    monkeypatch.setattr(ms, "_forgotten_names", lambda: {"jane doe"})
    res = ms.search("Wren sails", kinds=["card"])
    texts = [r["text"] for r in res["results"]]
    assert texts == ["Sails Wren alone in winter."]
    assert any("forget" in n for n in res["notes"])
    hidden = "card:user:" + cm.line_ref("Sails Wren with Jane Doe on Sundays.")
    assert ms.why("card", hidden)["ok"] is False


def _forget_jane(monkeypatch, emails=()):
    monkeypatch.setattr(ms, "_forgotten_names", lambda: {"jane doe"})
    monkeypatch.setattr(ms, "_forgotten_emails", lambda: set(emails))


def _hidden(w):
    return w["ok"] is False and w.get("hidden") is True


def test_a_forgotten_name_in_the_line_a_card_line_replaced_hides_its_why(monkeypatch):
    cm.add("user", "Sails Wren with Jane Doe.", actor="friday")
    cm.replace("user", "Jane Doe", "Sails Wren alone now.", actor="friday")
    _forget_jane(monkeypatch)
    res = ms.search("Wren sails", kinds=["card"])
    hit = next(r for r in res["results"] if r["text"] == "Sails Wren alone now.")
    # The line itself names nobody; the why's history ("it replaced ...") does.
    assert _hidden(ms.why(hit["kind"], hit["id"]))
    # The replaced line, asked for, is never shown either.
    full = ms.search("Wren sails", kinds=["card"], include_superseded=True)
    assert not any("Jane" in r["text"] for r in full["results"])


def test_a_forgotten_name_in_a_corrected_lines_reason_drops_the_result(monkeypatch):
    cm.add("user", "Moors Wren at Harwich.", actor="friday")
    cm.replace("user", "Harwich", "Moors Wren beside Jane Doe's boat.", actor="friday")
    _forget_jane(monkeypatch)
    full = ms.search("Wren Harwich", kinds=["card"], include_superseded=True)
    # The old line names nobody, but its status_reason quotes the new one.
    assert not any(r["kind"] == "card_change" for r in full["results"]), full["results"]
    assert any("forget" in n for n in full["notes"])


def test_a_forgotten_name_in_a_suggestions_old_line_or_reason(monkeypatch):
    cm.add("user", "Sails Wren with Jane Doe on Sundays.", actor="friday")
    q_old = cm.queue("user", "replace", old="Jane Doe", text="Sails Wren on Sundays.", source="review")
    q_reason = cm.queue("user", "add", text="Wren needs a new jib.", source="dream:2026-09-03",
                        reason="Jane Doe mentioned the jib.")
    assert q_old["ok"] and q_reason["ok"]
    _forget_jane(monkeypatch)
    res = ms.search("Wren", kinds=["suggestion"])
    ids = {r["id"] for r in res["results"]}
    assert q_old["id"] not in ids, "the line a suggestion would change names her"
    assert q_reason["id"] in ids
    assert _hidden(ms.why("suggestion", q_reason["id"])), "the suggestion's reason names her"


def test_a_forgotten_name_in_a_proposals_evidence_hides_its_why(monkeypatch):
    fid = _proposal("Wren is a wooden boat.", evidence="Jane Doe said she's all oak")
    _forget_jane(monkeypatch)
    res = ms.search("Wren wooden", kinds=["proposal"])
    assert [r["id"] for r in res["results"]] == [fid]
    w = ms.why("proposal", fid)
    assert _hidden(w) and "oak" not in str(w)


def test_a_forgotten_persons_address_or_name_in_any_case_drops_the_result(monkeypatch):
    # The owner's own lines (no near-duplicate check between these similar ones).
    for line in ("Emails the Wren crew list to JANE.D@Example.com.",
                 "Emails the Wren race entry to xjane.d@example.com.",
                 "Wren's skipper is jane   DOE.", "Wren's mate is Janet Doerr."):
        assert cm.add("user", line, actor="owner")["ok"], line
    _forget_jane(monkeypatch, emails={"jane.d@example.com"})
    texts = [r["text"] for r in ms.search("Wren", kinds=["card"])["results"]]
    assert not any("JANE.D@" in t for t in texts)
    assert not any("skipper" in t for t in texts), "a multi-word name matches in any case and spacing"
    # Whole words and whole addresses only.
    assert any("xjane.d@example.com" in t for t in texts)
    assert any("Janet Doerr" in t for t in texts)


def test_every_kind_search_returns_goes_back_to_why_unchanged(mem, monkeypatch):
    cid = cv.create("Boats")["id"]
    mid = _say(cid, "My sailing boat Wren is moored at Harwich.")
    mem.index("My sailing boat Wren is moored at Harwich.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id=mid)
    cm.add("user", "Keeps Wren at Harwich.", actor="friday")
    cm.replace("user", "Harwich", "Keeps Wren at Ipswich.", actor="friday")
    cm.queue("user", "add", text="Wren needs new sails.", source="dream:2026-09-03")
    um.note_fact("bio", "Owns a boat called Wren.", confidence=0.8, source="proposed:2026-09-02")
    _proposal("Wren is a wooden boat.", evidence="she's all oak, Wren")
    doc = {"doc_id": "d-17", "title": "Wren logbook", "pages": 3}
    monkeypatch.setattr(ms, "_documents", lambda q: [doc])
    monkeypatch.setattr(ms, "_document", lambda i: dict(doc) if i == "d-17" else None)

    res = ms.search("Wren", include_superseded=True, limit=50)
    assert res["ok"], res
    assert _kinds(res) == {"conversation", "card", "card_change", "suggestion", "fact", "proposal",
                           "document"}, res["results"]
    for hit in res["results"]:
        w = ms.why(hit["kind"], hit["id"])
        assert w["ok"], (hit["kind"], hit["id"], w)
        assert w["kind"] == hit["kind"] and w["id"] == hit["id"]


# ── why do you know this ─────────────────────────────────────────────────────

def test_why_for_a_conversation_turn_gives_the_original_message_and_its_date(mem):
    cid = cv.create("Boats")["id"]
    mid = _say(cid, "My sailing boat Wren is moored at Harwich.")
    mem.index("My sailing boat Wren is moored at Harwich.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id=mid)
    w = ms.why("conversation", mid)
    assert w["ok"], w
    assert w["text"] == "My sailing boat Wren is moored at Harwich."
    assert w["source"]["conversation_id"] == cid and w["source"]["conversation_title"] == "Boats"
    assert w["source"]["said_by"] == "you" and re.match(r"\d{4}-\d{2}-\d{2}", w["source"]["date"])
    assert "You said this" in w["summary"] and "Boats" in w["summary"]

    mem.supersede([mid], "moved to Ipswich")
    w2 = ms.why("conversation", mid)
    assert w2["status"] == "superseded"
    assert any("moved to Ipswich" in ev["text"] for ev in w2["history"])


def test_why_for_a_card_line_says_who_added_it_and_from_which_suggestion():
    q = cm.queue("user", "add", text="Races Wren in the autumn series.", source="dream:2026-09-05",
                 reason="The owner talked about the autumn series twice.")
    assert cm.decide(q["id"], True)["ok"]
    cm.add("user", "Keeps Wren's logbook on paper.", actor="friday")
    cm.add("user", "Wren's mooring buoy is painted vermilion.", actor="owner")

    hits = {r["text"]: r for r in ms.search("Wren", kinds=["card"])["results"]}

    kept = ms.why("card", hits["Races Wren in the autumn series."]["id"])
    assert kept["ok"], kept
    by = kept["source"]["added_by"]
    assert by["actor"] == "owner"
    assert by["suggestion"]["suggestion"] == q["id"]
    assert by["suggestion"]["source"] == "dream:2026-09-05"
    assert "nightly dreaming" in by["suggestion"]["from"]
    assert "You kept this from a suggestion by nightly dreaming" in kept["summary"]
    assert kept["influence"]["always"] is True

    friday = ms.why("card", hits["Keeps Wren's logbook on paper."]["id"])
    assert friday["source"]["added_by"]["actor"] == "friday"
    assert friday["summary"].startswith("Friday saved this in a conversation")

    owner = ms.why("card", hits["Wren's mooring buoy is painted vermilion."]["id"])
    assert owner["source"]["added_by"]["actor"] == "owner"
    assert owner["summary"].startswith("You wrote this")
    # The owner's own words are still not copied into the change record.
    on_disk = (cm._dir() / "changes.jsonl").read_text(encoding="utf-8")
    assert "vermilion" not in on_disk


def test_why_for_a_user_model_fact_gives_its_source_confidence_and_evidence():
    _proposal("Owns a boat called Wren.", evidence="my boat Wren", status="approved")
    um.note_fact("bio", "Owns a boat called Wren.", confidence=0.8, source="proposed:2026-09-02")
    um.note_fact("bio", "Probably sails Wren most weekends.", confidence=0.5, source="dream:2026-09-04")
    facts = {r["text"]: r for r in ms.search("Wren", kinds=["fact"])["results"]}

    a = ms.why("fact", facts["Owns a boat called Wren."]["id"])
    assert a["ok"] and a["status"] == "current"
    assert a["source"]["origin"] == "proposed:2026-09-02" and a["source"]["confidence"] == 0.8
    assert a["source"]["evidence"] == "my boat Wren"

    b = ms.why("fact", facts["Probably sails Wren most weekends."]["id"])
    assert b["status"] == "inferred" and b["source"]["origin"] == "dream:2026-09-04"
    assert "Nobody has confirmed it" in b["summary"]


def test_why_for_a_proposal_gives_the_quote_it_rests_on():
    fid = _proposal("Wren is a wooden boat.", evidence="she's all oak, Wren")
    w = ms.why("proposal", fid)
    assert w["ok"] and w["status"] == "pending"
    assert w["source"]["evidence"] == "she's all oak, Wren"


def test_influence_lists_the_replies_that_recalled_a_turn(mem, monkeypatch):
    from agent_friday.services import model_router
    from agent_friday.services import turn_vector
    cid = cv.create("Boats")["id"]
    mid = _say(cid, "My sailing boat Wren is moored at Harwich.")
    mem.index("My sailing boat Wren is moored at Harwich.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id=mid)
    none_yet = ms.why("conversation", mid)["influence"]
    assert none_yet["count"] == 0
    # The record keeps only its newest rows: it says "recently", never "since the record began".
    assert none_yet["summary"] == "No reply has recalled it recently."

    monkeypatch.setattr(turn_vector, "compute", lambda *a, **k: None)      # no model in a test
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    block = model_router._build_memory_context_block("where is my boat Wren moored", cid, min_relevance=0.0)
    assert "Wren" in block

    inf = ms.why("conversation", mid)["influence"]
    assert inf["recorded"] and inf["count"] == 1 and inf["last"]
    assert inf["recent"][0]["conversation_title"] == "Boats"
    # Ids and times only: the record holds no recalled words.
    assert "Harwich" not in mi._path().read_text(encoding="utf-8")


def test_nothing_is_recorded_off_the_record(monkeypatch):
    monkeypatch.setattr(mi, "_off_record", lambda *a, **k: True)
    assert mi.record("conversation", ["m-1"], session_id="c-1") is False
    assert mi.uses("conversation", "m-1")["count"] == 0


@pytest.mark.parametrize("stops_storage", [True, False])
def test_a_recall_into_an_off_record_turn_is_not_recorded(mem, monkeypatch, stops_storage):
    # The real check, through the model_router hook, with the real setting: off the record is enough,
    # whether or not it also stops storage (as conversation_fts never indexes an off-record message).
    from agent_friday.services import model_router
    from agent_friday.services import off_record
    from agent_friday.services import turn_vector
    monkeypatch.setattr(mi, "_off_record", _REAL_OFF_RECORD)
    settings = {"memory_recall_enabled": True, "off_record": True, "off_record_stops_storage": stops_storage}
    monkeypatch.setattr(off_record, "_settings", lambda: dict(settings))
    cid = cv.create("Boats")["id"]
    mid = _say(cid, "My sailing boat Wren is moored at Harwich.")
    mem.index("My sailing boat Wren is moored at Harwich.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id=mid)
    monkeypatch.setattr(turn_vector, "compute", lambda *a, **k: None)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    monkeypatch.setattr(model_router, "_load_settings", lambda: dict(settings))
    model_router._build_memory_context_block("where is my boat Wren moored", cid, min_relevance=0.0)
    assert mi.uses("conversation", mid)["count"] == 0
    assert not mi._path().exists() or "Wren" not in mi._path().read_text(encoding="utf-8")
    # And with off-record over, the same hook records it.
    settings["off_record"] = False
    model_router._build_memory_context_block("where is my boat Wren moored", cid, min_relevance=0.0)
    assert mi.uses("conversation", mid)["count"] == 1


def test_deleting_a_message_drops_its_ids_from_the_record_including_an_older_random_id(mem, monkeypatch):
    from agent_friday.services import model_router
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    cid = cv.create("Boats")["id"]
    text = "My sailing boat Wren is moored at Harwich."
    mid = _say(cid, text)
    # An older turn: stored under a random id, so delete_message finds it by its text.
    old_id = mem.index(text, "user", session_id="2026-09-01", timestamp="2026-09-01T10:00:00")
    assert old_id and old_id != mid
    assert mi.record("conversation", [mid, old_id, "m-other"], session_id=cid)
    assert fts.delete_message(cid, mid)["ok"]
    assert mi.uses("conversation", mid)["count"] == 0
    assert mi.uses("conversation", old_id)["count"] == 0, "the vector row's own id stayed in the record"
    assert mi.uses("conversation", "m-other")["count"] == 1


def test_clearing_a_conversation_drops_its_messages_and_its_own_rows_from_the_record():
    cid = cv.create("Boats")["id"]
    other = cv.create("Garden")["id"]
    mid = _say(cid, "My sailing boat Wren is moored at Harwich.")
    keep = _say(other, "The roses need pruning in March.")
    assert mi.record("conversation", [mid, keep], session_id=other)
    assert mi.record("conversation", [keep], session_id=cid)
    cv.clear(cid, include_pinned=True)
    assert mi.uses("conversation", mid)["count"] == 0
    # The row written for a reply in the cleared conversation is gone; the other conversation's stays.
    assert mi.uses("conversation", keep)["count"] == 1
    assert [r["session"] for r in mi.uses("conversation", keep)["recent"]] == [other]


def test_the_search_past_conversations_tool_records_what_it_handed_the_model(monkeypatch):
    from agent_friday.services import conversation_search_tool as cst
    cid = cv.create("Boats")["id"]
    mid = _say(cid, "My sailing boat Wren is moored at Harwich.")
    monkeypatch.setattr(cst, "_loop_is_local", lambda: True)
    out = cst._tool_search_conversations_typed({"query": "Wren Harwich"})
    assert "Harwich" in out
    u = mi.uses("conversation", mid)
    assert u["count"] == 1
    assert "Harwich" not in mi._path().read_text(encoding="utf-8")


# ── bounds ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kw", [
    {"query": ""}, {"query": "x" * 201}, {"query": "Wren", "kinds": ["nope"]},
    {"query": "Wren", "since": "last week"}, {"query": "Wren", "limit": 0}, {"query": "Wren", "limit": 51},
    # The right shape but not a day: refused, not read as "no date".
    {"query": "Wren", "since": "2026-13-45"}, {"query": "Wren", "until": "2026-02-30"},
])
def test_search_parameters_are_bounded(kw):
    res = ms.search(**kw)
    assert res["ok"] is False and res["error"]


@pytest.mark.parametrize("kind,item", [("nope", "x"), ("card", ""), ("card", "a" * 121), ("card", "../etc/passwd")])
def test_why_parameters_are_bounded(kind, item):
    assert ms.why(kind, item)["ok"] is False


def test_a_card_line_ref_is_keyed_and_its_journal_is_per_card():
    import hashlib
    line = "Keeps a sailing boat called Wren."
    ref = cm.line_ref(line)
    # Not the plain hash of the words: a guess cannot be checked against the journal without the key.
    assert ref and ref != hashlib.sha256(line.lower().encode("utf-8")).hexdigest()[:16]
    assert ref == cm.line_ref(line) and cm._ref_key_path().exists()
    assert line.encode("utf-8") not in cm._ref_key_path().read_bytes()
    # The key is not kept among the card's files (everything there is the card and its text journal).
    assert cm._ref_key_path().parent != cm._dir() and not (cm._dir() / "line-ref.key").exists()
    # The same words on the other card are another line, with another change record.
    cm.add("user", line, actor="owner")
    cm.add("friday", line, actor="friday")
    assert [r["target"] for r in cm.journal_for(line, target="user")] == ["user"]
    assert [r["target"] for r in cm.journal_for(line, target="friday")] == ["friday"]
    w = ms.why("card", "card:user:" + ref)
    assert w["ok"] and w["source"]["added_by"]["actor"] == "owner"


def test_a_line_ref_key_left_beside_the_card_is_moved_out_and_kept():
    import hmac, hashlib
    from agent_friday.services import credential_store as cs
    old_key = b"k" * 32
    old = cm._dir() / "line-ref.key"
    cs.write_secret(old, old_key, harden=False)
    line = "Keeps a sailing boat called Wren."
    expect = hmac.new(old_key, line.lower().encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    assert cm.line_ref(line) == expect                  # the same key, so every old ref still matches
    assert not old.exists() and cs.read_secret(cm._ref_key_path()) == old_key


def test_an_unreadable_line_ref_key_beside_the_card_is_never_replaced(monkeypatch):
    from agent_friday.services import credential_store as cs
    old = cm._dir() / "line-ref.key"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"sealed elsewhere")
    real = cs.read_secret

    def _read(p):
        if str(p) == str(old):
            raise RuntimeError("cannot open")
        return real(p)
    monkeypatch.setattr(cs, "read_secret", _read)
    assert cm.line_ref("Keeps a sailing boat called Wren.") == ""
    assert old.read_bytes() == b"sealed elsewhere" and not cm._ref_key_path().exists()


def test_deleting_a_kept_line_purges_the_change_record_whose_reason_quotes_it():
    # Another kept line's suggestion reason quotes the deleted one. The line carries quotes, which a
    # JSON dump escapes: the reason is compared as parsed text.
    line = 'Calls Wren "the old girl".'
    assert cm.add("user", line, actor="owner")["ok"]
    q = cm.queue("user", "add", text="Is fond of the boat.", source="dream:2026-09-05",
                 reason="The card says:\n" + line)
    assert cm.decide(q["id"], True)["ok"]
    assert any("old girl" in str((r.get("via") or {}).get("reason")) for r in cm.journal_rows())
    assert cm.remove("user", line, actor="owner")["ok"]
    on_disk = (cm._dir() / "changes.jsonl").read_text(encoding="utf-8") \
        if (cm._dir() / "changes.jsonl").exists() else ""
    assert "old girl" not in on_disk
