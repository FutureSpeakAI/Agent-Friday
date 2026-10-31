"""Near-duplicate memories do not crowd out distinct evidence (north star
NS-14.6-2), on every path that fills a turn's context with recalled items:
conversation recall (the RELEVANT PAST CONVERSATIONS block), the past-
conversation search tool (conversation_recall over the full-text index),
Library passages and wiki excerpts. The pure helper is
services/recall_dedupe.

CI does not install Chroma, so ConversationMemory runs over a small in-memory
collection with Chroma's interface (as in test_memory_truth). Synthetic text;
nothing reaches a network or loads a model."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest


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
def rd():
    from agent_friday.services import recall_dedupe
    return recall_dedupe


def _block(monkeypatch, memory, message, session="s-today"):
    from agent_friday.services import model_router
    from agent_friday.services import turn_vector
    monkeypatch.setattr(turn_vector, "compute", lambda *a, **k: None)      # no model in a test
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: memory)
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    return model_router._build_memory_context_block(message, session, min_relevance=0.0)


def _lines(block):
    return [ln for ln in block.splitlines() if ln.startswith("- [")]


# Six repeats of one statement, oldest first; the last is the newest. They
# differ only by case, punctuation, articles and filler words.
_REPEATS = [
    ("m-dup-1", "2026-05-01T09:00:00", "The mooring fee at Harwich is 400 a year."),
    ("m-dup-2", "2026-05-08T09:00:00", "Mooring fee at Harwich is 400 a year."),
    ("m-dup-3", "2026-05-15T09:00:00", "the mooring fee at harwich is 400 per year"),
    ("m-dup-4", "2026-05-22T09:00:00", "The mooring fee at Harwich is 400 a year!"),
    ("m-dup-5", "2026-06-01T09:00:00", "Actually, the mooring fee at Harwich is really 400 a year."),
    ("m-dup-6", "2026-09-20T09:00:00", "The mooring fee at Harwich is 400 a year."),
]
_DISTINCT = ("m-office", "2026-04-01T09:00:00", "The Harwich harbour office shuts at 5pm on Sundays.")


# ── (a) conversation recall: the distinct turn gets a slot, the newest repeat is cited ──

def test_near_identical_turns_do_not_crowd_out_a_distinct_one_and_the_newest_is_cited(mem, monkeypatch):
    for mid, ts, text in _REPEATS + [_DISTINCT]:
        mem.index(text, "user", session_id=ts[:10], timestamp=ts, msg_id=mid)
    block = _block(monkeypatch, mem, "what is the mooring fee at harwich")
    lines = _lines(block)
    assert any("ref m-office" in ln for ln in lines), \
        "the distinct turn was crowded out by repeats of one statement:\n" + "\n".join(lines)
    repeat_lines = [ln for ln in lines if "ref m-dup-" in ln]
    assert len(repeat_lines) == 1, "the same statement filled several slots:\n" + "\n".join(lines)
    assert "ref m-dup-6" in repeat_lines[0], "the repeat kept was not the newest: " + repeat_lines[0]
    assert repeat_lines[0].startswith("- [2026-09-20 ref m-dup-6]")


def test_the_slot_count_is_kept_and_freed_slots_go_to_distinct_evidence(monkeypatch):
    class Mem:
        asked = []

        def available(self):
            return True

        def search(self, message, n=5, **kw):
            self.asked.append(n)
            rows = [{"id": f"r{i}", "text": "My boat Wren is moored at Harwich marina.",
                     "timestamp": f"2026-0{i + 1}-01T10:00:00", "role": "user", "relevance": 0.9}
                    for i in range(6)]
            rows += [{"id": f"d{i}", "text": t, "timestamp": "2026-07-0%dT10:00:00" % (i + 1),
                      "role": "user", "relevance": 0.5}
                     for i, t in enumerate(["Wren needs new antifouling before May.",
                                            "The Harwich marina gate code changed in June.",
                                            "Wren's engine was serviced by Ipswich Marine.",
                                            "I keep the spare tiller in the garage.",
                                            "Insurance for Wren renews every March."])]
            return rows[:n]
    m = Mem()
    block = _block(monkeypatch, m, "tell me about my boat")
    lines = _lines(block)
    assert m.asked and m.asked[-1] > 5, "recall did not fetch past the slots it fills"
    assert len(lines) == 5, lines
    assert sum("ref r" in ln for ln in lines) == 1
    assert "ref r5" in "\n".join(lines), "the newest repeat was not the one kept"


# ── (b) different values and negations are distinct evidence ────────────────

def test_statements_that_differ_by_a_value_or_a_negation_are_all_recalled(monkeypatch):
    class Mem:
        def available(self):
            return True

        def search(self, message, n=5, **kw):
            return [
                {"id": "m-400", "text": "The mooring fee is 400.", "timestamp": "2026-03-01T09:00:00",
                 "role": "user", "relevance": 0.9},
                {"id": "m-520", "text": "The mooring fee is 520.", "timestamp": "2026-08-01T09:00:00",
                 "role": "user", "relevance": 0.88},
                {"id": "m-going", "text": "I am going to the regatta on Saturday.",
                 "timestamp": "2026-08-02T09:00:00", "role": "user", "relevance": 0.7},
                {"id": "m-not", "text": "I am not going to the regatta on Saturday.",
                 "timestamp": "2026-08-03T09:00:00", "role": "user", "relevance": 0.69},
            ][:n]
    block = _block(monkeypatch, Mem(), "mooring fee and the regatta")
    text = "\n".join(_lines(block))
    for ref in ("m-400", "m-520", "m-going", "m-not"):
        assert "ref " + ref in text, f"{ref} was suppressed as a duplicate:\n{text}"
    # the newer value still comes first (the current state wins)
    lines = _lines(block)
    assert lines.index(next(ln for ln in lines if "m-520" in ln)) < \
        lines.index(next(ln for ln in lines if "m-400" in ln))


def test_numbers_and_negation_make_texts_distinct(rd):
    assert not rd.is_near_duplicate("mooring fee is 400", "mooring fee is 520")
    assert not rd.is_near_duplicate("The mooring fee is 400 a year.", "The mooring fee is 400 a month and 50 extra.")
    assert not rd.is_near_duplicate("the boat is insured", "the boat is not insured")
    assert not rd.is_near_duplicate("The boat is insured.", "The boat isn't insured.")
    assert not rd.is_near_duplicate("I will go to the regatta", "I won't go to the regatta")
    assert not rd.is_near_duplicate("the fee is 400", "the fee is unknown")
    # the same value written another way is the same value
    assert rd.is_near_duplicate("The fee is 400 a year.", "the fee is 400.00 a year")
    assert rd.is_near_duplicate("The fee is 1,200 a year.", "The fee is 1200 a year!")
    # topic-sharing statements that say different things are kept apart
    assert not rd.is_near_duplicate("Wren is moored at Harwich.", "Wren is for sale at Harwich.")


# ── (c) the helper: thresholds, ordering, stability ─────────────────────────

def test_only_case_punctuation_articles_and_fillers_may_differ(rd):
    a = "alpha bravo charlie delta echo foxtrot golf hotel india"
    b = "alpha bravo charlie delta echo foxtrot golf hotel juliet"          # 8 shared of 10
    assert rd.similarity(a, b) == pytest.approx(0.8)
    # high word overlap is not enough: one word swapped is a different statement
    assert not rd.is_near_duplicate(a, b)
    assert not rd.is_near_duplicate(a, b, threshold=0.1)
    # articles, case, punctuation and pure fillers do not count
    assert rd.is_near_duplicate("The mooring fee at Harwich is 400.", "the mooring fee at harwich is 400")
    assert rd.is_near_duplicate("I really just paid the deposit.", "I paid the deposit")
    assert rd.is_near_duplicate("Please book a table for 4.", "book the table for 4")
    # but dropping a preposition or a copula does
    assert not rd.is_near_duplicate("The mooring fee at Harwich is 400.", "mooring fee, harwich: 400")
    # a restatement with one more content word is not a duplicate (no containment rule)
    assert not rd.is_near_duplicate("Wren moored Harwich", "Wren moored Harwich marina")
    assert not rd.is_near_duplicate("Wren Harwich", "Wren Harwich marina")
    # a filler inside what a negator governs still counts
    assert not rd.is_near_duplicate("The food was not very good.", "The food was not good.")
    assert not rd.is_near_duplicate("ok", "thanks")
    assert rd.is_near_duplicate("ok", "OK")


def _it(i, text, ts=None, score=None, superseded=False):
    return {"id": i, "text": text, "timestamp": ts, "relevance": score, "superseded": superseded}


def test_a_group_keeps_its_newest_member_at_the_rank_of_its_best(rd):
    items = [
        _it("old", "The mooring fee at Harwich is 400 a year.", "2026-01-01T00:00:00", 0.9),
        _it("other", "The harbour office shuts at 5pm.", "2026-02-01T00:00:00", 0.8),
        _it("new", "mooring fee at Harwich is 400 a year!", "2026-09-01T00:00:00", 0.7),
    ]
    out = rd.select(items, 5)
    assert [x["id"] for x in out] == ["new", "other"]


def test_within_a_group_not_superseded_then_newest_then_best_score_then_rank(rd):
    t = "The mooring fee at Harwich is 400 a year."
    sup = rd.select([_it("a", t, "2026-01-01", 0.5), _it("b", t, "2026-09-01", 0.9, superseded=True)], 3)
    assert [x["id"] for x in sup] == ["a"], "a superseded item won its group"
    by_score = rd.select([_it("a", t, "2026-05-01", 0.5), _it("b", t, "2026-05-01", 0.9)], 3)
    assert [x["id"] for x in by_score] == ["b"]
    by_rank = rd.select([_it("a", t, "2026-05-01", 0.5), _it("b", t, "2026-05-01", 0.5)], 3)
    assert [x["id"] for x in by_rank] == ["a"]
    no_time = rd.select([_it("a", t, None, 0.9), _it("b", t, "2026-05-01", 0.1)], 3)
    assert [x["id"] for x in no_time] == ["b"], "an item with a date lost to one with none"
    # the full-text index's float `ts` counts as a time too
    fts = rd.select([{"message_id": "x", "text": t, "ts": 100.0, "score": 3.0},
                     {"message_id": "y", "text": t, "ts": 200.0, "score": 1.0}], 3)
    assert [x["message_id"] for x in fts] == ["y"]


def test_the_same_message_id_never_appears_twice(rd):
    items = [_it("m1", "Wren is moored at Harwich.", "2026-01-01", 0.9),
             _it("m1", "Wren is moored at Harwich.", "2026-01-01", 0.9),
             _it("m2", "Completely different: the gate code changed.", "2026-02-01", 0.5),
             {"message_id": "m2", "text": "the gate code changed", "ts": 5.0}]
    out = rd.select(items, 10)
    ids = [x.get("id") or x.get("message_id") for x in out]
    assert ids == ["m1", "m2"]


def test_limit_is_kept_and_selection_is_stable(rd):
    dup = "My boat Wren is moored at Harwich marina."
    items = [_it(f"r{i}", dup, f"2026-0{i + 1}-01", 0.9) for i in range(6)]
    items += [_it("d1", "Wren needs new antifouling before May.", "2026-07-01", 0.4),
              _it("d2", "The marina gate code changed in June.", "2026-07-02", 0.3),
              _it("d3", "Insurance for Wren renews every March.", "2026-07-03", 0.2)]
    out = rd.select(items, 3)
    assert [x["id"] for x in out] == ["r5", "d1", "d2"]
    assert rd.select(list(items), 3) == out                  # same input, same output
    shuffled = items[3:6] + items[:3] + items[6:]            # the group's members reordered
    assert [x["id"] for x in rd.select(shuffled, 3)] == ["r5", "d1", "d2"]
    assert rd.select(items, 0) == []
    assert rd.select([], 3) == []


def test_keep_first_for_sources_without_a_date(rd):
    out = rd.select([{"text": "Lease clause 4: rent is 900."}, {"text": "lease clause 4 rent is 900"},
                     {"text": "Lease clause 5: pets allowed."}], 5, keep="first", ident=lambda r: None)
    assert [x["text"] for x in out] == ["Lease clause 4: rent is 900.", "Lease clause 5: pets allowed."]


def test_overfetch_is_a_bounded_multiple(rd):
    assert rd.overfetch(5) == 15
    assert rd.overfetch(1) == 3
    assert rd.overfetch(12) == 32
    assert rd.overfetch(100) == 120
    assert rd.overfetch(3, factor=2) == 6


def test_suppressed_items_are_counted_without_text(rd, monkeypatch):
    monkeypatch.setattr(rd, "SUPPRESSED", {})
    t = "The mooring fee at Harwich is 400 a year."
    rd.select([_it("a", t), _it("b", t), _it("a", t), _it("c", "Gate code changed.")], 5, path="probe")
    counts = rd.suppressed_counts()
    assert counts == {"probe": 2}
    assert all(isinstance(v, int) for v in counts.values())


# ── the past-conversation search tool (full-text index) ──────────────────────

def test_conversation_search_collapses_repeats_and_keeps_the_newest(monkeypatch):
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversation_recall as rc
    rows = [{"message_id": mid, "conversation_id": "c1", "title": "", "ts": 1000.0 + k,
             "date": ts[:10], "role": "user", "via": "chat", "text": text, "sent_to": [],
             "score": 10.0 - k * 0.1}
            for k, (mid, ts, text) in enumerate(_REPEATS)]
    rows.append({"message_id": "m-office", "conversation_id": "c2", "title": "", "ts": 900.0,
                 "date": "2026-04-01", "role": "user", "via": "chat", "text": _DISTINCT[2],
                 "sent_to": [], "score": 2.0})
    asked = []

    def fake_search(query, since=None, until=None, limit=8):
        asked.append(limit)
        return rows[:limit]
    monkeypatch.setattr(fts, "search", fake_search)
    hits = rc.search("mooring fee harwich", limit=3)
    ids = [h["message_id"] for h in hits]
    assert asked and asked[0] > 3, "the search did not fetch past its slots"
    assert "m-office" in ids, "the distinct turn was crowded out: %s" % ids
    assert [i for i in ids if i.startswith("m-dup-")] == ["m-dup-6"], ids


# ── (d) Library passages and wiki excerpts ───────────────────────────────────

def test_library_evidence_leaves_a_repeated_passage_out(monkeypatch):
    from agent_friday.services.library import search as ls
    docs = {1: {"id": 1, "title": "Mooring licence"}, 2: {"id": 2, "title": "Mooring licence (copy)"},
            3: {"id": 3, "title": "Harbour bylaws"}}

    class TB:
        def visible_docs(self):
            return docs

    class Store:
        def dec(self, raw, shelf):
            return raw

    def cand(pid, doc, text, score):
        return {"id": pid, "doc_id": doc, "raw": text, "shelf": "open", "block_ids": "[]",
                "section_id": None, "score": score, "route": score}
    ctx = SimpleNamespace(
        tb=TB(), store=Store(), question="mooring fee", cfg={"p_strong": 0.8, "p_weak": 0.4},
        cands={11: cand(11, 1, "The annual mooring fee is 400 pounds, payable in April.", 0.95),
               21: cand(21, 2, "The annual mooring fee is 400 pounds, payable in April.", 0.94),
               31: cand(31, 3, "Moorings must display the licence number on the bow.", 0.6)})
    ev = ls.build_evidence(ctx, 2, 12000)
    assert [e["passage_id"] for e in ev] == [11, 31], [e["passage_id"] for e in ev]
    assert [e["label"] for e in ev] == ["1.1", "2.1"]


def test_library_keeps_passages_that_differ_by_a_value(monkeypatch):
    from agent_friday.services.library import search as ls
    docs = {1: {"id": 1, "title": "Licence 2025"}, 2: {"id": 2, "title": "Licence 2026"}}

    class TB:
        def visible_docs(self):
            return docs

    class Store:
        def dec(self, raw, shelf):
            return raw
    ctx = SimpleNamespace(
        tb=TB(), store=Store(), question="mooring fee", cfg={"p_strong": 0.8, "p_weak": 0.4},
        cands={1: {"id": 1, "doc_id": 1, "raw": "The annual mooring fee is 400 pounds.", "shelf": "open",
                   "block_ids": "[]", "section_id": None, "score": 0.9, "route": 0.9},
               2: {"id": 2, "doc_id": 2, "raw": "The annual mooring fee is 520 pounds.", "shelf": "open",
                   "block_ids": "[]", "section_id": None, "score": 0.89, "route": 0.89}})
    assert [e["passage_id"] for e in ls.build_evidence(ctx, 2, 12000)] == [1, 2]


def _wiki_dirs(tmp_path, monkeypatch):
    from agent_friday.services import model_router
    home = tmp_path / "home"
    (home / "wiki").mkdir(parents=True)
    friday_wiki = tmp_path / "friday_wiki"
    friday_wiki.mkdir()
    monkeypatch.setattr(model_router, "HOME", home)
    monkeypatch.setattr(model_router, "WIKI_DIR_FRIDAY", friday_wiki)
    return model_router, home / "wiki", friday_wiki


def test_wiki_context_does_not_spend_its_slots_on_copies_of_one_page(tmp_path, monkeypatch):
    model_router, home_wiki, friday_wiki = _wiki_dirs(tmp_path, monkeypatch)
    page = "# Mooring\nThe mooring at Harwich costs 400 a year and renews in April.\n"
    # The same note in both wiki folders; only the frontmatter differs (and its
    # dates would make the raw texts look different if it were compared).
    (home_wiki / "mooring.md").write_text("---\nupdated: 2026-01-04\n---\n" + page, encoding="utf-8")
    (friday_wiki / "mooring.md").write_text("---\nupdated: 2026-09-21\ntags: [boat, 7]\n---\n" + page,
                                            encoding="utf-8")
    (friday_wiki / "harbour.md").write_text(
        "# Harbour\nThe Harwich harbour office shuts at 5pm on Sundays; mooring queries go there.\n",
        encoding="utf-8")
    out = model_router._get_wiki_context("mooring")
    files = [r["file"] for r in out]
    assert "harbour.md" in files, "a distinct page was crowded out by copies: %s" % files
    assert files.count("mooring.md") == 1, files
    # Windows writes text files with CRLF; the excerpt is the file as read.
    assert out[0]["excerpt"].replace("\r\n", "\n").startswith("---\nupdated: 2026-01-04"), \
        "the excerpt shown changed"
    assert set(out[0]) == {"file", "excerpt"}


def test_wiki_pages_with_another_title_or_file_name_are_never_copies(tmp_path, monkeypatch):
    model_router, home_wiki, friday_wiki = _wiki_dirs(tmp_path, monkeypatch)
    body = "The mooring at Harwich costs 400 a year and renews in April.\n"
    (home_wiki / "mooring.md").write_text("# Mooring\n" + body, encoding="utf-8")
    (home_wiki / "mooring-2025.md").write_text("# Mooring\n" + body, encoding="utf-8")   # other stem
    (friday_wiki / "mooring.md").write_text("# Mooring (Wren)\n" + body, encoding="utf-8")  # other H1
    files = sorted(r["file"] for r in model_router._get_wiki_context("mooring"))
    assert files == ["mooring-2025.md", "mooring.md", "mooring.md"], files


# ── adversarial: texts that share words but say different things ─────────────

_NOT_DUPLICATES = [
    ("Alice pays Bob 50 pounds every month.", "Bob pays Alice 50 pounds every month."),
    ("Alice pays Bob", "Bob pays Alice"),
    ("I do not like cats but I like dogs", "I like cats but I do not like dogs"),
    ("The tenant owes the landlord 900 for March.", "The landlord owes the tenant 900 for March."),
    ("My wife has a dentist appointment on Friday.", "My son has a dentist appointment on Friday."),
    ("The bin collection is on Tuesday mornings.", "The bin collection is on Wednesday mornings."),
    ("We renew the boat insurance in May every year.", "We renew the boat insurance in June every year."),
    ("We decided to use Postgres for the ledger service.", "We decided against Postgres for the ledger service."),
    ("We pinned the client to v1.20 of the library.", "We pinned the client to v1.2 of the library."),
    ("We pinned the client to 1.2.3 of the library.", "We pinned the client to 1.2.30 of the library."),
    ("The standup is at 9am every weekday.", "The standup is at 9pm every weekday."),
    ("The standup is at 9 am every weekday.", "The standup is at 9 pm every weekday."),
    ("Take 5 mg of the medicine with food.", "Take 5 g of the medicine with food."),
    ("Take 5mg of the medicine with food.", "Take 5g of the medicine with food."),
    ("I paid her the deposit yesterday.", "She paid me the deposit yesterday."),
    ("The fee is £400 a year.", "The fee is $400 a year."),
    # long sentences that differ by one lower-case word (or a preposition pair)
    ("The biopsy results from the clinic last week showed that the lump in my left breast is benign "
     "and needs no further treatment.",
     "The biopsy results from the clinic last week showed that the lump in my left breast is malignant "
     "and needs no further treatment."),
    ("The physio said the pain in my left knee comes from the old running injury and needs daily stretching.",
     "The physio said the pain in my right knee comes from the old running injury and needs daily stretching."),
    ("The doctor told me to increase the dose of my blood pressure tablets gradually over the next few weeks.",
     "The doctor told me to decrease the dose of my blood pressure tablets gradually over the next few weeks."),
    ("I take the thyroid tablet every morning on an empty stomach, at least half an hour before breakfast.",
     "I take the thyroid tablet every evening on an empty stomach, at least half an hour before breakfast."),
    ("For the long flight home next month please book me a window seat near the front of the plane.",
     "For the long flight home next month please book me an aisle seat near the front of the plane."),
    ("The spare key for the garden shed is in the blue box on the top shelf of the garage.",
     "The spare key for the garden shed is in the red box on the top shelf of the garage."),
    ("I lent tom the big ladder and the pressure washer last weekend and he has not given them back.",
     "I lent jim the big ladder and the pressure washer last weekend and he has not given them back."),
    ("Last spring we sold the old sailing dinghy to the couple who live next door to my sister.",
     "Last spring we bought the old sailing dinghy from the couple who live next door to my sister."),
    ("My parents moved to the small cottage near the coast after my father retired from the railway.",
     "My parents moved from the small cottage near the coast after my father retired from the railway."),
    ("The main water stopcock for the whole house is under the kitchen sink behind the cleaning bottles.",
     "The main water stopcock for the whole house was under the kitchen sink behind the cleaning bottles."),
    # short pairs: direction, tense, accents, other scripts, lower-case names, possessives
    ("Transfer 50 pounds to savings.", "Transfer 50 pounds from savings."),
    ("The boiler is broken.", "The boiler was broken."),
    ("The parcel goes to Paris on Monday.", "The parcel goes from Paris on Monday."),
    ("Meeting moved to Thursday.", "Meeting moved from Thursday."),
    ("My fiancé is a nurse.", "My fiancée is a nurse."),
    ("我下个月去北京出差", "我下个月去上海出差"),
    ("Flight to 北京 tomorrow", "Flight to 上海 tomorrow"),
    ("tom owes me 20 pounds", "jim owes me 20 pounds"),
    ("Our mooring fee is 400 pounds a year.", "The mooring fee is 400 pounds a year."),
]


@pytest.mark.parametrize("a,b", _NOT_DUPLICATES)
def test_swaps_names_people_days_months_versions_units_and_negations_are_distinct(rd, a, b):
    assert not rd.is_near_duplicate(a, b)
    assert not rd.is_near_duplicate(b, a)
    out = rd.select([{"id": "a", "text": a, "role": "user"}, {"id": "b", "text": b, "role": "user"}], 5)
    assert [x["id"] for x in out] == ["a", "b"]


def test_true_rewordings_and_repeats_still_collapse(rd):
    same = ["The mooring fee is 400 pounds a year.", "the mooring fee is 400 pounds a year",
            "Mooring fee is 400 pounds per year!", "Actually, the mooring fee is really 400 pounds a year."]
    for a in same:
        for b in same:
            assert rd.is_near_duplicate(a, b), (a, b)
    out = rd.select([{"id": str(i), "text": t, "role": "user"} for i, t in enumerate(same)], 5)
    assert len(out) == 1
    # case alone never separates a name, a day or a month
    assert rd.is_near_duplicate("Alice pays Bob 50 pounds every month.", "alice pays bob 50 pounds every month")
    assert rd.is_near_duplicate("The bin goes out on Tuesday.", "the bin goes out on tuesday")
    assert rd.is_near_duplicate("I don't like cats.", "I do not like cats.")


# ── grouping: anchors only, never across speakers, provider preference ──────

def test_near_duplicates_do_not_chain_through_one_word_changes(rd):
    a = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    b = "alpha bravo charlie delta echo foxtrot golf hotel india kilo"       # one word from a
    c = "alpha bravo charlie delta echo foxtrot golf hotel lima kilo"        # one word from b
    d = "alpha bravo charlie delta echo foxtrot golf mike lima kilo"         # one word from c
    for x, y in ((a, b), (b, c), (c, d), (a, c), (a, d)):
        assert not rd.is_near_duplicate(x, y), (x, y)
    out = rd.select([{"text": t} for t in (a, b, c, d)], 5, keep="first", ident=lambda r: None)
    assert [x["text"] for x in out] == [a, b, c, d], "a chain of one-word changes collapsed"
    # filler-only variants of one statement are one group, whatever the order
    v = ["alpha bravo charlie", "really alpha bravo charlie", "alpha just bravo charlie also"]
    out = rd.select([{"text": t} for t in v], 5, keep="first", ident=lambda r: None)
    assert [x["text"] for x in out] == [v[0]]


def test_the_user_and_friday_saying_the_same_words_are_never_one_item(rd):
    t = "The mooring fee at Harwich is 400 a year."
    items = [{"id": "u", "text": t, "role": "user", "timestamp": "2026-01-01"},
             {"id": "f", "text": t, "role": "assistant", "timestamp": "2026-09-01"},
             {"id": "f2", "text": t, "role": "friday", "timestamp": "2026-09-02"}]
    out = rd.select(items, 5)
    assert sorted(x["id"] for x in out) == ["f2", "u"], [x["id"] for x in out]


def test_a_user_turn_is_not_hidden_behind_friday_repeating_it_in_recall(monkeypatch):
    class Mem:
        def available(self):
            return True

        def search(self, message, n=5, **kw):
            t = "The mooring fee at Harwich is 400 a year."
            return [{"id": "m-friday", "text": t, "timestamp": "2026-09-01T09:00:00",
                     "role": "friday", "relevance": 0.9},
                    {"id": "m-user", "text": t, "timestamp": "2026-03-01T09:00:00",
                     "role": "user", "relevance": 0.8}][:n]
    text = "\n".join(_lines(_block(monkeypatch, Mem(), "mooring fee")))
    assert "ref m-user] User:" in text and "ref m-friday] You:" in text, text


def test_prefer_wins_within_a_group_before_the_newest(rd):
    t = "The mooring fee at Harwich is 400 a year."
    items = [_it("new", t, "2026-09-01", 0.9), _it("sent", t, "2026-01-01", 0.5)]
    assert [x["id"] for x in rd.select(items, 3)] == ["new"]
    out = rd.select(items, 3, prefer=lambda x: x["id"] == "sent")
    assert [x["id"] for x in out] == ["sent"]
    # a superseded item still loses, preferred or not
    items[1]["superseded"] = True
    assert [x["id"] for x in rd.select(items, 3, prefer=lambda x: x["id"] == "sent")] == ["new"]


def _fts_rows(sent_to_old, sent_to_new):
    t = "The mooring fee at Harwich is 400 a year."
    return [
        {"message_id": "m-new", "conversation_id": "c2", "title": "", "ts": 2000.0, "date": "2026-09-01",
         "role": "user", "via": "chat", "text": t, "sent_to": sent_to_new, "score": 9.0},
        {"message_id": "m-old", "conversation_id": "c1", "title": "", "ts": 1000.0, "date": "2026-05-01",
         "role": "user", "via": "chat", "text": t, "sent_to": sent_to_old, "score": 8.0},
        {"message_id": "m-gate", "conversation_id": "c3", "title": "", "ts": 1500.0, "date": "2026-07-01",
         "role": "user", "via": "chat", "text": "The Harwich marina gate code changed in June.",
         "sent_to": [], "score": 5.0},
    ]


def test_cloud_typed_search_keeps_the_twin_this_provider_already_saw(monkeypatch):
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversation_search_tool as cst
    rows = _fts_rows(["anthropic"], [])
    monkeypatch.setattr(fts, "search", lambda q, since=None, until=None, limit=8: rows[:limit])
    monkeypatch.setattr(cst, "_loop_is_local", lambda: False)
    monkeypatch.setattr(cst, "_provider", lambda: "anthropic")
    out = cst._tool_search_conversations_typed({"query": "mooring fee harwich"})
    assert "2026-05-01" in out, "the turn this model already saw was dropped for an unsent twin:\n" + out
    assert "2026-09-01" not in out
    # the suppressed twin is neither shown nor counted among the withheld matches
    assert "1 more match(es)" in out, out
    # a local model just gets the newest of the group
    monkeypatch.setattr(cst, "_loop_is_local", lambda: True)
    out = cst._tool_search_conversations_typed({"query": "mooring fee harwich"})
    assert "2026-09-01" in out and "2026-05-01" not in out, out


def test_voice_search_keeps_the_twin_gemini_already_heard(monkeypatch):
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversation_recall as rc
    from agent_friday.services import voice_engine as ve
    rows = _fts_rows([rc.prov.GEMINI], [])
    monkeypatch.setattr(fts, "search", lambda q, since=None, until=None, limit=8: rows[:limit])
    monkeypatch.setattr(ve, "_voice_local_only", lambda: True)
    shared = []
    monkeypatch.setattr(ve, "_start_local_share", lambda *a, **k: shared.append(a) or None)
    out = ve._tool_search_past_conversations({"query": "mooring fee harwich"}, session=None)
    assert "From earlier calls with you" in out and "2026-05-01" in out, out
    assert "2026-09-01" not in out
    assert "1 more match(es)" in out, out
    assert shared == []


# ── suppression counts: only what would have had a slot ────────────────────

def test_only_suppressions_that_would_have_taken_a_slot_are_counted(rd, monkeypatch):
    monkeypatch.setattr(rd, "SUPPRESSED", {})
    t, u = "The mooring fee at Harwich is 400 a year.", "The marina gate code changed in June."
    items = [{"text": t}, {"text": t}, {"text": u}, {"text": u}, {"text": u}]
    out = rd.select(items, 2, ident=lambda r: None, keep="first", path="probe")
    assert [x["text"] for x in out] == [t, u]
    assert rd.suppressed_counts() == {"probe": 1}, "over-fetched extras were counted as suppressed"


def test_library_counts_only_suppressions_within_its_slots(monkeypatch):
    from agent_friday.services import recall_dedupe as rd
    from agent_friday.services.library import search as ls
    monkeypatch.setattr(rd, "SUPPRESSED", {})
    docs = {d: {"id": d, "title": f"Doc {d}"} for d in (1, 2, 3, 4)}

    class TB:
        def visible_docs(self):
            return docs

    class Store:
        def dec(self, raw, shelf):
            return raw

    def cand(pid, doc, text, score):
        return {"id": pid, "doc_id": doc, "raw": text, "shelf": "open", "block_ids": "[]",
                "section_id": None, "score": score, "route": score}
    fee = "The annual mooring fee is 400 pounds, payable in April."
    ctx = SimpleNamespace(
        tb=TB(), store=Store(), question="mooring fee", cfg={"p_strong": 0.8, "p_weak": 0.4},
        cands={1: cand(1, 1, fee, 0.95), 2: cand(2, 2, fee, 0.94), 3: cand(3, 3, fee, 0.93),
               4: cand(4, 4, "Moorings must display the licence number on the bow.", 0.6)})
    assert [e["passage_id"] for e in ls.build_evidence(ctx, 2, 12000)] == [1, 4]
    assert rd.suppressed_counts() == {"library": 1}


# ── robustness: odd ids, limits, timestamps, scores and long texts ──────────

def test_words_in_any_script_are_words(rd):
    assert rd.signature("Zoë paid the fiancée").words == ("zoë", "paid", "fiancée")
    assert rd.signature("Flight to 北京").words == ("flight", "to", "北京")
    # composed and decomposed accents are the same word
    assert rd.is_near_duplicate("My fiancé is a nurse.", "My fiancé is a nurse.")


def test_unhashable_ids_count_as_no_id(rd):
    items = [{"id": ["x"], "text": "Wren is moored at Harwich."},
             {"id": {"k": 1}, "text": "The gate code changed in June."},
             {"id": ["x"], "text": "The gate code changed in June."}]
    out = rd.select(items, 5)
    assert [x["text"] for x in out] == ["Wren is moored at Harwich.", "The gate code changed in June."]


def test_limit_none_means_no_limit(rd):
    items = [_it(f"i{k}", f"Distinct statement number {k}.") for k in range(7)]
    items.append(_it("dup", "Distinct statement number 3!"))
    out = rd.select(items, None)
    assert len(out) == 7


def test_nan_and_inf_timestamps_and_scores_never_win(rd):
    t = "The mooring fee at Harwich is 400 a year."
    for bad in (float("nan"), float("inf"), "nan"):
        out = rd.select([_it("bad", t, bad, 0.9), _it("dated", t, "2026-05-01", 0.1)], 3)
        assert [x["id"] for x in out] == ["dated"], bad
    for bad in (float("nan"), float("inf")):
        out = rd.select([_it("bad", t, "2026-05-01", bad), _it("scored", t, "2026-05-01", 0.1)], 3)
        assert [x["id"] for x in out] == ["scored"], bad
    # a custom `when` returning NaN is treated as no time
    out = rd.select([_it("a", t), _it("b", t)], 3, when=lambda it: float("nan") if it["id"] == "a" else 5.0)
    assert [x["id"] for x in out] == ["b"]


def test_long_texts_are_compared_exactly(rd):
    long = " ".join(["alpha bravo"] * 1000)                       # 2,000 content words
    assert len(rd.signature(long).words) > rd.MAX_FUZZY_WORDS
    # past the bound only an exact content-word sequence counts, fillers included
    assert rd.is_near_duplicate(long, long.upper() + ".")
    assert not rd.is_near_duplicate(long, long + " really")
    assert not rd.is_near_duplicate(long, long.replace("bravo", "charlie", 1))
    # under the bound the same filler would not separate them
    short = " ".join(["alpha bravo"] * 10)
    assert rd.is_near_duplicate(short, short + " really")
    out = rd.select([{"text": long}, {"text": long + " really"}], 5, ident=lambda r: None)
    assert len(out) == 2
