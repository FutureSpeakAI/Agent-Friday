"""Adversarial security tests for the egress gate.

The egress gate (services/egress_gate.py) is the LAST security boundary before
any cloud HTTP call. These tests attack it with PII in many formats, multi-turn
leaks, provider-switch bypass attempts, classifier-crash recovery, rate limiting
under load, and the startup self-test contract.

All PII here is SYNTHETIC — invented values, never real user data.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services.sensitivity_classifier import Tier


DEVNULL = Path(os.devnull)


def _seal(content, provider="anthropic"):
    return eg.seal_outbound(
        {"messages": [{"role": "user", "content": content}]},
        provider,
        log_path=DEVNULL,
    )


def _first_content(sealed):
    msgs = sealed.get("messages") or [{}]
    return str(msgs[0].get("content", ""))


# ── PII in various formats — must never survive to a cloud provider ───────────

class TestPIIFormats:
    @pytest.mark.parametrize("ssn", [
        "123-45-6789",  # pragma: allowlist secret
        "123 45 6789",
        "My social security number is 123-45-6789.",  # pragma: allowlist secret
        "ssn: 123-45-6789 on file",  # pragma: allowlist secret
    ])
    def test_ssn_never_leaks_verbatim(self, ssn):
        out = _first_content(_seal(ssn))
        assert "123-45-6789" not in out  # pragma: allowlist secret
        assert "123 45 6789" not in out

    @pytest.mark.parametrize("cc", [
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "My card is 4111111111111111",
    ])
    def test_credit_card_never_leaks(self, cc):
        out = _first_content(_seal(cc))
        assert "4111" not in out or "[EGRESS-GATE" in out

    @pytest.mark.parametrize("key", [
        "sk-ant-api03-ABCDEFGHIJKLMNOP1234567890",  # pragma: allowlist secret
        "AIzaSyABCDEFGHIJKLMNOP1234567890abcd",
        "sk-proj-ABCDEFGHIJKLMNOP1234567890",  # pragma: allowlist secret
    ])
    def test_api_keys_never_leak(self, key):
        out = _first_content(_seal(f"the token is {key}"))
        assert key not in out

    def test_financial_keyword_content_gets_withheld(self):
        out = _first_content(_seal("Here is my bank account number and routing number for the wire."))
        # SENSITIVE → dropped to empty; PRIVATE → placeholder. Never the raw text.
        assert "bank account number" not in out.lower()

    def test_medical_content_withheld(self):
        out = _first_content(_seal("My A1C blood glucose diagnosis and prescription medication dosage."))
        assert "prescription" not in out.lower()

    def test_legal_custody_content_withheld(self):
        out = _first_content(_seal("The custody arrangement and divorce settlement court filing."))
        assert "custody" not in out.lower()


# ── Public content passes untouched ───────────────────────────────────────────

class TestPublicPasses:
    def test_ordinary_public_text_passes(self):
        text = "What is the capital of France and how tall is the Eiffel Tower?"
        out = _first_content(_seal(text))
        assert out == text

    def test_public_content_multiple_providers(self):
        text = "Explain how a for-loop works in Python."
        for provider in ("anthropic", "openai", "gemini", "openrouter"):
            assert _first_content(_seal(text, provider)) == text


# ── Local providers bypass the gate entirely (data stays on device) ───────────

class TestLocalBypass:
    @pytest.mark.parametrize("provider", ["ollama", "local", "OLLAMA"])
    def test_sensitive_content_untouched_for_local(self, provider):
        text = "My SSN is 123-45-6789."  # pragma: allowlist secret
        out = _first_content(_seal(text, provider))
        assert out == text  # local models are trusted with raw data


# ── Multi-field payloads — system + messages + tools all gated ────────────────

class TestMultiField:
    def test_system_prompt_gated(self):
        sealed = eg.seal_outbound(
            {"system": "User SSN 123-45-6789 for reference.",  # pragma: allowlist secret
             "messages": [{"role": "user", "content": "hi"}]},
            "anthropic", log_path=DEVNULL)
        assert "123-45-6789" not in str(sealed.get("system", ""))  # pragma: allowlist secret

    def test_tool_description_with_sensitive_context_redacted(self):
        sealed = eg.seal_outbound(
            {"messages": [{"role": "user", "content": "hi"}],
             "tools": [{"name": "t", "description": "Access the user's bank account number and tax return."}]},
            "anthropic", log_path=DEVNULL)
        desc = sealed["tools"][0]["description"]
        assert "bank account number" not in desc.lower()

    def test_structured_content_parts_gated(self):
        sealed = eg.seal_outbound(
            {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "My SSN is 123-45-6789."},  # pragma: allowlist secret
                {"type": "image", "source": {"url": "x"}},
            ]}]},
            "anthropic", log_path=DEVNULL)
        parts = sealed["messages"][0]["content"]
        assert "123-45-6789" not in parts[0]["text"]  # pragma: allowlist secret
        # non-text parts untouched
        assert parts[1]["type"] == "image"


# ── Multi-turn leak — a sensitive earlier turn is gated even amid public ones ──

class TestMultiTurnLeak:
    def test_sensitive_turn_among_public_turns_gated(self):
        sealed = eg.seal_outbound({"messages": [
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi! How can I help?"},
            {"role": "user", "content": "My bank account number is on the statement."},
            {"role": "assistant", "content": "Sure."},
        ]}, "anthropic", log_path=DEVNULL)
        contents = [str(m.get("content", "")) for m in sealed["messages"]]
        joined = " ".join(contents).lower()
        assert "bank account number" not in joined
        # public turns preserved
        assert "hello there" in joined


# ── Provider-switch bypass — routing to a different cloud provider still gates ─

class TestProviderSwitchBypass:
    def test_switching_cloud_provider_does_not_bypass(self):
        text = "My SSN is 123-45-6789 and bank account details."  # pragma: allowlist secret
        for provider in ("anthropic", "openai", "gemini", "some-unknown-cloud"):
            out = _first_content(_seal(text, provider))
            assert "123-45-6789" not in out  # pragma: allowlist secret

    def test_unknown_provider_treated_as_cloud(self):
        # An unrecognized provider string is fail-closed → treated as cloud.
        assert eg._is_cloud("mystery-provider") is True


# ── Classifier crash recovery — a raising classifier must fail closed ─────────

class TestClassifierCrashRecovery:
    def test_classifier_exception_does_not_leak(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("classifier exploded")
        monkeypatch.setattr(eg, "_classify_impl", _boom, raising=False)
        # _classify_cloud calls _classify_impl; if it raises the gate must not
        # silently pass raw text. seal_outbound should either withhold or raise,
        # never return the verbatim sensitive string.
        try:
            out = _first_content(_seal("My SSN is 123-45-6789."))  # pragma: allowlist secret
        except Exception:
            return  # raising is an acceptable fail-closed outcome
        assert "123-45-6789" not in out  # pragma: allowlist secret


# ── Rate limiting — a burst queues (best-effort) but never blocks forever ─────

class TestRateLimiting:
    def test_rate_limit_never_blocks_indefinitely(self, monkeypatch):
        # Force a tiny rate so the token bucket saturates immediately.
        monkeypatch.setattr(eg, "_RATE_MAX_PER_SEC", 1)
        monkeypatch.setattr(eg, "_RATE_MAX_WAIT_S", 0.2)
        results = []

        def worker():
            try:
                eg._rate_limit()
                results.append(True)
            except Exception:
                results.append(False)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        # Every caller must return (best-effort pacing), none may hang or raise.
        assert len(results) == 20
        assert all(results)


# ── Startup self-test — the gate proves it can withhold a known probe ─────────

class TestStartupSelfTest:
    def test_self_test_passes_with_working_gate(self):
        eg._SELF_TEST_RESULT = None
        result = eg.startup_self_test()
        assert result["ok"] is True
        assert eg.gate_operational() is True

    def test_gate_operational_true_before_selftest_runs(self):
        eg._SELF_TEST_RESULT = None
        # Unrun self-test defaults to operational (per-call wrapper is the guard).
        assert eg.gate_operational() is True

    def test_gate_operational_false_when_selftest_failed(self):
        eg._SELF_TEST_RESULT = {"ok": False, "error": "probe survived"}
        assert eg.gate_operational() is False
        eg._SELF_TEST_RESULT = None  # reset for other tests

    def test_self_test_records_failure_on_exception(self, monkeypatch):
        eg._SELF_TEST_RESULT = None
        monkeypatch.setattr(eg, "seal_outbound",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        result = eg.startup_self_test()
        assert result["ok"] is False
        assert "error" in result
        eg._SELF_TEST_RESULT = None


# ── Immutability — the gate never mutates the caller's payload ────────────────

class TestNoMutation:
    def test_input_payload_not_mutated(self):
        original = {"messages": [{"role": "user", "content": "My SSN is 123-45-6789."}]}  # pragma: allowlist secret
        eg.seal_outbound(original, "anthropic", log_path=DEVNULL)
        # original content unchanged — gate returns a new dict
        assert original["messages"][0]["content"] == "My SSN is 123-45-6789."  # pragma: allowlist secret

    def test_empty_and_none_content_safe(self):
        assert eg._gate_text("", "anthropic", "f", DEVNULL) == ""
        assert eg._gate_text(None, "anthropic", "f", DEVNULL) is None
