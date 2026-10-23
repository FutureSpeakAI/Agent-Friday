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


# Six ways of saying one thing, oldest first; the last is the newest.
_REPEATS = [
    ("m-dup-1", "2026-05-01T09:00:00", "The mooring fee at Harwich is 400 a year."),
    ("m-dup-2", "2026-05-08T09:00:00", "Mooring fee at Harwich is 400 a year."),
    ("m-dup-3", "2026-05-15T09:00:00", "the mooring fee at harwich is 400 per year"),
    ("m-dup-4", "2026-05-22T09:00:00", "The mooring fee at Harwich is 400 a year!"),
    ("m-dup-5", "2026-06-01T09:00:00", "Remember: the mooring fee at Harwich is 400 a year."),
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

def test_threshold_on_content_word_overlap(rd):
    a = "alpha bravo charlie delta echo foxtrot golf hotel india"
    b = "alpha bravo charlie delta echo foxtrot golf hotel juliet"          # 8 shared of 10
    assert rd.similarity(a, b) == pytest.approx(0.8)
    assert rd.is_near_duplicate(a, b)                                       # default 0.8
    assert not rd.is_near_duplicate(a, b, threshold=0.85)
    c = "alpha bravo charlie delta echo foxtrot kilo lima mike"             # 6 shared of 12
    assert not rd.is_near_duplicate(a, c)
    # stopwords, case and punctuation do not count
    assert rd.is_near_duplicate("The mooring fee at Harwich is 400.", "mooring fee, harwich: 400")
    # a restatement with one more word is a duplicate; two short words are not enough
    assert rd.is_near_duplicate("Wren moored Harwich", "Wren moored Harwich marina")
    assert not rd.is_near_duplicate("Wren Harwich", "Wren Harwich marina")
    assert not rd.is_near_duplicate("ok", "thanks")
    assert rd.is_near_duplicate("ok", "OK")


def _it(i, text, ts=None, score=None, superseded=False):
    return {"id": i, "text": text, "timestamp": ts, "relevance": score, "superseded": superseded}


def test_a_group_keeps_its_newest_member_at_the_rank_of_its_best(rd):
    items = [
        _it("old", "The mooring fee at Harwich is 400 a year.", "2026-01-01T00:00:00", 0.9),
        _it("other", "The harbour office shuts at 5pm.", "2026-02-01T00:00:00", 0.8),
        _it("new", "Mooring fee at Harwich: 400 a year.", "2026-09-01T00:00:00", 0.7),
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


def test_wiki_context_does_not_spend_its_slots_on_copies_of_one_page(tmp_path, monkeypatch):
    from agent_friday.services import model_router
    home = tmp_path / "home"
    (home / "wiki").mkdir(parents=True)
    friday_wiki = tmp_path / "friday_wiki"
    friday_wiki.mkdir()
    page = "# Mooring\nThe mooring at Harwich costs 400 a year and renews in April.\n"
    for name in ("mooring.md", "mooring-copy.md", "mooring-old.md"):
        (home / "wiki" / name).write_text(page, encoding="utf-8")
    (friday_wiki / "harbour.md").write_text(
        "# Harbour\nThe Harwich harbour office shuts at 5pm on Sundays; mooring queries go there.\n",
        encoding="utf-8")
    monkeypatch.setattr(model_router, "HOME", home)
    monkeypatch.setattr(model_router, "WIKI_DIR_FRIDAY", friday_wiki)
    out = model_router._get_wiki_context("mooring")
    files = [r["file"] for r in out]
    assert "harbour.md" in files, "a distinct page was crowded out by copies: %s" % files
    assert sum(f.startswith("mooring") for f in files) == 1, files
