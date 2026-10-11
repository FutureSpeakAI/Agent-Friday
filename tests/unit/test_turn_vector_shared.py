"""The user's message is embedded once per turn and shared (spec 9.4, U-3).

The non-privacy side of a turn used to run the same MiniLM over the same
message four times: the turn-shape head (laya2), the tool selector's query,
the context pruner's query and the memory recall query. services/turn_vector
computes the vector once and the other three take it, when they use the same
model; a consumer with no shared vector embeds with its own model as before.

Everything here is synthetic: a counting fake stands in for each model, and
nothing loads a real one.
"""
from __future__ import annotations

import hashlib
import sys
import types

import numpy as np
import pytest

from agent_friday.services import laya2_encoder, reflex_turn, tool_selector, turn_vector

DIM = 384
MESSAGE = "please find the notes about the garden project and email them to the team"


def _vec(text: str) -> list:
    """A deterministic unit vector for a text (hash-seeded)."""
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype("float32")
    return (v / np.linalg.norm(v)).tolist()


class CountingModel:
    """Stands in for SentenceTransformer: records every text it is asked to encode."""

    def __init__(self):
        self.texts = []

    def encode(self, x, **kw):
        one = isinstance(x, str)
        items = [x] if one else list(x)
        self.texts.extend(items)
        out = np.asarray([_vec(t) for t in items], dtype="float32")
        return out[0] if one else out


@pytest.fixture
def laya(monkeypatch):
    """A laya2 encoder that is "on disk" and counts what it is asked to embed."""
    calls = []

    def fake_embed(texts, max_len=laya2_encoder.DEFAULT_MAX_LEN):
        calls.append(list(texts))
        return [_vec(t) for t in texts]

    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", fake_embed)
    turn_vector.reset()
    yield calls
    turn_vector.reset()
    laya2_encoder.reset()


@pytest.fixture
def protos(monkeypatch):
    p = {"shape": (["lookup", "chat"], [_vec("lookup"), _vec("chat")]),
         "mutation": (["read_only", "state_changing"], [_vec("ro"), _vec("sc")])}
    monkeypatch.setattr(reflex_turn, "_prototypes", lambda: p)


# ── the cache itself ─────────────────────────────────────────────────────────

def test_compute_encodes_once_per_distinct_message(laya):
    a = turn_vector.compute(MESSAGE)
    b = turn_vector.compute("  " + MESSAGE.replace(" ", "  ") + " ")   # same words, other spacing
    assert a == b and len(a) == DIM
    assert len(laya) == 1
    assert turn_vector.stats()["encodes"] == 1


def test_peek_never_encodes(laya):
    assert turn_vector.peek(MESSAGE) is None
    assert laya == []
    turn_vector.compute(MESSAGE)
    assert turn_vector.peek(MESSAGE) == _vec(MESSAGE)
    assert len(laya) == 1


def test_no_encoder_means_none_not_an_error(monkeypatch):
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    turn_vector.reset()
    assert turn_vector.compute(MESSAGE) is None
    assert turn_vector.peek(MESSAGE) is None
    assert turn_vector.compute("") is None


def test_a_failing_encoder_means_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("onnx exploded")

    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", boom)
    turn_vector.reset()
    assert turn_vector.compute(MESSAGE) is None


def test_a_long_message_is_embedded_from_its_first_and_last_tokens(laya, monkeypatch):
    seen = {}

    def edge(text, edge=128):
        seen["args"] = (len(text), edge)
        return "HEAD " + "TAIL"

    monkeypatch.setattr(laya2_encoder, "edge_text", edge)
    long_text = "word " * 500
    turn_vector.compute(long_text)
    assert seen["args"][1] == 128
    assert laya[0] == ["HEAD TAIL"]


def test_cache_is_bounded(laya):
    for i in range(turn_vector.MAX_CACHED + 10):
        turn_vector.compute("message number %d" % i)
    assert turn_vector.stats()["cached"] == turn_vector.MAX_CACHED


# ── one embed for N consumers ────────────────────────────────────────────────

class _Memory:
    """A conversation_memory.ConversationMemory over a recording fake collection."""

    def __init__(self):
        from agent_friday.conversation_memory import ConversationMemory
        self.mem = ConversationMemory.__new__(ConversationMemory)
        self.mem.model_name = "all-MiniLM-L6-v2"
        import threading
        self.mem._lock = threading.Lock()
        self.queries = []
        outer = self

        class Col:
            metadata = {"embedding_model": "all-MiniLM-L6-v2", "embedding_dim": 384}

            def count(self):
                return 3

            def query(self, **kw):
                outer.queries.append(kw)
                return {"documents": [["a past line"]], "metadatas": [[{"role": "user"}]],
                        "distances": [[0.1]]}

        self.mem._collection = Col()
        self.mem._ensure = lambda: True
        self.mem._recorded_dimension = lambda: ("all-MiniLM-L6-v2", 384)


def _run_turn(monkeypatch, protos_unused=None):
    """One turn's non-privacy consumers over MESSAGE. Returns (selector model,
    pruner model, memory)."""
    sel_model = CountingModel()
    st = types.ModuleType("sentence_transformers")
    st.SentenceTransformer = lambda *a, **k: sel_model
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    monkeypatch.setitem(tool_selector._STATE, "model", sel_model)

    verdict = reflex_turn._classify_now(MESSAGE)                       # consumer 1
    assert verdict["shape"] in ("lookup", "chat")

    blocks = ["notes on the garden project", "calendar for the week", "the news digest"]
    assert tool_selector.rank_texts(blocks, MESSAGE) is not None       # consumer 2

    from agent_friday.pipeline.context_pruner import ContextPruner
    pr_model = CountingModel()
    pruner = ContextPruner()
    pruner._model = pr_model
    pruner._embed_query(MESSAGE)                                       # consumer 3

    m = _Memory()
    from agent_friday.services import turn_vector as tv
    m.mem.search(MESSAGE, n=3, query_embedding=tv.peek(MESSAGE))       # consumer 4
    return sel_model, pr_model, m


def test_one_embed_per_turn_for_four_consumers(laya, protos, monkeypatch):
    sel, pr, m = _run_turn(monkeypatch)
    laya_texts = [t for call in laya for t in call]
    assert len(laya) == 1 and len(laya_texts) == 1, "the message was embedded more than once"
    # The selector embedded its three candidate blocks, not the query.
    assert MESSAGE not in sel.texts
    assert MESSAGE not in pr.texts
    # Chroma was handed the shared vector, not asked to embed the text.
    (q,) = m.queries
    assert "query_embeddings" in q and "query_texts" not in q
    assert q["query_embeddings"][0] == _vec(MESSAGE)


def test_encode_calls_before_and_after_on_a_fixture_turn(laya, protos, monkeypatch, capsys):
    """Before: four embeddings of the message. After: one."""
    # BEFORE: each consumer on its own (the shared vector is withheld and the
    # turn-shape head embeds the way it did, straight through laya2).
    monkeypatch.setattr(turn_vector, "peek", lambda text: None)
    monkeypatch.setattr(turn_vector, "compute",
                        lambda text: laya2_encoder.embed([" ".join(text.split())])[0])
    sel, pr, m = _run_turn(monkeypatch)
    before = (len([t for c in laya for t in c if t == MESSAGE])
              + sel.texts.count(MESSAGE) + pr.texts.count(MESSAGE)
              + sum(1 for q in m.queries if q.get("query_texts") == [MESSAGE]))
    monkeypatch.undo()

    # AFTER: the same turn with turn_vector in place.
    laya.clear()
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)

    def counting(texts, max_len=laya2_encoder.DEFAULT_MAX_LEN):
        laya.append(list(texts))
        return [_vec(t) for t in texts]

    monkeypatch.setattr(laya2_encoder, "embed", counting)
    p = {"shape": (["lookup", "chat"], [_vec("lookup"), _vec("chat")]),
         "mutation": (["read_only", "state_changing"], [_vec("ro"), _vec("sc")])}
    monkeypatch.setattr(reflex_turn, "_prototypes", lambda: p)
    turn_vector.reset()
    sel, pr, m = _run_turn(monkeypatch)
    after = (len([t for c in laya for t in c if t == MESSAGE])
             + sel.texts.count(MESSAGE) + pr.texts.count(MESSAGE)
             + sum(1 for q in m.queries if q.get("query_texts") == [MESSAGE]))
    print("ENCODE CALLS ON THE MESSAGE: before=%d after=%d" % (before, after))
    assert (before, after) == (4, 1)


def test_each_consumer_falls_back_to_its_own_model_without_a_shared_vector(monkeypatch):
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    turn_vector.reset()
    sel = CountingModel()
    st = types.ModuleType("sentence_transformers")
    st.SentenceTransformer = lambda *a, **k: sel
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    monkeypatch.setitem(tool_selector._STATE, "model", sel)
    blocks = ["notes on the garden project", "calendar for the week"]
    assert tool_selector.rank_texts(blocks, MESSAGE) is not None
    assert MESSAGE in sel.texts

    from agent_friday.pipeline.context_pruner import ContextPruner
    pr = CountingModel()
    pruner = ContextPruner()
    pruner._model = pr
    pruner._embed_query(MESSAGE)
    assert MESSAGE in pr.texts

    m = _Memory()
    m.mem.search(MESSAGE, n=3)
    assert m.queries[0].get("query_texts") == [MESSAGE]
    assert "query_embeddings" not in m.queries[0]


# ── only when the model really is the same ───────────────────────────────────

def test_pruner_with_another_model_does_not_take_the_shared_vector(laya):
    from agent_friday.pipeline.context_pruner import ContextPruner
    turn_vector.compute(MESSAGE)
    other = CountingModel()
    pruner = ContextPruner(model_name="some-other-embedder")
    pruner._model = other
    pruner._embed_query(MESSAGE)
    assert MESSAGE in other.texts


def test_memory_with_a_different_width_embeds_the_query_itself(laya):
    turn_vector.compute(MESSAGE)
    m = _Memory()
    m.mem._recorded_dimension = lambda: ("all-MiniLM-L6-v2", 768)
    m.mem.search(MESSAGE, n=3, query_embedding=turn_vector.peek(MESSAGE))
    assert m.queries[0].get("query_texts") == [MESSAGE]


def test_memory_with_an_unstamped_collection_embeds_the_query_itself(laya):
    turn_vector.compute(MESSAGE)
    m = _Memory()
    m.mem._recorded_dimension = lambda: (None, None)
    m.mem.search(MESSAGE, n=3, query_embedding=turn_vector.peek(MESSAGE))
    assert m.queries[0].get("query_texts") == [MESSAGE]


def test_memory_context_block_hands_over_the_turn_vector(laya, monkeypatch):
    from agent_friday.services import model_router
    turn_vector.compute(MESSAGE)
    got = {}

    class Mem:
        def available(self):
            return True

        def search(self, message, n=5, query_embedding=None, **kw):
            got["vec"] = query_embedding
            return []

    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: Mem())
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    model_router._build_memory_context_block(MESSAGE, "s1")
    assert got["vec"] == _vec(MESSAGE)


def test_memory_context_block_without_a_vector_calls_search_as_before(monkeypatch):
    from agent_friday.services import model_router
    turn_vector.reset()
    got = {}

    class Mem:
        def available(self):
            return True

        def search(self, message, n=5):          # the old signature: no new keyword
            got["called"] = True
            return []

    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: Mem())
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    model_router._build_memory_context_block(MESSAGE, "s1")
    assert got["called"]
