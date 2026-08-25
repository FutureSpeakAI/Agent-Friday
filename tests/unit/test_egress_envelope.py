"""Envelope-coverage tests for the egress gate.

The gate's job is not "scan the message list" — it is "nothing sensitive leaves
this device". Those are the same thing only if every field of the outbound
payload that can carry prose is actually gated. This file pins the WHOLE
envelope, because the failure mode we keep hitting is a gate that scans the
obvious field and misses the rest (found 2026-08-24).

Every test here failed against the code as it stood on 2026-08-24. If one of
them starts passing for a reason you did not intend, a coverage hole reopened.

Synthetic data only — no real PII.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services.egress_gate import seal_outbound


# A phrase the deterministic classifier puts above PUBLIC, plus an SSN-shaped
# identifier the scrubber recognises. Using both means a test cannot pass just
# because one of the two layers happened to fire.
SENSITIVE = "custody and divorce settlement for SSN 123-45-6789"  # pragma: allowlist secret


def _leaked(blob: str) -> bool:
    """True when the marker text survived verbatim."""
    return "custody and divorce settlement" in blob or "123-45-6789" in blob  # pragma: allowlist secret


# ── G1: the OpenAI function shape must be gated like the Anthropic shape ──────

class TestOpenAIToolShape:
    """anthropic_to_openai_tools nests name/description under "function".

    The gate read them at the top level, so on every openai-compatible cloud
    provider (openai, openrouter, …) MCP tool descriptions bypassed it —
    including the ones the Anthropic path correctly withheld.
    """

    def test_mcp_description_gated_in_function_shape(self):
        payload = {
            "messages": [],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "mcp_gmail_send",
                    "description": SENSITIVE,
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        }
        result = seal_outbound(payload, "openrouter")
        assert not _leaked(result["tools"][0]["function"]["description"])

    def test_function_shape_keeps_its_structure(self):
        """Gating must not flatten the payload the provider expects."""
        payload = {
            "messages": [],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "mcp_gmail_send",
                    "description": SENSITIVE,
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        }
        result = seal_outbound(payload, "openrouter")
        tool = result["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "mcp_gmail_send"
        assert tool["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_first_party_function_shape_untouched(self):
        """The mcp_-only scoping survives the shape fix."""
        payload = {
            "messages": [],
            "tools": [{
                "type": "function",
                "function": {"name": "calendar_list",
                             "description": "list the user's calendar events"},
            }],
        }
        result = seal_outbound(payload, "openrouter")
        assert (result["tools"][0]["function"]["description"]
                == "list the user's calendar events")


# ── G2: third-party tool schemas carry prose too ─────────────────────────────

class TestToolSchemaProse:
    """An MCP server authors its input schema as well as its description.

    Only the description was ever replaced, so schema prose went verbatim.
    """

    def test_property_description_gated_anthropic_shape(self):
        payload = {
            "messages": [],
            "tools": [{
                "name": "mcp_vault_query",
                "description": "query records",
                "input_schema": {
                    "type": "object",
                    "properties": {"q": {"type": "string", "description": SENSITIVE}},
                },
            }],
        }
        result = seal_outbound(payload, "anthropic")
        desc = result["tools"][0]["input_schema"]["properties"]["q"]["description"]
        assert not _leaked(desc)

    def test_property_description_gated_function_shape(self):
        payload = {
            "messages": [],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "mcp_vault_query",
                    "description": "query records",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "q": {"type": "string", "description": SENSITIVE},
                        },
                    },
                },
            }],
        }
        result = seal_outbound(payload, "openai")
        props = result["tools"][0]["function"]["parameters"]["properties"]
        assert not _leaked(props["q"]["description"])

    def test_nested_schema_prose_gated(self):
        payload = {
            "messages": [],
            "tools": [{
                "name": "mcp_deep_tool",
                "description": "ok",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "outer": {
                            "type": "object",
                            "properties": {
                                "inner": {"type": "string", "description": SENSITIVE},
                            },
                        },
                    },
                },
            }],
        }
        result = seal_outbound(payload, "anthropic")
        inner = (result["tools"][0]["input_schema"]["properties"]["outer"]
                 ["properties"]["inner"])
        assert not _leaked(inner["description"])

    def test_schema_structure_is_never_altered(self):
        """Redacting prose must not touch the machinery the model calls with.

        Types, required lists and enum VALUES are load-bearing: replacing an
        enum member would make the tool uncallable rather than private.
        """
        schema = {
            "type": "object",
            "required": ["mode"],
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "slow"],
                         "description": SENSITIVE},
            },
        }
        payload = {"messages": [],
                   "tools": [{"name": "mcp_x_y", "description": "ok",
                              "input_schema": schema}]}
        result = seal_outbound(payload, "anthropic")
        out = result["tools"][0]["input_schema"]
        assert out["required"] == ["mode"]
        assert out["properties"]["mode"]["type"] == "string"
        assert out["properties"]["mode"]["enum"] == ["fast", "slow"]

    def test_first_party_schema_untouched(self):
        payload = {
            "messages": [],
            "tools": [{
                "name": "calendar_list",
                "description": "list events",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "day": {"type": "string",
                                "description": "the day to list, e.g. 2026-08-24"},
                    },
                },
            }],
        }
        result = seal_outbound(payload, "anthropic")
        day = result["tools"][0]["input_schema"]["properties"]["day"]
        assert day["description"] == "the day to list, e.g. 2026-08-24"


# ── G3: tool_use arguments replayed in history ───────────────────────────────

class TestToolUseArguments:
    """Assistant tool_use blocks are echoed back into the conversation
    (agent.py:6181). Their arguments are real user data, and they were neither
    tier-gated nor PII-scrubbed: "input" is not one of the keys _scrub_all
    recurses into, and tool_use is not one of the block types _gate_messages
    handles.

    Same-provider turns re-send what that provider already authored. The leak
    is a history built against one seat and replayed to another — a local seat
    falling back to cloud.
    """

    def test_tool_use_input_is_gated(self):
        payload = {"messages": [{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "vault_write",
                         "input": {"text": SENSITIVE}}],
        }]}
        result = seal_outbound(payload, "anthropic")
        block = result["messages"][0]["content"][0]
        assert not _leaked(str(block["input"]))

    def test_tool_use_input_gated_under_arbitrary_key_names(self):
        """Argument names are author-chosen; gating must not key off a whitelist."""
        payload = {"messages": [{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "search",
                         "input": {"wholly_unexpected_arg_name": SENSITIVE}}],
        }]}
        result = seal_outbound(payload, "anthropic")
        assert not _leaked(str(result["messages"][0]["content"][0]["input"]))

    def test_tool_use_nested_input_gated(self):
        payload = {"messages": [{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "send",
                         "input": {"msg": {"body": [SENSITIVE]}}}],
        }]}
        result = seal_outbound(payload, "anthropic")
        assert not _leaked(str(result["messages"][0]["content"][0]["input"]))

    def test_tool_use_identity_preserved(self):
        """id/name/type must survive: Anthropic pairs tool_use to tool_result
        by id, and breaking the pairing turns a redaction into a 400."""
        payload = {"messages": [{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_abc", "name": "vault_write",
                         "input": {"text": SENSITIVE}}],
        }]}
        result = seal_outbound(payload, "anthropic")
        block = result["messages"][0]["content"][0]
        assert block["id"] == "toolu_abc"
        assert block["name"] == "vault_write"
        assert block["type"] == "tool_use"
        assert isinstance(block["input"], dict)
        assert "text" in block["input"]  # keys kept; only values redacted

    def test_public_tool_use_input_passes(self):
        payload = {"messages": [{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "search",
                         "input": {"query": "how do I sort a list in Python"}}],
        }]}
        result = seal_outbound(payload, "anthropic")
        assert (result["messages"][0]["content"][0]["input"]["query"]
                == "how do I sort a list in Python")

    def test_non_string_argument_values_survive(self):
        payload = {"messages": [{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "page",
                         "input": {"limit": 25, "deep": True, "cursor": None}}],
        }]}
        result = seal_outbound(payload, "anthropic")
        got = result["messages"][0]["content"][0]["input"]
        assert got == {"limit": 25, "deep": True, "cursor": None}


# ── G4: system prompt as a list of blocks ────────────────────────────────────

class TestSystemBlocks:
    """Anthropic accepts `system` as a list of text blocks — the shape prompt
    caching requires (cache_control rides on the block). The gate ran only on
    `isinstance(system, str)`, so adopting block-form caching would have
    silently un-gated the system prompt. Nothing builds it that way today;
    this test is here so nothing can, unnoticed.
    """

    def test_system_block_list_is_gated(self):
        payload = {"system": [{"type": "text", "text": SENSITIVE}], "messages": []}
        result = seal_outbound(payload, "anthropic")
        assert not _leaked(str(result["system"]))

    def test_system_block_public_text_passes(self):
        text = "you are a helpful Python programming assistant"
        payload = {"system": [{"type": "text", "text": text}], "messages": []}
        result = seal_outbound(payload, "anthropic")
        assert result["system"][0]["text"] == text

    def test_system_block_cache_control_preserved(self):
        payload = {"system": [{"type": "text", "text": "you are helpful",
                               "cache_control": {"type": "ephemeral"}}],
                   "messages": []}
        result = seal_outbound(payload, "anthropic")
        assert result["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_system_string_still_gated(self):
        """The str path must not regress while adding the list path."""
        payload = {"system": SENSITIVE, "messages": []}
        result = seal_outbound(payload, "anthropic")
        assert not _leaked(result["system"])


# ── Local providers still bypass the whole envelope ──────────────────────────

class TestLocalBypassUnaffected:
    def test_local_provider_untouched_across_new_fields(self):
        payload = {
            "system": [{"type": "text", "text": SENSITIVE}],
            "messages": [{"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "w",
                 "input": {"text": SENSITIVE}}]}],
            "tools": [{"name": "mcp_a_b", "description": SENSITIVE}],
        }
        result = seal_outbound(payload, "ollama")
        assert result["system"][0]["text"] == SENSITIVE
        assert result["messages"][0]["content"][0]["input"]["text"] == SENSITIVE
        assert result["tools"][0]["description"] == SENSITIVE


# ── The cache the widened coverage depends on ────────────────────────────────

class TestToolDefinitionCache:
    """Gating tool SCHEMAS multiplies the classified string count ~7x. That is
    only affordable because tool definitions are static and their tier is
    memoised. If the cache is removed, coverage silently becomes a ~19s-per-
    call latency bug instead of a 700ms one, so it is pinned here.
    """

    def test_repeated_seal_reuses_the_classification(self, monkeypatch):
        from agent_friday.services import egress_gate as eg

        eg._TOOL_TIER_CACHE.clear()
        calls = []
        real = eg._classify_cloud
        monkeypatch.setattr(eg, "_classify_cloud",
                            lambda t: (calls.append(t), real(t))[1])

        payload = {
            "messages": [],
            "tools": [{
                "name": "mcp_a_b",
                "description": "perform an operation on the remote service",
                "input_schema": {"type": "object", "properties": {
                    "q": {"type": "string", "description": "the query to run"}}},
            }],
        }
        seal_outbound(payload, "anthropic")
        first = len(calls)
        assert first >= 2, "description and schema prose should both classify"

        seal_outbound(payload, "anthropic")
        assert len(calls) == first, "second seal must hit the cache, not reclassify"

    def test_user_content_is_never_cached(self):
        """The cache must hold tool definitions only. Message content is
        unbounded and is the sensitive material — keeping it in a process-wide
        dict is the sort of quiet copy this gate exists to prevent.
        """
        from agent_friday.services import egress_gate as eg

        eg._TOOL_TIER_CACHE.clear()
        seal_outbound({"messages": [
            {"role": "user", "content": "how do I sort a list in Python"}]},
            "anthropic")
        joined = " ".join(eg._TOOL_TIER_CACHE.keys())
        assert "sort a list" not in joined
        assert eg._TOOL_TIER_CACHE == {}

    def test_cache_is_bounded(self):
        from agent_friday.services import egress_gate as eg
        assert eg._TOOL_TIER_MAX > 0
        eg._TOOL_TIER_CACHE.clear()
        for i in range(eg._TOOL_TIER_MAX + 5):
            eg._TOOL_TIER_CACHE[f"filler-{i}"] = 0
        eg._classify_tool_text("a short public tool description")
        assert len(eg._TOOL_TIER_CACHE) <= eg._TOOL_TIER_MAX


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
