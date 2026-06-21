"""Unit tests for conversation_memory.py — ChromaDB-backed persistent memory.

Strategy:
  * extract_keywords() is PURE — tested thoroughly with no deps.
  * ConversationMemory construction + no-op behaviour when chromadb is absent —
    guarded by pytest.importorskip so the suite SKIPS gracefully if unavailable.
  * The full store (add/search) is tested against a real in-process PersistentClient
    pointed at a tmp dir, but the embedding function is monkeypatched to a tiny
    deterministic fake — no model download, no network calls.
  * get_conversation_memory() singleton — verified via a fresh reset.

DO NOT import server.py — this module is intentionally standalone.
"""
from __future__ import annotations

import sys
import threading
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

# ── extract_keywords is pure Python — no heavy deps needed ───────────────────
from conversation_memory import extract_keywords, _STOPWORDS  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  PART 1 — extract_keywords (pure, always runs)
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractKeywords:
    def test_empty_string_returns_empty(self):
        assert extract_keywords("") == []

    def test_none_returns_empty(self):
        assert extract_keywords(None) == []

    def test_returns_list(self):
        assert isinstance(extract_keywords("hello world"), list)

    def test_lowercased(self):
        result = extract_keywords("Python JavaScript RUST")
        assert all(k == k.lower() for k in result)

    def test_stopwords_excluded(self):
        # Build text that is pure stopwords.
        text = " ".join(list(_STOPWORDS)[:20])
        assert extract_keywords(text) == []

    def test_known_stopwords_not_in_output(self):
        result = extract_keywords("what is the best programming language")
        assert "the" not in result
        assert "what" not in result
        assert "is" not in result

    def test_meaningful_words_kept(self):
        result = extract_keywords("machine learning algorithms neural network")
        assert "machine" in result
        assert "learning" in result
        assert "algorithms" in result

    def test_deduplication(self):
        # Repeated word should appear only once.
        result = extract_keywords("apple apple apple banana banana orange")
        assert result.count("apple") == 1
        assert result.count("banana") == 1

    def test_frequency_ranking(self):
        # "apple" appears 4x, "banana" 2x, "orange" 1x → apple should rank first.
        result = extract_keywords("apple apple apple apple banana banana orange")
        assert result[0] == "apple"
        assert result[1] == "banana"

    def test_limit_respected(self):
        text = " ".join(f"word{i}" for i in range(20))
        result = extract_keywords(text, limit=5)
        assert len(result) <= 5

    def test_default_limit_is_8(self):
        text = " ".join(f"unique{i}" for i in range(20))
        result = extract_keywords(text)
        assert len(result) <= 8

    def test_short_words_excluded(self):
        # Words shorter than 3 chars are excluded by the regex pattern.
        result = extract_keywords("to do be it so no")
        assert result == []

    def test_numbers_in_words_allowed(self):
        # "python3" matches [A-Za-z][A-Za-z0-9'\-]{2,}
        result = extract_keywords("python3 is great programming language")
        assert "python3" in result

    def test_hyphenated_words_allowed(self):
        result = extract_keywords("long-term planning is important")
        # "long-term" should be captured as one token by the regex
        assert any("long" in k for k in result)

    def test_apostrophe_words_allowed(self):
        # "don't" filtered as a stopword, but "can't" is too.
        # Let's use a non-stopword with apostrophe like "it's" → actually stopword.
        # "friday's" should survive:
        result = extract_keywords("friday's assistant helps every day schedule")
        assert any("friday" in k for k in result)

    def test_all_stopwords_text_returns_empty(self):
        result = extract_keywords("a an the is are was were be been")
        assert result == []

    def test_mixed_case_deduplication(self):
        # "Python" and "python" should collapse to one lowercased entry.
        result = extract_keywords("Python python PYTHON")
        assert result.count("python") == 1

    def test_single_word_non_stopword(self):
        result = extract_keywords("friday")
        assert result == ["friday"]

    def test_single_stopword_returns_empty(self):
        result = extract_keywords("the")
        assert result == []

    def test_alphabetic_tiebreak(self):
        # Two words with the same frequency should be sorted alphabetically.
        result = extract_keywords("zebra apple zebra apple")
        # Both appear 2x; 'apple' < 'zebra' alphabetically → apple first.
        idx_apple = result.index("apple") if "apple" in result else 999
        idx_zebra = result.index("zebra") if "zebra" in result else 999
        assert idx_apple < idx_zebra


# ─────────────────────────────────────────────────────────────────────────────
#  PART 2 — ConversationMemory (requires chromadb; skips cleanly if absent)
# ─────────────────────────────────────────────────────────────────────────────

chromadb = pytest.importorskip("chromadb", reason="chromadb not installed")

from conversation_memory import ConversationMemory, get_conversation_memory  # noqa: E402


# ── Fake embedding function ────────────────────────────────────────────────

from chromadb import EmbeddingFunction, Documents, Embeddings  # noqa: E402


class _FakeEmbedFn(EmbeddingFunction[Documents]):
    """Deterministic dim-8 embedder — no model download, no network.

    Properly subclasses chromadb.EmbeddingFunction (required in chromadb 1.5+):
      - __call__          ChromaDB protocol
      - name()            @staticmethod → unique name string
      - get_config()      returns {} (no config needed for a fake)
      - build_from_config @staticmethod → reconstructs the object
    """

    def __init__(self):
        pass  # suppress base-class DeprecationWarning about missing __init__

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002
        import hashlib
        import random
        out = []
        for text in input:
            seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
            rng = random.Random(seed)
            v = [rng.random() for _ in range(8)]
            norm = sum(x ** 2 for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out  # chromadb base __init_subclass__ wraps this and validates/normalises

    @staticmethod
    def name() -> str:
        return "fake-embed-fn"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return _FakeEmbedFn()


@pytest.fixture
def mem_dir(tmp_path):
    """A fresh tmp directory for each test — no shared state."""
    return tmp_path / "conv_mem"


@pytest.fixture
def mem(mem_dir, monkeypatch):
    """A ConversationMemory whose embedding function is the cheap fake."""
    m = ConversationMemory(persist_dir=mem_dir)
    # Patch _build_embedding_function before _ensure() runs.
    monkeypatch.setattr(m, "_build_embedding_function", lambda: _FakeEmbedFn())
    return m


# ── Construction / availability ───────────────────────────────────────────────

class TestConversationMemoryAvailability:
    def test_available_returns_bool(self, mem):
        result = mem.available()
        assert isinstance(result, bool)

    def test_available_true_with_real_client(self, mem):
        assert mem.available() is True

    def test_persist_dir_created(self, mem, mem_dir):
        mem.available()
        assert mem_dir.exists()

    def test_unavailable_when_import_fails(self, mem_dir, monkeypatch):
        """Simulate chromadb import failure → available() returns False."""
        m = ConversationMemory(persist_dir=mem_dir / "broken")

        def bad_ensure():
            m._init_attempted = True
            m._init_error = ImportError("chromadb not installed")
            return False

        monkeypatch.setattr(m, "_ensure", bad_ensure)
        assert m.available() is False

    def test_stats_when_unavailable(self, mem_dir, monkeypatch):
        """stats() returns a well-formed dict even when backend is unavailable."""
        m = ConversationMemory(persist_dir=mem_dir / "broken2")
        m._init_attempted = True
        m._init_error = ImportError("no chromadb")
        s = m.stats()
        assert isinstance(s, dict)
        assert s["available"] is False
        assert s["count"] == 0

    def test_index_noop_when_unavailable(self, mem_dir, monkeypatch):
        m = ConversationMemory(persist_dir=mem_dir / "broken3")
        m._init_attempted = True
        m._init_error = ImportError("no chromadb")
        # index() must return None without raising.
        result = m.index("hello world", "user")
        assert result is None

    def test_search_noop_when_unavailable(self, mem_dir, monkeypatch):
        m = ConversationMemory(persist_dir=mem_dir / "broken4")
        m._init_attempted = True
        m._init_error = ImportError("no chromadb")
        result = m.search("hello")
        assert result == []

    def test_get_session_noop_when_unavailable(self, mem_dir, monkeypatch):
        m = ConversationMemory(persist_dir=mem_dir / "broken5")
        m._init_attempted = True
        m._init_error = ImportError("no chromadb")
        result = m.get_session("test-session")
        assert result == []


# ── index() / upsert ─────────────────────────────────────────────────────────

class TestIndex:
    def test_index_returns_string_id(self, mem):
        doc_id = mem.index("hello world test message", "user")
        assert isinstance(doc_id, str)
        assert len(doc_id) > 0

    def test_index_empty_text_returns_none(self, mem):
        assert mem.index("", "user") is None
        assert mem.index("   ", "user") is None

    def test_index_none_text_returns_none(self, mem):
        assert mem.index(None, "user") is None

    def test_index_custom_id_is_used(self, mem):
        returned_id = mem.index("some test content here", "user", msg_id="my-custom-id")
        assert returned_id == "my-custom-id"

    def test_index_upsert_same_id(self, mem):
        # Upserting with the same msg_id must not raise.
        mem.index("first version of content", "user", msg_id="stable-id")
        mem.index("updated version of same content", "user", msg_id="stable-id")
        # No exception means upsert succeeded.

    def test_role_normalisation_friday(self, mem):
        # "assistant" role must be normalised to "friday"
        doc_id = mem.index("assistant reply content here", "assistant", msg_id="role-test-1")
        assert doc_id is not None

    def test_role_normalisation_user(self, mem):
        doc_id = mem.index("user question content here", "user", msg_id="role-test-2")
        assert doc_id is not None

    def test_index_with_explicit_keywords(self, mem):
        doc_id = mem.index(
            "test message with explicit keywords",
            "user",
            topic_keywords=["kw1", "kw2"],
        )
        assert doc_id is not None

    def test_index_with_explicit_timestamp(self, mem):
        doc_id = mem.index(
            "test with explicit timestamp content",
            "user",
            timestamp="2026-01-01T12:00:00",
        )
        assert doc_id is not None


# ── index_exchange() ──────────────────────────────────────────────────────────

class TestIndexExchange:
    def test_returns_two_ids_for_nonempty_pair(self, mem):
        ids = mem.index_exchange("user message content here", "assistant response text here")
        assert len(ids) == 2

    def test_returns_one_id_if_assistant_empty(self, mem):
        ids = mem.index_exchange("user only message content", "")
        assert len(ids) == 1

    def test_returns_one_id_if_user_empty(self, mem):
        ids = mem.index_exchange("", "assistant only response text")
        assert len(ids) == 1

    def test_returns_empty_list_if_both_empty(self, mem):
        ids = mem.index_exchange("", "")
        assert ids == []

    def test_ids_are_distinct(self, mem):
        ids = mem.index_exchange(
            "first exchange user says something",
            "first exchange friday replies with info",
            user_msg_id="ex-u-1",
            assistant_msg_id="ex-a-1",
        )
        assert ids[0] != ids[1]


# ── search() ────────────────────────────────────────────────────────────────

class TestSearch:
    def test_search_empty_collection_returns_empty(self, mem):
        results = mem.search("anything at all")
        assert results == []

    def test_search_empty_query_returns_empty(self, mem):
        mem.index("some indexable content here", "user", msg_id="sq1")
        results = mem.search("")
        assert results == []

    def test_search_returns_list(self, mem):
        mem.index("machine learning and neural networks", "user", msg_id="s1")
        results = mem.search("neural networks")
        assert isinstance(results, list)

    def test_search_result_shape(self, mem):
        mem.index("programming python tutorial basics", "user", msg_id="s2")
        results = mem.search("python programming")
        if results:
            r = results[0]
            assert "text" in r
            assert "role" in r
            assert "timestamp" in r
            assert "distance" in r
            assert "relevance" in r

    def test_search_relevance_clamped_0_1(self, mem):
        mem.index("some content to search against keywords", "user", msg_id="s3")
        results = mem.search("content keywords")
        for r in results:
            assert 0.0 <= r["relevance"] <= 1.0

    def test_search_n_limits_results(self, mem):
        for i in range(10):
            mem.index(f"message number {i} with unique words abc{i}", "user", msg_id=f"sn{i}")
        results = mem.search("message number abc", n=3)
        assert len(results) <= 3

    def test_search_finds_indexed_content(self, mem):
        mem.index("the quick brown fox jumps over", "user", msg_id="fox1")
        results = mem.search("quick brown fox")
        assert any("fox" in r["text"] for r in results)

    def test_search_session_filter(self, mem):
        mem.index("session alpha content here", "user", session_id="alpha", msg_id="sf1")
        mem.index("session beta content here", "user", session_id="beta", msg_id="sf2")
        results = mem.search("session content", session_id="alpha")
        # All results must belong to "alpha".
        for r in results:
            assert r["session_id"] == "alpha"

    def test_search_role_filter(self, mem):
        mem.index("user question about topic", "user", session_id="rf1", msg_id="rfm1")
        mem.index("friday answer about topic", "friday", session_id="rf1", msg_id="rfm2")
        results = mem.search("topic", roles=["friday"])
        for r in results:
            assert r["role"] == "friday"

    def test_search_topic_keywords_list_not_string(self, mem):
        mem.index("keywords test content here", "user", msg_id="kw1")
        results = mem.search("keywords content")
        for r in results:
            assert isinstance(r["topic_keywords"], list)


# ── get_session() ────────────────────────────────────────────────────────────

class TestGetSession:
    def test_get_session_empty_returns_empty(self, mem):
        assert mem.get_session("nonexistent-session-xyz") == []

    def test_get_session_empty_session_id_returns_empty(self, mem):
        assert mem.get_session("") == []

    def test_get_session_returns_correct_session(self, mem):
        mem.index("turn one content text here", "user", session_id="gs1", msg_id="gs1m1")
        mem.index("turn two content text here", "friday", session_id="gs1", msg_id="gs1m2")
        mem.index("other session text here", "user", session_id="gs2", msg_id="gs2m1")
        rows = mem.get_session("gs1")
        texts = [r["text"] for r in rows]
        assert any("turn one" in t for t in texts)
        assert any("turn two" in t for t in texts)
        assert not any("other session" in t for t in texts)

    def test_get_session_sorted_by_timestamp(self, mem):
        mem.index("first message here", "user", session_id="gst",
                  timestamp="2026-01-01T10:00:00", msg_id="gstm1")
        mem.index("second message here", "friday", session_id="gst",
                  timestamp="2026-01-01T11:00:00", msg_id="gstm2")
        rows = mem.get_session("gst")
        ts = [r["timestamp"] for r in rows]
        assert ts == sorted(ts)

    def test_get_session_result_shape(self, mem):
        mem.index("session shape test content here", "user", session_id="shape", msg_id="shm1")
        rows = mem.get_session("shape")
        if rows:
            r = rows[0]
            assert "text" in r
            assert "role" in r
            assert "timestamp" in r
            assert "topic_keywords" in r
            assert isinstance(r["topic_keywords"], list)


# ── stats() ──────────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_shape_when_available(self, mem):
        s = mem.stats()
        assert isinstance(s, dict)
        assert "available" in s
        assert "count" in s
        assert "sessions" in s
        assert "persist_dir" in s
        assert "model" in s

    def test_stats_count_increases_after_index(self, mem):
        before = mem.stats().get("count", 0)
        mem.index("new entry to count in stats", "user", msg_id="cnt1")
        after = mem.stats().get("count", 0)
        assert after == before + 1

    def test_stats_available_true(self, mem):
        assert mem.stats()["available"] is True

    def test_stats_session_count(self, mem):
        mem.index("session counting test content", "user", session_id="sc1", msg_id="scc1")
        mem.index("another session test content", "user", session_id="sc2", msg_id="scc2")
        s = mem.stats()
        assert s["sessions"] >= 2

    def test_stats_unavailable_shape(self):
        m = ConversationMemory(persist_dir=Path(tempfile.mkdtemp()) / "never")
        m._init_attempted = True
        m._init_error = RuntimeError("simulated failure")
        s = m.stats()
        assert s["available"] is False
        assert "error" in s


# ── recent() ──────────────────────────────────────────────────────────────────

class TestRecent:
    def test_recent_empty_returns_empty(self, mem):
        assert mem.recent() == []

    def test_recent_newest_first(self, mem):
        mem.index("older friday reply here", "friday", session_id="r1",
                  timestamp="2026-01-01T10:00:00", msg_id="rc1")
        mem.index("newer friday reply here", "friday", session_id="r1",
                  timestamp="2026-01-02T10:00:00", msg_id="rc2")
        rows = mem.recent()
        ts = [r["timestamp"] for r in rows]
        assert ts == sorted(ts, reverse=True)
        assert "newer" in rows[0]["text"]

    def test_recent_role_filter(self, mem):
        mem.index("user turn content here", "user", session_id="r2", msg_id="rc3")
        mem.index("friday turn content here", "friday", session_id="r2", msg_id="rc4")
        rows = mem.recent(roles=["friday"])
        assert rows and all(r["role"] == "friday" for r in rows)

    def test_recent_respects_n(self, mem):
        for i in range(5):
            mem.index(f"reply number {i} content", "friday", session_id="r3",
                      timestamp=f"2026-03-0{i+1}T10:00:00", msg_id=f"rc5{i}")
        assert len(mem.recent(n=3, roles=["friday"])) == 3


# ── thread-safety smoke test ──────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_index_calls_dont_raise(self, mem):
        errors = []

        def worker(i):
            try:
                mem.index(
                    f"concurrent write {i} unique content words here",
                    "user",
                    msg_id=f"thread-{i}",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


# ── get_conversation_memory singleton ────────────────────────────────────────

class TestSingleton:
    def test_same_object_returned_twice(self):
        import conversation_memory as cm
        # Reset singleton to test fresh construction.
        orig = cm._instance
        cm._instance = None
        try:
            a = cm.get_conversation_memory(persist_dir=tempfile.mkdtemp())
            b = cm.get_conversation_memory()
            assert a is b
        finally:
            cm._instance = orig  # restore so other tests are unaffected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
