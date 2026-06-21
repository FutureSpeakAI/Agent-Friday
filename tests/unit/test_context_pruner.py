"""Unit tests for context_pruner.py — semantic context pruning.

Tests cover:
  * should_prune   — threshold logic (under/over/at boundary)
  * _pair_query_text — pure static method, user-turn extraction
  * _similarity    — cosine geometry (identical, orthogonal, zero vectors)
  * prune()        — structural reassembly with a monkeypatched embedder
  * configure()    — in-place threshold update & model-swap cache flush
  * from_settings() — factory method

Numpy is a hard import in context_pruner so we guard the whole file.
sentence-transformers is NOT used directly — _embed() is monkeypatched.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repo root on sys.path — mirrors the exemplar pattern.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

np = pytest = None  # placeholders — filled by importorskip below

import pytest  # noqa: E402  (needed before importorskip call)

np = pytest.importorskip("numpy")  # skip whole file if numpy absent

from context_pruner import ContextPruner  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_messages(*roles_contents):
    """Build a message list from (role, content) pairs."""
    return [{"role": r, "content": c} for r, c in roles_contents]


def _unit_vec(n, idx):
    """Return a float32 unit vector in dimension n with mass only on axis idx."""
    v = np.zeros(n, dtype="float32")
    v[idx] = 1.0
    return v


# ── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def pruner():
    """A ContextPruner with a low threshold so tests don't need huge fixtures."""
    return ContextPruner(max_turns=5, keep_recent=2, top_k=3)


# ── should_prune ─────────────────────────────────────────────────────────────

class TestShouldPrune:
    def test_empty_list_returns_false(self, pruner):
        assert pruner.should_prune([]) is False

    def test_only_system_messages_never_count(self, pruner):
        msgs = _make_messages(("system", "You are Friday.")) * 10
        assert pruner.should_prune(msgs) is False

    def test_under_threshold_returns_false(self, pruner):
        # 5 user+assistant pairs → turns == 5, threshold == 5 → NOT > 5
        msgs = []
        for i in range(5):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        assert pruner.should_prune(msgs) is False

    def test_exactly_at_threshold_returns_false(self, pruner):
        # turns == max_turns should NOT prune (strictly greater-than)
        msgs = []
        for i in range(pruner.max_turns):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        assert pruner.should_prune(msgs) is False

    def test_one_over_threshold_returns_true(self, pruner):
        msgs = []
        for i in range(pruner.max_turns + 1):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        assert pruner.should_prune(msgs) is True

    def test_mixed_system_and_body_counts_only_body(self, pruner):
        # System messages must not inflate the turn count.
        msgs = [{"role": "system", "content": "system context"} for _ in range(10)]
        for i in range(pruner.max_turns + 1):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        assert pruner.should_prune(msgs) is True

    def test_odd_body_length_uses_integer_division(self, pruner):
        # 11 non-system messages → turns = 11 // 2 = 5, same as max_turns → False
        msgs = []
        for i in range(11):
            msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"})
        assert pruner.should_prune(msgs) is False

    def test_custom_threshold(self):
        p = ContextPruner(max_turns=2)
        msgs = []
        for i in range(3):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        assert p.should_prune(msgs) is True


# ── _pair_query_text ──────────────────────────────────────────────────────────

class TestPairQueryText:
    def test_user_role_preferred(self):
        pair = [
            {"role": "user", "content": "user question"},
            {"role": "assistant", "content": "assistant reply"},
        ]
        assert ContextPruner._pair_query_text(pair) == "user question"

    def test_assistant_only_fallback(self):
        pair = [{"role": "assistant", "content": "assistant only"}]
        result = ContextPruner._pair_query_text(pair)
        assert "assistant only" in result

    def test_user_non_string_content_falls_back(self):
        # content is a list (multi-modal) — not a str — so the user branch is skipped.
        pair = [
            {"role": "user", "content": [{"type": "image"}]},
            {"role": "assistant", "content": "plain text fallback"},
        ]
        result = ContextPruner._pair_query_text(pair)
        # The fallback join should include the assistant's string content.
        assert "plain text fallback" in result

    def test_empty_pair_returns_empty_string(self):
        assert ContextPruner._pair_query_text([]) == ""

    def test_no_string_content_at_all(self):
        pair = [
            {"role": "user", "content": None},
            {"role": "assistant", "content": None},
        ]
        # Should return a string (possibly empty) without raising.
        result = ContextPruner._pair_query_text(pair)
        assert isinstance(result, str)

    def test_user_comes_second_still_preferred(self):
        # Even if user message is the second element, it should be picked.
        pair = [
            {"role": "assistant", "content": "reply first"},
            {"role": "user", "content": "query second"},
        ]
        assert ContextPruner._pair_query_text(pair) == "query second"

    def test_single_user_message(self):
        pair = [{"role": "user", "content": "solo user"}]
        assert ContextPruner._pair_query_text(pair) == "solo user"


# ── _similarity ───────────────────────────────────────────────────────────────

class TestSimilarity:
    def test_identical_unit_vectors_score_1(self):
        v = _unit_vec(4, 0)
        score = ContextPruner._similarity(v, v)
        assert abs(score - 1.0) < 1e-6

    def test_identical_arbitrary_vectors_score_1(self):
        v = np.array([1.0, 2.0, 3.0], dtype="float32")
        score = ContextPruner._similarity(v, v)
        assert abs(score - 1.0) < 1e-6

    def test_orthogonal_vectors_score_0(self):
        a = _unit_vec(4, 0)
        b = _unit_vec(4, 1)
        score = ContextPruner._similarity(a, b)
        assert abs(score - 0.0) < 1e-6

    def test_opposite_vectors_score_minus_1(self):
        v = _unit_vec(4, 0)
        score = ContextPruner._similarity(v, -v)
        assert abs(score - (-1.0)) < 1e-6

    def test_zero_vector_a_returns_0(self):
        a = np.zeros(4, dtype="float32")
        b = _unit_vec(4, 2)
        assert ContextPruner._similarity(a, b) == 0.0

    def test_zero_vector_b_returns_0(self):
        a = _unit_vec(4, 2)
        b = np.zeros(4, dtype="float32")
        assert ContextPruner._similarity(a, b) == 0.0

    def test_both_zero_returns_0(self):
        z = np.zeros(4, dtype="float32")
        assert ContextPruner._similarity(z, z) == 0.0

    def test_partial_overlap(self):
        # [1,1,0] vs [1,0,0] — cos = 1/sqrt(2) ≈ 0.707
        a = np.array([1.0, 1.0, 0.0], dtype="float32")
        b = np.array([1.0, 0.0, 0.0], dtype="float32")
        score = ContextPruner._similarity(a, b)
        assert abs(score - (1.0 / 2 ** 0.5)) < 1e-5

    def test_unnormalized_vectors(self):
        # Scale should not change cosine value.
        a = np.array([3.0, 0.0, 0.0], dtype="float32")
        b = np.array([7.0, 0.0, 0.0], dtype="float32")
        assert abs(ContextPruner._similarity(a, b) - 1.0) < 1e-6

    def test_list_input_accepted(self):
        # The function accepts plain lists — np.asarray should handle it.
        score = ContextPruner._similarity([1.0, 0.0], [0.0, 1.0])
        assert abs(score - 0.0) < 1e-6


# ── configure ────────────────────────────────────────────────────────────────

class TestConfigure:
    def test_updates_thresholds(self):
        p = ContextPruner(max_turns=50, keep_recent=4, top_k=10)
        p.configure({"max_turns": 20, "keep_recent": 2, "top_k": 5})
        assert p.max_turns == 20
        assert p.keep_recent == 2
        assert p.top_k == 5

    def test_partial_update_preserves_others(self):
        p = ContextPruner(max_turns=50, keep_recent=4, top_k=10)
        p.configure({"max_turns": 30})
        assert p.max_turns == 30
        assert p.keep_recent == 4  # unchanged
        assert p.top_k == 10       # unchanged

    def test_model_swap_flushes_cache(self, monkeypatch):
        p = ContextPruner()
        p._cache = {"abc": np.zeros(3)}
        p._model = object()  # pretend loaded
        p.configure({"model": "different-model"})
        assert p._cache == {}
        assert p._model is None

    def test_same_model_preserves_cache(self, monkeypatch):
        p = ContextPruner(model_name="same-model")
        sentinel = {"abc": np.zeros(3)}
        p._cache = sentinel
        p.configure({"model": "same-model"})
        assert p._cache is sentinel  # not flushed

    def test_empty_config_is_noop(self):
        p = ContextPruner(max_turns=50, keep_recent=4, top_k=10)
        p.configure({})
        assert p.max_turns == 50
        assert p.keep_recent == 4
        assert p.top_k == 10

    def test_none_config_is_noop(self):
        p = ContextPruner(max_turns=50)
        p.configure(None)
        assert p.max_turns == 50


# ── from_settings ─────────────────────────────────────────────────────────────

class TestFromSettings:
    def test_defaults_when_empty(self):
        p = ContextPruner.from_settings({})
        assert p.max_turns == 50
        assert p.keep_recent == 4
        assert p.top_k == 10

    def test_defaults_when_none(self):
        p = ContextPruner.from_settings(None)
        assert p.max_turns == 50

    def test_custom_settings(self):
        p = ContextPruner.from_settings({"max_turns": 20, "keep_recent": 3, "top_k": 7})
        assert p.max_turns == 20
        assert p.keep_recent == 3
        assert p.top_k == 7

    def test_custom_model(self):
        p = ContextPruner.from_settings({"model": "custom-model"})
        assert p.model_name == "custom-model"


# ── prune() structural tests (monkeypatched embedder) ─────────────────────────

def _make_fake_embed(dim=8):
    """Returns a fake _embed bound method that assigns deterministic vectors."""
    # Each call returns a new random unit vector seeded by the text hash so
    # prune() can compute similarities without downloading a real model.
    rng = np.random.default_rng(42)
    _memo = {}

    def _embed(self_or_text, text=None):
        # handle both static (text arg) and bound-method style calls
        key = text if text is not None else self_or_text
        if key not in _memo:
            v = rng.random(dim).astype("float32")
            v /= np.linalg.norm(v) + 1e-9
            _memo[key] = v
        return _memo[key]

    return _embed


@pytest.fixture
def patched_pruner(monkeypatch):
    """A ContextPruner whose _embed is replaced with a cheap fake."""
    p = ContextPruner(max_turns=2, keep_recent=1, top_k=2)

    _memo = {}
    rng = np.random.default_rng(99)

    def fake_embed(text):
        text = text or ""
        if text not in _memo:
            v = rng.random(8).astype("float32")
            v /= np.linalg.norm(v) + 1e-9
            _memo[text] = v
        return _memo[text]

    monkeypatch.setattr(p, "_embed", fake_embed)
    return p


class TestPrune:
    def test_empty_body_returns_system_only(self, patched_pruner):
        msgs = [{"role": "system", "content": "sys"}]
        result = patched_pruner.prune(msgs, "query")
        assert result == msgs

    def test_system_messages_always_first(self, patched_pruner):
        sys_msg = {"role": "system", "content": "sys"}
        body = [
            {"role": "user", "content": "q0"}, {"role": "assistant", "content": "a0"},
            {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"}, {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "current"},
        ]
        result = patched_pruner.prune([sys_msg] + body, "current query")
        assert result[0]["role"] == "system"

    def test_current_prompt_is_last(self, patched_pruner):
        body = [
            {"role": "user", "content": f"q{i}"} for i in range(6)
        ] + [{"role": "user", "content": "final question"}]
        msgs = body
        result = patched_pruner.prune(msgs, "final question")
        # The last message of the input becomes the last of the output.
        assert result[-1]["content"] == "final question"

    def test_no_archive_returns_recent_plus_current(self, patched_pruner):
        # keep_recent=1 → keep_n=2. 2 history messages → everything is 'recent', nothing archived.
        sys_msg = {"role": "system", "content": "sys"}
        msgs = [
            sys_msg,
            {"role": "user", "content": "r0"}, {"role": "assistant", "content": "a0"},
            {"role": "user", "content": "current"},
        ]
        result = patched_pruner.prune(msgs, "current")
        # Should have sys + r0 + a0 + current
        contents = [m["content"] for m in result]
        assert "r0" in contents
        assert "current" in contents

    def test_output_length_bounded(self, patched_pruner):
        # Build 20 turns; output should be <= sys + top_k*2 + keep_recent*2 + 1
        sys_msg = {"role": "system", "content": "sys"}
        body = []
        for i in range(20):
            body.append({"role": "user", "content": f"q{i}"})
            body.append({"role": "assistant", "content": f"a{i}"})
        body.append({"role": "user", "content": "final"})
        msgs = [sys_msg] + body
        result = patched_pruner.prune(msgs, "final")
        # 1 system + top_k*2 + keep_recent*2 + 1 current (upper bound)
        upper = 1 + patched_pruner.top_k * 2 + patched_pruner.keep_recent * 2 + 1
        assert len(result) <= upper

    def test_no_system_message_ok(self, patched_pruner):
        body = []
        for i in range(10):
            body.append({"role": "user", "content": f"q{i}"})
            body.append({"role": "assistant", "content": f"a{i}"})
        body.append({"role": "user", "content": "q_final"})
        result = patched_pruner.prune(body, "q_final")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_archived_turns_in_chronological_order(self, patched_pruner):
        """Top-k archived turns must be re-sorted chronologically."""
        sys_msg = {"role": "system", "content": "sys"}
        body = []
        for i in range(20):
            body.append({"role": "user", "content": f"q{i}"})
            body.append({"role": "assistant", "content": f"a{i}"})
        body.append({"role": "user", "content": "current"})
        msgs = [sys_msg] + body
        result = patched_pruner.prune(msgs, "current")

        # Extract numeric index from content (q<N>) for non-system non-"current" messages.
        indices = []
        for m in result:
            if m["role"] == "system":
                continue
            c = m["content"]
            if c.startswith("q") and c[1:].isdigit():
                indices.append(int(c[1:]))
        # The retrieved indices must be monotonically non-decreasing.
        assert indices == sorted(indices)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
