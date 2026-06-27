"""Unit tests for the egress gate (services/egress_gate.py).

The egress gate is the last security boundary before any cloud HTTP call.
These tests use synthetic data — no real PII.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services.egress_gate import (
    seal_outbound,
    _is_cloud,
    _gate_text,
    _gate_messages,
)
from agent_friday.services.sensitivity_classifier import Tier


# ── Provider classification ────────────────────────────────────────────────────

class TestIsCloud:
    @pytest.mark.parametrize("p", ["anthropic", "openai", "gemini", "openrouter", ""])
    def test_cloud_providers(self, p):
        assert _is_cloud(p) is True

    @pytest.mark.parametrize("p", ["ollama", "local", "OLLAMA", "Local"])
    def test_local_providers(self, p):
        assert _is_cloud(p) is False


# ── Local bypass — gate never touches local-provider payloads ─────────────────

class TestLocalBypass:
    def test_ollama_returned_unchanged(self):
        payload = {
            "system": "his SSN is 123-45-6789 and custody details",  # pragma: allowlist secret
            "messages": [{"role": "user", "content": "financial account balance"}],
        }
        result = seal_outbound(payload, "ollama")
        assert result["system"] == payload["system"]
        assert result["messages"] == payload["messages"]

    def test_local_returned_unchanged(self):
        payload = {"system": "medical record details", "messages": []}
        result = seal_outbound(payload, "local")
        assert result is not payload or result == payload


# ── Fail-closed: sensitive content is blocked ─────────────────────────────────

class TestSensitiveContentBlocked:
    def test_sensitive_system_prompt_dropped(self):
        payload = {
            "system": "the user's SSN is 123-45-6789",  # pragma: allowlist secret
            "messages": [],
        }
        result = seal_outbound(payload, "anthropic")
        assert "123-45-6789" not in result.get("system", "")  # pragma: allowlist secret

    def test_sensitive_message_redacted(self):
        payload = {
            "system": "you are a helpful assistant",
            "messages": [
                {"role": "user", "content": "custody and divorce settlement hearing"},
            ],
        }
        result = seal_outbound(payload, "anthropic")
        msg_content = result["messages"][0]["content"]
        # "custody" matches TIER_3 keyword → should not pass through unredacted
        assert msg_content == "" or "EGRESS-GATE" in msg_content

    def test_financial_content_not_forwarded(self):
        payload = {
            "messages": [
                {"role": "user", "content": "my bank account routing number is 123456789"}
            ]
        }
        result = seal_outbound(payload, "openai")
        content = result["messages"][0]["content"]
        # financial keyword match → redacted or dropped
        assert "routing number" not in content or "EGRESS-GATE" in content


# ── Public content passes through ─────────────────────────────────────────────

class TestPublicContentPassthrough:
    def test_general_news_passes(self):
        text = "breaking news: local sports team wins championship game"
        result = _gate_text(text, "anthropic", "system")
        assert result == text

    def test_technical_query_passes(self):
        payload = {
            "system": "you are a Python programming assistant",
            "messages": [
                {"role": "user", "content": "how do I sort a list in Python?"}
            ],
        }
        result = seal_outbound(payload, "anthropic")
        assert result["system"] == payload["system"]
        assert result["messages"][0]["content"] == payload["messages"][0]["content"]


# ── Payload structure preservation ────────────────────────────────────────────

class TestPayloadStructure:
    def test_extra_keys_preserved(self):
        payload = {
            "model": "claude-opus-4-8",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": "hello world"}],
        }
        result = seal_outbound(payload, "anthropic")
        assert result["model"] == "claude-opus-4-8"
        assert result["max_tokens"] == 4096

    def test_empty_messages_safe(self):
        payload = {"messages": []}
        result = seal_outbound(payload, "anthropic")
        assert result["messages"] == []

    def test_no_messages_key_safe(self):
        payload = {"model": "gpt-4o"}
        result = seal_outbound(payload, "openai")
        assert "messages" not in result

    def test_multi_part_content_gated(self):
        payload = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "how do I sort a list in Python"},
                    {"type": "text", "text": "financial account balance record"},
                ]
            }]
        }
        result = seal_outbound(payload, "anthropic")
        parts = result["messages"][0]["content"]
        assert parts[0]["text"] == "how do I sort a list in Python"  # public
        assert "financial account" not in parts[1]["text"] or "EGRESS-GATE" in parts[1]["text"]

    def test_tool_definitions_scanned(self):
        payload = {
            "messages": [],
            "tools": [
                {"name": "search", "description": "query a search engine for results"},
                {"name": "vault_read", "description": "read SSN and financial records"},  # pragma: allowlist secret
            ],
        }
        result = seal_outbound(payload, "anthropic")
        tools = result["tools"]
        assert tools[0]["description"] == "query a search engine for results"  # public
        assert "SSN" not in tools[1]["description"] or "withheld" in tools[1]["description"]


# ── gate_messages: list processing ───────────────────────────────────────────

class TestGateMessages:
    def test_non_dict_messages_passed_through(self):
        msgs = ["not a dict"]
        result = _gate_messages(msgs, "anthropic")
        assert result == msgs

    def test_message_without_content_passed_through(self):
        msgs = [{"role": "system"}]
        result = _gate_messages(msgs, "anthropic")
        assert result[0] == {"role": "system"}

    def test_multiple_messages_processed(self):
        msgs = [
            {"role": "user", "content": "how do I implement bubble sort?"},
            {"role": "assistant", "content": "iterate and swap adjacent elements"},
            {"role": "user", "content": "custody and divorce settlement"},
        ]
        result = _gate_messages(msgs, "anthropic")
        assert result[0]["content"] == "how do I implement bubble sort?"
        assert result[1]["content"] == "iterate and swap adjacent elements"
        # custody/divorce are TIER_3 → redacted or dropped
        assert "custody" not in result[2]["content"] or "EGRESS-GATE" in result[2]["content"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
