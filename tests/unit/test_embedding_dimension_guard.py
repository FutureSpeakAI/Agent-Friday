"""A5 — a write with the wrong vector width must raise, not vanish (D5).

The audit measured qwen3-embedding:0.6b at 1024 dimensions against Friday's
hardcoded all-MiniLM-L6-v2 at 384, and verified that a mismatch would be
swallowed by conversation_memory's broad `except Exception` — presenting as
permanent, invisible memory loss rather than an error.

D5's immediate piece is the write-time assertion: name the model and BOTH
dimensions, and let it escape the graceful-degradation handler.
"""
from __future__ import annotations

import pytest

from agent_friday.conversation_memory import (
    COLLECTION_NAME, ConversationMemory, EmbeddingDimensionMismatch,
    _DIM_META_KEY, _MODEL_META_KEY,
)


class _FakeCollection:
    def __init__(self, metadata=None, stored_dim=None):
        self.metadata = dict(metadata or {})
        self._stored_dim = stored_dim
        self.upserts = []
        self.modified = None

    def upsert(self, ids=None, documents=None, metadatas=None):
        self.upserts.append((ids, documents, metadatas))

    def peek(self, limit=1):
        if self._stored_dim is None:
            return {"embeddings": []}
        return {"embeddings": [[0.0] * self._stored_dim]}

    def modify(self, metadata=None):
        self.modified = dict(metadata or {})
        self.metadata = dict(metadata or {})


def _memory(collection, current_dim, model_name="all-MiniLM-L6-v2"):
    mem = ConversationMemory(persist_dir="/unused", model_name=model_name)
    mem._collection = collection
    mem._ensure = lambda: True                     # skip chromadb entirely
    mem._dim_cache = current_dim
    return mem


# ── the core property ────────────────────────────────────────────────────────
def test_mismatched_write_raises_naming_model_and_both_dimensions():
    coll = _FakeCollection({_DIM_META_KEY: 384,
                            _MODEL_META_KEY: "all-MiniLM-L6-v2"})
    mem = _memory(coll, current_dim=1024, model_name="qwen3-embedding:0.6b")

    with pytest.raises(EmbeddingDimensionMismatch) as ei:
        mem.index("hello", "user")

    err = ei.value
    assert err.recorded_dim == 384
    assert err.current_dim == 1024
    assert err.recorded_model == "all-MiniLM-L6-v2"
    assert err.current_model == "qwen3-embedding:0.6b"

    msg = str(err)
    for fragment in ("384", "1024", "all-MiniLM-L6-v2", "qwen3-embedding:0.6b",
                     COLLECTION_NAME):
        assert fragment in msg, f"error message omits {fragment!r}"

    assert coll.upserts == [], "a mismatched vector was written anyway"


def test_the_mismatch_escapes_the_graceful_degradation_handler():
    """index() swallows everything else; this one must get through.

    That handler is why the original defect was invisible — the write failed
    forever and returned None, which is indistinguishable from 'memory off'.
    """
    coll = _FakeCollection({_DIM_META_KEY: 384,
                            _MODEL_META_KEY: "all-MiniLM-L6-v2"})
    mem = _memory(coll, current_dim=1024, model_name="other-model")

    with pytest.raises(EmbeddingDimensionMismatch):
        mem.index("hello", "user")

    # Contrast: an ordinary write failure is still swallowed, as designed.
    coll_ok = _FakeCollection({_DIM_META_KEY: 384,
                               _MODEL_META_KEY: "all-MiniLM-L6-v2"})

    def _boom(**kw):
        raise RuntimeError("chroma exploded")

    coll_ok.upsert = _boom
    mem_ok = _memory(coll_ok, current_dim=384)
    assert mem_ok.index("hello", "user") is None


def test_matching_dimensions_write_normally():
    coll = _FakeCollection({_DIM_META_KEY: 384,
                            _MODEL_META_KEY: "all-MiniLM-L6-v2"})
    mem = _memory(coll, current_dim=384)

    doc_id = mem.index("hello", "user")
    assert doc_id
    assert len(coll.upserts) == 1


# ── unverifiable widths must not become false alarms ─────────────────────────
def test_unknown_current_dimension_does_not_block_writes():
    coll = _FakeCollection({_DIM_META_KEY: 384,
                            _MODEL_META_KEY: "all-MiniLM-L6-v2"})
    mem = _memory(coll, current_dim=None)
    assert mem.index("hello", "user")


def test_unstamped_empty_collection_does_not_block_writes():
    """A pre-D5 collection with nothing stored has no width to disagree with."""
    coll = _FakeCollection({}, stored_dim=None)
    mem = _memory(coll, current_dim=384)
    assert mem.index("hello", "user")


# ── pre-D5 collections ───────────────────────────────────────────────────────
def test_width_is_recovered_and_backfilled_for_unstamped_collections():
    """Collections built before the stamp existed are still protected."""
    coll = _FakeCollection({}, stored_dim=384)
    mem = _memory(coll, current_dim=1024, model_name="qwen3-embedding:0.6b")

    with pytest.raises(EmbeddingDimensionMismatch) as ei:
        mem.index("hello", "user")
    assert ei.value.recorded_dim == 384

    # And the recovered width is stamped so the next check is exact.
    assert coll.modified is not None
    assert coll.modified[_DIM_META_KEY] == 384


def test_recovered_width_matching_current_allows_the_write():
    coll = _FakeCollection({}, stored_dim=384)
    mem = _memory(coll, current_dim=384)
    assert mem.index("hello", "user")
    assert len(coll.upserts) == 1
