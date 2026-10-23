"""Forgetting a person reaches what was derived from the owner's conversations.

North star NS-15.7-2 ("forget traverses derived indexes, graph entities,
summaries, caches and proposals"), NS-15.7-3 (a deletion receipt listing
removed, not removable and retained-for-integrity items) and NS-36.2-9
(deletion traversal).

Through the real entry point (services/forget_person.forget, which POST
/api/people/forget calls): after forgetting a person, the turns that name them
are not recalled by ConversationMemory.search or found by conversation_fts.search
(not even after a rebuild from the transcripts), the day summary that named
them is gone and queued, and the summary written again from the cleaned index
does not mention them; card lines and pending suggestions naming them are
gone; graph retraction was requested for their turns; the receipt lists every
store with removed / not removable / retained. An unrelated person's data, the
transcript itself, and an ordinary word that collides with a forgotten name
are untouched.

Red on the base: forget() never touched the vector memory, conversation
search, the summaries or the memory card, and its receipt had no per-store
rows ("stores"), so the first recall assertion fails.

Synthetic data. The real ConversationMemory over an in-memory collection (CI
installs no Chroma), as tests/unit/test_correction_reaches_derived.py does.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from agent_friday.services import memory_derived as md


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
        if isinstance(where, dict) and isinstance(where.get("session_id"), str):
            keys = [k for k in keys if self.rows[k][1].get("session_id") == where["session_id"]]
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


DAY1, DAY2 = "2026-09-10", "2026-09-11"
CID = "conv-forget01"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    from agent_friday import core
    from agent_friday.services import egress_gate
    from agent_friday.services import forget_person as fp
    from agent_friday.services import forget_registry as fr
    from agent_friday.services import memory_dreaming
    from agent_friday.services import memory_proposals
    from agent_friday.services import memory_review
    from agent_friday.services import model_router
    from agent_friday.services import user_model
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(fp, "_friday_dir", lambda: tmp_path, raising=False)
    # Stores whose paths were bound at import: pointed at this test's home.
    for mod in (user_model, memory_dreaming, memory_proposals):
        monkeypatch.setattr(mod, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(user_model, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(memory_dreaming, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(memory_dreaming, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(memory_proposals, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(core, "CONTEXT_LOG_DIR", tmp_path / "vault" / "context-log")
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "vault" / "egress-log.jsonl")
    monkeypatch.setattr(fr, "_owner_names", lambda: set())
    memory_review._reset_for_tests()
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(model_router, "SESSION_SUMMARY_DIR", tmp_path / "memory" / "session_summaries")
    graph = []
    monkeypatch.setattr(md, "queue_graph_retraction", lambda texts: graph.append(list(texts)) or {})
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    return tmp_path, mem, graph


def _ts(day, hour):
    return datetime.strptime("%s %02d:00" % (day, hour), "%Y-%m-%d %H:%M").timestamp()


def _seed(tmp, mem, people, turns, card=(), pending=()):
    """people: {key: (name, aliases)}; turns: [(id, role, day, hour, text)]."""
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    graph = {"people": {k: {"name": n, "aliases": list(a), "entity_type": "human"}
                        for k, (n, a) in people.items()}}
    (tmp / "people_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    cv.create(title="Boat", cid=CID)
    with open(tmp / "conversations" / CID / "messages.jsonl", "w", encoding="utf-8") as fh:
        for mid, role, day, hour, text in turns:
            fh.write(json.dumps({"id": mid, "role": role, "text": text, "ts": _ts(day, hour)}) + "\n")
            mem.index(text, role, session_id=day, timestamp="%sT%02d:00:00" % (day, hour), msg_id=mid)
    fts.build(force=True)
    cm = tmp / "core_memory"
    cm.mkdir(parents=True, exist_ok=True)
    (cm / "ME.md").write_text("".join("- %s\n" % line for line in card), encoding="utf-8")
    (cm / "pending.jsonl").write_text("".join(json.dumps(
        {"id": "pend-%d" % i, "ts": 1.0, "target": "user", "op": "add", "text": t, "old": "",
         "source": "dream", "reason": ""}) + "\n" for i, t in enumerate(pending)), encoding="utf-8")


def _summary(day, text):
    from agent_friday.services import model_router
    model_router._save_session_summary(day, text)
    return model_router._session_summary_path(day, "md")


DANA_TURN = "Dana Okafor said the boat survey is booked for the first week of October."
DANA_REPLY = "Noted: Dana will send the surveyor's report once the survey is done."
FEE_TURN = "The mooring fee went up to 520 pounds this year, payable in March."
PRIYA_TURN = "Priya Shah sent the insurance papers for the boat this morning."


def test_forgetting_a_person_reaches_every_derived_store(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import core_memory
    from agent_friday.services import forget_person as fp
    from agent_friday.services import model_router
    _seed(tmp, mem,
          {"dana_okafor": ("Dana Okafor", ["Dana"]), "priya_shah": ("Priya Shah", [])},
          [("m-dana", "user", DAY1, 10, DANA_TURN), ("m-reply", "friday", DAY1, 10, DANA_REPLY),
           ("m-fee", "user", DAY1, 11, FEE_TURN), ("m-priya", "user", DAY2, 9, PRIYA_TURN)],
          card=["Dana Okafor is the owner's boat surveyor.", "Priya Shah handles the boat insurance.",
                "The owner will sail to Harwich in June."],
          pending=["Dana prefers phone calls in the morning.", "Priya Shah prefers email."])
    day1 = _summary(DAY1, "Dana Okafor booked the boat survey; the mooring fee went up.")
    day2 = _summary(DAY2, "Priya Shah sent the insurance papers.")
    (tmp / "actions").mkdir()
    (tmp / "actions" / "receipts.jsonl").write_text(json.dumps(
        {"id": "act-1", "tool": "send_email", "summary": "Emailed Dana Okafor the survey dates"}) + "\n",
        encoding="utf-8")
    # Seeded as recallable, so the checks below are not vacuous.
    assert any("Dana" in r["text"] for r in mem.search("Dana Okafor boat survey", n=10))
    assert fts.search("Dana")

    receipt = fp.forget("Dana Okafor")

    # (1) The recall indexes: never recalled, never searched, not even after a rebuild.
    assert not any("Dana" in r["text"] for r in mem.search("Dana Okafor boat survey", n=10)), \
        "a forgotten person's turn is still recalled by conversation memory"
    assert fts.search("Dana") == [], "a forgotten person's turn is still found by conversation search"
    fts.build(force=True)
    assert fts.search("Dana") == [], "a rebuild from the transcript brought the turn back into search"
    assert any(r["text"] == PRIYA_TURN for r in mem.search("Priya insurance papers", n=10))
    assert fts.search("Priya") and fts.search("mooring")
    # The transcript itself follows forget-person's policy: kept, in the owner's words.
    assert DANA_TURN in (tmp / "conversations" / CID / "messages.jsonl").read_text(encoding="utf-8")

    # (2) Day summaries: the one naming them is gone and queued; the other is untouched.
    assert not day1.exists(), "the day summary still names the forgotten person"
    assert day2.exists()
    assert DAY1 in json.loads((tmp / "memory" / "session_summaries" / "redo.json").read_text())

    # (3) Memory card lines and pending suggestions naming them are gone; the rest stay.
    lines = core_memory.entries("user")
    assert not any("Dana" in line for line in lines)
    assert "Priya Shah handles the boat insurance." in lines
    assert "The owner will sail to Harwich in June." in lines
    waiting = [r["text"] for r in core_memory.pending()]
    assert waiting == ["Priya Shah prefers email."]
    journal = (tmp / "core_memory" / "changes.jsonl")
    assert "Dana" not in (journal.read_text(encoding="utf-8") if journal.exists() else "")

    # (4) Graph retraction was requested for their turns, and not for the others.
    asked = [t for batch in graph for t in batch]
    assert DANA_TURN in asked and DANA_REPLY in asked
    assert PRIYA_TURN not in asked and FEE_TURN not in asked

    # The receipt: every store, each with removed / not removable / retained.
    rows = {r["store"]: r for r in receipt["stores"]}
    for store in ("people_graph", "kg_entities", "conversation_recall", "conversation_graph",
                  "day_summaries", "memory_card", "memory_card_pending", "action_receipts",
                  "memory_proposals", "library", "wiki_notes"):
        assert store in rows, "the receipt does not list %s" % store
    for r in receipt["stores"]:
        assert {"removed", "kind", "not_removable", "retained"} <= set(r)
    assert rows["conversation_recall"]["removed"] == 2
    assert rows["day_summaries"]["removed"] == 1
    assert rows["memory_card"]["removed"] == 1 and rows["memory_card_pending"]["removed"] == 1
    assert rows["conversation_graph"]["removed"] == 2
    kept = rows["conversation_recall"]["retained"]
    assert kept and kept[0]["count"] == 2 and kept[0]["reason"]
    assert rows["action_receipts"]["retained"][0]["count"] == 1
    assert rows["action_receipts"]["removed"] == 0
    # Counts and kinds only: no content beyond what the receipt showed before.
    dumped = json.dumps(receipt["stores"])
    assert "survey" not in dumped and "surveyor" not in dumped and "Priya" not in dumped

    # The summary written again reads the cleaned index: it does not bring them back.
    monkeypatch.setattr(model_router, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr(model_router, "_predict_route_provider", lambda **k: "local")
    monkeypatch.setattr(model_router, "_generate_text",
                        lambda messages, **k: messages[0]["content"].split("===")[2].strip())
    assert md.redo_summaries() == 1
    again = model_router._load_session_summary(DAY1)
    assert "mooring fee" in again, "the day was not summarised again"
    assert "Dana" not in again, "the summary written again names the forgotten person"


def test_a_common_word_that_is_also_the_name_is_untouched(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import core_memory
    from agent_friday.services import forget_person as fp
    will_turn = "Will Hartley checked the rigging on the boat on Saturday."
    plain = "I will phone the marina tomorrow about the winter mooring."
    _seed(tmp, mem, {"will_hartley": ("Will Hartley", ["Will"])},
          [("m-will", "user", DAY1, 10, will_turn), ("m-plain", "user", DAY1, 11, plain)],
          card=["The owner will sail to Harwich in June.", "Will is the owner's rigger."],
          pending=["The owner will repaint the hull in spring."])

    receipt = fp.forget("Will Hartley")

    assert not any(r["text"] == will_turn for r in mem.search("rigging boat Saturday", n=10))
    assert any(r["text"] == plain for r in mem.search("phone marina winter mooring", n=10)), \
        "an ordinary 'will' was removed from recall"
    assert fts.search("marina"), "an ordinary 'will' was removed from conversation search"
    assert not fts.search("rigging")
    # "Will" on its own is an ordinary word as well as a name: the alias is
    # refused (and the receipt says so); the full name is still forgotten.
    assert core_memory.entries("user") == ["The owner will sail to Harwich in June.",
                                           "Will is the owner's rigger."]
    assert [r["text"] for r in core_memory.pending()] == ["The owner will repaint the hull in spring."]
    rows = {r["store"]: r for r in receipt["stores"]}
    assert rows["conversation_recall"]["removed"] == 1
    assert [t for batch in graph for t in batch] == [will_turn]
    refused = rows["names"]["not_removable"]
    assert len(refused) == 1 and "'will'" in refused[0]["what"] and "ordinary word" in refused[0]["reason"]
    assert not any(str(a).lower() == "will" for e in fp.list_forgotten() for a in e.get("aliases") or [])


# ── review findings: the forgotten stay forgotten, the receipt is exact ──────

DANA = {"dana_okafor": ("Dana Okafor", ["Dana"]), "priya_shah": ("Priya Shah", [])}
TURNS = [("m-dana", "user", DAY1, 10, DANA_TURN), ("m-reply", "friday", DAY1, 10, DANA_REPLY),
         ("m-fee", "user", DAY1, 11, FEE_TURN), ("m-priya", "user", DAY2, 9, PRIYA_TURN)]


def test_a_rebuild_through_the_vector_memory_does_not_index_a_forgotten_turn_again(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import forget_person as fp
    _seed(tmp, mem, DANA, TURNS)

    fp.forget("Dana Okafor")
    assert fts.is_forgotten("m-dana") and fts.is_forgotten("m-reply") and not fts.is_forgotten("m-fee")

    # What a rebuild from the transcripts does (M4's rebuild_from_transcripts
    # re-indexes each line through ConversationMemory.index with its id).
    for mid, role, day, hour, text in TURNS:
        mem.index(text, role, session_id=day, timestamp="%sT%02d:00:00" % (day, hour), msg_id=mid)
    assert not any("Dana" in r["text"] for r in mem.search("Dana Okafor boat survey", n=10)), \
        "re-indexing a forgotten turn by its id brought it back into recall"
    assert "m-dana" not in mem._collection.rows and "m-reply" not in mem._collection.rows
    assert any(r["text"] == FEE_TURN for r in mem.search("mooring fee March", n=10))
    # Words said after the forget are the owner's new words: indexed as usual.
    assert mem.index("Dana Okafor called again today.", "user", session_id=DAY2, msg_id="m-new") == "m-new"


def test_the_review_buffer_drops_every_exchange_that_names_them(home):
    tmp, mem, graph = home
    from collections import deque
    from agent_friday.services import forget_person as fp
    from agent_friday.services import memory_review as mr
    _seed(tmp, mem, DANA, TURNS)
    # Buffered after the transcript was written, as a turn still in flight is;
    # the texts are not the stored ones exactly (the old exact-string drop missed them).
    mr._BUFFERS[DAY1] = deque([{"user": "Dana Okafor rang about the survey", "friday": "Noted.", "owner": True},
                               {"user": FEE_TURN, "friday": "Noted.", "owner": True},
                               {"user": "Anything new?", "friday": "Dana will send the report.", "owner": True}],
                              maxlen=mr.WINDOW)

    fp.forget("Dana Okafor")

    assert [ex["user"] for ex in mr._BUFFERS[DAY1]] == [FEE_TURN]


def test_the_receipt_counts_only_what_left_the_vector_memory(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import forget_person as fp
    _seed(tmp, mem, DANA, TURNS)
    monkeypatch.setattr(mem, "delete", lambda ids=(), exact_text=None: 0)     # a store that deletes nothing

    rows = {r["store"]: r for r in fp.forget("Dana Okafor")["stores"]}

    assert rows["conversation_recall"]["removed"] == 0, "turns still in the vector memory were counted as removed"
    left = [x for x in rows["conversation_recall"]["not_removable"] if x.get("count")]
    assert left and left[0]["count"] == 2 and "forget again" in left[0]["reason"]


@pytest.mark.parametrize("answer, kind", [
    ({}, "queued for retraction"),
    ({"queued": 2, "deferred": True}, "applied when the running graph pass saves"),
    ({"queued": 2, "entities": 1, "relationships": 2, "described_again": 0, "reports": 1, "refused": False},
     "retracted from the knowledge graph now (1 entities and 2 links removed"),
])
def test_the_graph_row_says_whether_the_turns_were_queued_or_taken_out(home, monkeypatch, answer, kind):
    tmp, mem, graph = home
    from agent_friday.services import forget_person as fp
    _seed(tmp, mem, DANA, TURNS)
    monkeypatch.setattr(md, "queue_graph_retraction", lambda texts: dict(answer))

    row = {r["store"]: r for r in fp.forget("Dana Okafor")["stores"]}["conversation_graph"]

    assert row["removed"] == 2 and kind in row["kind"]


def test_a_store_failure_reaches_the_receipt_as_marked_exception_text(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import forget_person as fp
    from agent_friday.services import forget_registry as fr
    from agent_friday.user_errors import ExceptionText
    _seed(tmp, mem, DANA, TURNS)

    def boom(days):
        raise RuntimeError(r"could not unlink C:\Users\someone\.friday\memory\x.md")
    monkeypatch.setattr(md, "mark_summaries", boom)
    fr._ensure_defaults()
    monkeypatch.setitem(fr._STORES, "zz_broken", (lambda n, e: 0, lambda n, e: 1 / 0))

    rows = {r["store"]: r for r in fp.forget("Dana Okafor")["stores"]}

    reason = rows["day_summaries"]["not_removable"][0]["reason"]
    assert isinstance(reason, ExceptionText), "an exception's text reached the receipt unmarked"
    assert isinstance(rows["zz_broken"]["not_removable"][0]["reason"], ExceptionText)


def test_the_user_model_dreams_and_chat_context_forget_them_and_the_logs_are_listed_as_kept(home):
    tmp, mem, graph = home
    from agent_friday.routes import chat as chat_mod
    from agent_friday.services import core_memory
    from agent_friday.services import forget_person as fp
    from agent_friday.services import memory_dreaming as dr
    from agent_friday.services import user_model
    _seed(tmp, mem, DANA, TURNS)
    # The old fact list: the one-time move onto the card has not run yet.
    user_model.note_fact("bio", "Dana Okafor is my boat surveyor.", source="manual")
    user_model.note_fact("preference", "I pay the mooring fee in March.", source="manual")
    topics = [{"topic": "dana", "mentions": 2}, {"topic": "okafor", "mentions": 1},
              {"topic": "mooring", "mentions": 1}]
    facts = [{"category": "preference", "text": "I prefer Dana Okafor for the survey.", "confidence": 0.7},
             {"category": "workflow", "text": "We decided to pay the mooring fee in March.", "confidence": 0.6}]
    summary = dr._summarize(DAY1, 3, topics, facts, 0)
    dr._persist(DAY1, 3, topics, facts, 0, summary)
    dr._write_markdown(DAY1, 3, topics, facts, 0, summary)
    logs = tmp / "vault" / "context-log"
    logs.mkdir(parents=True)
    (logs / ("%s.jsonl" % DAY1)).write_text(json.dumps({"text": DANA_TURN}) + "\n"
                                            + json.dumps({"text": FEE_TURN}) + "\n", encoding="utf-8")
    (tmp / "vault" / "egress-log.jsonl").write_text(
        json.dumps({"provider": "anthropic", "reason": "withheld: Dana Okafor's address"}) + "\n"
        + json.dumps({"provider": "anthropic", "reason": "allowed"}) + "\n", encoding="utf-8")

    receipt = fp.forget("Dana Okafor")
    rows = {r["store"]: r for r in receipt["stores"]}

    # The older user model, and the one-time move cannot carry the line back.
    left = [f["text"] for f in user_model._recent_facts(50)]
    assert left == ["I pay the mooring fee in March."]
    assert rows["user_model"]["removed"] == 1
    core_memory.snapshot()
    card = core_memory.entries("user") + [r["text"] for r in core_memory.pending()]
    assert not any("Dana" in line for line in card)
    assert "I pay the mooring fee in March." in card

    # The dreaming log: items and topics naming them go, the day's summary is written again.
    dream = dr.recent_dreams(5)[0]
    assert [t["topic"] for t in dream["topics"]] == ["mooring"]
    assert [f["text"] for f in dream["consolidated"]] == ["We decided to pay the mooring fee in March."]
    assert "dana" not in dream["summary"].lower()
    md_text = (tmp / "dreams" / ("%s.md" % DAY1)).read_text(encoding="utf-8")
    assert "dana" not in md_text.lower() and "okafor" not in md_text.lower() and "mooring" in md_text
    assert rows["dreams"]["removed"] == 3

    # The chat's replayed context: the owner still sees the message; the model gets a placeholder.
    said = [m["content"] for m in chat_mod._conv_context(CID)]
    assert DANA_TURN not in said and DANA_REPLY not in said and FEE_TURN in said
    assert chat_mod._FORGOTTEN_NOTE in said

    # Audit logs are kept, and the receipt says so, with why and for how long.
    ctx = rows["context_log"]["retained"]
    assert rows["context_log"]["removed"] == 0 and ctx[0]["count"] == 1 and "retention" in ctx[0]["reason"]
    eg = rows["egress_log"]["retained"]
    assert rows["egress_log"]["removed"] == 0 and eg[0]["count"] == 1 and eg[0]["reason"]
    assert "replayed to the model" in rows["conversation_recall"]["retained"][0]["reason"]


def test_forgetting_twice_is_safe_and_the_second_receipt_is_all_zeros(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import forget_person as fp
    _seed(tmp, mem, DANA, TURNS, card=["Dana Okafor is the owner's boat surveyor."],
          pending=["Dana prefers phone calls in the morning."])
    _summary(DAY1, "Dana Okafor booked the boat survey; the mooring fee went up.")
    first = fp.forget("Dana Okafor")
    assert {r["store"]: r for r in first["stores"]}["conversation_recall"]["removed"] == 2
    # The day is summarised again from the cleaned index before the second run.
    _summary(DAY1, "The mooring fee went up.")
    graph.clear()

    second = fp.forget("Dana Okafor")

    assert all(r["removed"] == 0 for r in second["stores"]), \
        [(r["store"], r["removed"]) for r in second["stores"] if r["removed"]]
    mine = ("conversation_recall", "conversation_graph", "day_summaries", "memory_card", "memory_card_pending",
            "user_model", "dreams", "action_receipts", "context_log", "egress_log")
    rows = {r["store"]: r for r in second["stores"]}
    assert all(rows[s]["not_removable"] == [] for s in mine)
    assert not [k for k in second["removed"] if k.endswith("_error") and k[:-len("_error")] in mine]
    assert graph == [], "the second run queued the graph again"
    assert (tmp / "memory" / "session_summaries" / ("%s.md" % DAY1)).exists(), \
        "the second run removed the clean summary written again"


# ── matching (forget_registry.subject_matcher) ──────────────────────────────

@pytest.fixture
def matcher(monkeypatch):
    from agent_friday.services import forget_registry as fr
    monkeypatch.setattr(fr, "_given_forms", lambda names: set())
    monkeypatch.setattr(fr, "_owner_names", lambda: set())
    monkeypatch.setattr(fr, "_friday_names", lambda: {"friday", "agent friday"})
    return fr


def test_a_name_of_several_words_matches_in_any_case(matcher):
    m = matcher.subject_matcher({"dana okafor"})
    assert m("DANA OKAFOR booked it") and m("ask dana   okafor") and m("Dana Okafor.")
    assert not m("Danaokafor") and not m("dana okafors")


def test_a_one_word_name_stays_case_sensitive(matcher):
    m = matcher.subject_matcher({"dana"})
    assert m("Dana said so") and not m("dana said so") and not m("Danae said so")


def test_an_apostrophe_name_matches_as_typed_and_with_the_inner_capital(matcher, monkeypatch):
    m = matcher.subject_matcher({"o'brien"})
    assert m("O'Brien called") and m("O\u2019Brien called") and m("O'brien called") and not m("o'brien called")
    monkeypatch.setattr(matcher, "_given_forms", lambda names: {"DeVito"})
    m = matcher.subject_matcher({"devito"})
    assert m("DeVito called") and m("Devito called") and not m("devito called")
    assert "DeVito" in m.forms


def test_text_and_names_are_compared_after_nfc_normalisation(matcher):
    import unicodedata
    m = matcher.subject_matcher({unicodedata.normalize("NFD", "zoë")})
    assert m(unicodedata.normalize("NFD", "Zoë came")) and m(unicodedata.normalize("NFC", "Zoë came"))
    assert not m("Zoe came")


def test_an_email_matches_only_whole(matcher):
    m = matcher.subject_matcher(set(), {"dana@x.com"})
    assert m("write to dana@x.com.") and m("DANA@X.COM wrote")
    assert not m("write to dana@x.com.au") and not m("write to dana@x.com-mail.org")


def test_the_receipt_reports_the_name_forms_matched(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_person as fp
    _seed(tmp, mem, DANA, TURNS)
    assert fp.forget("Dana Okafor")["matched_forms"] == ["Dana", "Dana Okafor"]


# ── names that would take far more than the person ───────────────────────────

def test_the_owners_own_name_is_refused_as_an_alias_and_as_the_name_asked_for(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import core_memory
    from agent_friday.services import forget_person as fp
    from agent_friday.services import forget_registry as fr
    monkeypatch.setattr(fr, "_owner_names", lambda: {"stephen webster", "stephen", "webster"})
    _seed(tmp, mem, {"dana_okafor": ("Dana Okafor", ["Dana", "Stephen"])}, TURNS,
          card=["Dana Okafor is the owner's boat surveyor.", "Stephen will sail to Harwich in June."])

    rows = {r["store"]: r for r in fp.forget("Dana Okafor")["stores"]}

    assert core_memory.entries("user") == ["Stephen will sail to Harwich in June."]
    refused = rows["names"]["not_removable"]
    assert any("'stephen'" in x["what"] and "your own name" in x["reason"] for x in refused)
    assert not any("stephen" in str(a).lower() for e in fp.list_forgotten() for a in e.get("aliases") or [])

    receipt = fp.forget("Stephen Webster")
    assert "your own name" in receipt["refused"] and receipt["removed"] == {}
    assert not fp.is_forgotten("Stephen Webster")
    assert core_memory.entries("user") == ["Stephen will sail to Harwich in June."]


def test_fridays_name_and_common_words_are_refused_as_aliases(home):
    tmp, mem, graph = home
    from agent_friday.services import core_memory
    from agent_friday.services import forget_person as fp
    _seed(tmp, mem, {"may_lin": ("May Lin", ["May", "Friday", "Mayling"])}, TURNS,
          card=["May Lin fixed the engine.", "Mayling owes the owner a beer.", "In May the owner sails.",
                "Friday will book the crane."])

    rows = {r["store"]: r for r in fp.forget("May Lin")["stores"]}

    assert core_memory.entries("user") == ["In May the owner sails.", "Friday will book the crane."]
    reasons = {x["what"]: x["reason"] for x in rows["names"]["not_removable"]}
    assert "Friday's own name" in reasons["the name 'friday' on its own"]
    assert "ordinary word" in reasons["the name 'may' on its own"]


# ── pre-existing collisions ──────────────────────────────────────────────────

def test_the_community_summary_scrub_takes_whole_words_only():
    from agent_friday.services import forget_person as fp
    out = fp._scrub_name_from_text({"summary": "Ann led Annual planning with ANN and annabel."}, ["ann"])
    assert out["summary"] == "[removed] led Annual planning with [removed] and annabel."


def test_forgetting_a_person_keeps_proposals_that_only_say_will(home):
    from agent_friday.services import memory_proposals as mp
    conn = mp._connect()
    conn.executemany("INSERT INTO proposed_facts(fact_id, ts, day, category, text, evidence, confidence, model) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     [("p1", 1.0, DAY1, "plan", "The owner will call the marina.", "I will call them", 0.45, "m"),
                      ("p2", 1.0, DAY1, "relationship", "Will Hartley rigged the boat.", "", 0.45, "m")])
    conn.commit()
    conn.close()

    assert mp.people_purge({"will hartley", "will"}, set()) == 1

    conn = mp._connect()
    left = [r[0] for r in conn.execute("SELECT fact_id FROM proposed_facts")]
    conn.close()
    assert left == ["p1"]
