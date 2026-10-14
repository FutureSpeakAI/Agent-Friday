"""The knowledge graph and conversation memory never use Chroma's default
embedder, which downloads an ONNX model from Chroma's S3 bucket on first use
with no notice (local-first and transparency rules).

The knowledge-graph collection is handed vectors from Friday's own MiniLM
(services/knowledge_graph/indexer.kg_vectors); conversation memory has no
fallback to the default embedder. Chroma's default embedder is replaced here by
one that records any use and raises, so a regression fails whether or not the
model happens to be cached on the machine running the test.
"""
from __future__ import annotations

import math

import pytest


@pytest.fixture(autouse=True)
def _own_memory_dir(monkeypatch, tmp_path):
    """A memory directory of this test's own: no collection made elsewhere."""
    from agent_friday import conversation_memory
    monkeypatch.setattr(conversation_memory, "DEFAULT_PERSIST_DIR", tmp_path / "conversations")


@pytest.fixture
def default_embedder_used(monkeypatch):
    from chromadb.utils.embedding_functions import onnx_mini_lm_l6_v2 as onnx
    used = []

    def _no(*a, **k):
        used.append("default embedder")
        raise RuntimeError("Chroma's default embedder was used (it downloads from S3)")

    monkeypatch.setattr(onnx.ONNXMiniLM_L6_V2, "_download_model_if_not_exists", _no)
    monkeypatch.setattr(onnx.ONNXMiniLM_L6_V2, "__call__", _no)
    return used


class _FakeMiniLM:
    """Friday's shared MiniLM, as minilm.model() hands it out: 384-d unit rows."""
    backend = "fake"
    calls = 0

    def encode(self, sentences, normalize_embeddings=True, **_):
        _FakeMiniLM.calls += 1
        one = isinstance(sentences, str)
        rows = []
        for s in ([sentences] if one else sentences):
            v = [((hash(s) >> i) % 97 + 1) / 97.0 for i in range(384)]
            n = math.sqrt(sum(x * x for x in v))
            rows.append([x / n for x in v])
        return rows[0] if one else rows


@pytest.fixture
def friday_minilm(monkeypatch):
    from agent_friday.services import minilm
    from agent_friday.conversation_memory import ConversationMemory
    from tests.unit.test_conversation_memory import _FakeEmbedFn
    _FakeMiniLM.calls = 0
    monkeypatch.setattr(minilm, "model", lambda feature="": _FakeMiniLM())
    # Conversation memory's own collection keeps its usual (fake) embedder.
    monkeypatch.setattr(ConversationMemory, "_build_embedding_function",
                        lambda self: _FakeEmbedFn())
    return _FakeMiniLM


ENTITY = {"id": "ent_probe0000001", "title": "PROBE ENTITY", "type": "concept",
          "description": "A harmless entity used to check the embedder."}


def test_indexing_entities_uses_friday_minilm_not_the_default(default_embedder_used, friday_minilm):
    from agent_friday.services.knowledge_graph import indexer
    said = []
    assert indexer._embed_entities([ENTITY], said.append) == 1, said
    assert default_embedder_used == []
    assert friday_minilm.calls >= 1


def test_a_graph_question_uses_friday_minilm_not_the_default(default_embedder_used, friday_minilm,
                                                            monkeypatch):
    from agent_friday.services.knowledge_graph import indexer, retrieval
    assert indexer._embed_entities([ENTITY], lambda s: None) == 1
    monkeypatch.setattr(retrieval, "_b_entities", lambda store: [dict(ENTITY)])
    hits = retrieval._match_entities("which probe entity?", store=None)
    assert [h["id"] for h in hits] == [ENTITY["id"]]
    assert default_embedder_used == []


def test_an_existing_collection_made_with_the_default_embedder_still_works(
        default_embedder_used, friday_minilm):
    """Machines that already ran the graph have a collection whose stored
    configuration names Chroma's default embedder. Handing it vectors needs
    no change to that configuration and never calls the default."""
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services.knowledge_graph import indexer
    cm = ConversationMemory()
    assert cm._ensure()
    coll = cm._client.get_or_create_collection("knowledge-graph")   # as before the fix
    assert type(coll._embedding_function).__name__ == "DefaultEmbeddingFunction"
    assert indexer._embed_entities([ENTITY], lambda s: None) == 1
    assert default_embedder_used == []


def test_conversation_memory_without_the_shared_model_stays_off(default_embedder_used, monkeypatch):
    """No silent fallback: without Friday's MiniLM, memory is unavailable and
    says why, instead of fetching Chroma's default model."""
    from agent_friday.services import minilm
    from agent_friday.conversation_memory import ConversationMemory

    def _absent(feature=""):
        raise RuntimeError("the shared MiniLM is not on this PC")

    monkeypatch.setattr(minilm, "model", _absent)
    mem = ConversationMemory()
    assert mem._ensure() is False
    assert "not on this PC" in str(mem._init_error)
    assert default_embedder_used == []
