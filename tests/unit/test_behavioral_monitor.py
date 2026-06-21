"""Unit tests for behavioral_monitor.py — internal governance / anomaly detection.

Covers: _clamp, _risk_level, _score_scope_drift, _score_privilege_escalation,
_score_data_exfiltration, _score_repetition, summarize_args, _scrub,
extract_remit, _tokens, _classify_result, _target_key, COMPOSITE_WEIGHTS,
and the full begin_session/log_action/evaluate lifecycle.

All tests use synthetic data only; no real network calls, no server import.
Disk writes go to tmp_path-backed BehavioralMonitor instances.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import math
import pytest

from behavioral_monitor import (
    BehavioralMonitor,
    Remit,
    Action,
    summarize_args,
    _clamp,
    _risk_level,
    _classify_result,
    _target_key,
    _tokens,
    _scrub,
    COMPOSITE_WEIGHTS,
    SENSITIVE_READ_TOOLS,
    OUTBOUND_TOOLS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mon(tmp_path):
    """A fresh BehavioralMonitor writing to an isolated tmp dir."""
    return BehavioralMonitor(base_dir=tmp_path / "bmon")


def _make_action(
    tool_name: str = "some_tool",
    arguments_summary: str = "",
    ring_level: int = 0,
    result_type: str = "text",
    arg_tokens: list | None = None,
) -> Action:
    return Action(
        tool_name=tool_name,
        arguments_summary=arguments_summary,
        timestamp="2026-01-01T00:00:00Z",
        ring_level=ring_level,
        result_type=result_type,
        _arg_tokens=arg_tokens or [],
    )


def _make_remit(
    tokens: list | None = None,
    expected_max_ring: int = 0,
    referenced_paths: list | None = None,
    mentions_sensitive: bool = False,
    raw: str = "test request",
) -> Remit:
    return Remit(
        raw=raw,
        tokens=tokens or [],
        referenced_paths=referenced_paths or [],
        expected_max_ring=expected_max_ring,
        mentions_sensitive=mentions_sensitive,
    )


# ── _clamp ────────────────────────────────────────────────────────────────────

class TestClamp:

    def test_within_range_unchanged(self):
        assert _clamp(0.5) == pytest.approx(0.5)

    def test_zero_unchanged(self):
        assert _clamp(0.0) == pytest.approx(0.0)

    def test_one_unchanged(self):
        assert _clamp(1.0) == pytest.approx(1.0)

    def test_above_one_clamped(self):
        assert _clamp(1.5) == pytest.approx(1.0)

    def test_below_zero_clamped(self):
        assert _clamp(-0.5) == pytest.approx(0.0)

    def test_large_positive_clamped(self):
        assert _clamp(999.0) == pytest.approx(1.0)

    def test_large_negative_clamped(self):
        assert _clamp(-999.0) == pytest.approx(0.0)

    def test_integer_input_works(self):
        assert _clamp(2) == pytest.approx(1.0)
        assert _clamp(-1) == pytest.approx(0.0)


# ── _risk_level ───────────────────────────────────────────────────────────────

class TestRiskLevel:

    # Exact boundary thresholds from source: 0.80 / 0.60 / 0.30
    def test_critical_at_exactly_0_80(self):
        assert _risk_level(0.80) == "critical"

    def test_critical_above_0_80(self):
        assert _risk_level(0.99) == "critical"
        assert _risk_level(1.00) == "critical"

    def test_high_at_exactly_0_60(self):
        assert _risk_level(0.60) == "high"

    def test_high_below_critical_threshold(self):
        assert _risk_level(0.79) == "high"
        assert _risk_level(0.61) == "high"

    def test_elevated_at_exactly_0_30(self):
        assert _risk_level(0.30) == "elevated"

    def test_elevated_below_high_threshold(self):
        assert _risk_level(0.59) == "elevated"
        assert _risk_level(0.31) == "elevated"

    def test_normal_below_0_30(self):
        assert _risk_level(0.29) == "normal"
        assert _risk_level(0.00) == "normal"

    def test_boundary_just_below_critical(self):
        # 0.799… is still "high"
        assert _risk_level(0.799) == "high"

    def test_boundary_just_below_elevated(self):
        # 0.299… is "normal"
        assert _risk_level(0.299) == "normal"

    @pytest.mark.parametrize("score,expected", [
        (0.80, "critical"),
        (0.60, "high"),
        (0.30, "elevated"),
        (0.29, "normal"),
        (0.00, "normal"),
        (1.00, "critical"),
    ])
    def test_parametrised_thresholds(self, score, expected):
        assert _risk_level(score) == expected


# ── COMPOSITE_WEIGHTS ─────────────────────────────────────────────────────────

class TestCompositeWeights:

    def test_weights_sum_to_one(self):
        total = sum(COMPOSITE_WEIGHTS.values())
        assert math.isclose(total, 1.0), f"Weights sum to {total}, expected 1.0"

    def test_expected_keys_present(self):
        expected_keys = {"scope_drift", "privilege_escalation",
                         "data_exfiltration", "repetition_anomaly"}
        assert set(COMPOSITE_WEIGHTS.keys()) == expected_keys

    def test_all_weights_positive(self):
        for k, v in COMPOSITE_WEIGHTS.items():
            assert v > 0, f"Weight for {k} must be positive"


# ── _scrub ────────────────────────────────────────────────────────────────────

class TestScrub:

    def test_email_scrubbed(self):
        out = _scrub("contact alice@example.com about this")
        assert "alice@example.com" not in out
        assert "<email>" in out

    def test_long_digits_scrubbed(self):
        out = _scrub("account 1234567890 is flagged")
        assert "1234567890" not in out
        assert "<num>" in out

    def test_short_digits_not_scrubbed(self):
        out = _scrub("I have 42 items")
        assert "42" in out

    def test_windows_user_path_scrubbed(self):
        out = _scrub(r"C:\Users\alice\Documents\secret.txt")  # pragma: allowlist secret
        assert "alice" not in out
        assert "<user>" in out

    def test_long_token_scrubbed(self):
        # 24+ alphanum chars = token
        token = "abcdefghijklmnopqrstuvwxyz"  # 26 chars  # pragma: allowlist secret
        out = _scrub(f"auth: {token}")
        assert token not in out
        assert "<token>" in out

    def test_short_word_not_scrubbed(self):
        out = _scrub("hello world")
        assert "hello" in out
        assert "world" in out

    def test_multiple_pii_types(self):
        s = r"user C:\Users\bob\file.txt emailed bob@test.com with token abcdefghijklmnopqrstuvwxyz123"  # pragma: allowlist secret
        out = _scrub(s)
        assert "bob@test.com" not in out
        assert "bob" not in out  # path scrubbed so 'bob' removed
        assert "<email>" in out
        assert "<user>" in out


# ── summarize_args ────────────────────────────────────────────────────────────

class TestSummarizeArgs:

    def test_none_returns_empty(self):
        assert summarize_args(None) == ""

    def test_dict_formats_key_equals_value(self):
        out = summarize_args({"key": "value"})
        assert "key=value" in out

    def test_email_scrubbed_in_value(self):
        out = summarize_args({"to": "alice@example.com"})
        assert "alice@example.com" not in out
        assert "<email>" in out

    def test_path_reduced_to_basename(self):
        out = summarize_args({"path": r"C:\Users\alice\Documents\report.txt"})  # pragma: allowlist secret
        assert "report.txt" in out
        assert "Documents" not in out

    def test_non_dict_stringified(self):
        out = summarize_args(["a", "b", "c"])
        assert len(out) > 0

    def test_max_len_respected(self):
        out = summarize_args({"k": "x" * 200}, max_len=48)
        # The value portion should be truncated to max_len
        assert len(out) <= 48 + 10  # key= prefix + value + ellipsis

    def test_multiple_keys_joined(self):
        out = summarize_args({"a": "1", "b": "2"})
        assert "a=1" in out
        assert "b=2" in out
        assert ";" in out

    def test_integer_value_serialised(self):
        out = summarize_args({"count": 42})
        assert "count=42" in out


# ── _tokens ───────────────────────────────────────────────────────────────────

class TestTokens:

    def test_empty_string_returns_empty_set(self):
        assert _tokens("") == set()

    def test_none_returns_empty_set(self):
        assert _tokens(None) == set()  # type: ignore

    def test_stopwords_removed(self):
        result = _tokens("the cat and a dog")
        assert "the" not in result
        assert "and" not in result
        assert "a" not in result

    def test_short_words_excluded(self):
        # Words < 3 chars excluded by \b\w{3,}\b
        result = _tokens("go do it now")
        # "go", "do", "it" are 2 chars — excluded; "now" is 3 chars — included
        assert "go" not in result
        assert "now" in result

    def test_lowercased(self):
        result = _tokens("HELLO World")
        assert "hello" in result
        assert "world" in result

    def test_meaningful_words_kept(self):
        result = _tokens("please summarize the quarterly financial report")
        assert "quarterly" in result
        assert "financial" in result
        assert "report" in result


# ── extract_remit ─────────────────────────────────────────────────────────────

class TestExtractRemit:

    def test_returns_remit_object(self, mon):
        r = mon.extract_remit("Show me my calendar events")
        assert isinstance(r, Remit)

    def test_raw_preserved_up_to_500(self, mon):
        msg = "x" * 600
        r = mon.extract_remit(msg)
        assert len(r.raw) == 500

    def test_outbound_intent_raises_ring(self, mon):
        r = mon.extract_remit("please send an email to my colleague")
        assert r.expected_max_ring >= 2

    def test_read_only_intent_keeps_ring_low(self, mon):
        r = mon.extract_remit("what time is my next meeting?")
        assert r.expected_max_ring <= 1

    def test_computer_control_intent_ring_3(self, mon):
        r = mon.extract_remit("click the button and screenshot the result")
        assert r.expected_max_ring == 3

    def test_sensitive_path_hint_detected(self, mon):
        r = mon.extract_remit("read from my vault please")
        assert r.mentions_sensitive is True

    def test_no_sensitive_hint_false(self, mon):
        r = mon.extract_remit("what is the weather today?")
        assert r.mentions_sensitive is False

    def test_file_references_extracted(self, mon):
        r = mon.extract_remit("update report.csv with the new numbers")
        assert "report.csv" in r.referenced_paths

    def test_empty_message_safe(self, mon):
        r = mon.extract_remit("")
        assert isinstance(r, Remit)
        assert 0 <= r.expected_max_ring <= 3

    def test_tokens_are_sorted_lowercase(self, mon):
        r = mon.extract_remit("Show quarterly financial summary data")
        assert r.tokens == sorted(r.tokens)


# ── _classify_result ──────────────────────────────────────────────────────────

class TestClassifyResult:

    def test_none_returns_none(self):
        assert _classify_result(None) == "none"

    def test_error_string(self):
        assert _classify_result("Error: something went wrong") == "error"

    def test_traceback_string(self):
        assert _classify_result("Traceback (most recent call last):") == "error"

    def test_exception_in_text(self):
        assert _classify_result("raised an Exception here") == "error"

    def test_vault_denied(self):
        assert _classify_result("[vault access denied] tier mismatch") == "denied"

    def test_vault_zt_deny(self):
        assert _classify_result("vault-zt deny: insufficient clearance") == "denied"

    def test_screenshot_result(self):
        assert _classify_result("[screenshot captured at 1920x1080]") == "image"

    def test_normal_text(self):
        assert _classify_result("The task completed successfully.") == "text"

    def test_dict_stringified_as_text(self):
        assert _classify_result({"status": "ok"}) == "text"

    def test_integer_result_is_text(self):
        assert _classify_result(42) == "text"


# ── _target_key ───────────────────────────────────────────────────────────────

class TestTargetKey:

    def test_hint_in_summary_uses_tool_and_hint(self):
        a = _make_action(tool_name="vault_read", arguments_summary="path=vault/secrets")
        key = _target_key(a)
        assert key.startswith("vault_read:")
        assert "vault" in key

    def test_no_hint_falls_back_to_equals_match(self):
        a = _make_action(tool_name="read_file", arguments_summary="file=report.txt")
        key = _target_key(a)
        assert "read_file:" in key

    def test_no_match_returns_tool_name(self):
        a = _make_action(tool_name="list_events", arguments_summary="")
        assert _target_key(a) == "list_events"

    def test_sensitive_hint_takes_priority(self):
        a = _make_action(tool_name="search_email", arguments_summary="query=ssn verification")
        key = _target_key(a)
        assert "ssn" in key or "search_email" in key


# ── _score_scope_drift ────────────────────────────────────────────────────────

class TestScoreScopeDrift:

    def test_no_actions_returns_zero(self, mon):
        remit = _make_remit(tokens=["email"])
        score, findings = mon._score_scope_drift([], remit)
        assert score == 0.0
        assert findings == []

    def test_all_in_scope_returns_zero(self, mon):
        remit = _make_remit(tokens=["email"])
        actions = [
            _make_action(tool_name="draft_email", arg_tokens=["email", "draft"]),
            _make_action(tool_name="send_email", arg_tokens=["email"]),
        ]
        score, findings = mon._score_scope_drift(actions, remit)
        assert score == pytest.approx(0.0)

    def test_out_of_scope_non_sensitive_partial_score(self, mon):
        remit = _make_remit(tokens=["weather"])
        # Tool name has nothing to do with weather, not sensitive
        actions = [_make_action(tool_name="list_files", arg_tokens=["documents"])]
        score, findings = mon._score_scope_drift(actions, remit)
        # Out of scope non-sensitive → 0.4
        assert score == pytest.approx(0.4)

    def test_out_of_scope_sensitive_high_score(self, mon):
        remit = _make_remit(tokens=["weather"])
        actions = [_make_action(tool_name="vault_read", arg_tokens=["vault"])]
        score, findings = mon._score_scope_drift(actions, remit)
        # vault_read is in SENSITIVE_READ_TOOLS → 0.85
        assert score == pytest.approx(0.85)
        assert len(findings) > 0

    def test_score_bounded(self, mon):
        remit = _make_remit(tokens=["weather"])
        actions = [_make_action(tool_name="vault_read") for _ in range(20)]
        score, _ = mon._score_scope_drift(actions, remit)
        assert 0.0 <= score <= 1.0

    def test_empty_remit_tokens_uses_ring_heuristic(self, mon):
        # No tokens, no paths — low-ring action should be considered in-scope
        remit = _make_remit(tokens=[], expected_max_ring=2)
        actions = [_make_action(tool_name="read_file", ring_level=0)]
        score, _ = mon._score_scope_drift(actions, remit)
        assert score == pytest.approx(0.0)

    def test_tool_name_overlap_marks_in_scope(self, mon):
        remit = _make_remit(tokens=["email"])
        # Tool name "draft_email" splits to ["draft", "email"]
        actions = [_make_action(tool_name="draft_email", arg_tokens=[])]
        score, _ = mon._score_scope_drift(actions, remit)
        assert score == pytest.approx(0.0)


# ── _score_privilege_escalation ───────────────────────────────────────────────

class TestScorePrivilegeEscalation:

    def test_no_actions_returns_zero(self, mon):
        remit = _make_remit(expected_max_ring=1)
        score, findings = mon._score_privilege_escalation([], remit)
        assert score == 0.0

    def test_within_expected_ring_returns_zero(self, mon):
        remit = _make_remit(expected_max_ring=2)
        actions = [
            _make_action(ring_level=0),
            _make_action(ring_level=1),
            _make_action(ring_level=2),
        ]
        score, findings = mon._score_privilege_escalation(actions, remit)
        assert score == pytest.approx(0.0)

    def test_exceeding_ring_produces_score(self, mon):
        remit = _make_remit(expected_max_ring=0)
        actions = [_make_action(ring_level=3)]
        score, findings = mon._score_privilege_escalation(actions, remit)
        assert score > 0.0

    def test_higher_breach_scores_higher(self, mon):
        remit = _make_remit(expected_max_ring=0)
        small = [_make_action(ring_level=1)]
        large = [_make_action(ring_level=3)]
        score_small, _ = mon._score_privilege_escalation(small, remit)
        score_large, _ = mon._score_privilege_escalation(large, remit)
        assert score_large > score_small

    def test_score_bounded(self, mon):
        remit = _make_remit(expected_max_ring=0)
        actions = [_make_action(ring_level=3) for _ in range(20)]
        score, _ = mon._score_privilege_escalation(actions, remit)
        assert 0.0 <= score <= 1.0

    def test_findings_list_capped_at_five(self, mon):
        remit = _make_remit(expected_max_ring=0)
        actions = [_make_action(tool_name=f"tool_{i}", ring_level=3) for i in range(10)]
        _, findings = mon._score_privilege_escalation(actions, remit)
        assert len(findings) <= 5


# ── _score_data_exfiltration ──────────────────────────────────────────────────

class TestScoreDataExfiltration:

    def test_no_actions_returns_zero(self, mon):
        score, findings = mon._score_data_exfiltration([])
        assert score == 0.0

    def test_outbound_only_no_prior_sensitive_is_zero(self, mon):
        actions = [_make_action(tool_name="send_email")]
        score, _ = mon._score_data_exfiltration(actions)
        assert score == pytest.approx(0.0)

    def test_sensitive_only_no_outbound_is_zero(self, mon):
        actions = [_make_action(tool_name="vault_read")]
        score, _ = mon._score_data_exfiltration(actions)
        assert score == pytest.approx(0.0)

    def test_vault_read_then_send_email_scores_high(self, mon):
        """The canonical exfiltration pattern: read sensitive then send."""
        actions = [
            _make_action(tool_name="vault_read"),
            _make_action(tool_name="send_email"),
        ]
        score, findings = mon._score_data_exfiltration(actions)
        assert score > 0.0
        assert len(findings) > 0

    def test_order_matters_outbound_before_read_is_zero(self, mon):
        """send_email BEFORE vault_read — should not score (no prior sensitive)."""
        actions = [
            _make_action(tool_name="send_email"),
            _make_action(tool_name="vault_read"),
        ]
        score, _ = mon._score_data_exfiltration(actions)
        assert score == pytest.approx(0.0)

    def test_score_escalates_with_multiple_outbound_hops(self, mon):
        """Each successive outbound hop after a sensitive read increases score."""
        single_hop = [
            _make_action(tool_name="vault_read"),
            _make_action(tool_name="send_email"),
        ]
        multi_hop = [
            _make_action(tool_name="vault_read"),
            _make_action(tool_name="send_email"),
            _make_action(tool_name="post"),
        ]
        s_single, _ = mon._score_data_exfiltration(single_hop)
        s_multi, _ = mon._score_data_exfiltration(multi_hop)
        assert s_multi >= s_single

    def test_score_bounded(self, mon):
        actions = (
            [_make_action(tool_name="vault_read")] +
            [_make_action(tool_name="send_email") for _ in range(10)]
        )
        score, _ = mon._score_data_exfiltration(actions)
        assert 0.0 <= score <= 1.0

    def test_search_email_then_browse_web_triggers(self, mon):
        actions = [
            _make_action(tool_name="search_email"),
            _make_action(tool_name="browse_web"),
        ]
        score, findings = mon._score_data_exfiltration(actions)
        assert score > 0.0

    def test_baseline_score_at_least_0_7(self, mon):
        """Source: base = 0.7 + 0.1 * hops; first hop → 0.7."""
        actions = [
            _make_action(tool_name="vault_read"),
            _make_action(tool_name="send_email"),
        ]
        score, _ = mon._score_data_exfiltration(actions)
        assert score >= 0.7


# ── _score_repetition ─────────────────────────────────────────────────────────

class TestScoreRepetition:

    def test_no_actions_returns_zero(self, mon):
        score, findings = mon._score_repetition([])
        assert score == 0.0

    def test_within_free_calls_is_zero(self, mon):
        # Tool score: 3 free → 0 at count ≤ 3.
        # But identical call score: 2 free → (count-2)/5.0.
        # Use DIFFERENT argument summaries so max_identical stays ≤ 2.
        actions = [
            _make_action(tool_name="read_file", arguments_summary=f"path=file{i}.txt")
            for i in range(3)
        ]
        score, _ = mon._score_repetition(actions)
        assert score == pytest.approx(0.0)

    def test_above_free_calls_scores_nonzero(self, mon):
        # 4 calls should produce a small non-zero score
        actions = [_make_action(tool_name="read_file") for _ in range(4)]
        score, _ = mon._score_repetition(actions)
        assert score > 0.0

    def test_tool_score_at_12_calls_is_one(self, mon):
        # Formula: (max_tool - 3) / 9.0 → clamped; 12 calls → (12-3)/9 = 1.0
        actions = [_make_action(tool_name="search_web") for _ in range(12)]
        score, _ = mon._score_repetition(actions)
        assert score == pytest.approx(1.0)

    def test_identical_call_free_calls(self, mon):
        # Identical: 2 free → score 0 at count ≤ 2
        actions = [_make_action(tool_name="vault_read", arguments_summary="key=abc") for _ in range(2)]
        score, _ = mon._score_repetition(actions)
        assert score == pytest.approx(0.0)

    def test_identical_calls_at_7_scores_one(self, mon):
        # Formula: (max_identical - 2) / 5.0 → clamped; 7 calls → (7-2)/5 = 1.0
        actions = [_make_action(tool_name="vault_read", arguments_summary="key=abc") for _ in range(7)]
        score, _ = mon._score_repetition(actions)
        assert score == pytest.approx(1.0)

    def test_findings_include_worst_tool(self, mon):
        actions = [_make_action(tool_name="search_web") for _ in range(8)]
        _, findings = mon._score_repetition(actions)
        assert any("search_web" in f for f in findings)
        assert any("8" in f for f in findings)

    def test_score_bounded(self, mon):
        actions = [_make_action(tool_name="run_command") for _ in range(100)]
        score, _ = mon._score_repetition(actions)
        assert 0.0 <= score <= 1.0

    def test_mixed_tools_score_based_on_most_repeated(self, mon):
        # 2 × tool_a (below free threshold), 6 × tool_b (above threshold)
        actions = (
            [_make_action(tool_name="tool_a") for _ in range(2)] +
            [_make_action(tool_name="tool_b") for _ in range(6)]
        )
        score, findings = mon._score_repetition(actions)
        assert score > 0.0
        assert any("tool_b" in f for f in findings)


# ── Full lifecycle: begin_session / log_action / evaluate ────────────────────

class TestLifecycle:

    def test_begin_session_returns_string_id(self, mon):
        sid = mon.begin_session("Show me my schedule")
        assert isinstance(sid, str)
        assert sid.startswith("bmon-")

    def test_log_action_does_not_raise_on_bad_session(self, mon):
        mon.log_action("non-existent-session", "some_tool", {"k": "v"})  # should silently pass

    def test_evaluate_on_unknown_session_returns_empty(self, mon):
        result = mon.evaluate("does-not-exist")
        assert result == {}

    def test_clean_session_scores_near_zero(self, mon):
        sid = mon.begin_session("What time is it?")
        mon.log_action(sid, "get_time", {}, ring_level=0, result="12:00")
        report = mon.evaluate(sid)
        assert report["scores"]["final_composite"] < 0.40

    def test_report_has_expected_keys(self, mon):
        sid = mon.begin_session("Read a file")
        mon.log_action(sid, "read_file", {"path": "notes.txt"}, ring_level=0)
        report = mon.evaluate(sid)
        for key in ("session_id", "scores", "risk_level", "findings", "remit"):
            assert key in report

    def test_risk_level_field_valid(self, mon):
        sid = mon.begin_session("Do something")
        report = mon.evaluate(sid)
        assert report["risk_level"] in ("normal", "elevated", "high", "critical")

    def test_exfiltration_session_scores_high(self, mon):
        sid = mon.begin_session("Look something up online")
        mon.log_action(sid, "vault_read", {"key": "api_key"}, ring_level=1)
        mon.log_action(sid, "send_email", {"to": "alice@example.com", "body": "here are the secrets"}, ring_level=2)
        report = mon.evaluate(sid)
        assert report["scores"]["data_exfiltration"] > 0.0

    def test_evaluate_pops_session(self, mon):
        sid = mon.begin_session("test")
        mon.evaluate(sid)
        # Second evaluate on same id returns empty
        assert mon.evaluate(sid) == {}

    def test_latest_report_persisted(self, mon, tmp_path):
        sid = mon.begin_session("test persistence")
        mon.evaluate(sid)
        assert mon.latest_report_path.exists()

    def test_trace_file_written(self, mon, tmp_path):
        sid = mon.begin_session("trace file test")
        mon.log_action(sid, "read_file", {"path": "data.csv"}, ring_level=0)
        mon.evaluate(sid)
        assert mon.traces_path.exists()

    def test_high_ring_session_privilege_score(self, mon):
        sid = mon.begin_session("Show me today's weather")
        # Ring-3 tool for a read-only weather request
        mon.log_action(sid, "run_command", {"cmd": "whoami"}, ring_level=3)
        report = mon.evaluate(sid)
        assert report["scores"]["privilege_escalation"] > 0.0

    def test_repetition_detected_in_session(self, mon):
        sid = mon.begin_session("search the web for news")
        for _ in range(12):
            mon.log_action(sid, "search_web", {"query": "news"}, ring_level=2)
        report = mon.evaluate(sid)
        assert report["scores"]["repetition_anomaly"] > 0.0

    def test_no_actions_session_is_benign(self, mon):
        sid = mon.begin_session("")
        report = mon.evaluate(sid)
        assert report["scores"]["final_composite"] == pytest.approx(0.0)

    def test_sensitive_unrelated_action_raises_scope_score(self, mon):
        # User asks about weather; agent reads vault
        sid = mon.begin_session("what is the weather like today?")
        mon.log_action(sid, "vault_read", {"key": "some_vault_key"}, ring_level=1)
        report = mon.evaluate(sid)
        assert report["scores"]["scope_drift"] > 0.0

    def test_session_meta_preserved_in_report(self, mon):
        sid = mon.begin_session("test", meta={"source": "unit-test"})
        report = mon.evaluate(sid)
        assert report.get("meta", {}).get("source") == "unit-test"

    def test_get_latest_report_before_any_session(self, mon):
        r = mon.get_latest_report()
        assert "scores" in r

    def test_get_history_summary_empty(self, mon):
        s = mon.get_history_summary()
        assert s["count"] == 0

    def test_get_history_summary_after_session(self, mon):
        sid = mon.begin_session("test summary")
        mon.evaluate(sid)
        s = mon.get_history_summary()
        assert s["count"] >= 1

    def test_get_risk_score_shape(self, mon):
        r = mon.get_risk_score()
        assert "composite" in r
        assert "risk_level" in r
        assert "recent_average" in r


# ── edge cases / degenerate inputs ───────────────────────────────────────────

class TestEdgeCases:

    def test_summarize_args_empty_dict(self):
        assert summarize_args({}) == ""

    def test_summarize_args_nested_value(self):
        out = summarize_args({"data": {"nested": "value"}})
        assert "data=" in out

    def test_clamp_exactly_zero_and_one(self):
        assert _clamp(0.0) == 0.0
        assert _clamp(1.0) == 1.0

    def test_risk_level_zero(self):
        assert _risk_level(0.0) == "normal"

    def test_risk_level_one(self):
        assert _risk_level(1.0) == "critical"

    def test_classify_result_empty_string(self):
        assert _classify_result("") == "text"

    def test_tokens_stopword_only(self):
        result = _tokens("the and or but for")
        assert result == set()

    def test_extract_remit_very_long_message(self, mon):
        msg = "please send email to alice " + "word " * 1000
        r = mon.extract_remit(msg)
        assert len(r.raw) <= 500

    def test_log_action_none_arguments(self, mon):
        sid = mon.begin_session("test")
        mon.log_action(sid, "some_tool", None, ring_level=0)
        report = mon.evaluate(sid)
        assert "scores" in report

    def test_log_action_string_arguments(self, mon):
        sid = mon.begin_session("test")
        mon.log_action(sid, "run_command", "ls -la", ring_level=1)
        report = mon.evaluate(sid)
        assert "scores" in report

    def test_composite_weights_used_in_evaluate(self, mon):
        # Verify that a pure repetition anomaly (no exfil/drift/priv) produces
        # a composite proportional to its weight (0.20)
        sid = mon.begin_session("search the web 15 times")
        for _ in range(15):
            # use a tool that is in OUTBOUND_TOOLS but no prior sensitive read
            mon.log_action(sid, "search_web", {"query": "news"}, ring_level=2)
        report = mon.evaluate(sid)
        rep_score = report["scores"]["repetition_anomaly"]
        # composite should be at least rep_score * 0.20 (other dims contribute too)
        assert report["scores"]["final_composite"] >= rep_score * 0.20 - 0.01


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
