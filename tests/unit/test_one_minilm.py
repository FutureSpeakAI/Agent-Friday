"""One MiniLM in the process, and one embedding of the user's turn (W7, spec c.3).

Before: the same all-MiniLM-L6-v2 was loaded as two ONNX sessions (Laya 2 and the
Library) and as four sentence-transformers copies (the privacy classifier, the
tool selector, the context pruner, conversation memory's Chroma embedder), and a
message could be embedded six times in a turn. Now services/laya2_encoder holds
the one pinned ONNX session, services/minilm hands it to every sentence-
transformers-style consumer, and services/turn_vector holds the turn's one
vector.

Everything here is synthetic: a counting fake stands in for the encoder, and no
model, file of the owner's or network is touched.
"""
from __future__ import annotations

import hashlib
import sys
import threading
import time
import types

import numpy as np
import pytest

from agent_friday.services import laya2_encoder, minilm, turn_vector

DIM = 384
MESSAGE = "please find the notes about the garden project and email them to the team"


def _vec(text: str) -> list:
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).standard_normal(DIM).astype("float32")
    return (v / np.linalg.norm(v)).tolist()


class CountingModel:
    """Stands in for a SentenceTransformer: records every text it is asked to encode."""

    def __init__(self):
        self.texts = []

    def encode(self, x, **kw):
        one = isinstance(x, str)
        items = [x] if one else list(x)
        self.texts.extend(items)
        out = np.asarray([_vec(t) for t in items], dtype="float32")
        return out[0] if one else out


@pytest.fixture
def enc(monkeypatch):
    """A Laya 2 encoder that is "on disk" and records every embed call as
    (texts, max_len)."""
    calls = []

    def fake_embed(texts, max_len=laya2_encoder.DEFAULT_MAX_LEN):
        calls.append((list(texts), max_len))
        return [_vec(t) for t in texts]

    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "load", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", fake_embed)
    turn_vector.reset()
    minilm.reset()
    yield calls
    turn_vector.reset()
    minilm.reset()
    laya2_encoder.reset()


def _encodes_of(calls, text):
    return sum(1 for texts, _ in calls for t in texts if t == text)


# ── a turn through every consumer embeds its text once ───────────────────────

def test_a_turn_through_the_router_and_the_rest_embeds_the_text_once(enc, monkeypatch):
    from agent_friday.pipeline.context_pruner import ContextPruner
    from agent_friday.services import laya_router, reflex_turn, tool_selector

    monkeypatch.setattr(laya_router, "_prototypes",
                        lambda: (["conversation", "act"], [_vec("c"), _vec("a")]))
    monkeypatch.setattr(reflex_turn, "_prototypes", lambda: {
        "shape": (["lookup", "chat"], [_vec("lookup"), _vec("chat")]),
        "mutation": (["read_only", "state_changing"], [_vec("ro"), _vec("sc")])})
    sel = CountingModel()
    monkeypatch.setitem(tool_selector._STATE, "model", sel)
    pr_model = CountingModel()
    pruner = ContextPruner()
    pruner._model = pr_model

    laya_router._score(MESSAGE)                                       # router tier 1
    reflex_turn._classify_now(MESSAGE)                                # turn-shape head
    assert tool_selector.rank_texts(["garden notes", "calendar"], MESSAGE) is not None
    pruner._embed_query(MESSAGE)                                      # context pruner

    assert _encodes_of(enc, MESSAGE) == 1, "the message was embedded more than once"
    assert MESSAGE not in sel.texts and MESSAGE not in pr_model.texts
    assert turn_vector.stats()["encodes"] == 1


def test_the_same_turn_without_a_shared_vector_embeds_it_several_times(enc, monkeypatch):
    """The 'before' half of the count: each consumer on its own encodes the text."""
    from agent_friday.services import laya_router, reflex_turn
    monkeypatch.setattr(laya_router, "_prototypes",
                        lambda: (["conversation", "act"], [_vec("c"), _vec("a")]))
    monkeypatch.setattr(reflex_turn, "_prototypes", lambda: {
        "shape": (["lookup", "chat"], [_vec("lookup"), _vec("chat")]),
        "mutation": (["read_only", "state_changing"], [_vec("ro"), _vec("sc")])})
    monkeypatch.setattr(turn_vector, "compute", lambda text, turn_id=None: laya2_encoder.embed([text])[0])
    monkeypatch.setattr(turn_vector, "compute_head", lambda text, turn_id=None: laya2_encoder.embed([text])[0])
    laya_router._score(MESSAGE)
    reflex_turn._classify_now(MESSAGE)
    assert _encodes_of(enc, MESSAGE) == 2


def test_the_router_score_is_unchanged_by_sharing(enc, monkeypatch):
    from agent_friday.services import laya_router
    labels, protos = ["conversation", "act", "search_web"], [_vec("c"), _vec("a"), _vec("w")]
    monkeypatch.setattr(laya_router, "_prototypes", lambda: (labels, protos))
    shared = laya_router._score(MESSAGE)
    turn_vector.reset()
    monkeypatch.setattr(turn_vector, "compute_head", lambda text, turn_id=None: None)
    direct = laya_router._score(MESSAGE)                              # the old path
    assert shared == direct


def test_recall_and_the_memory_write_reuse_the_turn_vector(enc, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import model_router
    got = {}

    class Mem:
        def available(self):
            return True

        def search(self, message, n=5, query_embedding=None, **kw):
            got["vec"] = query_embedding
            return []

    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: Mem())
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    model_router._build_memory_context_block(MESSAGE, "s1")           # computes it, once
    assert got["vec"] == _vec(MESSAGE)

    writes = []

    class Col:
        def upsert(self, **kw):
            writes.append(kw)

    mem = ConversationMemory.__new__(ConversationMemory)
    mem.model_name = "all-MiniLM-L6-v2"
    mem._lock = threading.Lock()
    mem._collection = Col()
    mem._ensure = lambda: True
    mem._dim_cache = DIM
    mem._recorded_dimension = lambda: ("all-MiniLM-L6-v2", DIM)
    mem.index(MESSAGE, "user")
    mem.index("a reply Friday wrote", "friday")
    assert writes[0]["embeddings"] == [_vec(MESSAGE)]                 # the user's turn: stored as is
    assert "embeddings" not in writes[1]                              # Friday's reply: Chroma embeds it
    assert _encodes_of(enc, MESSAGE) == 1


def test_a_long_user_message_is_stored_with_chromas_own_embedding(enc, monkeypatch):
    """Where the edge reading and the plain reading differ, the stored vector is not the shared one."""
    from agent_friday.conversation_memory import ConversationMemory
    monkeypatch.setattr(laya2_encoder, "edge_text",
                        lambda text, edge=128: text if len(text) <= 300 else text[:50] + " " + text[-50:])
    long_msg = "word " * 200
    turn_vector.compute(long_msg)
    writes = []

    class Col:
        def upsert(self, **kw):
            writes.append(kw)

    mem = ConversationMemory.__new__(ConversationMemory)
    mem.model_name = "all-MiniLM-L6-v2"
    mem._lock = threading.Lock()
    mem._collection = Col()
    mem._ensure = lambda: True
    mem._dim_cache = DIM
    mem._recorded_dimension = lambda: ("all-MiniLM-L6-v2", DIM)
    mem.index(long_msg, "user")
    assert "embeddings" not in writes[0]


# ── the turn vector: head reading, single flight, bounds ─────────────────────

def test_the_head_reading_is_the_shared_vector_for_a_message_that_fits(enc):
    a = turn_vector.compute(MESSAGE)
    b = turn_vector.compute_head(MESSAGE)
    assert a == b == _vec(MESSAGE)
    assert len(enc) == 1
    assert turn_vector.peek_full(MESSAGE) == a


def test_a_message_over_256_tokens_keeps_its_two_readings_apart(enc, monkeypatch):
    monkeypatch.setattr(laya2_encoder, "edge_text",
                        lambda text, edge=128: text if len(text) <= 300 else "EDGE " + text[-20:])
    long_msg = "word " * 200
    norm = " ".join(long_msg.split())
    edge = turn_vector.compute(long_msg)
    head = turn_vector.compute_head(long_msg)
    assert edge == _vec("EDGE " + norm[-20:])
    assert head == _vec(norm)                                         # first 256 tokens, as the router always did
    assert enc[-1][1] == laya2_encoder.DEFAULT_MAX_LEN
    assert turn_vector.peek_full(long_msg) is None
    n = len(enc)
    turn_vector.compute_head(long_msg)
    turn_vector.compute(long_msg)
    assert len(enc) == n                                              # both are cached now


def test_concurrent_consumers_of_one_text_share_one_encode(monkeypatch):
    calls = []

    def slow_embed(texts, max_len=laya2_encoder.DEFAULT_MAX_LEN):
        calls.append(list(texts))
        time.sleep(0.15)
        return [_vec(t) for t in texts]

    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", slow_embed)
    turn_vector.reset()
    out, errors = [], []

    def work():
        try:
            out.append(turn_vector.compute(MESSAGE))
        except Exception as e:                                        # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    turn_vector.reset()
    assert not errors and len(out) == 8 and all(v == _vec(MESSAGE) for v in out)
    assert len(calls) == 1


def test_a_vector_expires_and_a_turn_can_end_its_own(enc, monkeypatch):
    turn_vector.compute(MESSAGE, turn_id="t1")
    turn_vector.compute("another message", turn_id="t2")
    assert turn_vector.end_turn("t1") == 1
    assert turn_vector.peek(MESSAGE) is None
    assert turn_vector.peek("another message") is not None
    monkeypatch.setattr(turn_vector, "TTL_S", -1.0)
    turn_vector.compute("a third message")
    assert turn_vector.peek("a third message") is None               # gone as soon as it is stale


def test_the_cache_keeps_no_text(enc):
    turn_vector.compute(MESSAGE)
    keys = list(turn_vector._cache)
    assert all(MESSAGE not in k and len(k) == 64 for k in keys)


# ── degraded when absent (invariant 3) ───────────────────────────────────────

def test_no_encoder_means_degraded_everywhere(monkeypatch, tmp_path):
    from agent_friday.services import laya_router, tool_selector
    monkeypatch.setattr(laya2_encoder, "artifacts_dir", lambda: tmp_path)
    laya2_encoder.reset()
    turn_vector.reset()
    minilm.reset()
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # and no torch copy either
    monkeypatch.setitem(tool_selector._STATE, "model", None)
    monkeypatch.setitem(tool_selector._STATE, "sig", None)
    t0 = time.perf_counter()
    assert turn_vector.compute(MESSAGE) is None
    assert turn_vector.compute_head(MESSAGE) is None
    assert turn_vector.peek_full(MESSAGE) is None
    assert time.perf_counter() - t0 < 1.0
    with pytest.raises(Exception):
        minilm.model("a test")
    assert tool_selector.rank_texts(["a", "b"], MESSAGE) is None
    monkeypatch.setattr(laya_router, "_prototypes", lambda: (["conversation"], [_vec("c")]))
    with pytest.raises(RuntimeError):
        laya_router._score(MESSAGE)                                   # route() turns this into a degraded route
    laya2_encoder.reset()


def test_a_failing_encoder_is_degraded_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("onnx exploded")

    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", boom)
    turn_vector.reset()
    assert turn_vector.compute(MESSAGE) is None
    assert turn_vector.compute_head(MESSAGE) is None
    assert turn_vector.compute(MESSAGE) is None                       # and the failed text is not stuck "in flight"


# ── one encoder session ──────────────────────────────────────────────────────

def test_only_one_encoder_session_exists_after_warm_up(monkeypatch, tmp_path):
    (tmp_path / laya2_encoder.MODEL_NAME).write_bytes(b"not a model")
    (tmp_path / laya2_encoder.TOKENIZER_NAME).write_bytes(b"{}")
    made = []

    class FakeSession:
        def __init__(self, directory, threads):
            made.append(directory)
            self.threads = threads

        def embed(self, texts, max_len):
            return [_vec(t) for t in texts]

        def edge_text(self, text, edge):
            return text

    monkeypatch.setattr(laya2_encoder, "artifacts_dir", lambda: tmp_path)
    monkeypatch.setattr(laya2_encoder, "_sha256_of", lambda p: laya2_encoder.MODEL_SHA256)
    monkeypatch.setattr(laya2_encoder, "_Encoder", FakeSession)
    laya2_encoder.reset()
    turn_vector.reset()
    minilm.reset()
    from agent_friday.services.library import embed as lib

    assert lib.available() and lib.embed(["a library line"]).shape == (1, DIM)
    assert laya2_encoder.embed(["a router line"])
    assert minilm.model("test").encode(["a tool line"]).shape == (1, DIM)
    assert turn_vector.compute(MESSAGE) is not None
    pytest.importorskip("chromadb")
    fn = minilm.chroma_embedding_function_class()(minilm.model("memory"))
    assert len(fn(["a memory line"])) == 1
    assert len(made) == 1, "more than one encoder session was created"
    laya2_encoder.reset()
    turn_vector.reset()


def test_the_library_keeps_its_api_over_the_shared_session(enc):
    from agent_friday.services.library import embed as lib
    v = lib.embed(["one", "two", "three"], batch=2)
    assert v.shape == (3, DIM) and v.dtype == np.float32
    assert [c[0] for c in enc] == [["one", "two"], ["three"]]
    assert lib.embed([]).shape == (0, DIM)
    assert lib.stamp_id() == "%s@%s" % (laya2_encoder.MODEL_ID, laya2_encoder.MODEL_SHA256[:12])
    assert lib.from_blobs([lib.to_blob(v[0])]).shape == (1, DIM)


def test_the_library_is_unavailable_without_the_files(monkeypatch, tmp_path):
    from agent_friday.services.library import embed as lib
    monkeypatch.setattr(laya2_encoder, "artifacts_dir", lambda: tmp_path)
    laya2_encoder.reset()
    assert lib.available() is False
    assert lib.embed(["x"]) is None
    st = lib.status()
    assert st["state"] == "missing" and str(tmp_path) not in st["detail"]
    laya2_encoder.reset()


def test_a_rig_can_point_both_at_an_existing_copy(monkeypatch, tmp_path):
    monkeypatch.delenv("FRIDAY_ENCODER_DIR", raising=False)
    monkeypatch.setenv("FRIDAY_LIBRARY_ENCODER_DIR", str(tmp_path))
    assert laya2_encoder.artifacts_dir() == tmp_path
    monkeypatch.setenv("FRIDAY_ENCODER_DIR", str(tmp_path / "x"))
    assert laya2_encoder.artifacts_dir() == tmp_path / "x"


# ── the sentence-transformers-style adapter ──────────────────────────────────

def test_the_adapter_shapes_and_batching(enc):
    m = minilm.OnnxSentenceModel()
    one = m.encode("a single line")
    assert one.shape == (DIM,) and one.dtype == np.float32
    many = m.encode(["a", "b", "c"], batch_size=2, normalize_embeddings=True,
                    convert_to_numpy=True, show_progress_bar=False)
    assert many.shape == (3, DIM)
    assert [c[0] for c in enc[1:]] == [["a", "b"], ["c"]]
    assert np.allclose(many[2], _vec("c"))                            # order kept across batches
    assert m.encode([]).shape == (0, DIM)
    assert m.backend == "onnx" and m.get_sentence_embedding_dimension() == DIM


def test_the_consumers_take_the_shared_model(enc, monkeypatch):
    from agent_friday.pipeline.context_pruner import ContextPruner
    from agent_friday.services import tool_selector
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # no torch to fall back to
    p = ContextPruner()
    assert p._get_model().backend == "onnx"
    monkeypatch.setitem(tool_selector._STATE, "model", None)
    monkeypatch.setitem(tool_selector._STATE, "sig", None)
    tools = [{"name": "tool_%d" % i, "description": "does thing %d" % i} for i in range(40)]
    assert tool_selector.select(tools, "do thing 3 please") is not None
    assert tool_selector._STATE["model"].backend == "onnx"
    monkeypatch.setitem(tool_selector._STATE, "model", None)


def test_without_the_encoder_files_there_is_one_shared_torch_model(monkeypatch):
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    monkeypatch.setattr(laya2_encoder, "load", lambda: False)
    minilm.reset()
    built = []

    class FakeST(CountingModel):
        def __init__(self, name, device=None):
            super().__init__()
            built.append(name)
            assert device == "cpu"

    st = types.ModuleType("sentence_transformers")
    st.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    from agent_friday.services import embedder_cache
    monkeypatch.setattr(embedder_cache, "ensure_available", lambda *a, **k: True)
    a, b, c = minilm.model("privacy"), minilm.model("tools"), minilm.model("pruning")
    assert a is b is c and built == ["all-MiniLM-L6-v2"]
    minilm.reset()


def test_the_chroma_embedder_keeps_the_name_and_config_of_the_one_it_replaces(enc):
    pytest.importorskip("chromadb")
    from chromadb.utils import embedding_functions as efs
    base = efs.SentenceTransformerEmbeddingFunction
    fn = minilm.chroma_embedding_function_class()(minilm.OnnxSentenceModel())
    assert isinstance(fn, base) and fn.name() == base.name()
    assert fn.get_config() == {"model_name": "all-MiniLM-L6-v2", "device": "cpu",
                               "normalize_embeddings": False, "kwargs": {}}
    out = fn(["one line", "another"])
    assert len(out) == 2 and np.allclose(out[0], _vec("one line"))
    assert fn.default_space() == "cosine"


def test_memory_builds_its_chroma_embedder_on_the_shared_model(enc):
    pytest.importorskip("chromadb")
    from agent_friday import conversation_memory as cm
    mem = cm.ConversationMemory.__new__(cm.ConversationMemory)
    mem.model_name = "all-MiniLM-L6-v2"
    fn = mem._build_embedding_function()
    assert fn._model.backend == "onnx"
    assert fn.name() == "sentence_transformer"


# ── the privacy classifier takes the shared model, and stays fail-closed ─────

def test_the_privacy_layer_stays_on_its_own_torch_path(enc, monkeypatch):
    """The egress classifier's embedder path is its own: its sentence-transformers
    model, on the processor, encoded with normalize_embeddings=True. It never
    touches the ONNX session or the shared MiniLM; moving it waits for its
    sensitive review."""
    from agent_friday.services import embedder_cache
    from agent_friday.services import sensitivity_classifier as sc
    built, encoded = [], []

    class FakeST:
        def __init__(self, name, device=None):
            built.append((name, device))

        def encode(self, texts, **kw):
            encoded.append((list(texts), kw))
            return np.asarray([_vec(t) for t in texts], dtype="float32")

    st = types.ModuleType("sentence_transformers")
    st.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    monkeypatch.setattr(embedder_cache, "ensure_available", lambda *a, **k: True)
    monkeypatch.setattr(sc, "_EMBEDDER", sc._UNTRIED)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True)
    model = sc._load_embedder()
    assert isinstance(model, FakeST) and built == [("all-MiniLM-L6-v2", "cpu")]
    assert encoded[0][1].get("normalize_embeddings") is True
    assert not enc, "the classifier touched the ONNX encoder"
    assert minilm._torch_model is None, "the classifier took the shared MiniLM"


def test_a_chat_turn_releases_its_vector_when_it_is_indexed(enc, monkeypatch):
    from agent_friday.services import model_router
    turn_vector.compute(MESSAGE)
    assert turn_vector.peek(MESSAGE) is not None

    class Mem:
        def index_exchange(self, *a, **k):
            pass

    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: Mem())
    model_router._index_chat_turn(MESSAGE, "a reply", "s1")
    assert turn_vector.peek(MESSAGE) is None
    assert turn_vector.end_turn(text="nothing cached") == 0


def test_the_privacy_layer_stays_fail_closed_when_no_model_can_load(monkeypatch, tmp_path):
    from agent_friday.services import sensitivity_classifier as sc
    monkeypatch.setattr(laya2_encoder, "artifacts_dir", lambda: tmp_path)
    laya2_encoder.reset()
    minilm.reset()
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setattr(sc, "_EMBEDDER", sc._UNTRIED)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True)
    monkeypatch.setattr(sc, "_EMBEDDER_FAILED_AT", 0.0)
    monkeypatch.setattr(sc, "_EMBEDDER_ERROR", "")
    assert sc._load_embedder() is None
    assert sc._embedding_tier("anything at all") == (-1, 0.0)        # unavailable, never "nothing found"
    laya2_encoder.reset()


def test_nothing_on_the_privacy_path_shares_the_turn_vector():
    import inspect
    from agent_friday.services import egress_gate, sensitivity_classifier
    for mod in (egress_gate, sensitivity_classifier):
        assert "turn_vector" not in inspect.getsource(mod)


def test_the_verifier_embeds_nothing():
    import inspect
    from agent_friday.services import call_verifier
    src = inspect.getsource(call_verifier)
    assert "laya2_encoder" not in src and "sentence_transformers" not in src and "turn_vector" not in src


# ── optional: the real models agree (needs the encoder files and torch; no CI) ──

def test_the_onnx_and_torch_copies_agree_on_synthetic_sentences():
    import os
    if not os.environ.get("FRIDAY_TEST_REAL_MINILM"):
        pytest.skip("loads the real models; set FRIDAY_TEST_REAL_MINILM=1 with a scratch home")
    st = pytest.importorskip("sentence_transformers")
    if not laya2_encoder.is_available():
        pytest.skip("the Laya 2 encoder files are not installed")
    sentences = [MESSAGE, "what is on my calendar tomorrow afternoon", "ok",
                 "Tell me a short story about a lighthouse keeper who collects clocks.",
                 "   lots   of    whitespace \n and newlines\t tabs ", "word " * 400]
    a = minilm.OnnxSentenceModel().encode(sentences)
    b = st.SentenceTransformer("all-MiniLM-L6-v2", device="cpu").encode(
        sentences, normalize_embeddings=True, convert_to_numpy=True)
    cos = (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    assert float(cos.min()) >= 0.999
