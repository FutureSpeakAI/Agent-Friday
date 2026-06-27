"""Unit tests for context_compressor.py — Headroom-powered context compression.

Covers:
  - _estimate_tokens: char-based token estimate for message lists.
  - _coerce_int: type coercion with fallback for bad/bool/negative values.
  - _extract_messages: pulls list from .messages attribute or bare list.
  - should_compress: gating on enabled flag and token threshold.
  - compress: graceful-passthrough when headroom is absent (the common case
    in this test environment — headroom-ai has no Windows wheel, so
    _import_failed is set and the original messages are returned unchanged).
  - get_stats: dict shape and 'available' flag.
  - from_settings / configure: construction from config dict.

All tests are pure-logic; none import server or require headroom to be installed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.pipeline.context_compressor import ContextCompressor, _CHARS_PER_TOKEN


# ── helpers ───────────────────────────────────────────────────────────────────

def _msg(content: str) -> dict:
    return {"role": "user", "content": content}


def _block_msg(*texts: str) -> dict:
    """Build a message whose content is a list of Anthropic-style text blocks."""
    return {"role": "assistant", "content": [{"type": "text", "text": t} for t in texts]}


def _large_messages(n_chars: int = 5000):
    """Return a message list whose total char count exceeds n_chars."""
    text = "x" * n_chars
    return [_msg(text)]


# ════════════════════════════════════════════════════════════════════════
#  _estimate_tokens  (static method — call via instance or class)
# ════════════════════════════════════════════════════════════════════════

class TestEstimateTokens:
    def test_empty_list_returns_zero(self):
        assert ContextCompressor._estimate_tokens([]) == 0

    def test_none_list_returns_zero(self):
        assert ContextCompressor._estimate_tokens(None) == 0  # type: ignore[arg-type]

    def test_single_message_char_count(self):
        msgs = [_msg("a" * 400)]
        expected = 400 // _CHARS_PER_TOKEN
        assert ContextCompressor._estimate_tokens(msgs) == expected

    def test_multiple_messages_summed(self):
        msgs = [_msg("a" * 200), _msg("b" * 200)]
        expected = 400 // _CHARS_PER_TOKEN
        assert ContextCompressor._estimate_tokens(msgs) == expected

    def test_anthropic_content_blocks_counted(self):
        msgs = [_block_msg("hello " * 50, "world " * 50)]
        # 6 * 50 + 6 * 50 = 600 chars
        result = ContextCompressor._estimate_tokens(msgs)
        assert result == 600 // _CHARS_PER_TOKEN

    def test_non_dict_messages_ignored_gracefully(self):
        # A bare string in the list is not a dict — must not raise.
        result = ContextCompressor._estimate_tokens(["not a dict"])  # type: ignore[list-item]
        assert result == 0

    def test_message_without_content_key_counts_zero(self):
        msgs = [{"role": "user"}]
        assert ContextCompressor._estimate_tokens(msgs) == 0

    def test_integer_content_not_counted(self):
        # content value is not str or list — must not raise.
        msgs = [{"role": "user", "content": 12345}]
        result = ContextCompressor._estimate_tokens(msgs)
        assert result == 0


# ════════════════════════════════════════════════════════════════════════
#  _coerce_int  (static method)
# ════════════════════════════════════════════════════════════════════════

class TestCoerceInt:
    def test_valid_int_returned(self):
        assert ContextCompressor._coerce_int(42, 0) == 42

    def test_valid_float_truncated_to_int(self):
        assert ContextCompressor._coerce_int(3.9, 0) == 3

    def test_zero_is_valid(self):
        assert ContextCompressor._coerce_int(0, 99) == 0

    def test_negative_uses_fallback(self):
        assert ContextCompressor._coerce_int(-1, 77) == 77

    def test_none_uses_fallback(self):
        assert ContextCompressor._coerce_int(None, 55) == 55  # type: ignore[arg-type]

    def test_string_uses_fallback(self):
        assert ContextCompressor._coerce_int("100", 33) == 33  # type: ignore[arg-type]

    def test_bool_true_uses_fallback(self):
        # booleans are technically ints in Python; the implementation explicitly rejects them
        assert ContextCompressor._coerce_int(True, 10) == 10

    def test_bool_false_uses_fallback(self):
        assert ContextCompressor._coerce_int(False, 10) == 10


# ════════════════════════════════════════════════════════════════════════
#  _extract_messages  (static method)
# ════════════════════════════════════════════════════════════════════════

class TestExtractMessages:
    def _fallback(self):
        return [_msg("fallback")]

    def test_extracts_from_messages_attribute(self):
        msgs = [_msg("compressed")]
        result_obj = SimpleNamespace(messages=msgs)
        assert ContextCompressor._extract_messages(result_obj, self._fallback()) == msgs

    def test_bare_list_accepted(self):
        msgs = [_msg("bare")]
        assert ContextCompressor._extract_messages(msgs, self._fallback()) == msgs

    def test_none_messages_attribute_uses_fallback(self):
        result_obj = SimpleNamespace(messages=None)
        assert ContextCompressor._extract_messages(result_obj, self._fallback()) == self._fallback()

    def test_empty_list_uses_fallback(self):
        result_obj = SimpleNamespace(messages=[])
        assert ContextCompressor._extract_messages(result_obj, self._fallback()) == self._fallback()

    def test_non_list_messages_uses_fallback(self):
        result_obj = SimpleNamespace(messages="oops")
        assert ContextCompressor._extract_messages(result_obj, self._fallback()) == self._fallback()

    def test_object_without_messages_and_not_list_uses_fallback(self):
        result_obj = SimpleNamespace(other="stuff")
        assert ContextCompressor._extract_messages(result_obj, self._fallback()) == self._fallback()

    def test_bare_empty_list_uses_fallback(self):
        assert ContextCompressor._extract_messages([], self._fallback()) == self._fallback()


# ════════════════════════════════════════════════════════════════════════
#  should_compress
# ════════════════════════════════════════════════════════════════════════

class TestShouldCompress:
    def test_disabled_never_compresses(self):
        cc = ContextCompressor(enabled=False, min_tokens_to_compress=0)
        assert cc.should_compress(_large_messages()) is False

    def test_empty_messages_returns_false(self):
        cc = ContextCompressor(enabled=True, min_tokens_to_compress=0)
        assert cc.should_compress([]) is False

    def test_none_messages_returns_false(self):
        cc = ContextCompressor(enabled=True, min_tokens_to_compress=0)
        assert cc.should_compress(None) is False  # type: ignore[arg-type]

    def test_below_threshold_returns_false(self):
        cc = ContextCompressor(enabled=True, min_tokens_to_compress=10000)
        # 100 chars / 4 = 25 tokens — well below 10000
        msgs = [_msg("x" * 100)]
        assert cc.should_compress(msgs) is False

    def test_above_threshold_returns_true(self):
        cc = ContextCompressor(enabled=True, min_tokens_to_compress=10)
        # 400 chars / 4 = 100 tokens — above 10
        msgs = [_msg("x" * 400)]
        assert cc.should_compress(msgs) is True

    def test_exactly_at_threshold_returns_true(self):
        # threshold = 25 tokens → need exactly 100 chars
        cc = ContextCompressor(enabled=True, min_tokens_to_compress=25)
        msgs = [_msg("x" * 100)]
        assert cc.should_compress(msgs) is True

    def test_one_below_threshold_returns_false(self):
        cc = ContextCompressor(enabled=True, min_tokens_to_compress=26)
        msgs = [_msg("x" * 100)]  # 100 chars → 25 tokens
        assert cc.should_compress(msgs) is False


# ════════════════════════════════════════════════════════════════════════
#  compress — graceful passthrough when headroom is absent
# ════════════════════════════════════════════════════════════════════════

class TestCompressGracefulPassthrough:
    """Headroom is not installed in this test environment (no Windows wheel).
    The compress() method must return the original messages unchanged.
    """

    def test_disabled_returns_input_unchanged(self):
        cc = ContextCompressor(enabled=False)
        msgs = [_msg("hello")]
        assert cc.compress(msgs) is msgs

    def test_empty_returns_input_unchanged(self):
        cc = ContextCompressor(enabled=True)
        assert cc.compress([]) == []

    def test_none_returns_none(self):
        cc = ContextCompressor(enabled=True)
        assert cc.compress(None) is None  # type: ignore[arg-type]

    def test_headroom_missing_returns_original(self):
        cc = ContextCompressor(enabled=True)
        # Force import_failed so _load_headroom returns None immediately.
        cc._import_failed = True
        msgs = [_msg("some message content")]
        result = cc.compress(msgs)
        assert result is msgs

    def test_headroom_missing_no_stats_incremented(self):
        cc = ContextCompressor(enabled=True)
        cc._import_failed = True
        msgs = [_msg("content")]
        cc.compress(msgs)
        assert cc._stats["calls"] == 0
        assert cc._stats["tokens_saved"] == 0

    def test_compression_error_returns_original_and_increments_errors(self):
        """Simulate a headroom callable that raises — errors counter goes up,
        original messages returned."""
        cc = ContextCompressor(enabled=True)

        def _bad_compress(msgs, model=None):
            raise RuntimeError("simulated headroom crash")

        cc._headroom = _bad_compress  # bypass lazy import
        msgs = [_msg("large " * 300)]
        result = cc.compress(msgs)
        assert result is msgs
        assert cc._stats["errors"] == 1

    def test_compression_with_stub_headroom_updates_stats(self):
        """If a stub headroom is provided that returns a valid result object,
        compress() should update stats and return the compressed list."""
        cc = ContextCompressor(enabled=True)
        compressed_msgs = [_msg("short")]

        class FakeResult:
            messages = compressed_msgs
            tokens_before = 100
            tokens_after = 20
            tokens_saved = 80

        def _fake_compress(msgs, model=None):
            return FakeResult()

        cc._headroom = _fake_compress
        original = [_msg("original " * 100)]
        result = cc.compress(original)
        assert result == compressed_msgs
        assert cc._stats["calls"] == 1
        assert cc._stats["tokens_saved"] == 80
        assert cc._stats["tokens_before"] == 100
        assert cc._stats["tokens_after"] == 20


# ════════════════════════════════════════════════════════════════════════
#  get_stats
# ════════════════════════════════════════════════════════════════════════

class TestGetStats:
    def test_stats_is_dict(self):
        cc = ContextCompressor()
        assert isinstance(cc.get_stats(), dict)

    def test_stats_has_expected_keys(self):
        cc = ContextCompressor()
        s = cc.get_stats()
        for key in ("calls", "tokens_saved", "tokens_before", "tokens_after",
                    "compression_ratio", "last_ratio", "errors",
                    "enabled", "min_tokens_to_compress", "available"):
            assert key in s, f"missing key: {key}"

    def test_available_false_when_headroom_absent(self):
        cc = ContextCompressor()
        cc._import_failed = True
        assert cc.get_stats()["available"] is False

    def test_available_true_when_headroom_loaded(self):
        cc = ContextCompressor()
        cc._headroom = lambda msgs, model=None: msgs  # stub
        assert cc.get_stats()["available"] is True

    def test_enabled_reflected_in_stats(self):
        cc = ContextCompressor(enabled=False)
        assert cc.get_stats()["enabled"] is False

    def test_min_tokens_reflected_in_stats(self):
        cc = ContextCompressor(min_tokens_to_compress=2500)
        assert cc.get_stats()["min_tokens_to_compress"] == 2500


# ════════════════════════════════════════════════════════════════════════
#  from_settings / configure
# ════════════════════════════════════════════════════════════════════════

class TestFromSettingsAndConfigure:
    def test_from_settings_none_uses_defaults(self):
        cc = ContextCompressor.from_settings(None)
        assert cc._enabled is True
        assert cc._min_tokens == 1000

    def test_from_settings_respects_enabled_false(self):
        cc = ContextCompressor.from_settings({"enabled": False})
        assert cc._enabled is False

    def test_from_settings_respects_min_tokens(self):
        cc = ContextCompressor.from_settings({"min_tokens_to_compress": 500})
        assert cc._min_tokens == 500

    def test_configure_updates_enabled(self):
        cc = ContextCompressor(enabled=True)
        cc.configure({"enabled": False})
        assert cc._enabled is False

    def test_configure_updates_min_tokens(self):
        cc = ContextCompressor(min_tokens_to_compress=1000)
        cc.configure({"min_tokens_to_compress": 250})
        assert cc._min_tokens == 250

    def test_configure_returns_self(self):
        cc = ContextCompressor()
        result = cc.configure({})
        assert result is cc

    def test_configure_none_is_safe(self):
        cc = ContextCompressor(enabled=True, min_tokens_to_compress=1000)
        cc.configure(None)  # must not raise
        assert cc._enabled is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
